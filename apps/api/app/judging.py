"""One evaluation cycle of a run, as the `evaluate` job runs it.

The judge reads a stratified sample of the run's journeys, not only the first: one from each kind of
journey and outcome in turn, largest first, chosen deterministically from the run and cycle.
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import runtime
from app.evaluation import evaluate_journeys
from app.retrieval import reference as reference_passages
from app.judge import EvalNotConfigured, InferenceEngineClient, JudgeUnavailable
from app.models import CorpusItem, EvalCycle, EvalVerdict, Run
from app.settings import Settings
from app.store import DbStore, store_for
from sectors.registry import get_sector
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


def judge_models(cfg: Settings) -> list[str]:
    models = [cfg.inference_judge_model]
    if cfg.second_judge_model and cfg.second_judge_model != cfg.inference_judge_model:
        models.append(cfg.second_judge_model)
    return models


def sample_entries(entries: list[dict], size: int, seed: str) -> list[dict]:
    """Take journeys from each (kind, outcome) stratum in turn, largest stratum first, in a seeded order."""
    strata: dict[tuple, list[dict]] = {}
    for entry in entries:
        strata.setdefault((entry["trajectory_type"], entry.get("outcome") or ""), []).append(entry)
    for members in strata.values():
        members.sort(key=lambda entry: hashlib.sha256(f"{seed}|{entry['trajectory_id']}".encode()).hexdigest())
    order = sorted(strata, key=lambda key: (-len(strata[key]), key))
    picked: list[dict] = []
    while len(picked) < size and any(strata[key] for key in order):
        for key in order:
            if strata[key] and len(picked) < size:
                picked.append(strata[key].pop(0))
    return picked


def judge_run(db: Session, run: Run, cfg: Settings, progress=None) -> EvalCycle:
    """Judge a sample of the run's journeys, record the cycle and its verdicts, and return the cycle."""
    if run.cycle_count >= int(run.config["max_cycles"]):
        raise HTTPException(status_code=409, detail="max evaluation cycles reached")
    sector = get_sector(run.config["sector"])
    found = store_for(run) or DbStore(banking_fixture().model_dump(mode="json"))
    whole = []
    if isinstance(found, DbStore):
        # A candidate bundle is checked whole before any journey is sampled from it.
        whole = sector.hard_checks(TrajectoryBundle.model_validate(found.bundle))
    cycle_index = run.cycle_count + 1
    entries = sample_entries(found.entries(), cfg.judge_sample_size, f"{run.id}|{cycle_index}")
    journeys = [TrajectoryBundle.model_validate(found.journey(entry["trajectory_id"])) for entry in entries]
    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == run.project_id)))
    cold = run.config["start_mode"] == "cold"
    passages, chosen = ("", []) if cold else reference_passages(items, sector, run.config["sub_domains"], budget=cfg.judge_reference_chars)
    brief = sector.judge_brief(
        sub_domains=run.config["sub_domains"],
        language=run.config["language"],
        corpus_excerpt=passages,
        cold_start=cold,
        jurisdiction=run.config.get("jurisdiction") or "neutral",
    )
    created: list[InferenceEngineClient] = []

    class _Lazy:
        """Connect to the engine only when a rubric needs it; a run that fails its hard checks never does."""

        def run_eval(self, **kwargs):
            if runtime.judge is not None:
                return runtime.judge.run_eval(**kwargs)
            if not created:
                created.append(judge_client(cfg))
            return created[0].run_eval(**kwargs)

    reference = "weak" if cold else "corpus"
    try:
        if whole:
            result = {
                "hard_check_passed": False, "hard_check_errors": whole, "reference_quality": reference, "accepted": False,
                "revision_notes": whole, "verdicts": [], "called_judge": False, "sample": [], "models": judge_models(cfg),
                "scores": {}, "agreement": {}, "flags": [], "canary": None,
            }
        else:
            result = evaluate_journeys(
                journeys=journeys,
                sector=sector,
                brief=brief,
                reference_quality=reference,
                thresholds=run.config["thresholds"],
                judge=_Lazy(),
                models=judge_models(cfg),
                budget_tokens=cfg.judge_prompt_tokens,
                progress=progress,
            )
    except JudgeUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
    finally:
        for client in created:
            client.close()
    cycle = EvalCycle(
        run_id=run.id,
        cycle_index=cycle_index,
        hard_check_passed=1 if result["hard_check_passed"] else 0,
        hard_check_errors=result["hard_check_errors"],
        reference_quality=result["reference_quality"],
        accepted=1 if result["accepted"] else 0,
        revision_notes=result["revision_notes"],
        judge_tenant=cfg.inference_tenant if result["called_judge"] else "",
        judge_org_id=cfg.inference_org_id if result["called_judge"] else "",
        judge_key_id=cfg.inference_key_id if result["called_judge"] else "",
        sample=result["sample"],
        models=result["models"],
        scores=result["scores"],
        agreement=result["agreement"],
        flags=result["flags"],
        canary=result["canary"],
        reference=chosen,
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
                trajectory_id=verdict["trajectory_id"],
                pair_order=verdict["order"],
                canary=1 if verdict["canary"] else 0,
            )
        )
    run.cycle_count += 1
    run.status = "evaluated"
    db.commit()
    return cycle
