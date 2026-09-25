from datetime import timedelta
from itertools import combinations

from sectors.insurance.checks import insurance_hard_checks
from sectors.insurance.generate import GENERATOR_ID, generate_insurance_bundle
from sectors.insurance.pack import SUB_DOMAINS
from trajectory_contract import TrajectoryBundle


def _bundle(**overrides):
    payload = {
        "sub_domains": ["quoting", "underwriting", "policy_administration", "billing"],
        "language": "en",
        "target_trajectory_count": 4,
        "event_budget": 400,
        "min_events": 6,
        "max_events": 16,
        "max_assistant_turns": 2,
        "start_mode": "cold",
        "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome",
        "consumer": "post_training",
        "target_family": "llm",
        "seed": "insurance-test",
    }
    payload.update(overrides)
    return generate_insurance_bundle(**payload)


def _types(bundle: TrajectoryBundle, trajectory) -> list[str]:
    events = {event.event_id: event for event in bundle.events}
    return [events[item].event_type for item in trajectory.event_ids]


def test_generated_policy_passes_hard_checks_and_uses_the_contract():
    bundle = _bundle(target_trajectory_count=8, event_budget=500, sub_domains=list(SUB_DOMAINS))
    assert insurance_hard_checks(bundle) == []
    assert bundle.generation is not None
    assert bundle.generation.generator_id == GENERATOR_ID
    assert bundle.generation.primary_trajectories == 8
    primaries = [item for item in bundle.trajectories if item.parent_trajectory_id is None]
    assert len(primaries) == 8
    assert any(item.observed_or_synthetic == "alternative" for item in bundle.trajectories)
    for sample in bundle.samples:
        for sequence in sample.sequences:
            for context in sequence.contexts:
                for segment in context.segments:
                    if segment.trainable:
                        assert segment.role == "assistant"
    issued = [item for item in primaries if "policy.issued" in _types(bundle, item)]
    assert issued
    for item in issued:
        types = _types(bundle, item)
        assert types.index("underwriting.accepted") < types.index("policy.issued")


def test_every_sub_domain_combination_stays_legal():
    names = list(SUB_DOMAINS)
    for size in range(1, len(names) + 1):
        for subset in combinations(names, size):
            bundle = _bundle(
                sub_domains=list(subset),
                target_trajectory_count=2,
                min_events=3,
                max_events=14,
                event_budget=None,
                seed="|".join(subset),
            )
            assert insurance_hard_checks(bundle) == [], subset


def test_claim_order_and_a_dropped_notification_removes_the_decision():
    claim = _bundle(sub_domains=["claims", "policy_administration"], target_trajectory_count=3, min_events=4, max_events=20, event_budget=None)
    assert insurance_hard_checks(claim) == []
    assert any(event.event_type == "claim.settled" for event in claim.events)
    for trajectory in claim.trajectories:
        types = _types(claim, trajectory)
        if "claim.settled" in types:
            assert types.index("claim.notified") < types.index("claim.assessed") < types.index("claim.settled")
            assert types.index("policy.issued") < types.index("claim.notified")
    dropped = _bundle(
        sub_domains=["quoting", "underwriting", "policy_administration", "claims"],
        corpus_text="claim.notified then claim.settled",
        start_mode="warm",
        seed="drop-claim",
        target_trajectory_count=2,
        min_events=4,
        max_events=16,
        event_budget=200,
        feedback=[{"target_type": "event", "target_id": "claim.notified", "stance": "drop", "comment": "No claim."}],
    )
    assert all(event.event_type != "claim.notified" for event in dropped.events)
    assert all(event.event_type != "claim.settled" for event in dropped.events)
    assert insurance_hard_checks(dropped) == []


def test_warm_corpus_names_a_claim_unless_the_domain_excludes_it():
    scope = ["quoting", "underwriting", "policy_administration", "billing", "claims"]
    kept = _bundle(
        start_mode="warm",
        sub_domains=scope,
        corpus_text="Public notes: claim.notified after the policy is in force. Customers pay USD on mobile for motor cover.",
        seed="events",
        target_trajectory_count=1,
        min_events=6,
        max_events=16,
        event_budget=80,
        feedback=[
            {"target_type": "trajectory", "target_id": "underwriting_referral", "stance": "drop", "comment": "Skip referral."},
            {"target_type": "trajectory", "target_id": "underwriting_declined", "stance": "drop", "comment": "Skip decline."},
            {"target_type": "trajectory", "target_id": "claim_settled", "stance": "drop", "comment": "Skip the claim variant."},
            {"target_type": "trajectory", "target_id": "claim_denied", "stance": "drop", "comment": "Skip denial."},
            {"target_type": "trajectory", "target_id": "policy_renewed", "stance": "drop", "comment": "Skip renewal."},
            {"target_type": "trajectory", "target_id": "policy_cancelled", "stance": "drop", "comment": "Skip cancel."},
            {"target_type": "trajectory", "target_id": "complaint_case", "stance": "drop", "comment": "Skip complaint."},
        ],
    )
    events = {event.event_id: event for event in kept.events}
    primaries = [item for item in kept.trajectories if item.parent_trajectory_id is None]
    assert primaries
    assert all(any(events[event_id].event_type == "claim.notified" for event_id in item.event_ids) for item in primaries)
    assert any(event.currency == "USD" for event in kept.events)
    assert any(event.event_type == "product.viewed" and event.channel_id == "mobile" for event in kept.events)
    assert any(obj.subtype == "motor" for obj in kept.objects if obj.object_type == "product_offering")
    assert insurance_hard_checks(kept) == []
    plain = _bundle(
        start_mode="warm",
        sub_domains=["quoting", "underwriting", "policy_administration", "billing"],
        corpus_text="claim.notified Customers pay USD on mobile.",
        seed="events",
        target_trajectory_count=1,
        min_events=6,
        max_events=16,
        event_budget=80,
    )
    assert all(event.event_type != "claim.notified" for event in plain.events)
    cold = _bundle(
        start_mode="cold",
        sub_domains=scope,
        corpus_text="Customers pay USD on mobile.",
        seed="cold-usd",
        target_trajectory_count=1,
        min_events=6,
        max_events=16,
    )
    assert all(event.currency != "USD" for event in cold.events)
    assert "Cold start" in cold.samples[0].sequences[0].contexts[0].segments[0].text


def test_same_seed_is_stable_and_turkish_names_the_policy():
    first = _bundle(seed="same").model_dump(mode="json")
    second = _bundle(seed="same").model_dump(mode="json")
    assert first == second
    turkish = _bundle(language="tr", seed="tr")
    assert any("sigorta" in sample.prompt.lower() for sample in turkish.samples)


def test_settlement_before_assessment_is_rejected():
    bundle = _bundle(sub_domains=["claims", "policy_administration"], target_trajectory_count=3, min_events=4, max_events=20, event_budget=None, seed="order")
    events = {event.event_id: event for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if any(events[event_id].event_type == "claim.settled" for event_id in item.event_ids))
    settled = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "claim.settled")
    assessed = next(events[event_id] for event_id in trajectory.event_ids if events[event_id].event_type == "claim.assessed")
    settled.event_time = assessed.event_time - timedelta(days=1)
    errors = insurance_hard_checks(bundle)
    assert any("claim decided before assessment" in item for item in errors)
