from datetime import timedelta
from itertools import combinations

from sectors.banking.checks import banking_hard_checks
from sectors.banking.generate import GENERATOR_ID, generate_banking_bundle
from sectors.banking.pack import SUB_DOMAINS
from sectors.registry import known_sectors


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


def test_feedback_drop_revise_and_keep_change_the_next_bundle():
    parent = _bundle(sub_domains=["cards_and_payments", "onboarding_and_kyc"], seed="parent")
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


def test_same_seed_is_stable_and_other_sectors_stay_unregistered():
    first = _bundle(seed="same").model_dump(mode="json")
    second = _bundle(seed="same").model_dump(mode="json")
    assert first == second
    assert known_sectors() == ["banking"]


def test_helpfulness_revision_adds_a_longer_journey():
    bundle = _bundle(revision_notes=["helpfulness 1 below 3. Make the journey more representative."], seed="help")
    primaries = [item for item in bundle.trajectories if item.parent_trajectory_id is None]
    assert max(len(item.event_ids) for item in primaries) > 8
    rendered = " ".join(segment.text for sample in bundle.samples for sequence in sample.sequences for context in sequence.contexts for segment in context.segments)
    assert "helpfulness" in rendered
