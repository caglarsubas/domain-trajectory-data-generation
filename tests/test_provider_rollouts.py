import json

import httpx
import pytest

from app import runtime
from app.harness import RENDER
from app.agents import AgentError, Budget, ProviderAgent, add_provider_rollouts, check
from sectors.registry import get_sector
from test_api import _auth, _project, _ready_key, _run

BANKING = get_sector("banking")
SETTINGS = dict(
    sub_domains=["onboarding_and_kyc", "consumer_credit"], language="en", target_trajectory_count=4, event_budget=None, min_events=4, max_events=14,
    max_assistant_turns=6, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
    target_family="llm", corpus_text="", feedback=None, revision_notes=None, parent_bundle=None, seed="provider-test", group_size=4,
)
TOOLS = [{"name": "CustomerOffer.Execute", "description": "Customer Offer: Execute.", "parameters": {"type": "object", "properties": {"application_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["approved", "declined"]}}, "required": ["application_id", "outcome"], "additionalProperties": False}}]


def _episodes():
    return [episode.model_dump(mode="json") for episode in BANKING.generate(**SETTINGS, episodes=True).episodes]


# Provider wire formats


def test_openai_and_xai_speak_chat_completions_with_tool_calls(monkeypatch):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.host, request.url.path, request.headers.get("authorization"), body))
        if len(seen) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "CustomerOffer_Execute", "arguments": "{\"application_id\": \"A1\", \"outcome\": \"approved\"}"}}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Approved A1."}}]})

    agent = ProviderAgent("openai", "sk-agent-test-000001", "gpt-test", transport=httpx.MockTransport(handler))
    reply, state = agent.act("system", "task", TOOLS)
    assert reply.call == {"name": "CustomerOffer_Execute", "arguments": {"application_id": "A1", "outcome": "approved"}, "id": "c1", "count": 1}
    assert seen[0][1] == "/v1/chat/completions" and seen[0][2] == "Bearer sk-agent-test-000001"
    assert seen[0][3]["tools"][0]["function"]["name"] == "CustomerOffer_Execute" and seen[0][3]["model"] == "gpt-test"
    final = agent.report(state, reply.call, {"status": "ok"})
    assert final.text == "Approved A1." and seen[1][3]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "{\"status\": \"ok\"}"}
    assert "sk-agent-test-000001" not in json.dumps([item[3] for item in seen])

    monkeypatch.setenv("XAI_BASE_URL", "https://api.x.ai")
    seen.clear()
    ProviderAgent("xai", "xai-agent-000001", "grok-test", transport=httpx.MockTransport(handler)).act("system", "task", TOOLS)
    assert seen[0][0] == "api.x.ai" and seen[0][1] == "/v1/chat/completions"


def test_anthropic_uses_tool_use_and_tool_result_blocks():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers.get("x-api-key"), body))
        if len(seen) == 1:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Deciding."}, {"type": "tool_use", "id": "tu1", "name": "CustomerOffer_Execute", "input": {"application_id": "A1", "outcome": "declined"}}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Declined A1."}]})

    agent = ProviderAgent("anthropic", "sk-ant-agent-00001", "claude-test", transport=httpx.MockTransport(handler))
    reply, state = agent.act("system", "task", TOOLS)
    assert reply.call["name"] == "CustomerOffer_Execute" and reply.call["arguments"]["outcome"] == "declined" and reply.text == "Deciding."
    assert seen[0][1]["tools"][0]["input_schema"]["required"] == ["application_id", "outcome"] and seen[0][1]["system"] == "system"
    assert agent.report(state, reply.call, {"status": "ok"}).text == "Declined A1."
    assert seen[1][1]["messages"][-1]["content"][0]["type"] == "tool_result" and seen[0][0] == "sk-ant-agent-00001"


def test_google_uses_function_declarations_without_unsupported_schema_keys():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.path, request.headers.get("x-goog-api-key"), body))
        if len(seen) == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"functionCall": {"name": "CustomerOffer_Execute", "args": {"application_id": "A1", "outcome": "approved"}}}]}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Done."}]}}]})

    agent = ProviderAgent("google", "g" * 30, "gemini-test", transport=httpx.MockTransport(handler))
    reply, state = agent.act("system", "task", TOOLS)
    assert seen[0][0] == "/v1beta/models/gemini-test:generateContent" and seen[0][1] == "g" * 30
    declaration = seen[0][2]["tools"][0]["functionDeclarations"][0]
    assert "additionalProperties" not in declaration["parameters"] and reply.call["arguments"]["outcome"] == "approved"
    assert agent.report(state, reply.call, {"status": "ok"}).text == "Done."
    assert "functionResponse" in seen[1][2]["contents"][-1]["parts"][0]


def test_provider_errors_hide_the_key():
    agent = ProviderAgent("openai", "sk-hidden-key-00001", transport=httpx.MockTransport(lambda request: httpx.Response(401, text="bad key sk-hidden-key-00001")))
    with pytest.raises(AgentError) as error:
        agent.act("system", "task", TOOLS)
    assert "401" in str(error.value) and "sk-hidden-key-00001" not in str(error.value)


# Checks against the skeleton


def test_a_model_call_is_checked_against_the_episode_skeleton():
    episode = _episodes()[0]
    reference = episode["skeleton"]["reference"]
    wire = reference["tool"].replace(".", "_")
    good = check(episode, {"name": wire, "arguments": reference["arguments"], "count": 1})
    assert good == {**good, "tool_known": True, "arguments_valid": True, "legal": True, "grounded": True, "event": reference["event"], "branch": "observed", "single_call": True}
    stranger = {**reference["arguments"], next(key for key in reference["arguments"] if key.endswith("_id")): "Z-999"}
    assert check(episode, {"name": wire, "arguments": stranger, "count": 1})["grounded"] is False
    assert check(episode, {"name": "Nope_Tool", "arguments": {}, "count": 1})["tool_known"] is False
    assert check(episode, {"name": wire, "arguments": {"surprise": 1}, "count": 1})["arguments_valid"] is False
    assert check(episode, None)["legal"] is False
    illegal = next(item for item in episode["rollouts"] if item["policy"] == "perturbed:illegal")
    forbidden = check(episode, {"name": illegal["turns"][2]["tool_call"]["name"].replace(".", "_"), "arguments": illegal["turns"][2]["tool_call"]["arguments"], "count": 2})
    assert forbidden["legal"] is False and forbidden["single_call"] is False


class ScriptedModel:
    """Takes the reference step on even calls and answers without a call on odd ones."""

    def __init__(self, episodes, fail_after=None):
        self.model, self.turn, self.fail_after = "scripted-model", 0, fail_after
        self.by_task = {episode["task"]: episode for episode in episodes}

    def act(self, system, task, tools):
        from app.agents import Reply

        self.turn += 1
        if self.fail_after is not None and self.turn > self.fail_after:
            raise AgentError("scripted outage")
        reference = self.by_task[task]["skeleton"]["reference"]
        if self.turn % 2:
            return Reply("", {"name": reference["tool"].replace(".", "_"), "arguments": reference["arguments"], "id": "c", "count": 1}), {}
        return Reply("I would rather not call anything."), {}

    def report(self, state, call, result):
        from app.agents import Reply

        return Reply(f"Recorded: {result.get('event', result.get('reason'))}.")


def test_provider_rollouts_are_scored_and_stop_at_the_budget():
    episodes = _episodes()
    model = ScriptedModel(episodes)
    budget = Budget(limit=5)
    add_provider_rollouts(episodes, model, 2, budget)
    added = [item for episode in episodes for item in episode["rollouts"] if item["policy"] == "provider:scripted-model"]
    assert budget.used <= 5 and budget.rollouts == len(added) and budget.skipped == 2 * len(episodes) - len(added)
    first = added[0]
    assert first["checks"]["legal"] and first["reward"] > 0 and first["turns"][4]["text"].startswith("Recorded:")
    silent = next(item for item in added if item["checks"]["tool_known"] is False)
    assert silent["reward"] == 0.0 and silent["turns"][2]["tool_call"] is None
    episode = next(item for item in episodes if silent in item["rollouts"])
    for render in RENDER.values():
        rendered = json.dumps(render(episode, silent, "train"))
        assert "I would rather not call anything." in rendered
    for episode in episodes:
        if any(item["policy"].startswith("provider:") for item in episode["rollouts"]):
            assert sum(item["advantage"] for item in episode["rollouts"]) == pytest.approx(0.0, abs=1e-3)


def test_repeated_provider_errors_stop_the_provider_rollouts():
    episodes = _episodes()
    budget = Budget(limit=100)
    add_provider_rollouts(episodes, ScriptedModel(episodes, fail_after=0), 4, budget)
    assert budget.errors == 3 and budget.used == 3 and budget.rollouts == 0 and budget.last_error == "scripted outage"
    assert budget.skipped == 4 * len(episodes) - 3 and budget.as_dict()["stopped_by"] == "errors"


# Runs


class FakeFactory:
    """An agent per run that calls the first offered operation with the customer from the task."""

    def __init__(self):
        self.keys = []

    def __call__(self, provider, key, model):
        self.keys.append((provider, key))
        factory = self

        class Agent:
            def __init__(self):
                self.model = model or "fake-model"

            def act(self, system, task, tools):
                from app.agents import Reply

                party = task.split("customer ")[1].split(".")[0]
                arguments = {name: party for name in tools[0]["parameters"]["properties"] if name.endswith("_id")}
                return Reply("", {"name": tools[0]["name"].replace(".", "_"), "arguments": arguments, "id": "c", "count": 1}), {}

            def report(self, state, call, result):
                from app.agents import Reply

                return Reply("Done.")

        return Agent()


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    return headers, project_id


def test_a_run_adds_provider_rollouts_on_its_own_key_within_its_budget(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "provider-run@example.com")
    credential_id = _ready_key(client, headers, secret="sk-provider-run-0001")
    factory = FakeFactory()
    runtime.agent_factory = factory
    try:
        options = dict(target_trajectory_count=6, event_budget=None, group_size=4, sub_domains=["onboarding_and_kyc", "consumer_credit"])
        plain = _run(client, headers, project_id, credential_id, **options).json()
        run = _run(client, headers, project_id, credential_id, provider_rollouts=2, provider_model="gpt-test", **options).json()
        assert run["config"]["provider_call_budget"] == 6 * 2 * 2
        provider = run["generation"]["episodes"]["provider"]
        added = [item for episode in run["bundle"]["episodes"] for item in episode["rollouts"] if item["policy"] == "provider:gpt-test"]
        assert provider["calls"] == 2 * len(added) and provider["rollouts"] == len(added) > 0
        assert all(item["checks"] is not None for item in added)
        assert run["generation"]["episodes"]["policies"]["provider:gpt-test"] == len(added)
        assert factory.keys == [("openai", "sk-provider-run-0001")]
        assert "sk-provider-run-0001" not in json.dumps(run)
        # Provider rollouts change no journey.
        assert [item["event_ids"] for item in run["bundle"]["trajectories"]] == [item["event_ids"] for item in plain["bundle"]["trajectories"]]

        capped = _run(client, headers, project_id, credential_id, provider_rollouts=2, provider_call_budget=4, **options).json()
        assert capped["generation"]["episodes"]["provider"]["calls"] <= 4 and capped["generation"]["episodes"]["provider"]["skipped_rollouts"] > 0
        assert capped["generation"]["episodes"]["provider"]["stopped_by"] == "budget"
        chat = client.get(f"/runs/{run['id']}/export/episodes-openai.jsonl", headers=headers, params={"allow_unaccepted": "true"}).text.splitlines()
        assert any(json.loads(line)["policy"] == "provider:gpt-test" for line in chat)
    finally:
        runtime.agent_factory = None


def test_provider_rollouts_need_a_key_and_episodes_and_demo_runs_are_capped(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "provider-rules@example.com")
    missing = _run(client, headers, project_id, None, target_trajectory_count=4, group_size=2, provider_rollouts=1)
    assert missing.status_code == 422 and "own provider key" in missing.json()["detail"]
    credential_id = _ready_key(client, headers)
    scored = _run(client, headers, project_id, credential_id, target_trajectory_count=4, group_size=2, provider_rollouts=1, consumer="decision_scoring")
    assert scored.status_code == 422 and "need episodes" in scored.json()["detail"]
    single = _run(client, headers, project_id, credential_id, target_trajectory_count=4, group_size=1, provider_rollouts=1)
    assert single.status_code == 422 and "two sequences per prompt" in single.json()["detail"]

    monkeypatch.setenv("DEMO_MAX_PROVIDER_CALLS", "10")
    demo = _auth(client, "provider-demo@example.com", "password-123", kind="demo")
    assert client.get("/quota", headers=demo).json()["demo"]["max_provider_calls"] == 10
    demo_project = _project(client, demo)
    client.post(f"/projects/{demo_project}/corpus", headers=demo, data={"kind": "paper"}, files={"upload": ("notes.md", b"Notes.", "text/markdown")})
    demo_key = _ready_key(client, demo)
    over = _run(client, demo, demo_project, demo_key, target_trajectory_count=4, group_size=2, provider_rollouts=2)
    assert over.status_code == 422 and "at most 10 provider calls" in over.json()["detail"]
    sneaky = _run(client, headers, project_id, credential_id, target_trajectory_count=4, group_size=2, provider_rollouts=1, provider_model="../files?x=")
    assert sneaky.status_code == 422


def test_a_large_run_spends_one_provider_budget_across_its_batches(client, tmp_path, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id = _study(client, "provider-large@example.com")
    credential_id = _ready_key(client, headers)
    runtime.agent_factory = FakeFactory()
    try:
        run = _run(client, headers, project_id, credential_id, target_trajectory_count=60, event_budget=None, group_size=2,
                   sub_domains=["onboarding_and_kyc", "consumer_credit"], provider_rollouts=1, provider_call_budget=20).json()
    finally:
        runtime.agent_factory = None
    provider = run["generation"]["episodes"]["provider"]
    assert run["generation"]["storage"]["batches"] > 1
    assert provider["calls"] == 20 and provider["rollouts"] == 10 and provider["skipped_rollouts"] > 0
