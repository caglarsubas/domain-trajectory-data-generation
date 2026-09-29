"""One evaluation cycle of a run, as the `evaluate` job runs it.

The judge reads a stratified sample of the run's journeys, not only the first: one from each kind of
journey and outcome in turn, largest first, chosen deterministically from the run and cycle. When the run has
provider-written turns, it also reads a sample of them for faithfulness, and a turn every readable repeat calls
unfaithful goes back to its template in the stored run (decision 14).
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import faithfulness, runtime, study_rubrics
from app.controls import build as build_controls
from app.evaluation import code_signals, evaluate_journeys
from app.realism import prepare as prepare_realism
from app.retrieval import reference as reference_passages
from app.judge import EvalNotConfigured, InferenceEngineClient, JudgeUnavailable, RubricLimitReached, RubricsUnsupported
from app.judge_rubrics import CODE_RUBRICS, STUDY_KINDS, TURN_FAITHFULNESS
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


def study_brief(db: Session, run: Run, cfg: Settings, sector) -> tuple[str, list[dict]]:
    """The judge's brief for a run's study, with the warm-start passages chosen for it, and those passages."""
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
    return brief, chosen


class LazyJudge:
    """Connect to the engine only when a call needs it; a run that fails its hard checks never does."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.created: list[InferenceEngineClient] = []

    def _target(self):
        if runtime.judge is not None:
            return runtime.judge
        if not self.created:
            self.created.append(judge_client(self.cfg))
        return self.created[0]

    def run_eval(self, **kwargs):
        return self._target().run_eval(**kwargs)

    def _method(self, name: str):
        method = getattr(self._target(), name, None)
        if method is None:
            raise RubricsUnsupported("This judge does not accept rubrics.")
        return method

    def register_rubric(self, definition):
        return self._method("register_rubric")(definition)

    def list_rubrics(self):
        return self._method("list_rubrics")()

    def delete_rubric(self, name):
        return self._method("delete_rubric")(name)

    def close(self) -> None:
        for client in self.created:
            client.close()


def register(judge, definition: dict, db: Session) -> str:
    """Register a rubric and return the engine's digest. When the platform tenant is full, remove the study rubrics
    no study has approved any more and try once more."""
    from app.study_rubrics import PREFIX, active_names

    try:
        return judge.register_rubric(definition)["digest"]
    except RubricLimitReached:
        keep = active_names(db) | {definition["name"]}
        stale = [item["name"] for item in judge.list_rubrics() if item.get("source") == "tenant" and item["name"].startswith(PREFIX) and item["name"] not in keep]
        if not stale:
            raise
        for name in stale:
            judge.delete_rubric(name)
        return judge.register_rubric(definition)["digest"]


def real_cases(db: Session, run: Run) -> dict | None:
    """Real cases from the study's own data sources, for the judge to set against generated journeys (decision 22).

    Each source kept a sample at calibration, as event types and hours since each case began. Generated journeys are
    shown cut to the events those sources record, a step no sampled case ever times is shown untimed on both sides, and
    both are drawn at the coarsest resolution the sources record times at (`app.realism`).
    """
    from sectors.calibration import Calibration

    from app.realism import source_resolution

    items = list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == run.project_id, CorpusItem.kind == "data_source")))
    cases, visible, sources, by_source = [], set(), [], []
    for item in items:
        found = item.calibration or {}
        if found.get("status") != "ready" or not found.get("real_cases") or not found.get("calibration"):
            continue
        cases.extend(found["real_cases"])
        by_source.append(found["real_cases"])
        visible |= Calibration.from_dict(found["calibration"]).observed_events
        sources.append(item.name)
    if not cases:
        return None
    timed = {event for case in cases for event, hours in case if hours is not None}
    untimed = sorted({event for case in cases for event, _ in case} - timed)
    return {"cases": cases, "visible": sorted(visible), "untimed": untimed, "sources": sources, "resolution": source_resolution(by_source)}


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
    cold = run.config["start_mode"] == "cold"
    brief, chosen = study_brief(db, run, cfg, sector)
    reference = "weak" if cold else "corpus"
    repeats = cfg.judge_repeats
    judging = {
        "repeats": repeats,
        "temperature": cfg.judge_temperature if repeats > 1 else 0.0,
        "rubrics": {},
        "notes": [],
    }
    lazy = LazyJudge(cfg)
    turns = [] if whole else faithfulness.sample_turns(found, cfg.judge_text_sample, f"{run.id}|{cycle_index}|text")
    faithful: dict | None = None
    try:
        compared: dict[str, str] = {}
        study: dict[str, dict] = {}
        if not whole:
            # Register the code-comparison rubrics this sample can be compared on, and the study's approved rubrics; an
            # engine without tenant rubrics still judges the rest.
            wanted = {name for journey, entry in zip(journeys, entries) for name in code_signals(journey, entry["trajectory_id"])}
            approved = study_rubrics.for_project(db, run.project_id, statuses=("approved",))
            try:
                for name in sorted(wanted & set(CODE_RUBRICS)):
                    compared[name] = register(lazy, CODE_RUBRICS[name], db)
                if turns:
                    faithful = {"turns": turns, "language": run.config["language"], "digest": register(lazy, TURN_FAITHFULNESS, db)}
                for row in approved:
                    definition = study_rubrics.definition(row, sector.label)
                    study[definition["name"]] = {
                        "kind": row.kind,
                        "title": row.title,
                        "signal": STUDY_KINDS[row.kind]["signal"],
                        "rubric_id": row.id,
                        "digest": register(lazy, definition, db),
                    }
            except RubricsUnsupported as exc:
                skipped = sorted(wanted & set(CODE_RUBRICS)) + (["the written turns' faithfulness"] if turns else []) + [f"the study's {row.kind} rubric" for row in approved]
                judging["notes"].append(f"{exc.message} The judge did not score {', '.join(skipped)}.")
                compared, study, faithful = {}, {}, None
            judging["rubrics"] = {name: {"source": "tenant", "digest": digest} for name, digest in compared.items()}
            if faithful:
                judging["rubrics"][TURN_FAITHFULNESS["name"]] = {"source": "tenant", "digest": faithful["digest"]}
            judging["rubrics"].update(
                {name: {"source": "study", "digest": info["digest"], "kind": info["kind"], "title": info["title"], "rubric_id": info["rubric_id"]} for name, info in study.items()}
            )
            judging["study"] = sorted(study)
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
                judge=lazy,
                models=judge_models(cfg),
                budget_tokens=cfg.judge_prompt_tokens,
                progress=progress,
                repeats=repeats,
                temperature=judging["temperature"],
                compared=compared,
                study=study,
                controls=build_controls(journeys, sector, run.config),
                faithfulness=faithful,
                realism=prepare_realism(real_cases(db, run), found, sector, run.config, cfg.realism_sample, f"{run.id}|{cycle_index}"),
            )
    except JudgeUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
    finally:
        lazy.close()
    found_turns = (result.get("agreement") or {}).get("faithfulness")
    if found_turns:
        rows = [row for row in found_turns["rows"] if row["revert"]]
        reverted = faithfulness.revert(run, found, rows)
        judging["faithfulness"] = {"turns": found_turns["turns"], "reverted": reverted}
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
        judging=judging if result["called_judge"] else None,
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
                repeat_index=verdict.get("repeat", 0),
                rubric_digest=verdict.get("rubric_digest"),
                control=verdict.get("control"),
                segment_id=verdict.get("segment_id"),
            )
        )
    run.cycle_count += 1
    run.status = "evaluated"
    db.commit()
    return cycle
