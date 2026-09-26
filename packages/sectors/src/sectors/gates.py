"""The gates a sector pack passes before the composer offers it: the same ones banking passes.

- complete_spec: every event has roles, phrases in each language, a named operation, and machines whose
  object kinds the pack declares; every sub-domain has milestones; prompts, openings, follow-ups, a goal,
  and agent wording exist in each language.
- legal_sweep: random configurations never break a rule, a repeat limit, or the length bound, and every
  journey classifies to a declared type.
- every_sub_domain: each sub-domain alone, and all together, yield legal journeys that reach its milestones.
- diversity: 64 journeys across every sub-domain hold at least 32 distinct event sequences.
- reachable: every event of the pack occurs in a large run, so every outcome is possible.
- stable: the same seed draws the same bundle.
- agent_ready: groups yield episodes whose operations follow the pack's map, decision points, and every signal.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DIVERSITY_JOURNEYS = 64
DIVERSITY_DISTINCT = 32


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str


def _base(**overrides) -> dict:
    settings = dict(
        language="en", target_trajectory_count=8, event_budget=None, min_events=4, max_events=18, max_assistant_turns=4,
        start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training", target_family="llm",
        seed="gates", group_size=1,
    )
    settings.update(overrides)
    return settings


def _paths(bundle, primaries_only: bool = True) -> list[list[str]]:
    events = {event.event_id: event.event_type for event in bundle.events}
    return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories if not primaries_only or trajectory.parent_trajectory_id is None]


def complete_spec(sector) -> Gate:
    from sectors.episodes import BIAN

    pack, lifecycle = sector.pack, sector.lifecycle
    problems = []
    if set(sector.sub_domains) != set(lifecycle.sub_domains):
        problems.append("the sector's sub-domains differ from its machines'")
    if not set(sector.default_sub_domains) <= set(sector.sub_domains):
        problems.append("default sub-domains outside the sector")
    kinds = set(lifecycle.object_types)
    operations = pack.operations or BIAN
    for spec in lifecycle.events:
        name = spec.event_type
        if name not in pack.roles:
            problems.append(f"{name} has no roles")
        for kind, _ in pack.roles.get(name, ()):
            if kind not in pack.objects or kind not in kinds:
                problems.append(f"{name} links an undeclared object kind {kind}")
        for item in (*spec.requires, *spec.sets):
            if item.kind not in kinds:
                problems.append(f"{name} reads or moves an undeclared object kind {item.kind}")
        for lang in sector.languages:
            if name not in pack.phrases.get(lang, {}):
                problems.append(f"{name} has no {lang} phrase")
        if name not in operations:
            problems.append(f"{name} has no named operation")
    for domain in sector.sub_domains:
        milestones = lifecycle.milestones.get(domain, ())
        if not milestones or not set(milestones) <= set(lifecycle.namespace):
            problems.append(f"{domain} has no milestones in the pack")
    for lang in sector.languages:
        wording = pack.agent.get(lang) or {}
        checks = {
            "prompts": bool(pack.prompts.get(lang)),
            "openings": bool((pack.openings.get(lang) or {}).get("*")),
            "follow-ups": bool(pack.follow_ups.get(lang)),
            "a goal": bool(pack.goal.get(lang)),
            "agent wording": "{party}" in wording.get("task", "") and "{situation}" in wording.get("task", "") and bool(wording.get("system")),
        }
        problems.extend(f"no {lang} {what}" for what, present in checks.items() if not present)
    return Gate("complete_spec", not problems, "; ".join(problems[:6]) or f"{len(lifecycle.events)} events, {len(sector.sub_domains)} sub-domains")


def legal_sweep(sector, trials: int = 150) -> Gate:
    rng = random.Random(f"sweep-{sector.id}")
    lifecycle, pack = sector.lifecycle, sector.pack
    for trial in range(trials):
        names = list(sector.sub_domains)
        low = rng.randint(1, 12)
        high = rng.randint(low, 30)
        bundle = sector.generate(**_base(
            sub_domains=rng.sample(names, rng.randint(1, len(names))),
            language=rng.choice(list(sector.languages)),
            target_trajectory_count=rng.randint(1, 10),
            event_budget=rng.choice([None, rng.randint(5, 300)]),
            min_events=low,
            max_events=high,
            max_assistant_turns=rng.randint(1, 6),
            start_mode=rng.choice(["cold", "warm"]),
            corpus_text=rng.choice(["", " ".join(rng.sample(list(lifecycle.namespace), 3)) + " on mobile in USD."]),
            seed=f"{sector.id}-{trial}",
            group_size=rng.choice([1, 1, 2, 4, 8]),
        ))
        errors = sector.hard_checks(bundle)
        if errors:
            return Gate("legal_sweep", False, f"trial {trial}: {errors[0]}")
        for trajectory, types in zip(bundle.trajectories, _paths(bundle, primaries_only=False)):
            if len(types) > high:
                return Gate("legal_sweep", False, f"trial {trial}: {trajectory.trajectory_id} is longer than {high} events")
            over = next((name for name in set(types) if types.count(name) > lifecycle[name].repeat), None)
            if over:
                return Gate("legal_sweep", False, f"trial {trial}: {over} repeats beyond its limit")
            if pack.classify(types) not in pack.trajectory_types:
                return Gate("legal_sweep", False, f"trial {trial}: {pack.classify(types)} is not a declared journey type")
    return Gate("legal_sweep", True, f"{trials} random configurations, no rule broken")


def every_sub_domain(sector) -> Gate:
    lifecycle = sector.lifecycle
    for scope in [[domain] for domain in sector.sub_domains] + [list(sector.sub_domains)]:
        bundle = sector.generate(**_base(sub_domains=scope, target_trajectory_count=12, min_events=3, seed="scope|" + "|".join(scope)))
        errors = sector.hard_checks(bundle)
        if errors:
            return Gate("every_sub_domain", False, f"{'+'.join(scope)}: {errors[0]}")
        reached = {name for path in _paths(bundle) for name in path}
        missing = [domain for domain in scope if not set(lifecycle.milestones.get(domain, ())) & reached]
        if missing:
            return Gate("every_sub_domain", False, f"{'+'.join(scope)} never reaches a milestone of {', '.join(missing)}")
    return Gate("every_sub_domain", True, f"{len(sector.sub_domains)} sub-domains alone and together")


def diversity(sector) -> Gate:
    bundle = sector.generate(**_base(sub_domains=list(sector.sub_domains), target_trajectory_count=DIVERSITY_JOURNEYS, min_events=6, max_events=24, seed="diversity"))
    distinct = len({tuple(path) for path in _paths(bundle)})
    return Gate("diversity", distinct >= DIVERSITY_DISTINCT, f"{distinct} distinct sequences in {DIVERSITY_JOURNEYS} journeys")


def reachable(sector) -> Gate:
    bundle = sector.generate(**_base(
        sub_domains=list(sector.sub_domains), target_trajectory_count=200, min_events=4, max_events=30, group_size=4, seed="reachable", materialization_cap=800,
    ))
    seen = {name for path in _paths(bundle, primaries_only=False) for name in path}
    missing = [name for name in sector.lifecycle.namespace if name not in seen]
    return Gate("reachable", not missing, f"never drawn: {', '.join(missing)}" if missing else f"all {len(seen)} events drawn")


def stable(sector) -> Gate:
    first = sector.generate(**_base(sub_domains=list(sector.sub_domains), seed="stable")).model_dump(mode="json")
    second = sector.generate(**_base(sub_domains=list(sector.sub_domains), seed="stable")).model_dump(mode="json")
    return Gate("stable", first == second, "the same seed draws the same bundle" if first == second else "the same seed drew different bundles")


def agent_ready(sector) -> Gate:
    from sectors.episodes import tool_name
    from sectors.scorers import SIGNALS

    bundle = sector.generate(**_base(
        sub_domains=list(sector.sub_domains), target_trajectory_count=16, min_events=6, max_events=24, group_size=4, seed="agent",
        episodes=True, decisions=True, signal_mechanism="decision_score",
    ))
    if not bundle.episodes:
        return Gate("agent_ready", False, "no group parted at a decision, so no episode was built")
    if not bundle.decisions:
        return Gate("agent_ready", False, "no outcome decision was recorded")
    expected = {tool_name(event, sector.pack.operations)[0] for episode in bundle.episodes for event in episode.skeleton["legal_events"]}
    named = {tool.name for episode in bundle.episodes for tool in episode.tools}
    if not expected <= named:
        return Gate("agent_ready", False, f"episode operations miss {', '.join(sorted(expected - named))}")
    verdicts = bundle.samples[0].sequences[0].signals or {}
    if set(verdicts) != set(SIGNALS):
        return Gate("agent_ready", False, f"signals scored: {', '.join(sorted(verdicts))}")
    return Gate("agent_ready", True, f"{len(bundle.episodes)} episodes, {len(bundle.decisions)} decisions, five signals")


GATES = (complete_spec, legal_sweep, every_sub_domain, diversity, reachable, stable, agent_ready)


def run_gates(sector) -> list[Gate]:
    return [gate(sector) for gate in GATES]
