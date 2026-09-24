from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CorpusItem, EvalCycle, EvalVerdict, Feedback, Project, Run
from sectors.banking.generate import GENERATOR_ID
from trajectory_contract import banking_fixture


def corpus_out(item: CorpusItem) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "uri": item.uri,
        "content_hash": item.content_hash,
        "provenance": item.provenance,
        "created_at": item.created_at.isoformat(),
    }


def project_out(project: Project, db: Session) -> dict:
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project.id)))
    return {
        "id": project.id,
        "name": project.name,
        "sector": project.sector,
        "created_at": project.created_at.isoformat(),
        "corpus": [corpus_out(item) for item in items],
    }


def feedback_out(row: Feedback) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "stance": row.stance,
        "comment": row.comment,
        "created_at": row.created_at.isoformat(),
    }


def run_out(run: Run, db: Session) -> dict:
    cycles = list(
        db.scalars(select(EvalCycle).where(EvalCycle.run_id == run.id).order_by(EvalCycle.cycle_index))
    )
    cycle_payload = []
    for cycle in cycles:
        verdicts = list(db.scalars(select(EvalVerdict).where(EvalVerdict.cycle_id == cycle.id)))
        cycle_payload.append(
            {
                "id": cycle.id,
                "cycle_index": cycle.cycle_index,
                "hard_check_passed": bool(cycle.hard_check_passed),
                "hard_check_errors": cycle.hard_check_errors,
                "reference_quality": cycle.reference_quality,
                "accepted": bool(cycle.accepted),
                "revision_notes": cycle.revision_notes,
                "judge_tenant": cycle.judge_tenant,
                "judge_org_id": cycle.judge_org_id,
                "judge_key_id": cycle.judge_key_id,
                "verdicts": [
                    {
                        "rubric": row.rubric,
                        "score": row.score,
                        "parsed": row.parsed,
                        "raw": row.raw,
                        "judge_model": row.judge_model,
                        "duration_ms": row.duration_ms,
                    }
                    for row in verdicts
                ],
            }
        )
    notes = list(db.scalars(select(Feedback).where(Feedback.run_id == run.id).order_by(Feedback.created_at)))
    if run.candidate:
        bundle = run.candidate
        source = "candidate"
    else:
        bundle = banking_fixture().model_dump(mode="json")
        source = "fixture"
    headline = None
    if cycle_payload and cycle_payload[-1]["verdicts"]:
        scores = [item["score"] for item in cycle_payload[-1]["verdicts"]]
        headline = round(sum(scores) / len(scores), 2)
    return {
        "id": run.id,
        "project_id": run.project_id,
        "parent_run_id": run.parent_run_id,
        "status": run.status,
        "config": run.config,
        "inherited_feedback_ids": run.inherited_feedback_ids or [],
        "cycle_count": run.cycle_count,
        "created_at": run.created_at.isoformat(),
        "feedback": [feedback_out(row) for row in notes],
        "cycles": cycle_payload,
        "headline_score": headline,
        "bundle": bundle,
        "bundle_source": source,
        "generation_active": _generation(run) is not None,
        "generation": _generation(run),
    }


def _generation(run: Run) -> dict | None:
    candidate = run.candidate if isinstance(run.candidate, dict) else None
    if not candidate:
        return None
    trajectories = candidate.get("trajectories") or []
    meta = candidate.get("generation")
    generated = isinstance(meta, dict) or any(item.get("generator_id") == GENERATOR_ID for item in trajectories)
    if not generated:
        return None
    if isinstance(meta, dict):
        return meta
    primaries = [item for item in trajectories if not item.get("parent_trajectory_id")]
    return {
        "generator_id": GENERATOR_ID,
        "requested_trajectories": run.config.get("target_trajectory_count"),
        "primary_trajectories": len(primaries),
        "alternative_trajectories": len(trajectories) - len(primaries),
        "event_count": len(candidate.get("events") or []),
        "limited_by": None,
    }
