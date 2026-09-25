import pytest

from sectors import rewards


def approx(values, expected):
    assert values == pytest.approx(expected, abs=1e-9)


def test_multiplicative_reward_is_zero_when_verification_fails():
    assert rewards.multiplicative_reward(True, 0.8, 0.5) == pytest.approx(0.4)
    assert rewards.multiplicative_reward(False, 1.0, 1.0) == 0.0


def test_group_advantages_are_centred():
    approx(rewards.group_advantages([1.0, 0.0, 1.0, 0.0]), [0.5, -0.5, 0.5, -0.5])


def test_redistribution_conserves_passing_mass_and_favours_quality():
    # Binary rewards, mean 0.5. Passing advantages are 0.5 each; failing -0.5 each.
    passed = [True, True, False, False]
    quality = [1.0, 0.5, 1.0, 1.0]
    out = rewards.redistribute([1.0, 1.0, 0.0, 0.0], passed, quality)
    # lambda = (0.5 + 0.5) / (1.0*0.5 + 0.5*0.5) = 4/3, under the cap of 2.
    # Shifted: [2/3, 1/3, -0.5, -0.5], mean 0, so no re-centring change.
    approx(out, [2 / 3, 1 / 3, -0.5, -0.5])
    assert out[0] + out[1] == pytest.approx(1.0)
    assert out[0] > out[1]


def test_redistribution_caps_the_rescaling_factor_and_recentres():
    passed = [True, True, False]
    quality = [1.0, 0.1, 1.0]
    out = rewards.redistribute([1.0, 1.0, 0.0], passed, quality, cap=1.2)
    # Base: [1/3, 1/3, -2/3]. Uncapped lambda = (2/3) / (1/3 + 1/30) = 20/11 > 1.2, so lambda = 1.2.
    shifted = [1.2 * 1 / 3, 1.2 * 0.1 / 3, -2 / 3]
    mean = sum(shifted) / 3
    approx(out, [value - mean for value in shifted])
    assert sum(out) == pytest.approx(0.0)


def test_length_penalty_discounts_long_successes_against_the_median():
    # Three of four pass (0.75 > 0.5). Successful lengths 10, 12, 20: median reference 12.
    out = rewards.length_penalty([1.0, 1.0, 1.0, 0.0], [True, True, True, False], [10, 12, 20, 40])
    # Length 20: excess = (20/12 - 1 - 0.1) / (1 - 0.1) = 0.6296..., deduction 0.5 * 0.6296.
    excess = (20 / 12 - 1 - 0.1) / 0.9
    approx(out, [1.0, 1.0, 1.0 - 0.5 * excess, 0.0])


def test_length_penalty_spares_hard_groups():
    out = rewards.length_penalty([1.0, 0.0, 0.0, 0.0], [True, False, False, False], [30, 5, 5, 5])
    approx(out, [1.0, 0.0, 0.0, 0.0])


def test_segment_advantages_mask_flagged_positive_turns_and_conserve_mass():
    advantages = [0.5, -0.5]
    flags = [[False, True], [False, True]]
    tokens = [[10, 10], [10, 10]]
    out = rewards.segment_advantages(advantages, flags, tokens, kappa=1.5, alpha_max=2.0, beta_min=0.2)
    # alpha = 1 + (0.5*10)/(0.5*10) = 2; beta = 1 - 0.5 * (0.5*10)/(0.5*10) = 0.5.
    approx(out[0], [1.0, 0.0])
    approx(out[1], [-0.25, -0.75])
    positive = sum(a * t for a, t in zip(out[0], tokens[0]))
    negative = sum(a * t for a, t in zip(out[1], tokens[1]))
    assert positive == pytest.approx(0.5 * 20)
    assert negative == pytest.approx(-0.5 * 20)


def test_segment_advantages_without_flags_change_nothing():
    out = rewards.segment_advantages([0.3, -0.3, 0.0], [[False], [False], [False]], [[5], [5], [5]])
    approx([row[0] for row in out], [0.3, -0.3, 0.0])


def test_cascade_drops_empty_contexts_then_sequences_then_the_sample():
    result = rewards.cascade([[[True, False], [False]], [[False]]])
    assert result.context_dropped == [[False, True], [True]]
    assert result.sequence_dropped == [False, True]
    assert result.rejected is False
    assert rewards.cascade([[[False]], [[False]]]).rejected is True


def test_all_pass_and_all_fail_groups_are_not_accepted():
    assert rewards.group_accepted([True, False, True]) is True
    assert rewards.group_accepted([True, True]) is False
    assert rewards.group_accepted([False, False]) is False
    assert rewards.group_accepted([True]) is None


def test_quantile_interpolates_between_ranks():
    assert rewards.quantile([10, 12, 20], 50) == 12
    assert rewards.quantile([10, 20], 50) == 15
    assert rewards.quantile([4], 90) == 4
