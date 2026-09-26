"""A run's overview for the studio: its variants and its process map.

Built incrementally, like the quality report, so a large run is summarised batch by batch.
Each event type also carries when it typically happens, as elapsed time from the journey's first
event and as a step number, and each transition its typical dwell, so the map can sit on a time axis.
Times are averaged on a log scale, the scale the studio draws them on.
"""

from __future__ import annotations

import hashlib
import math

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
        # type -> [occurrences, sum of relative position, sum of log1p hours from start, sum of step index, timed occurrences]
        self.nodes: dict[str, list[float]] = {}
        self.edges: dict[str, int] = {}
        # "from|to" -> [sum of log1p dwell hours, timed transitions]
        self.dwell: dict[str, list[float]] = {}

    def add(self, bundle) -> None:
        events = {event.event_id: event for event in bundle.events}
        for trajectory in bundle.trajectories:
            if trajectory.parent_trajectory_id is not None:
                continue
            chain = [events[event_id] for event_id in trajectory.event_ids if event_id in events]
            types = [event.event_type for event in chain]
            hours = [max(0.0, (event.event_time - chain[0].event_time).total_seconds() / 3600) for event in chain]
            self.journeys += 1
            key = variant_id(types)
            entry = self.variants.setdefault(key, {"id": key, "types": types, "count": 0, "kind": self.classify(types), "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < EXAMPLES:
                entry["examples"].append(trajectory.trajectory_id)
            for index, name in enumerate(types):
                node = self.nodes.setdefault(name, [0, 0.0, 0.0, 0.0, 0])
                node[0] += 1
                node[1] += index / (len(types) - 1) if len(types) > 1 else 0.0
                node[2] += math.log1p(hours[index])
                node[3] += index
                node[4] += 1
            for index, (a, b) in enumerate(zip(types, types[1:])):
                self.edges[f"{a}|{b}"] = self.edges.get(f"{a}|{b}", 0) + 1
                spent = self.dwell.setdefault(f"{a}|{b}", [0.0, 0])
                spent[0] += math.log1p(hours[index + 1] - hours[index])
                spent[1] += 1

    def report(self) -> dict:
        ranked = sorted(self.variants.values(), key=lambda item: (-item["count"], item["id"]))
        return {
            "journeys": self.journeys,
            "distinct_variants": len(self.variants),
            "variants": ranked[:TOP_VARIANTS],
            "nodes": [_node(name, value) for name, value in sorted(self.nodes.items())],
            "edges": [_edge(key, count, self.dwell.get(key)) for key, count in sorted(self.edges.items())],
        }

    def state(self) -> dict:
        return {"journeys": self.journeys, "variants": self.variants, "nodes": self.nodes, "edges": self.edges, "dwell": self.dwell}

    def restore(self, state: dict) -> None:
        self.journeys = int(state.get("journeys", 0))
        self.variants = dict(state.get("variants", {}))
        # A checkpoint written before timing was measured holds [occurrences, position]; its timing starts at zero.
        self.nodes = {key: [*value, 0.0, 0.0, 0][:5] for key, value in state.get("nodes", {}).items()}
        self.edges = dict(state.get("edges", {}))
        self.dwell = {key: list(value) for key, value in state.get("dwell", {}).items()}


def _node(name: str, value: list[float]) -> dict:
    count, position, log_hours, steps, timed = value
    node = {"type": name, "count": int(count), "position": round(position / count, 4)}
    if timed:
        node["hours"] = round(math.expm1(log_hours / timed), 3)
        node["step"] = round(1 + steps / timed, 2)
    return node


def _edge(key: str, count: int, dwell: list[float] | None) -> dict:
    source, target = key.split("|")
    edge = {"from": source, "to": target, "count": count}
    if dwell and dwell[1]:
        edge["hours"] = round(math.expm1(dwell[0] / dwell[1]), 3)
    return edge


def overview_of(bundle, classify) -> dict:
    accumulator = OverviewAccumulator(classify)
    accumulator.add(bundle)
    return accumulator.report()
