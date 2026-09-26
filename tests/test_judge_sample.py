import re

import pytest

from app import runtime
from app.evaluation import render_journey, summarize
from app.judging import sample_entries
from test_api import _auth, _link, _project, _ready_key, _run
from trajectory_contract import TrajectoryBundle

PRIMARY, SECOND = "qwen3.8:27b", "gemma4:26b"


class ScriptedJudge:
    """The primary judge is consistent and catches the broken control journey; the second opinion is not and does not."""

    def __init__(self, journeys: int):
        self.journeys = journeys
        self.calls = []
        self.correctness_seen = {PRIMARY: 0, SECOND: 0}
        self.helpfulness_seen = {PRIMARY: 0, SECOND: 0}

    def run_eval(self, *, rubric, prompt, response, expected=None, response_b=None, judge_model=None):
        self.calls.append({"rubric": rubric, "model": judge_model, "response": response, "response_b": response_b})
        second = judge_model == SECOND
        if rubric == "helpfulness":
            self.helpfulness_seen[judge_model] += 1
            score = 2 if second and self.helpfulness_seen[judge_model] == 1 else 4
        elif rubric == "correctness":
            self.correctness_seen[judge_model] += 1
            control = self.correctness_seen[judge_model] > self.journeys
            score = (1 if second else 0) if control else 1
        elif rubric == "safety":
            score = 1
        else:
            # A = the first response shown. The primary prefers the primary journey in both orders; the second picks A always.
            first_is_primary = self.calls[-1]["response"].startswith("Journey") and "simulated alternative" not in response.split("\n")[1]
            score = 1 if second or first_is_primary else 0
        return {"score": score, "parsed": {"justification": f"{rubric} by {judge_model}"}, "raw": "{}", "judge_model": judge_model, "duration_ms": 2}


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


@pytest.fixture()
def three_journeys(monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "3")


def test_a_cycle_judges_a_sample_with_both_models_and_reports_agreement(client, three_journeys):
    headers, project_id, credential_id = _study(client, "sample-judge@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=12, event_budget=None,
               sub_domains=["onboarding_and_kyc", "deposits", "consumer_credit"]).json()
    runtime.judge = ScriptedJudge(journeys=3)
    body = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()
    cycle = body["cycles"][0]

    assert cycle["models"] == [PRIMARY, SECOND]
    assert len(cycle["sample"]) == 3 and all(entry["alternative"] for entry in cycle["sample"])
    assert {call["model"] for call in runtime.judge.calls} == {PRIMARY, SECOND}
    assert len(runtime.judge.calls) == 2 * (3 * 5 + 1) == len(cycle["verdicts"])

    scores = cycle["scores"]
    assert scores["helpfulness"] == {PRIMARY: 4.0, SECOND: pytest.approx(3.3333, abs=1e-3)}
    assert scores["pairwise_quality"] == {PRIMARY: 1.0, SECOND: 0.5}
    by_rubric = cycle["agreement"]["by_rubric"]
    assert by_rubric["helpfulness"]["journeys"] == 3 and by_rubric["helpfulness"]["agree"] == 2
    assert by_rubric["correctness"]["rate"] == 1.0
    order = cycle["agreement"]["order_consistency"]
    assert order[PRIMARY]["rate"] == 1.0 and order[SECOND]["rate"] == 0.0

    kinds = [(flag["kind"], flag["model"]) for flag in cycle["flags"]]
    assert ("models_disagree", f"{PRIMARY} vs {SECOND}") in kinds
    assert kinds.count(("order_flip", SECOND)) == 3 and ("order_flip", PRIMARY) not in kinds
    assert ("likely_false_positive", SECOND) in kinds and ("likely_false_positive", PRIMARY) not in kinds
    assert cycle["canary"]["results"] == {PRIMARY: 0.0, SECOND: 1.0}

    assert cycle["accepted"] is True
    assert cycle["headline_score"] == pytest.approx((0.8 + 1 + 1 + 1) / 4, abs=0.01)
    assert body["headline_score"] == cycle["headline_score"]
    pairwise = [item for item in cycle["verdicts"] if item["rubric"] == "pairwise_quality"]
    assert {item["order"] for item in pairwise} == {"ab", "ba"}
    assert sum(item["canary"] for item in cycle["verdicts"]) == 2
    assert {item["trajectory_id"] for item in cycle["verdicts"]} == {entry["trajectory_id"] for entry in cycle["sample"]}


def test_the_judge_reads_amounts_states_and_the_sample_text(client, three_journeys):
    headers, project_id, credential_id = _study(client, "what-judge-sees@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=12, event_budget=None,
               sub_domains=["onboarding_and_kyc", "deposits"]).json()
    runtime.judge = ScriptedJudge(journeys=3)
    client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    texts = [call["response"] for call in runtime.judge.calls if call["rubric"] == "helpfulness"]
    joined = "\n".join(texts)
    assert "Events:" in joined and "state:" in joined and "Sample text:" in joined and "Outcome:" in joined
    assert re.search(r"\d[\d,]*\.\d{2} [A-Z]{3} (credit|debit)", joined)


def test_one_model_can_judge_alone_and_the_sample_size_is_a_setting(client, monkeypatch):
    monkeypatch.setenv("INFERENCE_ENGINE_SECOND_JUDGE_MODEL", "")
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    headers, project_id, credential_id = _study(client, "one-judge@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=10, event_budget=None).json()
    runtime.judge = ScriptedJudge(journeys=2)
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert cycle["models"] == [PRIMARY]
    assert len(cycle["sample"]) == 2
    assert cycle["agreement"]["by_rubric"] == {}
    assert {call["model"] for call in runtime.judge.calls} == {PRIMARY}


class DoubtingJudge:
    def run_eval(self, *, rubric, judge_model=None, **kwargs):
        score = {"helpfulness": 4, "correctness": 0, "safety": 1, "pairwise_quality": 1}[rubric]
        if rubric == "pairwise_quality" and kwargs["response"].split("\n")[0].endswith("alternative."):
            score = 0
        return {"score": score, "parsed": {"reason": "the order looks wrong"}, "raw": "{}", "judge_model": judge_model, "duration_ms": 1}


def test_a_legal_journey_judged_incorrect_is_flagged_as_a_likely_false_negative(client, three_journeys):
    headers, project_id, credential_id = _study(client, "false-negative@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=8, event_budget=None).json()
    runtime.judge = DoubtingJudge()
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    negatives = [flag for flag in cycle["flags"] if flag["kind"] == "likely_false_negative"]
    assert len(negatives) == 3 * 2
    assert cycle["accepted"] is False
    assert any(note.startswith("correctness 0 below 0.5. the order looks wrong") for note in cycle["revision_notes"])


def test_sampling_takes_every_kind_and_outcome_before_repeating_one():
    entries = (
        [{"trajectory_id": f"a{i}", "trajectory_type": "retail journey", "outcome": "pass"} for i in range(10)]
        + [{"trajectory_id": f"b{i}", "trajectory_type": "retail journey", "outcome": "fail"} for i in range(3)]
        + [{"trajectory_id": "c0", "trajectory_type": "kyc review", "outcome": "fail"}]
    )
    picked = sample_entries(entries, 4, "seed-1")
    assert [entry["trajectory_id"][0] for entry in picked] == ["a", "b", "c", "a"]
    assert sample_entries(entries, 4, "seed-1") == picked
    other = sample_entries(entries, 4, "seed-2")
    assert {entry["trajectory_id"] for entry in other} != {entry["trajectory_id"] for entry in picked}
    assert len(sample_entries(entries, 50, "seed-1")) == len(entries)


def test_a_long_journey_is_shortened_to_the_budget_and_says_so(client):
    from trajectory_contract import banking_fixture

    bundle = banking_fixture()
    primary = next(item for item in bundle.trajectories if item.parent_trajectory_id is None)
    full, cut = render_journey(bundle, primary.trajectory_id, 100_000)
    assert cut is False
    short, cut = render_journey(bundle, primary.trajectory_id, 700)
    assert cut is True and len(short) <= 700
    assert short.startswith("Journey") and "Events:" in short
    assert len(short) < len(full)


def test_the_control_journey_breaks_the_rules_the_judge_should_see(client):
    from app.evaluation import broken_copy
    from sectors.registry import get_sector
    from trajectory_contract import banking_fixture

    bundle = banking_fixture()
    sector = get_sector("banking")
    primary = next(item for item in bundle.trajectories if item.parent_trajectory_id is None)
    assert sector.hard_checks(bundle) == []
    broken = broken_copy(bundle, primary.trajectory_id)
    assert isinstance(broken, TrajectoryBundle) and sector.hard_checks(broken)
    assert bundle.trajectories[0].event_ids != broken.trajectories[0].event_ids


def _verdict(tid, rubric, score, *, readable=True, order=None):
    return {
        "trajectory_id": tid,
        "rubric": rubric,
        "score": score if readable else None,
        "readable": readable,
        "judge_model": "judge",
        "order": order,
        "canary": False,
        "parsed": {},
        "raw": "",
    }


def _passing(tid, *, ab=True, ba=True, helpfulness=True):
    return [
        _verdict(tid, "helpfulness", 5.0, readable=helpfulness),
        _verdict(tid, "correctness", 1.0),
        _verdict(tid, "safety", 1.0),
        _verdict(tid, "pairwise_quality", 1.0, readable=ab, order="ab"),
        _verdict(tid, "pairwise_quality", 0.0, readable=ba, order="ba"),
    ]


def test_one_unreadable_pairwise_order_does_not_block_acceptance():
    sample = [{"trajectory_id": "T1"}, {"trajectory_id": "T2"}]
    result = summarize(_passing("T1") + _passing("T2", ab=False), sample, ["judge"], {})
    assert result["accepted"] is True
    assert {"kind": "unreadable", "trajectory_id": "T2", "rubric": "pairwise_quality", "model": "judge"} in result["flags"]


def test_a_journey_left_unscored_still_blocks_acceptance():
    sample = [{"trajectory_id": "T1"}, {"trajectory_id": "T2"}]
    both_orders = summarize(_passing("T1") + _passing("T2", ab=False, ba=False), sample, ["judge"], {})
    assert both_orders["accepted"] is False
    single = summarize(_passing("T1") + _passing("T2", helpfulness=False), sample, ["judge"], {})
    assert single["accepted"] is False
