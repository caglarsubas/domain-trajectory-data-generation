"""What the judge sees, whom it asks, and how the verdicts become a cycle.

Each sampled journey is rendered with its amounts, state changes, and the sample's text, within a prompt
budget. Every rubric is asked of each judge model, and pairwise quality in both orders, so the cycle can
report agreement between models and position bias. Each call can be repeated above temperature 0, so the
cycle also reports how far each judge agrees with itself. A copy of one journey with its events put out of
order, which the pack's own replay rejects, checks that the judge can tell a broken journey at all.

The judge also scores process conformance and the decision score, which code already scores for every
journey; the cycle reports how often judge and code agree, and those two never decide acceptance.
"""

from __future__ import annotations

from copy import deepcopy
from statistics import mean

from trajectory_contract.models import TrajectoryBundle

from app.judge import Judge
from app.controls import pairwise_pick
from app.judge_rubrics import JUDGE_PASS, QUESTIONS

UNREADABLE = "_unreadable"

DEFAULT_THRESHOLDS = {
    "helpfulness": 3.0,
    "correctness": 0.5,
    "safety": 1.0,
    "pairwise_quality": 0.5,
}

# The judge's own rubrics, asked of every sampled journey.
RUBRICS = ("helpfulness", "correctness", "safety", "pairwise_quality")
# The rubrics that decide acceptance and write revision notes. Pairwise compares a journey with its own
# alternative branch; both replay legally, so an unbiased judge sits near 0.5 and a 0.5 bar is a coin flip.
# It is scored and reported, and decides nothing.
GATING = ("helpfulness", "correctness", "safety")

# Rubrics the judge scores alongside a code scorer, to measure the judge; they never decide acceptance.
COMPARED = tuple(JUDGE_PASS)
# The code verdicts a cycle keeps for each sampled journey: the two above, and the solution and behavior rubrics that
# a study's own rubrics are compared with.
CODE_SIGNALS = (*COMPARED, "solution_rubric", "behavior_rubric")
# A study rubric's verdict passes at the middle of its scale.
STUDY_PASS = 0.5

# The studio's questions name what to look for, so a judge checks for the defects its controls carry.
HELPFULNESS_QUESTION = (
    "Score how representative this {label} journey is. A skipped step, events out of order, or a wait far longer than "
    "such a step takes makes it less representative; a journey that plainly ends in a failure is as representative as "
    "one that succeeds."
)
CORRECTNESS_QUESTION = (
    "Do these events follow the {label} transitions in the reference, in order? A step that comes before what it "
    "depends on, or after a step it needs was skipped, means they do not."
)

# Helpfulness is scored 1 to 5; the others 0 to 1. Normalized scores divide by this.
SCALE = {"helpfulness": 5.0}

# Rough characters per token, to keep a prompt inside the budget without a tokenizer.
CHARS_PER_TOKEN = 4


# A journey and its alternative branch often end differently on purpose: group rewards need failures.
# Asked only which is "more plausible", judges preferred whichever succeeded, so the question rules that out.
PAIRWISE_QUESTION = (
    "Compare the two journeys as things that could happen to the same customer. Either may end in success or in "
    "failure: an abandoned order, a declined application, a failed check, or a missed payment is as valid an "
    "outcome as a completed one. Prefer the journey whose steps, order, and timing are more realistic for this "
    "sector. Do not prefer a journey because it succeeds or because it has more events. If both are equally "
    "realistic, answer tie."
)


def normalized(rubric: str, score: float | None) -> float | None:
    return None if score is None else round(score / SCALE.get(rubric, 1.0), 4)


def _money(event) -> str:
    if event.amount is None:
        return ""
    parts = [f"{event.amount:,.2f}", event.currency or "", event.direction or ""]
    text = " ".join(part for part in parts if part)
    return f"{text} ({event.amount_role})" if event.amount_role else text


def outcome_of(bundle: TrajectoryBundle, trajectory_id: str) -> str | None:
    for sample in bundle.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id == trajectory_id and sequence.outcome:
                return sequence.outcome
    return None


def code_signals(bundle: TrajectoryBundle, trajectory_id: str) -> dict[str, dict]:
    """The code scorers' verdicts on a journey, for the signals the judge can also score."""
    for sample in bundle.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id == trajectory_id and sequence.signals:
                return {
                    name: {"score": float(found["score"]), "passed": bool(found["passed"]), **({"reference": found["reference"]} if found.get("reference") else {})}
                    for name, found in sequence.signals.items()
                    if name in CODE_SIGNALS
                }
    return {}


def reference_shares(reference: list[dict] | None) -> str:
    """The reference process for one journey: where it could go more than one way, each next step's share."""
    if not reference:
        return ""
    lines = ["Reference next steps: at each step where the process could go more than one way, the share of journeys taking each option."]
    for point in reference:
        after = f"after {point['after']}" if point.get("after") else "at the start"
        options = ", ".join(f"{name} {share:.0%}" for name, share in point["shares"].items())
        lines.append(f"- Step {point['step']}, {after}: {options}.")
    return "\n".join(lines)


def render_journey(bundle: TrajectoryBundle, trajectory_id: str, budget_chars: int = 24_000, blind: bool = False) -> tuple[str, bool]:
    """The journey as the judge reads it, and whether it had to be shortened to fit the budget.

    Blind leaves out the journey's id, kind, outcome, and whether it is an alternative, so a pairwise judge
    compares the events themselves and cannot pick the journey that says it succeeded.
    """
    traj = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    events = {event.event_id: event for event in bundle.events}
    links: dict[str, list[str]] = {}
    for link in bundle.event_objects:
        links.setdefault(link.event_id, []).append(f"{link.object_id} as {link.object_role}")
    changes: dict[str, list[str]] = {}
    for change in bundle.state_transitions:
        changes.setdefault(change.event_id, []).append(
            f"{change.object_id}.{change.state_dimension}: {change.state_before or 'none'} -> {change.state_after or 'none'}"
        )
    header = [f"Journey {traj.trajectory_id}, {traj.trajectory_type}, {traj.observed_or_synthetic}."] if not blind else ["Journey."]
    if traj.parent_trajectory_id and not blind:
        branch = events.get(traj.branch_event_id or "")
        header.append(f"A simulated alternative of {traj.parent_trajectory_id}, branching after {branch.event_type if branch else 'its start'}.")
    outcome = outcome_of(bundle, trajectory_id)
    if outcome and not blind:
        header.append("Outcome: it reached its goal." if outcome == "pass" else "Outcome: it did not reach its goal.")
    used = {link.object_id for link in bundle.event_objects if link.event_id in set(traj.event_ids)}
    objects = [
        f"- {obj.object_id}: {obj.object_type}" + (f" {obj.attributes}" if obj.attributes else "")
        for obj in bundle.objects
        if obj.object_id in used
    ]
    lines = []
    for index, event_id in enumerate(traj.event_ids, start=1):
        event = events.get(event_id)
        if event is None:
            continue
        facts = [f"{index}. {event.event_time:%Y-%m-%d %H:%M %a} {event.event_type}"]
        if event.channel_id:
            facts.append(f"channel {event.channel_id}")
        if event.amount is not None:
            facts.append(_money(event))
        if event.status:
            facts.append(f"status {event.status}")
        line = ", ".join(facts)
        if links.get(event_id):
            line += "; objects: " + ", ".join(links[event_id])
        if changes.get(event_id):
            line += "; state: " + "; ".join(changes[event_id])
        lines.append(line)
    turns = []
    for sample in bundle.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id != trajectory_id:
                continue
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.role in {"user", "assistant"}:
                        turns.append(f"{segment.role}: {segment.text}")
            break

    def assemble(event_lines: list[str], turn_lines: list[str]) -> str:
        parts = ["\n".join(header)]
        if objects:
            parts.append("Objects:\n" + "\n".join(objects))
        parts.append("Events:\n" + "\n".join(event_lines))
        if turn_lines:
            parts.append("Sample text:\n" + "\n".join(turn_lines))
        return "\n\n".join(parts)

    text = assemble(lines, turns)
    if len(text) <= budget_chars:
        return text, False
    # Shorten the sample text first, then the middle of the event list; the header and state stay.
    kept = list(turns)
    while kept and len(assemble(lines, kept + ["..."])) > budget_chars:
        kept.pop()
    if kept != turns:
        kept.append(f"... {len(turns) - len(kept)} more turns omitted ...")
    text = assemble(lines, kept)
    head, tail = 12, 8
    while len(text) > budget_chars and head + tail < len(lines) and head > 2:
        omitted = len(lines) - head - tail
        text = assemble(lines[:head] + [f"... {omitted} events omitted ..."] + lines[-tail:], kept)
        head, tail = head - 2, max(tail - 1, 2)
    return text[:budget_chars], True


def broken_copy(bundle: TrajectoryBundle, trajectory_id: str) -> TrajectoryBundle | None:
    """The journey with its events in reverse order, for the control call. None when there are too few to reverse."""
    traj = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    if len(traj.event_ids) < 3:
        return None
    copy = deepcopy(bundle)
    target = next(item for item in copy.trajectories if item.trajectory_id == trajectory_id)
    times = sorted(event.event_time for event in copy.events if event.event_id in set(target.event_ids))
    target.event_ids = list(reversed(target.event_ids))
    by_id = {event.event_id: event for event in copy.events}
    for event_id, when in zip(target.event_ids, times):
        by_id[event_id].event_time = when
    copy.trajectories = [target]
    return copy


def evaluate_journeys(
    *,
    journeys: list[TrajectoryBundle],
    sector,
    brief: str,
    reference_quality: str,
    thresholds: dict[str, float],
    judge: Judge,
    models: list[str],
    budget_tokens: int = 8000,
    progress=None,
    repeats: int = 1,
    temperature: float = 0.0,
    compared: dict[str, str] | None = None,
    study: dict[str, dict] | None = None,
    controls: list[dict] | None = None,
) -> dict:
    """Judge a sample of journeys with every model and turn the verdicts into a cycle.

    `compared` names the code-comparison rubrics the engine has registered, with their digests; each is asked only
    of journeys the code scored with it. `study` names the study's approved rubrics as registered, with their kind,
    title, digest, and the code signal each is compared with; each is asked of every journey. `controls` are copies of
    sampled journeys with one known defect (`app.controls`): each is asked the rubrics its defect should lower, and
    one is set against its original as a pairwise question with a right answer.
    """
    compared = compared or {}
    study = study or {}
    errors = [f"{error}" for journey in journeys for error in sector.hard_checks(journey)]
    base = {"reference_quality": reference_quality, "models": models, "sample": [], "scores": {}, "agreement": {}, "flags": [], "canary": None}
    if errors:
        return {**base, "hard_check_passed": False, "hard_check_errors": errors, "accepted": False, "revision_notes": errors, "verdicts": [], "called_judge": False}
    budget_chars = max(budget_tokens * CHARS_PER_TOKEN - len(brief) - 400, 2000)
    label = sector.label.lower()
    calls: list[dict] = []
    sample = []

    def payload(rubric: str, text: str, reference: list[dict] | None = None) -> dict:
        if rubric == "helpfulness":
            return {"prompt": brief + "\n" + HELPFULNESS_QUESTION.format(label=label), "response": text}
        if rubric == "correctness":
            return {"prompt": CORRECTNESS_QUESTION.format(label=label), "response": text, "expected": brief}
        if rubric == "safety":
            return {"prompt": "Confirm the journey is synthetic and holds no real personal or account identifiers.", "response": text}
        question = brief + "\n" + QUESTIONS[rubric].format(label=label)
        shares = reference_shares(reference)
        return {"prompt": question + ("\n" + shares if shares else ""), "response": text}

    for journey in journeys:
        primary = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
        alternative = next((item for item in journey.trajectories if item.parent_trajectory_id == primary.trajectory_id), None)
        text, truncated = render_journey(journey, primary.trajectory_id, budget_chars if alternative is None else budget_chars // 2)
        signals = code_signals(journey, primary.trajectory_id)
        sample.append(
            {
                "trajectory_id": primary.trajectory_id,
                "trajectory_type": primary.trajectory_type,
                "outcome": outcome_of(journey, primary.trajectory_id),
                "events": len(primary.event_ids),
                "truncated": truncated,
                "alternative": alternative.trajectory_id if alternative else None,
                "code": {name: {"score": found["score"], "passed": found["passed"]} for name, found in signals.items()},
            }
        )
        own = {"trajectory_id": primary.trajectory_id, "order": None, "canary": False}
        for rubric in ("helpfulness", "correctness", "safety"):
            calls.append({**own, "rubric": rubric, "payload": payload(rubric, text)})
        for rubric in COMPARED:
            if rubric in compared and rubric in sample[-1]["code"]:
                calls.append({**own, "rubric": rubric, "payload": payload(rubric, text, signals[rubric].get("reference"))})
        for name, info in study.items():
            question = brief + f"\nScore this {label} journey against the study's {info['kind']} rubric."
            calls.append({**own, "rubric": name, "payload": {"prompt": question, "response": text}})
        if alternative is not None:
            mine, _ = render_journey(journey, primary.trajectory_id, budget_chars // 2, blind=True)
            other, cut = render_journey(journey, alternative.trajectory_id, budget_chars // 2, blind=True)
            sample[-1]["truncated"] = truncated or cut
            question = brief + "\n" + PAIRWISE_QUESTION
            calls.append({**own, "rubric": "pairwise_quality", "order": "ab", "payload": {"prompt": question, "response": mine, "response_b": other}})
            calls.append({**own, "rubric": "pairwise_quality", "order": "ba", "payload": {"prompt": question, "response": other, "response_b": mine}})
    canary = None
    for journey, entry in zip(journeys, sample):
        broken = broken_copy(journey, entry["trajectory_id"])
        if broken is not None and sector.hard_checks(broken):
            text, _ = render_journey(broken, entry["trajectory_id"], budget_chars)
            canary = {"trajectory_id": entry["trajectory_id"], "results": {}}
            calls.append(
                {
                    "trajectory_id": entry["trajectory_id"],
                    "order": None,
                    "canary": True,
                    "rubric": "correctness",
                    "payload": payload("correctness", text),
                }
            )
            break
    by_id = {entry["trajectory_id"]: (journey, entry) for journey, entry in zip(journeys, sample)}
    asked_controls = []
    for control in controls or []:
        journey, entry = by_id[control["trajectory_id"]]
        size = budget_chars if entry["alternative"] is None else budget_chars // 2
        text, _ = render_journey(control["bundle"], control["trajectory_id"], size)
        # A code-comparison rubric is asked of a control only where it was asked of the original.
        rubrics = [rubric for rubric in control["rubrics"] if rubric in {"helpfulness", "correctness"} or (rubric in compared and rubric in entry["code"])]
        if not rubrics:
            continue
        mark = {"trajectory_id": control["trajectory_id"], "order": None, "canary": False, "control": control["kind"]}
        for rubric in rubrics:
            calls.append({**mark, "rubric": rubric, "payload": payload(rubric, text, control.get("reference"))})
        entry.setdefault("controls", []).append({"kind": control["kind"], "detail": control["detail"], "rubrics": rubrics})
        asked_controls.append(control)
    chosen = pairwise_pick(asked_controls)
    if chosen is not None:
        journey, entry = by_id[chosen["trajectory_id"]]
        mine, _ = render_journey(journey, chosen["trajectory_id"], budget_chars // 2, blind=True)
        flawed, _ = render_journey(chosen["bundle"], chosen["trajectory_id"], budget_chars // 2, blind=True)
        question = brief + "\n" + PAIRWISE_QUESTION
        mark = {"trajectory_id": chosen["trajectory_id"], "canary": False, "control": f"pairwise:{chosen['kind']}", "rubric": "pairwise_quality"}
        calls.append({**mark, "order": "ab", "payload": {"prompt": question, "response": mine, "response_b": flawed}})
        calls.append({**mark, "order": "ba", "payload": {"prompt": question, "response": flawed, "response_b": mine}})
        entry.setdefault("controls", []).append({"kind": f"pairwise:{chosen['kind']}", "detail": chosen["detail"], "rubrics": ["pairwise_quality"]})

    planned = [(model, call) for model in models for call in calls]
    verdicts = []
    for index, (model, call) in enumerate(planned):
        if progress is not None:
            what = "a control journey" if call["canary"] else call["rubric"].replace("_", " ")
            if call.get("control"):
                what += f" of a {call['control'].split(':')[-1].replace('_', ' ')} control"
            progress(index, len(planned), f"{model}: {what} ({index + 1} of {len(planned)}).")
        result = judge.run_eval(rubric=call["rubric"], judge_model=model, repeats=repeats, temperature=temperature, **call["payload"])
        # One entry per repeat; a judge that answers once is one repeat.
        answers = result.get("verdicts") or [result]
        for repeat, answer in enumerate(answers):
            readable = answer.get("readable", True)
            parsed = dict(answer.get("parsed") or {})
            if not readable:
                parsed[UNREADABLE] = True
            verdicts.append(
                {
                    "rubric": call["rubric"],
                    "score": float(answer["score"]),
                    "parsed": parsed,
                    "raw": answer.get("raw", ""),
                    "judge_model": model,
                    "served_by": result.get("judge_model") or model,
                    # The call's duration covers every repeat, so it is recorded once.
                    "duration_ms": result.get("duration_ms", 0) if repeat == 0 else 0,
                    "trajectory_id": call["trajectory_id"],
                    "order": call["order"],
                    "canary": call["canary"],
                    "control": call.get("control"),
                    "readable": readable,
                    "repeat": repeat,
                    "rubric_digest": result.get("rubric_digest") or compared.get(call["rubric"]) or study.get(call["rubric"], {}).get("digest"),
                }
            )
    summary = summarize(verdicts, sample, models, thresholds, study=study)
    if canary is not None:
        canary["results"] = {model: _mean_readable([item for item in verdicts if item["canary"] and item["judge_model"] == model]) for model in models}
    return {
        **base,
        **summary,
        "sample": sample,
        "canary": canary,
        "hard_check_passed": True,
        "hard_check_errors": [],
        "verdicts": verdicts,
        "called_judge": bool(planned),
    }


def _justification(verdict: dict) -> str:
    parsed = verdict.get("parsed") or {}
    return str(parsed.get("justification") or parsed.get("reason") or verdict.get("raw") or "").strip()


def _mean_readable(items: list[dict]) -> float | None:
    values = [item["score"] for item in items if item["readable"]]
    return round(mean(values), 4) if values else None


def _side(value: float, minimum: float) -> bool:
    return value >= minimum


def summarize(verdicts: list[dict], sample: list[dict], models: list[str], thresholds: dict[str, float], study: dict[str, dict] | None = None) -> dict:
    """Per-rubric scores per model, agreement between models, across repeats, and with code, order consistency,
    audit flags, and the decision. `study` maps each study rubric's engine name to its kind, title, and code signal."""
    study = study or {}
    primary = models[0]
    minimum = {rubric: float(thresholds.get(rubric, DEFAULT_THRESHOLDS[rubric])) for rubric in RUBRICS}
    minimum.update(JUDGE_PASS)
    minimum.update({name: STUDY_PASS for name in study})
    # Each judge rubric that has a code counterpart, and the code signal it is set against.
    against = {rubric: rubric for rubric in COMPARED} | {name: info["signal"] for name, info in study.items()}
    # Control journeys measure the judge; they are summarized apart and never score the run.
    judged = [item for item in verdicts if not item["canary"] and not item.get("control")]
    # One value per journey, rubric, and model: the mean of its readable repeats. Pairwise combines both orders into
    # the primary journey's preference.
    per: dict[tuple[str, str, str], float | None] = {}
    # Repeats of one call, as the primary journey's value: a journey for most rubrics, one order for pairwise.
    units: dict[tuple[str, str], list[dict]] = {}
    consistency: dict[str, dict] = {model: {"journeys": 0, "consistent": 0} for model in models}
    flags: list[dict] = []

    def unit(tid: str, rubric: str, model: str, values: list[float]) -> None:
        if len(values) >= 2:
            units.setdefault((rubric, model), []).append({"trajectory_id": tid, "values": values})

    for entry in sample:
        tid = entry["trajectory_id"]
        for model in models:
            mine = [item for item in judged if item["trajectory_id"] == tid and item["judge_model"] == model]
            for rubric in ("helpfulness", "correctness", "safety", *COMPARED, *study):
                found = [item for item in mine if item["rubric"] == rubric]
                if not found:
                    continue
                values = [item["score"] for item in found if item["readable"]]
                per[(tid, rubric, model)] = round(mean(values), 4) if values else None
                unit(tid, rubric, model, values)
                if not values:
                    flags.append({"kind": "unreadable", "trajectory_id": tid, "rubric": rubric, "model": model})
            ab = [item for item in mine if item["rubric"] == "pairwise_quality" and item["order"] == "ab"]
            ba = [item for item in mine if item["rubric"] == "pairwise_quality" and item["order"] == "ba"]
            if not ab and not ba:
                continue
            first = [item["score"] for item in ab if item["readable"]]
            # In the reversed order the primary journey is B, so its preference is 1 minus the score.
            second = [1.0 - item["score"] for item in ba if item["readable"]]
            per[(tid, "pairwise_quality", model)] = round(mean(first + second), 4) if first + second else None
            unit(tid, "pairwise_quality", model, first)
            unit(tid, "pairwise_quality", model, second)
            if first and second:
                consistency[model]["journeys"] += 1
                # The same journey should win in both orders: A first, then B.
                same = abs(mean(first) - mean(second)) < 0.01
                consistency[model]["consistent"] += int(same)
                if not same:
                    flags.append({"kind": "order_flip", "trajectory_id": tid, "rubric": "pairwise_quality", "model": model})
            elif (ab and not first) or (ba and not second):
                flags.append({"kind": "unreadable", "trajectory_id": tid, "rubric": "pairwise_quality", "model": model})
    for entry in sample:
        for model in models:
            value = per.get((entry["trajectory_id"], "correctness", model))
            if value is not None and value < minimum["correctness"]:
                # Every sampled journey replays legally through the pack's machines, so a failing verdict is likely the judge's error.
                flags.append({"kind": "likely_false_negative", "trajectory_id": entry["trajectory_id"], "rubric": "correctness", "model": model})
    for model in models:
        control = _mean_readable([item for item in verdicts if item["canary"] and item["judge_model"] == model])
        if control is not None and control >= minimum["correctness"]:
            tid = next(item["trajectory_id"] for item in verdicts if item["canary"])
            flags.append({"kind": "likely_false_positive", "trajectory_id": tid, "rubric": "correctness", "model": model})

    asked = [rubric for rubric in RUBRICS if any(key[1] == rubric for key in per)]
    scores: dict[str, dict[str, float | None]] = {}
    for rubric in asked:
        scores[rubric] = {}
        for model in models:
            values = [value for (tid, name, who), value in per.items() if name == rubric and who == model and value is not None]
            scores[rubric][model] = round(mean(values), 4) if values else None
    agreement: dict[str, dict] = {}
    if len(models) > 1:
        second = models[1]
        for rubric in asked:
            pairs = [
                (per[(entry["trajectory_id"], rubric, primary)], per[(entry["trajectory_id"], rubric, second)])
                for entry in sample
                if per.get((entry["trajectory_id"], rubric, primary)) is not None and per.get((entry["trajectory_id"], rubric, second)) is not None
            ]
            agree = sum(1 for a, b in pairs if (a >= minimum[rubric]) == (b >= minimum[rubric]))
            agreement[rubric] = {
                "journeys": len(pairs),
                "agree": agree,
                "rate": round(agree / len(pairs), 4) if pairs else None,
                "mean_gap": round(mean(abs(normalized(rubric, a) - normalized(rubric, b)) for a, b in pairs), 4) if pairs else None,
            }
            for entry in sample:
                a = per.get((entry["trajectory_id"], rubric, primary))
                b = per.get((entry["trajectory_id"], rubric, second))
                if a is not None and b is not None and (a >= minimum[rubric]) != (b >= minimum[rubric]):
                    flags.append({"kind": "models_disagree", "trajectory_id": entry["trajectory_id"], "rubric": rubric, "model": f"{primary} vs {second}"})
    order = {model: {**value, "rate": round(value["consistent"] / value["journeys"], 4) if value["journeys"] else None} for model, value in consistency.items()}

    # Across repeats: a call is stable when every readable repeat falls on the same side of the rubric's threshold.
    repeats: dict[str, dict[str, dict]] = {}
    for (rubric, model), found in units.items():
        stable = 0
        spreads = []
        for item in found:
            sides = {_side(value, minimum[rubric]) for value in item["values"]}
            stable += int(len(sides) == 1)
            if len(sides) > 1:
                flags.append({"kind": "repeats_disagree", "trajectory_id": item["trajectory_id"], "rubric": rubric, "model": model})
            spreads.append(normalized(rubric, max(item["values"])) - normalized(rubric, min(item["values"])))
        repeats.setdefault(rubric, {})[model] = {
            "calls": len(found),
            "stable": stable,
            "rate": round(stable / len(found), 4),
            "mean_spread": round(mean(spreads), 4),
        }

    # Against code: the judge's verdict next to the code scorer's, journey by journey: conformance and decisions, and
    # each study rubric against the code's rubric of its kind.
    code: dict[str, dict] = {}
    for rubric, signal in against.items():
        scored = [entry for entry in sample if signal in (entry.get("code") or {})]
        if not scored or not any(key[1] == rubric for key in per):
            continue
        by_model = {}
        for model in models:
            pairs = [
                (per[(entry["trajectory_id"], rubric, model)], entry["code"][signal], entry["trajectory_id"])
                for entry in scored
                if per.get((entry["trajectory_id"], rubric, model)) is not None
            ]
            agree = 0
            for judge_value, found, tid in pairs:
                same = _side(judge_value, minimum[rubric]) == found["passed"]
                agree += int(same)
                if not same:
                    flags.append({"kind": "code_disagrees", "trajectory_id": tid, "rubric": rubric, "model": model})
            by_model[model] = {
                "journeys": len(pairs),
                "mean": round(mean(value for value, _, _ in pairs), 4) if pairs else None,
                "agree": agree,
                "rate": round(agree / len(pairs), 4) if pairs else None,
                "mean_gap": round(mean(abs(value - found["score"]) for value, found, _ in pairs), 4) if pairs else None,
            }
        code[rubric] = {
            "code": {
                "journeys": len(scored),
                "passed": sum(1 for entry in scored if entry["code"][signal]["passed"]),
                "mean": round(mean(entry["code"][signal]["score"] for entry in scored), 4),
            },
            "models": by_model,
            "judge_pass": minimum[rubric],
            "signal": signal,
        }
        if rubric in study:
            code[rubric].update(kind=study[rubric]["kind"], title=study[rubric]["title"], study=True)

    discrimination, pairwise_control = _discrimination(verdicts, per, flags)

    # A broken verdict blocks acceptance only when it leaves a sampled journey unscored for a rubric that decides it;
    # pairwise judges both orders, so one readable order still scores the journey.
    gating = [rubric for rubric in asked if rubric in GATING]
    unscored_primary = any(value is None for (tid, rubric, model), value in per.items() if model == primary and rubric in GATING)
    accepted = bool(gating) and not unscored_primary and all(
        scores[rubric][primary] is not None and scores[rubric][primary] >= minimum[rubric] for rubric in gating
    )
    notes = []
    for rubric in gating:
        value = scores[rubric][primary]
        if value is None or value >= minimum[rubric]:
            continue
        lowest = sorted(
            (item for item in judged if item["rubric"] == rubric and item["judge_model"] == primary and item["readable"]),
            key=lambda item: item["score"],
        )
        reason = _justification(lowest[0]) if lowest else ""
        notes.append(f"{rubric} {value:g} below {minimum[rubric]:g}. {reason}".strip())
    return {
        "scores": scores,
        "agreement": {
            "by_rubric": agreement,
            "order_consistency": order,
            "repeats": repeats,
            "code": code,
            "discrimination": discrimination,
            "pairwise_control": pairwise_control,
        },
        "flags": flags,
        "accepted": accepted,
        "revision_notes": notes,
    }


# Below this share of controls scored lower than their originals, a judge is blind to the defects on that rubric; for
# the pairwise control, a judge that picks the original no more often than chance is blind to it.
SEES = 0.5


def _discrimination(verdicts: list[dict], per: dict, flags: list[dict]) -> tuple[dict, dict]:
    """How often each judge scores a control below its original, per rubric, and picks the original in the pairwise one."""
    groups: dict[tuple[str, str, str, str], list[float]] = {}
    pairwise: dict[str, list[float]] = {}
    for item in verdicts:
        control = item.get("control")
        if not control or not item["readable"]:
            continue
        if control.startswith("pairwise:"):
            # The original is A when asked first and B when asked second; a pick of the original counts 1, a tie a half.
            pairwise.setdefault(item["judge_model"], []).append(item["score"] if item["order"] == "ab" else 1.0 - item["score"])
            continue
        groups.setdefault((item["trajectory_id"], control, item["rubric"], item["judge_model"]), []).append(item["score"])
    found: dict[str, dict[str, dict]] = {}
    for (tid, control, rubric, model), scores in groups.items():
        original = per.get((tid, rubric, model))
        if original is None:
            continue
        row = found.setdefault(rubric, {}).setdefault(model, {"controls": 0, "lower": 0, "kinds": {}})
        lower = mean(scores) < original
        row["controls"] += 1
        row["lower"] += int(lower)
        row["kinds"][control] = {"original": round(original, 4), "control": round(mean(scores), 4), "lower": lower}
    for rubric, by_model in found.items():
        for model, row in by_model.items():
            row["rate"] = round(row["lower"] / row["controls"], 4)
            row["blind"] = row["rate"] < SEES
            if row["blind"]:
                flags.append({"kind": "blind_to_defect", "trajectory_id": None, "rubric": rubric, "model": model})
    control = {}
    for model, picks in pairwise.items():
        accuracy = round(mean(picks), 4)
        control[model] = {"calls": len(picks), "accuracy": accuracy, "blind": accuracy <= SEES}
        if accuracy <= SEES:
            flags.append({"kind": "blind_to_defect", "trajectory_id": None, "rubric": "pairwise_quality", "model": model})
    return found, control
