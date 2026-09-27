import random

import pytest

from sectors.journeys import natural_lengths
from sectors.lifecycle import EventSpec, LifecycleSpec, Walker, need, put
from sectors.registry import get_sector


def _run(sector_id, sub_domains, **overrides):
    settings = dict(
        sub_domains=sub_domains, language="en", target_trajectory_count=100, event_budget=None, min_events=8, max_events=24,
        max_assistant_turns=4, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome", consumer="post_training",
        target_family="llm", seed="scope-test", group_size=1, materialization_cap=200,
    )
    settings.update(overrides)
    return get_sector(sector_id).generate(**settings)


def _primaries(bundle):
    events = {event.event_id: event.event_type for event in bundle.events}
    return [[events[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories if trajectory.parent_trajectory_id is None]


def test_a_walk_that_runs_out_of_legal_events_is_marked_exhausted():
    life = LifecycleSpec(
        events=(
            EventSpec("case.opened", ("d",), sets=(put("case", "status", "open"),), opening=True),
            EventSpec("case.closed", ("d",), requires=(need("case", "status", "open"),), sets=(put("case", "status", "closed"),)),
        ),
        milestones={"d": ("case.closed",)},
        object_types={"case": "case"},
    )
    path = Walker(life, allowed=life.namespace, sub_domains=["d"]).walk(random.Random(1), floor=10, cap=20)
    assert path.types == ["case.opened", "case.closed"] and path.exhausted and not path.stopped


@pytest.mark.parametrize("sector_id, scope, outcome_share", [
    ("airline", ["shopping_and_booking"], 0.88),
    ("hotel", ["booking_and_reservations"], 0.9),
])
def test_a_narrow_scope_passes_at_its_policy_rate_rather_than_failing_to_reach_the_minimum(sector_id, scope, outcome_share):
    # Booking alone runs about four events, below the minimum of eight: the journey ends when the scope has nothing more.
    sector = get_sector(sector_id)
    bundle = _run(sector_id, scope)
    assert sector.hard_checks(bundle) == []
    primaries = _primaries(bundle)
    rate = sum(sector.pack.success(path) for path in primaries) / len(primaries)
    assert abs(rate - outcome_share) < 0.1, rate
    assert all(len(path) <= 4 for path in primaries)


def test_a_journey_that_never_reaches_its_scope_is_redrawn():
    airline = get_sector("airline")
    bundle = _run("airline", ["baggage"], group_size=4, target_trajectory_count=40, min_events=6)
    milestones = set(airline.lifecycle.milestones["baggage"])
    for path in _primaries(bundle):
        ended = airline.lifecycle[path[-1]].ends_journey
        assert ended or milestones & set(path), path


def test_natural_lengths_show_where_a_minimum_cannot_be_met(client):
    lengths = natural_lengths(get_sector("airline").pack, ["shopping_and_booking"], 24)
    assert max(lengths) <= 4 and sum(lengths.values()) == 300
    found = client.get("/sectors/airline/lengths", params={"sub_domains": "shopping_and_booking,unknown", "max_events": 24}).json()
    assert found["sub_domains"] == ["shopping_and_booking"] and max(int(length) for length in found["lengths"]) <= 4
    banking = client.get("/sectors/banking/lengths", params={"sub_domains": "onboarding_and_kyc,deposits", "max_events": 24}).json()
    assert max(int(length) for length in banking["lengths"]) > 8
    assert client.get("/sectors/shipping/lengths").status_code == 404


@pytest.mark.parametrize("sector_id, failed, recovered", [
    ("insurance", ["product.viewed", "quote.started", "quote.abandoned"], None),
    ("insurance", ["policy.issued", "premium.missed", "policy.lapsed"], ["policy.issued", "premium.missed", "premium.paid"]),
    ("insurance", ["policy.issued", "policy.renewal_declined"], ["policy.issued", "policy.renewed"]),
    ("insurance", ["complaint.received", "complaint.rejected"], ["complaint.received", "complaint.resolved"]),
    ("telecom", ["fault.reported", "fault.diagnosed", "fault.closed_unresolved"], ["fault.reported", "fault.diagnosed", "fault.resolved_remotely"]),
    ("airline", ["flight.arrived", "bag.damaged"], ["flight.arrived", "bag.delayed", "bag.returned"]),
    ("airline", ["flight.arrived", "bag.delayed", "bag.lost"], None),
    ("airline", ["flight.arrived", "miles.missing"], ["flight.arrived", "miles.credited"]),
    ("hotel", ["guest.checked_out", "folio.disputed", "folio.adjusted"], ["guest.checked_out", "folio.settled"]),
])
def test_each_new_failure_outcome_misses_the_goal(sector_id, failed, recovered):
    pack = get_sector(sector_id).pack
    assert not pack.success(failed)
    if recovered:
        assert pack.success(recovered)
