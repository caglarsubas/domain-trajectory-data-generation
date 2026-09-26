"""One evaluation cycle of a run, as the `evaluate` job runs it."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import runtime
from app.evaluation import evaluate_bundle
from app.judge import EvalNotConfigured, InferenceEngineClient, JudgeUnavailable
from app.models import CorpusItem, EvalCycle, EvalVerdict, Run
from app.settings import Settings
from app.store import store_for
from trajectory_contract import TrajectoryBundle, banking_fixture


def judge_client(cfg: Settings) -> InferenceEngineClient:
    try:
        return InferenceEngineClient(
            base_url=cfg.inference_base_url,
            api_key=cfg.inference_api_key,
            tenant=cfg.inference_tenant,
            org_id=cfg.inference_org_id,
            key_id=cfg.inference_key_id,
            judge_model=cfg.inference_judge_model,
        )
    except EvalNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _bundle(run: Run) -> TrajectoryBundle:
    if run.candidate:
        return TrajectoryBundle.model_validate(run.candidate)
    found = store_for(run)
    if found is not None:
        # A large run is judged on its first journey and that journey's alternative, as a small run is.
        entries = found.entries()
        if entries:
            return TrajectoryBundle.model_validate(found.journey(entries[0]["trajectory_id"]))
    return banking_fixture()


def judge_run(db: Session, run: Run, cfg: Settings, progress=None) -> EvalCycle:
    """Judge the run's candidate, record the cycle and its verdicts, and return the cycle."""
    if run.cycle_count >= int(run.config["max_cycles"]):
        raise HTTPException(status_code=409, detail="max evaluation cycles reached")
    bundle = _bundle(run)
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == run.project_id)))
    created: list[InferenceEngineClient] = []

    class _Lazy:
        """Connect to the engine only when a rubric needs it; a run that fails its hard checks never does."""

        def run_eval(self, **kwargs):
            if runtime.judge is not None:
                return runtime.judge.run_eval(**kwargs)
            if not created:
                created.append(judge_client(cfg))
            return created[0].run_eval(**kwargs)

    try:
        result = evaluate_bundle(
            bundle=bundle,
            sector_id=run.config["sector"],
            sub_domains=run.config["sub_domains"],
            language=run.config["language"],
            cold_start=run.config["start_mode"] == "cold",
            corpus_items=items,
            thresholds=run.config["thresholds"],
            judge=_Lazy(),
            progress=progress,
        )
    except JudgeUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
    finally:
        for client in created:
            client.close()
    cycle = EvalCycle(
        run_id=run.id,
        cycle_index=run.cycle_count + 1,
        hard_check_passed=1 if result["hard_check_passed"] else 0,
        hard_check_errors=result["hard_check_errors"],
        reference_quality=result["reference_quality"],
        accepted=1 if result["accepted"] else 0,
        revision_notes=result["revision_notes"],
        judge_tenant=cfg.inference_tenant if result["called_judge"] else "",
        judge_org_id=cfg.inference_org_id if result["called_judge"] else "",
        judge_key_id=cfg.inference_key_id if result["called_judge"] else "",
    )
    db.add(cycle)
    db.flush()
    for verdict in result["verdicts"]:
        db.add(
            EvalVerdict(
                cycle_id=cycle.id,
                rubric=verdict["rubric"],
                score=verdict["score"],
                parsed=verdict["parsed"],
                raw=verdict["raw"],
                judge_model=verdict["judge_model"],
                duration_ms=verdict["duration_ms"],
            )
        )
    run.cycle_count += 1
    run.status = "evaluated"
    db.commit()
    return cycle
