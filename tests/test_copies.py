import gzip
import json
from types import SimpleNamespace

from app import store
from app.copies import CopyGuard, _rows, guard_for
from test_api import _auth, _project, _run

OPEN = {"allow_unaccepted": "true"}
NARRATIVE = "I was charged twice for the same card payment and the branch told me to wait three weeks for my refund"


def test_a_twelve_word_run_is_a_copy_whatever_its_case_and_punctuation():
    guard = CopyGuard()
    guard.index(NARRATIVE)
    assert guard.copied({"turns": [{"text": "Customer said: I WAS charged twice, for the same card payment and the branch told me to wait."}]})
    # Eleven words in a row are not a copy.
    assert not guard.copied("the same card payment and the branch told me to wait")
    assert not guard.copied({"text": "A synthetic journey with its own words, long enough to be checked, and nothing borrowed."})
    assert not CopyGuard().copied(NARRATIVE)


def test_data_source_rows_long_enough_are_indexed_plain_or_gzipped(tmp_path):
    rows = "case,activity,timestamp,narrative\nC1,complaint.received,2024-01-01," + NARRATIVE + "\nC2,complaint.resolved,2024-01-02,Resolved\n"
    plain = tmp_path / "complaints.csv"
    plain.write_text(rows)
    packed = tmp_path / "complaints.csv.gz"
    packed.write_bytes(gzip.compress(rows.encode(), mtime=0))
    assert list(_rows(plain)) == list(_rows(packed))
    items = [SimpleNamespace(kind="data_source", storage_path=str(packed)), SimpleNamespace(kind="paper", storage_path=str(tmp_path / "missing.md"))]
    guard = guard_for(SimpleNamespace(scalars=lambda _query: items), SimpleNamespace(project_id="p"))
    assert guard.rows == 1 and guard.documents == 0 and guard.copied(NARRATIVE.upper())


def _long_turn(samples):
    for sample in samples:
        for sequence in sample["sequences"]:
            for segment in sequence["contexts"][0]["segments"]:
                if segment["role"] == "assistant" and len(segment["text"].split()) >= 14:
                    return segment["text"]
    raise AssertionError("no assistant turn long enough to plant")


def _plant(client, headers, project_id, text):
    upload = client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"},
                         files={"upload": ("leak.md", f"Our internal notes quote a customer:\n\n{text}\n".encode(), "text/markdown")})
    assert upload.status_code == 200, upload.text


def test_an_export_leaves_out_records_that_copy_an_upload(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "copies-small@example.com", "password-123")
    project_id = _project(client, headers)
    run = _run(client, headers, project_id, None, target_trajectory_count=8, event_budget=None, group_size=2, max_assistant_turns=3,
               start_mode="cold", cold_start_acknowledged=True, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    base = f"/runs/{run['id']}/export"
    clean = client.get(f"{base}/manifest.json", headers=headers, params=OPEN).json()
    assert clean["copies"]["left_out"] == {} and clean["copies"]["windows"] == 0
    samples = [json.loads(line) for line in client.get(f"{base}/samples.jsonl", headers=headers, params=OPEN).text.splitlines()]
    turn = _long_turn(samples)

    _plant(client, headers, project_id, turn)
    manifest = client.get(f"{base}/manifest.json", headers=headers, params=OPEN).json()
    left = manifest["copies"]["left_out"]
    kept = [json.loads(line) for line in client.get(f"{base}/samples.jsonl", headers=headers, params=OPEN).text.splitlines()]
    # Every sample holding the planted turn is left out, and so is any other sharing a 12-word run with it.
    holding = sum(1 for sample in samples if turn in json.dumps(sample, ensure_ascii=False))
    assert manifest["copies"]["documents"] == 1 and left["samples.jsonl"] >= holding >= 1
    assert len(kept) == len(samples) - left["samples.jsonl"] == manifest["counts"]["samples"]
    assert all(turn not in json.dumps(sample, ensure_ascii=False) for sample in kept)
    prefixes = client.get(f"{base}/prefixes.jsonl", headers=headers, params=OPEN).text
    assert turn not in prefixes and left["prefixes.jsonl"] >= 1


def test_a_large_run_export_job_leaves_out_copies_too(client, tmp_path, monkeypatch):
    import app.generation as generation

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers = _auth(client, "copies-large@example.com", "password-123")
    project_id = _project(client, headers)
    run = _run(client, headers, project_id, None, target_trajectory_count=60, event_budget=None, group_size=2, max_assistant_turns=3,
               start_mode="cold", cold_start_acknowledged=True, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    page = client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"limit": 5}).json()["data"]
    journey = client.get(f"/runs/{run['id']}/journeys/{page[0]['trajectory_id']}", headers=headers).json()
    _plant(client, headers, project_id, _long_turn(journey["samples"]))
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={"allow_unaccepted": True}).status_code == 200
    manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers, params=OPEN).json()
    assert manifest["copies"]["left_out"]["samples.jsonl"] >= 1
    assert manifest["counts"]["samples"] + manifest["copies"]["left_out"]["samples.jsonl"] == 60
