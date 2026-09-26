from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluation import DEFAULT_THRESHOLDS
from app.store import MAX_RUN_SEQUENCES
from app.models import Account, CorpusItem, Credential, Feedback, Project, Run
from app.schemas import RerunBody, RunBody
from sectors.registry import get_sector


def require_project(db: Session, project_id: str, owner: Account) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_id != owner.id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def require_run(db: Session, run_id: str, owner: Account) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.owner_id != owner.id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def config_from_body(body: RunBody, account: Account, db: Session) -> dict:
    try:
        sector = get_sector(body.sector)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    code = body.language.split("-")[0].strip().lower()
    if code not in sector.languages:
        supported = " or ".join(sector.languages)
        raise HTTPException(status_code=422, detail=f"{sector.label} runs are written in {supported}; {body.language} is not supported yet")
    unknown = [name for name in body.sub_domains if name not in sector.sub_domains]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown sub-domains: {', '.join(unknown)}")
    if body.target_trajectory_count * body.group_size > MAX_RUN_SEQUENCES:
        raise HTTPException(
            status_code=422,
            detail=f"a run holds at most {MAX_RUN_SEQUENCES:,} sequences; {body.target_trajectory_count:,} prompts × {body.group_size} is more",
        )
    if body.min_events > body.max_events:
        raise HTTPException(status_code=422, detail="min_events cannot exceed max_events")
    project = require_project(db, body.project_id, account)
    if project.sector != body.sector:
        raise HTTPException(status_code=422, detail="run sector must match the study")
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project.id)))
    if body.start_mode == "warm" and not items:
        raise HTTPException(status_code=422, detail="warm start requires at least one corpus document")
    if body.start_mode == "cold" and not body.cold_start_acknowledged:
        raise HTTPException(status_code=422, detail="cold start requires acknowledgment")
    credential = db.get(Credential, body.credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="credential not found")
    if credential.scope == "platform" and account.kind != "admin":
        raise HTTPException(status_code=403, detail="platform credentials are only available to admin")
    if account.kind in {"user", "demo"} and credential.scope != "byok":
        raise HTTPException(status_code=403, detail="user and demo runs require your own key")
    if not credential.ready:
        raise HTTPException(status_code=422, detail="credential is not ready for deep search")
    thresholds = {**DEFAULT_THRESHOLDS, **(body.thresholds or {})}
    return {
        "sector": sector.id,
        "target_trajectory_count": body.target_trajectory_count,
        "event_budget": body.event_budget,
        "min_events": body.min_events,
        "max_events": body.max_events,
        "max_assistant_turns": body.max_assistant_turns,
        "sub_domains": body.sub_domains,
        "language": body.language,
        "start_mode": body.start_mode,
        "cold_start_acknowledged": body.cold_start_acknowledged,
        "reward_mechanism": body.reward_mechanism,
        "signal_mechanism": body.signal_mechanism,
        "consumer": body.consumer,
        "target_family": body.target_family,
        "thresholds": thresholds,
        "max_cycles": body.max_cycles,
        "group_size": body.group_size,
        "credential_id": credential.id,
    }


def rerun_config(parent: Run, body: RerunBody, account: Account, db: Session) -> tuple[dict, list[str]]:
    merged = dict(parent.config)
    overrides = body.model_dump(exclude_unset=True, exclude={"feedback_ids"})
    merged.update({key: value for key, value in overrides.items() if value is not None})
    try:
        validated = RunBody(project_id=parent.project_id, **merged)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config = config_from_body(validated, account, db)
    known = {row.id for row in db.scalars(select(Feedback).where(Feedback.run_id == parent.id))}
    missing = [item for item in body.feedback_ids if item not in known]
    if missing:
        raise HTTPException(status_code=422, detail="feedback does not belong to the parent run")
    return config, list(body.feedback_ids)
