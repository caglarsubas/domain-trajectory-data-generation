from sqlalchemy import func, select

from app import jobs, runtime, store
from app.db import SessionLocal
from app.models import EvalCycle, EvalVerdict, Feedback, Job, Run
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run


def _study(client, email, kind="user"):
    headers = _auth(client, email, "password-123", kind=kind)
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_deleting_a_run_removes_its_records_and_detaches_what_came_from_it(client):
    headers, project_id, credential_id = _study(client, "delete@example.com")
    parent = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    runtime.judge = RecordingJudge()
    assert client.post(f"/runs/{parent['id']}/evaluate", headers=headers, json={}).status_code == 200
    note = client.post(
        f"/runs/{parent['id']}/feedback",
        headers=headers,
        json={"target_type": "run", "target_id": parent["id"], "stance": "keep", "comment": "Keep it."},
    )
    assert note.status_code == 200, note.text
    child = client.post(f"/runs/{parent['id']}/rerun", headers=headers, json={}).json()
    assert child["parent_run_id"] == parent["id"]
    with SessionLocal() as db:
        cycle_ids = list(db.scalars(select(EvalCycle.id).where(EvalCycle.run_id == parent["id"])))
        assert cycle_ids and _count(db, EvalVerdict, EvalVerdict.cycle_id.in_(cycle_ids)) > 0
        job_ids = list(db.scalars(select(Job.id).where(Job.run_id == parent["id"])))
        assert job_ids

    assert client.delete(f"/runs/{parent['id']}", headers=headers).status_code == 204

    assert client.get(f"/runs/{parent['id']}", headers=headers).status_code == 404
    assert parent["id"] not in {item["id"] for item in client.get("/runs", headers=headers).json()["data"]}
    kept = client.get(f"/runs/{child['id']}", headers=headers)
    assert kept.status_code == 200 and kept.json()["parent_run_id"] is None
    with SessionLocal() as db:
        assert _count(db, EvalCycle, EvalCycle.run_id == parent["id"]) == 0
        assert _count(db, EvalVerdict, EvalVerdict.cycle_id.in_(cycle_ids)) == 0
        assert _count(db, Feedback, Feedback.run_id == parent["id"]) == 0
        # The jobs stay, without their run, so quotas still count them.
        assert {job.run_id for job in db.scalars(select(Job).where(Job.id.in_(job_ids)))} == {None}
    assert client.delete(f"/runs/{parent['id']}", headers=headers).status_code == 404


def test_only_the_owner_can_delete_a_run(client):
    headers, project_id, credential_id = _study(client, "owner@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    stranger = _auth(client, "stranger@example.com", "password-123")
    assert client.delete(f"/runs/{run['id']}", headers=stranger).status_code == 404
    assert client.get(f"/runs/{run['id']}", headers=headers).status_code == 200


def test_a_run_with_a_job_still_working_is_not_deleted(client, monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")
    headers, project_id, credential_id = _study(client, "busy@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    refused = client.delete(f"/runs/{run['id']}", headers=headers)
    assert refused.status_code == 409
    assert "Cancel it first" in refused.json()["detail"]
    assert client.post(f"/runs/{run['id']}/cancel", headers=headers).status_code == 200
    assert client.delete(f"/runs/{run['id']}", headers=headers).status_code == 204
    assert jobs.Worker().run_next() is False


def test_deleting_a_demo_run_does_not_free_its_daily_quota(client, monkeypatch):
    monkeypatch.setenv("DEMO_RUNS_PER_DAY", "1")
    headers, project_id, credential_id = _study(client, "demo-delete@example.com", kind="demo")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    assert client.delete(f"/runs/{run['id']}", headers=headers).status_code == 204
    assert _run(client, headers, project_id, credential_id, target_trajectory_count=4).status_code == 429


def test_deleting_a_large_run_removes_its_files(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    import app.generation as generation

    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id, credential_id = _study(client, "large-delete@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=150, event_budget=None, min_events=6, max_events=16).json()
    root = store.run_dir(run["id"])
    assert root.is_dir() and any(root.iterdir())
    assert client.delete(f"/runs/{run['id']}", headers=headers).status_code == 204
    assert not root.exists()
    with SessionLocal() as db:
        assert db.get(Run, run["id"]) is None
