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
from dataclasses import replace

from app.calibrate import study_calibration
from app.facts import facts_report, steering_for
from app.models import CorpusItem, EvalCycle, Project, Run
from app.store import BATCH_SEQUENCES, MAX_RUN_SEQUENCES, SMALL_RUN_SEQUENCES, batch_name, batch_path, journey_entries, run_dir, store_for, write_json
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
    corpus_steering = None
    if config.get("start_mode") == "warm":
        # Warm runs steer from the study's facts: explicit ones, and implied ones a person accepted.
        project = db.get(Project, project_id)
        corpus_steering = steering_for(items, get_sector(config["sector"]), project.fact_reviews if project else None)
    calibration, channels = (None, {})
    if config.get("start_mode") == "warm" and config.get("calibrate", True):
        # Data sources reweight next steps and durations; their channel mix fills in when no fact names a channel.
        calibration, channels = study_calibration(items)
        if corpus_steering is not None and corpus_steering.channel is None and channels:
            corpus_steering = replace(corpus_steering, channel=max(channels, key=lambda name: (channels[name], name)))
    seed_payload = {
        # Keys that change no journey stay out of the seed, so turning them on draws the same journeys.
        "config": {key: config[key] for key in sorted(config) if key not in {"credential_id", "episodes"}},
        "facts": corpus_steering.report() if corpus_steering is not None else None,
        "corpus_text": corpus_text,
        "feedback": feedback,
        "revisions": revisions,
        "corpus": sorted(item.content_hash for item in items),
    }
    if calibration is not None:
        # Only a calibrated run's seed changes, so runs without data sources draw as before.
        seed_payload["calibration"] = calibration.summary()
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
        "jurisdiction": config.get("jurisdiction") or "neutral",
        "corpus_steering": corpus_steering,
        "calibration": calibration,
        # Episodes by default for post-training, where agent rollouts are the training data.
        "episodes": config.get("episodes") if config.get("episodes") is not None else config.get("consumer") == "post_training",
        "operations": _operations(items),
    }
    return sector, kwargs, items


def is_large(config: dict) -> bool:
    """Batched when the run is bigger than a small run, or when targets or shares need the batch loop."""
    total = int(config["target_trajectory_count"]) * int(config.get("group_size") or 1)
    return total > SMALL_RUN_SEQUENCES or config.get("target_kind") == "accepted_groups" or bool(config.get("domain_shares"))


def candidate_for_run(db: Session, config: dict, *, project_id: str, feedback_rows: list, parent: Run | None, progress=None):
    sector, kwargs, items = prepare(db, config, project_id=project_id, feedback_rows=feedback_rows, parent=parent)
    bundle = sector.generate(**kwargs, progress=progress)
    errors = sector.hard_checks(bundle)
    if errors:
        raise HTTPException(status_code=500, detail=f"generated trajectory failed {sector.id} checks")
    assert bundle.generation is not None
    if bundle.generation.steering is not None:
        bundle.generation.steering["documents"] = [_document_report(sector, item) for item in items]
        bundle.generation.steering["facts"] = _facts_counts(db, config, project_id, items, sector)
    bundle.generation.overview = overview_of(bundle, sector.classify)
    return bundle


# Accepted-group targets: the first batch assumes this acceptance rate, later batches use the observed one,
# plus a margin, and a bucket stops at five times its target if acceptance stays too low.
PRIOR_ACCEPTANCE = 0.6
OVERSAMPLE = 0.1
ACCEPTANCE_CEILING = 5


def buckets_for(config: dict, requested: int) -> list[dict]:
    """One bucket per share, with targets that add up to the request (largest remainder), or one bucket for all."""
    shares = config.get("domain_shares") or {}
    if not shares:
        return [{"domains": list(config["sub_domains"]), "target": requested}]
    names = [name for name in config["sub_domains"] if name in shares]
    total = sum(shares[name] for name in names)
    exact = {name: requested * shares[name] / total for name in names}
    targets = {name: int(exact[name]) for name in names}
    for name in sorted(names, key=lambda item: exact[item] - targets[item], reverse=True)[: requested - sum(targets.values())]:
        targets[name] += 1
    return [{"domains": [name], "target": targets[name]} for name in names if targets[name] > 0]


def generate_batched(db: Session, run: Run, *, feedback_rows: list, parent: Run | None, progress) -> dict:
    """Generate a large run batch by batch into files. Returns the run's generation metadata."""
    sector, kwargs, items = prepare(db, run.config, project_id=run.project_id, feedback_rows=feedback_rows, parent=parent)
    size = kwargs["group_size"]
    requested = kwargs["target_trajectory_count"]
    accepted_mode = run.config.get("target_kind") == "accepted_groups"
    per_batch = max(1, BATCH_SEQUENCES // size)
    buckets = buckets_for(run.config, requested)
    root = run_dir(run.id)
    checkpoint = root / "state.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {"done": [], "budget": kwargs["event_budget"]}
    progress_state = state.setdefault("buckets", [{"groups": 0, "accepted": 0, "finished": False, "reason": None} for _ in buckets])
    lifecycle = sector.lifecycle
    quality = QualityAccumulator(lifecycle, sub_domains=_domains(sector, kwargs), allowed=_allowed(sector, kwargs), cold=kwargs["start_mode"] == "cold")
    overview = OverviewAccumulator(sector.classify)
    quality.restore(state.get("quality", {}))
    overview.restore(state.get("overview", {}))
    totals = state.get("totals", {"primary": 0, "alternative": 0, "events": 0, "groups": 0, "signal": 0, "accepted": 0, "passes": 0, "sequences": 0, "flags": {}})
    entries = state.get("journeys", [])
    meta_first = state.get("first")
    limited_by = state.get("limited_by")
    goal = sum(bucket["target"] for bucket in buckets)

    def reached() -> int:
        return sum(item["accepted"] if accepted_mode else item["groups"] for item in progress_state)

    for number, (bucket, standing) in enumerate(zip(buckets, progress_state)):
        ceiling = bucket["target"]
        if accepted_mode:
            # Oversampling never draws more than the run limit allows, shared across parts by their targets.
            room = max(bucket["target"], (MAX_RUN_SEQUENCES // size) * bucket["target"] // max(goal, 1))
            ceiling = min(bucket["target"] * ACCEPTANCE_CEILING, room)
        while not standing["finished"]:
            count = standing["accepted"] if accepted_mode else standing["groups"]
            if count >= bucket["target"]:
                standing["finished"] = True
                break
            if standing["groups"] >= ceiling:
                standing.update({"finished": True, "reason": "acceptance"})
                limited_by = "acceptance"
                break
            budget = state["budget"]
            if budget is not None and budget <= 0:
                standing.update({"finished": True, "reason": "event_budget"})
                limited_by = "event_budget"
                break
            if accepted_mode:
                rate = standing["accepted"] / standing["groups"] if standing["groups"] else PRIOR_ACCEPTANCE
                wanted = math.ceil((bucket["target"] - standing["accepted"]) / max(rate, 0.05) * (1 + OVERSAMPLE))
                groups = max(1, min(per_batch, wanted, ceiling - standing["groups"]))
            else:
                groups = min(per_batch, bucket["target"] - standing["groups"])
            name = batch_name(len(state["done"]))
            before = reached()
            bundle = sector.generate(
                **{
                    **kwargs,
                    "sub_domains": bucket["domains"],
                    "target_trajectory_count": groups,
                    "event_budget": budget,
                    "seed": f"{kwargs['seed']}|bucket{number}|{name}",
                },
                materialization_cap=groups * size,
                id_prefix=f"{name}.",
                progress=lambda done, _total, message="": progress(
                    min(before + (done if not accepted_mode else 0), goal), goal, f"Batch {len(state['done']) + 1}: {before:,} of {goal:,} {'accepted groups' if accepted_mode else 'drawn'}."
                ),
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
            rewards = meta.rewards or {}
            totals["primary"] += meta.primary_trajectories
            totals["alternative"] += meta.alternative_trajectories
            totals["events"] += meta.event_count
            totals["groups"] += rewards.get("groups", 0)
            totals["signal"] += rewards.get("groups_with_signal", 0)
            totals["accepted"] += rewards.get("accepted_groups", 0)
            totals["sequences"] += sum(len(sample.sequences) for sample in bundle.samples)
            totals["passes"] += sum(1 for sample in bundle.samples for sequence in sample.sequences if sequence.outcome == "pass")
            for flag, flagged in (rewards.get("flags") or {}).items():
                totals["flags"][flag] = totals["flags"].get(flag, 0) + flagged
            if meta.episodes:
                summed = totals.setdefault("episodes", {"episodes": 0, "rollouts": 0, "accepted_groups": 0, "with_api_operations": 0, "policies": {}})
                for key in ("episodes", "rollouts", "accepted_groups", "with_api_operations"):
                    summed[key] += meta.episodes.get(key, 0)
                for policy, count in (meta.episodes.get("policies") or {}).items():
                    summed["policies"][policy] = summed["policies"].get(policy, 0) + count
            if meta_first is None:
                meta_first = {
                    "generator_id": meta.generator_id,
                    "pack_version": meta.pack_version,
                    "steering": meta.steering,
                    "mechanism": rewards.get("mechanism"),
                    "notes": meta.notes,
                    "calibration": meta.calibration,
                }
            standing["groups"] += meta.primary_trajectories
            standing["accepted"] += rewards.get("accepted_groups", 0)
            state["done"].append(name)
            if budget is not None:
                state["budget"] = budget - meta.event_count
            if meta.limited_by == "event_budget":
                standing.update({"finished": True, "reason": "event_budget"})
                limited_by = "event_budget"
            state.update({"quality": quality.state(), "overview": overview.state(), "totals": totals, "journeys": entries, "first": meta_first, "limited_by": limited_by})
            write_json(checkpoint, state)
            progress(min(reached(), goal), goal, f"Batch {len(state['done'])} done: {reached():,} of {goal:,} {'accepted groups' if accepted_mode else 'drawn'}.")

    target = {
        "kind": "accepted_groups" if accepted_mode else "prompts",
        "requested": requested,
        "reached": reached(),
        "buckets": [
            {"sub_domains": bucket["domains"], "target": bucket["target"], "generated": standing["groups"], "accepted": standing["accepted"], "stopped_by": standing["reason"]}
            for bucket, standing in zip(buckets, progress_state)
        ],
    }
    steering = (meta_first or {}).get("steering")
    if steering is not None:
        steering = {
            **steering,
            "documents": [_document_report(sector, item) for item in items],
            "facts": _facts_counts(db, run.config, run.project_id, items, sector),
        }
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
        "notes": (meta_first or {}).get("notes"),
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
        "quality": _with_representative(quality.report(), kwargs.get("calibration"), overview.report()),
        "overview": overview.report(),
        "calibration": (meta_first or {}).get("calibration"),
        "episodes": totals.get("episodes"),
        "storage": {"kind": "files", "batches": len(state["done"]), "batch_sequences": BATCH_SEQUENCES},
        "target": target,
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


def _operations(items: list) -> list[dict]:
    from app.documents import operations_from_text

    found = []
    for item in ordered(items):
        found.extend(operations_from_text(read_document(item).body))
    return found


def _with_representative(report: dict, calibration, summary: dict) -> dict:
    """A large run's representativeness, measured over the steps of every batch through the overview's edges."""
    if calibration is None:
        return report
    from collections import Counter

    from sectors.calibration import representativeness

    steps = Counter({(edge["from"], edge["to"]): edge["count"] for edge in summary.get("edges") or []})
    return {**report, "representative": representativeness(calibration, steps)}


def _facts_counts(db: Session, config: dict, project_id: str, items: list, sector) -> dict:
    project = db.get(Project, project_id)
    report = facts_report(
        items,
        sector,
        project.fact_reviews if project else None,
        sub_domains=list(config["sub_domains"]),
        jurisdiction=config.get("jurisdiction") or "neutral",
        language=config["language"],
    )
    return {**report["counts"], "unsupported": [item["statement"] for item in report["unsupported"]]}


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
