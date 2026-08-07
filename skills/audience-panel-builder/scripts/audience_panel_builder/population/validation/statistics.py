"""Deterministic complete-block statistics for held-out ordering validation.

The functions in this module deliberately operate on validation *blocks*.
Pairwise comparisons within a block describe the same experimental unit and
must never be treated as independent bootstrap or permutation observations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import product
import math
import random
from statistics import NormalDist

from ...common import ContractError


_NORMAL = NormalDist()
_BCA_METHOD = "bca_complete_block_bootstrap"


class InsufficientUncertaintyError(ContractError):
    """Complete-block uncertainty cannot be calculated for these inputs."""


@dataclass(frozen=True)
class Interval:
    """A point estimate with two-sided and one-sided BCa bounds."""

    point: float
    two_sided_lower: float
    two_sided_upper: float
    one_sided_lower: float
    method: str
    resamples: int
    seed: int


def _finite_values(values: Sequence[float], *, path: str, minimum: int = 0) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ContractError(f"{path} must be a sequence of finite numbers")
    result: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractError(f"{path}[{index}] must be a finite number")
        number = float(value)
        if not math.isfinite(number):
            raise ContractError(f"{path}[{index}] must be a finite number")
        result.append(number)
    if len(result) < minimum:
        raise InsufficientUncertaintyError(
            f"{path} requires at least {minimum} independent blocks"
        )
    return tuple(result)


def _positive_int(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{path} must be a positive integer")
    return value


def _seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("seed must be an integer")
    return value


def _mean(values: Sequence[float]) -> float:
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise InsufficientUncertaintyError(
            "complete-block statistic overflows finite arithmetic"
        ) from exc
    result = total / len(values)
    if not math.isfinite(result):
        raise InsufficientUncertaintyError(
            "complete-block statistic is non-finite"
        )
    return result


def kendall_tau_b(
    synthetic_ranks: Sequence[float],
    observed_ranks: Sequence[float],
) -> float:
    """Calculate Kendall's tau-b directly, including ties in either ranking."""
    synthetic = _finite_values(synthetic_ranks, path="synthetic_ranks", minimum=2)
    observed = _finite_values(observed_ranks, path="observed_ranks", minimum=2)
    if len(synthetic) != len(observed):
        raise ContractError("synthetic_ranks and observed_ranks must have equal length")

    concordant = discordant = synthetic_only_ties = observed_only_ties = 0
    for left in range(len(synthetic) - 1):
        for right in range(left + 1, len(synthetic)):
            synthetic_difference = synthetic[left] - synthetic[right]
            observed_difference = observed[left] - observed[right]
            if synthetic_difference == 0.0 and observed_difference == 0.0:
                continue
            if synthetic_difference == 0.0:
                synthetic_only_ties += 1
            elif observed_difference == 0.0:
                observed_only_ties += 1
            elif (synthetic_difference > 0.0) == (observed_difference > 0.0):
                concordant += 1
            else:
                discordant += 1

    denominator = math.sqrt(
        (concordant + discordant + synthetic_only_ties)
        * (concordant + discordant + observed_only_ties)
    )
    if denominator == 0.0:
        raise ContractError("Kendall tau-b is undefined for this block")
    return (concordant - discordant) / denominator


def block_pairwise_agreement(comparison: dict[str, object]) -> tuple[float, float]:
    """Return agreement and observed-direction coverage for one full block."""
    if not isinstance(comparison, Mapping):
        raise ContractError("comparison must be an object")
    pairs = comparison.get("pairwise_comparisons")
    if not isinstance(pairs, list) or not pairs:
        raise ContractError("comparison.pairwise_comparisons must be a non-empty array")

    covered = 0
    directional = 0
    matching = 0
    valid_synthetic = {
        "synthetic_a_above_b", "synthetic_b_above_a", "synthetic_tie",
    }
    valid_observed = {
        "observed_a_above_b", "observed_b_above_a", "observed_equivalent",
        "observed_indeterminate",
    }
    expected = {
        "synthetic_a_above_b": "observed_a_above_b",
        "synthetic_b_above_a": "observed_b_above_a",
    }
    for index, pair in enumerate(pairs):
        if not isinstance(pair, Mapping):
            raise ContractError(f"comparison.pairwise_comparisons[{index}] must be an object")
        synthetic = pair.get("synthetic_direction")
        observed = pair.get("observed_direction")
        if synthetic not in valid_synthetic or observed not in valid_observed:
            raise ContractError(f"comparison.pairwise_comparisons[{index}] has invalid directions")
        if observed == "observed_indeterminate":
            continue
        covered += 1
        # Equivalence is a determinate observed outcome for coverage, but it
        # is not a direction and cannot manufacture directional agreement.
        if observed == "observed_equivalent":
            continue
        # A synthetic tie against a strict observed direction is a
        # directional, non-agreeing comparison.
        directional += 1
        if expected.get(synthetic) == observed:
            matching += 1
    if directional == 0:
        raise ContractError("comparison contains no determinate directional pairwise observations")
    return matching / directional, covered / len(pairs)


def _complete_block_resample_indices(
    block_count: int, *, seed: int, resamples: int,
) -> tuple[tuple[int, ...], ...]:
    """Generate the single reproducible complete-block index matrix."""
    _positive_int(block_count, path="block_count")
    checked_seed = _seed(seed)
    checked_resamples = _positive_int(resamples, path="resamples")
    generator = random.Random(checked_seed)
    return tuple(
        tuple(generator.randrange(block_count) for _ in range(block_count))
        for _ in range(checked_resamples)
    )


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise InsufficientUncertaintyError("bootstrap quantile is undefined")
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    # This convex form avoids overflowing an intermediate subtraction between
    # two otherwise finite extreme block statistics.
    result = (
        (1.0 - fraction) * sorted_values[lower]
        + fraction * sorted_values[upper]
    )
    if not math.isfinite(result):
        raise InsufficientUncertaintyError("bootstrap quantile is non-finite")
    return result


def _bca_probability(zero_correction: float, acceleration: float, alpha: float) -> float:
    z_alpha = _NORMAL.inv_cdf(alpha)
    denominator = 1.0 - acceleration * (zero_correction + z_alpha)
    if denominator == 0.0:
        raise InsufficientUncertaintyError("BCa acceleration makes an interval undefined")
    adjusted = _NORMAL.cdf(
        zero_correction + (zero_correction + z_alpha) / denominator
    )
    if not math.isfinite(adjusted):
        raise InsufficientUncertaintyError("BCa interval is non-finite")
    return min(1.0, max(0.0, adjusted))


def bca_block_interval(
    block_values: Sequence[float],
    *,
    seed: int,
    resamples: int = 20_000,
    confidence_level: float = 0.95,
) -> Interval:
    """Return deterministic BCa uncertainty for equally weighted blocks."""
    values = _finite_values(block_values, path="block_values", minimum=2)
    checked_seed = _seed(seed)
    checked_resamples = _positive_int(resamples, path="resamples")
    if not isinstance(confidence_level, (int, float)) or isinstance(confidence_level, bool) or not math.isfinite(confidence_level) or not 0.0 < confidence_level < 1.0:
        raise ContractError("confidence_level must be between zero and one")

    point = _mean(values)
    # This is not merely a shortcut: exact equal input blocks make every
    # complete-block bootstrap and leave-one-out statistic the same constant.
    if len(set(values)) == 1:
        constant = values[0]
        return Interval(
            point=constant, two_sided_lower=constant, two_sided_upper=constant,
            one_sided_lower=constant, method=_BCA_METHOD,
            resamples=checked_resamples, seed=checked_seed,
        )
    matrix = _complete_block_resample_indices(
        len(values), seed=checked_seed, resamples=checked_resamples,
    )
    bootstrap = tuple(_mean(tuple(values[index] for index in row)) for row in matrix)
    jackknife = tuple(
        _mean(values[:index] + values[index + 1:])
        for index in range(len(values))
    )
    if not all(math.isfinite(value) for value in (*bootstrap, *jackknife)):
        raise InsufficientUncertaintyError("bootstrap or jackknife values are non-finite")
    if len(set((*bootstrap, *jackknife))) == 1:
        constant = bootstrap[0]
        return Interval(
            point=constant, two_sided_lower=constant, two_sided_upper=constant,
            one_sided_lower=constant, method=_BCA_METHOD,
            resamples=checked_resamples, seed=checked_seed,
        )

    less_than_point = sum(value < point for value in bootstrap)
    if less_than_point in {0, len(bootstrap)}:
        raise InsufficientUncertaintyError("BCa bias correction is undefined")
    zero_correction = _NORMAL.inv_cdf(less_than_point / len(bootstrap))
    jackknife_mean = _mean(jackknife)
    deviations = tuple(jackknife_mean - value for value in jackknife)
    if not all(math.isfinite(value) for value in deviations):
        raise InsufficientUncertaintyError("BCa jackknife deviations are non-finite")
    scale = max(abs(value) for value in deviations)
    if scale == 0.0:
        acceleration = 0.0
    else:
        # Acceleration is invariant to common scaling. Normalizing first
        # keeps the squared/cubed sums finite even for extreme valid floats.
        scaled = tuple(value / scale for value in deviations)
        squared_sum = math.fsum(value * value for value in scaled)
        acceleration = math.fsum(value ** 3 for value in scaled) / (
            6.0 * squared_sum ** 1.5
        )
    if not math.isfinite(acceleration):
        raise InsufficientUncertaintyError("BCa acceleration is undefined")

    tail = (1.0 - float(confidence_level)) / 2.0
    ordered = tuple(sorted(bootstrap))
    lower = _quantile(ordered, _bca_probability(zero_correction, acceleration, tail))
    upper = _quantile(ordered, _bca_probability(zero_correction, acceleration, 1.0 - tail))
    one_sided_lower = _quantile(
        ordered, _bca_probability(zero_correction, acceleration, 1.0 - float(confidence_level)),
    )
    if not all(math.isfinite(value) for value in (point, lower, upper, one_sided_lower)):
        raise InsufficientUncertaintyError("BCa interval is non-finite")
    return Interval(
        point=point, two_sided_lower=lower, two_sided_upper=upper,
        one_sided_lower=one_sided_lower, method=_BCA_METHOD,
        resamples=checked_resamples, seed=checked_seed,
    )


def complete_block_sign_permutation_p(
    block_values: Sequence[float],
    *,
    seed: int,
    maximum_exact_blocks: int = 20,
    monte_carlo_resamples: int = 100_000,
) -> float:
    """Return the one-sided sign-permutation p-value over complete blocks."""
    values = _finite_values(block_values, path="block_values", minimum=1)
    checked_seed = _seed(seed)
    exact_limit = _positive_int(maximum_exact_blocks, path="maximum_exact_blocks")
    samples = _positive_int(monte_carlo_resamples, path="monte_carlo_resamples")
    observed = math.fsum(values)
    if len(values) <= exact_limit:
        extreme = sum(
            math.fsum(sign * value for sign, value in zip(signs, values)) >= observed
            for signs in product((-1.0, 1.0), repeat=len(values))
        )
        return extreme / (2 ** len(values))

    generator = random.Random(checked_seed)
    extreme = 0
    for _ in range(samples):
        permuted = math.fsum(
            (1.0 if generator.getrandbits(1) else -1.0) * value
            for value in values
        )
        if permuted >= observed:
            extreme += 1
    return (extreme + 1.0) / (samples + 1.0)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Apply the Holm step-down correction while preserving caller order."""
    values = _finite_values(p_values, path="p_values")
    if any(value < 0.0 or value > 1.0 for value in values):
        raise ContractError("p_values must be between zero and one")
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    adjusted = [0.0] * len(values)
    previous = 0.0
    for rank, (index, value) in enumerate(ordered):
        candidate = min(1.0, value * (len(values) - rank))
        previous = max(previous, candidate)
        adjusted[index] = previous
    return adjusted
