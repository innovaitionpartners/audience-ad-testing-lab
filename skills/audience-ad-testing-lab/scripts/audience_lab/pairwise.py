"""Connected Davidson boundary resolution for frozen MaxDiff shortlist groups.

This module deliberately gives the boundary comparisons their own utility scale.
It never accepts or combines MaxDiff utilities.  Reported stability is conditional
on the realized, predeclared pair assignments in this synthetic model-call run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import LinearConstraint, minimize
from scipy.special import logsumexp


_CLEAR_FINALIST_THRESHOLD = 0.90
_CLEAR_NON_FINALIST_THRESHOLD = 0.10
_OUTCOMES = {"first", "second", "tie"}
_MINIMUM_UTILITY_TIE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class PairwiseConfig:
    """Fixed Davidson estimation and conditional-stability settings."""

    tie_parameter: float
    penalty_lambda: float
    optimizer_tolerance: float = 1e-8
    bootstrap_count: int = 2000
    successful_fit_floor: float = 0.95
    seed: int = 0

    def __post_init__(self) -> None:
        for field, value, positive in (
            ("tie_parameter", self.tie_parameter, False),
            ("penalty_lambda", self.penalty_lambda, True),
            ("optimizer_tolerance", self.optimizer_tolerance, True),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (value <= 0 if positive else value < 0)
            ):
                qualifier = "positive" if positive else "non-negative"
                raise ValueError(f"{field} must be a finite {qualifier} number")
        if isinstance(self.bootstrap_count, bool) or not isinstance(
            self.bootstrap_count, int
        ):
            raise ValueError("bootstrap_count must be an integer")
        if self.bootstrap_count < 1:
            raise ValueError("bootstrap_count must be at least 1")
        if (
            isinstance(self.successful_fit_floor, bool)
            or not isinstance(self.successful_fit_floor, (int, float))
            or not math.isfinite(self.successful_fit_floor)
            or not 0 <= self.successful_fit_floor <= 1
        ):
            raise ValueError("successful_fit_floor must be between zero and one")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or self.seed < 0
        ):
            raise ValueError("seed must be a non-negative integer")


@dataclass(frozen=True)
class IndexedPairwiseObservation:
    """One Davidson outcome translated to optimizer vector indices."""

    first_index: int
    second_index: int
    outcome: str


@dataclass(frozen=True)
class _Observation:
    record_id: str
    synthetic_replicate_id: str
    segment_id: str
    archetype_id: str
    first_id: str
    second_id: str
    outcome: str
    assignment_id: str
    wave: int


@dataclass(frozen=True)
class _PairAssignment:
    assignment_id: str
    wave: int
    first_id: str
    second_id: str


@dataclass(frozen=True)
class PairwiseFit:
    """One connected Davidson fit, including explicit refusal diagnostics."""

    utilities: dict[str, float]
    ranked_ids: tuple[str, ...]
    success: bool
    connected: bool
    identified: bool
    converged: bool
    loss: float | None
    projected_gradient_norm: float | None
    iterations: int
    message: str
    observation_count: int
    candidate_count: int
    outcome_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "utilities": dict(self.utilities),
            "ranked_ids": list(self.ranked_ids),
            "success": self.success,
            "connected": self.connected,
            "identified": self.identified,
            "converged": self.converged,
            "loss": self.loss,
            "projected_gradient_norm": self.projected_gradient_norm,
            "iterations": self.iterations,
            "message": self.message,
            "observation_count": self.observation_count,
            "candidate_count": self.candidate_count,
            "outcome_counts": dict(self.outcome_counts),
        }


@dataclass(frozen=True)
class BoundaryResult:
    """A resolved, unresolved, or invalid frozen-boundary decision."""

    status: str
    status_reasons: tuple[str, ...]
    selected_ids: list[str]
    utilities: dict[str, float]
    ranked_ids: list[str]
    inclusion_frequencies: dict[str, float]
    classifications: dict[str, str]
    candidate_ids: list[str]
    frozen_clear_finalist_ids: list[str]
    frozen_clear_non_finalist_ids: list[str]
    model_diagnostics: dict[str, Any]
    decision_audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_reasons": list(self.status_reasons),
            "estimand": "centered_pairwise_davidson_log_utility",
            "stability_diagnostic": (
                "conditional_within_run_boundary_slot_inclusion_frequency"
            ),
            "boundary_candidate_ids": list(self.candidate_ids),
            "frozen_clear_finalist_ids": list(self.frozen_clear_finalist_ids),
            "frozen_clear_non_finalist_ids": list(
                self.frozen_clear_non_finalist_ids
            ),
            "selected_boundary_ids": list(self.selected_ids),
            "proposed_finalist_ids": list(self.frozen_clear_finalist_ids)
            + list(self.selected_ids),
            "utilities": dict(self.utilities),
            "ranked_ids": list(self.ranked_ids),
            "conditional_inclusion_frequencies": dict(
                self.inclusion_frequencies
            ),
            "classifications": dict(self.classifications),
            "model_diagnostics": self.model_diagnostics,
            "decision_audit": self.decision_audit,
            "interpretation_limits": [
                "Pairwise utilities are centered and protocol-relative only within the frozen boundary set.",
                "Pairwise utilities are not pooled with MaxDiff utilities or assumed to share their scale.",
                "Conditional stability reflects realized synthetic pair assignments, not human-population uncertainty.",
                "Creative-ID ordering only serializes equal utilities; cutoff-tied inclusion is allocated symmetrically.",
                "Results do not establish human-response or campaign-performance validity.",
            ],
        }


def davidson_probabilities(
    first_utility: float, second_utility: float, tie_parameter: float
) -> tuple[float, float, float]:
    """Return Davidson probabilities for first, second, and tie outcomes."""

    values = (first_utility, second_utility, tie_parameter)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("Davidson utilities and tie_parameter must be finite numbers")
    if tie_parameter < 0:
        raise ValueError("tie_parameter must be non-negative")
    tie_logit = (
        math.log(float(tie_parameter))
        + 0.5 * (float(first_utility) + float(second_utility))
        if tie_parameter > 0
        else -math.inf
    )
    logits = np.asarray(
        [float(first_utility), float(second_utility), tie_logit], dtype=float
    )
    denominator = float(logsumexp(logits))
    probabilities = np.exp(logits - denominator)
    return tuple(float(value) for value in probabilities)  # type: ignore[return-value]


def davidson_loss_and_gradient(
    utilities: np.ndarray,
    observations: Sequence[IndexedPairwiseObservation],
    weights: Sequence[float],
    tie_parameter: float,
    penalty_lambda: float,
) -> tuple[float, np.ndarray]:
    """Return penalized Davidson negative log likelihood and analytic gradient."""

    values = np.asarray(utilities, dtype=float)
    analysis_weights = np.asarray(weights, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("utilities must be a finite one-dimensional vector")
    if analysis_weights.shape != (len(observations),):
        raise ValueError("observations and weights must have equal length")
    if not np.all(np.isfinite(analysis_weights)) or np.any(analysis_weights <= 0):
        raise ValueError("analysis weights must be finite positive numbers")
    if (
        isinstance(tie_parameter, bool)
        or not isinstance(tie_parameter, (int, float))
        or not math.isfinite(tie_parameter)
        or tie_parameter < 0
    ):
        raise ValueError("tie_parameter must be a finite non-negative number")
    if (
        isinstance(penalty_lambda, bool)
        or not isinstance(penalty_lambda, (int, float))
        or not math.isfinite(penalty_lambda)
        or penalty_lambda <= 0
    ):
        raise ValueError("penalty_lambda must be a finite positive number")

    loss = 0.5 * float(penalty_lambda) * float(np.dot(values, values))
    gradient = float(penalty_lambda) * values.copy()
    for observation, weight in zip(observations, analysis_weights, strict=True):
        if observation.outcome not in _OUTCOMES:
            raise ValueError(f"unsupported Davidson outcome: {observation.outcome}")
        if not (
            0 <= observation.first_index < len(values)
            and 0 <= observation.second_index < len(values)
            and observation.first_index != observation.second_index
        ):
            raise ValueError("pairwise observation indices are invalid")
        first = observation.first_index
        second = observation.second_index
        first_probability, second_probability, tie_probability = (
            davidson_probabilities(values[first], values[second], tie_parameter)
        )
        probability = {
            "first": first_probability,
            "second": second_probability,
            "tie": tie_probability,
        }[observation.outcome]
        if probability <= 0:
            raise ValueError(
                "tie outcome has zero probability under the fixed tie_parameter"
            )
        scaled_weight = float(weight)
        loss -= scaled_weight * math.log(probability)
        gradient[first] += scaled_weight * (
            first_probability + 0.5 * tie_probability
        )
        gradient[second] += scaled_weight * (
            second_probability + 0.5 * tie_probability
        )
        if observation.outcome == "first":
            gradient[first] -= scaled_weight
        elif observation.outcome == "second":
            gradient[second] -= scaled_weight
        else:
            gradient[first] -= 0.5 * scaled_weight
            gradient[second] -= 0.5 * scaled_weight
    return float(loss), gradient


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _pair_from_payload(payload: Mapping[str, Any], index: int) -> tuple[str, str]:
    pair = payload.get(
        "assigned_variation_ids",
        payload.get("variation_ids", payload.get("pair")),
    )
    if not _is_array(pair) or len(pair) != 2:
        raise ValueError(f"observation {index} must contain exactly two creatives")
    if not all(_is_non_empty_string(item) for item in pair):
        raise ValueError(f"observation {index} creative IDs must be non-empty strings")
    if pair[0] == pair[1]:
        raise ValueError(f"observation {index} pair creatives must differ")
    return str(pair[0]), str(pair[1])


def _record_metadata(
    payload: Mapping[str, Any], index: int
) -> tuple[str, str, int, tuple[str, str]]:
    record_id = payload.get("response_id", payload.get("record_id"))
    if not _is_non_empty_string(record_id):
        raise ValueError(f"observation {index} record ID must be a non-empty string")
    assignment_id = payload.get(
        "pair_assignment_id",
        payload.get("boundary_job_id", record_id),
    )
    if not _is_non_empty_string(assignment_id):
        raise ValueError(
            f"observation {index} pair_assignment_id must be a non-empty string"
        )
    wave = payload.get("boundary_wave", payload.get("wave", 1))
    wave = _positive_int(wave, f"observation {index} boundary_wave")
    return str(record_id), str(assignment_id), wave, _pair_from_payload(payload, index)


def _normalize_observations(
    observations: Sequence[Mapping[str, Any] | _Observation],
) -> tuple[_Observation, ...]:
    normalized: list[_Observation] = []
    seen_record_ids: set[str] = set()
    seen_replicate_ids: set[str] = set()
    for index, payload in enumerate(observations):
        if isinstance(payload, _Observation):
            item = payload
        else:
            if not isinstance(payload, Mapping):
                raise ValueError(f"observation {index} must be an object")
            if "record_type" in payload and payload.get("record_type") != "boundary_response":
                raise ValueError(f"observation {index} must be a boundary_response")
            record_id, assignment_id, wave, pair = _record_metadata(payload, index)
            replicate_id = payload.get(
                "synthetic_replicate_id", f"synthetic-replicate-{record_id}"
            )
            if not _is_non_empty_string(replicate_id):
                raise ValueError(
                    f"observation {index} synthetic_replicate_id must be a non-empty string"
                )
            segment_id = payload.get("segment_id", "__all_records__")
            archetype_id = payload.get(
                "persona_archetype_id", "__archetype_not_supplied__"
            )
            if not _is_non_empty_string(segment_id):
                raise ValueError(
                    f"observation {index} segment_id must be a non-empty string"
                )
            if not _is_non_empty_string(archetype_id):
                raise ValueError(
                    f"observation {index} persona_archetype_id must be a non-empty string"
                )
            if payload.get("usable_pairwise_observation", payload.get("usable", True)) is not True:
                continue
            shown = payload.get("shown_order", pair)
            if (
                not _is_array(shown)
                or len(shown) != 2
                or set(shown) != set(pair)
            ):
                raise ValueError(
                    f"observation {index} shown_order must be an exact pair permutation"
                )
            first_id, second_id = str(shown[0]), str(shown[1])
            choice = payload.get("pairwise_choice")
            if isinstance(choice, Mapping):
                status = choice.get("status")
                preferred = choice.get("preferred_variation_id")
            else:
                status = payload.get("outcome")
                preferred = payload.get("preferred_variation_id")
            if status in {"first_preferred", "first"}:
                if preferred not in {None, "", first_id}:
                    raise ValueError(
                        f"observation {index} preferred ID does not match first shown creative"
                    )
                outcome = "first"
            elif status in {"second_preferred", "second"}:
                if preferred not in {None, "", second_id}:
                    raise ValueError(
                        f"observation {index} preferred ID does not match second shown creative"
                    )
                outcome = "second"
            elif status == "tie":
                if preferred not in {None, ""}:
                    raise ValueError(
                        f"observation {index} preferred ID must be empty for a tie"
                    )
                outcome = "tie"
            else:
                raise ValueError(
                    f"observation {index} has an unsupported pairwise outcome"
                )
            item = _Observation(
                record_id=record_id,
                synthetic_replicate_id=str(replicate_id),
                segment_id=str(segment_id),
                archetype_id=str(archetype_id),
                first_id=first_id,
                second_id=second_id,
                outcome=outcome,
                assignment_id=assignment_id,
                wave=wave,
            )
        if item.record_id in seen_record_ids:
            raise ValueError("usable pairwise record IDs must be unique")
        if item.synthetic_replicate_id in seen_replicate_ids:
            raise ValueError("usable pairwise synthetic replicate IDs must be unique")
        seen_record_ids.add(item.record_id)
        seen_replicate_ids.add(item.synthetic_replicate_id)
        normalized.append(item)
    return tuple(normalized)


def _candidate_roster(
    observations: Sequence[Mapping[str, Any] | _Observation],
) -> tuple[str, ...]:
    creative_ids: set[str] = set()
    for index, payload in enumerate(observations):
        if isinstance(payload, _Observation):
            creative_ids.update((payload.first_id, payload.second_id))
        elif isinstance(payload, Mapping):
            creative_ids.update(_pair_from_payload(payload, index))
    return tuple(sorted(creative_ids))


def _comparison_graph_connected(
    candidate_ids: Sequence[str], observations: Sequence[_Observation]
) -> bool:
    roster = tuple(candidate_ids)
    if len(roster) < 2:
        return False
    neighbors: dict[str, set[str]] = {candidate_id: set() for candidate_id in roster}
    for observation in observations:
        if observation.first_id in neighbors and observation.second_id in neighbors:
            neighbors[observation.first_id].add(observation.second_id)
            neighbors[observation.second_id].add(observation.first_id)
    visited: set[str] = set()
    pending = [roster[0]]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(neighbors[current] - visited, reverse=True))
    return visited == set(roster)


def _normalized_weights(weights: Sequence[float], count: int) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if values.shape != (count,):
        raise ValueError("analysis weights must match usable observation count")
    if count == 0:
        return values
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("analysis weights must be finite positive numbers")
    return values * (count / float(values.sum()))


def _weights_for_observations(
    observations: Sequence[_Observation],
    segment_weights: Mapping[str, float] | None,
) -> np.ndarray:
    if not observations:
        return np.asarray([], dtype=float)
    if segment_weights is None:
        return np.ones(len(observations), dtype=float)
    if not isinstance(segment_weights, Mapping) or not segment_weights:
        raise ValueError("locked segment weights must be a non-empty object")
    counts = Counter(item.segment_id for item in observations)
    if set(counts) != set(segment_weights):
        raise ValueError(
            "locked segment IDs must exactly match usable pairwise response segments"
        )
    parsed: dict[str, float] = {}
    for segment_id, weight in segment_weights.items():
        if (
            not _is_non_empty_string(segment_id)
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError("locked segment weights must be finite positive numbers")
        parsed[str(segment_id)] = float(weight)
    total = sum(parsed.values())
    raw = [
        (parsed[item.segment_id] / total)
        / (counts[item.segment_id] / len(observations))
        for item in observations
    ]
    return _normalized_weights(raw, len(observations))


def _invalid_fit(
    *,
    connected: bool,
    identified: bool,
    message: str,
    observation_count: int,
    candidate_count: int,
    outcome_counts: Mapping[str, int] | None = None,
) -> PairwiseFit:
    return PairwiseFit(
        utilities={},
        ranked_ids=(),
        success=False,
        connected=connected,
        identified=identified,
        converged=False,
        loss=None,
        projected_gradient_norm=None,
        iterations=0,
        message=message,
        observation_count=observation_count,
        candidate_count=candidate_count,
        outcome_counts=dict(outcome_counts or {key: 0 for key in sorted(_OUTCOMES)}),
    )


def _fit_normalized(
    observations: Sequence[_Observation],
    config: PairwiseConfig,
    weights: Sequence[float],
    candidate_ids: Sequence[str],
) -> PairwiseFit:
    roster = tuple(sorted(set(candidate_ids)))
    outcome_counts = Counter(item.outcome for item in observations)
    connected = _comparison_graph_connected(roster, observations)
    identified = len(roster) >= 2 and bool(observations) and connected
    if not observations:
        return _invalid_fit(
            connected=False,
            identified=False,
            message="no usable pairwise observations",
            observation_count=0,
            candidate_count=len(roster),
        )
    if not connected:
        return _invalid_fit(
            connected=False,
            identified=False,
            message="pairwise comparison graph is disconnected before regularization",
            observation_count=len(observations),
            candidate_count=len(roster),
            outcome_counts=outcome_counts,
        )
    if config.tie_parameter == 0 and outcome_counts["tie"]:
        return _invalid_fit(
            connected=True,
            identified=False,
            message="tie outcome cannot be fit when fixed tie_parameter is zero",
            observation_count=len(observations),
            candidate_count=len(roster),
            outcome_counts=outcome_counts,
        )
    if not identified:
        return _invalid_fit(
            connected=connected,
            identified=False,
            message="Davidson boundary model is not identified",
            observation_count=len(observations),
            candidate_count=len(roster),
            outcome_counts=outcome_counts,
        )

    index_by_id = {candidate_id: index for index, candidate_id in enumerate(roster)}
    indexed = tuple(
        IndexedPairwiseObservation(
            index_by_id[item.first_id], index_by_id[item.second_id], item.outcome
        )
        for item in observations
    )
    normalized_weights = _normalized_weights(weights, len(observations))

    def objective(values: np.ndarray) -> float:
        return davidson_loss_and_gradient(
            values,
            indexed,
            normalized_weights,
            config.tie_parameter,
            config.penalty_lambda,
        )[0]

    def gradient(values: np.ndarray) -> np.ndarray:
        return davidson_loss_and_gradient(
            values,
            indexed,
            normalized_weights,
            config.tie_parameter,
            config.penalty_lambda,
        )[1]

    constraint = LinearConstraint(
        np.ones((1, len(roster))), np.asarray([0.0]), np.asarray([0.0])
    )
    try:
        optimized = minimize(
            objective,
            np.zeros(len(roster), dtype=float),
            method="SLSQP",
            jac=gradient,
            constraints=(constraint,),
            options={
                "ftol": config.optimizer_tolerance,
                "maxiter": 1000,
                "disp": False,
            },
        )
    except (FloatingPointError, ValueError, ArithmeticError) as exc:
        return _invalid_fit(
            connected=True,
            identified=True,
            message=f"optimizer failure: {exc}",
            observation_count=len(observations),
            candidate_count=len(roster),
            outcome_counts=outcome_counts,
        )

    values = np.asarray(optimized.x, dtype=float)
    finite = bool(
        np.all(np.isfinite(values)) and math.isfinite(float(optimized.fun))
    )
    constraint_residual = abs(float(values.sum())) if finite else math.inf
    if finite:
        projected_gradient = gradient(values)
        projected_gradient -= float(projected_gradient.mean())
        projected_norm = float(np.linalg.norm(projected_gradient))
    else:
        projected_norm = math.inf
    gradient_limit = max(1e-5, math.sqrt(config.optimizer_tolerance) * 5)
    converged = bool(
        optimized.success
        and finite
        and constraint_residual <= max(1e-9, config.optimizer_tolerance * 10)
        and projected_norm <= gradient_limit
    )
    if not converged:
        return PairwiseFit(
            utilities={},
            ranked_ids=(),
            success=False,
            connected=True,
            identified=True,
            converged=False,
            loss=float(optimized.fun) if finite else None,
            projected_gradient_norm=projected_norm if finite else None,
            iterations=int(getattr(optimized, "nit", 0)),
            message=str(optimized.message),
            observation_count=len(observations),
            candidate_count=len(roster),
            outcome_counts={key: outcome_counts[key] for key in sorted(_OUTCOMES)},
        )

    values -= float(values.mean())
    utilities = {
        candidate_id: float(values[index_by_id[candidate_id]])
        for candidate_id in roster
    }
    tie_tolerance = max(
        float(config.optimizer_tolerance), _MINIMUM_UTILITY_TIE_TOLERANCE
    )
    ranked = tuple(
        sorted(
            roster,
            key=lambda item: (-round(utilities[item] / tie_tolerance), item),
        )
    )
    return PairwiseFit(
        utilities=utilities,
        ranked_ids=ranked,
        success=True,
        connected=True,
        identified=True,
        converged=True,
        loss=float(objective(values)),
        projected_gradient_norm=projected_norm,
        iterations=int(getattr(optimized, "nit", 0)),
        message=str(optimized.message),
        observation_count=len(observations),
        candidate_count=len(roster),
        outcome_counts={key: outcome_counts[key] for key in sorted(_OUTCOMES)},
    )


def fit_davidson(
    observations: Sequence[Mapping[str, Any] | _Observation],
    config: PairwiseConfig,
    *,
    segment_weights: Mapping[str, float] | None = None,
    analysis_weights: Sequence[float] | None = None,
    candidate_ids: Sequence[str] | None = None,
) -> PairwiseFit:
    """Fit a connected, sum-to-zero Davidson model or return refusal diagnostics."""

    normalized = _normalize_observations(observations)
    roster = tuple(
        sorted(
            set(
                _candidate_roster(observations)
                if candidate_ids is None
                else candidate_ids
            )
        )
    )
    if not all(_is_non_empty_string(item) for item in roster):
        raise ValueError("candidate IDs must be non-empty strings")
    observed = {
        creative_id
        for item in normalized
        for creative_id in (item.first_id, item.second_id)
    }
    outside = sorted(observed - set(roster))
    if outside:
        raise ValueError(
            "pairwise observations contain creatives outside the frozen boundary: "
            + ",".join(outside)
        )
    if analysis_weights is not None and segment_weights is not None:
        raise ValueError("provide analysis_weights or segment_weights, not both")
    weights = (
        _normalized_weights(analysis_weights, len(normalized))
        if analysis_weights is not None
        else _weights_for_observations(normalized, segment_weights)
    )
    return _fit_normalized(normalized, config, weights, roster)


def symmetric_cutoff_inclusion(
    utilities: Mapping[str, float],
    slots: int,
    *,
    tolerance: float,
) -> dict[str, float]:
    """Allocate cutoff-tied slots symmetrically instead of by creative ID.

    Candidates strictly above the cutoff band receive one inclusion unit,
    candidates strictly below it receive zero, and every candidate within the
    fixed tolerance band shares the remaining units equally.  The fractional
    units are accumulated across bootstrap fits and then classified with the
    fixed 0.90/0.10 product rules.
    """

    if not isinstance(utilities, Mapping) or not utilities:
        raise ValueError("utilities must be a non-empty object")
    if isinstance(slots, bool) or not isinstance(slots, int):
        raise ValueError("slots must be an integer")
    if not 0 <= slots <= len(utilities):
        raise ValueError("slots must be within the utility roster")
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0
    ):
        raise ValueError("cutoff tie tolerance must be a finite positive number")

    parsed: dict[str, float] = {}
    for candidate_id, utility in utilities.items():
        if not _is_non_empty_string(candidate_id):
            raise ValueError("utility IDs must be non-empty strings")
        if (
            isinstance(utility, bool)
            or not isinstance(utility, (int, float))
            or not math.isfinite(utility)
        ):
            raise ValueError("utilities must be finite numbers")
        parsed[str(candidate_id)] = float(utility)
    if slots == 0:
        return {candidate_id: 0.0 for candidate_id in parsed}
    if slots == len(parsed):
        return {candidate_id: 1.0 for candidate_id in parsed}

    ordered_values = sorted(parsed.values(), reverse=True)
    cutoff = ordered_values[slots - 1]
    above = {
        candidate_id
        for candidate_id, utility in parsed.items()
        if utility > cutoff + float(tolerance)
    }
    tied = {
        candidate_id
        for candidate_id, utility in parsed.items()
        if abs(utility - cutoff) <= float(tolerance)
    }
    if not tied:
        raise ValueError("cutoff tie band must include the cutoff candidate")
    remaining = slots - len(above)
    fractional = remaining / len(tied)
    if not 0 <= fractional <= 1:
        raise ValueError("cutoff tie allocation is internally inconsistent")
    return {
        candidate_id: (
            1.0
            if candidate_id in above
            else fractional if candidate_id in tied else 0.0
        )
        for candidate_id in parsed
    }


def _bootstrap_stability(
    observations: tuple[_Observation, ...],
    candidate_ids: tuple[str, ...],
    slots: int,
    config: PairwiseConfig,
    segment_weights: Mapping[str, float] | None,
) -> tuple[dict[str, float], dict[str, Any]]:
    rng = np.random.default_rng(config.seed)
    strata: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        strata[observation.segment_id].append(observation)
    inclusion_counts = {candidate_id: 0.0 for candidate_id in candidate_ids}
    successful = 0
    disconnected = 0
    nonconvergent = 0
    cutoff_tied_fits = 0
    tie_tolerance = max(
        float(config.optimizer_tolerance), _MINIMUM_UTILITY_TIE_TOLERANCE
    )
    for _ in range(config.bootstrap_count):
        sampled: list[_Observation] = []
        for segment_id in sorted(strata):
            records = strata[segment_id]
            selected = rng.integers(0, len(records), size=len(records))
            sampled.extend(records[int(index)] for index in selected)
        sample = tuple(sampled)
        try:
            weights = _weights_for_observations(sample, segment_weights)
            fitted = _fit_normalized(sample, config, weights, candidate_ids)
        except ValueError:
            nonconvergent += 1
            continue
        if not fitted.connected or not fitted.identified:
            disconnected += 1
            continue
        if not fitted.success:
            nonconvergent += 1
            continue
        successful += 1
        inclusion = symmetric_cutoff_inclusion(
            fitted.utilities,
            slots,
            tolerance=tie_tolerance,
        )
        if any(0.0 < value < 1.0 for value in inclusion.values()):
            cutoff_tied_fits += 1
        for candidate_id, value in inclusion.items():
            inclusion_counts[candidate_id] += value
    frequencies = {
        candidate_id: (
            inclusion_counts[candidate_id] / successful if successful else 0.0
        )
        for candidate_id in candidate_ids
    }
    diagnostics = {
        "resample_unit": "whole_synthetic_replicate_record",
        "stratification": "locked_segment",
        "conditional_on_realized_pair_assignments": True,
        "seed": config.seed,
        "requested_fits": config.bootstrap_count,
        "successful_fits": successful,
        "disconnected_fits": disconnected,
        "nonconvergent_fits": nonconvergent,
        "successful_fit_rate": successful / config.bootstrap_count,
        "successful_fit_floor": config.successful_fit_floor,
        "cutoff_tie_policy": "symmetric_fractional_inclusion",
        "cutoff_tie_tolerance": tie_tolerance,
        "cutoff_tied_fits": cutoff_tied_fits,
    }
    return frequencies, diagnostics


def classify_inclusion_frequency(
    frequency: float,
    clear_finalist_threshold: float = _CLEAR_FINALIST_THRESHOLD,
    clear_non_finalist_threshold: float = _CLEAR_NON_FINALIST_THRESHOLD,
) -> str:
    """Apply the inclusive 0.90/0.10 boundary product rules."""

    if (
        isinstance(frequency, bool)
        or not isinstance(frequency, (int, float))
        or not math.isfinite(frequency)
        or not 0 <= frequency <= 1
    ):
        raise ValueError("inclusion frequency must be between zero and one")
    if clear_finalist_threshold != 0.90 or clear_non_finalist_threshold != 0.10:
        raise ValueError("boundary inclusion thresholds are fixed at 0.90/0.10")
    if frequency >= clear_finalist_threshold:
        return "clear_finalist"
    if frequency <= clear_non_finalist_threshold:
        return "clear_non_finalist"
    return "boundary_candidate"


def _normalize_assignments(
    assignments: Sequence[Mapping[str, Any]] | None,
) -> tuple[tuple[_PairAssignment, ...], str]:
    if assignments is None:
        raise ValueError(
            "predeclared_pair_assignments must be supplied from an explicit frozen plan"
        )
    source = "predeclared_boundary_plan"
    if not _is_array(assignments):
        raise ValueError("predeclared_pair_assignments must be an array")
    normalized: list[_PairAssignment] = []
    seen: set[str] = set()
    for index, payload in enumerate(assignments):
        if not isinstance(payload, Mapping):
            raise ValueError(f"pair assignment {index} must be an object")
        assignment_id = payload.get(
            "pair_assignment_id", payload.get("boundary_job_id", payload.get("job_id"))
        )
        if not _is_non_empty_string(assignment_id):
            raise ValueError(
                f"pair assignment {index} pair_assignment_id must be non-empty"
            )
        if assignment_id in seen:
            raise ValueError("predeclared pair assignment IDs must be unique")
        seen.add(str(assignment_id))
        wave = _positive_int(
            payload.get("wave", payload.get("boundary_wave")),
            f"pair assignment {index} wave",
        )
        first, second = _pair_from_payload(payload, index)
        normalized.append(
            _PairAssignment(str(assignment_id), wave, first, second)
        )
    return tuple(normalized), source


def _base_audit(
    *,
    slots: int,
    candidates: Sequence[str],
    clear_finalists: Sequence[str],
    clear_non_finalists: Sequence[str],
    policy_source: str,
    jobs_per_wave: int,
    max_waves: int,
    boundary_reserved: int,
    available_boundary_reserve: int,
    finalist_reserved: int,
) -> dict[str, Any]:
    return {
        "policy_version": "connected-davidson-boundary-v1",
        "model_scope": "frozen_boundary_candidates_only",
        "maxdiff_utilities_pooled": False,
        "clear_groups_frozen": True,
        "frozen_clear_finalist_ids": list(clear_finalists),
        "frozen_clear_non_finalist_ids": list(clear_non_finalists),
        "boundary_candidate_ids": list(candidates),
        "remaining_finalist_slots": slots,
        "inclusion_policy": {
            "clear_finalist": ">=0.90",
            "clear_non_finalist": "<=0.10",
            "boundary_candidate": "strictly_between_0.10_and_0.90",
            "cutoff_tie_policy": "symmetric_fractional_inclusion",
            "cutoff_tie_tolerance": "max(optimizer_tolerance,1e-12)",
        },
        "predeclaration": {
            "source": policy_source,
            "boundary_jobs_per_wave": jobs_per_wave,
            "boundary_waves_max": max_waves,
        },
        "reserve": {
            "boundary_reserved": boundary_reserved,
            "available_boundary_reserve": available_boundary_reserve,
            "boundary_jobs_observed": 0,
            "boundary_jobs_consumed": 0,
            "boundary_jobs_remaining": available_boundary_reserve,
            "boundary_jobs_over_reserve": 0,
            "finalist_reserved_before": finalist_reserved,
            "finalist_reserved_after": finalist_reserved,
            "finalist_reserve_consumed": 0,
        },
        "waves": [],
        "selection_decisions": [],
        "next_wave_job_ids": [],
        "stopping_decision": {
            "reason": "not_evaluated",
            "wave": None,
            "resolved": False,
        },
    }


def _account_realized_calls(audit: dict[str, Any], realized_calls: int) -> None:
    """Charge every supplied provider result against the boundary reserve."""

    reserve = audit["reserve"]
    available = reserve.get("available_boundary_reserve")
    reserve["boundary_jobs_observed"] = realized_calls
    reserve["boundary_jobs_consumed"] = realized_calls
    if isinstance(available, int) and not isinstance(available, bool):
        reserve["boundary_jobs_remaining"] = max(available - realized_calls, 0)
        reserve["boundary_jobs_over_reserve"] = max(realized_calls - available, 0)
    else:
        reserve["boundary_jobs_remaining"] = None
        reserve["boundary_jobs_over_reserve"] = None


def _result(
    *,
    status: str,
    reasons: Sequence[str],
    candidates: Sequence[str],
    clear_finalists: Sequence[str],
    clear_non_finalists: Sequence[str],
    audit: dict[str, Any],
    fit: PairwiseFit | None = None,
    frequencies: Mapping[str, float] | None = None,
    classifications: Mapping[str, str] | None = None,
    selected: Sequence[str] = (),
    diagnostics: Mapping[str, Any] | None = None,
) -> BoundaryResult:
    publish_model = status == "resolved" and fit is not None and fit.success
    candidate_list = list(candidates)
    return BoundaryResult(
        status=status,
        status_reasons=tuple(dict.fromkeys(reasons)),
        selected_ids=list(selected) if publish_model else [],
        utilities=dict(fit.utilities) if publish_model else {},
        ranked_ids=list(fit.ranked_ids) if publish_model else [],
        inclusion_frequencies=dict(frequencies or {}) if publish_model else {},
        classifications=(
            dict(classifications or {})
            if publish_model
            else {candidate_id: "unresolved" for candidate_id in candidate_list}
        ),
        candidate_ids=candidate_list,
        frozen_clear_finalist_ids=list(clear_finalists),
        frozen_clear_non_finalist_ids=list(clear_non_finalists),
        model_diagnostics=dict(diagnostics or {}),
        decision_audit=audit,
    )


def resolve_boundary(
    responses: Sequence[Mapping[str, Any] | _Observation],
    slots: int,
    config: PairwiseConfig,
    *,
    candidate_ids: Sequence[str] | None = None,
    segment_weights: Mapping[str, float] | None = None,
    predeclared_pair_assignments: Sequence[Mapping[str, Any]] | None = None,
    boundary_jobs_per_wave: int | None = None,
    boundary_waves_max: int | None = None,
    boundary_reserved: int | None = None,
    available_boundary_reserve: int | None = None,
    finalist_reserved: int = 0,
    clear_finalist_ids: Sequence[str] = (),
    clear_non_finalist_ids: Sequence[str] = (),
) -> BoundaryResult:
    """Resolve only frozen boundary candidates under a bounded predeclared policy."""

    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise ValueError("slots must be a non-negative integer")
    if not _is_array(responses):
        raise ValueError("responses must be an array")
    candidates = tuple(
        sorted(
            set(
                _candidate_roster(responses)
                if candidate_ids is None
                else candidate_ids
            )
        )
    )
    clear_finalists = tuple(sorted(set(clear_finalist_ids)))
    clear_non_finalists = tuple(sorted(set(clear_non_finalist_ids)))
    if not all(
        _is_non_empty_string(item)
        for item in (*candidates, *clear_finalists, *clear_non_finalists)
    ):
        raise ValueError("all frozen creative IDs must be non-empty strings")
    if (
        set(candidates) & set(clear_finalists)
        or set(candidates) & set(clear_non_finalists)
        or set(clear_finalists) & set(clear_non_finalists)
    ):
        raise ValueError("frozen screening groups must be disjoint")

    try:
        assignments, policy_source = _normalize_assignments(
            predeclared_pair_assignments
        )
    except ValueError as exc:
        invalid_jobs_per_wave = (
            boundary_jobs_per_wave
            if isinstance(boundary_jobs_per_wave, int)
            and not isinstance(boundary_jobs_per_wave, bool)
            and boundary_jobs_per_wave > 0
            else 0
        )
        invalid_max_waves = (
            boundary_waves_max
            if isinstance(boundary_waves_max, int)
            and not isinstance(boundary_waves_max, bool)
            and boundary_waves_max > 0
            else 0
        )
        invalid_reserved = (
            boundary_reserved
            if isinstance(boundary_reserved, int)
            and not isinstance(boundary_reserved, bool)
            and boundary_reserved >= 0
            else 0
        )
        invalid_available = (
            available_boundary_reserve
            if isinstance(available_boundary_reserve, int)
            and not isinstance(available_boundary_reserve, bool)
            and available_boundary_reserve >= 0
            else invalid_reserved
        )
        audit = _base_audit(
            slots=slots,
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            policy_source="invalid",
            jobs_per_wave=invalid_jobs_per_wave,
            max_waves=invalid_max_waves,
            boundary_reserved=invalid_reserved,
            available_boundary_reserve=invalid_available,
            finalist_reserved=(
                finalist_reserved
                if isinstance(finalist_reserved, int)
                and not isinstance(finalist_reserved, bool)
                and finalist_reserved >= 0
                else 0
            ),
        )
        _account_realized_calls(audit, len(responses))
        audit["stopping_decision"] = {
            "reason": "invalid_predeclared_policy",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="invalid",
            reasons=(
                "predeclared_pair_assignments_required"
                if predeclared_pair_assignments is None
                else "malformed_predeclared_pair_assignment",
            ),
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            diagnostics={"input_errors": [str(exc)]},
        )

    counts_by_wave = Counter(item.wave for item in assignments)
    derived_jobs_per_wave = max(counts_by_wave.values(), default=max(len(responses), 1))
    derived_max_waves = max(counts_by_wave, default=1)
    jobs_per_wave = (
        derived_jobs_per_wave
        if boundary_jobs_per_wave is None
        else boundary_jobs_per_wave
    )
    max_waves = derived_max_waves if boundary_waves_max is None else boundary_waves_max
    try:
        jobs_per_wave = _positive_int(jobs_per_wave, "boundary_jobs_per_wave")
        max_waves = _positive_int(max_waves, "boundary_waves_max")
        binding_reserved = jobs_per_wave * max_waves
        reserved = binding_reserved if boundary_reserved is None else boundary_reserved
        if isinstance(reserved, bool) or not isinstance(reserved, int) or reserved < 0:
            raise ValueError("boundary_reserved must be a non-negative integer")
        available = (
            reserved
            if available_boundary_reserve is None
            else available_boundary_reserve
        )
        if (
            isinstance(available, bool)
            or not isinstance(available, int)
            or available < 0
        ):
            raise ValueError(
                "available_boundary_reserve must be a non-negative integer"
            )
        if (
            isinstance(finalist_reserved, bool)
            or not isinstance(finalist_reserved, int)
            or finalist_reserved < 0
        ):
            raise ValueError("finalist_reserved must be a non-negative integer")
    except ValueError as exc:
        audit = _base_audit(
            slots=slots,
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            policy_source=policy_source,
            jobs_per_wave=0,
            max_waves=0,
            boundary_reserved=0,
            available_boundary_reserve=0,
            finalist_reserved=(
                finalist_reserved
                if isinstance(finalist_reserved, int)
                and not isinstance(finalist_reserved, bool)
                and finalist_reserved >= 0
                else 0
            ),
        )
        _account_realized_calls(audit, len(responses))
        audit["stopping_decision"] = {
            "reason": "invalid_reserve_policy",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="invalid",
            reasons=("invalid_boundary_reserve_policy",),
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            diagnostics={"input_errors": [str(exc)]},
        )

    audit = _base_audit(
        slots=slots,
        candidates=candidates,
        clear_finalists=clear_finalists,
        clear_non_finalists=clear_non_finalists,
        policy_source=policy_source,
        jobs_per_wave=jobs_per_wave,
        max_waves=max_waves,
        boundary_reserved=reserved,
        available_boundary_reserve=available,
        finalist_reserved=finalist_reserved,
    )
    _account_realized_calls(audit, len(responses))

    policy_reasons: list[str] = []
    policy_errors: list[str] = []
    if reserved != binding_reserved:
        policy_reasons.append("boundary_reserve_not_binding")
        policy_errors.append(
            "boundary_reserved must equal boundary_jobs_per_wave * boundary_waves_max"
        )
    if available > reserved:
        policy_reasons.append("available_boundary_reserve_exceeds_reservation")
        policy_errors.append("available boundary reserve cannot exceed boundary_reserved")
    if len(assignments) > available:
        policy_reasons.append("predeclared_jobs_exceed_boundary_reserve")
        policy_errors.append("predeclared pair assignments exceed available boundary reserve")
    if any(wave > max_waves for wave in counts_by_wave):
        policy_reasons.append("predeclared_wave_exceeds_maximum")
        policy_errors.append("predeclared pair assignment exceeds boundary_waves_max")
    if counts_by_wave and set(counts_by_wave) != set(
        range(1, max(counts_by_wave) + 1)
    ):
        policy_reasons.append("predeclared_waves_not_contiguous")
        policy_errors.append("predeclared pair-assignment waves must start at one without gaps")
    if any(count > jobs_per_wave for count in counts_by_wave.values()):
        policy_reasons.append("predeclared_wave_exceeds_job_cap")
        policy_errors.append("predeclared wave exceeds boundary_jobs_per_wave")
    for assignment in assignments:
        pair = {assignment.first_id, assignment.second_id}
        if not pair <= set(candidates):
            policy_reasons.append("out_of_scope_predeclared_pair")
            policy_errors.append(
                f"pair assignment {assignment.assignment_id} is outside frozen boundary candidates"
            )
    if policy_reasons:
        audit["stopping_decision"] = {
            "reason": "invalid_predeclared_policy",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="invalid",
            reasons=policy_reasons,
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            diagnostics={"input_errors": policy_errors},
        )

    try:
        normalized = _normalize_observations(responses)
        response_metadata: list[tuple[str, str, int, tuple[str, str]]] = []
        seen_response_ids: set[str] = set()
        seen_replicates: set[str] = set()
        for index, response in enumerate(responses):
            if isinstance(response, _Observation):
                metadata = (
                    response.record_id,
                    response.assignment_id,
                    response.wave,
                    (response.first_id, response.second_id),
                )
                replicate = response.synthetic_replicate_id
            else:
                if not isinstance(response, Mapping):
                    raise ValueError(f"observation {index} must be an object")
                metadata = _record_metadata(response, index)
                replicate = response.get(
                    "synthetic_replicate_id", f"synthetic-replicate-{metadata[0]}"
                )
            if metadata[0] in seen_response_ids:
                raise ValueError("boundary response IDs must be unique")
            if not _is_non_empty_string(replicate) or replicate in seen_replicates:
                raise ValueError("boundary synthetic replicate IDs must be unique")
            seen_response_ids.add(metadata[0])
            seen_replicates.add(str(replicate))
            response_metadata.append(metadata)
    except ValueError as exc:
        audit["stopping_decision"] = {
            "reason": "malformed_boundary_response",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="invalid",
            reasons=("malformed_boundary_response",),
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            diagnostics={"input_errors": [str(exc)]},
        )

    assignment_by_id = {item.assignment_id: item for item in assignments}
    response_by_assignment: dict[str, tuple[str, int, tuple[str, str]]] = {}
    response_policy_reasons: list[str] = []
    response_policy_errors: list[str] = []
    for record_id, assignment_id, wave, pair in response_metadata:
        assignment = assignment_by_id.get(assignment_id)
        if assignment is None:
            response_policy_reasons.append("response_not_predeclared")
            response_policy_errors.append(
                f"response {record_id} does not match a predeclared pair assignment"
            )
            continue
        if assignment_id in response_by_assignment:
            response_policy_reasons.append("duplicate_pair_assignment_response")
            response_policy_errors.append(
                f"multiple responses supplied for pair assignment {assignment_id}"
            )
        response_by_assignment[assignment_id] = (record_id, wave, pair)
        if wave != assignment.wave:
            response_policy_reasons.append("response_wave_mismatch")
            response_policy_errors.append(
                f"response {record_id} does not match its predeclared wave"
            )
        if set(pair) != {assignment.first_id, assignment.second_id}:
            response_policy_reasons.append("response_pair_mismatch")
            response_policy_errors.append(
                f"response {record_id} does not match its predeclared pair"
            )
        if not set(pair) <= set(candidates):
            response_policy_reasons.append("out_of_scope_pairwise_response")
            response_policy_errors.append(
                f"response {record_id} compares outside frozen boundary candidates"
            )
    if len(response_metadata) > available:
        response_policy_reasons.append("boundary_reserve_exceeded")
        response_policy_errors.append(
            "realized boundary responses exceed available boundary reserve"
        )
    if response_policy_reasons:
        audit["stopping_decision"] = {
            "reason": "invalid_boundary_response_scope",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="invalid",
            reasons=response_policy_reasons,
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            diagnostics={"input_errors": response_policy_errors},
        )

    if slots > len(candidates):
        audit["stopping_decision"] = {
            "reason": "insufficient_boundary_candidates",
            "wave": None,
            "resolved": False,
        }
        return _result(
            status="unresolved",
            reasons=("insufficient_boundary_candidates",),
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
        )
    if slots == 0 or slots == len(candidates):
        selected = [] if slots == 0 else list(candidates)
        classifications = {
            candidate_id: (
                "clear_finalist" if candidate_id in selected else "clear_non_finalist"
            )
            for candidate_id in candidates
        }
        frequencies = {
            candidate_id: (1.0 if candidate_id in selected else 0.0)
            for candidate_id in candidates
        }
        audit["selection_decisions"] = [
            {
                "candidate_id": candidate_id,
                "conditional_inclusion_frequency": frequencies[candidate_id],
                "classification": classifications[candidate_id],
                "selected": candidate_id in selected,
            }
            for candidate_id in candidates
        ]
        audit["stopping_decision"] = {
            "reason": "no_pairwise_discrimination_required",
            "wave": None,
            "resolved": True,
        }
        trivial_fit = PairwiseFit(
            utilities={},
            ranked_ids=tuple(candidates),
            success=True,
            connected=True,
            identified=True,
            converged=True,
            loss=0.0,
            projected_gradient_norm=0.0,
            iterations=0,
            message="no pairwise discrimination required",
            observation_count=len(normalized),
            candidate_count=len(candidates),
            outcome_counts={
                key: Counter(item.outcome for item in normalized)[key]
                for key in sorted(_OUTCOMES)
            },
        )
        return BoundaryResult(
            status="resolved",
            status_reasons=(),
            selected_ids=selected,
            utilities={},
            ranked_ids=list(candidates),
            inclusion_frequencies=frequencies,
            classifications=classifications,
            candidate_ids=list(candidates),
            frozen_clear_finalist_ids=list(clear_finalists),
            frozen_clear_non_finalist_ids=list(clear_non_finalists),
            model_diagnostics={"fit": trivial_fit.as_dict()},
            decision_audit=audit,
        )

    assignments_by_wave: dict[int, list[_PairAssignment]] = defaultdict(list)
    normalized_by_wave: dict[int, list[_Observation]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_wave[assignment.wave].append(assignment)
    for observation in normalized:
        normalized_by_wave[observation.wave].append(observation)

    consumed = 0
    cumulative: list[_Observation] = []
    final_fit: PairwiseFit | None = None
    final_frequencies: dict[str, float] = {}
    final_classifications: dict[str, str] = {}
    final_bootstrap: dict[str, Any] = {}
    final_reasons: list[str] = []
    stopping_reason = "predeclared_jobs_exhausted"
    stopping_wave: int | None = None
    resolved = False

    for wave in range(1, max_waves + 1):
        planned = sorted(
            assignments_by_wave.get(wave, []), key=lambda item: item.assignment_id
        )
        if not planned:
            stopping_reason = (
                "boundary_reserve_exhausted"
                if consumed >= available
                else "predeclared_jobs_exhausted"
            )
            stopping_wave = wave - 1 if wave > 1 else None
            break
        planned_ids = [item.assignment_id for item in planned]
        received_ids = sorted(set(planned_ids) & set(response_by_assignment))
        if set(received_ids) != set(planned_ids):
            later_responses = [
                item
                for item in response_metadata
                if item[2] > wave
            ]
            if later_responses:
                audit["stopping_decision"] = {
                    "reason": "nonsequential_wave_responses",
                    "wave": wave,
                    "resolved": False,
                }
                return _result(
                    status="invalid",
                    reasons=("nonsequential_wave_responses",),
                    candidates=candidates,
                    clear_finalists=clear_finalists,
                    clear_non_finalists=clear_non_finalists,
                    audit=audit,
                    diagnostics={
                        "input_errors": [
                            "later-wave responses arrived before the current predeclared wave completed"
                        ]
                    },
                )
            consumed += len(received_ids)
            audit["waves"].append(
                {
                    "wave": wave,
                    "predeclared_job_ids": planned_ids,
                    "received_job_ids": received_ids,
                    "usable_response_ids": sorted(
                        item.record_id
                        for item in normalized_by_wave.get(wave, [])
                        if item.assignment_id in received_ids
                    ),
                    "completed": False,
                    "decision": "await_remaining_predeclared_responses",
                }
            )
            audit["next_wave_job_ids"] = sorted(set(planned_ids) - set(received_ids))
            stopping_reason = "awaiting_predeclared_wave_responses"
            stopping_wave = wave
            break
        if consumed + len(received_ids) > available:
            audit["stopping_decision"] = {
                "reason": "boundary_reserve_exceeded",
                "wave": wave,
                "resolved": False,
            }
            return _result(
                status="invalid",
                reasons=("boundary_reserve_exceeded",),
                candidates=candidates,
                clear_finalists=clear_finalists,
                clear_non_finalists=clear_non_finalists,
                audit=audit,
                diagnostics={
                    "input_errors": [
                        "realized boundary responses exceed available boundary reserve"
                    ]
                },
            )
        consumed += len(received_ids)
        cumulative.extend(normalized_by_wave.get(wave, []))
        observed_segments = {item.segment_id for item in cumulative}
        expected_segments = (
            set(segment_weights) if isinstance(segment_weights, Mapping) else None
        )
        segment_coverage_complete = (
            expected_segments is None or observed_segments == expected_segments
        )
        if not segment_coverage_complete:
            connected = _comparison_graph_connected(candidates, cumulative)
            fit = _invalid_fit(
                connected=connected,
                identified=False,
                message="locked segment coverage is incomplete in this wave",
                observation_count=len(cumulative),
                candidate_count=len(candidates),
                outcome_counts=Counter(item.outcome for item in cumulative),
            )
        else:
            try:
                fit = fit_davidson(
                    cumulative,
                    config,
                    segment_weights=segment_weights,
                    candidate_ids=candidates,
                )
            except ValueError as exc:
                audit["stopping_decision"] = {
                    "reason": "malformed_boundary_response",
                    "wave": wave,
                    "resolved": False,
                }
                return _result(
                    status="invalid",
                    reasons=("malformed_boundary_response",),
                    candidates=candidates,
                    clear_finalists=clear_finalists,
                    clear_non_finalists=clear_non_finalists,
                    audit=audit,
                    diagnostics={"input_errors": [str(exc)]},
                )
        frequencies: dict[str, float] = {}
        bootstrap: dict[str, Any] = {
            "requested_fits": config.bootstrap_count,
            "successful_fits": 0,
            "successful_fit_rate": 0.0,
            "successful_fit_floor": config.successful_fit_floor,
            "conditional_on_realized_pair_assignments": True,
        }
        classifications = {candidate_id: "unresolved" for candidate_id in candidates}
        selected: list[str] = []
        wave_reasons: list[str] = []
        if not segment_coverage_complete:
            wave_reasons.append("locked_segment_coverage_incomplete")
        elif not fit.connected:
            wave_reasons.append("comparison_graph_disconnected")
        elif not fit.identified:
            wave_reasons.append("pairwise_model_unidentified")
        elif not fit.converged or not fit.success:
            wave_reasons.append("pairwise_optimizer_not_converged")
        else:
            frequencies, bootstrap = _bootstrap_stability(
                tuple(cumulative), candidates, slots, config, segment_weights
            )
            classifications = {
                candidate_id: classify_inclusion_frequency(frequencies[candidate_id])
                for candidate_id in candidates
            }
            selected = [
                candidate_id
                for candidate_id in fit.ranked_ids
                if classifications[candidate_id] == "clear_finalist"
            ]
            if bootstrap["successful_fit_rate"] < config.successful_fit_floor:
                wave_reasons.append("bootstrap_successful_fit_floor_not_met")
            if len(selected) != slots:
                wave_reasons.append("conditional_inclusion_rule_not_met")
        wave_resolved = not wave_reasons and len(selected) == slots
        audit["waves"].append(
            {
                "wave": wave,
                "predeclared_job_ids": planned_ids,
                "received_job_ids": received_ids,
                "usable_response_ids": sorted(
                    item.record_id for item in normalized_by_wave.get(wave, [])
                ),
                "completed": True,
                "cumulative_boundary_jobs_consumed": consumed,
                "cumulative_usable_observations": len(cumulative),
                "fit": fit.as_dict(),
                "conditional_inclusion_frequencies": dict(frequencies),
                "classifications": dict(classifications),
                "bootstrap": bootstrap,
                "selected_boundary_ids": selected,
                "decision": "stop_resolved" if wave_resolved else "continue_if_predeclared",
                "decision_reasons": wave_reasons,
            }
        )
        final_fit = fit
        final_frequencies = frequencies
        final_classifications = classifications
        final_bootstrap = bootstrap
        final_reasons = wave_reasons
        stopping_wave = wave
        if wave_resolved:
            later_received = [
                metadata
                for metadata in response_metadata
                if metadata[2] > wave
            ]
            if later_received:
                audit["stopping_decision"] = {
                    "reason": "responses_after_inclusion_stop",
                    "wave": wave,
                    "resolved": False,
                }
                return _result(
                    status="invalid",
                    reasons=("responses_after_inclusion_stop",),
                    candidates=candidates,
                    clear_finalists=clear_finalists,
                    clear_non_finalists=clear_non_finalists,
                    audit=audit,
                    diagnostics={
                        "input_errors": [
                            "later-wave responses were generated after the inclusion stopping rule was met"
                        ]
                    },
                )
            resolved = True
            stopping_reason = "inclusion_rule_satisfied"
            break
        if wave == max_waves:
            stopping_reason = "maximum_waves_reached"
            break
        next_jobs = sorted(
            item.assignment_id for item in assignments_by_wave.get(wave + 1, [])
        )
        if consumed >= available:
            stopping_reason = "boundary_reserve_exhausted"
            break
        if not next_jobs:
            stopping_reason = "predeclared_jobs_exhausted"
            break
        audit["next_wave_job_ids"] = next_jobs

    audit["reserve"].update(
        {
            "finalist_reserved_after": finalist_reserved,
            "finalist_reserve_consumed": 0,
        }
    )
    if resolved:
        audit["next_wave_job_ids"] = []
    audit["stopping_decision"] = {
        "reason": stopping_reason,
        "wave": stopping_wave,
        "resolved": resolved,
    }
    audit["selection_decisions"] = [
        {
            "candidate_id": candidate_id,
            "conditional_inclusion_frequency": final_frequencies.get(candidate_id),
            "classification": final_classifications.get(candidate_id, "unresolved"),
            "selected": bool(
                resolved
                and final_classifications.get(candidate_id) == "clear_finalist"
            ),
        }
        for candidate_id in candidates
    ]
    diagnostics = {
        "fit": final_fit.as_dict() if final_fit is not None else None,
        "bootstrap": final_bootstrap,
        "configuration": {
            "model": "davidson",
            "tie_parameter": config.tie_parameter,
            "penalty_lambda": config.penalty_lambda,
            "optimizer_tolerance": config.optimizer_tolerance,
            "bootstrap_count": config.bootstrap_count,
            "successful_fit_floor": config.successful_fit_floor,
            "seed": config.seed,
            "sum_to_zero_identification": True,
            "cutoff_tie_policy": "symmetric_fractional_inclusion",
            "cutoff_tie_tolerance": max(
                float(config.optimizer_tolerance),
                _MINIMUM_UTILITY_TIE_TOLERANCE,
            ),
            "creative_id_tie_breaker": "display_order_only_never_selection",
        },
        "utility_scale": "pairwise_boundary_only_not_comparable_to_maxdiff",
    }
    if resolved and final_fit is not None:
        selected = [
            candidate_id
            for candidate_id in final_fit.ranked_ids
            if final_classifications.get(candidate_id) == "clear_finalist"
        ]
        return _result(
            status="resolved",
            reasons=(),
            candidates=candidates,
            clear_finalists=clear_finalists,
            clear_non_finalists=clear_non_finalists,
            audit=audit,
            fit=final_fit,
            frequencies=final_frequencies,
            classifications=final_classifications,
            selected=selected,
            diagnostics=diagnostics,
        )

    unresolved_reasons = list(final_reasons)
    if stopping_reason not in {
        "not_evaluated",
        "inclusion_rule_satisfied",
    }:
        unresolved_reasons.append(stopping_reason)
    if not unresolved_reasons:
        unresolved_reasons.append("insufficient_usable_pairwise_observations")
    return _result(
        status="unresolved",
        reasons=unresolved_reasons,
        candidates=candidates,
        clear_finalists=clear_finalists,
        clear_non_finalists=clear_non_finalists,
        audit=audit,
        diagnostics=diagnostics,
    )


__all__ = [
    "BoundaryResult",
    "IndexedPairwiseObservation",
    "PairwiseConfig",
    "PairwiseFit",
    "classify_inclusion_frequency",
    "davidson_loss_and_gradient",
    "davidson_probabilities",
    "fit_davidson",
    "resolve_boundary",
    "symmetric_cutoff_inclusion",
]
