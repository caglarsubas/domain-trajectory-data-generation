from datetime import datetime, timedelta, timezone

import pytest

from app import jobs
from app.db import SessionLocal
from app.models import Job
from test_api import _auth, _link, _project, _ready_key, _run


@pytest.fixture()
def worker_mode(monkeypatch):
    monkeypatch.setenv("JOBS_MODE", "worker")


def _queued_run(client, email, **overrides):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None, **overrides)
    assert created.status_code == 200, created.text
    return headers, created.json()


def test_a_run_waits_in_the_queue_until_a_worker_generates_it(client, worker_mode):
    headers, run = _queued_run(client, "queue@example.com")
    assert run["status"] == "queued"
    assert run["bundle"] is None and run["bundle_source"] == "pending"
    assert run["job"]["status"] == "queued"
    assert client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).status_code == 409
    assert client.post(f"/runs/{run['id']}/feedback", headers=headers,
                       json={"target_type": "run", "target_id": run["id"], "stance": "keep", "comment": "Early."}).status_code == 409
    assert client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers).status_code == 409

    assert jobs.Worker().run_next() is True
    done = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert done["status"] == "generated"
    assert done["job"]["status"] == "succeeded" and done["job"]["progress"] == 1.0
    assert len(done["bundle"]["samples"]) >= 1
    assert jobs.Worker().run_next() is False


def test_a_queued_run_can_be_cancelled_before_it_starts(client, worker_mode):
    headers, run = _queued_run(client, "cancel-early@example.com")
    cancelled = client.post(f"/runs/{run['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert jobs.Worker().run_next() is False
    assert client.post(f"/runs/{run['id']}/cancel", headers=headers).status_code == 409


def test_a_running_job_stops_at_its_next_progress_report(client, worker_mode):
    headers, run = _queued_run(client, "cancel-running@example.com")
    db = SessionLocal()
    job = db.get(Job, run["job"]["id"])
    job.status = "running"
    job.cancel_requested = 1
    db.commit()
    db.close()
    jobs.run_job(run["job"]["id"])
    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert after["status"] == "cancelled"
    assert after["job"]["status"] == "cancelled"
    assert after["bundle"] is None


def test_a_failing_job_marks_the_run_failed_with_its_error(client, worker_mode, monkeypatch):
    headers, run = _queued_run(client, "fail@example.com")

    def boom(db, job, report):
        raise RuntimeError("generator exploded")

    monkeypatch.setitem(jobs.HANDLERS, "generate", boom)
    assert jobs.Worker().run_next() is True
    after = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert after["status"] == "failed"
    assert after["job"]["status"] == "failed"
    assert "generator exploded" in after["job"]["error"]


def test_a_job_whose_worker_stopped_is_requeued(client, worker_mode):
    _headers, run = _queued_run(client, "stale@example.com")
    db = SessionLocal()
    job = db.get(Job, run["job"]["id"])
    job.status = "running"
    job.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    assert jobs.requeue_stale(db) == 1
    db.refresh(job)
    assert job.status == "queued"
    db.close()


def test_the_run_list_carries_summaries_not_bundles(client):
    headers, run = _queued_run(client, "list@example.com")
    listed = client.get("/runs", headers=headers).json()["data"]
    assert len(listed) == 1
    item = listed[0]
    assert "bundle" not in item
    assert item["status"] == "generated"
    assert item["generation"]["primary_trajectories"] >= 1
    assert item["job"]["status"] == "succeeded"


def test_a_rerun_needs_a_generated_parent(client, worker_mode):
    headers, run = _queued_run(client, "rerun-early@example.com")
    assert client.post(f"/runs/{run['id']}/rerun", headers=headers, json={"feedback_ids": []}).status_code == 409


def test_modes_follow_the_database_unless_set(monkeypatch):
    monkeypatch.delenv("JOBS_MODE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    assert jobs.mode() == "inline"
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    assert jobs.mode() == "thread"
    monkeypatch.setenv("JOBS_MODE", "worker")
    assert jobs.mode() == "worker"
