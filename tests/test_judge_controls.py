"""Controls with one known defect: made and confirmed by the pack, asked of the judges, and summarized as discrimination."""

import re
from datetime import datetime

import pytest

from app import runtime
from app.controls import KINDS, TARGETS, build
from app.evaluation import _discrimination, render_journey
from app.judging import sample_entries
from app.store import DbStore
from sectors import controls as found
from sectors.registry import get_sector
from test_api import _auth, _link, _project, _ready_key, _run
from trajectory_contract import TrajectoryBundle

BANKING = get_sector("banking")
DOMAINS = ["onboarding_and_kyc", "deposits", "consumer_credit"]
CONFIG = {"sub_domains": DOMAINS, "min_events": 6, "max_events": 16, "language": "en"}
EVENT = re.compile(r"^\d+\. (\d{4}-\d\d-\d\d \d\d:\d\d) \w+ ([a-z_]+\.[a-z_]+)", re.MULTILINE)


def _journeys(count=6, seed="controls"):
    bundle = BANKING.generate(sub_domains=DOMAINS, language="en", target_trajectory_count=12, event_budget=None, min_events=6, max_events=16,
                              max_assistant_turns=4, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="decision_score",
                              consumer="post_training", target_family="llm", seed=seed, group_size=1)
    store = DbStore(bundle.model_dump(mode="json"))
    entries = sample_entries(store.entries(), count, "controls")
    return [TrajectoryBundle.model_validate(store.journey(entry["trajectory_id"])) for entry in entries]


def _types(bundle, trajectory_id):
    events = {event.event_id: event.event_type for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    return [events[item] for item in trajectory.event_ids]


# ---------------------------------------------------------------------------
# Finding defects
# ---------------------------------------------------------------------------


def test_each_defect_is_found_and_confirmed_by_the_pack():
    lifecycle = BANKING.lifecycle
    journey = _journeys(1)[0]
    primary = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
    types = _types(journey, primary.trajectory_id)
    assert found.first_illegal(lifecycle, types) is None

    missing = found.missing_step(lifecycle, types)
    removed = types[: missing["index"]] + types[missing["index"] + 1 :]
    assert found.first_illegal(lifecycle, removed) is not None and missing["removed"] == types[missing["index"]]

    hours = [24.0] * (len(types) - 1)
    slow = found.slow_wait(lifecycle, types, hours)
    assert slow["hours"] >= max(found.MIN_STRETCH_HOURS, found.STRETCH * slow["longest"]) and slow["was"] == 24.0

    walker = found.walker_for(BANKING.pack, DOMAINS)
    worse = None
    for journey in _journeys(6):
        primary = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
        worse = found.worse_choice(BANKING.pack, walker, _types(journey, primary.trajectory_id), floor=6, cap=16)
        if worse:
            break
    assert worse and worse["rival"] != worse["taken"] and worse["rival_value"] <= found.WORSE * worse["taken_value"]
    assert found.missing_step(lifecycle, types[:2]) is None and found.slow_wait(lifecycle, types[:1], []) is None


def test_controls_are_flawed_copies_that_read_like_their_originals():
    journeys = _journeys()
    controls = build(journeys, BANKING, CONFIG)
    originals = {next(t for t in j.trajectories if t.parent_trajectory_id is None).trajectory_id: j for j in journeys}
    # Every sampled journey gets every control the pack confirms for it, in a fixed order (decision 19).
    by_journey = {}
    for control in controls:
        by_journey.setdefault(control["trajectory_id"], []).append(control["kind"])
    assert set(by_journey) == set(originals)
    assert all(kinds == [kind for kind in KINDS if kind in kinds] for kinds in by_journey.values())
    assert all("reversed" in kinds and "slow_wait" in kinds for kinds in by_journey.values())
    assert {kind for kinds in by_journey.values() for kind in kinds} == set(KINDS)
    for control in controls:
        copy, tid = control["bundle"], control["trajectory_id"]
        assert control["rubrics"] == TARGETS[control["kind"]] and control["detail"]
        # A missing step and reversed events break the pack's rules; the others keep them, so their defect is the only one.
        assert bool(BANKING.hard_checks(copy)) == (control["kind"] in ("missing_step", "reversed"))
        text, _ = render_journey(copy, tid, 24000)
        original, _ = render_journey(originals[tid], tid, 24000)
        assert text.split("\n")[0] == original.split("\n")[0] and "Sample text:" in text and text != original
        # The copy's narration follows its own events, so the text does not give the defect away or contradict it.
        control_types = _types(copy, tid)
        assert [match[1] for match in EVENT.findall(text)] == control_types
    missing = next(control for control in controls if control["kind"] == "missing_step")
    worse = next(control for control in controls if control["kind"] == "worse_choice")
    assert _types(worse["bundle"], worse["trajectory_id"])[-1] in worse["detail"]
    assert missing["reference"] is not None


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def _defects(text: str) -> tuple[bool, bool]:
    """What a careful judge sees: a step the pack's rules forbid, and a wait five times longer than its step ever takes."""
    events = EVENT.findall(text)
    names = [name for _, name in events]
    illegal = found.first_illegal(BANKING.lifecycle, names) is not None
    times = [datetime.strptime(when, "%Y-%m-%d %H:%M") for when, _ in events]
    slow = False
    for name, earlier, later in zip(names[1:], times, times[1:]):
        spec = BANKING.lifecycle[name]
        longest = max(spec.dwell_hours[1], (spec.cycle_hours or (0.0, 0.0))[1])
        slow = slow or (later - earlier).total_seconds() / 3600 > 5 * max(longest, 1.0)
    return illegal, slow


class SeeingJudge:
    def register_rubric(self, definition):
        return {"name": definition["name"], "digest": "sha256:" + definition["name"]}

    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        illegal, slow = _defects(payload["response"])
        if rubric == "pairwise_quality":
            other = _defects(payload["response_b"])
            score = 0.5 if any((illegal, slow)) == any(other) else (0.0 if any((illegal, slow)) else 1.0)
        elif rubric == "helpfulness":
            score = 2 if illegal or slow else 5
        elif rubric in ("correctness", "process_conformance"):
            score = 0.0 if illegal else 1.0
        else:
            score = 1.0
        verdicts = [{"score": score, "parsed": {"reason": rubric}, "raw": "{}", "readable": True}] * repeats
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 1}


class BlindJudge(SeeingJudge):
    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        score = {"helpfulness": 5, "pairwise_quality": 1.0}.get(rubric, 1.0)
        verdicts = [{"score": score, "parsed": {"reason": "fine"}, "raw": "{}", "readable": True}] * repeats
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 1}


def _study(client, email):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    return headers, project_id, _ready_key(client, headers)


@pytest.fixture()
def four_journeys(monkeypatch):
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "4")


def _cycle(client, email, judge):
    headers, project_id, credential_id = _study(client, email)
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=12, event_budget=None, sub_domains=DOMAINS,
               signal_mechanism="decision_score").json()
    runtime.judge = judge
    response = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    return response.json()["cycles"][0]


def test_a_judge_that_sees_the_defects_scores_the_controls_lower(client, four_journeys):
    cycle = _cycle(client, "seeing-judge@example.com", SeeingJudge())
    controls = [item for entry in cycle["sample"] for item in entry.get("controls", [])]
    assert {item["kind"] for item in controls} >= {"missing_step", "slow_wait", "pairwise:missing_step"}
    discrimination = cycle["agreement"]["discrimination"]
    for rubric in ("correctness", "helpfulness"):
        for model, row in discrimination[rubric].items():
            assert row["rate"] == 1.0 and row["blind"] is False and row["controls"] >= 1
    missing = discrimination["correctness"]["qwen3.6:27b"]["kinds"]["missing_step"]
    assert missing["lower"] == missing["controls"] >= 1 and (missing["original"], missing["control"]) == (1.0, 0.0)
    # Every sampled journey has its reversed copy, and a judge that sees caught them all.
    backwards = discrimination["correctness"]["qwen3.6:27b"]["kinds"]["reversed"]
    assert backwards["controls"] == len(cycle["sample"]) == backwards["lower"]
    # It saw both rubrics' controls, so both decide.
    deciding = cycle["agreement"]["deciding"]
    assert deciding["judge"] == "qwen3.6:27b" and deciding["code_only"] is False
    assert all(row["decides"] for row in deciding["rubrics"].values()) and set(deciding["rubrics"]) == {"helpfulness", "correctness"}
    assert all(row["accuracy"] == 1.0 and not row["blind"] for row in cycle["agreement"]["pairwise_control"].values())
    # This judge never looks at decisions, so it is blind there and only there: the flag is per rubric.
    blind = {(flag["rubric"], flag["model"]) for flag in cycle["flags"] if flag["kind"] == "blind_to_defect"}
    assert blind == {("decision_score", "qwen3.6:27b"), ("decision_score", "gemma4:26b")}
    # Controls measure the judge and decide nothing: the rule-breaking copy writes no note and flags no journey.
    assert cycle["accepted"] is True and cycle["revision_notes"] == []
    assert not any(flag["kind"] == "likely_false_negative" for flag in cycle["flags"])
    marked = [item for item in cycle["verdicts"] if item["control"]]
    assert marked and {item["control"] for item in marked} >= {"missing_step", "pairwise:missing_step"}


def test_a_judge_that_gives_everything_the_top_score_is_flagged_blind(client, four_journeys):
    cycle = _cycle(client, "blind-judge@example.com", BlindJudge())
    discrimination = cycle["agreement"]["discrimination"]
    assert all(row["rate"] == 0.0 and row["blind"] for by_model in discrimination.values() for row in by_model.values())
    # Always picking the first journey is right in one order and wrong in the other: chance, so blind.
    assert all(row["accuracy"] == 0.5 and row["blind"] for row in cycle["agreement"]["pairwise_control"].values())
    blind = {(flag["rubric"], flag["model"]) for flag in cycle["flags"] if flag["kind"] == "blind_to_defect"}
    assert ("correctness", "qwen3.6:27b") in blind and ("pairwise_quality", "gemma4:26b") in blind
    # The acceptance decision is the one the judge would make without controls.
    assert cycle["accepted"] is True


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _v(rubric, score, *, control=None, order=None, model="judge", readable=True, tid="T1"):
    return {"rubric": rubric, "score": score, "control": control, "order": order, "judge_model": model, "readable": readable, "trajectory_id": tid}


def test_discrimination_compares_each_control_with_its_original_mean():
    per = {("T1", "helpfulness", "judge"): 4.0, ("T2", "helpfulness", "judge"): 4.0, ("T1", "correctness", "judge"): 1.0}
    verdicts = [
        _v("helpfulness", 2, control="missing_step"), _v("helpfulness", 5, control="missing_step"),  # mean 3.5, lower
        _v("helpfulness", 4, control="slow_wait", tid="T2"),  # equal: not lower
        _v("correctness", 1.0, control="missing_step"),  # not lower
        _v("correctness", 0.0, control="missing_step", readable=False),  # unreadable: left out
        _v("pairwise_quality", 1.0, control="pairwise:missing_step", order="ab"),
        _v("pairwise_quality", 0.5, control="pairwise:missing_step", order="ba"),
    ]
    flags = []
    discrimination, pairwise = _discrimination(verdicts, per, flags)
    assert discrimination["helpfulness"]["judge"]["controls"] == 2 and discrimination["helpfulness"]["judge"]["rate"] == 0.5
    assert discrimination["helpfulness"]["judge"]["blind"] is False
    assert discrimination["correctness"]["judge"] == {"controls": 1, "lower": 0, "kinds": {"missing_step": {"controls": 1, "lower": 0, "original": 1.0, "control": 1.0}}, "rate": 0.0, "blind": True}
    assert pairwise["judge"] == {"calls": 2, "accuracy": 0.75, "blind": False}
    assert flags == [{"kind": "blind_to_defect", "trajectory_id": None, "rubric": "correctness", "model": "judge"}]
