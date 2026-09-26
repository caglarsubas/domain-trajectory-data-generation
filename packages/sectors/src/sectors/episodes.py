"""Episodes: a decision in a journey as an agent task against a mock bank.

At the point where a group's rollouts part ways, the builder takes the state the pack's machines had
reached, the operations that could be called there (named after BIAN service domains and action terms),
a task, and rubric items a program can check. Rollouts come from a scripted policy: the step the journey
took, its legal alternatives, and two perturbations, an operation the state forbids and a call naming
another case's object. Each rollout is scored on the rubric and rewarded as MiMo does: verification
times solution. Provider models join as rollouts in a later part of this slice, checked against the
episode's skeleton.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from trajectory_contract.models import Episode, EpisodeTurn, Rollout, RubricItem, ToolCall, ToolSpec, TrajectoryBundle

from sectors import rewards
from sectors.lifecycle import satisfied

# Event to (BIAN service domain, action term). Events that share both become one operation with an outcome argument.
BIAN = {
    "product.viewed": ("Product Directory", "Retrieve"),
    "application.started": ("Customer Offer", "Initiate"),
    "application.submitted": ("Customer Offer", "Update"),
    "application.abandoned": ("Customer Offer", "Control"),
    "application.approved": ("Customer Offer", "Execute"),
    "application.declined": ("Customer Offer", "Execute"),
    "kyc.started": ("Customer Due Diligence", "Initiate"),
    "kyc.review_required": ("Customer Due Diligence", "Request"),
    "kyc.document_submitted": ("Document Services", "Capture"),
    "kyc.passed": ("Customer Due Diligence", "Evaluate"),
    "kyc.failed": ("Customer Due Diligence", "Evaluate"),
    "account.opened": ("Current Account", "Initiate"),
    "account.funded": ("Payment Execution", "Execute"),
    "account.limit_change_requested": ("Current Account", "Request"),
    "account.limit_changed": ("Current Account", "Update"),
    "account.limit_change_declined": ("Current Account", "Update"),
    "account.closed": ("Current Account", "Control"),
    "card.issued": ("Issued Device Administration", "Initiate"),
    "card.activated": ("Issued Device Administration", "Update"),
    "card.purchase_authorised": ("Card Authorization", "Evaluate"),
    "card.purchase_declined": ("Card Authorization", "Evaluate"),
    "loan.disbursed": ("Consumer Loan", "Execute"),
    "loan.repayment_received": ("Consumer Loan", "Capture"),
    "loan.delinquent": ("Collections", "Initiate"),
    "loan.cured": ("Collections", "Update"),
    "complaint.received": ("Customer Case Management", "Initiate"),
    "complaint.resolved": ("Customer Case Management", "Execute"),
    "complaint.rejected": ("Customer Case Management", "Execute"),
}

RUBRIC = (
    ("format", "format", "Makes exactly one operation call whose arguments the operation's schema accepts."),
    ("legality", "legality", "Calls an operation the case's current state allows."),
    ("grounding", "grounding", "Names only this case's own objects in the arguments."),
    ("decision", "decision", "Takes a step after which the journey reaches its goal."),
    ("report", "report", "Ends with a message that says what was recorded, or why it was refused."),
)

TASK = {
    "en": "You operate the bank's systems for customer {party}. {situation} Decide the next step and record it with one operation call, then say what you did.",
    "tr": "{party} numaralı müşteri için bankanın sistemlerini yönetiyorsunuz. {situation} Sıradaki adıma karar verin, tek bir işlem çağrısıyla kaydedin ve ne yaptığınızı söyleyin.",
}
SYSTEM = {
    "en": "You are an operations agent at a retail bank. Use only the listed operations and only the case's own identifiers.",
    "tr": "Bir perakende bankada operasyon temsilcisisiniz. Yalnızca listelenen işlemleri ve yalnızca bu vakanın kimliklerini kullanın.",
}
REFUSED = {"en": "The operation was refused: {reason}.", "tr": "İşlem reddedildi: {reason}."}


def tool_name(event: str) -> tuple[str, str, str]:
    domain, action = BIAN.get(event) or (event.split(".")[0].replace("_", " ").title(), event.split(".")[-1].split("_")[0].title())
    return re.sub(r"[^A-Za-z]", "", domain.title()) + "." + action, domain, action


def _outcome(event: str) -> str:
    return event.split(".", 1)[1]


def build_tools(pack, events: list[str], operations: list[dict] | None = None) -> list[ToolSpec]:
    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(tool_name(event)[0], []).append(event)
    tools = []
    for name, members in grouped.items():
        _, domain, action = tool_name(members[0])
        properties: dict[str, Any] = {}
        required: list[str] = []
        for event in members:
            for kind, role in pack.roles[event]:
                key = f"{role}_id"
                if key not in properties:
                    properties[key] = {"type": "string", "description": f"Identifier of the {pack.objects[kind][1].replace('_', ' ')} ({role})."}
                    required.append(key)
        if len(members) > 1:
            properties["outcome"] = {"type": "string", "enum": [_outcome(event) for event in members], "description": "Which outcome to record."}
            required.append("outcome")
        english = pack.phrases["en"]
        described = " / ".join(english.get(event, event).rstrip(".") for event in members)
        tool = ToolSpec(
            name=name,
            service_domain=domain,
            action=action,
            description=f"{domain}: {action}. Records: {described}.",
            parameters={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            events=members,
        )
        match = _operation_for(tool, operations or [])
        if match:
            tool.http = match
        tools.append(tool)
    return sorted(tools, key=lambda tool: tool.name)


SUFFIXES = ("ations", "ation", "ions", "ion", "ing", "ed", "es", "s", "e")


def _stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {_stem(word) for word in re.findall(r"[a-z]+", spaced.lower()) if len(word) > 2}


def _operation_for(tool: ToolSpec, operations: list[dict]) -> dict | None:
    """The study's API operation closest to this tool by the words of its events, when it shares most of them."""
    wanted = _words(" ".join(event.replace(".", " ").replace("_", " ") for event in tool.events))
    # The object noun alone ("application") is not enough: the operation must share an action word too.
    actions = _words(" ".join(event.split(".", 1)[1].replace("_", " ") for event in tool.events))
    best, score = None, 0.0
    for operation in operations:
        words = _words(" ".join(str(operation.get(key) or "") for key in ("operationId", "summary", "path")))
        if not actions & words:
            continue
        overlap = len(wanted & words) / (len(wanted) or 1)
        if overlap > score:
            best, score = operation, overlap
    if best is None or score < 0.34:
        return None
    return {key: best.get(key) for key in ("method", "path", "operationId") if best.get(key)}


def _replay(pack, bundle: TrajectoryBundle, event_ids: list[str]) -> tuple[dict, dict[str, str], dict[str, str]]:
    """The machines' state after these events, the same as display strings, and each object kind's identifier."""
    kinds_by_type = {object_type: kind for kind, object_type in pack.lifecycle.object_types.items()}
    types = {obj.object_id: obj.object_type for obj in bundle.objects}
    wanted = set(event_ids)
    state: dict = {}
    shown: dict[str, str] = {}
    for change in bundle.state_transitions:
        if change.event_id in wanted and change.state_after is not None:
            kind = kinds_by_type.get(types.get(change.object_id, ""))
            if kind:
                state[(kind, change.state_dimension)] = change.state_after
            shown[f"{change.object_id}.{change.state_dimension}"] = change.state_after
    objects: dict[str, str] = {}
    for link in bundle.event_objects:
        if link.event_id in wanted:
            kind = next((name for name, spec in pack.objects.items() if spec[1] == types.get(link.object_id)), None)
            if kind and kind not in objects:
                objects[kind] = link.object_id
    return state, shown, objects


def _arguments(pack, event: str, objects: dict[str, str], taken_links: dict[str, str], tools: dict[str, ToolSpec]) -> dict[str, Any]:
    """The call a careful agent would make: the case's own objects by role, and the outcome when the operation asks for one."""
    arguments: dict[str, Any] = {}
    for kind, role in pack.roles[event]:
        found = objects.get(kind) or taken_links.get(kind)
        if found:
            arguments[f"{role}_id"] = found
    tool = tools.get(tool_name(event)[0])
    if tool is not None and "outcome" in tool.parameters["properties"]:
        arguments["outcome"] = _outcome(event)
    return arguments


def valid_arguments(tool: ToolSpec | None, arguments: dict) -> bool:
    """Whether a call's arguments fit the operation's schema: every required key, no others, and a listed outcome."""
    if tool is None:
        return False
    properties = tool.parameters["properties"]
    if not set(tool.parameters.get("required", [])) <= set(arguments) or not set(arguments) <= set(properties):
        return False
    allowed = properties.get("outcome", {}).get("enum")
    return not allowed or arguments.get("outcome") in allowed


def _score(well_formed: bool, legal: bool, grounded: bool, outcome: str) -> tuple[dict[str, float], float]:
    scores = {"format": float(well_formed), "legality": float(legal), "grounding": float(grounded), "decision": float(legal and outcome == "pass"), "report": 1.0}
    verified = legal and grounded
    return scores, round(rewards.multiplicative_reward(verified, sum(scores.values()) / len(scores), 1.0), 4)


def build_episodes(pack, bundle: TrajectoryBundle, *, language: str = "en", seed: str = "episodes", operations: list[dict] | None = None) -> list[Episode]:
    lang = language.split("-")[0] if language.split("-")[0] in TASK else "en"
    rng = random.Random(f"{seed}|episodes")
    events = {event.event_id: event for event in bundle.events}
    trajectories = {item.trajectory_id: item for item in bundle.trajectories}
    links: dict[str, dict[str, str]] = {}
    object_types = {obj.object_id: obj.object_type for obj in bundle.objects}
    for link in bundle.event_objects:
        kind = next((name for name, spec in pack.objects.items() if spec[1] == object_types.get(link.object_id)), None)
        if kind:
            links.setdefault(link.event_id, {})[kind] = link.object_id
    phrases = pack.phrases.get(lang) or pack.phrases["en"]
    episodes = []
    for sample in bundle.samples:
        members = [(sequence, trajectories.get(sequence.trajectory_id or "")) for sequence in sample.sequences]
        members = [(sequence, trajectory) for sequence, trajectory in members if trajectory is not None]
        if len(members) < 2:
            continue
        paths = [[events[item].event_type for item in trajectory.event_ids if item in events] for _, trajectory in members]
        split = 0
        while all(split < len(path) for path in paths) and len({path[split] for path in paths}) == 1:
            split += 1
        primary_sequence, primary = members[0]
        if split == 0 or split >= len(paths[0]):
            continue
        prefix_ids = primary.event_ids[:split]
        state, shown, objects = _replay(pack, bundle, prefix_ids)
        counts: dict[str, int] = {}
        for name in paths[0][:split]:
            counts[name] = counts.get(name, 0) + 1
        legal = [spec.event_type for spec in pack.lifecycle.events if counts.get(spec.event_type, 0) < spec.repeat and satisfied(spec, state)]
        illegal = [name for name in pack.lifecycle.namespace if name not in legal and name in pack.roles]
        distractors = rng.sample(illegal, min(2, len(illegal)))
        tools = build_tools(pack, legal + distractors, operations)
        by_name = {tool.name: tool for tool in tools}
        tool_of = {event: tool_name(event)[0] for event in legal + distractors}
        history = [
            {"event_type": events[item].event_type, "time": events[item].event_time.isoformat(), "text": phrases.get(events[item].event_type, events[item].event_type)}
            for item in prefix_ids
            if item in events
        ]
        party = objects.get("party") or primary.root_party_id
        task = TASK[lang].format(party=party, situation=" ".join(item["text"] for item in history[-2:]))
        system = SYSTEM[lang]

        def rollout(policy: str, event: str, arguments: dict, result: dict, legal_step: bool, grounded: bool, outcome: str, index: int) -> Rollout:
            call = ToolCall(name=tool_of.get(event, tool_name(event)[0]), arguments=arguments)
            final = phrases.get(event, event) if result.get("status") == "ok" else REFUSED[lang].format(reason=result.get("reason", ""))
            scores, reward = _score(valid_arguments(by_name.get(call.name), arguments), legal_step, grounded, outcome)
            return Rollout(
                rollout_id=f"{sample.sample_id}.R{index}",
                policy=policy,
                turns=[
                    EpisodeTurn(role="system", text=system),
                    EpisodeTurn(role="user", text=task),
                    EpisodeTurn(role="assistant", tool_call=call, trainable=True),
                    EpisodeTurn(role="tool", tool_result=result),
                    EpisodeTurn(role="assistant", text=final, trainable=True),
                ],
                action_event=event,
                legal=legal_step,
                outcome=outcome,
                rubric_scores=scores,
                reward=reward,
            )

        rollouts = []
        seen: set[str] = set()
        for index, ((sequence, trajectory), path) in enumerate(zip(members, paths)):
            if split >= len(path) or path[split] in seen:
                continue
            event = path[split]
            seen.add(event)
            event_id = trajectory.event_ids[split]
            changes = [
                {"object": change.object_id, "dimension": change.state_dimension, "from": change.state_before, "to": change.state_after}
                for change in bundle.state_transitions
                if change.event_id == event_id
            ]
            created = {kind: object_id for kind, object_id in links.get(event_id, {}).items() if kind not in objects}
            result = {"status": "ok", "event": event, "state_changes": changes, "created": created}
            policy = "reference" if index == 0 else "alternative"
            arguments = _arguments(pack, event, objects, links.get(event_id, {}), by_name)
            rollouts.append(rollout(policy, event, arguments, result, True, True, sequence.outcome or "pass", len(rollouts)))
        reference = rollouts[0]
        if distractors:
            event = distractors[0]
            spec = pack.lifecycle[event]
            unmet = next((guard for guard in spec.requires if state.get((guard.kind, guard.dimension)) not in guard.states), None)
            reason = f"{unmet.kind}.{unmet.dimension} is {state.get((unmet.kind, unmet.dimension)) or 'not set'}" if unmet else "the step cannot repeat"
            result = {"status": "error", "reason": f"precondition failed: {reason}"}
            rollouts.append(rollout("perturbed:illegal", event, _arguments(pack, event, objects, {}, by_name), result, False, True, "fail", len(rollouts)))
        wrong = dict(reference.turns[2].tool_call.arguments)
        key = next((name for name in wrong if name.endswith("_id")), None)
        if key:
            wrong[key] = f"{wrong[key]}-X{rng.randrange(100, 999)}"
            result = {"status": "error", "reason": f"unknown object {wrong[key]}"}
            rollouts.append(rollout("perturbed:wrong_object", reference.action_event, wrong, result, True, False, "fail", len(rollouts)))
        for item, advantage in zip(rollouts, rewards.group_advantages([item.reward for item in rollouts])):
            item.advantage = round(advantage, 4)
        skeleton = {
            "legal_events": legal,
            "legal_tools": sorted({tool_name(event)[0] for event in legal}),
            "objects": objects,
            "reference": {"tool": reference.turns[2].tool_call.name, "event": reference.action_event, "arguments": reference.turns[2].tool_call.arguments},
            "alternatives": [item.action_event for item in rollouts if item.policy == "alternative"],
        }
        episodes.append(
            Episode(
                episode_id=f"{sample.sample_id}.EP",
                trajectory_id=primary.trajectory_id,
                sample_id=sample.sample_id,
                decision_index=split,
                state=shown,
                history=history,
                task=task,
                tools=tools,
                rubric=[RubricItem(item_id=item_id, kind=kind, text=text) for item_id, kind, text in RUBRIC],
                skeleton=skeleton,
                rollouts=rollouts,
            )
        )
    return episodes


# A rollout passes when the call is well formed, legal, grounded, and leads to the goal; the report item does not decide it.
PASSING_ITEMS = ("format", "legality", "grounding", "decision")


def passes(rollout) -> bool:
    scores = rollout.rubric_scores if hasattr(rollout, "rubric_scores") else rollout.get("rubric_scores") or {}
    return all(scores.get(item) == 1.0 for item in PASSING_ITEMS)


def summarize(episodes: list[Episode]) -> dict:
    from sectors.scorers import PASS_AT, pass_at_k

    rollouts = [item for episode in episodes for item in episode.rollouts]
    accepted = sum(1 for episode in episodes if rewards.group_accepted([item.outcome == "pass" and item.legal for item in episode.rollouts]))
    # Each provider model's attempts per episode, as pass@k averaged over the episodes it attempted at least k times.
    models: dict[str, dict] = {}
    for episode in episodes:
        attempts: dict[str, list[bool]] = {}
        for item in episode.rollouts:
            if item.policy.startswith("provider:"):
                attempts.setdefault(item.policy, []).append(passes(item))
        for policy, found in attempts.items():
            row = models.setdefault(policy, {"episodes": 0, "pass_at_k": {}, "episodes_at_k": {}})
            row["episodes"] += 1
            for k in PASS_AT:
                if k <= len(found):
                    row["pass_at_k"][str(k)] = row["pass_at_k"].get(str(k), 0.0) + pass_at_k(len(found), sum(found), k)
                    row["episodes_at_k"][str(k)] = row["episodes_at_k"].get(str(k), 0) + 1
    return {
        "episodes": len(episodes),
        "rollouts": len(rollouts),
        "accepted_groups": accepted,
        "policies": {name: sum(1 for item in rollouts if item.policy == name) for name in sorted({item.policy for item in rollouts})},
        "with_api_operations": sum(1 for episode in episodes if any(tool.http for tool in episode.tools)),
        "models": {
            policy: {"episodes": row["episodes"], "episodes_at_k": row["episodes_at_k"], "pass_at_k": {k: round(value / row["episodes_at_k"][k], 4) for k, value in row["pass_at_k"].items()}}
            for policy, row in models.items()
        },
    }


def arguments_json(arguments: dict) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)
