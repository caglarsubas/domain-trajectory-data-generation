"""Calibration from real event logs: how often each legal step follows another, and how long it takes.

A calibration holds directly-follows counts between the pack's event types (after a data source's
activities are mapped to them), how journeys start and end, and duration quantiles per step. The walker
blends the observed next-step shares with the pack's prior in proportion to how much data backs them,
and dwell times follow the observed quantiles once enough steps were seen. The pack's machines still
decide what is legal: calibration only reweights legal choices.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field

# Observations that weigh as much as the pack's prior; more data moves the walker further toward the data.
PRIOR_STRENGTH = 25.0
MIN_DWELL_SAMPLES = 5
START = "<start>"


@dataclass
class Calibration:
    transitions: dict[str, Counter] = field(default_factory=dict)
    starts: Counter = field(default_factory=Counter)
    ends: Counter = field(default_factory=Counter)
    # "a>b": (median hours, 10th percentile, 90th percentile, samples)
    dwell: dict[str, tuple[float, float, float, int]] = field(default_factory=dict)
    cases: int = 0
    sources: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Calibration":
        data = data or {}
        return cls(
            transitions={key: Counter(value) for key, value in (data.get("transitions") or {}).items()},
            starts=Counter(data.get("starts") or {}),
            ends=Counter(data.get("ends") or {}),
            dwell={key: tuple(value) for key, value in (data.get("dwell") or {}).items()},
            cases=int(data.get("cases") or 0),
            sources=list(data.get("sources") or []),
        )

    def as_dict(self) -> dict:
        return {
            "transitions": {key: dict(value) for key, value in self.transitions.items()},
            "starts": dict(self.starts),
            "ends": dict(self.ends),
            "dwell": {key: list(value) for key, value in self.dwell.items()},
            "cases": self.cases,
            "sources": list(self.sources),
        }

    @property
    def empty(self) -> bool:
        return not self.transitions and not self.starts

    def reweight(self, previous: str | None, options: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Blend the observed shares of the next step with the prior weights, keeping their total."""
        observed = self.starts if previous is None else self.transitions.get(previous)
        if not observed or not options:
            return options
        seen = sum(observed.get(name, 0) for name, _ in options)
        if not seen:
            return options
        total = sum(weight for _, weight in options) or 1.0
        trust = seen / (seen + PRIOR_STRENGTH)
        return [(name, total * ((1 - trust) * weight / total + trust * observed.get(name, 0) / seen)) for name, weight in options]

    def dwell_hours(self, rng: random.Random, previous: str | None, event: str) -> float | None:
        """Hours from the previous step to this one drawn around the observed quantiles, or None without enough data."""
        if previous is None:
            return None
        found = self.dwell.get(f"{previous}>{event}")
        if not found or found[3] < MIN_DWELL_SAMPLES or found[0] <= 0:
            return None
        median, low, high, _ = found
        low, high = max(low, 1e-3), max(high, low * 1.01)
        sigma = max(0.1, (math.log(high) - math.log(low)) / 2.563)
        return min(high * 2, max(low / 2, rng.lognormvariate(math.log(median), sigma)))

    def projected(self, allowed, depth: int = 8) -> "Calibration":
        """The calibration seen from a run's scope: steps through events it leaves out are followed to the next event it keeps."""
        allowed = set(allowed)

        def spread(counts) -> Counter:
            kept, frontier = Counter(), Counter(counts)
            for _ in range(depth):
                onward = Counter()
                for event, weight in frontier.items():
                    if event in allowed:
                        kept[event] += weight
                        continue
                    following = self.transitions.get(event)
                    total = sum(following.values()) if following else 0
                    for name, count in (following or {}).items():
                        onward[name] += weight * count / total
                frontier = onward
                if not frontier:
                    break
            return kept

        seen = Calibration(dwell=dict(self.dwell), ends=Counter(self.ends), cases=self.cases, sources=list(self.sources))
        seen.transitions = {event: spread(following) for event, following in self.transitions.items() if event in allowed}
        seen.starts = spread(self.starts)
        return seen

    def summary(self) -> dict:
        return {
            "sources": list(self.sources),
            "cases": self.cases,
            "transitions": sum(len(value) for value in self.transitions.values()),
            "steps_observed": sum(sum(value.values()) for value in self.transitions.values()),
            "timed_steps": sum(1 for value in self.dwell.values() if value[3] >= MIN_DWELL_SAMPLES),
        }


def merge(calibrations: list[Calibration]) -> Calibration:
    """Several data sources as one: counts add up, and each step keeps the dwell with the most samples."""
    merged = Calibration()
    for item in calibrations:
        for key, value in item.transitions.items():
            merged.transitions.setdefault(key, Counter()).update(value)
        merged.starts.update(item.starts)
        merged.ends.update(item.ends)
        for key, value in item.dwell.items():
            if key not in merged.dwell or value[3] > merged.dwell[key][3]:
                merged.dwell[key] = value
        merged.cases += item.cases
        merged.sources.extend(item.sources)
    return merged


def quantiles(samples: list[float]) -> tuple[float, float, float, int]:
    ordered = sorted(samples)
    count = len(ordered)

    def at(share: float) -> float:
        return round(ordered[min(count - 1, int(share * (count - 1) + 0.5))], 4)

    return at(0.5), at(0.1), at(0.9), count


def build(sequences, *, source: str) -> Calibration:
    """A calibration from mapped sequences: lists of (event type, hours since the case began or None)."""
    calibration = Calibration(sources=[source])
    samples: dict[str, list[float]] = {}
    for steps in sequences:
        runs = []
        for event, when in steps:
            if not runs or runs[-1][0] != event:
                runs.append((event, when))
        if not runs:
            continue
        calibration.cases += 1
        calibration.starts[runs[0][0]] += 1
        calibration.ends[runs[-1][0]] += 1
        for (a, at), (b, bt) in zip(runs, runs[1:]):
            calibration.transitions.setdefault(a, Counter())[b] += 1
            if at is not None and bt is not None and bt >= at:
                bucket = samples.setdefault(f"{a}>{b}", [])
                if len(bucket) < 20000:
                    bucket.append(bt - at)
    calibration.dwell = {key: quantiles(values) for key, values in samples.items() if values}
    return calibration


def steps_of(sequences: list[list[str]]) -> Counter:
    generated = Counter()
    for types in sequences:
        runs = [name for index, name in enumerate(types) if index == 0 or types[index - 1] != name]
        for a, b in zip(runs, runs[1:]):
            generated[(a, b)] += 1
    return generated


def representativeness(calibration: Calibration, generated: Counter) -> dict:
    """How the generated journeys compare with the data: fitness, precision, and the gap between next-step shares."""
    real = Counter()
    for a, following in calibration.transitions.items():
        for b, count in following.items():
            real[(a, b)] += count
    if not real or not generated:
        return {"status": "not_measured", "reason": "No calibrated step overlaps the generated journeys."}
    # Each measure only counts steps between events both sides know: the data says nothing about the others.
    events = {a for a, _ in generated} | {b for _, b in generated}
    covered = {a for a, _ in real} | {b for _, b in real}
    relevant = Counter({key: value for key, value in real.items() if key[0] in events and key[1] in events})
    comparable = Counter({key: value for key, value in generated.items() if key[0] in covered and key[1] in covered})
    if not relevant or not comparable:
        return {"status": "not_measured", "reason": "The data sources and the generated journeys share no steps."}
    fitness = sum(value for key, value in relevant.items() if key in generated) / sum(relevant.values())
    precision = sum(value for key, value in comparable.items() if key in real) / sum(comparable.values())
    gaps = []
    for a in {key[0] for key in generated} & {key[0] for key in real}:
        mine = {b: count for (x, b), count in generated.items() if x == a}
        theirs = {b: count for (x, b), count in real.items() if x == a}
        gaps.append(_jensen_shannon(mine, theirs))
    return {
        "status": "measured",
        "sources": list(calibration.sources),
        "fitness": round(fitness, 3),
        "precision": round(precision, 3),
        "next_step_divergence": round(sum(gaps) / len(gaps), 3) if gaps else None,
        "covered_events": len(events & covered),
        "explanation": "Among events both the data and the run contain: fitness is the share of observed steps the generated journeys also take, precision the share of generated steps the data shows, and divergence compares next-step shares, 0 when they match.",
    }


def _jensen_shannon(p: dict, q: dict) -> float:
    keys = set(p) | set(q)
    ps, qs = sum(p.values()) or 1, sum(q.values()) or 1
    total = 0.0
    for key in keys:
        a, b = p.get(key, 0) / ps, q.get(key, 0) / qs
        middle = (a + b) / 2
        if a:
            total += 0.5 * a * math.log2(a / middle)
        if b:
            total += 0.5 * b * math.log2(b / middle)
    return total
