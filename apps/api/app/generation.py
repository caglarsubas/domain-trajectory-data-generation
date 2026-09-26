"""Build a sector candidate when a run is created or repeated.

Small runs are one generator call whose bundle is stored on the run. Larger runs are generated
in batches written as files, with a checkpoint after every batch so a restarted worker resumes
where the last one stopped.
"""

from __future__ import annotations

import json
import math

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus_text import ordered, read_corpus_text, read_document
from app.models import CorpusItem, EvalCycle, Run
from app.store import BATCH_SEQUENCES, SMALL_RUN_SEQUENCES, batch_name, batch_path, journey_entries, run_dir, store_for, write_json
from sectors.overview import OverviewAccumulator, overview_of, variant_id
from sectors.quality import QualityAccumulator
from sectors.registry import get_sector


def prepare(db: Session, config: dict, *, project_id: str, feedback_rows: list, parent: Run | None) -> tuple:
    """The generator arguments shared by every batch, and the corpus items behind them."""
    items = []
    corpus_text = ""
    if config.get("start_mode") == "warm":
        items = ordered(list(db.scalars(select(CorpusItem).where(CorpusItem.project_id == project_id))))
        corpus_text = read_corpus_text(items)
    revisions: list[str] = []
    if parent is not None:
        cycles = list(db.scalars(select(EvalCycle).where(EvalCycle.run_id == parent.id)))
        for cycle in cycles:
            revisions.extend(cycle.revision_notes or [])
    feedback = [
        {"target_type": row.target_type, "target_id": row.target_id, "stance": row.stance, "comment": row.comment}
        for row in feedback_rows
    ]
    parent_bundle = None
    if parent is not None:
        if parent.candidate:
            parent_bundle = parent.candidate
        else:
            # A large parent is never loaded whole: each note's target is resolved to its type name instead.
            feedback = _resolve_notes(parent, feedback)
    seed_payload = {
        "config": {key: config[key] for key in sorted(config) if key != "credential_id"},
        "corpus_text": corpus_text,
        "feedback": feedback,
        "revisions": revisions,
        "corpus": sorted(item.content_hash for item in items),
    }
    sector = get_sector(config["sector"])
    kwargs = {
        "sub_domains": list(config["sub_domains"]),
        "language": config["language"],
        "target_trajectory_count": int(config["target_trajectory_count"]),
        "event_budget": config.get("event_budget"),
        "min_events": int(config["min_events"]),
        "max_events": int(config["max_events"]),
        "max_assistant_turns": int(config["max_assistant_turns"]),
        "start_mode": config["start_mode"],
        "reward_mechanism": config["reward_mechanism"],
        "signal_mechanism": config["signal_mechanism"],
        "consumer": config["consumer"],
        "target_family": config["target_family"],
        "corpus_text": corpus_text,
        "feedback": feedback,
        "revision_notes": revisions,
        "parent_bundle": parent_bundle,
        "seed": json.dumps(seed_payload, sort_keys=True, default=str),
        "group_size": int(config.get("group_size") or 1),
    }
    return sector, kwargs, items


def is_large(config: dict) -> bool:
    return int(config["target_trajectory_count"]) * int(config.get("group_size") or 1) > SMALL_RUN_SEQUENCES


def candidate_for_run(db: Session, config: dict, *, project_id: str, feedback_rows: list, parent: Run | None, progress=None):
    sector, kwargs, items = prepare(db, config, project_id=project_id, feedback_rows=feedback_rows, parent=parent)
    bundle = sector.generate(**kwargs, progress=progress)
    errors = sector.hard_checks(bundle)
    if errors:
        raise HTTPException(status_code=500, detail=f"generated trajectory failed {sector.id} checks")
    assert bundle.generation is not None
    if bundle.generation.steering is not None:
        bundle.generation.steering["documents"] = [_document_report(sector, item) for item in items]
    bundle.generation.overview = overview_of(bundle, sector.classify)
    return bundle


def generate_batched(db: Session, run: Run, *, feedback_rows: list, parent: Run | None, progress) -> dict:
    """Generate a large run batch by batch into files. Returns the run's generation metadata."""
    sector, kwargs, items = prepare(db, run.config, project_id=run.project_id, feedback_rows=feedback_rows, parent=parent)
    size = kwargs["group_size"]
    requested = kwargs["target_trajectory_count"]
    per_batch = max(1, BATCH_SEQUENCES // size)
    total_batches = math.ceil(requested / per_batch)
    root = run_dir(run.id)
    checkpoint = root / "state.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {"done": [], "groups": 0, "budget": kwargs["event_budget"]}
    lifecycle = sector.lifecycle
    quality = QualityAccumulator(lifecycle, sub_domains=_domains(sector, kwargs), allowed=_allowed(sector, kwargs), cold=kwargs["start_mode"] == "cold")
    overview = OverviewAccumulator(sector.classify)
    quality.restore(state.get("quality", {}))
    overview.restore(state.get("overview", {}))
    totals = state.get("totals", {"primary": 0, "alternative": 0, "events": 0, "groups": 0, "signal": 0, "accepted": 0, "passes": 0, "sequences": 0, "flags": {}})
    entries = state.get("journeys", [])
    meta_first = state.get("first")
    limited_by = None

    for index in range(total_batches):
        name = batch_name(index)
        if name in state["done"]:
            continue
        groups = min(per_batch, requested - state["groups"])
        budget = state["budget"]
        if groups <= 0:
            break
        if budget is not None and budget <= 0:
            limited_by = "event_budget"
            break
        done_before = state["groups"]
        bundle = sector.generate(
            **{**kwargs, "target_trajectory_count": groups, "event_budget": budget, "seed": f"{kwargs['seed']}|{name}"},
            materialization_cap=groups * size,
            id_prefix=f"{name}.",
            progress=lambda done, _total, message="": progress(done_before + done, requested, f"Batch {index + 1} of {total_batches}: drew {done_before + done} of {requested}."),
        )
        errors = sector.hard_checks(bundle)
        if errors:
            raise HTTPException(status_code=500, detail=f"generated batch {name} failed {sector.id} checks")
        quality.add(bundle)
        overview.add(bundle)
        meta = bundle.generation
        dumped = bundle.model_dump(mode="json")
        write_json(batch_path(root, name), dumped)
        entries.extend(journey_entries(dumped, name, variant_id))
        totals["primary"] += meta.primary_trajectories
        totals["alternative"] += meta.alternative_trajectories
        totals["events"] += meta.event_count
        rewards = meta.rewards or {}
        totals["groups"] += rewards.get("groups", 0)
        totals["signal"] += rewards.get("groups_with_signal", 0)
        totals["accepted"] += rewards.get("accepted_groups", 0)
        totals["sequences"] += sum(len(sample.sequences) for sample in bundle.samples)
        totals["passes"] += sum(1 for sample in bundle.samples for sequence in sample.sequences if sequence.outcome == "pass")
        for flag, count in (rewards.get("flags") or {}).items():
            totals["flags"][flag] = totals["flags"].get(flag, 0) + count
        if meta_first is None:
            meta_first = {"generator_id": meta.generator_id, "pack_version": meta.pack_version, "steering": meta.steering, "mechanism": rewards.get("mechanism")}
        state["done"].append(name)
        state["groups"] += meta.primary_trajectories
        if budget is not None:
            state["budget"] = budget - meta.event_count
        state.update({"quality": quality.state(), "overview": overview.state(), "totals": totals, "journeys": entries, "first": meta_first})
        write_json(checkpoint, state)
        if meta.limited_by == "event_budget":
            limited_by = "event_budget"
            break

    steering = (meta_first or {}).get("steering")
    if steering is not None:
        steering = {**steering, "documents": [_document_report(sector, item) for item in items]}
    generation = {
        "generator_id": (meta_first or {}).get("generator_id"),
        "pack_version": (meta_first or {}).get("pack_version"),
        "group_size": size,
        "requested_trajectories": requested,
        "primary_trajectories": totals["primary"],
        "alternative_trajectories": totals["alternative"],
        "event_count": totals["events"],
        "limited_by": limited_by,
        "steering": steering,
        "rewards": {
            "mechanism": (meta_first or {}).get("mechanism"),
            "groups": totals["groups"],
            "groups_with_signal": totals["signal"],
            "accepted_groups": totals["accepted"],
            "pass_rate": round(totals["passes"] / totals["sequences"], 4) if totals["sequences"] else None,
            "penalty_mode": "record",
            "flags": totals["flags"],
            "note": None if totals["signal"] else "Groups of one carry no group-relative signal; set a group size above 1.",
        },
        "quality": quality.report(),
        "overview": overview.report(),
        "storage": {"kind": "files", "batches": len(state["done"]), "batch_sequences": BATCH_SEQUENCES},
    }
    write_json(root / "index.json", {"batches": state["done"], "journeys": entries})
    return generation


def _domains(sector, kwargs) -> list[str]:
    return [name for name in kwargs["sub_domains"] if name in sector.lifecycle.sub_domains] or [sector.lifecycle.sub_domains[0]]


def _allowed(sector, kwargs) -> tuple[str, ...]:
    from sectors.lifecycle import allowed_events

    return allowed_events(sector.lifecycle, _domains(sector, kwargs), set())


def _resolve_notes(parent: Run, feedback: list[dict]) -> list[dict]:
    store = store_for(parent)
    if store is None:
        return feedback
    resolved = []
    for note in feedback:
        target = note["target_id"]
        if note["target_type"] == "event":
            target = store.event_type(target) or target
        elif note["target_type"] == "trajectory":
            target = store.trajectory_type(target) or target
        resolved.append({**note, "target_id": target})
    return resolved


def _document_report(sector, item) -> dict:
    doc = read_document(item)
    found = sector.steering(doc.text) if doc.readable else None
    return {
        "id": doc.item_id,
        "kind": doc.kind,
        "name": doc.name,
        "readable": doc.readable,
        "reason": doc.reason,
        "characters": len(doc.text),
        "terms": list(found.terms) if found else [],
        "named_events": list(found.events) if found else [],
        "negated_events": list(found.negated) if found else [],
    }
