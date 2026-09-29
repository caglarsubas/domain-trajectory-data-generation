"""Acceptance the judges can earn (Slice 14): a rubric decides only where its judge sees its controls (decision 18),
every sampled journey has its controls (decision 19), and a cycle judged twice by one model says so (decision 20)."""

import pytest

from app import runtime
from app.evaluation import MIN_CONTROLS, _sees, usual_waits
from app.settings import load_settings
from sectors.registry import get_sector
from test_api import RecordingJudge, _auth, _link, _project, _ready_key, _run

BANKING = get_sector("banking")
LOW = {"helpfulness": 2, "correctness": 1, "safety": 1, "pairwise_quality": 1}


@pytest.fixture()
def three_journeys(monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "3")


def _cycle(client, email, judge, **options):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    run = _run(client, headers, project_id, _ready_key(client, headers), target_trajectory_count=8, event_budget=None, **options).json()
    runtime.judge = judge
    response = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    return response.json()["cycles"][0]


def test_a_judge_blind_to_its_controls_decides_nothing_and_the_run_stands_on_the_code(client, three_journeys):
    # Helpfulness 2 for every journey and every flawed copy: this judge cannot tell them apart.
    cycle = _cycle(client, "blind-acceptance@example.com", RecordingJudge(scores=LOW, sees=False))
    deciding = cycle["agreement"]["deciding"]
    assert deciding["judge"] == load_settings().inference_judge_model == "qwen3.6:27b"
    assert deciding["code_only"] is True
    assert {rubric: row["decides"] for rubric, row in deciding["rubrics"].items()} == {"helpfulness": False, "correctness": False}
    assert deciding["rubrics"]["helpfulness"]["why"].startswith("it scored 0 of ")
    # Below its threshold, helpfulness would have rejected the run; blind, it only writes its note.
    assert cycle["accepted"] is True and any(note.startswith("helpfulness 2 below 3") for note in cycle["revision_notes"])
    assert {(flag["kind"], flag["rubric"]) for flag in cycle["flags"] if flag["kind"] == "did_not_decide"} == {
        ("did_not_decide", "helpfulness"), ("did_not_decide", "correctness")
    }


def test_a_judge_that_sees_its_controls_decides(client, three_journeys):
    cycle = _cycle(client, "seeing-acceptance@example.com", RecordingJudge(scores=LOW))
    deciding = cycle["agreement"]["deciding"]
    assert deciding["code_only"] is False and all(row["decides"] for row in deciding["rubrics"].values())
    discrimination = cycle["agreement"]["discrimination"]
    # Every sampled journey has its controls, so each rubric is measured over several of them, not one.
    assert discrimination["helpfulness"]["qwen3.6:27b"]["controls"] >= MIN_CONTROLS
    assert discrimination["correctness"]["qwen3.6:27b"]["kinds"]["reversed"]["controls"] == len(cycle["sample"]) == 3
    assert cycle["accepted"] is False and not any(flag["kind"] == "did_not_decide" for flag in cycle["flags"])


def _row(controls, lower, reversed_lower=None, reversed_controls=0):
    kinds = {"missing_step": {"controls": controls - reversed_controls, "lower": lower - (reversed_lower or 0)}}
    if reversed_controls:
        kinds["reversed"] = {"controls": reversed_controls, "lower": reversed_lower}
    return {"j": {"controls": controls, "lower": lower, "rate": lower / controls if controls else 0.0, "kinds": kinds}}


def test_a_rubric_decides_only_over_enough_controls_and_correctness_must_catch_reversed_journeys():
    assert _sees({"helpfulness": _row(2, 2)}, "helpfulness", "j") == (False, "only 2 of its controls had a readable score, fewer than 3")
    assert _sees({"helpfulness": _row(3, 2)}, "helpfulness", "j")[0] is True
    assert _sees({"helpfulness": _row(4, 1)}, "helpfulness", "j") == (False, "it scored 1 of 4 controls lower than their originals")
    assert _sees({}, "helpfulness", "j")[0] is False
    # Correctness sees its missing steps but passes the reversed journeys: it does not decide.
    assert _sees({"correctness": _row(6, 3, reversed_lower=0, reversed_controls=3)}, "correctness", "j") == (
        False, "it did not catch the journeys with their events reversed"
    )
    assert _sees({"correctness": _row(6, 5, reversed_lower=3, reversed_controls=3)}, "correctness", "j")[0] is True


class OneModel(RecordingJudge):
    """Two named judges, one model behind both, as an engine substitution group serves them."""

    def run_eval(self, **kwargs):
        return {**super().run_eval(**kwargs), "judge_model": "gemma4:26b"}


class TwoModels(RecordingJudge):
    """Each named judge served by itself."""

    def run_eval(self, **kwargs):
        return {**super().run_eval(**kwargs), "judge_model": kwargs["judge_model"]}


def test_a_cycle_judged_twice_by_one_model_says_so(client, three_journeys):
    cycle = _cycle(client, "one-model@example.com", OneModel())
    assert cycle["models"] == ["qwen3.6:27b", "gemma4:26b"]
    assert cycle["agreement"]["same_model"] == ["gemma4:26b"]
    assert cycle["agreement"]["served_by"] == {"qwen3.6:27b": ["gemma4:26b"], "gemma4:26b": ["gemma4:26b"]}
    assert ("same_model", "qwen3.6:27b and gemma4:26b") in {(flag["kind"], flag["model"]) for flag in cycle["flags"]}
    # Two distinct models raise no such flag.
    distinct = _cycle(client, "two-models@example.com", TwoModels())
    assert distinct["agreement"]["same_model"] == [] and not any(flag["kind"] == "same_model" for flag in distinct["flags"])


def test_helpfulness_is_told_each_steps_usual_wait(client, three_journeys):
    judge = RecordingJudge()
    cycle = _cycle(client, "usual-waits@example.com", judge)
    asked = [call for call in judge.calls if call["rubric"] == "helpfulness"]
    assert asked and all("Usual waits in the reference process:" in call["prompt"] for call in asked)
    # A slow-wait control's prompt gives the usual wait of the step its defect stretched.
    slow = [item for entry in cycle["sample"] for item in entry.get("controls", []) if item["kind"] == "slow_wait"]
    assert slow
    stretched = slow[0]["detail"].split("the wait before ", 1)[1].split(" ", 1)[0]
    assert any(f"- {stretched}" in call["prompt"] and call["repeats"] == 1 for call in asked)
    # The other rubrics are not given the waits.
    assert not any("Usual waits" in call["prompt"] for call in judge.calls if call["rubric"] in ("correctness", "safety"))


def test_usual_waits_follow_the_pack_and_a_repeat_waits_for_its_cycle():
    text = usual_waits(BANKING.lifecycle, ["product.viewed", "application.started", "application.submitted", "account.opened", "account.funded", "account.funded"])
    lines = text.splitlines()
    assert lines[0] == "Usual waits in the reference process:" and lines[1].startswith("- application.started: ")
    funded = BANKING.lifecycle["account.funded"]
    assert any(line.startswith("- account.funded:") for line in lines)
    assert any(line.startswith("- account.funded again:") for line in lines) == (funded.cycle_hours is not None)
    assert usual_waits(BANKING.lifecycle, ["product.viewed"]) == ""
