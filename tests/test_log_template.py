"""Calibration people can bring (Slice 15, decision 21): a template per pack for a team's own log, and a preview of what
the mapped log would change before a run."""

import csv
import io
from dataclasses import replace

import pytest

from app.calibrate import _event_log, suggest
from app.eventlog import read_cases
from app.log_template import _paths, _shares
from sectors.journeys import generate_bundle
from sectors.registry import get_sector, known_sectors
from sectors.telecom.corpus import CorpusSteering, steering_from_text
from sectors.telecom.spec import PACK as TELECOM
from test_api import _auth
from test_calibration import _upload

# A team whose customers abandon orders, fail credit checks and ports, and see plan changes declined far more often than
# the pack's priors say: the process its log comes from.
BOOST = {"order.abandoned": 4.0, "credit_check.failed": 3.0, "port.failed": 3.0, "plan.change_declined": 3.0}
TEAM = replace(TELECOM, lifecycle=replace(TELECOM.lifecycle, events=tuple(
    replace(spec, weight=spec.weight * BOOST.get(spec.event_type, 1.0)) for spec in TELECOM.lifecycle.events
)))


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize("sector_id", known_sectors())
def test_every_pack_offers_its_events_and_an_example_log_the_reader_takes(client, tmp_path, sector_id):
    sector = get_sector(sector_id)
    events = client.get(f"/sectors/{sector_id}/log-template/events.csv")
    assert events.status_code == 200 and events.headers["content-type"].startswith("text/csv")
    assert f'filename="{sector_id}-events.csv"' in events.headers["content-disposition"]
    rows = _rows(events.text)
    assert [row["activity"] for row in rows] == [spec.event_type for spec in sector.lifecycle.events]
    assert all(row["meaning"] and set(row["sub_domains"].split()) <= set(sector.sub_domains) for row in rows)

    example = client.get(f"/sectors/{sector_id}/log-template/example.csv")
    assert example.status_code == 200 and example.text.splitlines()[0] == "case_id,activity,timestamp"
    path = tmp_path / "example.csv"
    path.write_text(example.text)
    kind, cases = read_cases(path)
    cases = list(cases)
    # Some journeys end early, an abandoned order after a step or two, as a team's own would.
    assert kind and len(cases) == 20 and all(len(case) >= 2 for case in cases)
    # Named as the pack's events, every activity maps to itself, and every event is timed.
    activities = {name for case in cases for name, _ in case}
    assert activities <= set(sector.event_namespace)
    assert all(when is not None for case in cases for _, when in case)
    assert suggest(sorted(activities), tuple(sector.event_namespace)) == {name: name for name in activities}


def test_a_template_asks_for_a_known_pack_and_part(client):
    assert client.get("/sectors/nowhere/log-template/events.csv").status_code == 404
    assert client.get("/sectors/telecom/log-template/other.csv").status_code == 404


def test_an_activity_named_as_an_event_is_that_event_before_any_guess():
    namespace = tuple(get_sector("telecom").event_namespace)
    found = suggest(["credit_check.passed", " port.failed ", "Order Submitted"], namespace)
    assert found["credit_check.passed"] == "credit_check.passed" and found[" port.failed "] == "port.failed"
    # Anything else is still matched by shared words.
    assert found["Order Submitted"] == "order.submitted"


def _team_log(journeys: int = 600) -> str:
    sector = get_sector("telecom")
    bundle = generate_bundle(TEAM, steering_from_text, CorpusSteering(None, None, (), ()), sub_domains=list(sector.sub_domains), language="en",
                             target_trajectory_count=journeys, event_budget=None, min_events=4, max_events=24, max_assistant_turns=3, start_mode="cold",
                             reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training", target_family="llm", seed="team-log",
                             group_size=1, materialization_cap=journeys)
    events = {event.event_id: event for event in bundle.events}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["case_id", "activity", "timestamp"])
    for number, trajectory in enumerate([item for item in bundle.trajectories if item.parent_trajectory_id is None], start=1):
        for event_id in trajectory.event_ids:
            writer.writerow([f"case-{number:04d}", events[event_id].event_type, events[event_id].event_time.strftime("%Y-%m-%dT%H:%M:%SZ")])
    return out.getvalue()


def test_a_telecom_log_from_the_template_calibrates_a_run_and_its_preview_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "team-log@example.com", "password-123")
    project = client.post("/projects", headers=headers, json={"name": "Our own orders", "sector": "telecom"}).json()
    log = _team_log()
    item = _upload(client, headers, project["id"], "orders.csv", log.encode())
    corpus = next(entry for entry in client.get("/projects", headers=headers).json()["data"] if entry["id"] == project["id"])["corpus"]
    calibration = next(entry for entry in corpus if entry["id"] == item["id"])["calibration"]
    # Written from the template, the log maps whole, each activity to its own event.
    assert calibration["status"] == "ready" and calibration["mapped_share"] == 1.0
    assert all(activity == event for activity, event in calibration["mapping"].items())

    preview = calibration["preview"]
    assert preview["journeys"] == 600 and preview["unseen"] == []
    moves = {(move["after"], move["next"]): move for move in preview["moves"]}
    abandoned = moves[("order.started", "order.abandoned")]
    # The team abandons a third of its orders; the pack's prior, about one in eight; calibration follows the data.
    assert abandoned["pack"] < 0.2 and abs(abandoned["calibrated"] - abandoned["data"]) < 0.05
    assert preview["divergence"]["calibrated"] < preview["divergence"]["uncalibrated"]

    # A run calibrated from the same log lands within 0.1 of the data's next-step shares, and the preview's shares
    # are the ones such a run takes.
    path = tmp_path / "orders-again.csv"
    path.write_text(log)
    found = _event_log(path, tuple(get_sector("telecom").event_namespace), None, None, None)
    run = get_sector("telecom").generate(
        sub_domains=list(get_sector("telecom").sub_domains), language="en", target_trajectory_count=1500, event_budget=None, min_events=4,
        max_events=24, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
        consumer="post_training", target_family="llm", seed="check", group_size=1, materialization_cap=1500, calibration=found["calibration"],
    )
    representative = run.generation.quality["representative"]
    assert representative["next_step_divergence"] < 0.1 and representative["weighted_divergence"] <= preview["divergence"]["calibrated"] + 0.01
    shares = _shares(_paths(run))
    assert all(abs(shares[move["after"]].get(move["next"], 0.0) - move["calibrated"]) < 0.1 for move in preview["moves"])
