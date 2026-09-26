import json

import pytest

from app.documents import operations_from_text
from sectors.episodes import build_episodes, tool_name, valid_arguments
from sectors.registry import get_sector
from test_api import _auth, _project, _run
from trajectory_contract import ToolSpec

BANKING = get_sector("banking")
SETTINGS = dict(
    sub_domains=["onboarding_and_kyc", "consumer_credit"], language="en", target_trajectory_count=6, event_budget=None, min_events=4, max_events=14,
    max_assistant_turns=6, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
    target_family="llm", corpus_text="", feedback=None, revision_notes=None, parent_bundle=None, seed="episodes-test", group_size=4,
)


@pytest.fixture(scope="module")
def bundle():
    return BANKING.generate(**SETTINGS, episodes=True)


def test_each_group_becomes_an_episode_at_the_point_its_rollouts_part(bundle):
    # A group whose rollouts never part carries no decision and makes no episode.
    assert 1 <= len(bundle.episodes) <= len(bundle.samples) and bundle.generation.episodes["episodes"] == len(bundle.episodes)
    events = {event.event_id: event.event_type for event in bundle.events}
    for episode in bundle.episodes:
        primary = next(item for item in bundle.trajectories if item.trajectory_id == episode.trajectory_id)
        taken = events[primary.event_ids[episode.decision_index]]
        assert [item["event_type"] for item in episode.history] == [events[item] for item in primary.event_ids[: episode.decision_index]]
        assert taken in episode.skeleton["legal_events"] and episode.skeleton["reference"]["event"] == taken
        assert set(episode.skeleton["alternatives"]) <= set(episode.skeleton["legal_events"])
        assert {tool_name(event)[0] for event in episode.skeleton["legal_events"]} <= {tool.name for tool in episode.tools}
        assert [item.item_id for item in episode.rubric] == ["format", "legality", "grounding", "decision", "report"]
    assert BANKING.hard_checks(bundle) == []


def test_rollouts_mix_the_step_taken_its_alternatives_and_two_perturbations(bundle):
    for episode in bundle.episodes:
        policies = [item.policy for item in episode.rollouts]
        assert policies[0] == "reference" and policies[-2:] == ["perturbed:illegal", "perturbed:wrong_object"]
        reference, illegal, wrong = episode.rollouts[0], episode.rollouts[-2], episode.rollouts[-1]
        assert [turn.role for turn in reference.turns] == ["system", "user", "assistant", "tool", "assistant"]
        assert reference.turns[2].trainable and reference.turns[4].trainable and not reference.turns[3].trainable
        assert reference.turns[3].tool_result["status"] == "ok" and reference.legal
        assert illegal.turns[3].tool_result["reason"].startswith("precondition failed") and not illegal.legal
        assert illegal.action_event not in episode.skeleton["legal_events"]
        assert wrong.turns[3].tool_result["status"] == "error" and wrong.rubric_scores["grounding"] == 0.0
        assert wrong.turns[2].tool_call.name == reference.turns[2].tool_call.name
        assert illegal.reward == wrong.reward == 0.0 and reference.reward > 0
        assert sum(item.advantage for item in episode.rollouts) == pytest.approx(0.0, abs=1e-3)
    assert bundle.generation.episodes["accepted_groups"] >= 1


def test_one_operation_records_several_outcomes_through_an_argument(bundle):
    decisions = [tool for episode in bundle.episodes for tool in episode.tools if tool.name == "CustomerOffer.Execute"]
    assert decisions
    tool = decisions[0]
    assert tool.service_domain == "Customer Offer" and tool.action == "Execute"
    if len(tool.events) > 1:
        assert tool.parameters["properties"]["outcome"]["enum"] == [event.split(".", 1)[1] for event in tool.events]
    good = {key: "X" for key in tool.parameters["required"]}
    if "outcome" in good:
        good["outcome"] = tool.parameters["properties"]["outcome"]["enum"][0]
        assert not valid_arguments(tool, {**good, "outcome": "maybe"})
    assert valid_arguments(tool, good)
    assert not valid_arguments(tool, {"stranger": "X"})
    assert not valid_arguments(None, {})


def test_a_study_api_operation_shapes_the_matching_tool():
    lines = "API definition: Retail 1\\n- POST /applications/{id}/decision (decideApplication): Approve or decline an application [applications]\\n- GET /health"
    operations = operations_from_text(lines.replace("\\\\n", "\\n").replace("\\n", "\n"))
    assert operations[0] == {"method": "POST", "path": "/applications/{id}/decision", "operationId": "decideApplication", "summary": "Approve or decline an application"}
    shaped = BANKING.generate(**SETTINGS, episodes=True, operations=operations)
    matched = {tool.name: tool.http for episode in shaped.episodes for tool in episode.tools if tool.http}
    assert matched.get("CustomerOffer.Execute", {}).get("operationId") == "decideApplication"
    # Submitting shares only the noun with the decision operation, so it is not matched to it.
    assert "CustomerOffer.Update" not in matched and "CustomerOffer.Control" not in matched
    assert shaped.generation.episodes["with_api_operations"] >= 1


def test_episodes_speak_the_run_language_and_work_for_insurance():
    turkish = BANKING.generate(**{**SETTINGS, "language": "tr"}, episodes=True)
    assert "müşteri" in turkish.episodes[0].task
    insurance = get_sector("insurance")
    built = insurance.generate(**{**SETTINGS, "sub_domains": list(insurance.sub_domains)[:2]}, episodes=True)
    assert built.episodes and insurance.hard_checks(built) == []
    assert all("." in tool.name for episode in built.episodes for tool in episode.tools)


def _study(client, email):
    headers = _auth(client, email, "password-123")
    return headers, _project(client, headers)


def test_post_training_runs_carry_episodes_and_others_do_not_unless_asked(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "episodes-api@example.com")
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    options = dict(target_trajectory_count=6, event_budget=None, group_size=4, sub_domains=["onboarding_and_kyc", "consumer_credit"])
    trained = _run(client, headers, project_id, None, **options).json()
    assert trained["config"]["consumer"] == "post_training" and trained["bundle"]["episodes"]
    assert trained["generation"]["episodes"]["episodes"] == len(trained["bundle"]["episodes"])
    scored = _run(client, headers, project_id, None, consumer="decision_scoring", **options).json()
    assert scored["bundle"]["episodes"] == [] and scored["generation"]["episodes"] is None
    asked = _run(client, headers, project_id, None, consumer="evaluation", episodes=True, **options).json()
    assert asked["bundle"]["episodes"]
    off = _run(client, headers, project_id, None, episodes=False, **options).json()
    assert off["bundle"]["episodes"] == []
    # Turning episodes on or off draws the same journeys.
    assert [item["event_ids"] for item in off["bundle"]["trajectories"]] == [item["event_ids"] for item in trained["bundle"]["trajectories"]]
    journey = client.get(f"/runs/{trained['id']}/journeys/{trained['bundle']['episodes'][0]['trajectory_id']}", headers=headers).json()
    assert journey["episodes"][0]["episode_id"] == trained["bundle"]["episodes"][0]["episode_id"]


def test_episodes_export_once_per_harness_with_one_held_out(client, tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "episodes-export@example.com")
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    run = _run(client, headers, project_id, None, target_trajectory_count=6, event_budget=None, group_size=4, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    open_ = {"allow_unaccepted": "true"}
    manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers, params=open_).json()
    assert manifest["episodes"]["harnesses"] == {"formats": ["openai", "anthropic", "react"], "train": ["openai", "anthropic"], "held_out": "react"}
    assert manifest["counts"]["episodes"] == len(run["bundle"]["episodes"])
    samples = {json.loads(line)["sample_id"]: json.loads(line)["split"] for line in client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers, params=open_).text.splitlines()}
    text = client.get(f"/runs/{run['id']}/export/episodes.jsonl", headers=headers, params=open_).text
    assert hashlib.sha256(text.encode()).hexdigest() == manifest["files"]["episodes.jsonl"]
    episodes = [json.loads(line) for line in text.splitlines()]
    assert all(item["split"] == samples[item["sample_id"]] for item in episodes)
    rollouts = sum(len(item["rollouts"]) for item in episodes)
    assert manifest["counts"]["rollouts"] == rollouts

    chat = [json.loads(line) for line in client.get(f"/runs/{run['id']}/export/episodes-openai.jsonl", headers=headers, params=open_).text.splitlines()]
    assert len(chat) == rollouts
    call = chat[0]["messages"][2]["tool_calls"][0]
    assert "." not in call["function"]["name"] and json.loads(call["function"]["arguments"])
    assert chat[0]["messages"][3]["tool_call_id"] == call["id"] and chat[0]["tools"][0]["type"] == "function"
    blocks = [json.loads(line) for line in client.get(f"/runs/{run['id']}/export/episodes-anthropic.jsonl", headers=headers, params=open_).text.splitlines()]
    assert blocks[0]["messages"][1]["content"][0]["type"] == "tool_use" and blocks[0]["messages"][2]["content"][0]["type"] == "tool_result"
    react = [json.loads(line) for line in client.get(f"/runs/{run['id']}/export/episodes-react.jsonl", headers=headers, params=open_).text.splitlines()]
    assert react[0]["completion"].startswith("Thought:") and "\nAction: " in react[0]["completion"] and "Operations:" in react[0]["prompt"]
    assert {line["advantage"] is not None for line in chat + blocks + react} == {True}


def test_a_large_run_sums_its_episodes_across_batches(client, tmp_path, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id = _study(client, "episodes-large@example.com")
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    run = _run(client, headers, project_id, None, target_trajectory_count=80, event_budget=None, group_size=2, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    summary = run["generation"]["episodes"]
    assert run["generation"]["storage"]["batches"] > 1 and summary["episodes"] > 20
    assert summary["rollouts"] == sum(summary["policies"].values())
