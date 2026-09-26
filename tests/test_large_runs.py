import json

import pytest

from app import jobs, store
from app.db import SessionLocal
from app.models import Job, Run
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run
from app import runtime


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    import app.generation as generation

    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()


def _large(client, email, **overrides):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    payload = {"target_trajectory_count": 150, "event_budget": None, "min_events": 6, "max_events": 16}
    payload.update(overrides)
    created = _run(client, headers, project_id, credential_id, **payload)
    assert created.status_code == 200, created.text
    return headers, created.json()


def test_a_large_run_is_written_in_batches_and_read_a_journey_at_a_time(client):
    headers, run = _large(client, "large@example.com")
    assert run["status"] == "generated"
    assert run["bundle"] is None and run["bundle_source"] == "paged"
    generation = run["generation"]
    assert generation["primary_trajectories"] == 150
    assert generation["storage"]["batches"] == 4
    assert generation["quality"]["complete"]["passed"] is True
    assert generation["overview"]["journeys"] == 150
    assert sum(variant["count"] for variant in generation["overview"]["variants"]) <= 150

    page = client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"offset": 40, "limit": 20}).json()
    assert page["total"] == 150 and page["paged"] is True and len(page["data"]) == 20
    first = page["data"][0]
    assert first["trajectory_id"].startswith("B0001.")
    journey = client.get(f"/runs/{run['id']}/journeys/{first['trajectory_id']}", headers=headers).json()
    assert journey["trajectories"][0]["trajectory_id"] == first["trajectory_id"]
    assert {event["event_id"].split(".")[0] for event in journey["events"]} == {"B0001"}
    assert journey["samples"]

    variant = generation["overview"]["variants"][0]
    filtered = client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"variant": variant["id"]}).json()
    assert filtered["total"] == variant["count"]

    event_id = journey["events"][2]["event_id"]
    note = client.post(f"/runs/{run['id']}/feedback", headers=headers, json={"target_type": "event", "target_id": event_id, "stance": "drop", "comment": "No."})
    assert note.status_code == 200, note.text
    assert client.post(f"/runs/{run['id']}/feedback", headers=headers,
                       json={"target_type": "event", "target_id": "B0009.E99999", "stance": "drop", "comment": "No."}).status_code == 422
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers).status_code == 409


def test_a_rerun_of_a_large_run_resolves_notes_without_loading_the_parent(client):
    headers, run = _large(client, "large-rerun@example.com")
    first = client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"limit": 1}).json()["data"][0]
    journey = client.get(f"/runs/{run['id']}/journeys/{first['trajectory_id']}", headers=headers).json()
    dropped = next(event for event in journey["events"] if event["event_type"] == "kyc.started")
    note = client.post(f"/runs/{run['id']}/feedback", headers=headers,
                       json={"target_type": "event", "target_id": dropped["event_id"], "stance": "drop", "comment": "Skip KYC."}).json()
    child = client.post(f"/runs/{run['id']}/rerun", headers=headers, json={"feedback_ids": [note["id"]]}).json()
    assert child["status"] == "generated"
    page = client.get(f"/runs/{child['id']}/journeys", headers=headers, params={"limit": 5}).json()
    for entry in page["data"]:
        detail = client.get(f"/runs/{child['id']}/journeys/{entry['trajectory_id']}", headers=headers).json()
        assert all(event["event_type"] != "kyc.started" for event in detail["events"])


def test_a_large_run_is_judged_on_a_sample_drawn_across_its_batches(client):
    headers, run = _large(client, "large-judge@example.com")
    runtime.judge = RecordingJudge()
    judged = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert judged.status_code == 200, judged.text
    cycle = judged.json()["cycles"][-1]
    assert cycle["hard_check_passed"] is True
    sampled = [entry["trajectory_id"] for entry in cycle["sample"]]
    assert len(sampled) == 6
    listed = {row["trajectory_id"] for row in client.get(f"/runs/{run['id']}/journeys", headers=headers, params={"limit": 500}).json()["data"]}
    assert set(sampled) <= listed
    assert len({trajectory_id.split(".")[0] for trajectory_id in sampled}) > 1
    runtime.judge = None


def test_a_crashed_run_resumes_from_its_last_batch_and_matches_an_uninterrupted_one(client, monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    headers, interrupted = _large(client, "crash@example.com")
    _, reference = _large(client, "reference@example.com")
    import app.generation as generation

    real = generation.write_json
    calls = {"batches": 0}

    def crash_after_two(path, value):
        real(path, value)
        if path.name.endswith(".json.gz"):
            calls["batches"] += 1
            if calls["batches"] == 2:
                raise SystemExit("worker killed")

    monkeypatch.setattr(generation, "write_json", crash_after_two)
    with pytest.raises(SystemExit):
        jobs.run_job(interrupted["job"]["id"])
    monkeypatch.setattr(generation, "write_json", real)
    db = SessionLocal()
    job = db.get(Job, interrupted["job"]["id"])
    assert job.status == "running"
    job.status = "queued"
    db.commit()
    db.close()
    assert jobs.Worker().run_next() is True
    assert jobs.Worker().run_next() is True
    resumed = client.get(f"/runs/{interrupted['id']}", headers=headers).json()
    assert resumed["status"] == "generated"
    assert resumed["generation"]["primary_trajectories"] == 150
    root = store.run_dir(interrupted["id"])
    state = json.loads((root / "state.json").read_text())
    assert state["done"] == ["B0000", "B0001", "B0002", "B0003"]
    assert jobs.Worker().run_next() is False
    reference_headers = _auth_login(client, "reference@example.com")
    ours = client.get(f"/runs/{interrupted['id']}/journeys", headers=headers, params={"limit": 500}).json()["data"]
    theirs = client.get(f"/runs/{reference['id']}/journeys", headers=reference_headers, params={"limit": 500}).json()["data"]
    strip = lambda rows: [(row["trajectory_id"], row["trajectory_type"], row["events"], row["variant"]) for row in rows]
    assert strip(ours) == strip(theirs)


def _auth_login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": "password-123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_a_large_run_can_be_cancelled_between_batches(client, monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    headers, run = _large(client, "large-cancel@example.com")
    import app.generation as generation

    real = generation.write_json

    def cancel_after_first(path, value):
        real(path, value)
        if path.name == "B0000.json.gz":
            db = SessionLocal()
            db.get(Job, run["job"]["id"]).cancel_requested = 1
            db.commit()
            db.close()

    monkeypatch.setattr(generation, "write_json", cancel_after_first)
    monkeypatch.setattr(jobs, "PROGRESS_EVERY_SECONDS", 0.0)
    assert jobs.Worker().run_next() is True
    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert after["status"] == "cancelled"
    assert after["job"]["status"] == "cancelled"


def test_a_run_above_the_sequence_limit_is_refused(client):
    headers = _auth(client, "too-big@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    refused = _run(client, headers, project_id, credential_id, target_trajectory_count=20_000, group_size=8)
    assert refused.status_code == 422
    assert "at most" in refused.json()["detail"]
