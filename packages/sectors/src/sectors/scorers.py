"""The five signal mechanisms as scorers: what a sequence's score means, and when it passes.

Each scorer reads only a journey's event types and times, so the same code verifies a generated
sequence and a model's attempt at an evaluation task. The run's signal decides which verdict is the
sequence's pass or fail, and so drives rewards, advantages, and group acceptance; the solution and
behavior rubrics are always the solution and behavior terms of MiMo's multiplicative reward.

- outcome: the journey reaches the pack's goal.
- solution rubric: the resulting state; the goal, no decision left open, and every selected sub-domain reached.
- behavior rubric: how the path was built; no step undoes an earlier state, and waits in the faster half of each step's range.
- process conformance: how typical each step is under the reference process (the priors, or the calibrated shares).
- decision score: at each outcome decision, the simulated value of the choice against the best choice there.
"""

from __future__ import annotations

import math
from typing import Any

from sectors.lifecycle import Walker, apply

SIGNALS = ("outcome", "solution_rubric", "behavior_rubric", "process_conformance", "decision_score")
PASS_AT = (1, 2, 4, 8, 16)
PROMPT_SHARE = 0.5
TYPICAL = 0.5
NEAR_BEST = 0.9

VERIFIERS = {
    "outcome": {
        "description": "The journey reaches the pack's goal.",
        "items": {"goal": "1 when the journey ends without a failed outcome, as the pack's goal states."},
        "score": "goal",
        "pass": "goal = 1",
    },
    "solution_rubric": {
        "description": "The resulting state is the one wanted.",
        "items": {
            "goal": "1 when the journey reaches the pack's goal.",
            "settled": "1 when no one-off outcome decision, such as a KYC result or a credit decision, is left open at the end.",
            "scope": "The share of the run's sub-domains whose milestone events the journey reaches.",
        },
        "score": "mean of the items",
        "pass": "goal = 1 and settled = 1",
    },
    "behavior_rubric": {
        "description": "The path was built well.",
        "items": {
            "no_rework": "1 minus the share of steps that return an object to a state it had already left.",
            "prompt": "The share of waits in the faster half of each step's range (at or below its geometric middle).",
        },
        "score": "mean of the items",
        "pass": f"no_rework = 1 and prompt >= {PROMPT_SHARE}",
    },
    "process_conformance": {
        "description": "Each step follows the reference process.",
        "items": {
            "typicality": "The mean over steps of the chosen step's share divided by the most likely step's share, under the run's policy.",
            "observed_share": "For calibrated runs, the share of transitions the data sources observed.",
        },
        "score": "typicality",
        "pass": f"typicality >= {TYPICAL}",
    },
    "decision_score": {
        "description": "Each outcome decision took a choice as good as the best one there.",
        "items": {
            "decisions": "How many first outcome decisions of each group the journey made.",
            "worst": "The lowest ratio of a choice's simulated value to the best value at its decision.",
        },
        "score": "mean ratio over decisions, 1 without decisions",
        "pass": f"worst >= {NEAR_BEST}",
    },
}


class Scorer:
    """Score a path with every signal under a run's policy and scope."""

    def __init__(self, pack, walker: Walker, *, domains: list[str], floor: int, cap: int, decisions: bool = False) -> None:
        from sectors.decisions import _Values

        self.pack, self.walker, self.domains = pack, walker, domains
        lifecycle = pack.lifecycle
        groups: dict[str, list] = {}
        for spec in lifecycle.events:
            if spec.outcome:
                groups.setdefault(spec.outcome, []).append(spec)
        # One-off decisions: every outcome of the group happens once, unlike a monthly repayment.
        self.one_off = {group for group, members in groups.items() if all(spec.repeat == 1 for spec in members)}
        self.milestones = {domain: set(lifecycle.milestones.get(domain, ())) & set(walker.allowed) for domain in domains}
        self.values = _Values(pack, walker, floor=floor, cap=cap) if decisions else None

    def score(self, types: list[str], hours: list[float] | None = None) -> dict[str, dict[str, Any]]:
        """Every signal's score, verdict, and items for one path; `hours` are the waits before each step after the first."""
        found = {
            "outcome": self.outcome(types),
            "solution_rubric": self.solution(types),
            "behavior_rubric": self.behavior(types, hours),
            "process_conformance": self.conformance(types),
        }
        if self.values is not None:
            found["decision_score"] = self.decision(types)
        return found

    def outcome(self, types: list[str]) -> dict:
        goal = bool(self.pack.success(types))
        return {"score": float(goal), "passed": goal, "items": {"goal": float(goal)}}

    def solution(self, types: list[str]) -> dict:
        lifecycle = self.pack.lifecycle
        goal = float(bool(self.pack.success(types)))
        state: dict = {}
        counts: dict[str, int] = {}
        for name in types:
            apply(lifecycle[name], state)
            counts[name] = counts.get(name, 0) + 1
        ended = bool(types) and lifecycle[types[-1]].ends_journey
        open_decisions = [] if ended else [name for name, _ in self.walker.options(state, counts) if lifecycle[name].outcome in self.one_off]
        settled = float(not open_decisions)
        reachable = [domain for domain, events in self.milestones.items() if events]
        scope = sum(1 for domain in reachable if self.milestones[domain] & set(types)) / len(reachable) if reachable else 1.0
        items = {"goal": goal, "settled": settled, "scope": round(scope, 4)}
        return {"score": round(sum(items.values()) / len(items), 4), "passed": goal == 1.0 and settled == 1.0, "items": items}

    def behavior(self, types: list[str], hours: list[float] | None) -> dict:
        lifecycle = self.pack.lifecycle
        state: dict = {}
        left: dict[tuple[str, str], set[str]] = {}
        rework = 0
        for name in types:
            spec = lifecycle[name]
            returned = False
            for effect in spec.sets:
                key = (effect.kind, effect.dimension)
                current = state.get(key)
                if current is not None and current != effect.state:
                    left.setdefault(key, set()).add(current)
                    returned = returned or effect.state in left[key]
                state[key] = effect.state
            rework += int(returned)
        no_rework = 1.0 - rework / len(types) if types else 1.0
        waits = list(hours or [])[: max(len(types) - 1, 0)]
        quick = 0
        for name, wait in zip(types[1:], waits):
            low, high = lifecycle[name].dwell_hours
            quick += int(wait <= math.sqrt(max(low, 1e-3) * max(high, 1e-3)))
        prompt = quick / len(waits) if waits else 1.0
        items = {"no_rework": round(no_rework, 4), "prompt": round(prompt, 4)}
        return {"score": round(sum(items.values()) / len(items), 4), "passed": no_rework == 1.0 and prompt >= PROMPT_SHARE, "items": items}

    def conformance(self, types: list[str]) -> dict:
        lifecycle, walker = self.pack.lifecycle, self.walker
        state: dict = {}
        counts: dict[str, int] = {}
        ratios = []
        observed = []
        for index, name in enumerate(types):
            options = walker.options(state, counts, first=index == 0)
            if walker.calibration is not None:
                options = walker.calibration.reweight(types[index - 1] if index else None, options)
                following = walker.calibration.starts if index == 0 else walker.calibration.transitions.get(types[index - 1])
                observed.append(bool(following and following.get(name)))
            weights = dict(options)
            if name in weights and len(options) > 1:
                ratios.append(weights[name] / max(weights.values()))
            apply(lifecycle[name], state)
            counts[name] = counts.get(name, 0) + 1
        typicality = sum(ratios) / len(ratios) if ratios else 1.0
        items: dict[str, float] = {"typicality": round(typicality, 4), "steps": float(len(ratios))}
        if observed:
            items["observed_share"] = round(sum(observed) / len(observed), 4)
        return {"score": round(typicality, 4), "passed": typicality >= TYPICAL, "items": items}

    def decision(self, types: list[str]) -> dict:
        from sectors.decisions import decision_steps

        lifecycle = self.pack.lifecycle
        ratios = []
        for index, state, counts, rivals in decision_steps(lifecycle, self.walker, types):
            values = {}
            for name, _ in rivals:
                after, tally = dict(state), dict(counts)
                apply(lifecycle[name], after)
                tally[name] = tally.get(name, 0) + 1
                values[name] = self.values(types[:index] + [name], after, tally)
            best = max(values.values())
            ratios.append(values[types[index]] / best if best > 0 else 1.0)
        score = sum(ratios) / len(ratios) if ratios else 1.0
        worst = min(ratios) if ratios else 1.0
        items = {"decisions": float(len(ratios)), "worst": round(worst, 4)}
        return {"score": round(score, 4), "passed": worst >= NEAR_BEST, "items": items}


def pass_at_k(attempts: int, passes: int, k: int) -> float:
    """The unbiased estimate of passing at least once in k attempts, from n attempts with c passes: 1 - C(n-c, k) / C(n, k)."""
    if k > attempts or attempts == 0:
        raise ValueError("k must be between 1 and the number of attempts")
    if attempts - passes < k:
        return 1.0
    return 1.0 - math.comb(attempts - passes, k) / math.comb(attempts, k)
