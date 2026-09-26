import random
from datetime import timedelta
from itertools import combinations

import pytest

from sectors.banking.checks import banking_hard_checks
from sectors.banking.generate import GENERATOR_ID, generate_banking_bundle
from sectors.banking.pack import SUB_DOMAINS
from sectors.banking.spec import LIFECYCLE as BANKING_LIFECYCLE, PACK as BANKING_PACK
from sectors.insurance.spec import LIFECYCLE as INSURANCE_LIFECYCLE
from sectors.registry import get_sector, known_sectors
from trajectory_contract import banking_fixture

LIFECYCLES = {"banking": BANKING_LIFECYCLE, "insurance": INSURANCE_LIFECYCLE}


def _bundle(**overrides):
    payload = {
        "sub_domains": ["onboarding_and_kyc", "deposits"],
        "language": "en",
        "target_trajectory_count": 4,
        "event_budget": 400,
        "min_events": 8,
        "max_events": 16,
        "max_assistant_turns": 2,
        "start_mode": "cold",
        "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome",
        "consumer": "post_training",
        "target_family": "llm",
        "seed": "generator-test",
    }
    payload.update(overrides)
    return generate_banking_bundle(**payload)


def test_generated_bundle_passes_hard_checks_and_uses_the_contract():
    bundle = _bundle(target_trajectory_count=40, event_budget=800, max_events=24)
    assert banking_hard_checks(bundle) == []
    assert bundle.generation is not None
    assert bundle.generation.generator_id == GENERATOR_ID
    assert bundle.generation.primary_trajectories == 40
    assert bundle.generation.event_count <= 800
    primaries = [item for item in bundle.trajectories if item.parent_trajectory_id is None]
    assert len(primaries) == 40
    for primary in primaries:
        assert primary.generator_id == GENERATOR_ID
        assert primary.observed_or_synthetic == "synthetic"
        assert bundle.generation is not None
        length = len(primary.event_ids)
        assert 8 <= length <= 24
    alternatives = [item for item in bundle.trajectories if item.parent_trajectory_id]
    assert alternatives
    assert alternatives[0].observed_or_synthetic == "alternative"
    assert alternatives[0].parent_trajectory_id == primaries[0].trajectory_id
    for sample in bundle.samples:
        for sequence in sample.sequences:
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.trainable:
                        assert segment.role == "assistant"
                    else:
                        assert segment.role != "assistant"


def test_every_sub_domain_combination_stays_legal():
    names = list(SUB_DOMAINS)
    subsets = []
    for size in range(1, len(names) + 1):
        subsets.extend(combinations(names, size))
    for subset in subsets:
        bundle = _bundle(
            sub_domains=list(subset),
            target_trajectory_count=2,
            min_events=3,
            max_events=12,
            event_budget=None,
            seed="|".join(subset),
        )
        assert banking_hard_checks(bundle) == [], subset


def test_card_and_loan_order_survive_a_short_budget():
    cards = _bundle(sub_domains=["cards_and_payments"], min_events=1, max_events=4, event_budget=4, target_trajectory_count=3)
    credit = _bundle(
        sub_domains=["consumer_credit"],
        min_events=1,
        max_events=30,
        event_budget=None,
        target_trajectory_count=3,
        seed="credit",
    )
    assert banking_hard_checks(cards) == []
    assert banking_hard_checks(credit) == []
    assert any(event.event_type == "loan.disbursed" for event in credit.events)
    events = {event.event_id: event for event in credit.events}
    for trajectory in credit.trajectories:
        types = [events[item].event_type for item in trajectory.event_ids]
        if "loan.disbursed" in types:
            assert types.index("application.approved") < types.index("loan.disbursed")


def test_an_account_opens_only_after_its_application_is_approved():
    bundle = _bundle(sub_domains=list(SUB_DOMAINS), target_trajectory_count=64, event_budget=None, seed="approval")
    assert banking_hard_checks(bundle) == []
    events = {event.event_id: event for event in bundle.events}
    opened = 0
    for trajectory in bundle.trajectories:
        types = [events[item].event_type for item in trajectory.event_ids]
        if "account.opened" in types:
            opened += 1
            assert "application.approved" in types, types
            assert types.index("application.approved") < types.index("account.opened"), types
    assert opened > 0


def test_feedback_drop_revise_and_keep_change_the_next_bundle():
    parent = _bundle(sub_domains=["cards_and_payments", "onboarding_and_kyc"], seed="parent", target_trajectory_count=6)
    purchase = next(event for event in parent.events if event.event_type == "card.purchase_authorised")
    activation = next(event for event in parent.events if event.event_type == "card.activated")
    child = _bundle(
        sub_domains=["cards_and_payments", "onboarding_and_kyc"],
        seed="child",
        parent_bundle=parent,
        feedback=[
            {"target_type": "event", "target_id": purchase.event_id, "stance": "drop", "comment": "No purchase."},
            {"target_type": "event", "target_id": activation.event_id, "stance": "revise", "comment": "Wait a week before activation."},
            {"target_type": "event", "target_id": "account.funded", "stance": "keep", "comment": "Keep the funding."},
        ],
    )
    assert banking_hard_checks(child) == []
    assert all(event.event_type != "card.purchase_authorised" for event in child.events)
    assert any(event.event_type == "account.funded" for event in child.events)
    events = {event.event_id: event for event in child.events}
    gaps = []
    for trajectory in child.trajectories:
        if trajectory.parent_trajectory_id:
            continue
        issued = next((events[item] for item in trajectory.event_ids if events[item].event_type == "card.issued"), None)
        activated = next((events[item] for item in trajectory.event_ids if events[item].event_type == "card.activated"), None)
        if issued and activated:
            gaps.append(activated.event_time - issued.event_time)
    assert gaps
    assert min(gaps) >= timedelta(days=7)
    rendered = " ".join(segment.text for sample in child.samples for sequence in sample.sequences for context in sequence.contexts for segment in context.segments)
    assert "Wait a week before activation." in rendered


def test_language_reward_and_studio_cap():
    turkish = _bundle(language="tr", reward_mechanism="groupwise_advantage_redistribution", target_family="jev", seed="tr")
    assert any("Hesap" in sample.prompt or "hesap" in sample.prompt for sample in turkish.samples)
    assert any(sequence.advantage is not None for sample in turkish.samples for sequence in sample.sequences)
    assert all(sequence.reward is not None for sample in turkish.samples for sequence in sample.sequences)
    capped = _bundle(target_trajectory_count=10, materialization_cap=3, event_budget=None, seed="cap")
    assert capped.generation is not None
    assert capped.generation.primary_trajectories == 3
    assert capped.generation.requested_trajectories == 10
    assert capped.generation.limited_by == "studio_cap"


def test_same_seed_is_stable_and_later_sectors_stay_unregistered():
    first = _bundle(seed="same").model_dump(mode="json")
    second = _bundle(seed="same").model_dump(mode="json")
    assert first == second
    # A pack is registered once it passes the gates; airline and hotel packs follow.
    assert known_sectors() == ["banking", "insurance", "telecom"]
    assert "airline" not in known_sectors() and "hotel" not in known_sectors()


def _primaries(bundle):
    events = {event.event_id: event for event in bundle.events}
    return [
        [events[item].event_type for item in trajectory.event_ids]
        for trajectory in bundle.trajectories
        if trajectory.parent_trajectory_id is None
    ]


def _share(bundle, event_type):
    journeys = _primaries(bundle)
    return sum(event_type in journey for journey in journeys) / len(journeys)


def test_warm_corpus_weights_named_events_and_notes_still_win():
    scope = ["onboarding_and_kyc", "risk_and_compliance", "deposits"]
    common = {
        "start_mode": "warm",
        "sub_domains": scope,
        "seed": "events",
        "target_trajectory_count": 48,
        "min_events": 6,
        "max_events": 16,
        "event_budget": None,
    }
    named_text = "Public notes: kyc.document_submitted before the check passes. Customers use USD on mobile."
    named = _bundle(corpus_text=named_text, **common)

    def pooled_share(text):
        # Pooled over seeds, so the assertion measures the weighting rather than one draw.
        seeds = ("events", "events-2", "events-3")
        return sum(_share(_bundle(corpus_text=text, **{**common, "seed": seed}), "kyc.document_submitted") for seed in seeds) / len(seeds)

    assert pooled_share(named_text) > pooled_share("Customers use USD on mobile.") + 0.15
    assert any(event.currency == "USD" for event in named.events)
    assert any(event.event_type == "product.viewed" and event.channel_id == "mobile" for event in named.events)
    assert banking_hard_checks(named) == []

    without_types = _bundle(
        feedback=[
            {"target_type": "trajectory", "target_id": "kyc_review", "stance": "drop", "comment": "Skip review."},
            {"target_type": "trajectory", "target_id": "application_declined", "stance": "drop", "comment": "Skip decline."},
        ],
        corpus_text="Customers use USD on mobile.",
        **common,
    )
    kinds = {item.trajectory_type for item in without_types.trajectories if item.parent_trajectory_id is None}
    assert not kinds & {"kyc_review", "application_declined"}

    dropped = _bundle(
        start_mode="warm",
        sub_domains=["cards_and_payments", "onboarding_and_kyc"],
        corpus_text="card.issued then card.activated",
        seed="drop-issued",
        target_trajectory_count=8,
        min_events=4,
        max_events=16,
        event_budget=None,
        feedback=[{"target_type": "event", "target_id": "card.issued", "stance": "drop", "comment": "No issuance."}],
    )
    assert all(event.event_type != "card.issued" for event in dropped.events)
    assert all(event.event_type != "card.activated" for event in dropped.events)
    assert banking_hard_checks(dropped) == []


def test_a_corpus_naming_both_outcomes_never_yields_a_contradictory_journey():
    bundle = _bundle(
        start_mode="warm",
        sub_domains=["onboarding_and_kyc", "risk_and_compliance", "deposits"],
        corpus_text="The kyc failed and the application declined. Elsewhere kyc.passed and application.approved.",
        target_trajectory_count=64,
        event_budget=None,
        min_events=4,
        max_events=16,
        seed="both-outcomes",
    )
    assert banking_hard_checks(bundle) == []
    journeys = _primaries(bundle)
    for journey in journeys:
        assert not {"kyc.passed", "kyc.failed"} <= set(journey), journey
        assert not {"application.approved", "application.declined"} <= set(journey), journey
    assert any("kyc.failed" in journey for journey in journeys)
    assert any("application.approved" in journey for journey in journeys)


def test_sixty_four_journeys_hold_at_least_thirty_two_distinct_sequences():
    bundle = _bundle(sub_domains=list(SUB_DOMAINS), target_trajectory_count=64, event_budget=None, min_events=6, max_events=24, seed="diversity")
    assert len({tuple(journey) for journey in _primaries(bundle)}) >= 32


def test_replay_rejects_an_outcome_after_the_decision():
    bundle = banking_fixture()
    bundle.events[6].event_type = "kyc.failed"
    errors = banking_hard_checks(bundle)
    assert any("KYC failed outside an open KYC case" in item for item in errors)
    assert any("account funded while not active" in item for item in errors)


def test_alternatives_are_marked_simulated_and_share_the_parent_prefix():
    bundle = _bundle(target_trajectory_count=12, event_budget=None, seed="branches")
    by_id = {item.trajectory_id: item for item in bundle.trajectories}
    alternatives = [item for item in bundle.trajectories if item.parent_trajectory_id]
    assert alternatives
    for alt in alternatives:
        parent = by_id[alt.parent_trajectory_id]
        split = parent.event_ids.index(alt.branch_event_id) + 1
        assert alt.event_ids[:split] == parent.event_ids[:split]
        assert alt.causal_claim is False
        assert 0 < alt.probability < 1


def test_reviewer_text_never_becomes_trainable():
    parent = _bundle(seed="review-parent")
    event = next(item for item in parent.events if item.event_type == "kyc.started")
    child = _bundle(
        seed="review-child",
        parent_bundle=parent,
        feedback=[{"target_type": "event", "target_id": event.event_id, "stance": "revise", "comment": "Slow the checks down please."}],
        revision_notes=["correctness 0 below 0.5. Journeys look invented."],
    )
    segments = [segment for sample in child.samples for sequence in sample.sequences for context in sequence.contexts for segment in context.segments]
    trainable = " ".join(segment.text for segment in segments if segment.trainable)
    system = " ".join(segment.text for segment in segments if segment.role == "system")
    assert "Slow the checks down please." not in trainable and "Journeys look invented." not in trainable
    assert "causal counterfactual" not in trainable
    assert "Slow the checks down please." in system


@pytest.mark.parametrize("sector", ["banking", "insurance"])
def test_random_configurations_never_break_a_rule(sector):
    pack = get_sector(sector)
    rng = random.Random(f"sweep-{sector}")
    lifecycle = LIFECYCLES[sector]
    for trial in range(250):
        names = list(pack.sub_domains)
        low = rng.randint(1, 12)
        high = rng.randint(low, 30)
        bundle = pack.generate(
            sub_domains=rng.sample(names, rng.randint(1, len(names))),
            language=rng.choice(["en", "tr"]),
            target_trajectory_count=rng.randint(1, 10),
            event_budget=rng.choice([None, rng.randint(5, 300)]),
            min_events=low,
            max_events=high,
            max_assistant_turns=rng.randint(1, 6),
            start_mode=rng.choice(["cold", "warm"]),
            corpus_text=rng.choice(["", "The kyc failed. claim.denied after claim.assessed. USD on mobile."]),
            reward_mechanism="binary_outcome",
            signal_mechanism="outcome",
            consumer="post_training",
            target_family="llm",
            seed=f"{sector}-{trial}",
            group_size=rng.choice([1, 1, 2, 4, 8]),
        )
        assert pack.hard_checks(bundle) == [], (sector, trial)
        events = {event.event_id: event for event in bundle.events}
        for trajectory in bundle.trajectories:
            types = [events[item].event_type for item in trajectory.event_ids]
            assert len(types) <= high
            for name in set(types):
                assert types.count(name) <= lifecycle[name].repeat, (name, types)


def test_helpfulness_revision_adds_a_longer_journey():
    bundle = _bundle(revision_notes=["helpfulness 1 below 3. Make the journey more representative."], seed="help")
    primaries = [item for item in bundle.trajectories if item.parent_trajectory_id is None]
    assert max(len(item.event_ids) for item in primaries) > 8
    rendered = " ".join(segment.text for sample in bundle.samples for sequence in sample.sequences for context in sequence.contexts for segment in context.segments)
    assert "helpfulness" in rendered


@pytest.mark.parametrize("group_size", [1, 4])
def test_helpfulness_revision_keeps_the_outcomes_the_domain_ends_early(group_size):
    # A declined application or failed KYC ends a journey after about six events, so a raised minimum must not
    # leave those outcomes out; journeys that can go on still get the extra event.
    settings = {
        "sub_domains": ["onboarding_and_kyc", "deposits", "consumer_credit"],
        "min_events": 6,
        "max_events": 24,
        "event_budget": None,
        "target_trajectory_count": 200,
        "materialization_cap": 800,
        "group_size": group_size,
        "seed": "help-failures",
    }
    plain = _primaries(_bundle(**settings))
    revised = _primaries(_bundle(**settings, revision_notes=["helpfulness 2 below 3."]))

    def failed(journeys):
        return sum(not BANKING_PACK.success(types) for types in journeys) / len(journeys)

    assert failed(plain) > 0.05
    assert failed(revised) >= failed(plain) / 2
    assert any("application.declined" in types for types in revised)
    assert all(len(types) >= 7 or BANKING_LIFECYCLE[types[-1]].ends_journey for types in revised)
