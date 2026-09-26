import pytest

from sectors.banking.checks import banking_hard_checks
from sectors.banking.generate import generate_banking_bundle
from sectors.banking.pack import SUB_DOMAINS
from sectors.banking.spec import LIFECYCLE, PACK, intent
from test_api import _auth, _link, _project, _ready_key, _run


def _bundle(**overrides):
    payload = {
        "sub_domains": list(SUB_DOMAINS),
        "language": "en",
        "target_trajectory_count": 8,
        "event_budget": None,
        "min_events": 6,
        "max_events": 20,
        "max_assistant_turns": 3,
        "start_mode": "cold",
        "reward_mechanism": "binary_outcome",
        "signal_mechanism": "outcome",
        "consumer": "post_training",
        "target_family": "llm",
        "seed": "groups",
        "group_size": 4,
    }
    payload.update(overrides)
    return generate_banking_bundle(**payload)


def _types(bundle, trajectory_id):
    events = {event.event_id: event.event_type for event in bundle.events}
    trajectory = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    return [events[item] for item in trajectory.event_ids]


def test_a_group_shares_its_prompt_and_prefix_then_diverges():
    bundle = _bundle()
    assert banking_hard_checks(bundle) == []
    assert bundle.generation.group_size == 4
    assert len(bundle.samples) == 8
    trajectories = {item.trajectory_id: item for item in bundle.trajectories}
    for sample in bundle.samples:
        assert len(sample.sequences) == 4
        ids = [sequence.trajectory_id for sequence in sample.sequences]
        first = trajectories[ids[0]]
        assert first.parent_trajectory_id is None
        intents = {intent(_types(bundle, item)) for item in ids} - {None}
        assert len(intents) <= 1
        for rollout_id in ids[1:]:
            rollout = trajectories[rollout_id]
            assert rollout.parent_trajectory_id == first.trajectory_id
            assert rollout.group_id == first.group_id
            assert rollout.causal_claim is False
            split = first.event_ids.index(rollout.branch_event_id) + 1
            assert rollout.event_ids[:split] == first.event_ids[:split]
    assert any(len({tuple(_types(bundle, s.trajectory_id)) for s in sample.sequences}) > 1 for sample in bundle.samples)


def test_the_first_sequence_is_not_chosen_for_success():
    # Declined and abandoned applications end before eight events. The first sequence was redrawn until it reached
    # the minimum, so it passed every time, while its rollouts, which keep such endings, passed seven in ten.
    bundle = _bundle(sub_domains=["onboarding_and_kyc", "deposits"], min_events=8, max_events=24, target_trajectory_count=200,
                     materialization_cap=800, seed="first")
    assert banking_hard_checks(bundle) == []
    firsts = [sample.sequences[0] for sample in bundle.samples]
    rollouts = [sequence for sample in bundle.samples for sequence in sample.sequences[1:]]
    after_failure = [sequence for sample in bundle.samples if sample.sequences[0].outcome == "fail" for sequence in sample.sequences[1:]]

    def rate(sequences):
        return sum(sequence.outcome == "pass" for sequence in sequences) / len(sequences)

    assert rate(firsts) < 0.9
    assert abs(rate(firsts) - rate(rollouts)) < 0.12
    # A first sequence that reached no product leaves its rollouts free to reach one, so they need not fail with it.
    assert rate(after_failure) > 0.4
    short = [_types(bundle, sequence.trajectory_id) for sequence in firsts if len(_types(bundle, sequence.trajectory_id)) < 8]
    assert short and all(LIFECYCLE[types[-1]].ends_journey for types in short)


def test_the_opening_asks_for_the_product_the_group_reached():
    bundle = _bundle(sub_domains=["onboarding_and_kyc", "consumer_credit"], target_trajectory_count=60, materialization_cap=240, seed="asking")
    loan = PACK.openings["en"]["loan_origination"] + PACK.openings["en"]["loan_delinquency"]
    reopened = 0
    for sample in bundle.samples:
        reached = [intent(_types(bundle, sequence.trajectory_id)) for sequence in sample.sequences]
        opening = next(segment.text for segment in sample.sequences[0].contexts[0].segments if segment.role == "user")
        if "loan" in reached:
            assert opening in loan, (reached, opening)
            reopened += reached[0] is None
    assert reopened


def test_group_rewards_are_centred_and_all_pass_groups_are_not_accepted():
    bundle = _bundle(target_trajectory_count=12, seed="rewards")
    for sample in bundle.samples:
        outcomes = [sequence.outcome for sequence in sample.sequences]
        assert sum(sequence.advantage for sequence in sample.sequences) == pytest.approx(0.0, abs=1e-3)
        assert sample.group_pass_rate == pytest.approx(outcomes.count("pass") / len(outcomes))
        if len(set(outcomes)) == 1:
            assert sample.group_accepted is False
        else:
            assert sample.group_accepted is True
        for sequence in sample.sequences:
            segments = [segment for context in sequence.contexts for segment in context.segments]
            assert len(sequence.mask) == len(segments)
            assert all(segment.advantage is not None for segment in segments if segment.trainable)
            assert sequence.token_estimate > 0
    summary = bundle.generation.rewards
    assert summary["groups"] == 12 and summary["penalty_mode"] == "record"
    assert summary["accepted_groups"] == sum(1 for sample in bundle.samples if sample.group_accepted)


@pytest.mark.parametrize("sub_domain", SUB_DOMAINS)
def test_every_banking_sub_domain_yields_accepted_groups(sub_domain):
    bundle = _bundle(sub_domains=[sub_domain], target_trajectory_count=40, max_events=16, seed="accepted")
    assert banking_hard_checks(bundle) == []
    assert bundle.samples and all(len(sample.sequences) == 4 for sample in bundle.samples)
    accepted = [sample for sample in bundle.samples if sample.group_accepted]
    assert accepted, sub_domain
    assert bundle.generation.rewards["accepted_groups"] == len(accepted)


def test_redistribution_gives_passing_quality_factors_and_keeps_groups_centred():
    bundle = _bundle(reward_mechanism="groupwise_advantage_redistribution", seed="gar")
    for sample in bundle.samples:
        assert sum(sequence.advantage for sequence in sample.sequences) == pytest.approx(0.0, abs=1e-3)
        for sequence in sample.sequences:
            if sequence.outcome == "pass":
                assert 0 < sequence.quality_factor <= 1
            else:
                assert sequence.quality_factor is None


def test_groups_count_against_the_studio_cap():
    bundle = _bundle(target_trajectory_count=100, group_size=16)
    assert len(bundle.samples) == 4
    assert bundle.generation.limited_by == "studio_cap"


def test_a_group_of_one_says_it_carries_no_group_signal():
    bundle = _bundle(group_size=1)
    assert all(sample.group_accepted is None for sample in bundle.samples)
    assert all(sequence.advantage == 0 for sample in bundle.samples for sequence in sample.sequences)
    assert "group size above 1" in bundle.generation.rewards["note"]


def test_api_stores_the_group_size(client):
    headers = _auth(client, "groups@example.com", "password-123")
    project_id = _project(client, headers)
    _link(client, headers, project_id)
    credential_id = _ready_key(client, headers)
    created = _run(client, headers, project_id, credential_id, group_size=4, target_trajectory_count=6, event_budget=None)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["config"]["group_size"] == 4
    assert body["generation"]["group_size"] == 4
    assert all(len(sample["sequences"]) == 4 for sample in body["bundle"]["samples"])
    too_big = _run(client, headers, project_id, credential_id, group_size=17)
    assert too_big.status_code == 422
