"""A run's overview for the studio: its variants and its process map.

Built incrementally, like the quality report, so a large run is summarised batch by batch.
"""

from __future__ import annotations

import hashlib

from sectors.quality import primary_journeys

TOP_VARIANTS = 50
EXAMPLES = 5


def variant_id(types: list[str]) -> str:
    return hashlib.sha1(">".join(types).encode()).hexdigest()[:12]


class OverviewAccumulator:
    def __init__(self, classify) -> None:
        self.classify = classify
        self.journeys = 0
        self.variants: dict[str, dict] = {}
        self.nodes: dict[str, list[float]] = {}
        self.edges: dict[str, int] = {}

    def add(self, bundle) -> None:
        events = {event.event_id: event.event_type for event in bundle.events}
        for trajectory in bundle.trajectories:
            if trajectory.parent_trajectory_id is not None:
                continue
            types = [events[event_id] for event_id in trajectory.event_ids if event_id in events]
            self.journeys += 1
            key = variant_id(types)
            entry = self.variants.setdefault(key, {"id": key, "types": types, "count": 0, "kind": self.classify(types), "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < EXAMPLES:
                entry["examples"].append(trajectory.trajectory_id)
            for index, name in enumerate(types):
                node = self.nodes.setdefault(name, [0, 0.0])
                node[0] += 1
                node[1] += index / (len(types) - 1) if len(types) > 1 else 0.0
            for a, b in zip(types, types[1:]):
                self.edges[f"{a}|{b}"] = self.edges.get(f"{a}|{b}", 0) + 1

    def report(self) -> dict:
        ranked = sorted(self.variants.values(), key=lambda item: (-item["count"], item["id"]))
        return {
            "journeys": self.journeys,
            "distinct_variants": len(self.variants),
            "variants": ranked[:TOP_VARIANTS],
            "nodes": [{"type": name, "count": int(count), "position": round(total / count, 4)} for name, (count, total) in sorted(self.nodes.items())],
            "edges": [{"from": key.split("|")[0], "to": key.split("|")[1], "count": count} for key, count in sorted(self.edges.items())],
        }

    def state(self) -> dict:
        return {"journeys": self.journeys, "variants": self.variants, "nodes": self.nodes, "edges": self.edges}

    def restore(self, state: dict) -> None:
        self.journeys = int(state.get("journeys", 0))
        self.variants = dict(state.get("variants", {}))
        self.nodes = {key: list(value) for key, value in state.get("nodes", {}).items()}
        self.edges = dict(state.get("edges", {}))


def overview_of(bundle, classify) -> dict:
    accumulator = OverviewAccumulator(classify)
    accumulator.add(bundle)
    return accumulator.report()
