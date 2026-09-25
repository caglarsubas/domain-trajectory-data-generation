"""Export a run: samples.jsonl, domain.jsonl, ocel.json, and manifest.json.

Every part is built from the stored candidate, so a re-export of the same run with the same
held-out sub-domain reproduces the same bytes and the same split.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from trajectory_contract import TrajectoryBundle

PARTS = ("samples.jsonl", "domain.jsonl", "ocel.json", "manifest.json")
SPLIT_RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
FORMAT_VERSION = 1

INTENDED_USE = {
    "post_training": "Supervised and reinforcement post-training of language models on synthetic journeys.",
    "decision_scoring": "Scoring decisions at branch points of synthetic journeys.",
    "evaluation": "Evaluating models against synthetic journeys with verifiable outcomes.",
}
LIMITATIONS = (
    "Transition probabilities and dwell times are hand-set priors until data sources calibrate them (Slice 5).",
    "Solution and behavior scores are deterministic measures until the judge supplies rubric scores (Slice 4).",
    "Turn text is built from templates in English or Turkish; it narrates events rather than acting with tools (Slice 6).",
    "Penalty rules run in record-only mode: flags are recorded but change no reward, mask, or advantage.",
    "Alternative branches and group rollouts are simulated alternatives, not causal counterfactuals.",
    "The studio stores at most 64 journeys per run until background jobs arrive (Slice 3).",
)


def split_of(run_id: str, sample_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{sample_id}".encode()).digest()
    position = int.from_bytes(digest[:8], "big") / 2**64
    if position < SPLIT_RATIOS["train"]:
        return "train"
    if position < SPLIT_RATIOS["train"] + SPLIT_RATIOS["validation"]:
        return "validation"
    return "test"


def splits(run_id: str, bundle: TrajectoryBundle, lifecycle, held_out: str | None) -> dict[str, str]:
    """Split per sample id. A sample is held out when any of its sequences reaches a milestone of the held-out sub-domain."""
    milestones = set(lifecycle.milestones.get(held_out, ())) if held_out else set()
    events = {event.event_id: event.event_type for event in bundle.events}
    trajectories = {item.trajectory_id: item for item in bundle.trajectories}
    result = {}
    for sample in bundle.samples:
        reached = set()
        for sequence in sample.sequences:
            trajectory = trajectories.get(sequence.trajectory_id or sample.trajectory_id or "")
            if trajectory is not None:
                reached.update(events.get(event_id) for event_id in trajectory.event_ids)
        result[sample.sample_id] = "heldout" if milestones & reached else split_of(run_id, sample.sample_id)
    return result


def samples_part(bundle: TrajectoryBundle, split: dict[str, str]) -> str:
    lines = []
    for sample in bundle.samples:
        record = sample.model_dump(mode="json")
        record["split"] = split[sample.sample_id]
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def domain_part(bundle: TrajectoryBundle, split: dict[str, str]) -> str:
    sample_of = {}
    for sample in bundle.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id:
                sample_of[sequence.trajectory_id] = sample.sample_id
    lines = []
    for record_type, records in (
        ("object", bundle.objects),
        ("relationship", bundle.relationships),
        ("event", bundle.events),
        ("event_object", bundle.event_objects),
        ("state_transition", bundle.state_transitions),
        ("trajectory", bundle.trajectories),
    ):
        for record in records:
            row = {"record_type": record_type, **record.model_dump(mode="json")}
            if record_type == "trajectory":
                row["split"] = split.get(sample_of.get(record.trajectory_id, ""))
            lines.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def ocel_part(bundle: TrajectoryBundle) -> str:
    """The domain layer in OCEL 2.0 JSON: typed objects and events with qualified relationships."""
    events_by_id = {event.event_id: event for event in bundle.events}
    dimensions: dict[str, set[str]] = {}
    history: dict[str, list[dict]] = {}
    types_of = {obj.object_id: obj.object_type for obj in bundle.objects}
    for change in bundle.state_transitions:
        event = events_by_id.get(change.event_id)
        object_type = types_of.get(change.object_id)
        if event is None or object_type is None or change.state_after is None:
            continue
        dimensions.setdefault(object_type, set()).add(change.state_dimension)
        history.setdefault(change.object_id, []).append(
            {"name": change.state_dimension, "time": event.event_time.isoformat(), "value": change.state_after}
        )
    object_types = sorted({obj.object_type for obj in bundle.objects})
    event_attributes = (
        ("channel", "string"),
        ("status", "string"),
        ("amount", "float"),
        ("currency", "string"),
        ("direction", "string"),
        ("amount_role", "string"),
        ("effective_time", "time"),
        ("observation_status", "string"),
    )
    links: dict[str, list[dict]] = {}
    for link in bundle.event_objects:
        links.setdefault(link.event_id, []).append({"objectId": link.object_id, "qualifier": link.qualifier or link.object_role})
    related: dict[str, list[dict]] = {}
    for relationship in bundle.relationships:
        related.setdefault(relationship.subject_id, []).append({"objectId": relationship.object_id, "qualifier": relationship.predicate})
    document = {
        "objectTypes": [
            {
                "name": name,
                "attributes": [{"name": "subtype", "type": "string"}, {"name": "pii_class", "type": "string"}]
                + [{"name": dimension, "type": "string"} for dimension in sorted(dimensions.get(name, ()))],
            }
            for name in object_types
        ],
        "eventTypes": [
            {"name": name, "attributes": [{"name": key, "type": kind} for key, kind in event_attributes]}
            for name in sorted({event.event_type for event in bundle.events})
        ],
        "objects": [
            {
                "id": obj.object_id,
                "type": obj.object_type,
                "attributes": [
                    {"name": "subtype", "time": (obj.valid_from or datetime(1970, 1, 1, tzinfo=timezone.utc)).isoformat(), "value": obj.subtype},
                    {"name": "pii_class", "time": (obj.valid_from or datetime(1970, 1, 1, tzinfo=timezone.utc)).isoformat(), "value": obj.pii_class},
                ]
                + history.get(obj.object_id, []),
                "relationships": related.get(obj.object_id, []),
            }
            for obj in bundle.objects
        ],
        "events": [
            {
                "id": event.event_id,
                "type": event.event_type,
                "time": event.event_time.isoformat(),
                "attributes": [
                    {"name": key, "value": value}
                    for key, value in (
                        ("channel", event.channel_id),
                        ("status", event.status),
                        ("amount", event.amount),
                        ("currency", event.currency),
                        ("direction", event.direction),
                        ("amount_role", event.amount_role),
                        ("effective_time", event.effective_time.isoformat() if event.effective_time else None),
                        ("observation_status", event.observation_status.value),
                    )
                    if value is not None
                ],
                "relationships": links.get(event.event_id, []),
            }
            for event in bundle.events
        ],
    }
    return json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1)


def manifest_part(run, bundle: TrajectoryBundle, sector, cycles: list[dict], split: dict[str, str], held_out: str | None, files: dict[str, str]) -> str:
    generation = bundle.generation
    config = {key: value for key, value in (run.config or {}).items() if key != "credential_id"}
    counts = {name: sum(1 for value in split.values() if value == name) for name in ("train", "validation", "test", "heldout")}
    steering = dict(generation.steering or {}) if generation else {}
    manifest = {
        "format": "trajectory-studio-export",
        "format_version": FORMAT_VERSION,
        "run_id": run.id,
        "parent_run_id": run.parent_run_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "sector": sector.id,
        "generator_id": generation.generator_id if generation else None,
        "pack_version": generation.pack_version if generation else None,
        "configuration": config,
        "group_size": generation.group_size if generation else 1,
        "counts": {
            "samples": len(bundle.samples),
            "sequences": sum(len(sample.sequences) for sample in bundle.samples),
            "trajectories": len(bundle.trajectories),
            "events": len(bundle.events),
            "objects": len(bundle.objects),
            "relationships": len(bundle.relationships),
        },
        "split": {
            "method": "sha256(run_id|sample_id) over [0, 1): train below 0.8, validation below 0.9, test above",
            "ratios": SPLIT_RATIOS,
            "held_out_sub_domain": held_out,
            "held_out_rule": "A sample is held out when any of its sequences reaches a milestone event of that sub-domain."
            if held_out
            else None,
            "counts": counts,
        },
        "judge_cycles": [
            {
                "cycle": cycle["cycle_index"],
                "reference_quality": cycle["reference_quality"],
                "accepted": cycle["accepted"],
                "hard_check_passed": cycle["hard_check_passed"],
                "verdicts": [
                    {"rubric": verdict["rubric"], "score": verdict["score"], "judge_model": verdict["judge_model"]}
                    for verdict in cycle["verdicts"]
                ],
            }
            for cycle in cycles
        ],
        "rewards": generation.rewards if generation else None,
        "quality": generation.quality if generation else None,
        "steering": {key: steering.get(key) for key in ("currency", "channel", "products", "named_events", "negated_events", "weighted_events")}
        if steering
        else None,
        "documents": [
            {key: doc.get(key) for key in ("name", "kind", "readable", "reason", "terms", "named_events")}
            for doc in steering.get("documents", [])
        ],
        "data_card": {
            "scope": {"sector": sector.id, "sub_domains": config.get("sub_domains"), "language": config.get("language")},
            "jurisdiction_profile": "neutral retail; Turkey and United Kingdom profiles arrive in Slice 5",
            "intended_use": INTENDED_USE.get(config.get("consumer"), INTENDED_USE["post_training"]),
            "target_family": config.get("target_family"),
            "start": config.get("start_mode"),
            "reference": "weak (cold start)" if config.get("start_mode") == "cold" else "warm-start corpus",
            "known_limitations": list(LIMITATIONS),
        },
        "files": files,
        "synthetic": "Every record in this export is synthetic. No real person, account, policy, or claim is described.",
    }
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=1)


def build(run, bundle: TrajectoryBundle, sector, cycles: list[dict], held_out: str | None) -> dict[str, str]:
    split = splits(run.id, bundle, sector.lifecycle, held_out)
    parts = {
        "samples.jsonl": samples_part(bundle, split),
        "domain.jsonl": domain_part(bundle, split),
        "ocel.json": ocel_part(bundle),
    }
    files = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in parts.items()}
    parts["manifest.json"] = manifest_part(run, bundle, sector, cycles, split, held_out, files)
    return parts
