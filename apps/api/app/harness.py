"""One episode rollout in several agent harness formats, as MiMo trains across harnesses.

Each line is one rollout of one episode, with its reward and group advantage, so a trainer can weight it.
`openai` uses chat messages with tool calls, `anthropic` uses content blocks with tool use and tool
results, and `react` writes Thought, Action, and Observation as plain text. One harness is held out of
training to measure whether a model generalizes to a format it never saw.
"""

from __future__ import annotations

import json

HARNESSES = ("openai", "anthropic", "react")
HELD_OUT = "react"


def _function_name(name: str) -> str:
    # Function names in chat APIs allow letters, digits, underscores, and dashes.
    return name.replace(".", "_")


def _common(episode, rollout, split: str) -> dict:
    return {
        "episode_id": episode["episode_id"],
        "rollout_id": rollout["rollout_id"],
        "policy": rollout["policy"],
        "split": split,
        "reward": rollout["reward"],
        "advantage": rollout["advantage"],
        "outcome": rollout["outcome"],
        "legal": rollout["legal"],
    }


def openai(episode: dict, rollout: dict, split: str) -> dict:
    turns = rollout["turns"]
    call = turns[2]["tool_call"]
    call_id = f"call_{rollout['rollout_id'].replace('.', '_')}"
    return {
        **_common(episode, rollout, split),
        "tools": [
            {"type": "function", "function": {"name": _function_name(tool["name"]), "description": tool["description"], "parameters": tool["parameters"]}}
            for tool in episode["tools"]
        ],
        "messages": [
            {"role": "system", "content": turns[0]["text"]},
            {"role": "user", "content": turns[1]["text"]},
            {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": _function_name(call["name"]), "arguments": json.dumps(call["arguments"], sort_keys=True, ensure_ascii=False)}}]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(turns[3]["tool_result"], sort_keys=True, ensure_ascii=False)},
            {"role": "assistant", "content": turns[4]["text"]},
        ],
    }


def anthropic(episode: dict, rollout: dict, split: str) -> dict:
    turns = rollout["turns"]
    call = turns[2]["tool_call"]
    use_id = f"toolu_{rollout['rollout_id'].replace('.', '_')}"
    return {
        **_common(episode, rollout, split),
        "system": turns[0]["text"],
        "tools": [{"name": _function_name(tool["name"]), "description": tool["description"], "input_schema": tool["parameters"]} for tool in episode["tools"]],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": turns[1]["text"]}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": use_id, "name": _function_name(call["name"]), "input": call["arguments"]}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id, "content": json.dumps(turns[3]["tool_result"], sort_keys=True, ensure_ascii=False)}]},
            {"role": "assistant", "content": [{"type": "text", "text": turns[4]["text"]}]},
        ],
    }


def react(episode: dict, rollout: dict, split: str) -> dict:
    turns = rollout["turns"]
    call = turns[2]["tool_call"]
    tools = "\n".join(f"- {tool['name']}({', '.join(tool['parameters']['properties'])}): {tool['description']}" for tool in episode["tools"])
    prompt = f"{turns[0]['text']}\n\nOperations:\n{tools}\n\nTask: {turns[1]['text']}\n"
    completion = (
        f"Thought: The case's state allows the next step; I record it.\n"
        f"Action: {call['name']}{json.dumps(call['arguments'], sort_keys=True, ensure_ascii=False)}\n"
        f"Observation: {json.dumps(turns[3]['tool_result'], sort_keys=True, ensure_ascii=False)}\n"
        f"Final: {turns[4]['text']}"
    )
    return {**_common(episode, rollout, split), "prompt": prompt, "completion": completion}


RENDER = {"openai": openai, "anthropic": anthropic, "react": react}
