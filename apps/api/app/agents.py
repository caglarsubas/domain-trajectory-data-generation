"""Provider models as agents in episodes, through the key of the account that owns the run.

Each provider rollout is two calls: the model sees the task and the episode's operations and makes a
call; the mock bank answers; the model reports what it did. Every call it makes is checked by code
against the episode's skeleton (a known operation, arguments the schema accepts, a legal step, the
case's own objects) and scored on the same rubric as the scripted rollouts. The key is sent only to its
provider, in a header, and never written anywhere. A budget caps the calls a run may make.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx

from sectors import rewards
from sectors.episodes import tool_name, valid_arguments
from trajectory_contract.models import EpisodeTurn, Rollout, ToolCall

MODELS = {
    "openai": ("OPENAI_AGENT_MODEL", "gpt-5.5"),
    "anthropic": ("ANTHROPIC_AGENT_MODEL", "claude-sonnet-4-5"),
    "google": ("GOOGLE_AGENT_MODEL", "gemini-2.5-flash"),
    "xai": ("XAI_AGENT_MODEL", "grok-4.5"),
}
BASES = {
    "openai": ("OPENAI_BASE_URL", "https://api.openai.com"),
    "anthropic": ("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "google": ("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com"),
    "xai": ("XAI_BASE_URL", "https://api.x.ai"),
}
CALLS_PER_ROLLOUT = 2
MAX_ERRORS = 3
TIMEOUT = 60.0


class AgentError(Exception):
    """A provider call that failed, with a reason safe to show."""


@dataclass
class Reply:
    text: str = ""
    call: dict | None = None


def default_model(provider: str) -> str:
    env, fallback = MODELS[provider]
    return os.environ.get(env, "").strip() or fallback


def _base(provider: str) -> str:
    env, fallback = BASES[provider]
    return os.environ.get(env, fallback).strip().rstrip("/") or fallback


def _wire(name: str) -> str:
    return name.replace(".", "_")


def _safe(text: str, key: str) -> str:
    from sectors.steering import scrub_text

    return scrub_text(text.replace(key, "") if key else text)[:200].strip()


class ProviderAgent:
    """One provider's tool-calling API: a first turn that may call an operation, and a report after the result."""

    def __init__(self, provider: str, key: str, model: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.provider, self.key, self.model = provider, key, model or default_model(provider)
        self.transport = transport

    def _post(self, path: str, headers: dict, body: dict) -> dict:
        try:
            with httpx.Client(base_url=_base(self.provider), transport=self.transport, timeout=TIMEOUT) as client:
                response = client.post(path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise AgentError(f"{self.provider} could not be reached") from exc
        if response.status_code != 200:
            raise AgentError(f"{self.provider} answered {response.status_code}: {_safe(response.text, self.key)}")
        try:
            return response.json()
        except ValueError as exc:
            raise AgentError(f"{self.provider} returned a response that could not be read") from exc

    def act(self, system: str, task: str, tools: list[dict]) -> tuple[Reply, dict]:
        """The model's first turn, and the conversation state to continue from."""
        if self.provider in {"openai", "xai"}:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
            wire = [{"type": "function", "function": {"name": _wire(tool["name"]), "description": tool["description"], "parameters": tool["parameters"]}} for tool in tools]
            data = self._post("/v1/chat/completions", {"Authorization": f"Bearer {self.key}"}, {"model": self.model, "messages": messages, "tools": wire})
            message = (data.get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls") or []
            call = None
            if calls:
                function = calls[0].get("function") or {}
                call = {"name": function.get("name", ""), "arguments": _arguments(function.get("arguments")), "id": calls[0].get("id", "call_1"), "count": len(calls)}
            return Reply(message.get("content") or "", call), {"messages": messages + [message], "tools": wire}
        if self.provider == "anthropic":
            wire = [{"name": _wire(tool["name"]), "description": tool["description"], "input_schema": tool["parameters"]} for tool in tools]
            messages = [{"role": "user", "content": task}]
            data = self._post("/v1/messages", self._anthropic_headers(), {"model": self.model, "max_tokens": 1024, "system": system, "messages": messages, "tools": wire})
            blocks = data.get("content") or []
            uses = [block for block in blocks if block.get("type") == "tool_use"]
            text = " ".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()
            call = {"name": uses[0].get("name", ""), "arguments": uses[0].get("input") or {}, "id": uses[0].get("id", "toolu_1"), "count": len(uses)} if uses else None
            return Reply(text, call), {"messages": messages + [{"role": "assistant", "content": blocks}], "tools": wire, "system": system}
        if self.provider == "google":
            wire = [{"functionDeclarations": [{"name": _wire(tool["name"]), "description": tool["description"], "parameters": _gemini_schema(tool["parameters"])} for tool in tools]}]
            contents = [{"role": "user", "parts": [{"text": task}]}]
            data = self._post(f"/v1beta/models/{self.model}:generateContent", {"x-goog-api-key": self.key}, {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "tools": wire})
            parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
            functions = [part["functionCall"] for part in parts if "functionCall" in part]
            text = " ".join(part.get("text", "") for part in parts if "text" in part).strip()
            call = {"name": functions[0].get("name", ""), "arguments": functions[0].get("args") or {}, "id": "call_1", "count": len(functions)} if functions else None
            return Reply(text, call), {"contents": contents + [{"role": "model", "parts": parts}], "tools": wire, "system": system}
        raise AgentError(f"no agent for {self.provider}")

    def report(self, state: dict, call: dict, result: dict) -> Reply:
        """The model's message after the mock bank answered its call."""
        payload = json.dumps(result, ensure_ascii=False)
        if self.provider in {"openai", "xai"}:
            messages = state["messages"] + [{"role": "tool", "tool_call_id": call["id"], "content": payload}]
            data = self._post("/v1/chat/completions", {"Authorization": f"Bearer {self.key}"}, {"model": self.model, "messages": messages, "tools": state["tools"]})
            return Reply(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        if self.provider == "anthropic":
            messages = state["messages"] + [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": payload}]}]
            data = self._post("/v1/messages", self._anthropic_headers(), {"model": self.model, "max_tokens": 512, "system": state["system"], "messages": messages, "tools": state["tools"]})
            return Reply(" ".join(block.get("text", "") for block in data.get("content") or [] if block.get("type") == "text").strip())
        contents = state["contents"] + [{"role": "user", "parts": [{"functionResponse": {"name": call["name"], "response": result}}]}]
        data = self._post(f"/v1beta/models/{self.model}:generateContent", {"x-goog-api-key": self.key}, {"systemInstruction": {"parts": [{"text": state["system"]}]}, "contents": contents, "tools": state["tools"]})
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        return Reply(" ".join(part.get("text", "") for part in parts if "text" in part).strip())

    def _anthropic_headers(self) -> dict:
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}


def _arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        found = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"__unparsed__": str(raw)[:200]}
    return found if isinstance(found, dict) else {"__unparsed__": str(raw)[:200]}


def _gemini_schema(schema: dict) -> dict:
    # Gemini's function declarations take an OpenAPI subset without additionalProperties.
    return {key: value for key, value in schema.items() if key != "additionalProperties"}


def check(episode: dict, call: dict | None) -> dict:
    """A model's call against the episode's skeleton: which operation, whether it fits, is legal, and names the case's objects."""
    if call is None:
        return {"tool_known": False, "arguments_valid": False, "legal": False, "grounded": False, "event": None, "branch": None, "single_call": False}
    tools = {_wire(tool["name"]): tool for tool in episode["tools"]}
    tool = tools.get(call["name"])
    if tool is None:
        return {"tool_known": False, "arguments_valid": False, "legal": False, "grounded": False, "event": None, "branch": None, "single_call": call.get("count", 1) == 1}
    from trajectory_contract.models import ToolSpec

    spec = ToolSpec.model_validate(tool)
    arguments = call["arguments"]
    valid = valid_arguments(spec, arguments)
    if len(spec.events) > 1:
        event = next((name for name in spec.events if name.split(".", 1)[1] == arguments.get("outcome")), None)
    else:
        event = spec.events[0]
    skeleton = episode["skeleton"]
    legal = event in skeleton["legal_events"]
    own = set(skeleton["objects"].values()) | set((skeleton["reference"].get("arguments") or {}).values())
    named = [value for key, value in arguments.items() if key.endswith("_id")]
    grounded = bool(named) and all(value in own for value in named)
    observed = {item["action_event"]: item["outcome"] for item in episode["rollouts"] if item["policy"] in {"reference", "alternative"}}
    branch = "observed" if event in observed else ("unobserved" if legal else None)
    return {"tool_known": True, "arguments_valid": valid, "legal": legal, "grounded": grounded, "event": event, "branch": branch, "single_call": call.get("count", 1) == 1}


def mock_result(episode: dict, checks: dict) -> dict:
    """What the mock bank answers: the recorded step when legal and grounded, a refusal otherwise."""
    if not checks["tool_known"]:
        return {"status": "error", "reason": "unknown operation"}
    if not checks["arguments_valid"]:
        return {"status": "error", "reason": "the arguments do not fit the operation"}
    if not checks["grounded"]:
        return {"status": "error", "reason": "unknown object"}
    if not checks["legal"]:
        return {"status": "error", "reason": "precondition failed: the case's state does not allow this step"}
    same = next((item for item in episode["rollouts"] if item["action_event"] == checks["event"] and item["policy"] in {"reference", "alternative"}), None)
    if same is not None:
        return same["turns"][3]["tool_result"]
    return {"status": "ok", "event": checks["event"]}


def score(checks: dict, outcome: str, reported: bool) -> tuple[dict, float]:
    scores = {
        "format": float(checks["tool_known"] and checks["arguments_valid"] and checks["single_call"]),
        "legality": float(checks["legal"]),
        "grounding": float(checks["grounded"]),
        "decision": float(checks["legal"] and outcome == "pass"),
        "report": float(reported),
    }
    return scores, round(rewards.multiplicative_reward(checks["legal"] and checks["grounded"], sum(scores.values()) / len(scores), 1.0), 4)


@dataclass
class Budget:
    limit: int
    used: int = 0
    errors: int = 0
    skipped: int = 0
    rollouts: int = 0
    last_error: str | None = None
    checks: dict = field(default_factory=lambda: {"legal": 0, "grounded": 0, "observed_branch": 0, "no_call": 0})

    @property
    def open(self) -> bool:
        return self.used + CALLS_PER_ROLLOUT <= self.limit and self.errors < MAX_ERRORS

    def as_dict(self) -> dict:
        stopped_by = "errors" if self.errors >= MAX_ERRORS else ("budget" if self.skipped else None)
        return {
            "limit": self.limit, "calls": self.used, "rollouts": self.rollouts, "skipped_rollouts": self.skipped, "stopped_by": stopped_by,
            "errors": self.errors, "last_error": self.last_error, "checks": dict(self.checks),
        }


def add_provider_rollouts(episodes: list[dict], agent, per_episode: int, budget: Budget, progress=None) -> None:
    """Append provider rollouts to each episode until the budget runs out, then recompute each group's advantages."""
    label = f"provider:{agent.model}"
    for position, episode in enumerate(episodes):
        added = False
        for turn in range(per_episode):
            if not budget.open:
                budget.skipped += 1
                continue
            if progress is not None:
                progress(position, len(episodes), f"Asking {agent.model} for episode {position + 1} of {len(episodes)}.")
            system = episode["rollouts"][0]["turns"][0]["text"]
            try:
                # Every attempted call counts, failed ones too: the provider may bill them.
                budget.used += 1
                reply, state = agent.act(system, episode["task"], episode["tools"])
                checks = check(episode, reply.call)
                result = mock_result(episode, checks)
                final = reply.text
                if reply.call is not None:
                    budget.used += 1
                    final = agent.report(state, reply.call, result).text or final
            except AgentError as exc:
                budget.errors += 1
                budget.last_error = str(exc)
                continue
            observed = {item["action_event"]: item["outcome"] for item in episode["rollouts"] if item["policy"] in {"reference", "alternative"}}
            outcome = observed.get(checks["event"], "fail") if checks["legal"] else "fail"
            scores, reward = score(checks, outcome, bool(final.strip()))
            budget.rollouts += 1
            for name, key in (("legal", "legal"), ("grounded", "grounded")):
                budget.checks[name] += int(checks[key])
            budget.checks["observed_branch"] += int(checks["branch"] == "observed")
            budget.checks["no_call"] += int(reply.call is None)
            call = reply.call or {}
            name = next((tool["name"] for tool in episode["tools"] if _wire(tool["name"]) == call.get("name")), call.get("name") or tool_name(checks["event"] or "unknown.unknown")[0])
            episode["rollouts"].append(
                Rollout(
                    rollout_id=f"{episode['episode_id']}.P{turn}",
                    policy=label,
                    turns=[
                        EpisodeTurn(role="system", text=system),
                        EpisodeTurn(role="user", text=episode["task"]),
                        EpisodeTurn(role="assistant", text=reply.text or None, tool_call=ToolCall(name=name, arguments=call.get("arguments") or {}) if reply.call else None, trainable=True),
                        EpisodeTurn(role="tool", tool_result=result),
                        EpisodeTurn(role="assistant", text=final or None, trainable=True),
                    ],
                    action_event=checks["event"],
                    legal=checks["legal"],
                    outcome=outcome,
                    rubric_scores=scores,
                    reward=reward,
                    checks=checks,
                ).model_dump(mode="json")
            )
            added = True
        if added:
            values = [item["reward"] for item in episode["rollouts"]]
            for item, advantage in zip(episode["rollouts"], rewards.group_advantages(values)):
                item["advantage"] = round(advantage, 4)
