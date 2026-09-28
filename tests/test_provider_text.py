"""Provider-written turn text: the skeleton a model gets, the checks every turn passes, and runs that use it."""

import json
import re

import httpx
import pytest

from app import runtime
from app.agents import AgentError, ProviderAgent, TextBudget, TextWriter, _turns_from
from sectors.registry import get_sector
from sectors.turn_text import TurnPlan, check_turn, language_ok
from test_api import _auth, _project, _ready_key, _run

BANKING = get_sector("banking")
SETTINGS = dict(
    sub_domains=["onboarding_and_kyc", "deposits", "consumer_credit"], language="en", target_trajectory_count=4, event_budget=None,
    min_events=6, max_events=16, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
    consumer="post_training", target_family="llm", corpus_text="", feedback=None, revision_notes=None, parent_bundle=None, seed="text-test",
    group_size=2,
)


def _faithful(plan) -> list:
    """Each event's template sentence, reworded around its facts."""
    return [[{"event": event["event"], "text": "As an update, " + event["template"][0].lower() + event["template"][1:]} for event in turn.events] for turn in plan.turns]


class Recorder:
    """A writer that answers every sequence, faithfully unless told to spoil a turn."""

    def __init__(self, spoil=None):
        self.plans, self.spoil = [], spoil

    def __call__(self, plan):
        self.plans.append(plan)
        turns = _faithful(plan)
        if self.spoil:
            turns = self.spoil(turns, plan)
        return {"turns": turns, "written_by": "provider:test-model"}


def _assistant(bundle):
    return [segment for sample in bundle.samples for sequence in sample.sequences for context in sequence.contexts for segment in context.segments if segment.role == "assistant"]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _turn(*events, template="The account was funded (1,234.50 GBP, 3 days after the previous one).") -> TurnPlan:
    return TurnPlan(events=[{"event": name, "template": template, "value": value, "allowed": {1234.5, 3.0}} for name, value in events], template=template)


def test_a_faithful_turn_passes_and_keeps_its_facts():
    turn = _turn(("account.funded", 1234.5))
    reason, text = check_turn(turn, [{"event": "account.funded", "text": "Your account was funded with 1,234.50 GBP, 3 days after the last deposit."}], "en")
    assert reason is None and text.startswith("Your account")


@pytest.mark.parametrize(
    ("sentences", "reason"),
    [
        ([], "empty"),
        ([{"event": "account.opened", "text": "Your account was opened."}], "events"),
        ([{"event": "account.funded", "text": ""}], "empty"),
        ([{"event": "account.funded", "text": "Your account was funded with some money."}], "amount_missing"),
        ([{"event": "account.funded", "text": "Your account was funded with 1,234.50 GBP after 7 days."}], "invented_number"),
        ([{"event": "account.funded", "text": "Account AC1234 was funded with 1,234.50 GBP."}], "invented_identifier"),
        ([{"event": "account.funded", "text": "We funded 1,234.50 GBP; write to help@bank.example for more."}], "invented_identifier"),
        ([{"event": "account.funded", "text": "The account.funded step moved 1,234.50 GBP."}], "raw_event_name"),
        ([{"event": "account.funded", "text": "Hesabınıza 1.234,50 GBP yatırıldı ve işleminiz tamamlandı."}], "wrong_language"),
        ([{"event": "account.funded", "text": "1,234.50 GBP " + "and the funds were moved " * 20}], "too_long"),
        ([{"event": "account.funded", "text": "The account was funded (1,234.50 GBP, 3 days after the previous one)."}], "copied_template"),
    ],
)
def test_a_turn_that_breaks_the_skeleton_is_refused(sentences, reason):
    assert check_turn(_turn(("account.funded", 1234.5)), sentences, "en")[0] == reason


def test_events_must_come_in_order_and_turkish_numbers_count():
    turn = _turn(("account.opened", None), ("account.funded", 1234.5))
    swapped = [{"event": "account.funded", "text": "Funded with 1,234.50 GBP."}, {"event": "account.opened", "text": "The account was opened."}]
    assert check_turn(turn, swapped, "en")[0] == "events"
    turkish = [{"event": "account.opened", "text": "Hesabınız bu sabah açıldı."}, {"event": "account.funded", "text": "Hesabınıza 1.234,50 GBP yatırıldı."}]
    assert check_turn(turn, turkish, "tr")[0] is None
    assert not language_ok("Your account was opened and funded.", "tr")


# ---------------------------------------------------------------------------
# The generator with a writer
# ---------------------------------------------------------------------------


def test_the_skeleton_gives_each_turns_events_in_the_templates_order():
    writer = Recorder()
    bundle = BANKING.generate(**SETTINGS, writer=writer)
    plan = writer.plans[0]
    kinds = {event.event_id: event.event_type for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if item.trajectory_id == plan.trajectory_id)
    assert [event["event"] for turn in plan.turns for event in turn.events] == [kinds[item] for item in trajectory.event_ids]
    skeleton = plan.skeleton()
    assert skeleton["language"] == "en" and skeleton["opening"] and len(skeleton["turns"]) == len(plan.turns) <= 3
    assert set(skeleton["turns"][0][0]) <= {"event", "template", "amount", "since"}
    # The skeleton carries synthetic facts only: no object id reaches the model.
    assert not any(obj.object_id in json.dumps(skeleton) for obj in bundle.objects)


def test_written_turns_replace_the_template_and_journeys_stay_as_drawn():
    plain = BANKING.generate(**SETTINGS)
    written = BANKING.generate(**SETTINGS, writer=Recorder())
    assert [item.event_ids for item in written.trajectories] == [item.event_ids for item in plain.trajectories]
    turns = _assistant(written)
    assert all(segment.written_by == "provider:test-model" and segment.text.startswith("As an update,") for segment in turns)
    report = written.generation.text
    assert report["written"] == report["turns"] == len(turns) and report["kept_template"] == 0 and report["asked"] == report["sequences"]
    # Scored after writing: token estimates count the written text.
    plain_tokens = sum(sequence.token_estimate for sample in plain.samples for sequence in sample.sequences)
    written_tokens = sum(sequence.token_estimate for sample in written.samples for sequence in sample.sequences)
    assert written_tokens > plain_tokens
    assert all(segment.written_by is None for segment in _assistant(plain))


def test_a_turn_that_fails_keeps_its_template():
    def spoil(turns, plan):
        # The second turn of every sequence invents a reference number.
        if len(turns) > 1:
            turns[1][0]["text"] += " Your reference is RX2049."
        return turns

    plain = BANKING.generate(**SETTINGS)
    written = BANKING.generate(**SETTINGS, writer=Recorder(spoil))
    report = written.generation.text
    assert report["kept_template"] > 0 and report["reasons"] == {"invented_identifier": report["kept_template"]}
    kept = [segment for segment in _assistant(written) if segment.written_by is None]
    assert len(kept) == report["kept_template"] and set(segment.text for segment in kept) <= set(segment.text for segment in _assistant(plain))


# ---------------------------------------------------------------------------
# The provider writer
# ---------------------------------------------------------------------------


class FakeWriterAgent:
    """Answers from the skeleton in the prompt, as a careful model would."""

    def __init__(self, provider="openai", key="", model="text-model", fail=False):
        self.provider, self.key, self.model, self.fail = provider, key, model or "text-model", fail
        self.prompts = []

    def write(self, system, prompt):
        self.prompts.append((system, prompt))
        if self.fail:
            raise AgentError("openai answered 500: boom")
        skeleton = json.loads(prompt.split("\n", 1)[1])
        turns = [[{"event": event["event"], "text": "As an update, " + event["template"][0].lower() + event["template"][1:]} for event in turn] for turn in skeleton["turns"]]
        return json.dumps({"turns": turns})


def test_the_writer_spends_one_call_per_sequence_and_stops_at_its_budget():
    agent = FakeWriterAgent()
    budget = TextBudget(limit=3)
    bundle = BANKING.generate(**SETTINGS, writer=TextWriter(agent, budget, "Banking"))
    sequences = sum(len(sample.sequences) for sample in bundle.samples)
    assert budget.used == 3 == len(agent.prompts) and budget.skipped == sequences - 3
    assert budget.as_dict()["stopped_by"] == "budget"
    system, prompt = agent.prompts[0]
    assert "banking" in system and "English" in system and "State every amount exactly as given" in system
    assert prompt.startswith("Skeleton:\n")
    written = [segment for segment in _assistant(bundle) if segment.written_by == "provider:text-model"]
    assert written and bundle.generation.text["asked"] == 3


def test_repeated_provider_errors_stop_the_writer():
    budget = TextBudget(limit=50)
    bundle = BANKING.generate(**SETTINGS, writer=TextWriter(FakeWriterAgent(fail=True), budget, "Banking"))
    assert budget.errors == 3 and budget.used == 3 and budget.as_dict()["stopped_by"] == "errors"
    assert budget.last_error == "openai answered 500: boom"
    assert all(segment.written_by is None for segment in _assistant(bundle)) and bundle.generation.text["asked"] == 0


def test_the_writers_answer_is_read_in_either_shape():
    assert _turns_from('Sure: {"turns": [[{"event": "a.b", "text": "x"}]]}') == [[{"event": "a.b", "text": "x"}]]
    assert _turns_from('{"turns": [{"sentences": [{"event": "a.b", "text": "x"}]}]}') == [[{"event": "a.b", "text": "x"}]]
    assert _turns_from("no json here") is None and _turns_from('{"other": 1}') is None


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_each_provider_is_asked_for_json(provider, monkeypatch):
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        text = '{"turns": []}'
        if provider == "openai":
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        if provider == "anthropic":
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    agent = ProviderAgent(provider, "sk-writer-test-0001", "model-x", transport=httpx.MockTransport(handler))
    assert agent.write("system", "Skeleton:\n{}") == '{"turns": []}'
    if provider == "openai":
        assert seen["path"] == "/v1/chat/completions" and seen["body"]["response_format"] == {"type": "json_object"}
    elif provider == "anthropic":
        assert seen["path"] == "/v1/messages" and seen["body"]["system"] == "system"
    else:
        assert seen["path"].endswith(":generateContent") and seen["body"]["generationConfig"] == {"responseMimeType": "application/json"}
    assert "sk-writer-test-0001" not in json.dumps(seen["body"])


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class Factory:
    def __init__(self):
        self.keys, self.agents = [], []

    def __call__(self, provider, key, model):
        self.keys.append((provider, key))
        agent = FakeWriterAgent(provider, key, model)
        self.agents.append(agent)
        return agent


def _study(client, email, kind="user"):
    headers = _auth(client, email, "password-123", kind=kind)
    project_id = _project(client, headers)
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Accounts open after checks.", "text/markdown")})
    return headers, project_id


def test_a_run_writes_its_turn_text_on_its_own_key(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "provider-text@example.com")
    credential_id = _ready_key(client, headers, secret="sk-provider-text-0001")
    missing = _run(client, headers, project_id, None, target_trajectory_count=4, provider_text=True)
    assert missing.status_code == 422 and "own provider key" in missing.json()["detail"]
    factory = Factory()
    runtime.agent_factory = factory
    try:
        options = dict(target_trajectory_count=6, event_budget=None, group_size=2, provider_model="text-model")
        plain = _run(client, headers, project_id, credential_id, **options).json()
        run = _run(client, headers, project_id, credential_id, provider_text=True, **options).json()
    finally:
        runtime.agent_factory = None
    assert run["config"]["provider_text"] is True and run["config"]["provider_text_budget"] == 12
    text = run["generation"]["text"]
    assert text["provider"]["calls"] == text["asked"] == text["sequences"] == 12
    assert text["written"] == text["turns"] and text["written_share"] == 1.0 and text["written_by"] == "provider:text-model"
    assert factory.keys == [("openai", "sk-provider-text-0001")]
    assert "sk-provider-text-0001" not in json.dumps(run)
    segments = [segment for sample in run["bundle"]["samples"] for sequence in sample["sequences"] for context in sequence["contexts"] for segment in context["segments"]]
    assert all(segment["written_by"] == "provider:text-model" for segment in segments if segment["role"] == "assistant")
    assert [item["event_ids"] for item in run["bundle"]["trajectories"]] == [item["event_ids"] for item in plain["bundle"]["trajectories"]]
    assert plain["generation"]["text"] is None
    # The export carries the written text, checked for copies of uploads like any other.
    exported = client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers, params={"allow_unaccepted": "true"})
    assert exported.status_code == 200 and "As an update" in exported.text


def test_demo_runs_count_written_text_against_their_provider_calls(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_MAX_PROVIDER_CALLS", "10")
    headers, project_id = _study(client, "provider-text-demo@example.com", kind="demo")
    key = _ready_key(client, headers)
    over = _run(client, headers, project_id, key, target_trajectory_count=6, group_size=2, provider_text=True)
    assert over.status_code == 422 and "at most 10 provider calls" in over.json()["detail"]
    capped = _run(client, headers, project_id, key, target_trajectory_count=6, group_size=2, provider_text=True, provider_text_budget=8)
    assert capped.status_code == 200 and capped.json()["config"]["provider_text_budget"] == 8


def test_a_large_run_spends_one_text_budget_across_its_batches(client, tmp_path, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id = _study(client, "provider-text-large@example.com")
    credential_id = _ready_key(client, headers)
    runtime.agent_factory = Factory()
    try:
        run = _run(client, headers, project_id, credential_id, target_trajectory_count=60, event_budget=None, group_size=2,
                   provider_text=True, provider_text_budget=25).json()
    finally:
        runtime.agent_factory = None
    text = run["generation"]["text"]
    assert run["generation"]["storage"]["batches"] > 1
    assert text["provider"]["calls"] == 25 == text["asked"] and text["sequences"] == 120 and text["provider"]["skipped_sequences"] == 95
    assert text["written"] > 0 and re.fullmatch(r"provider:\S+", text["written_by"])
