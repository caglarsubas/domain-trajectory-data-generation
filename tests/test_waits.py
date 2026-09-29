"""Waits as the data has them (Slice 18, decision 25): each timed step keeps its waits every 5% and the walker draws
between them, a calibration stored with three quantiles draws without a spike, and each step's waits are measured."""

import math
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from sectors.calibration import Calibration, build, quantiles, resolution_of, wait_fit, waits_of
from sectors.registry import get_sector

START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def skewed(count=4000, seed=0, days=False):
    """Waits like a booking's lead time: many near zero and a long tail, in hours, or in whole days when `days`."""
    rng = random.Random(seed)
    waits = []
    for _ in range(count):
        hours = rng.expovariate(1 / 2.0) if rng.random() < 0.3 else rng.lognormvariate(math.log(900), 1.1)
        waits.append(round(hours / 24) * 24.0 if days else round(hours, 3))
    return waits


def calibration_of(waits):
    return build([[("a", 0.0), ("b", hours)] for hours in waits], source="toy")


def draws(calibration, count=20000, seed=1):
    rng = random.Random(seed)
    return [calibration.dwell_hours(rng, "a", "b") for _ in range(count)]


def largest_share(values):
    return Counter(round(value, 6) for value in values).most_common(1)[0][1] / len(values)


def test_a_step_keeps_its_waits_every_five_percent_and_the_resolution_they_were_recorded_at():
    median, low, high, count, levels, resolution = quantiles([float(hours) for hours in range(1, 101)], 1.0)
    assert (median, low, high, count, resolution) == (51.0, 11.0, 90.0, 100, 1.0)
    assert len(levels) == 21 and levels[0] == 1.0 and levels[-1] == 100.0 and levels[10] == 50.5
    # A few waits still give distinct levels, read between neighbouring samples.
    assert len(set(quantiles([1.0, 3.0, 7.0, 20.0, 50.0])[4])) == 21
    assert resolution_of([0.0, 24.0, 72.0]) == 24.0 and resolution_of([0.0, 5.0, 26.0]) == 1.0 and resolution_of([0.5, 26.0]) == 0.0
    assert resolution_of([0.0, 0.0]) == 0.0
    assert calibration_of(skewed(days=True)).dwell["a>b"][5] == 24.0 and calibration_of(skewed()).dwell["a>b"][5] == 0.0


def test_draws_follow_the_data_with_no_wait_piled_on_one_value():
    waits = skewed()
    calibration = calibration_of(waits)
    median, _, high, _, levels, _ = calibration.dwell["a>b"]
    drawn = draws(calibration)
    # A log-normal fitted to three quantiles and clamped at twice the 90th percentile put a tenth or more of these on one value.
    assert largest_share(drawn) < 0.01
    assert 0.08 <= sum(value > high for value in drawn) / len(drawn) <= 0.12
    assert levels[0] <= min(drawn) and max(drawn) <= levels[-1]
    assert abs(sorted(drawn)[len(drawn) // 2] / median - 1) < 0.1


def test_a_source_that_records_dates_is_drawn_across_the_day_each_wait_stands_for():
    calibration = calibration_of(skewed(days=True))
    levels = calibration.dwell["a>b"][4]
    drawn = draws(calibration)
    # A fifth of the recorded waits are exactly zero or one day; the draws spread them instead of repeating them.
    assert largest_share(drawn) < 0.01
    assert sum(value % 24 for value in drawn) > 0 and min(drawn) >= 0 and max(drawn) <= levels[-1] + 12


def test_a_calibration_stored_with_three_quantiles_draws_within_bounded_tails():
    waits = skewed()
    full = calibration_of(waits)
    stored = Calibration.from_dict({**full.as_dict(), "dwell": {"a>b": list(full.dwell["a>b"][:4])}})
    median, low, high, _ = stored.dwell["a>b"]
    drawn = draws(stored)
    assert largest_share(drawn) < 0.01
    assert 0.08 <= sum(value > high for value in drawn) / len(drawn) <= 0.12
    assert max(0.0, 2 * low - median) <= min(drawn) and max(drawn) <= high + (high - median)


def test_a_step_the_data_cannot_time_keeps_the_packs_range():
    zero = calibration_of([0.0] * 50 + [24.0] * 10)
    assert zero.dwell["a>b"][0] == 0 and zero.dwell_hours(random.Random(0), "a", "b") is None
    stored = Calibration.from_dict({"dwell": {"a>b": [0.0, 0.0, 5.0, 60]}})
    assert stored.dwell_hours(random.Random(0), "a", "b") is None
    assert calibration_of(skewed()).dwell_hours(random.Random(0), "a", "c") is None


def journeys_with(waits):
    return [[("a", START), ("b", START + timedelta(hours=hours))] for hours in waits]


def test_each_timed_step_is_measured_against_the_data():
    calibration = calibration_of(skewed())
    close = wait_fit(calibration, waits_of(journeys_with(draws(calibration, 3000)), calibration))
    row = close["steps"][0]
    assert close["timed_steps"] == 1 and row["step"] == "a>b" and row["drawn"] == 3000 and row["cases"] == 4000
    assert close["distance"] < 0.1 and 0.07 <= row["above_p90"] <= 0.13
    far = wait_fit(calibration, waits_of(journeys_with([value * 10 for value in draws(calibration, 3000)]), calibration))
    assert far["distance"] > 1.5 and far["steps"][0]["above_p90"] > 0.5
    # Too few generated waits say nothing, and a step the data cannot time is not the data's to judge.
    assert wait_fit(calibration, waits_of(journeys_with([1.0] * 5), calibration)) is None
    untimed = calibration_of([0.0] * 50)
    assert wait_fit(untimed, waits_of(journeys_with([3.0] * 100), untimed)) is None


def test_a_wait_is_read_as_the_data_records_it():
    by_date = calibration_of(skewed(days=True))
    # Two hours that cross midnight are a day apart by date, and twenty hours within one date are none.
    late = [[("a", datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)), ("b", datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc))]]
    early = [[("a", datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)), ("b", datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc))]]
    assert list(waits_of(late, by_date)["a>b"]) == [int(math.log1p(24.0) / 0.02)]
    assert list(waits_of(early, by_date)["a>b"]) == [0]
    # Repeats of an event and steps the data never timed are not counted.
    assert waits_of([[("a", START), ("a", START), ("c", START)]], by_date) == {}


def test_a_calibrated_run_reports_its_waits():
    sector = get_sector("banking")
    rng = random.Random(3)
    sequences = [
        [("application.started", 0.0), ("application.submitted", submitted := rng.lognormvariate(math.log(2), 1.0)),
         ("kyc.started", submitted + rng.lognormvariate(math.log(200), 0.8))]
        for _ in range(400)
    ]
    calibration = build(sequences, source="toy log")
    bundle = sector.generate(sub_domains=["onboarding_and_kyc", "consumer_credit"], language="en", target_trajectory_count=300, materialization_cap=300,
                             event_budget=None, min_events=4, max_events=14, max_assistant_turns=3, start_mode="warm", reward_mechanism="binary_outcome",
                             signal_mechanism="outcome", consumer="post_training", target_family="llm", seed="waits", group_size=1,
                             calibration=calibration.as_dict())
    waits = bundle.generation.quality["representative"]["waits"]
    steps = {row["step"]: row for row in waits["steps"]}
    assert "application.submitted>kyc.started" in steps
    row = steps["application.submitted>kyc.started"]
    assert row["distance"] < 0.25 and 0.03 <= row["above_p90"] <= 0.2 and row["median_hours"][0] > 100
