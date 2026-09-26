"""Export a run: samples.jsonl, domain.jsonl, ocel.json, and manifest.json.

One exporter writes every part from a stream of bundles: a small run's single bundle, or a large
run's batches one at a time, so no export holds a whole large run. A small run's parts are built in
memory and served at once; a large run's are written by a job as gzip files and served from disk.
Either way a re-export with the same held-out sub-domain reproduces the same content and split.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from trajectory_contract import TrajectoryBundle

PARTS = ("samples.jsonl", "domain.jsonl", "ocel.json", "manifest.json")
DATA_PARTS = PARTS[:3]
SPLIT_RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
FORMAT_VERSION = 1
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
EVENT_ATTRIBUTES = (
    ("channel", "string"),
    ("status", "string"),
    ("amount", "float"),
    ("currency", "string"),
    ("direction", "string"),
    ("amount_role", "string"),
    ("effective_time", "time"),
    ("observation_status", "string"),
)

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


class Sink:
    """A text destination that also hashes what it writes, so the manifest can carry checksums."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, text: str) -> None:
        data = text.encode()
        self.digest.update(data)
        self.size += len(data)
        self.stream.write(data)


def _line(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


class Exporter:
    """Add bundles one at a time; `finish` writes the OCEL document and returns the manifest."""

    def __init__(self, run_id: str, lifecycle, held_out: str | None, open_part, scratch) -> None:
        self.run_id = run_id
        self.lifecycle = lifecycle
        self.held_out = held_out
        self.open_part = open_part
        self.samples = Sink(open_part("samples.jsonl"))
        self.domain = Sink(open_part("domain.jsonl"))
        self.ocel_objects = scratch("objects")
        self.ocel_events = scratch("events")
        self.first_object = self.first_event = True
        self.object_dimensions: dict[str, set[str]] = {}
        self.event_types: set[str] = set()
        self.counts = {"samples": 0, "sequences": 0, "trajectories": 0, "events": 0, "objects": 0, "relationships": 0}
        self.split_counts = {"train": 0, "validation": 0, "test": 0, "heldout": 0}

    def add(self, bundle: TrajectoryBundle) -> None:
        split = splits(self.run_id, bundle, self.lifecycle, self.held_out)
        sample_of = {}
        for sample in bundle.samples:
            record = sample.model_dump(mode="json")
            record["split"] = split[sample.sample_id]
            self.samples.write(_line(record))
            self.split_counts[record["split"]] += 1
            self.counts["samples"] += 1
            self.counts["sequences"] += len(sample.sequences)
            for sequence in sample.sequences:
                if sequence.trajectory_id:
                    sample_of[sequence.trajectory_id] = sample.sample_id
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
                self.domain.write(_line(row))
        self.counts["trajectories"] += len(bundle.trajectories)
        self.counts["events"] += len(bundle.events)
        self.counts["objects"] += len(bundle.objects)
        self.counts["relationships"] += len(bundle.relationships)
        self._ocel(bundle)

    def _ocel(self, bundle: TrajectoryBundle) -> None:
        events_by_id = {event.event_id: event for event in bundle.events}
        types_of = {obj.object_id: obj.object_type for obj in bundle.objects}
        history: dict[str, list[dict]] = {}
        for change in bundle.state_transitions:
            event = events_by_id.get(change.event_id)
            object_type = types_of.get(change.object_id)
            if event is None or object_type is None or change.state_after is None:
                continue
            self.object_dimensions.setdefault(object_type, set()).add(change.state_dimension)
            history.setdefault(change.object_id, []).append(
                {"name": change.state_dimension, "time": event.event_time.isoformat(), "value": change.state_after}
            )
        links: dict[str, list[dict]] = {}
        for link in bundle.event_objects:
            links.setdefault(link.event_id, []).append({"objectId": link.object_id, "qualifier": link.qualifier or link.object_role})
        related: dict[str, list[dict]] = {}
        for relationship in bundle.relationships:
            related.setdefault(relationship.subject_id, []).append({"objectId": relationship.object_id, "qualifier": relationship.predicate})
        for obj in bundle.objects:
            self.object_dimensions.setdefault(obj.object_type, set())
            since = (obj.valid_from or EPOCH).isoformat()
            item = {
                "id": obj.object_id,
                "type": obj.object_type,
                "attributes": [
                    {"name": "subtype", "time": since, "value": obj.subtype},
                    {"name": "pii_class", "time": since, "value": obj.pii_class},
                ]
                + history.get(obj.object_id, []),
                "relationships": related.get(obj.object_id, []),
            }
            self.ocel_objects.write(("" if self.first_object else ",") + json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            self.first_object = False
        for event in bundle.events:
            self.event_types.add(event.event_type)
            attributes = (
                ("channel", event.channel_id),
                ("status", event.status),
                ("amount", event.amount),
                ("currency", event.currency),
                ("direction", event.direction),
                ("amount_role", event.amount_role),
                ("effective_time", event.effective_time.isoformat() if event.effective_time else None),
                ("observation_status", event.observation_status.value),
            )
            item = {
                "id": event.event_id,
                "type": event.event_type,
                "time": event.event_time.isoformat(),
                "attributes": [{"name": key, "value": value} for key, value in attributes if value is not None],
                "relationships": links.get(event.event_id, []),
            }
            self.ocel_events.write(("" if self.first_event else ",") + json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            self.first_event = False

    def finish(self, run, sector, cycles: list[dict], generation: dict | None) -> dict:
        ocel = Sink(self.open_part("ocel.json"))
        types = [
            {
                "name": name,
                "attributes": [{"name": "subtype", "type": "string"}, {"name": "pii_class", "type": "string"}]
                + [{"name": dimension, "type": "string"} for dimension in sorted(self.object_dimensions[name])],
            }
            for name in sorted(self.object_dimensions)
        ]
        kinds = [{"name": name, "attributes": [{"name": key, "type": kind} for key, kind in EVENT_ATTRIBUTES]} for name in sorted(self.event_types)]
        ocel.write('{"eventTypes":' + json.dumps(kinds, separators=(",", ":")) + ',"events":[')
        _copy(self.ocel_events, ocel)
        ocel.write('],"objectTypes":' + json.dumps(types, separators=(",", ":")) + ',"objects":[')
        _copy(self.ocel_objects, ocel)
        ocel.write("]}")
        files = {name: sink.digest.hexdigest() for name, sink in (("samples.jsonl", self.samples), ("domain.jsonl", self.domain), ("ocel.json", ocel))}
        sizes = {name: sink.size for name, sink in (("samples.jsonl", self.samples), ("domain.jsonl", self.domain), ("ocel.json", ocel))}
        manifest = _manifest(run, sector, cycles, generation or {}, self.counts, self.split_counts, self.held_out, files, sizes)
        Sink(self.open_part("manifest.json")).write(manifest)
        return {"files": files, "sizes": sizes, "counts": self.counts, "split": self.split_counts}


def _copy(scratch, sink: Sink) -> None:
    scratch.seek(0)
    while chunk := scratch.read(1 << 20):
        sink.write(chunk)


def _manifest(run, sector, cycles, generation, counts, split_counts, held_out, files, sizes) -> str:
    config = {key: value for key, value in (run.config or {}).items() if key != "credential_id"}
    steering = dict(generation.get("steering") or {})
    limitations = list(LIMITATIONS)
    latest = cycles[-1] if cycles else None
    accepted = bool(latest and latest["accepted"])
    if not accepted:
        limitations.append("The judge had not accepted this run when it was exported; it was exported on request.")
    if not generation.get("storage"):
        limitations.append("This run was small enough to keep on the run itself; runs above 64 sequences are written in batches.")
    manifest = {
        "format": "trajectory-studio-export",
        "format_version": FORMAT_VERSION,
        "run_id": run.id,
        "parent_run_id": run.parent_run_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "sector": sector.id,
        "generator_id": generation.get("generator_id"),
        "pack_version": generation.get("pack_version"),
        "configuration": config,
        "group_size": generation.get("group_size") or 1,
        "target": generation.get("target"),
        "counts": counts,
        "split": {
            "method": "sha256(run_id|sample_id) over [0, 1): train below 0.8, validation below 0.9, test above",
            "ratios": SPLIT_RATIOS,
            "held_out_sub_domain": held_out,
            "held_out_rule": "A sample is held out when any of its sequences reaches a milestone event of that sub-domain."
            if held_out
            else None,
            "counts": split_counts,
        },
        "review": {
            "accepted": accepted,
            "cycle": latest["cycle_index"] if latest else None,
            "exported_without_acceptance": not accepted,
        },
        "judge_cycles": [
            {
                "cycle": cycle["cycle_index"],
                "reference_quality": cycle["reference_quality"],
                "accepted": cycle["accepted"],
                "hard_check_passed": cycle["hard_check_passed"],
                "models": cycle.get("models", []),
                "sample": [entry["trajectory_id"] for entry in cycle.get("sample", [])],
                "scores": cycle.get("scores", {}),
                "agreement": cycle.get("agreement", {}),
                "flags": cycle.get("flags", []),
                "canary": cycle.get("canary"),
                "verdicts": [
                    {
                        "rubric": verdict["rubric"],
                        "score": verdict["score"],
                        "judge_model": verdict["judge_model"],
                        "trajectory_id": verdict.get("trajectory_id"),
                        "order": verdict.get("order"),
                        "canary": verdict.get("canary", False),
                    }
                    for verdict in cycle["verdicts"]
                ],
            }
            for cycle in cycles
        ],
        "rewards": generation.get("rewards"),
        "quality": generation.get("quality"),
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
            "known_limitations": limitations,
        },
        "files": files,
        "file_sizes": sizes,
        "synthetic": "Every record in this export is synthetic. No real person, account, policy, or claim is described.",
    }
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=1)


def build(run, bundle: TrajectoryBundle, sector, cycles: list[dict], held_out: str | None) -> dict[str, str]:
    """A small run's parts, in memory."""
    buffers = {name: io.BytesIO() for name in PARTS}
    exporter = Exporter(run.id, sector.lifecycle, held_out, lambda name: buffers[name], lambda _name: io.StringIO())
    exporter.add(bundle)
    exporter.finish(run, sector, cycles, run.generation or (bundle.generation.model_dump(mode="json") if bundle.generation else None))
    return {name: buffer.getvalue().decode() for name, buffer in buffers.items()}


def export_key(held_out: str | None, unaccepted: bool = False) -> str:
    """An export made before the judge accepted the run lives apart, so an accepted export never inherits its manifest."""
    base = f"heldout-{held_out}" if held_out else "all"
    return f"{base}-unaccepted" if unaccepted else base


def part_path(root: Path, held_out: str | None, part: str, unaccepted: bool = False) -> Path:
    suffix = "" if part == "manifest.json" else ".gz"
    return root / "exports" / export_key(held_out, unaccepted) / f"{part}{suffix}"


def write_files(run, store, sector, cycles: list[dict], held_out: str | None, root: Path, report, unaccepted: bool = False) -> dict:
    """A large run's parts, written batch by batch as gzip files under the run's directory."""
    target = root / "exports" / export_key(held_out, unaccepted)
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    handles = []

    def open_part(name: str):
        path = staging / (name if name == "manifest.json" else f"{name}.gz")
        handle = open(path, "wb") if name == "manifest.json" else gzip.open(path, "wb", compresslevel=5)
        handles.append(handle)
        return handle

    def scratch(name: str):
        handle = open(staging / f"{name}.part", "w+", encoding="utf-8")
        handles.append(handle)
        return handle

    try:
        exporter = Exporter(run.id, sector.lifecycle, held_out, open_part, scratch)
        names = store.batches()
        for index, bundle in enumerate(store.bundles(), start=1):
            exporter.add(bundle)
            report(index, len(names) + 1, f"Exported batch {index} of {len(names)}.")
        summary = exporter.finish(run, sector, cycles, run.generation)
    finally:
        for handle in handles:
            handle.close()
    for leftover in staging.glob("*.part"):
        leftover.unlink()
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return summary


def prepared(root: Path, held_out: str | None, unaccepted: bool = False) -> bool:
    return part_path(root, held_out, "manifest.json", unaccepted).is_file()
