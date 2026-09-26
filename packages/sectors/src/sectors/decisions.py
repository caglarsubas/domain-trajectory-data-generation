"""Decision records: the outcome decisions in a journey, for decision-scoring and Jev-type models.

An outcome decision is a step where the pack's machines left more than one outcome of the same group
open, such as approving or declining an application; a journey records the first decision of each group. Each one is recorded with what a model would
need and nothing it cannot use: the machine states, facts derived from the history (counts, days,
money) so a model that does no arithmetic or date reasoning need not, the options with the generator
policy's share of each, and each option's value, the share of simulated continuations under the same
policy that reach the pack's goal. Every counterfactual is relative to that policy, never causal.

Exported records turn a decision into typed questions: a choice among the outcomes, true or false on
whether an outcome is allowed, and a score for each outcome, each with explicit criteria, an abstain
answer, and variants with keys reordered and the question paraphrased.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from trajectory_contract.models import DecisionOption, DecisionPoint, DecisionRecord, TrajectoryBundle

from sectors.lifecycle import Step, Walker, apply, satisfied

VALUE_SAMPLES = 64
HISTORY_SHOWN = 12

QUESTIONS = {
    "en": {
        "choice": ("The case has reached the {group} decision. Which outcome should be recorded?", "Given these facts, what should the {group} outcome be?"),
        "true_false": ("Is “{label}” allowed in the case's current state?", "Do the case's current states permit “{label}”?"),
        "score": (
            "If “{label}” is recorded now, how likely is the journey to reach its goal? Answer from 0 to 1.",
            "Score from 0 to 1 the chance that the journey reaches its goal after “{label}”.",
        ),
    },
    "tr": {
        "choice": ("Vaka bir {group} kararına geldi. Hangi sonuç kaydedilmeli?", "Bu olgulara göre {group} sonucu ne olmalı?"),
        "true_false": ("Vakanın mevcut durumunda “{label}” adımına izin var mı?", "Vakanın mevcut durumları “{label}” adımına izin veriyor mu?"),
        "score": (
            "“{label}” şimdi kaydedilirse yolculuğun hedefine ulaşma olasılığı nedir? 0 ile 1 arasında yanıtlayın.",
            "“{label}” sonrasında yolculuğun hedefine ulaşma şansını 0 ile 1 arasında puanlayın.",
        ),
    },
}
WORDS = {
    "en": {
        "abstain": "Abstain: the facts do not settle it.",
        "true": "True",
        "false": "False",
        "needs": "“{label}” needs {requires}.",
        "free": "“{label}” has no precondition.",
        "policy": "Target shares are the generator's policy at this point, not observed rates.",
        "goal": "Goal: {goal}",
        "value": "The target is the share of {n} simulated continuations under the generator's policy that reach the goal.",
        "allowed": "Every precondition holds.",
        "headings": ("State", "Facts", "History", "Criteria", "Options"),
        "answer_option": "Answer with one option id.",
        "answer_score": "Answer with a number from 0 to 1, or abstain.",
    },
    "tr": {
        "abstain": "Çekimser: olgular bunu belirlemiyor.",
        "true": "Doğru",
        "false": "Yanlış",
        "needs": "“{label}” için gereken: {requires}.",
        "free": "“{label}” için ön koşul yok.",
        "policy": "Hedef paylar üreticinin bu noktadaki politikasıdır, gözlenmiş oranlar değildir.",
        "goal": "Hedef: {goal}",
        "value": "Hedef, üreticinin politikasıyla simüle edilen {n} devamın hedefe ulaşan payıdır.",
        "allowed": "Tüm ön koşullar sağlanıyor.",
        "headings": ("Durum", "Olgular", "Geçmiş", "Ölçütler", "Seçenekler"),
        "answer_option": "Tek bir seçenek kimliğiyle yanıtlayın.",
        "answer_score": "0 ile 1 arasında bir sayıyla ya da abstain ile yanıtlayın.",
    },
}
# Decision records use their own split names; they follow the split of the sample they come from.
SPLITS = {"train": "train", "validation": "calibration", "test": "held_out", "heldout": "held_out"}


def requires(spec) -> list[str]:
    return [f"{guard.kind}.{guard.dimension} in {{{', '.join(state or 'unset' for state in guard.states)}}}" for guard in spec.requires]


def _unmet(spec, state: dict) -> str | None:
    guard = next((item for item in spec.requires if state.get((item.kind, item.dimension)) not in item.states), None)
    if guard is None:
        return None
    return f"{guard.kind}.{guard.dimension} is {state.get((guard.kind, guard.dimension)) or 'unset'}"


def _facts(prefix: list, counts: dict[str, int]) -> dict[str, Any]:
    """What a model should not have to compute: how long, how often, and how much money moved."""
    first, last = prefix[0].event_time, prefix[-1].event_time
    facts: dict[str, Any] = {
        "events_so_far": len(prefix),
        "days_since_start": round((last - first).total_seconds() / 86400, 2),
        "days_since_previous_step": round((last - prefix[-2].event_time).total_seconds() / 86400, 2) if len(prefix) > 1 else 0.0,
        "last_event": prefix[-1].event_type,
    }
    moved = [event for event in prefix if event.amount is not None and event.direction]
    if moved:
        credited = sum(event.amount for event in moved if event.direction == "credit")
        debited = sum(event.amount for event in moved if event.direction == "debit")
        facts.update({"credited": round(credited, 2), "debited": round(debited, 2), "net": round(credited - debited, 2), "currency": moved[0].currency})
    for name in sorted(counts):
        facts[f"times.{name}"] = counts[name]
    return facts


# Values per policy, shared by every batch of a run: the same context gets the same target wherever it occurs.
_CACHE: dict[str, dict[str, float]] = {}
CACHE_POLICIES = 8
CACHE_ENTRIES = 200_000


def _policy(walker: Walker, floor: int, cap: int) -> str:
    """Everything a continuation's distribution depends on besides the context itself."""
    found = walker.calibration
    # Next-step shares read only the starts and transitions, so those identify a calibrated policy.
    calibration = None if found is None else (sorted(found.starts.items()), sorted((event, sorted(following.items())) for event, following in found.transitions.items()))
    raw = repr((walker.allowed, sorted(walker.weights.items()), walker.kept, walker.goals, floor, cap, calibration))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class _Values:
    """Each option's value by simulation, cached on the policy and the context, so repeated contexts cost nothing."""

    def __init__(self, pack, walker: Walker, *, floor: int, cap: int) -> None:
        self.pack, self.walker, self.floor, self.cap = pack, walker, floor, cap
        self.policy = _policy(walker, floor, cap)
        if self.policy not in _CACHE and len(_CACHE) >= CACHE_POLICIES:
            _CACHE.pop(next(iter(_CACHE)))
        self.cache = _CACHE.setdefault(self.policy, {})
        if len(self.cache) > CACHE_ENTRIES:
            self.cache.clear()

    def __call__(self, types: list[str], state: dict, counts: dict[str, int]) -> float:
        name = types[-1]
        key = f"{self.policy}|{len(types)}|{name}|{sorted(state.items())}|{sorted(counts.items())}"
        if key in self.cache:
            return self.cache[key]
        if self.pack.lifecycle[name].ends_journey:
            value = float(self.pack.success(types))
        else:
            # Seeded by the policy and the context alone, so a value does not depend on which batch asked first.
            rng = random.Random(key)
            prefix = [Step(item, (), {}, {}) for item in types]
            reached = 0
            for _ in range(VALUE_SAMPLES):
                walked = self.walker.walk(rng, floor=self.floor, cap=self.cap, state=state, counts=counts, prefix=prefix)
                reached += self.pack.success(walked.types)
            value = reached / VALUE_SAMPLES
        self.cache[key] = round(value, 4)
        return self.cache[key]


def build_decisions(pack, bundle: TrajectoryBundle, walker: Walker, *, floor: int, cap: int, language: str = "en") -> list[DecisionPoint]:
    """Every outcome decision in each prompt's journey, with the policy's shares and each option's simulated value."""
    lang = language.split("-")[0] if language.split("-")[0] in QUESTIONS else "en"
    lifecycle = pack.lifecycle
    phrases = pack.phrases.get(lang) or pack.phrases["en"]
    goal = pack.goal.get(lang) or pack.goal.get("en") or "The journey reaches the pack's success condition."
    events = {event.event_id: event for event in bundle.events}
    types_of = {obj.object_id: obj.object_type for obj in bundle.objects}
    kinds = {object_type: kind for kind, object_type in lifecycle.object_types.items()}
    linked: dict[str, dict[str, str]] = {}
    for link in bundle.event_objects:
        kind = kinds.get(types_of.get(link.object_id, ""))
        if kind:
            linked.setdefault(link.event_id, {}).setdefault(kind, link.object_id)
    sample_of = {}
    for sample in bundle.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id:
                sample_of.setdefault(sequence.trajectory_id, sample.sample_id)
    values = _Values(pack, walker, floor=floor, cap=cap)
    points = []
    for trajectory in bundle.trajectories:
        if trajectory.parent_trajectory_id is not None:
            continue
        journey = [events[item] for item in trajectory.event_ids if item in events]
        for index, state, counts, rivals in decision_steps(lifecycle, walker, [event.event_type for event in journey]):
            objects: dict[str, str] = {}
            for event in journey[:index]:
                for kind, object_id in linked.get(event.event_id, {}).items():
                    objects.setdefault(kind, object_id)
            points.append(_point(pack, trajectory, journey, index, state, counts, objects, rivals, values, phrases, goal, sample_of, lang))
    return points


def decision_steps(lifecycle, walker: Walker, types: list[str]):
    """The first outcome decision of each group along a path: where, the state and counts before it, and the rivals with their policy weights.

    A repeated decision, such as a monthly repayment, differs from the first only in its counts.
    """
    state: dict = {}
    counts: dict[str, int] = {}
    decided: set[str] = set()
    for index, name in enumerate(types):
        spec = lifecycle.get(name)
        if spec is None:
            return
        if index and spec.outcome and spec.outcome not in decided:
            offered = walker.options(state, counts)
            if walker.calibration is not None:
                offered = walker.calibration.reweight(types[index - 1], offered)
            rivals = [(option, weight) for option, weight in offered if lifecycle[option].outcome == spec.outcome]
            if len(rivals) > 1 and name in dict(rivals):
                decided.add(spec.outcome)
                yield index, dict(state), dict(counts), rivals
        apply(spec, state)
        counts[name] = counts.get(name, 0) + 1


def _point(pack, trajectory, journey, index, state, counts, objects, rivals, values, phrases, goal, sample_of, lang) -> DecisionPoint:
    lifecycle = pack.lifecycle
    event = journey[index]
    types = [item.event_type for item in journey]
    total = sum(weight for _, weight in rivals)
    options = []
    for name, weight in rivals:
        after, tally = dict(state), dict(counts)
        apply(lifecycle[name], after)
        tally[name] = tally.get(name, 0) + 1
        options.append(
            DecisionOption(
                option_id=name,
                event_type=name,
                label=phrases.get(name, name),
                probability=round(weight / total, 4),
                value=values(types[:index] + [name], after, tally),
                requires=requires(lifecycle[name]),
            )
        )
    subject = lifecycle.kind_of(event.event_type)
    decision_id = f"{trajectory.trajectory_id}.D{index:02d}"
    # A distractor for the false answer: an outcome whose preconditions the state does not meet, about the same object when one exists.
    legal = {name for name, _ in rivals}
    blocked = [spec for spec in lifecycle.events if spec.event_type not in legal and spec.requires and not satisfied(spec, state)]
    close = [spec for spec in blocked if lifecycle.kind_of(spec.event_type) == subject] or blocked
    distractor = None
    if close:
        spec = random.Random(decision_id).choice(close)
        distractor = {"event_type": spec.event_type, "label": phrases.get(spec.event_type, spec.event_type), "requires": requires(spec), "unmet": _unmet(spec, state)}
    return DecisionPoint(
        decision_id=decision_id,
        trajectory_id=trajectory.trajectory_id,
        sample_id=sample_of.get(trajectory.trajectory_id),
        decision_index=index,
        decision_event_id=event.event_id,
        group=lifecycle[event.event_type].outcome,
        subject=subject,
        state={f"{kind}.{dimension}": value for (kind, dimension), value in sorted(state.items())},
        facts=_facts(journey[:index], counts),
        history=types[:index],
        objects=dict(objects),
        options=options,
        distractor=distractor,
        taken=event.event_type,
        outcome={
            "taken": event.event_type,
            "journey_success": bool(pack.success(types)),
            "journey_type": trajectory.trajectory_type,
            "final_event": types[-1],
            "steps_after": len(types) - index - 1,
        },
        value_samples=VALUE_SAMPLES,
        goal=goal,
    )


def summarize(points: list[DecisionPoint]) -> dict:
    groups: dict[str, int] = {}
    for point in points:
        groups[point.group] = groups.get(point.group, 0) + 1
    taken = [next(option.value for option in point.options if option.option_id == point.taken) for point in points]
    return {
        "points": len(points),
        "groups": dict(sorted(groups.items())),
        "with_distractor": sum(1 for point in points if point.distractor),
        "records": sum(record_count(point) for point in points),
        "value_samples": VALUE_SAMPLES,
        "mean_taken_value": round(sum(taken) / len(taken), 4) if taken else None,
        "counterfactual_basis": "generator_policy",
    }


def record_count(point: DecisionPoint) -> int:
    # A choice, a true and possibly a false, and a score per option, each as original, reordered, and paraphrase.
    return 3 * (1 + 1 + (1 if point.distractor else 0) + len(point.options))


def records(point: DecisionPoint | dict, *, split: str, language: str) -> list[DecisionRecord]:
    """A decision point's typed questions, each with its reordered and paraphrased variants."""
    point = point if isinstance(point, DecisionPoint) else DecisionPoint.model_validate(point)
    lang = language.split("-")[0] if language.split("-")[0] in QUESTIONS else "en"
    words = WORDS[lang]
    decision_split = SPLITS.get(split, split)
    group = point.group.replace("_", " ")
    history = point.history[-HISTORY_SHOWN:]

    def need(option_label: str, needs: list[str]) -> str:
        return words["needs"].format(label=option_label, requires=" and ".join(needs)) if needs else words["free"].format(label=option_label)

    base: list[dict] = []
    base.append({
        "suffix": "C",
        "question_type": "choice",
        "question": QUESTIONS[lang]["choice"],
        "fill": {"group": group},
        "criteria": [need(option.label, option.requires) for option in point.options] + [words["policy"]],
        "options": [{"option_id": option.option_id, "label": option.label} for option in point.options],
        "target_distribution": {option.option_id: option.probability for option in point.options},
        "target_basis": "generator_policy_share",
        "rationale": None,
    })
    taken = next(option for option in point.options if option.option_id == point.taken)
    truths = [(taken.label, taken.requires, True, words["allowed"])]
    if point.distractor:
        truths.append((point.distractor["label"], point.distractor["requires"], False, point.distractor.get("unmet")))
    for label, needs, allowed, rationale in truths:
        base.append({
            "suffix": "T1" if allowed else "T0",
            "question_type": "true_false",
            "question": QUESTIONS[lang]["true_false"],
            "fill": {"label": label},
            "criteria": [need(label, needs)],
            "options": [{"option_id": "true", "label": words["true"]}, {"option_id": "false", "label": words["false"]}],
            "target_distribution": {"true": 1.0 if allowed else 0.0, "false": 0.0 if allowed else 1.0},
            "target_basis": "machine_rules",
            "rationale": rationale,
        })
    for number, option in enumerate(point.options, start=1):
        base.append({
            "suffix": f"S{number}",
            "question_type": "score",
            "question": QUESTIONS[lang]["score"],
            "fill": {"label": option.label},
            "criteria": [words["goal"].format(goal=point.goal), words["value"].format(n=point.value_samples)],
            "options": [],
            "target_score": option.value,
            "target_basis": "simulated_goal_share",
            "rationale": None,
        })

    found: list[DecisionRecord] = []
    for item in base:
        record_id = f"{point.decision_id}.{item['suffix']}"
        for variant in ("original", "reordered", "paraphrase"):
            state, facts, options = dict(point.state), dict(point.facts), list(item["options"])
            if variant == "reordered":
                rng = random.Random(record_id)
                state = dict(rng.sample(list(state.items()), len(state)))
                facts = dict(rng.sample(list(facts.items()), len(facts)))
                options = rng.sample(options, len(options))
            question = item["question"][1 if variant == "paraphrase" else 0].format(**item["fill"])
            found.append(
                DecisionRecord(
                    record_id=record_id if variant == "original" else f"{record_id}~{variant[0]}",
                    decision_id=point.decision_id,
                    trajectory_id=point.trajectory_id,
                    sample_id=point.sample_id,
                    language=lang,
                    split=decision_split,
                    variant=variant,
                    variant_of=None if variant == "original" else record_id,
                    question_type=item["question_type"],
                    question=question,
                    criteria=item["criteria"],
                    options=options,
                    abstain={"option_id": "abstain", "label": words["abstain"]},
                    state=state,
                    facts=facts,
                    history=history,
                    target_distribution=item.get("target_distribution"),
                    target_score=item.get("target_score"),
                    target_basis=item["target_basis"],
                    rationale=item["rationale"],
                    outcome=point.outcome,
                    prompt=_prompt(words, question, state, facts, history, item["criteria"], options, item["question_type"]),
                )
            )
    return found


def _prompt(words: dict, question: str, state: dict, facts: dict, history: list[str], criteria: list[str], options: list[dict], kind: str) -> str:
    state_h, facts_h, history_h, criteria_h, options_h = words["headings"]
    lines = [
        question,
        f"{state_h}: " + "; ".join(f"{key} = {value}" for key, value in state.items()),
        f"{facts_h}: " + "; ".join(f"{key} = {value}" for key, value in facts.items()),
        f"{history_h}: " + ", ".join(history),
        f"{criteria_h}: " + " ".join(criteria),
    ]
    if kind != "score":
        lines.append(f"{options_h}: " + "; ".join(f"{option['option_id']} ({option['label']})" for option in options) + f"; abstain ({words['abstain']})")
        lines.append(words["answer_option"])
    else:
        lines.append(words["answer_score"])
    return "\n".join(lines)
