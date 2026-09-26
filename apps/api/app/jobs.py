"""A small job queue on the application database.

Jobs are rows. A worker claims the oldest queued job, runs its handler, and records progress,
the result, or the error. `JOBS_MODE` decides who runs them:

- `inline`: the request that enqueues a job runs it before answering (SQLite and tests).
- `thread`: a background thread in the API process polls for jobs (Postgres without a worker).
- `worker`: a separate process, `python -m app.worker`, polls for jobs (Docker Compose).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Job, Run

ACTIVE = ("queued", "running")
POLL_SECONDS = 1.0
PROGRESS_EVERY_SECONDS = 0.5
STALE_AFTER = timedelta(minutes=5)


class JobCancelled(Exception):
    pass


def mode() -> str:
    configured = os.environ.get("JOBS_MODE", "").strip().lower()
    if configured in {"inline", "thread", "worker"}:
        return configured
    return "inline" if os.environ.get("DATABASE_URL", "sqlite").startswith("sqlite") else "thread"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db, *, kind: str, owner_id: str, run_id: str | None, payload: dict) -> Job:
    job = Job(kind=kind, owner_id=owner_id, run_id=run_id, payload=payload, status="queued", message="Waiting for a worker.")
    db.add(job)
    db.commit()
    db.refresh(job)
    if mode() == "inline":
        run_job(job.id)
        db.expire_all()
    return job


def latest_for(db, run_id: str) -> Job | None:
    return db.scalars(select(Job).where(Job.run_id == run_id).order_by(Job.created_at.desc())).first()


def cancel(db, job: Job) -> Job:
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = _now()
        job.message = "Cancelled before it started."
        _mark_run(db, job, "cancelled")
    elif job.status == "running":
        job.cancel_requested = 1
        job.message = "Cancelling."
    db.commit()
    return job


def claim_next(db) -> Job | None:
    """Claim the oldest queued job. Postgres skips rows another worker holds; the conditional update settles races."""
    candidate = db.scalars(
        select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1).with_for_update(skip_locked=True)
    ).first()
    if candidate is None:
        db.rollback()
        return None
    claimed = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == "queued")
        .values(status="running", started_at=_now(), heartbeat_at=_now(), attempts=Job.attempts + 1, message="Started.")
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    db.refresh(candidate)
    return candidate


def requeue_stale(db, *, older_than: timedelta = STALE_AFTER) -> int:
    """Put back running jobs whose worker stopped sending heartbeats, so a restart picks them up."""
    cutoff = _now() - older_than
    result = db.execute(
        update(Job)
        .where(Job.status == "running", Job.heartbeat_at < cutoff)
        .values(status="queued", message="Requeued after the worker stopped.")
    )
    db.commit()
    return result.rowcount or 0


class Reporter:
    """Progress callback handed to a handler. Raises JobCancelled once a cancel is requested."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last = 0.0

    def __call__(self, done: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        if done < total and now - self._last < PROGRESS_EVERY_SECONDS:
            return
        self._last = now
        db = SessionLocal()
        try:
            job = db.get(Job, self.job_id)
            if job is None:
                return
            if job.cancel_requested:
                raise JobCancelled()
            job.progress = round(done / total, 4) if total else 0.0
            job.message = message or f"{done} of {total}."
            job.heartbeat_at = _now()
            db.commit()
        finally:
            db.close()


def run_job(job_id: str) -> None:
    """Run one job to an end state. Claims it first when it is still queued."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None or job.status in {"succeeded", "failed", "cancelled"}:
            return
        if job.status == "queued":
            job.status = "running"
            job.started_at = _now()
            job.heartbeat_at = _now()
            job.attempts += 1
            db.commit()
        handler = HANDLERS[job.kind]
        try:
            message = handler(db, job, Reporter(job.id))
        except JobCancelled:
            db.rollback()
            _finish(db, job_id, "cancelled", "Cancelled.")
            return
        except HTTPException as exc:
            db.rollback()
            _finish(db, job_id, "failed", str(exc.detail), error=str(exc.detail))
            return
        except Exception as exc:  # noqa: BLE001 - a job must end in a state the studio can show
            db.rollback()
            _finish(db, job_id, "failed", "The job failed.", error=f"{type(exc).__name__}: {exc}")
            return
        _finish(db, job_id, "succeeded", message or "Done.")
    finally:
        db.close()


def _finish(db, job_id: str, status: str, message: str, *, error: str | None = None) -> None:
    job = db.get(Job, job_id)
    job.status = status
    job.message = message
    job.error = error
    job.finished_at = _now()
    if status == "succeeded":
        job.progress = 1.0
    db.commit()
    if status in {"failed", "cancelled"}:
        _mark_run(db, job, status)
        db.commit()


def _mark_run(db, job: Job, status: str) -> None:
    if job.kind == "generate" and job.run_id:
        run = db.get(Run, job.run_id)
        if run is not None and run.status in {"queued", "generating"}:
            run.status = status


def _generate(db, job: Job, report: Callable) -> str:
    from app.generation import candidate_for_run, generate_batched, is_large
    from app.models import Feedback

    run = db.get(Run, job.run_id)
    run.status = "generating"
    db.commit()
    parent = db.get(Run, run.parent_run_id) if run.parent_run_id else None
    feedback_ids = list(job.payload.get("feedback_ids") or [])
    rows = []
    if feedback_ids:
        rows = list(db.scalars(select(Feedback).where(Feedback.id.in_(feedback_ids))))
        order = {item: index for index, item in enumerate(feedback_ids)}
        rows.sort(key=lambda row: order.get(row.id, 0))
    if is_large(run.config):
        run.generation = generate_batched(db, run, feedback_rows=rows, parent=parent, progress=report)
        run.candidate = None
    else:
        bundle = candidate_for_run(db, run.config, project_id=run.project_id, feedback_rows=rows, parent=parent, progress=report)
        run.candidate = bundle.model_dump(mode="json")
        run.generation = run.candidate.get("generation")
    run.status = "generated"
    db.commit()
    meta = run.generation or {}
    unit = "groups" if (meta.get("group_size") or 1) > 1 else "journeys"
    return f"Generated {meta.get('primary_trajectories', 0)} {unit}."


HANDLERS: dict[str, Callable] = {"generate": _generate}


class Worker:
    """Poll for queued jobs and run them one at a time."""

    def __init__(self, poll_seconds: float = POLL_SECONDS) -> None:
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    def run_next(self) -> bool:
        db = SessionLocal()
        try:
            job = claim_next(db)
        finally:
            db.close()
        if job is None:
            return False
        run_job(job.id)
        return True

    def loop(self) -> None:
        db = SessionLocal()
        try:
            requeue_stale(db)
        finally:
            db.close()
        while not self._stop.is_set():
            if not self.run_next():
                self._stop.wait(self.poll_seconds)

    def stop(self) -> None:
        self._stop.set()

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.loop, name="job-worker", daemon=True)
        thread.start()
        return thread
