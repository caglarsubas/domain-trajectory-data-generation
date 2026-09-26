import gzip
import json
from types import SimpleNamespace

import pytest

from app import runtime
from sectors.lifecycle import EventSpec, LifecycleSpec, Walker, need, put
from sectors.registry import get_sector
from sectors.scorers import SIGNALS, Scorer, pass_at_k
from test_api import _auth, _project, _ready_key, _run
from test_provider_rollouts import FakeFactory

BANKING = get_sector("banking")
OPEN = {"allow_unaccepted": "true"}

# A case that opens, may pause and resume, and is approved or declined.
LIFE = LifecycleSpec(
    events=(
        EventSpec("case.opened", ("d",), sets=(put("case", "status", "open"),), opening=True, dwell_hours=(1.0, 4.0)),
        EventSpec("case.paused", ("d",), requires=(need("case", "status", "open"),), sets=(put("case", "status", "paused"),), weight=0.2, dwell_hours=(1.0, 4.0)),
        EventSpec("case.resumed", ("d",), requires=(need("case", "status", "paused"),), sets=(put("case", "status", "open"),), dwell_hours=(1.0, 4.0)),
        EventSpec("case.approved", ("d",), requires=(need("case", "status", "open"),), sets=(put("case", "status", "approved"),), weight=0.8, outcome="decision", ends_journey=True),
        EventSpec("case.declined", ("d",), requires=(need("case", "status", "open"),), sets=(put("case", "status", "declined"),), weight=0.2, outcome="decision", ends_journey=True),
    ),
    milestones={"d": ("case.approved", "case.declined")},
    object_types={"case": "case"},
)
PACK = SimpleNamespace(lifecycle=LIFE, success=lambda types: bool(types) and "case.declined" not in types, goal={"en": "The case is not declined."})


@pytest.fixture()
def scorer():
    walker = Walker(LIFE, allowed=LIFE.namespace, sub_domains=["d"])
    return Scorer(PACK, walker, domains=["d"], floor=1, cap=8, decisions=True)


# Scorers


def test_a_quick_direct_approval_passes_every_signal(scorer):
    found = scorer.score(["case.opened", "case.approved"], [1.0])
    assert set(found) == set(SIGNALS)
    assert all(verdict["passed"] for verdict in found.values())
    assert found["solution_rubric"]["items"] == {"goal": 1.0, "settled": 1.0, "scope": 1.0}
    assert found["behavior_rubric"]["items"] == {"no_rework": 1.0, "prompt": 1.0}
    # The approval was the most likely step, and the better of the two outcomes.
    assert found["process_conformance"]["score"] == 1.0 and found["decision_score"]["items"] == {"decisions": 1.0, "worst": 1.0}


def test_a_slow_detour_to_a_decline_fails_each_signal_for_its_own_reason(scorer):
    found = scorer.score(["case.opened", "case.paused", "case.resumed", "case.declined"], [10.0, 10.0, 10.0])
    assert not any(verdict["passed"] for verdict in found.values())
    # Resuming returns the case to a state it had left: one step in four is rework. Every wait is slow.
    assert found["behavior_rubric"]["items"] == {"no_rework": 0.75, "prompt": 0.0}
    # Pausing and declining each had a quarter of the most likely step's share; resuming was the only step.
    assert found["process_conformance"]["items"]["typicality"] == 0.25 and found["process_conformance"]["items"]["steps"] == 2.0
    assert found["decision_score"]["items"]["worst"] == 0.0


def test_a_journey_that_stops_with_its_decision_open_is_not_a_solution(scorer):
    found = scorer.score(["case.opened"], [])
    assert found["outcome"]["passed"] and not found["solution_rubric"]["passed"]
    assert found["solution_rubric"]["items"]["settled"] == 0.0 and found["solution_rubric"]["items"]["scope"] == 0.0


def test_pass_at_k_is_the_unbiased_estimate():
    assert pass_at_k(4, 1, 1) == 0.25 and pass_at_k(4, 1, 2) == 0.5 and pass_at_k(4, 0, 4) == 0.0 and pass_at_k(4, 4, 1) == 1.0
    assert pass_at_k(5, 2, 3) == pytest.approx(0.9)
    with pytest.raises(ValueError):
        pass_at_k(2, 1, 3)


# Generation


SETTINGS = dict(
    sub_domains=["onboarding_and_kyc", "consumer_credit", "cards_and_payments"], language="en", target_trajectory_count=12, event_budget=None,
    min_events=6, max_events=18, max_assistant_turns=6, start_mode="cold", reward_mechanism="groupwise_reward_synthesis",
    consumer="evaluation", target_family="llm", corpus_text="", feedback=None, revision_notes=None, parent_bundle=None, seed="signals-test", group_size=4,
)


def test_the_signal_decides_pass_and_reward_but_draws_the_same_journeys():
    runs = {signal: BANKING.generate(**SETTINGS, signal_mechanism=signal) for signal in SIGNALS}
    first = runs["outcome"]
    for signal, bundle in runs.items():
        assert [item.event_ids for item in bundle.trajectories] == [item.event_ids for item in first.trajectories]
        rewards = bundle.generation.rewards
        assert rewards["signal"] == signal and set(rewards["signals"]) >= set(SIGNALS) - {"decision_score"}
        for sample in bundle.samples:
            for sequence in sample.sequences:
                verdicts = sequence.signals
                assert sequence.outcome == ("pass" if verdicts[signal]["passed"] else "fail")
                # MiMo's multiplicative reward: verification times the solution and behavior rubrics.
                expected = (1.0 if verdicts[signal]["passed"] else 0.0) * verdicts["solution_rubric"]["score"] * verdicts["behavior_rubric"]["score"]
                assert sequence.reward == pytest.approx(expected, abs=1e-3)
                assert sequence.solution_score == verdicts["solution_rubric"]["score"] and sequence.behavior_score == verdicts["behavior_rubric"]["score"]
        passes = [sequence.outcome == "pass" for sample in bundle.samples for sequence in sample.sequences]
        assert rewards["pass_rate"] == pytest.approx(sum(passes) / len(passes), abs=1e-3) == rewards["signals"][signal]["pass_rate"]
        assert set(rewards["signals"][signal]["pass_at_k"]) == {"1", "2", "4"}
    # The signals disagree somewhere, so choosing one changes the data.
    verdicts = {signal: [sequence.outcome for sample in bundle.samples for sequence in sample.sequences] for signal, bundle in runs.items()}
    assert len({tuple(value) for value in verdicts.values()}) > 1
    assert "decision_score" not in runs["outcome"].samples[0].sequences[0].signals
    assert "decision_score" in runs["decision_score"].samples[0].sequences[0].signals


# Runs and export


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": ("notes.md", b"Loans are decided after checks.", "text/markdown")})
    return headers, project_id


def test_an_evaluation_run_exports_tasks_with_verifiers_references_and_pass_at_k(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _study(client, "evaluation-export@example.com")
    credential_id = _ready_key(client, headers, secret="sk-evaluation-run-0001")
    runtime.agent_factory = FakeFactory()
    try:
        run = _run(client, headers, project_id, credential_id, consumer="evaluation", signal_mechanism="process_conformance", target_trajectory_count=8,
                   event_budget=None, group_size=4, sub_domains=["onboarding_and_kyc", "consumer_credit"], provider_rollouts=2, provider_model="gpt-test").json()
    finally:
        runtime.agent_factory = None
    # Evaluation runs build episodes by default: they are its agent tasks.
    assert run["bundle"]["episodes"] and run["generation"]["rewards"]["signal"] == "process_conformance"
    base = f"/runs/{run['id']}/export"
    manifest = client.get(f"{base}/manifest.json", headers=headers, params=OPEN).json()
    assert manifest["parts_for_this_run"] == ["tasks.jsonl", "evaluation.json", "domain.jsonl", "ocel.json"]
    tasks = [json.loads(line) for line in client.get(f"{base}/tasks.jsonl", headers=headers, params=OPEN).text.splitlines()]
    journeys = [task for task in tasks if task["kind"] == "journey"]
    agents = [task for task in tasks if task["kind"] == "agent_episode"]
    assert len(journeys) == len(run["bundle"]["samples"]) and len(agents) == len(run["bundle"]["episodes"]) == manifest["evaluation"]["tasks"]["agent_episode"]
    events = {event["event_id"]: event["event_type"] for event in run["bundle"]["events"]}
    trajectories = {item["trajectory_id"]: item for item in run["bundle"]["trajectories"]}
    for task in journeys:
        assert task["primary_verifier"] == "process_conformance" and task["environment"] == "environment"
        for reference in task["references"]:
            assert reference["events"] == [events[item] for item in trajectories[reference["trajectory_id"]]["event_ids"]]
        outcome = task["results"]["generator"]["outcome"]
        passes = sum(reference["signals"]["outcome"]["passed"] for reference in task["references"])
        assert outcome["attempts"] == 4 and outcome["passes"] == passes
        assert outcome["pass_at_k"] == {str(k): round(pass_at_k(4, passes, k), 4) for k in (1, 2, 4)}
    agent = agents[0]
    assert set(agent["verifiers"]) == {"format", "legality", "grounding", "decision", "report"}
    assert agent["environment"]["legal_events"] and set(agent["environment"]["responses"]) <= set(agent["environment"]["legal_events"])
    assert agent["results"]["provider:gpt-test"]["attempts"] == 2 and set(agent["results"]["provider:gpt-test"]["pass_at_k"]) == {"1", "2"}

    report = client.get(f"{base}/evaluation.json", headers=headers, params=OPEN).json()
    journey_row = report["journeys"]["by_verifier"]["outcome"]
    expected = sum(task["results"]["generator"]["outcome"]["pass_at_k"]["2"] for task in journeys) / len(journeys)
    assert journey_row["tasks"] == len(journeys) and journey_row["pass@k"]["2"] == pytest.approx(expected, abs=1e-3)
    model = report["agent_episodes"]["by_policy"]["provider:gpt-test"]
    assert model["tasks"] == len(agents) and set(model["pass@k"]) == {"1", "2"}
    # The run itself reports the model's pass@k, the same as the export's.
    assert run["generation"]["episodes"]["models"]["provider:gpt-test"]["pass_at_k"] == model["pass@k"]
    assert report["environment"]["sector"] == "banking" and any(item["event_type"] == "application.approved" for item in report["environment"]["events"])
    assert set(report["verifiers"]["journey"]) == set(SIGNALS)
    assert manifest["files"]["evaluation.json"] and manifest["evaluation"]["journeys"]["primary_verifier"] == "process_conformance"


def test_a_large_run_merges_signals_across_batches_and_exports_its_tasks(client, tmp_path, monkeypatch):
    import app.generation as generation
    from app import store

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "BATCH_SEQUENCES", 40)
    monkeypatch.setattr(generation, "BATCH_SEQUENCES", 40)
    store._read_json.cache_clear()
    headers, project_id = _study(client, "evaluation-large@example.com")
    run = _run(client, headers, project_id, None, consumer="evaluation", signal_mechanism="behavior_rubric", target_trajectory_count=30, event_budget=None,
               group_size=4, sub_domains=["onboarding_and_kyc", "consumer_credit"]).json()
    rewards = run["generation"]["rewards"]
    assert run["generation"]["storage"]["batches"] > 1 and rewards["signal"] == "behavior_rubric"
    assert rewards["signals"]["behavior_rubric"]["sequences"] == 120 and rewards["signals"]["behavior_rubric"]["pass_rate"] == rewards["pass_rate"]
    assert set(rewards["signals"]["outcome"]["pass_at_k"]) == {"1", "2", "4"}
    assert client.post(f"/runs/{run['id']}/exports", headers=headers, json={"allow_unaccepted": True}).status_code == 200
    lines = gzip.decompress(client.get(f"/runs/{run['id']}/export/tasks.jsonl", headers=headers, params=OPEN).content).decode().splitlines()
    report = json.loads(gzip.decompress(client.get(f"/runs/{run['id']}/export/evaluation.json", headers=headers, params=OPEN).content))
    assert report["tasks"]["journey"] == 30 and len(lines) == report["tasks"]["journey"] + report["tasks"]["agent_episode"]
