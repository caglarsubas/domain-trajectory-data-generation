from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus_text import ordered, read_document
from app.evaluation import UNREADABLE, normalized
from app.jobs import latest_for
from app.models import CorpusItem, EvalCycle, EvalVerdict, Feedback, Project, Run
from trajectory_contract import banking_fixture


def corpus_out(item: CorpusItem) -> dict:
    doc = read_document(item)
    return {
        "readable": doc.readable,
        "unreadable_reason": doc.reason,
        "characters": len(doc.text),
        "calibration": _calibration_out(item.calibration) if item.kind == "data_source" else None,
        "parser": (item.ingest or {}).get("source") or doc.parser,
        "parse_detail": (item.ingest or {}).get("detail") or doc.detail,
        "ingest": item.ingest,
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "uri": item.uri,
        "content_hash": item.content_hash,
        "provenance": item.provenance,
        "created_at": item.created_at.isoformat(),
    }


def _calibration_out(calibration: dict | None) -> dict | None:
    """A data source's calibration without its raw counts: status, size, mapping, and what it covers."""
    if not calibration:
        return None
    from sectors.calibration import Calibration

    summary = Calibration.from_dict(calibration.get("calibration")).summary() if calibration.get("calibration") else None
    keep = ("status", "reason", "format", "cases", "events", "activities", "mapping", "mapped_share", "channels", "outcomes", "preview")
    return {**{key: calibration.get(key) for key in keep if key in calibration}, "summary": summary}


def project_out(project: Project, db: Session) -> dict:
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project.id)))
    return {
        "id": project.id,
        "name": project.name,
        "sector": project.sector,
        "created_at": project.created_at.isoformat(),
        "corpus": [corpus_out(item) for item in ordered(items)],
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


def job_out(job) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "result": job.result,
        "run_id": job.run_id,
        "project_id": job.project_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def run_summary(run: Run, db: Session) -> dict:
    """A run without its bundle, for listing. Reads only the stored generation metadata."""
    from app.jobs import latest_for

    cycle = db.scalars(select(EvalCycle).where(EvalCycle.run_id == run.id).order_by(EvalCycle.cycle_index.desc())).first()
    headline = None
    if cycle is not None:
        verdicts = list(db.scalars(select(EvalVerdict).where(EvalVerdict.cycle_id == cycle.id)))
        headline = headline_score(cycle, verdicts)
    return {
        "id": run.id,
        "project_id": run.project_id,
        "parent_run_id": run.parent_run_id,
        "status": run.status,
        "config": run.config,
        "cycle_count": run.cycle_count,
        "created_at": run.created_at.isoformat(),
        "headline_score": headline,
        "generation": run.generation,
        "job": job_out(latest_for(db, run.id)),
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
                "sample": cycle.sample or [],
                "models": cycle.models or [],
                "scores": cycle.scores or {},
                "agreement": cycle.agreement or {},
                "flags": cycle.flags or [],
                "canary": cycle.canary,
                "reference": cycle.reference or [],
                "judging": cycle.judging,
                "headline_score": headline_score(cycle, verdicts),
                "verdicts": [
                    {
                        "rubric": row.rubric,
                        "score": None if (row.parsed or {}).get(UNREADABLE) else row.score,
                        "readable": not (row.parsed or {}).get(UNREADABLE),
                        "parsed": row.parsed,
                        "raw": row.raw,
                        "judge_model": row.judge_model,
                        "duration_ms": row.duration_ms,
                        "trajectory_id": row.trajectory_id,
                        "order": row.pair_order,
                        "canary": bool(row.canary),
                        "repeat": row.repeat_index or 0,
                        "rubric_digest": row.rubric_digest,
                        "control": row.control,
                        "segment_id": row.segment_id,
                    }
                    for row in verdicts
                ],
            }
        )
    notes = list(db.scalars(select(Feedback).where(Feedback.run_id == run.id).order_by(Feedback.created_at)))
    if run.candidate:
        bundle = run.candidate
        source = "candidate"
        if run.generation is None and isinstance(run.candidate.get("generation"), dict):
            run.generation = run.candidate["generation"]
            db.commit()
        overview = (run.generation or {}).get("overview")
        # An overview stored before the process map had a time axis is rebuilt once from the bundle.
        untimed = bool(overview and overview.get("nodes")) and "hours" not in overview["nodes"][0]
        if run.generation is not None and (not overview or untimed):
            from sectors.overview import overview_of
            from sectors.registry import get_sector
            from trajectory_contract import TrajectoryBundle

            sector = get_sector((run.config or {}).get("sector", "banking"))
            run.generation = {**run.generation, "overview": overview_of(TrajectoryBundle.model_validate(run.candidate), sector.classify)}
            db.commit()
    elif run.status in {"queued", "generating", "failed", "cancelled"}:
        bundle = None
        source = "pending"
    elif (run.generation or {}).get("storage"):
        # A large run is read a journey at a time through /runs/{id}/journeys.
        bundle = None
        source = "paged"
    else:
        bundle = banking_fixture().model_dump(mode="json")
        source = "fixture"
    headline = cycle_payload[-1]["headline_score"] if cycle_payload else None
    judge_job = job_out(latest_for(db, run.id, "evaluate"))
    from app.judging import resume_state

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
        "job": job_out(latest_for(db, run.id)),
        "judge_job": judge_job,
        # A judge cycle that stopped keeps its answers, and asking again resumes it (decision 27).
        "judge_resume": resume_state(run) if judge_job and judge_job["status"] in ("failed", "cancelled") else None,
        **_study_state(run, db, cycles[-1] if cycles else None),
    }


def judged(cycle: EvalCycle | None) -> bool:
    """A cycle that reached a decision: the hard checks failed, or the primary judge read every rubric that decides it."""
    from app.evaluation import GATING

    if cycle is None:
        return False
    if not cycle.hard_check_passed:
        return True
    if cycle.models:
        # Pairwise, the code comparisons, and study rubrics are reported; an unreadable one does not reopen the run.
        return not any(
            flag.get("kind") == "unreadable" and flag.get("model") == cycle.models[0] and flag.get("rubric") in GATING
            for flag in cycle.flags or []
        )
    return True


def _study_state(run: Run, db: Session, cycle: EvalCycle | None) -> dict:
    """Whether the judge has read the run, the study's rubrics, and whether approved ones wait for a cycle."""
    from app import study_rubrics
    from app.jobs import latest_for
    from sectors.registry import get_sector

    label = get_sector(run.config.get("sector", "banking")).label
    rows = study_rubrics.for_project(db, run.project_id)
    return {
        "judged": judged(cycle),
        "study_rubrics": [study_rubrics.rubric_out(row, label) for row in rows],
        "study_rubrics_pending": judged(cycle) and study_rubrics.pending(db, run, cycle, label),
        "proposal_job": job_out(latest_for(db, run.id, "propose_rubrics")),
    }


def _generation(run: Run) -> dict | None:
    candidate = run.candidate if isinstance(run.candidate, dict) else None
    if not candidate:
        # A large run stores its summary on the run. Judging sets the status to evaluated and leaves that summary in place.
        return run.generation if run.status in {"generated", "evaluated"} else None
    trajectories = candidate.get("trajectories") or []
    meta = candidate.get("generation")
    generated = isinstance(meta, dict) or any(item.get("generator_id") for item in trajectories)
    if not generated:
        return None
    if isinstance(meta, dict):
        return meta
    primaries = [item for item in trajectories if not item.get("parent_trajectory_id")]
    return {
        # Candidates stored before generation metadata existed came from the first banking generator.
        "generator_id": "banking-semi-markov-v1",
        "requested_trajectories": run.config.get("target_trajectory_count"),
        "primary_trajectories": len(primaries),
        "alternative_trajectories": len(trajectories) - len(primaries),
        "event_count": len(candidate.get("events") or []),
        "limited_by": None,
    }


def headline_score(cycle: EvalCycle, verdicts: list) -> float | None:
    """The primary judge's rubric scores, each normalized to 0-1, averaged. Older cycles average their verdicts."""
    if cycle.scores and cycle.models:
        primary = cycle.models[0]
        values = [normalized(rubric, by_model.get(primary)) for rubric, by_model in cycle.scores.items()]
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 2) if values else None
    scores = [row.score for row in verdicts if not (row.parsed or {}).get(UNREADABLE)]
    return round(sum(scores) / len(scores), 2) if scores else None
