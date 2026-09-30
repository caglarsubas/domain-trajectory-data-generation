"""A judge that can tell ours from theirs (Slice 16): real cases kept at calibration (decision 22), set against generated
journeys in the same form, and each judge's rate of picking the real one reported, never deciding (decision 23). A fair
comparison (Slice 19, decision 26): both sides cut to the study's scope and drawn at the data's resolution, an out-of-order
control, and a realism sample of the run's own."""

import json

import pytest

from app import runtime
from app.calibrate import REAL_CASES, _hotel_bookings
from app.evaluation import DISTINGUISHABLE, SEES_REALISM, evaluate_journeys, generated_steps, render_steps
from app.judging import sample_entries
from app.realism import CONTROLS, out_of_order, prepare, scope_of
from app.store import DbStore
from sectors.registry import get_sector
from test_calibration import CatalogueFetcher
from test_catalogue_sources import _hotel_run, _hotel_study, hotel_csv
from trajectory_contract import TrajectoryBundle

HOTEL = get_sector("hotel")
VISIBLE = {"reservation.confirmed", "reservation.cancelled", "room.assigned", "guest.checked_in", "guest.checked_out", "guest.no_show"}


def test_calibration_keeps_an_even_sample_of_real_cases_as_steps_and_hours_only(tmp_path):
    path = tmp_path / "hotels.csv"
    path.write_bytes(hotel_csv(200))
    found = _hotel_bookings(path)
    cases = found["real_cases"]
    assert len(cases) == REAL_CASES and cases == _hotel_bookings(path)["real_cases"]
    # Each step is an event and its hours since the case began, or none where the data does not time it.
    assert all(len(step) == 2 and step[0] in HOTEL.event_namespace and (step[1] is None or isinstance(step[1], float)) for case in cases for step in case)
    assert all(case[0] == ["reservation.confirmed", 0.0] for case in cases)


def test_a_study_never_shows_its_real_cases_outside_the_judge(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _hotel_study(client, "real-cases-private@example.com")
    runtime.fetcher = CatalogueFetcher(hotel_csv(200))
    client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "hotel_booking_demand"})
    projects = client.get("/projects", headers=headers).json()["data"]
    assert "real_cases" not in json.dumps(projects)
    run = _hotel_run(client, headers, project_id)
    exported = client.get(f"/runs/{run['id']}/export/samples.jsonl", headers=headers, params={"allow_unaccepted": "true"})
    assert exported.status_code == 200 and "real_cases" not in exported.text


def _bundle(sub_domains=None):
    return HOTEL.generate(sub_domains=sub_domains or list(HOTEL.sub_domains), language="en", target_trajectory_count=24, event_budget=None, min_events=4,
                          max_events=16, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
                          consumer="post_training", target_family="llm", seed="realism", group_size=1)


def _journeys(count=3):
    store = DbStore(_bundle().model_dump(mode="json"))
    return [TrajectoryBundle.model_validate(store.journey(entry["trajectory_id"])) for entry in sample_entries(store.entries(), count, "realism")]


def test_real_and_generated_journeys_are_shown_alike():
    journey = _journeys(1)[0]
    trajectory = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
    steps = generated_steps(journey, trajectory.trajectory_id, VISIBLE)
    # Cut to the events the data records, and timed from the first of them.
    assert steps and all(event in VISIBLE for event, _ in steps) and steps[0][1] == 0.0
    real = [("reservation.confirmed", 0.0), ("reservation.modified", None), ("reservation.cancelled", 240.0)]
    text = render_steps(real, untimed={"reservation.modified"})
    assert text.splitlines() == ["Journey.", "Steps:", "1. reservation.confirmed, at the start", "2. reservation.modified", "3. reservation.cancelled, 10 days after the first step"]
    # A step the data never times is untimed on the generated side too.
    ours = render_steps([("reservation.confirmed", 0.0), ("reservation.modified", 30.0)], untimed={"reservation.modified"})
    assert ours.splitlines()[-1] == "2. reservation.modified" and ours.splitlines()[:2] == text.splitlines()[:2]


def test_a_source_of_dates_is_drawn_in_whole_days_on_both_sides():
    days = render_steps([("reservation.confirmed", 0.0), ("room.assigned", 0.0), ("guest.checked_in", 24.0), ("guest.checked_out", 96.0)], set(), 24.0)
    assert days.splitlines()[2:] == ["1. reservation.confirmed, at the start", "2. room.assigned, the same day as the first step",
                                     "3. guest.checked_in, 1 day after the first step", "4. guest.checked_out, 4 days after the first step"]
    # A generated journey is timed as the data would record it: two hours across midnight are a day, twenty within one date none.
    journey = _journeys(1)[0]
    trajectory = next(item for item in journey.trajectories if item.parent_trajectory_id is None)
    by_date = generated_steps(journey, trajectory.trajectory_id, VISIBLE, 24.0)
    assert by_date and all(hours % 24 == 0 for _, hours in by_date)
    assert "hours" not in render_steps(by_date, set(), 24.0) and "minutes" not in render_steps(by_date, set(), 24.0)


def test_an_out_of_order_copy_moves_the_first_step_to_the_end_and_keeps_the_times():
    steps = [("reservation.confirmed", 0.0), ("room.assigned", 72.0), ("guest.checked_in", 72.0)]
    assert out_of_order(steps) == [("room.assigned", 0.0), ("guest.checked_in", 72.0), ("reservation.confirmed", 72.0)]


def test_real_cases_are_cut_to_the_studys_scope():
    scope = scope_of(HOTEL, ["booking_and_reservations", "modifications_and_cancellations", "arrival_and_check_in"])
    assert "guest.checked_in" in scope and "guest.checked_out" not in scope
    store = DbStore(_bundle(["booking_and_reservations", "modifications_and_cancellations", "arrival_and_check_in"]).model_dump(mode="json"))
    source = {"cases": REAL, "visible": sorted(VISIBLE), "untimed": [], "sources": ["hotels.csv"], "resolution": 24.0}
    found = prepare(source, store, HOTEL, {"sub_domains": ["booking_and_reservations", "modifications_and_cancellations", "arrival_and_check_in"]}, 6, "scope")
    assert found and all(event != "guest.checked_out" for pair in found["pairs"] + found["controls"] for event, _ in pair["real"])
    # A case left with fewer than two kinds of step says nothing, and a scope that leaves none asks nothing.
    assert prepare({**source, "cases": [[["reservation.confirmed", 0.0], ["guest.checked_out", 96.0]]]}, store, HOTEL,
                   {"sub_domains": ["booking_and_reservations", "arrival_and_check_in"]}, 6, "scope") is None


class Judge:
    """Answers pairwise questions by `pick`; passes everything else."""

    def __init__(self, pick):
        self.pick, self.asked = pick, []

    def register_rubric(self, definition):
        return {"name": definition["name"], "digest": "sha256:" + definition["name"]}

    def run_eval(self, *, rubric, judge_model=None, repeats=1, temperature=0.0, **payload):
        rubric = rubric.removeprefix("trajectory_")  # the studio asks its own copies of these rubrics (decision 28)
        if rubric == "pairwise_quality" and "recorded from a real customer" in payload["prompt"]:
            self.asked.append({"model": judge_model, "repeats": repeats, "temperature": temperature, **payload})
            score, reason = self.pick(payload["response"], payload["response_b"])
        else:
            score, reason = {"helpfulness": 5}.get(rubric, 1.0), "fine"
        verdicts = [{"score": score, "parsed": {"reason": reason}, "raw": "{}", "readable": True}] * repeats
        return {**verdicts[0], "verdicts": verdicts, "judge_model": judge_model, "duration_ms": 1}


# Real cases with a tell no generated journey here has: a check-out 23,976 hours, 999 days, after confirmation.
REAL = [[["reservation.confirmed", 0.0], ["room.assigned", 72.0], ["guest.checked_in", 72.0], ["guest.checked_out", 23976.0]]]


def _realism(size=8):
    store = DbStore(_bundle().model_dump(mode="json"))
    source = {"cases": REAL, "visible": sorted(VISIBLE), "untimed": [], "sources": ["hotels.csv"], "resolution": 0.0}
    return prepare(source, store, HOTEL, {"sub_domains": list(HOTEL.sub_domains)}, size, "cycle")


def _cycle(pick):
    judge = Judge(pick)
    result = evaluate_journeys(journeys=_journeys(), sector=HOTEL, brief="Hotel brief.", reference_quality="weak", thresholds={}, judge=judge,
                               models=["a", "b"], repeats=3, temperature=0.7, realism=_realism())
    return judge, result


def test_a_judge_that_spots_the_real_case_is_flagged_and_decides_nothing():
    def spots(first, second):
        return (1.0, "the stay runs 999 days") if "999 days" in first else (0.0, "the stay runs 999 days")

    judge, result = _cycle(spots)
    found = result["agreement"]["realism"]
    assert found["sources"] == ["hotels.csv"] and found["cases"] == 1 and found["comparisons"] == 8 and found["controls"] == CONTROLS
    for model in ("a", "b"):
        row = found["models"][model]
        assert row["picked_real"] == 1.0 >= DISTINGUISHABLE and row["distinguishable"] and row["reasons"] == ["the stay runs 999 days"]
        # It picks the real case over every out-of-order journey too, so it sees realism and its rate is a finding.
        assert row["calls"] == 16 and row["control"] == {"calls": 2 * CONTROLS, "picked_real": 1.0} and row["sees"]
    assert {(flag["kind"], flag["model"]) for flag in result["flags"] if flag["kind"] == "distinguishable"} == {("distinguishable", "a"), ("distinguishable", "b")}
    # Blind, in both orders, once at temperature 0; the real case is first in half of them.
    assert {call["repeats"] for call in judge.asked} == {1} and {call["temperature"] for call in judge.asked} == {0.0}
    assert sum("999 days" in call["response"] for call in judge.asked) == len(judge.asked) // 2
    assert not any("Journey T" in call["response"] or "Outcome:" in call["response"] for call in judge.asked)
    # It decides nothing and stays out of the journeys' scores and the controls' discrimination.
    without = evaluate_journeys(journeys=_journeys(), sector=HOTEL, brief="Hotel brief.", reference_quality="weak", thresholds={}, judge=Judge(spots),
                                models=["a", "b"], repeats=3, temperature=0.7)
    assert result["accepted"] is True and result["scores"] == without["scores"]
    assert "pairwise_quality" not in result["agreement"]["discrimination"]


def test_a_judge_that_always_picks_the_first_journey_cannot_tell_and_is_blind_to_realism():
    _, result = _cycle(lambda first, second: (1.0, "the first reads better"))
    rows = result["agreement"]["realism"]["models"]
    assert all(row["picked_real"] == 0.5 and row["control"]["picked_real"] == 0.5 < SEES_REALISM for row in rows.values())
    assert all(not row["sees"] and not row["distinguishable"] for row in rows.values())
    assert not any(flag["kind"] == "distinguishable" for flag in result["flags"])
    assert {(flag["kind"], flag["model"]) for flag in result["flags"] if flag["kind"] == "blind_to_realism"} == {("blind_to_realism", "a"), ("blind_to_realism", "b")}


def test_a_judge_that_sees_order_but_not_our_journeys_sees_realism_and_cannot_tell():
    def order(first, second):
        # Picks whichever journey starts with a confirmation, and ties when both do.
        a, b = (text.splitlines()[2].startswith("1. reservation.confirmed") for text in (first, second))
        return (0.5, "both plausible") if a == b else ((1.0 if a else 0.0), "starts where a booking starts")

    _, result = _cycle(order)
    for row in result["agreement"]["realism"]["models"].values():
        assert row["control"]["picked_real"] == 1.0 and row["sees"] and row["picked_real"] < DISTINGUISHABLE and not row["distinguishable"]
    assert not any(flag["kind"] in ("distinguishable", "blind_to_realism") for flag in result["flags"])


def test_a_cycle_sets_journeys_against_the_studys_own_real_cases(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("JUDGE_SAMPLE_SIZE", "3")
    monkeypatch.setenv("REALISM_SAMPLE", "10")
    headers, project_id = _hotel_study(client, "real-cases-cycle@example.com")
    runtime.fetcher = CatalogueFetcher(hotel_csv(200))
    client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "hotel_booking_demand"})
    run = _hotel_run(client, headers, project_id)
    judge = runtime.judge = Judge(lambda first, second: (0.5, "cannot tell"))
    response = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    assert response.status_code == 200, response.text
    cycle = response.json()["cycles"][0]
    found = cycle["agreement"]["realism"]
    assert found["sources"] and 0 < found["cases"] <= REAL_CASES and found["resolution_hours"] == 24.0
    # Its own sample of ten, apart from the three the rubrics read, and four out-of-order controls.
    assert len(cycle["sample"]) == 3 and found["comparisons"] == 10 and found["controls"] == CONTROLS
    assert all(row["picked_real"] == 0.5 for row in found["models"].values())
    # Both sides show only what the source records within the study's scope, in whole days: no checkout in a study without
    # departures, and no hours.
    shown = [line for call in judge.asked for side in ("response", "response_b") for line in call[side].splitlines()[2:]]
    assert judge.asked and all(line.split(". ", 1)[1].split(",")[0] in HOTEL.event_namespace for line in shown)
    assert not any("guest.checked_out" in line or "hours" in line or "minutes" in line for line in shown)
    assert {item["control"] for item in cycle["verdicts"] if item["rubric"] == "pairwise_quality" and item["control"]} >= {"realism", "realism:out_of_order"}


def test_a_realism_sample_of_none_asks_no_such_question(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("REALISM_SAMPLE", "0")
    headers, project_id = _hotel_study(client, "no-realism-sample@example.com")
    runtime.fetcher = CatalogueFetcher(hotel_csv(200))
    client.post(f"/projects/{project_id}/catalogue", headers=headers, json={"entry": "hotel_booking_demand"})
    run = _hotel_run(client, headers, project_id)
    judge = runtime.judge = Judge(lambda first, second: (0.5, "cannot tell"))
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert "realism" not in cycle["agreement"] and judge.asked == []


def test_a_study_without_real_cases_asks_no_such_question(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id = _hotel_study(client, "no-real-cases@example.com")
    run = _hotel_run(client, headers, project_id)
    judge = runtime.judge = Judge(lambda first, second: (0.5, "cannot tell"))
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    assert "realism" not in cycle["agreement"] and judge.asked == []
