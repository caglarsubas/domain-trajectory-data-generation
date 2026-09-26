import gzip
import io
import json
import random
import zipfile
from pathlib import Path

import httpx
import pytest

from app import runtime
from app.calibrate import suggest
from app.eventlog import LogError, read_cases
from app.fetch import FetchError, safe_download
from sectors.calibration import Calibration, build
from sectors.registry import get_sector
from test_api import _auth, _project, _run

NAMESPACE = tuple(get_sector("banking").event_namespace)


def xes_log(cases: list[list[tuple[str, str]]], lifecycle: bool = True) -> bytes:
    """A small XES log; each case is a list of (activity, ISO time)."""
    traces = []
    for number, events in enumerate(cases):
        body = "".join(
            f'<event><string key="concept:name" value="{name}"/>'
            + ('<string key="lifecycle:transition" value="complete"/>' if lifecycle else "")
            + f'<date key="time:timestamp" value="{when}"/></event>'
            + (f'<event><string key="concept:name" value="{name}"/><string key="lifecycle:transition" value="start"/><date key="time:timestamp" value="{when}"/></event>' if lifecycle else "")
            for name, when in events
        )
        traces.append(f'<trace><string key="concept:name" value="Application_{number}"/>{body}</trace>')
    return f'<?xml version="1.0" encoding="UTF-8"?><log xes.version="1.0" xmlns="http://www.xes-standard.org/">{"".join(traces)}</log>'.encode()


def bpi_like(count: int = 60) -> list[list[tuple[str, str]]]:
    """Applications that are submitted, validated, and then granted (A_Pending) or denied, two to one."""
    cases = []
    for number in range(count):
        day = 1 + number % 20
        end = "A_Denied" if number % 3 == 0 else "A_Pending"
        cases.append([
            ("A_Create Application", f"2016-01-{day:02d}T09:00:00+01:00"),
            ("A_Submitted", f"2016-01-{day:02d}T09:10:00+01:00"),
            ("O_Create Offer", f"2016-01-{day:02d}T10:00:00+01:00"),
            ("W_Validate application", f"2016-01-{day:02d}T12:00:00+01:00"),
            ("A_Validating", f"2016-01-{day + 2:02d}T12:00:00+01:00"),
            (end, f"2016-01-{day + 5:02d}T12:00:00+01:00"),
        ])
    return cases


# Reading logs


def test_xes_csv_and_ocel_logs_are_read_as_cases(tmp_path):
    plain = tmp_path / "log.xes"
    plain.write_bytes(xes_log(bpi_like(3)))
    kind, cases = read_cases(plain)
    cases = list(cases)
    assert kind == "xes" and len(cases) == 3
    assert [name for name, _ in cases[0]] == ["A_Create Application", "A_Submitted", "O_Create Offer", "W_Validate application", "A_Validating", "A_Denied"]
    assert cases[0][1][1] - cases[0][0][1] == 600

    zipped = tmp_path / "log.xes.gz"
    zipped.write_bytes(gzip.compress(xes_log(bpi_like(4))))
    assert len(list(read_cases(zipped)[1])) == 4

    table = tmp_path / "log.csv"
    table.write_text("case:concept:name;concept:name;time:timestamp\nc1;submit;2024-01-02 10:00:00\nc1;create;2024-01-01 10:00:00\nc2;create;01/02/2024 09:00\n")
    kind, cases = read_cases(table)
    cases = list(cases)
    assert kind == "csv" and [name for name, _ in cases[0]] == ["create", "submit"] and len(cases) == 2

    ocel = tmp_path / "log.json"
    ocel.write_text(json.dumps({
        "objectTypes": [], "eventTypes": [],
        "objects": [{"id": "a1", "type": "application"}, {"id": "a2", "type": "application"}, {"id": "p1", "type": "party"}],
        "events": [
            {"id": "e1", "type": "submit", "time": "2024-01-01T10:00:00Z", "relationships": [{"objectId": "a1"}, {"objectId": "p1"}]},
            {"id": "e2", "type": "approve", "time": "2024-01-02T10:00:00Z", "relationships": [{"objectId": "a1"}]},
            {"id": "e3", "type": "submit", "time": "2024-01-03T10:00:00Z", "relationships": [{"objectId": "a2"}]},
        ],
    }))
    kind, cases = read_cases(ocel)
    assert kind == "ocel" and sorted(len(case) for case in cases) == [1, 2]


def test_a_parquet_log_is_read_when_pyarrow_is_there(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    path = tmp_path / "log.parquet"
    pq.write_table(pa.table({"case_id": ["c1", "c1", "c2"], "activity": ["create", "submit", "create"], "timestamp": ["2024-01-01T10:00:00", "2024-01-01T11:00:00", "2024-01-02T09:00:00"]}), path)
    kind, cases = read_cases(path)
    assert kind == "parquet" and sorted(len(case) for case in cases) == [1, 2]


def test_files_that_are_not_event_logs_say_why(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text("name,colour\nAda,blue\n")
    with pytest.raises(LogError, match="case and activity"):
        list(read_cases(path)[1])


# Mapping and calibration


def test_activities_are_mapped_by_shared_words_and_ties_are_left_open():
    mapping = suggest(["A_Create Application", "A_Submitted", "A_Denied", "A_Cancelled", "W_Validate application", "card activated"], NAMESPACE)
    assert mapping["A_Create Application"] == "application.started"
    assert mapping["A_Submitted"] == "application.submitted"
    assert mapping["A_Denied"] == "application.declined"
    assert mapping["A_Cancelled"] == "application.abandoned"
    assert mapping["W_Validate application"] is None
    assert mapping["card activated"] == "card.activated"


def test_a_calibration_reweights_legal_choices_and_times_steps():
    sequences = [[("a", 0.0), ("b", 2.0), ("c", 50.0)] for _ in range(90)] + [[("a", 0.0), ("d", 5.0)] for _ in range(10)]
    calibration = build(sequences, source="toy")
    assert calibration.cases == 100 and calibration.transitions["a"] == {"b": 90, "d": 10}
    assert calibration.dwell["a>b"] == (2.0, 2.0, 2.0, 90)
    blended = dict(calibration.reweight("a", [("b", 1.0), ("d", 1.0), ("e", 1.0)]))
    assert blended["b"] > blended["d"] > 0 and blended["e"] < 1.0
    assert sum(blended.values()) == pytest.approx(3.0)
    assert calibration.reweight("zzz", [("b", 1.0)]) == [("b", 1.0)]
    assert 1.0 <= calibration.dwell_hours(random.Random(1), "a", "b") <= 4.0
    assert calibration.dwell_hours(random.Random(1), "a", "e") is None


# Studies calibrated from data sources


def _study(client, email):
    headers = _auth(client, email, "password-123")
    return headers, _project(client, headers)


def _upload(client, headers, project_id, name, body, kind="data_source"):
    response = client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": kind}, files={"upload": (name, body, "application/octet-stream")})
    assert response.status_code == 200, response.text
    return response.json()


def _declined_share(run):
    kinds = {event["event_id"]: event["event_type"] for event in run["bundle"]["events"]}
    primaries = [[kinds[item] for item in trajectory["event_ids"]] for trajectory in run["bundle"]["trajectories"] if not trajectory.get("parent_trajectory_id")]
    return sum("application.declined" in types for types in primaries) / len(primaries)


def test_an_uploaded_log_calibrates_runs_and_its_mapping_can_be_corrected(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "calibrate-upload@example.com")
    _upload(client, headers, project_id, "notes.md", b"Customers open accounts after kyc.passed.", kind="paper")
    item = _upload(client, headers, project_id, "loans.xes", xes_log(bpi_like(60)))
    assert item["job"]["status"] == "succeeded"
    summary = item["calibration"]
    assert summary["status"] == "ready" and summary["format"] == "xes" and summary["cases"] == 60
    assert summary["mapping"]["A_Denied"] == "application.declined" and summary["mapping"]["O_Create Offer"] is None
    assert 0 < summary["mapped_share"] < 1 and summary["summary"]["cases"] == 60
    facts = client.get(f"/projects/{project_id}/facts", headers=headers).json()
    assert not any("A_Create" in json.dumps(row["evidence"]) for row in facts["explicit"] + facts["implied"])

    options = dict(target_trajectory_count=30, event_budget=None, sub_domains=["onboarding_and_kyc", "consumer_credit"], min_events=4, max_events=14)
    plain = _run(client, headers, project_id, None, calibrate=False, **options).json()
    calibrated = _run(client, headers, project_id, None, **options).json()
    assert plain["generation"]["calibration"] is None and plain["generation"]["quality"]["representative"]["status"] != "measured"
    assert calibrated["generation"]["calibration"]["cases"] == 60
    representative = calibrated["generation"]["quality"]["representative"]
    assert representative["status"] == "measured" and 0 <= representative["fitness"] <= 1 and 0 <= representative["precision"] <= 1
    assert _declined_share(calibrated) > _declined_share(plain)
    assert get_sector("banking").hard_checks(__import__("trajectory_contract").TrajectoryBundle.model_validate(calibrated["bundle"])) == []

    remapped = client.put(f"/projects/{project_id}/corpus/{item['id']}/mapping", headers=headers, json={"mapping": {"A_Pending": "application.approved", "W_Validate application": "kyc.started"}})
    assert remapped.status_code == 200 and remapped.json()["calibration"]["mapping"]["W_Validate application"] == "kyc.started"
    assert remapped.json()["calibration"]["mapped_share"] > summary["mapped_share"]
    bad = client.put(f"/projects/{project_id}/corpus/{item['id']}/mapping", headers=headers, json={"mapping": {"A_Pending": "loan.teleported"}})
    assert bad.status_code == 422


class CatalogueFetcher:
    def __init__(self, body: bytes):
        self.body, self.urls = body, []

    def fetch(self, url, kind):
        raise FetchError("not a page")

    def download(self, url, destination, max_bytes):
        self.urls.append(url)
        Path(destination).write_bytes(self.body)
        import hashlib

        return url, "application/gzip", len(self.body), hashlib.sha256(self.body).hexdigest()


def test_the_catalogue_downloads_bpi_2017_on_demand_and_records_its_licence(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    listed = {entry["id"]: entry for entry in client.get("/catalogue").json()["data"]}
    assert listed["bpi2017"]["licence"] == "4TU.ResearchData General Terms of Use" and listed["bpi2017"]["bytes"] == 29_658_747
    assert listed["uci_bank_marketing"]["licence"] == "CC BY 4.0" and listed["cfpb_complaints"]["availability"] == "upload"
    assert {listed[name]["availability"] for name in ("hmda", "freddie_mac", "amlsim", "paysim")} == {"planned"}
    assert "mapping" not in listed["bpi2017"]

    headers, project_id = _study(client, "catalogue@example.com")
    runtime.fetcher = CatalogueFetcher(gzip.compress(xes_log(bpi_like(30))))
    added = client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "bpi2017"}).json()
    assert runtime.fetcher.urls == [listed["bpi2017"]["url"]]
    assert added["ingest"]["licence"] == "4TU.ResearchData General Terms of Use" and added["ingest"]["snapshot_date"]
    project = next(item for item in client.get("/projects", headers=headers).json()["data"] if item["id"] == project_id)
    source = project["corpus"][0]
    assert source["calibration"]["status"] == "ready" and source["calibration"]["cases"] == 30
    # The catalogue's own mapping wins where shared words would not: A_Pending is a granted loan.
    assert source["calibration"]["mapping"]["A_Pending"] == "application.approved"
    assert client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "hmda"}).status_code == 409
    assert "export" in client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "cfpb_complaints"}).json()["detail"]
    assert client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "nope"}).status_code == 404


def uci_archive(rows: list[tuple[str, str]]) -> bytes:
    lines = ['"age";"contact";"duration";"y"'] + [f'30;"{contact}";120;"{answer}"' for contact, answer in rows]
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("bank-additional/bank-additional-full.csv", "\n".join(lines))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("bank-additional.zip", inner.getvalue())
    return outer.getvalue()


def test_uci_bank_marketing_and_a_cfpb_export_calibrate_through_their_adapters(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "adapters@example.com")
    runtime.fetcher = CatalogueFetcher(uci_archive([("cellular", "yes")] * 12 + [("telephone", "no")] * 88))
    uci = client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "uci_bank_marketing"}).json()
    project = next(item for item in client.get("/projects", headers=headers).json()["data"] if item["id"] == project_id)
    uci = next(item for item in project["corpus"] if item["id"] == uci["id"])["calibration"]
    assert uci["format"] == "UCI Bank Marketing" and uci["cases"] == 100 and uci["outcomes"] == {"converted": 0.12}
    assert uci["channels"] == {"call_centre": 100}

    export = (
        "Date received,Product,Issue,Submitted via,Company response to consumer,Timely response?\n"
        + "\n".join(["2025-01-02,Checking or savings account,Fees,Web,Closed with monetary relief,Yes"] * 3 + ["2025-01-03,Checking or savings account,Fees,Phone,Closed with explanation,Yes"] * 7)
    )
    cfpb = _upload(client, headers, project_id, "complaints.csv", export.encode())["calibration"]
    assert cfpb["format"] == "CFPB complaint export" and cfpb["outcomes"] == {"relief": 0.3, "explanation": 0.7}
    assert cfpb["channels"] == {"web": 3, "call_centre": 7}

    _upload(client, headers, project_id, "notes.md", b"Plain notes with nothing to steer.", kind="paper")
    run = _run(client, headers, project_id, None, target_trajectory_count=8, event_budget=None).json()
    assert run["generation"]["steering"]["channel"] == "call_centre"
    assert len(run["generation"]["calibration"]["sources"]) == 2


def test_a_large_calibrated_run_measures_representativeness_across_batches(client, tmp_path, monkeypatch):
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    store._read_json.cache_clear()
    headers, project_id = _study(client, "calibrate-large@example.com")
    _upload(client, headers, project_id, "loans.xes", xes_log(bpi_like(40)))
    run = _run(client, headers, project_id, None, target_trajectory_count=90, event_budget=None, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    assert run["generation"]["storage"] and run["generation"]["calibration"]["cases"] == 40
    assert run["generation"]["quality"]["representative"]["status"] == "measured"


def test_a_catalogue_download_streams_under_the_guard_and_its_limit(tmp_path):
    def resolver(host, port, type=None):
        table = {"data.example": "93.184.216.34", "inside.example": "10.0.0.2"}
        return [(2, 1, 6, "", (table[host], port))]

    def handler(request):
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": "http://inside.example/file"})
        return httpx.Response(200, content=b"x" * 3000)

    transport = httpx.MockTransport(handler)
    final, _, size, digest = safe_download("https://data.example/file", tmp_path / "a", max_bytes=10_000, transport=transport, resolver=resolver)
    assert size == 3000 and len(digest) == 64 and (tmp_path / "a").stat().st_size == 3000
    with pytest.raises(FetchError, match="larger than"):
        safe_download("https://data.example/file", tmp_path / "b", max_bytes=1000, transport=transport, resolver=resolver)
    with pytest.raises(FetchError, match="private network"):
        safe_download("https://data.example/moved", tmp_path / "c", max_bytes=10_000, transport=transport, resolver=resolver)


def test_a_calibration_follows_steps_through_events_a_run_leaves_out():
    calibration = build([[("a", 0.0), ("x", 1.0), ("b", 2.0)]] * 30 + [[("a", 0.0), ("c", 1.0)]] * 10, source="toy")
    seen = calibration.projected({"a", "b", "c"})
    assert dict(seen.transitions["a"]) == {"b": 30.0, "c": 10.0} and "x" not in seen.transitions
    assert dict(calibration.projected({"a", "x", "b", "c"}).transitions["a"]) == {"x": 30, "c": 10}


def test_bpi_2017_through_the_catalogue_moves_the_decision_toward_the_data(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "bpi-decisions@example.com")
    _upload(client, headers, project_id, "notes.md", b"Loan applications are decided after checks.", kind="paper")
    runtime.fetcher = CatalogueFetcher(gzip.compress(xes_log(bpi_like(90))))
    client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "bpi2017"})
    options = dict(target_trajectory_count=40, event_budget=None, sub_domains=["onboarding_and_kyc", "consumer_credit"], min_events=4, max_events=14)
    plain = _run(client, headers, project_id, None, calibrate=False, **options).json()
    calibrated = _run(client, headers, project_id, None, **options).json()
    # One in three applications in the data is denied; the pack's prior declines far fewer.
    assert _declined_share(calibrated) >= 2 * _declined_share(plain) > 0
    assert calibrated["generation"]["quality"]["representative"]["status"] == "measured"
