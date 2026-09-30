"""Questions the studio owns (Slice 21, decision 28): helpfulness, correctness, safety, and pairwise quality registered
as the platform tenant's own rubrics, worded as the engine's built-ins were before llm_inference_engine #122, asked by
name; a change of wording said beside the scores; a verdict the engine cut off still read as a score."""

import hashlib
import json

import pytest

from app import judge_rubrics, runtime
from app.judge import _verdict
from app.judge_rubrics import PLATFORM_RUBRICS
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run


class Owned(RecordingJudge):
    """A judge with a registry: each rubric's digest follows its wording, as the engine's does."""

    def __init__(self):
        super().__init__()
        self.registered, self.asked = {}, []

    def register_rubric(self, definition):
        digest = "sha256:" + hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        self.registered[definition["name"]] = definition
        return {"name": definition["name"], "digest": digest}

    def run_eval(self, **kwargs):
        self.asked.append(kwargs["rubric"])
        return super().run_eval(**{**kwargs, "rubric": kwargs["rubric"].removeprefix("trajectory_")})


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id


def _judged(client, headers, project_id, judge):
    run = _run(client, headers, project_id, _ready_key(client, headers), target_trajectory_count=8, event_budget=None).json()
    runtime.judge = judge
    response = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    return response.json()["cycles"][0]


def test_the_studio_asks_its_own_copies_of_the_questions_it_scores_by(client, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    headers, project_id = _study(client, "own-questions@example.com")
    judge = Owned()
    cycle = _judged(client, headers, project_id, judge)
    # Registered under names of their own, worded as the engine's built-ins were through the sixth review.
    assert set(judge.registered) >= {"trajectory_helpfulness", "trajectory_correctness", "trajectory_safety", "trajectory_pairwise_quality"}
    assert judge.registered["trajectory_correctness"]["system_prompt"].startswith("You are a fact-checking judge.")
    assert "one or two sentences" not in json.dumps(judge.registered)
    # Asked by those names; each verdict keeps the studio's name, and the cycle records what it was asked as.
    assert {"trajectory_helpfulness", "trajectory_correctness", "trajectory_safety", "trajectory_pairwise_quality"} <= set(judge.asked)
    assert not {"helpfulness", "correctness", "safety", "pairwise_quality"} & set(judge.asked)
    assert {row["rubric"] for row in cycle["verdicts"]} >= {"helpfulness", "correctness", "safety", "pairwise_quality"}
    rubrics = cycle["judging"]["rubrics"]
    assert rubrics["correctness"]["source"] == "platform" and rubrics["correctness"]["asked_as"] == "trajectory_correctness"
    assert rubrics["correctness"]["digest"].startswith("sha256:") and cycle["judging"]["wording_changed"] == []


def test_a_change_of_wording_is_said_beside_the_scores(client, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    headers, project_id = _study(client, "wording-changed@example.com")
    _judged(client, headers, project_id, Owned())
    # Asked the same way again, nothing is said.
    assert _judged(client, headers, project_id, Owned())["judging"]["wording_changed"] == []
    reworded = {**PLATFORM_RUBRICS["correctness"], "system_prompt": PLATFORM_RUBRICS["correctness"]["system_prompt"] + " Keep the reason short."}
    monkeypatch.setitem(judge_rubrics.PLATFORM_RUBRICS, "correctness", reworded)
    assert _judged(client, headers, project_id, Owned())["judging"]["wording_changed"] == ["correctness"]


def test_an_engine_without_a_registry_is_asked_its_own_questions_and_the_cycle_says_so(client, monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "2")
    headers, project_id = _study(client, "no-registry@example.com")
    _judged(client, headers, project_id, Owned())
    plain = RecordingJudge()
    cycle = _judged(client, headers, project_id, plain)
    assert {call["rubric"] for call in plain.calls} >= {"helpfulness", "correctness", "safety"}
    assert any("whose wording the engine sets" in note for note in cycle["judging"]["notes"])
    # The study's last cycle asked the studio's own questions, so every one of them was asked differently this time.
    assert cycle["judging"]["wording_changed"] == ["correctness", "helpfulness", "pairwise_quality", "safety"]


@pytest.mark.parametrize("status, readable", [("truncated", True), ("clean", True), ("failed", False)])
def test_a_verdict_the_engine_cut_off_keeps_its_score(status, readable):
    found = _verdict({"score": 1.0, "parse_status": status, "raw": '{"correct": true, "reason": "The steps follow'})
    assert found["readable"] is readable and found["score"] == 1.0
