"""What the judge sees, whom it asks, and how the verdicts become a cycle.

Each sampled journey is rendered with its amounts, state changes, and the sample's text, within a prompt
budget. Every rubric is asked of each judge model, and pairwise quality in both orders, so the cycle can
report agreement between models and position bias. A copy of one journey with its events put out of
order, which the pack's own replay rejects, checks that the judge can tell a broken journey at all.
"""

from __future__ import annotations

from copy import deepcopy
from statistics import mean

from trajectory_contract.models import TrajectoryBundle

from app.judge import Judge

UNREADABLE = "_unreadable"

DEFAULT_THRESHOLDS = {
    "helpfulness": 3.0,
    "correctness": 0.5,
    "safety": 1.0,
    "pairwise_quality": 0.5,
}

RUBRICS = ("helpfulness", "correctness", "safety", "pairwise_quality")

# Helpfulness is scored 1 to 5; the others 0 to 1. Normalized scores divide by this.
SCALE = {"helpfulness": 5.0}

# Rough characters per token, to keep a prompt inside the budget without a tokenizer.
CHARS_PER_TOKEN = 4


def corpus_excerpt(items: list) -> str:
    from app.corpus_text import read_corpus_excerpt

    return read_corpus_excerpt(items)


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


def render_journey(bundle: TrajectoryBundle, trajectory_id: str, budget_chars: int = 24_000) -> tuple[str, bool]:
    """The journey as the judge reads it, and whether it had to be shortened to fit the budget."""
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
    header = [f"Journey {traj.trajectory_id}, {traj.trajectory_type}, {traj.observed_or_synthetic}."]
    if traj.parent_trajectory_id:
        branch = events.get(traj.branch_event_id or "")
        header.append(f"A simulated alternative of {traj.parent_trajectory_id}, branching after {branch.event_type if branch else 'its start'}.")
    outcome = outcome_of(bundle, trajectory_id)
    if outcome:
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
) -> dict:
    """Judge a sample of journeys with every model and turn the verdicts into a cycle."""
    errors = [f"{error}" for journey in journeys for error in sector.hard_checks(journey)]
    base = {"reference_quality": reference_quality, "models": models, "sample": [], "scores": {}, "agreement": {}, "flags": [], "canary": None}
    if errors:
        return {**base, "hard_check_passed": False, "hard_check_errors": errors, "accepted": False, "revision_notes": errors, "verdicts": [], "called_judge": False}
    budget_chars = max(budget_tokens * CHARS_PER_TOKEN - len(brief) - 400, 2000)
    label = sector.label.lower()
    calls: list[dict] = []
    sample = []
    for journey in journeys:
        primary = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
        alternative = next((item for item in journey.trajectories if item.parent_trajectory_id == primary.trajectory_id), None)
        text, truncated = render_journey(journey, primary.trajectory_id, budget_chars if alternative is None else budget_chars // 2)
        sample.append(
            {
                "trajectory_id": primary.trajectory_id,
                "trajectory_type": primary.trajectory_type,
                "outcome": outcome_of(journey, primary.trajectory_id),
                "events": len(primary.event_ids),
                "truncated": truncated,
                "alternative": alternative.trajectory_id if alternative else None,
            }
        )
        own = {"trajectory_id": primary.trajectory_id, "order": None, "canary": False}
        calls.append({**own, "rubric": "helpfulness", "payload": {"prompt": brief + f"\nScore how representative this {label} journey is.", "response": text}})
        calls.append(
            {
                **own,
                "rubric": "correctness",
                "payload": {"prompt": f"Do these events follow the {label} transitions in the reference, in order?", "response": text, "expected": brief},
            }
        )
        calls.append({**own, "rubric": "safety", "payload": {"prompt": "Confirm the journey is synthetic and holds no real personal or account identifiers.", "response": text}})
        if alternative is not None:
            other, cut = render_journey(journey, alternative.trajectory_id, budget_chars // 2)
            sample[-1]["truncated"] = truncated or cut
            question = brief + "\nCompare the two journeys. Which is the more plausible customer journey?"
            calls.append({**own, "rubric": "pairwise_quality", "order": "ab", "payload": {"prompt": question, "response": text, "response_b": other}})
            calls.append({**own, "rubric": "pairwise_quality", "order": "ba", "payload": {"prompt": question, "response": other, "response_b": text}})
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
                    "payload": {"prompt": f"Do these events follow the {label} transitions in the reference, in order?", "response": text, "expected": brief},
                }
            )
            break

    planned = [(model, call) for model in models for call in calls]
    verdicts = []
    for index, (model, call) in enumerate(planned):
        if progress is not None:
            what = "a control journey" if call["canary"] else call["rubric"].replace("_", " ")
            progress(index, len(planned), f"{model}: {what} ({index + 1} of {len(planned)}).")
        result = judge.run_eval(rubric=call["rubric"], judge_model=model, **call["payload"])
        readable = result.get("readable", True)
        parsed = dict(result.get("parsed") or {})
        if not readable:
            parsed[UNREADABLE] = True
        verdicts.append(
            {
                "rubric": call["rubric"],
                "score": float(result["score"]),
                "parsed": parsed,
                "raw": result.get("raw", ""),
                "judge_model": model,
                "served_by": result.get("judge_model") or model,
                "duration_ms": result.get("duration_ms", 0),
                "trajectory_id": call["trajectory_id"],
                "order": call["order"],
                "canary": call["canary"],
                "readable": readable,
            }
        )
    summary = summarize(verdicts, sample, models, thresholds)
    if canary is not None:
        canary["results"] = {
            item["judge_model"]: (item["score"] if item["readable"] else None) for item in verdicts if item["canary"]
        }
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


def summarize(verdicts: list[dict], sample: list[dict], models: list[str], thresholds: dict[str, float]) -> dict:
    """Per-rubric scores per model, agreement between models, order consistency, audit flags, and the decision."""
    primary = models[0]
    minimum = {rubric: float(thresholds.get(rubric, DEFAULT_THRESHOLDS[rubric])) for rubric in RUBRICS}
    judged = [item for item in verdicts if not item["canary"]]
    # One value per journey, rubric, and model; pairwise combines both orders into the primary journey's preference.
    per: dict[tuple[str, str, str], float | None] = {}
    consistency: dict[str, dict] = {model: {"journeys": 0, "consistent": 0} for model in models}
    flags: list[dict] = []
    for entry in sample:
        tid = entry["trajectory_id"]
        for model in models:
            mine = [item for item in judged if item["trajectory_id"] == tid and item["judge_model"] == model]
            for rubric in ("helpfulness", "correctness", "safety"):
                found = next((item for item in mine if item["rubric"] == rubric), None)
                if found is None:
                    continue
                per[(tid, rubric, model)] = found["score"] if found["readable"] else None
                if not found["readable"]:
                    flags.append({"kind": "unreadable", "trajectory_id": tid, "rubric": rubric, "model": model})
            ab = next((item for item in mine if item["rubric"] == "pairwise_quality" and item["order"] == "ab"), None)
            ba = next((item for item in mine if item["rubric"] == "pairwise_quality" and item["order"] == "ba"), None)
            if ab is None and ba is None:
                continue
            values = []
            if ab is not None and ab["readable"]:
                values.append(ab["score"])
            if ba is not None and ba["readable"]:
                values.append(1.0 - ba["score"])
            per[(tid, "pairwise_quality", model)] = round(mean(values), 4) if values else None
            if ab is not None and ba is not None and ab["readable"] and ba["readable"]:
                consistency[model]["journeys"] += 1
                # The same journey should win in both orders: A first, then B.
                same = abs(ab["score"] - (1.0 - ba["score"])) < 0.01
                consistency[model]["consistent"] += int(same)
                if not same:
                    flags.append({"kind": "order_flip", "trajectory_id": tid, "rubric": "pairwise_quality", "model": model})
            elif (ab is not None and not ab["readable"]) or (ba is not None and not ba["readable"]):
                flags.append({"kind": "unreadable", "trajectory_id": tid, "rubric": "pairwise_quality", "model": model})
    for entry in sample:
        for model in models:
            value = per.get((entry["trajectory_id"], "correctness", model))
            if value is not None and value < minimum["correctness"]:
                # Every sampled journey replays legally through the pack's machines, so a failing verdict is likely the judge's error.
                flags.append({"kind": "likely_false_negative", "trajectory_id": entry["trajectory_id"], "rubric": "correctness", "model": model})
    for item in verdicts:
        if item["canary"] and item["readable"] and item["score"] >= minimum["correctness"]:
            flags.append({"kind": "likely_false_positive", "trajectory_id": item["trajectory_id"], "rubric": "correctness", "model": item["judge_model"]})

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

    unreadable_primary = any(not item["readable"] for item in judged if item["judge_model"] == primary)
    accepted = bool(asked) and not unreadable_primary and all(
        scores[rubric][primary] is not None and scores[rubric][primary] >= minimum[rubric] for rubric in asked
    )
    notes = []
    for rubric in asked:
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
        "agreement": {"by_rubric": agreement, "order_consistency": order},
        "flags": flags,
        "accepted": accepted,
        "revision_notes": notes,
    }
