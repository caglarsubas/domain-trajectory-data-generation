"""Calibration beyond one step (Slice 13, decision 15): next-step shares after the last two events wherever at least
PRIOR_STRENGTH observations back them, and after the last event otherwise."""

import io
import zipfile

import pytest

from app.calibrate import _on_time
from sectors.calibration import PRIOR_STRENGTH, Calibration, build, merge, pair, representativeness, steps_of, triples_of
from sectors.registry import get_sector
from test_catalogue_sources import bts_zip

AIRLINE = get_sector("airline")
OPTIONS = [("c", 1.0), ("d", 1.0)]


def _calibration(cases) -> Calibration:
    return build(([(name, None) for name in case] for case in cases), source="test")


def test_pairs_count_each_step_after_its_two_events_from_the_start():
    calibration = _calibration([["a", "b", "c"]] * 3 + [["a", "a", "b", "d"]])
    # A repeat of the same event is not a new step, as for the next-step counts.
    assert calibration.pairs[pair(None, "a")] == {"b": 4} and calibration.pairs["a>b"] == {"c": 3, "d": 1}
    assert Calibration.from_dict(calibration.as_dict()).pairs == calibration.pairs
    assert merge([calibration, calibration]).pairs["a>b"] == {"c": 6, "d": 2}


def test_shares_follow_the_last_two_events_where_enough_data_backs_them():
    # After b, c and d are even; after a then b, c always follows, and after x then b, d does.
    calibration = _calibration([["a", "b", "c"]] * 40 + [["x", "b", "d"]] * 40)
    after_a, after_x = dict(calibration.reweight("b", OPTIONS, "a")), dict(calibration.reweight("b", OPTIONS, "x"))
    assert after_a["c"] > 1.5 > after_a["d"] and after_x["d"] > 1.5 > after_x["c"]
    # The last event alone cannot tell them apart.
    alone = dict(calibration.reweight("b", OPTIONS))
    assert alone["c"] == pytest.approx(alone["d"])
    # Below PRIOR_STRENGTH observations among the options, a pair falls back to the last event.
    thin = _calibration([["a", "b", "c"]] * (int(PRIOR_STRENGTH) - 1) + [["x", "b", "d"]] * 60)
    assert thin.reweight("b", OPTIONS, "a") == thin.reweight("b", OPTIONS)
    backed = _calibration([["a", "b", "c"]] * int(PRIOR_STRENGTH) + [["x", "b", "d"]] * 60)
    assert backed.reweight("b", OPTIONS, "a") != backed.reweight("b", OPTIONS)


def test_the_context_is_read_over_the_events_the_data_contains():
    calibration = _calibration([["a", "b", "c"]] * 30)
    # An event the data never contains leaves no trace, and a repeat is not a new step.
    assert calibration.context(["a", "loyalty", "b"]) == ("b", "a") and calibration.context(["a", "b", "b"]) == ("b", "a")
    # Before any event the data contains, the data has recorded nothing, so its next event is drawn as its journeys start.
    assert calibration.context(["offer", "order"]) == (None, None)
    assert dict(calibration.reweight(None, [("a", 1.0), ("b", 1.0)]))["a"] > 1.5


def test_pairs_through_events_a_run_leaves_out_fall_back_to_the_last_event():
    calibration = _calibration([["a", "b", "c"]] * 30 + [["a", "e", "c"]] * 30)
    seen = calibration.projected({"a", "b", "c"})
    assert "a>b" in seen.pairs and "a>e" not in seen.pairs and pair(None, "a") in seen.pairs


def test_second_order_divergence_shows_a_conditional_gap_the_next_step_shares_hide():
    data = _calibration([["a", "b", "c"]] * 30 + [["x", "b", "d"]] * 30)
    same = [["a", "b", "c"]] * 30 + [["x", "b", "d"]] * 30
    # The same next-step shares after b, with the conditions swapped.
    swapped = [["a", "b", "d"]] * 30 + [["x", "b", "c"]] * 30
    right = representativeness(data, steps_of(same), triples_of(same))
    wrong = representativeness(data, steps_of(swapped), triples_of(swapped))
    assert right["next_step_divergence"] == wrong["next_step_divergence"] == 0.0
    assert right["second_order_divergence"] == 0.0 and wrong["second_order_divergence"] == 0.5
    assert representativeness(data, steps_of(same))["second_order_divergence"] is None


def _flights(tmp_path, repeat=40) -> dict:
    """The BTS fixture, forty times over: 1,000 delayed flights, 80% of them arriving late."""
    archive = zipfile.ZipFile(io.BytesIO(bts_zip()))
    lines = archive.read(archive.namelist()[0]).decode().splitlines()
    path = tmp_path / "ontime.csv"
    path.write_text("\n".join([lines[0]] + lines[1:] * repeat) + "\n")
    return _on_time(path)


def _late_share(calibration) -> tuple[int, float]:
    bundle = AIRLINE.generate(
        sub_domains=["check_in_and_boarding", "disruption_and_compensation"], language="en", target_trajectory_count=600, event_budget=None,
        min_events=4, max_events=30, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
        consumer="post_training", target_family="llm", seed="late", group_size=1, materialization_cap=600, calibration=calibration,
    )
    events = {event.event_id: event.event_type for event in bundle.events}
    paths = [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories if trajectory.parent_trajectory_id is None]
    delayed = [path for path in paths if "flight.delayed" in path and {"flight.arrived", "flight.arrived_late"} & set(path)]
    return len(delayed), sum("flight.arrived_late" in path for path in delayed) / len(delayed)


def test_a_calibrated_airline_run_lands_delayed_flights_late_as_the_data_does(tmp_path):
    found = _flights(tmp_path)
    assert found["outcomes"]["arrived_late_when_delayed"] == 0.8
    delayed, calibrated = _late_share(found["calibration"])
    _, plain = _late_share(None)
    # The last event alone gave the share of all flights arriving late, 20% here; the last two give the delayed ones'.
    assert delayed >= 40 and abs(calibrated - 0.8) <= 0.1 and plain < 0.3
    one_step = {**found["calibration"], "pairs": {}}
    assert _late_share(one_step)[1] < 0.4
