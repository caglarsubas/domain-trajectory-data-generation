"""Calibration from real event logs: how often each legal step follows another, and how long it takes.

A calibration holds directly-follows counts between the pack's event types (after a data source's
activities are mapped to them), the same counts after each pair of events, how journeys start and end, and
duration quantiles per step. The walker blends the observed next-step shares with the pack's prior in
proportion to how much data backs them, and dwell times follow the observed quantiles once enough steps
were seen. The shares are taken after the last two events wherever at least PRIOR_STRENGTH observations back
them, and after the last event otherwise (decision 15), so an outcome that depends on the step before last,
such as a delayed flight's arrival, follows the data. The pack's machines still decide what is legal:
calibration only reweights legal choices.

A source sees only part of a journey: flight records know nothing of checked bags, and hotel bookings
nothing of loyalty sign-ups. An event a source never contains keeps its prior weight wherever it is a
choice, and the data redistributes only the weight of the choices it can see, with two exceptions for steps
the pack cannot take where the data takes them. A step the data records right after an event it never
contains, such as an approval after a KYC check BPI Challenge 2017 does not record, counts for that event
(`through_unseen`). A repeat the journey has made and cannot make again, such as validating an application
a second time, is followed through the data to the steps after it (`past_repeats`).
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property

# Observations that weigh as much as the pack's prior; more data moves the walker further toward the data.
PRIOR_STRENGTH = 25.0
MIN_DWELL_SAMPLES = 5
# Durations kept per step. Past this many, an even random sample stands for them all: files are often sorted, by hotel
# and date or by case start, so the first ones would stand for one part of the data.
MAX_DWELL_SAMPLES = 20000
START = "<start>"


def pair(before: str | None, previous: str) -> str:
    """The key of a two-event context: the event before last, or the start, and the last one."""
    return f"{before or START}>{previous}"


def context(types: list[str]) -> tuple[str | None, str | None]:
    """The last event and the one before it, as the data counts steps: a repeat of the last event is not a new step."""
    if not types:
        return None, None
    previous = types[-1]
    return previous, next((name for name in reversed(types) if name != previous), None)


@dataclass
class Calibration:
    transitions: dict[str, Counter] = field(default_factory=dict)
    # "a>b": the next steps observed after a then b, with "<start>" for a journey's first event.
    pairs: dict[str, Counter] = field(default_factory=dict)
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
            pairs={key: Counter(value) for key, value in (data.get("pairs") or {}).items()},
            starts=Counter(data.get("starts") or {}),
            ends=Counter(data.get("ends") or {}),
            dwell={key: tuple(value) for key, value in (data.get("dwell") or {}).items()},
            cases=int(data.get("cases") or 0),
            sources=list(data.get("sources") or []),
        )

    def as_dict(self) -> dict:
        return {
            "transitions": {key: dict(value) for key, value in self.transitions.items()},
            "pairs": {key: dict(value) for key, value in self.pairs.items()},
            "starts": dict(self.starts),
            "ends": dict(self.ends),
            "dwell": {key: list(value) for key, value in self.dwell.items()},
            "cases": self.cases,
            "sources": list(self.sources),
        }

    @property
    def empty(self) -> bool:
        return not self.transitions and not self.starts

    @cached_property
    def observed_events(self) -> set[str]:
        """Every event type the data contains anywhere; the rest are ones its sources cannot see.

        Read at every step of every calibrated walk, so it is computed once: a calibration is complete when built.
        """
        found = set(self.starts) | set(self.ends) | set(self.transitions)
        for following in self.transitions.values():
            found.update(following)
        return found

    def context(self, types: list[str]) -> tuple[str | None, str | None]:
        """A journey's last two events as the data would have recorded them.

        Events the data never contains leave no trace in its sequences, so the context is read over the events it does
        contain: a loyalty sign-up between a flight's departure and its arrival does not hide that the flight was
        delayed. Until the journey holds one of them, the data has recorded nothing, and its next one is drawn as the
        data's journeys start: flights in the on-time data are seen from check-in, so a delay there comes at the gate.
        """
        visible = self.observed_events
        return context([name for name in types if name in visible])

    def following(self, previous: str | None, options, before: str | None = None) -> Counter | None:
        """The observed next steps after the last two events where at least PRIOR_STRENGTH of them are among the options,
        else after the last event; the starts at a journey's first step."""
        if previous is None:
            return self.starts
        found = self.pairs.get(pair(before, previous))
        if found and sum(found.get(name, 0) for name, _ in options) >= PRIOR_STRENGTH:
            return found
        return self.transitions.get(previous)

    def past_repeats(self, observed: Counter, legal: set[str], taken, previous: str | None = None, depth: int = 8) -> Counter:
        """Observed next steps with each repeat the journey cannot make followed through the data to the steps after it.

        The data can go back to a step a journey has taken, such as validating an application again after asking for
        documents, where the pack's machines take it once. What the data does after that repeat, coming after
        `previous`, stands in for it, as `projected` follows steps through events a run leaves out. Dropping it would
        leave only the choices made at the first pass: BPI Challenge 2017 declines 42% of the applications it decides at
        their first validation, and 13% of those it validates again.
        """
        if not any(name in taken and name not in legal for name in observed):
            return observed
        # Each repeat is followed with the context it had in the data, the event it came after, where that pair is
        # backed: validating again after a review approves more than a first validation.
        kept, frontier = Counter(), Counter({(previous, event): count for event, count in observed.items()})
        for _ in range(depth):
            onward = Counter()
            for (source, event), weight in frontier.items():
                if event in legal or event not in taken:
                    kept[event] += weight
                    continue
                found = self.pairs.get(pair(source, event)) if source is not None else None
                following = found if found and sum(found.values()) >= PRIOR_STRENGTH else self.transitions.get(event)
                total = sum(following.values()) if following else 0
                for name, count in (following or {}).items():
                    onward[(event, name)] += weight * count / total
            frontier = onward
            if not frontier:
                break
        return kept

    def through_unseen(self, observed: Counter, options: list[tuple[str, float]], leads) -> Counter:
        """Observed next steps with each one the pack reaches only through an event the data never contains credited to it.

        The data skips what it cannot see: BPI Challenge 2017 records no passed KYC check, so an application goes from
        validation straight to its approval, where the pack passes the check first. A step the options leave out counts
        for the options the data never contains that lead to it, `leads(option, step)`, in proportion to their weights:
        a decline follows a passed check or a failed one. A step no such option leads to is left out, as before.
        """
        visible = self.observed_events
        unseen = [(name, weight) for name, weight in options if name not in visible]
        legal = {name for name, _ in options}
        if not unseen or all(name in legal for name in observed):
            return observed
        credited = Counter()
        for event, count in observed.items():
            leading = [] if event in legal else [(name, weight) for name, weight in unseen if leads(name, event)]
            total = sum(weight for _, weight in leading)
            if not total:
                credited[event] += count
                continue
            for name, weight in leading:
                credited[name] += count * weight / total
        return credited

    def reweight(
        self, previous: str | None, options: list[tuple[str, float]], before: str | None = None, taken=(), leads=None
    ) -> list[tuple[str, float]]:
        """Blend the observed shares of the next step with the prior weights, keeping their total.

        The shares are the ones after `before` then `previous` where enough data backs them, else after `previous`,
        with repeats of the events `taken` so far that the options leave out followed onward (`past_repeats`), and,
        given `leads`, steps reached through an event the data never contains credited to that event
        (`through_unseen`). Only the choices the data can see or was credited with are reweighted, within the weight
        they held together; any other choice keeps its prior weight.
        """
        observed = self.following(previous, options, before)
        if not observed or not options:
            return options
        observed = self.past_repeats(observed, {name for name, _ in options}, taken, previous)
        if leads is not None:
            observed = self.through_unseen(observed, options, leads)
        seen = sum(observed.get(name, 0) for name, _ in options)
        if not seen:
            return options
        visible = self.observed_events
        known = {name for name, _ in options if name in visible or observed.get(name)}
        total = sum(weight for name, weight in options if name in known) or 1.0
        trust = seen / (seen + PRIOR_STRENGTH)
        return [
            (name, total * ((1 - trust) * weight / total + trust * observed.get(name, 0) / seen)) if name in known else (name, weight)
            for name, weight in options
        ]

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
        # A pair through a left-out event is not a context the run can reach, so it falls back to the last event.
        seen.pairs = {
            key: spread(following)
            for key, following in self.pairs.items()
            if all(name == START or name in allowed for name in key.rsplit(">", 1))
        }
        seen.starts = spread(self.starts)
        return seen

    def summary(self) -> dict:
        return {
            "sources": list(self.sources),
            "cases": self.cases,
            "transitions": sum(len(value) for value in self.transitions.values()),
            "steps_observed": sum(sum(value.values()) for value in self.transitions.values()),
            # Two-event contexts backed well enough to be used instead of the last event alone.
            "second_order_contexts": sum(1 for value in self.pairs.values() if sum(value.values()) >= PRIOR_STRENGTH),
            "timed_steps": sum(1 for value in self.dwell.values() if value[3] >= MIN_DWELL_SAMPLES),
        }


def merge(calibrations: list[Calibration]) -> Calibration:
    """Several data sources as one: counts add up, and each step keeps the dwell with the most samples."""
    merged = Calibration()
    for item in calibrations:
        for key, value in item.transitions.items():
            merged.transitions.setdefault(key, Counter()).update(value)
        for key, value in item.pairs.items():
            merged.pairs.setdefault(key, Counter()).update(value)
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
    offered: Counter = Counter()
    # Seeded, so the same file gives the same calibration.
    rng = random.Random(0)
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
        names = [START] + [event for event, _ in runs]
        for a, b, c in zip(names, names[1:], names[2:]):
            calibration.pairs.setdefault(pair(a, b), Counter())[c] += 1
        for (a, at), (b, bt) in zip(runs, runs[1:]):
            calibration.transitions.setdefault(a, Counter())[b] += 1
            if at is not None and bt is not None and bt >= at:
                key = f"{a}>{b}"
                bucket = samples.setdefault(key, [])
                offered[key] += 1
                if len(bucket) < MAX_DWELL_SAMPLES:
                    bucket.append(bt - at)
                else:
                    slot = rng.randrange(offered[key])
                    if slot < MAX_DWELL_SAMPLES:
                        bucket[slot] = bt - at
    calibration.dwell = {key: quantiles(values) for key, values in samples.items() if values}
    return calibration


def _runs(types: list[str]) -> list[str]:
    return [name for index, name in enumerate(types) if index == 0 or types[index - 1] != name]


def steps_of(sequences: list[list[str]]) -> Counter:
    generated = Counter()
    for types in sequences:
        runs = _runs(types)
        for a, b in zip(runs, runs[1:]):
            generated[(a, b)] += 1
    return generated


def triples_of(sequences: list[list[str]], visible: set[str] | None = None) -> Counter:
    """Each step with the two events before it, the first of them "<start>" for a journey's second event. Given the events
    a calibration's data contains, the journeys are read over those alone, as the walker reads its context."""
    generated = Counter()
    for types in sequences:
        names = [START] + _runs([name for name in types if visible is None or name in visible])
        for a, b, c in zip(names, names[1:], names[2:]):
            generated[(pair(a, b), c)] += 1
    return generated


def representativeness(calibration: Calibration, generated: Counter, triples: Counter | None = None) -> dict:
    """How the generated journeys compare with the data: fitness, precision, the gap between next-step shares, and the
    same gap after each pair of events, where a conditional outcome shows even when the next-step shares match."""
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
    gaps, weights = [], []
    for a in {key[0] for key in generated} & {key[0] for key in real}:
        mine = {b: count for (x, b), count in generated.items() if x == a}
        theirs = {b: count for (x, b), count in real.items() if x == a}
        gaps.append(_jensen_shannon(mine, theirs))
        weights.append(sum(mine.values()))
    second = []
    for key in {context for context, _ in triples or {}} & set(calibration.pairs):
        theirs = {name: count for name, count in calibration.pairs[key].items() if name in events}
        # Only pairs the data backs as the walker would use them: at least PRIOR_STRENGTH observations.
        if sum(theirs.values()) < PRIOR_STRENGTH:
            continue
        mine = {name: count for (context, name), count in triples.items() if context == key and name in covered}
        if mine:
            second.append(_jensen_shannon(mine, theirs))
    return {
        "status": "measured",
        "sources": list(calibration.sources),
        "fitness": round(fitness, 3),
        "precision": round(precision, 3),
        "next_step_divergence": round(sum(gaps) / len(gaps), 3) if gaps else None,
        # The same gaps weighted by how often the run leaves each event: the plain mean lets a step taken twice count as
        # much as one taken a thousand times, so at a few hundred journeys its sampling noise alone reaches 0.1.
        "weighted_divergence": round(sum(gap * weight for gap, weight in zip(gaps, weights)) / sum(weights), 3) if gaps else None,
        "second_order_divergence": round(sum(second) / len(second), 3) if second else None,
        "covered_events": len(events & covered),
        "explanation": "Among events both the data and the run contain: fitness is the share of observed steps the generated journeys also take, precision the share of generated steps the data shows, and divergence compares next-step shares, 0 when they match, after the last event and after the last two; the weighted divergence counts each event by how often the run leaves it.",
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
