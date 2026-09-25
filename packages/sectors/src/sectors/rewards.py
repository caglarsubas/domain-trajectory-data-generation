"""MiMo-V2.6 reward mechanisms, shared by every sector pack.

Pure functions over plain numbers, so each formula can be checked by hand:

- multiplicative reward (MiMo §4.3.1, Eq. 2): R = R_test * S_sol * S_beh
- group-relative advantage: A_i = R_i - mean(R)
- groupwise advantage redistribution (§4.3.2, Eq. 3)
- group-relative length penalty (§4.3.3, Eq. 4)
- segment-level behavioral penalties (§4.3.3, Eq. 5)
- the penalty cascade and the dynamic-sampler group filter (§6.1)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Groupwise advantage redistribution: cap on the common rescaling factor.
GAR_LAMBDA_CAP = 2.0

# Group-relative length penalty (Eq. 4).
LENGTH_MAX_DEDUCTION = 0.5  # X
LENGTH_TOLERANCE = 0.1  # delta: relative excess tolerated above the reference length
LENGTH_SATURATION = 1.0  # s: relative excess at which the deduction is full
LENGTH_RAMP = 1.0  # gamma
LENGTH_MIN_PASS_RATE = 0.5  # A: the penalty applies only when more than this share of the group passes
LENGTH_PERCENTILE = 50.0  # B: reference length is this percentile of successful lengths

# Segment penalties (Eq. 5).
SEGMENT_KAPPA = 1.5
SEGMENT_ALPHA_MAX = 2.0
SEGMENT_BETA_MIN = 0.2


def multiplicative_reward(passed: bool, solution: float, behavior: float) -> float:
    """A failed verification zeroes the reward; rubric scores cannot rescue it."""
    return (1.0 if passed else 0.0) * solution * behavior


def group_advantages(rewards: list[float]) -> list[float]:
    if not rewards:
        return []
    mean = sum(rewards) / len(rewards)
    return [reward - mean for reward in rewards]


def redistribute(rewards: list[float], passed: list[bool], quality: list[float], cap: float = GAR_LAMBDA_CAP) -> list[float]:
    """Eq. 3: shift positive advantage from lower- to higher-quality passing sequences, then re-centre.

    `quality` is f_i in (0, 1] for passing sequences; failing sequences keep their advantage.
    Uncapped, the sum of advantages over the passing set is conserved.
    """
    base = group_advantages(rewards)
    passing = [index for index, ok in enumerate(passed) if ok]
    weighted = sum(quality[index] * base[index] for index in passing)
    if not passing or weighted == 0:
        return base
    scale = min(cap, sum(base[index] for index in passing) / weighted)
    shifted = [scale * quality[index] * base[index] if passed[index] else base[index] for index in range(len(base))]
    mean = sum(shifted) / len(shifted)
    return [value - mean for value in shifted]


def quantile(values: list[float], percentile: float) -> float:
    """Linear interpolation between closest ranks, as numpy's default."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def length_penalty(
    rewards: list[float],
    passed: list[bool],
    lengths: list[int],
    *,
    deduction: float = LENGTH_MAX_DEDUCTION,
    tolerance: float = LENGTH_TOLERANCE,
    saturation: float = LENGTH_SATURATION,
    ramp: float = LENGTH_RAMP,
    min_pass_rate: float = LENGTH_MIN_PASS_RATE,
    percentile: float = LENGTH_PERCENTILE,
) -> list[float]:
    """Eq. 4: discount successful sequences longer than a percentile of successful lengths.

    Groups at or below the minimum pass rate keep their rewards, so hard prompts keep room to explore.
    """
    passing = [index for index, ok in enumerate(passed) if ok]
    if not rewards or len(passing) / len(rewards) <= min_pass_rate:
        return list(rewards)
    reference = quantile([lengths[index] for index in passing], percentile)
    if reference <= 0:
        return list(rewards)
    adjusted = []
    for index, reward in enumerate(rewards):
        if not passed[index]:
            adjusted.append(reward)
            continue
        excess = (lengths[index] / reference - 1 - tolerance) / (saturation - tolerance)
        adjusted.append(reward - deduction * min(1.0, max(0.0, excess)) ** ramp)
    return adjusted


def segment_advantages(
    advantages: list[float],
    flags: list[list[bool]],
    tokens: list[list[int]],
    *,
    kappa: float = SEGMENT_KAPPA,
    alpha_max: float = SEGMENT_ALPHA_MAX,
    beta_min: float = SEGMENT_BETA_MIN,
) -> list[list[float]]:
    """Eq. 5 at segment granularity: every token in a segment shares its flag.

    Flagged segments are masked in positive sequences and weighted by kappa in negative ones; the
    removed or added mass is returned to unflagged segments of the same sign, so each sign's total
    is conserved unless a scale is clipped. Sums run over tokens across the whole batch.
    """
    h_pos = c_pos = h_neg = c_neg = 0.0
    for advantage, seq_flags, seq_tokens in zip(advantages, flags, tokens):
        for flagged, count in zip(seq_flags, seq_tokens):
            if advantage > 0:
                if flagged:
                    h_pos += advantage * count
                else:
                    c_pos += advantage * count
            elif advantage < 0:
                if flagged:
                    h_neg += abs(advantage) * count
                else:
                    c_neg += abs(advantage) * count
    alpha = min(alpha_max, 1 + h_pos / c_pos) if c_pos else 1.0
    beta = max(beta_min, 1 - (kappa - 1) * h_neg / c_neg) if c_neg else 1.0
    result = []
    for advantage, seq_flags in zip(advantages, flags):
        row = []
        for flagged in seq_flags:
            if advantage > 0:
                row.append(0.0 if flagged else alpha * advantage)
            elif advantage < 0:
                row.append((kappa if flagged else beta) * advantage)
            else:
                row.append(0.0)
        result.append(row)
    return result


@dataclass
class Cascade:
    context_dropped: list[list[bool]]
    sequence_dropped: list[bool]
    rejected: bool


def cascade(trainable_survives: list[list[list[bool]]]) -> Cascade:
    """A context with no surviving trainable segment is dropped; a sequence with no surviving
    context gets zero advantage; a sample with no surviving sequence is rejected.

    `trainable_survives[sequence][context]` lists, per trainable segment, whether it survives.
    """
    contexts = [[not any(segments) for segments in sequence] for sequence in trainable_survives]
    sequences = [all(dropped) for dropped in contexts]
    return Cascade(contexts, sequences, bool(sequences) and all(sequences))


def group_accepted(passed: list[bool]) -> bool | None:
    """The dynamic sampler rejects all-pass and all-fail groups. A group of one carries no group signal."""
    if len(passed) < 2:
        return None
    return any(passed) and not all(passed)
