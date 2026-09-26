"""Where a run's journeys live, behind one interface.

Runs of up to SMALL_RUN_SEQUENCES sequences keep their bundle in the database, as before.
Larger runs are generated in batches of BATCH_SEQUENCES and written as files under
DATA_DIR/runs/<run id>/, so no request, and no worker, ever holds a whole large run.
"""

from __future__ import annotations

import gzip
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from trajectory_contract import TrajectoryBundle

SMALL_RUN_SEQUENCES = 64
BATCH_SEQUENCES = 256
MAX_RUN_SEQUENCES = 100_000


def data_dir() -> Path:
    root = Path(__file__).resolve().parents[3]
    return Path(os.environ.get("DATA_DIR", root / "data"))


def run_dir(run_id: str) -> Path:
    return data_dir() / "runs" / run_id


def batch_name(index: int) -> str:
    return f"B{index:04d}"


def write_json(path: Path, value) -> None:
    """Write atomically: a crash leaves the old file or the new one, never half of one. `.gz` paths are compressed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    temporary.write_bytes(gzip.compress(data, compresslevel=5) if path.suffix == ".gz" else data)
    os.replace(temporary, path)


def batch_path(root: Path, name: str) -> Path:
    return root / "batches" / f"{name}.json.gz"


def extract(bundle: dict, trajectory_id: str) -> dict | None:
    """One journey as a self-contained bundle: the trajectory, its alternatives or rollouts, and what they reference."""
    trajectories = [item for item in bundle["trajectories"] if item["trajectory_id"] == trajectory_id or item.get("parent_trajectory_id") == trajectory_id]
    if not trajectories:
        return None
    event_ids = {event_id for item in trajectories for event_id in item["event_ids"]}
    links = [link for link in bundle["event_objects"] if link["event_id"] in event_ids]
    object_ids = {link["object_id"] for link in links} | {item["root_party_id"] for item in trajectories}
    trajectory_ids = {item["trajectory_id"] for item in trajectories}
    return {
        "objects": [obj for obj in bundle["objects"] if obj["object_id"] in object_ids],
        "relationships": [rel for rel in bundle["relationships"] if rel["subject_id"] in object_ids and rel["object_id"] in object_ids],
        "events": [event for event in bundle["events"] if event["event_id"] in event_ids],
        "event_objects": links,
        "state_transitions": [change for change in bundle["state_transitions"] if change["event_id"] in event_ids],
        "trajectories": trajectories,
        "samples": [
            sample
            for sample in bundle["samples"]
            if sample.get("trajectory_id") in trajectory_ids
            or any(sequence.get("trajectory_id") in trajectory_ids for sequence in sample["sequences"])
        ],
        "generation": None,
    }


def journey_entries(bundle: dict, batch: str | None, variant_of) -> list[dict]:
    events = {event["event_id"]: event["event_type"] for event in bundle["events"]}
    sizes = {}
    outcomes = {}
    for sample in bundle["samples"]:
        for sequence in sample["sequences"]:
            if sequence.get("trajectory_id"):
                sizes[sequence["trajectory_id"]] = len(sample["sequences"])
                outcomes[sequence["trajectory_id"]] = sequence.get("outcome")
    entries = []
    for item in bundle["trajectories"]:
        if item.get("parent_trajectory_id"):
            continue
        types = [events[event_id] for event_id in item["event_ids"] if event_id in events]
        entries.append(
            {
                "trajectory_id": item["trajectory_id"],
                "trajectory_type": item["trajectory_type"],
                "events": len(types),
                "variant": variant_of(types),
                "sequences": sizes.get(item["trajectory_id"], 1),
                "outcome": outcomes.get(item["trajectory_id"]),
                "batch": batch,
            }
        )
    return entries


class DbStore:
    """A run whose bundle sits in `runs.candidate`."""

    paged = False

    def __init__(self, candidate: dict) -> None:
        self.bundle = candidate
        self._events = {event["event_id"]: event["event_type"] for event in candidate["events"]}
        self._trajectories = {item["trajectory_id"]: item["trajectory_type"] for item in candidate["trajectories"]}

    def entries(self) -> list[dict]:
        from sectors.overview import variant_id

        return journey_entries(self.bundle, None, variant_id)

    def journey(self, trajectory_id: str) -> dict | None:
        return extract(self.bundle, trajectory_id)

    def event_type(self, event_id: str) -> str | None:
        return self._events.get(event_id)

    def trajectory_type(self, trajectory_id: str) -> str | None:
        return self._trajectories.get(trajectory_id)

    def bundles(self) -> Iterator[TrajectoryBundle]:
        yield TrajectoryBundle.model_validate(self.bundle)


class FileStore:
    """A run written as batch files, with an index of its primary journeys."""

    paged = True

    def __init__(self, run_id: str) -> None:
        self.root = run_dir(run_id)

    def exists(self) -> bool:
        return (self.root / "index.json").is_file()

    def entries(self) -> list[dict]:
        path = self.root / "index.json"
        return _read_json(str(path), path.stat().st_mtime)["journeys"]

    def batches(self) -> list[str]:
        path = self.root / "index.json"
        return _read_json(str(path), path.stat().st_mtime)["batches"]

    def batch(self, name: str) -> dict:
        path = batch_path(self.root, name)
        return _read_json(str(path), path.stat().st_mtime)

    def _batch_of(self, record_id: str) -> str | None:
        namespace, dot, _ = record_id.partition(".")
        return namespace if dot and namespace in self.batches() else None

    def journey(self, trajectory_id: str) -> dict | None:
        name = self._batch_of(trajectory_id)
        return extract(self.batch(name), trajectory_id) if name else None

    def event_type(self, event_id: str) -> str | None:
        name = self._batch_of(event_id)
        if name is None:
            return None
        return next((event["event_type"] for event in self.batch(name)["events"] if event["event_id"] == event_id), None)

    def trajectory_type(self, trajectory_id: str) -> str | None:
        name = self._batch_of(trajectory_id)
        if name is None:
            return None
        return next((item["trajectory_type"] for item in self.batch(name)["trajectories"] if item["trajectory_id"] == trajectory_id), None)

    def bundles(self) -> Iterator[TrajectoryBundle]:
        for name in self.batches():
            yield TrajectoryBundle.model_validate(self.batch(name))


@lru_cache(maxsize=8)
def _read_json(path: str, mtime: float) -> dict:
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw)


def store_for(run) -> DbStore | FileStore | None:
    if run.candidate:
        return DbStore(run.candidate)
    files = FileStore(run.id)
    return files if files.exists() else None
