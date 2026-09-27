"""Repeated judgments, rubrics registered with the engine, and the judge scored against the code scorers."""

import json

import httpx
import pytest

from app import runtime
from app.evaluation import summarize
from app.judge import InferenceEngineClient, JudgeUnavailable, RubricsUnsupported
from app.judge_rubrics import CODE_RUBRICS, DECISION_SCORE, PROCESS_CONFORMANCE
from app.settings import SettingsError, load_settings
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run

PRIMARY, SECOND = "qwen3.8:27b", "gemma4:26b"


def _client(handler):
    return InferenceEngineClient(
        base_url="http://engine.test",
        api_key="sk-tenant-secret",
        tenant="domain-trajectory-data-generation",
        org_id="org-trajdata",
        key_id="domain-trajectory-data-generation-primary",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def _engine_verdict(score, status="clean"):
    return {"score": score, "parsed": {"score": score, "reason": "r"}, "raw": "{}" if status != "failed" else "", "parse_status": status}


# ---------------------------------------------------------------------------
# Engine client
# ---------------------------------------------------------------------------


def test_repeats_ask_the_engine_for_n_verdicts_above_temperature_zero():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        verdicts = [_engine_verdict(1.0), _engine_verdict(0.0, "failed"), _engine_verdict(0.75)]
        return httpx.Response(200, json={"judge_model": PRIMARY, "verdict": verdicts[0], "verdicts": verdicts, "rubric_digest": "sha256:abc", "duration_ms": 9})

    client = _client(handler)
    result = client.run_eval(rubric="process_conformance", prompt="p", response="r", repeats=3, temperature=0.7)
    assert seen["body"]["n"] == 3 and seen["body"]["temperature"] == 0.7 and seen["body"]["seed"] == 0
    assert [item["score"] for item in result["verdicts"]] == [1.0, 0.0, 0.75]
    assert [item["readable"] for item in result["verdicts"]] == [True, False, True]
    assert result["score"] == 1.0 and result["rubric_digest"] == "sha256:abc"
    client.close()


def test_one_repeat_sends_no_repeat_fields_and_an_older_engine_counts_as_one():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"judge_model": PRIMARY, "verdict": _engine_verdict(4), "duration_ms": 1})

    client = _client(handler)
    once = client.run_eval(rubric="helpfulness", prompt="p", response="r")
    assert "n" not in seen["body"] and "temperature" not in seen["body"]
    assert len(once["verdicts"]) == 1 and once["rubric_digest"] is None
    # An engine that ignores `n` still answers with one verdict, and that is one repeat.
    assert len(client.run_eval(rubric="helpfulness", prompt="p", response="r", repeats=3, temperature=0.7)["verdicts"]) == 1
    client.close()


def test_registering_a_rubric_posts_its_definition_and_returns_the_digest():
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(201, json={**seen["body"], "source": "tenant", "digest": "sha256:d1"})

    client = _client(handler)
    assert client.register_rubric(PROCESS_CONFORMANCE) == {"name": "process_conformance", "digest": "sha256:d1"}
    assert seen["path"] == "/v1/evals/rubrics" and seen["body"] == PROCESS_CONFORMANCE
    assert seen["auth"] == "Bearer sk-tenant-secret"
    client.close()


@pytest.mark.parametrize("status", [404, 405])
def test_an_engine_without_tenant_rubrics_says_so(status):
    client = _client(lambda request: httpx.Response(status, json={"detail": "Method Not Allowed", "error": {"code": "invalid_request_error"}}))
    with pytest.raises(RubricsUnsupported, match="does not accept rubrics"):
        client.register_rubric(DECISION_SCORE)
    client.close()


def test_a_reserved_name_is_an_engine_failure_not_a_missing_feature():
    detail = {"message": "'safety' is a built-in rubric", "type": "rubric_name_reserved"}
    client = _client(lambda request: httpx.Response(409, json={"detail": detail, "error": detail}))
    with pytest.raises(JudgeUnavailable) as caught:
        client.register_rubric({**DECISION_SCORE, "name": "safety"})
    assert not isinstance(caught.value, RubricsUnsupported)
    assert "409" in caught.value.message and "built-in" in caught.value.message
    client.close()


def test_typed_engine_errors_become_plain_explanations():
    too_long = {"type": "context_length_exceeded", "message": "too long", "requested_tokens": 40000, "context_window": 32768}
    client = _client(lambda request: httpx.Response(400, json={"detail": too_long, "error": too_long}))
    with pytest.raises(JudgeUnavailable, match="40000 tokens of a 32768-token window. Lower JUDGE_PROMPT_TOKENS") as caught:
        client.run_eval(rubric="helpfulness", prompt="p", response="r")
    assert caught.value.status == 502
    client.close()

    slow = {"type": "generation_timeout", "message": "slow", "timeout_seconds": 240}
    client = _client(lambda request: httpx.Response(504, json={"detail": slow, "error": slow}))
    with pytest.raises(JudgeUnavailable, match="timed out after 240 seconds") as caught:
        client.run_eval(rubric="helpfulness", prompt="p", response="r")
    assert caught.value.status == 504
    client.close()


def test_deleting_a_rubric():
    answers = iter([httpx.Response(204), httpx.Response(404, json={"detail": "unknown rubric"})])
    client = _client(lambda request: next(answers))
    assert client.delete_rubric("study_x") is True
    assert client.delete_rubric("study_x") is False
    client.close()


def test_repeat_settings(monkeypatch):
    assert (load_settings().judge_repeats, load_settings().judge_temperature) == (3, 0.7)
    monkeypatch.setenv("JUDGE_REPEATS", "9")
    assert load_settings().judge_repeats == 5
    monkeypatch.setenv("JUDGE_REPEATS", "0")
    assert load_settings().judge_repeats == 1
    monkeypatch.setenv("JUDGE_TEMPERATURE", "0")
    with pytest.raises(SettingsError, match="above 0"):
        load_settings()


# ---------------------------------------------------------------------------
# A cycle with repeats and the code-comparison rubrics
# ---------------------------------------------------------------------------


class RepeatingJudge:
    """Answers every call with as many verdicts as asked. The second repeat of helpfulness disagrees with the
    others; the judge calls every journey conforming and every decision poor."""

    def __init__(self):
        self.calls = []
        self.registered = []

    def register_rubric(self, definition):
        self.registered.append(definition)
        return {"name": definition["name"], "digest": f"sha256:{definition['name']}"}

    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        self.calls.append({"rubric": rubric, "model": judge_model, "repeats": repeats, "temperature": temperature, **payload})
        base = {"helpfulness": 4, "correctness": 1, "safety": 1, "pairwise_quality": 1, "process_conformance": 1.0, "decision_score": 0.25}[rubric]
        if rubric == "pairwise_quality" and payload["response"].split("\n")[0].endswith("alternative."):
            base = 0
        verdicts = []
        for index in range(repeats):
            score = 2 if rubric == "helpfulness" and index == 1 else base
            verdicts.append({"score": score, "parsed": {"reason": f"{rubric} {index}"}, "raw": "{}", "readable": True})
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 3}


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


@pytest.fixture()
def two_journeys(monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")


def test_a_cycle_repeats_every_rubric_and_scores_the_judge_against_code(client, two_journeys):
    headers, project_id, credential_id = _study(client, "repeats@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=10, event_budget=None,
               signal_mechanism="decision_score").json()
    judge = runtime.judge = RepeatingJudge()
    body = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert body.status_code == 200, body.text
    cycle = body.json()["cycles"][0]

    assert [item["name"] for item in judge.registered] == ["decision_score", "process_conformance"]
    assert cycle["judging"] == {
        "repeats": 3,
        "temperature": 0.7,
        "rubrics": {name: {"source": "tenant", "digest": f"sha256:{name}"} for name in ("decision_score", "process_conformance")},
        "notes": [],
        "study": [],
    }
    assert {call["repeats"] for call in judge.calls} == {3} and {call["temperature"] for call in judge.calls} == {0.7}
    asked = {call["rubric"] for call in judge.calls}
    assert {"process_conformance", "decision_score"} <= asked
    conformance = [call for call in judge.calls if call["rubric"] == "process_conformance"]
    assert len(conformance) == 2 * 2 and all("reference process" in call["prompt"] for call in conformance)

    # Every call is stored once per repeat, and the registered rubrics carry their digest.
    assert len(cycle["verdicts"]) == 3 * len(judge.calls)
    assert {item["repeat"] for item in cycle["verdicts"]} == {0, 1, 2}
    assert {item["rubric_digest"] for item in cycle["verdicts"] if item["rubric"] == "decision_score"} == {"sha256:decision_score"}
    assert {item["rubric_digest"] for item in cycle["verdicts"] if item["rubric"] == "safety"} == {None}

    repeats = cycle["agreement"]["repeats"]
    # Helpfulness: 4, 2, 4 straddles the threshold of 3 on every journey; safety never moves.
    assert repeats["helpfulness"][PRIMARY] == {"calls": 2, "stable": 0, "rate": 0.0, "mean_spread": 0.4}
    assert repeats["safety"][PRIMARY]["rate"] == 1.0 and repeats["safety"][PRIMARY]["mean_spread"] == 0.0
    assert repeats["pairwise_quality"][PRIMARY]["calls"] == 2 * 2
    assert ("repeats_disagree", "helpfulness", PRIMARY) in {(flag["kind"], flag["rubric"], flag["model"]) for flag in cycle["flags"]}
    assert cycle["scores"]["helpfulness"][PRIMARY] == pytest.approx(10 / 3, abs=1e-3)

    code = cycle["agreement"]["code"]
    sample = cycle["sample"]
    passed = sum(entry["code"]["process_conformance"]["passed"] for entry in sample)
    assert code["process_conformance"]["code"]["journeys"] == 2
    assert code["process_conformance"]["models"][PRIMARY]["agree"] == passed
    assert code["process_conformance"]["models"][PRIMARY]["mean"] == 1.0
    decided = code["decision_score"]
    assert decided["judge_pass"] == 0.75 and decided["models"][PRIMARY]["mean"] == 0.25
    assert decided["models"][PRIMARY]["agree"] == 2 - decided["code"]["passed"]
    assert "process_conformance" not in cycle["scores"] and "decision_score" not in cycle["scores"]
    # The judge thinks every decision is poor, yet the comparison rubrics never decide acceptance.
    assert cycle["accepted"] is True


def test_an_unreadable_comparison_verdict_neither_blocks_acceptance_nor_reopens_the_run(client, two_journeys):
    class Unreadable(RepeatingJudge):
        def run_eval(self, *, rubric, **kwargs):
            result = super().run_eval(rubric=rubric, **kwargs)
            if rubric == "process_conformance":
                for item in result["verdicts"]:
                    item.update(readable=False, raw="")
                result.update(result["verdicts"][0])
            return result

    headers, project_id, credential_id = _study(client, "unreadable-compared@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=10, event_budget=None).json()
    runtime.judge = Unreadable()
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert ("unreadable", "process_conformance") in {(flag["kind"], flag["rubric"]) for flag in cycle["flags"]}
    assert cycle["accepted"] is True
    again = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert again.status_code == 409 and "already read" in again.json()["detail"]


def test_a_judge_without_a_registry_still_judges_and_says_what_it_skipped(client, two_journeys):
    headers, project_id, credential_id = _study(client, "no-registry@example.com")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=10, event_budget=None).json()
    runtime.judge = RecordingJudge()
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert {call["rubric"] for call in runtime.judge.calls} == {"helpfulness", "correctness", "safety", "pairwise_quality"}
    assert cycle["judging"]["rubrics"] == {}
    assert cycle["judging"]["notes"] == ["This judge does not accept rubrics. The judge did not score process_conformance."]
    assert cycle["agreement"]["code"] == {} and cycle["agreement"]["repeats"] == {}


def test_every_code_rubric_is_a_valid_engine_definition():
    for name, definition in CODE_RUBRICS.items():
        assert definition["name"] == name
        assert set(definition["expected_keys"]) >= {definition["score"]["key"]}
        assert "{response}" in definition["user_prompt_template"] and "{prompt}" in definition["user_prompt_template"]
        assert definition["user_prompt_template"].count("{") == 2


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _v(tid, rubric, score, repeat, *, readable=True, order=None, model="judge"):
    return {"trajectory_id": tid, "rubric": rubric, "score": score, "readable": readable, "judge_model": model,
            "order": order, "canary": False, "parsed": {}, "raw": "", "repeat": repeat}


def test_repeats_are_averaged_and_their_spread_reported():
    sample = [{"trajectory_id": "T1", "code": {"process_conformance": {"score": 0.4, "passed": False}}}]
    verdicts = [
        _v("T1", "helpfulness", 5, 0), _v("T1", "helpfulness", 4, 1), _v("T1", "helpfulness", 0, 2, readable=False),
        _v("T1", "correctness", 1, 0), _v("T1", "correctness", 1, 1),
        _v("T1", "safety", 1, 0), _v("T1", "safety", 0, 1),
        _v("T1", "process_conformance", 0.75, 0), _v("T1", "process_conformance", 0.25, 1),
    ]
    result = summarize(verdicts, sample, ["judge"], {})
    assert result["scores"]["helpfulness"]["judge"] == 4.5
    assert result["scores"]["safety"]["judge"] == 0.5
    repeats = result["agreement"]["repeats"]
    assert repeats["helpfulness"]["judge"] == {"calls": 1, "stable": 1, "rate": 1.0, "mean_spread": 0.2}
    assert repeats["safety"]["judge"]["stable"] == 0
    code = result["agreement"]["code"]["process_conformance"]
    # The judge's mean of 0.5 passes where the code's typicality of 0.4 fails.
    assert code["models"]["judge"] == {"journeys": 1, "mean": 0.5, "agree": 0, "rate": 0.0, "mean_gap": 0.1}
    assert {"kind": "code_disagrees", "trajectory_id": "T1", "rubric": "process_conformance", "model": "judge"} in result["flags"]
    assert result["accepted"] is False


def test_pairwise_repeats_count_each_order_as_its_own_call():
    sample = [{"trajectory_id": "T1"}]
    verdicts = [
        _v("T1", "pairwise_quality", 1.0, 0, order="ab"), _v("T1", "pairwise_quality", 1.0, 1, order="ab"),
        _v("T1", "pairwise_quality", 0.0, 0, order="ba"), _v("T1", "pairwise_quality", 1.0, 1, order="ba"),
    ]
    result = summarize(verdicts, sample, ["judge"], {})
    assert result["scores"]["pairwise_quality"]["judge"] == 0.75
    assert result["agreement"]["repeats"]["pairwise_quality"]["judge"] == {"calls": 2, "stable": 1, "rate": 0.5, "mean_spread": 0.5}
    assert result["agreement"]["order_consistency"]["judge"] == {"journeys": 1, "consistent": 0, "rate": 0.0}
