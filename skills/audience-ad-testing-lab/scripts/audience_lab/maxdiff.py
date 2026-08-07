"""Deterministic weighted joint MaxDiff estimation and shortlist stability gates.

Utilities from this module are centered, protocol-relative log utilities.  They
describe the best common fit to the locked synthetic-segment mixture in one
study.  They are not human-population quantities and are not comparable across
protocols or studies.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import LinearConstraint, minimize
from scipy.special import logsumexp


_DEFAULT_SEGMENT = "__all_records__"
_DEFAULT_ARCHETYPE = "__archetype_not_supplied__"
_DEFAULT_PROFILE = "__grounded_profile_not_supplied__"
_REQUIRED_BOOTSTRAP_COUNT = 2000
_MINIMUM_SUCCESSFUL_FIT_FLOOR = 0.95
_CLEAR_FINALIST_THRESHOLD = 0.90
_CLEAR_NON_FINALIST_THRESHOLD = 0.10
_MINIMUM_UTILITY_TIE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class MaxDiffConfig:
    """Fixed estimation and conditional-stability rules for one model run."""

    penalty_lambda: float
    optimizer_tolerance: float = 1e-8
    bootstrap_count: int = 2000
    successful_fit_floor: float = 0.95
    clear_finalist_threshold: float = 0.90
    clear_non_finalist_threshold: float = 0.10
    seed: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.penalty_lambda, bool)
            or not isinstance(self.penalty_lambda, (int, float))
            or not math.isfinite(self.penalty_lambda)
            or self.penalty_lambda < 0
        ):
            raise ValueError("penalty_lambda must be a finite non-negative number")
        if (
            isinstance(self.optimizer_tolerance, bool)
            or not isinstance(self.optimizer_tolerance, (int, float))
            or not math.isfinite(self.optimizer_tolerance)
            or self.optimizer_tolerance <= 0
        ):
            raise ValueError("optimizer_tolerance must be a finite positive number")
        if isinstance(self.bootstrap_count, bool) or not isinstance(self.bootstrap_count, int):
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
            isinstance(self.clear_finalist_threshold, bool)
            or not isinstance(self.clear_finalist_threshold, (int, float))
            or not math.isfinite(self.clear_finalist_threshold)
            or isinstance(self.clear_non_finalist_threshold, bool)
            or not isinstance(self.clear_non_finalist_threshold, (int, float))
            or not math.isfinite(self.clear_non_finalist_threshold)
            or not 0
            <= self.clear_non_finalist_threshold
            < self.clear_finalist_threshold
            <= 1
        ):
            raise ValueError("shortlist thresholds must satisfy 0 <= lower < upper <= 1")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")


@dataclass(frozen=True)
class IndexedObservation:
    """One best-worst record translated to optimizer vector indices."""

    block_indices: tuple[int, ...]
    best_index: int
    worst_index: int


@dataclass(frozen=True)
class _Observation:
    record_id: str
    segment_id: str
    archetype_id: str
    profile_id: str
    block_ids: tuple[str, ...]
    best_id: str
    worst_id: str


@dataclass(frozen=True)
class MaxDiffFit:
    """One global fit, including explicit refusal states."""

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
    creative_count: int

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
            "creative_count": self.creative_count,
        }


@dataclass(frozen=True)
class ScreeningResult:
    """Protocol-relative screening result with deterministic gate reasons."""

    validity_status: str
    validity_reasons: tuple[str, ...]
    utilities: dict[str, float]
    ranked_ids: tuple[str, ...]
    top_k_inclusion_frequencies: dict[str, float]
    classifications: dict[str, str]
    archetype_sensitivity: dict[str, Any]
    diagnostics: dict[str, Any]
    requested_top_k: int
    recovery_config_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimand": "centered_protocol_relative_log_utility",
            "stability_diagnostic": "conditional_within_run_top_k_inclusion_frequency",
            "requested_top_k": self.requested_top_k,
            "utilities": dict(self.utilities),
            "ranked_ids": list(self.ranked_ids),
            "top_k_inclusion_frequencies": dict(self.top_k_inclusion_frequencies),
            "classifications": dict(self.classifications),
            "archetype_sensitivity": self.archetype_sensitivity,
            "model_diagnostics": self.diagnostics,
            "recovery_config_version": self.recovery_config_version,
            "validity_status": self.validity_status,
            "validity_reasons": list(self.validity_reasons),
            "interpretation_limits": [
                "Utilities are centered and protocol-relative.",
                "Conditional stability reflects this synthetic model-call run only.",
                "Results do not establish human-response or campaign-performance validity.",
            ],
        }


def maxdiff_loss_and_gradient(
    u: np.ndarray,
    observations: Sequence[IndexedObservation],
    weights: Sequence[float],
    penalty_lambda: float,
) -> tuple[float, np.ndarray]:
    """Return the penalized joint best-worst negative log likelihood and gradient."""

    utilities = np.asarray(u, dtype=float)
    analysis_weights = np.asarray(weights, dtype=float)
    if len(observations) != len(analysis_weights):
        raise ValueError("observations and weights must have equal length")
    loss = 0.5 * penalty_lambda * float(np.dot(utilities, utilities))
    gradient = penalty_lambda * utilities.copy()
    for observation, weight in zip(observations, analysis_weights, strict=True):
        block = tuple(observation.block_indices)
        pairs = tuple((left, right) for left in block for right in block if left != right)
        if not pairs:
            raise ValueError("each MaxDiff block must contain at least two creatives")
        logits = np.asarray(
            [utilities[left] - utilities[right] for left, right in pairs], dtype=float
        )
        log_denominator = float(logsumexp(logits))
        probabilities = np.exp(logits - log_denominator)
        best = observation.best_index
        worst = observation.worst_index
        loss += float(weight) * (
            log_denominator - (utilities[best] - utilities[worst])
        )
        gradient[best] -= float(weight)
        gradient[worst] += float(weight)
        for probability, (left, right) in zip(probabilities, pairs, strict=True):
            gradient[left] += float(weight) * float(probability)
            gradient[right] -= float(weight) * float(probability)
    return float(loss), gradient


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_usable(payload: Mapping[str, Any]) -> bool:
    if "usable_maxdiff_block" in payload:
        return payload.get("usable_maxdiff_block") is True
    if "usable" in payload:
        return payload.get("usable") is True
    return True


def _normalize_observations(
    observations: Sequence[Mapping[str, Any] | _Observation],
) -> tuple[_Observation, ...]:
    normalized: list[_Observation] = []
    for index, payload in enumerate(observations):
        if isinstance(payload, _Observation):
            normalized.append(payload)
            continue
        if not isinstance(payload, Mapping):
            raise ValueError(f"observation {index} must be an object")
        if not _is_usable(payload):
            continue

        block = payload.get("assigned_variation_ids", payload.get("block"))
        if not _is_array(block) or len(block) != 4:
            raise ValueError(f"observation {index} must contain exactly four creatives")
        if not all(_is_non_empty_string(item) for item in block):
            raise ValueError(f"observation {index} creative IDs must be non-empty strings")
        block_ids = tuple(block)
        if len(set(block_ids)) != len(block_ids):
            raise ValueError(f"observation {index} creative IDs must be unique")

        choice = payload.get("comparative_choice")
        if isinstance(choice, Mapping):
            if choice.get("status", "best_worst") != "best_worst":
                continue
            best_id = choice.get("best_variation_id")
            worst_id = choice.get("weakest_variation_id")
        else:
            best_id = payload.get("best_variation_id", payload.get("best"))
            worst_id = payload.get(
                "weakest_variation_id", payload.get("worst_variation_id", payload.get("worst"))
            )
        if best_id not in block_ids or worst_id not in block_ids:
            raise ValueError(f"observation {index} best and weakest IDs must be in its block")
        if best_id == worst_id:
            raise ValueError(f"observation {index} best and weakest IDs must differ")

        record_id = payload.get("response_id", payload.get("record_id", f"record-{index}"))
        segment_id = payload.get("segment_id", _DEFAULT_SEGMENT)
        archetype_id = payload.get("persona_archetype_id", _DEFAULT_ARCHETYPE)
        profile_id = payload.get("grounded_profile_id", _DEFAULT_PROFILE)
        if not _is_non_empty_string(record_id):
            raise ValueError(f"observation {index} record ID must be a non-empty string")
        if not _is_non_empty_string(segment_id):
            raise ValueError(f"observation {index} segment_id must be a non-empty string")
        if not _is_non_empty_string(archetype_id):
            raise ValueError(
                f"observation {index} persona_archetype_id must be a non-empty string"
            )
        if not _is_non_empty_string(profile_id):
            raise ValueError(
                f"observation {index} grounded_profile_id must be a non-empty string"
            )
        normalized.append(
            _Observation(
                record_id=record_id,
                segment_id=segment_id,
                archetype_id=archetype_id,
                profile_id=profile_id,
                block_ids=block_ids,
                best_id=best_id,
                worst_id=worst_id,
            )
        )
    record_ids = [item.record_id for item in normalized]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("usable MaxDiff record IDs must be unique")
    return tuple(normalized)


def _creative_roster(
    observations: Sequence[Mapping[str, Any] | _Observation],
) -> tuple[str, ...]:
    creative_ids: set[str] = set()
    for payload in observations:
        if isinstance(payload, _Observation):
            creative_ids.update(payload.block_ids)
            continue
        if not isinstance(payload, Mapping):
            continue
        block = payload.get("assigned_variation_ids", payload.get("block"))
        if _is_array(block):
            creative_ids.update(item for item in block if _is_non_empty_string(item))
    return tuple(sorted(creative_ids))


def _normalized_weights(weights: Sequence[float], count: int) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if values.shape != (count,):
        raise ValueError("analysis weights must match the usable observation count")
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("analysis weights must be finite positive numbers")
    total = float(values.sum())
    if total <= 0:
        raise ValueError("analysis weights must have a positive sum")
    return values * (count / total)


def _weights_for_normalized(
    observations: Sequence[_Observation],
    segment_weights: Mapping[str, float] | None,
    profile_weights: Mapping[str, float] | None = None,
) -> np.ndarray:
    if not observations:
        return np.asarray([], dtype=float)
    if segment_weights is None and profile_weights is None:
        return np.ones(len(observations), dtype=float)
    if not isinstance(segment_weights, Mapping) or not segment_weights:
        raise ValueError("locked segment weights must be a non-empty object")
    observed_counts = Counter(item.segment_id for item in observations)
    supplied_segments = set(segment_weights)
    observed_segments = set(observed_counts)
    if supplied_segments != observed_segments:
        missing = sorted(observed_segments - supplied_segments)
        empty = sorted(supplied_segments - observed_segments)
        details = []
        if missing:
            details.append(f"missing locked weights for {','.join(missing)}")
        if empty:
            details.append(f"locked segments have no usable records: {','.join(empty)}")
        raise ValueError("; ".join(details))
    parsed: dict[str, float] = {}
    for segment_id, weight in segment_weights.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError("locked segment weights must be finite positive numbers")
        parsed[segment_id] = float(weight)
    target_total = sum(parsed.values())
    if target_total <= 0:
        raise ValueError("locked segment weights must have a positive sum")
    target = {segment_id: weight / target_total for segment_id, weight in parsed.items()}
    record_count = len(observations)
    if profile_weights is None:
        raw = [
            target[item.segment_id] / (observed_counts[item.segment_id] / record_count)
            for item in observations
        ]
        return _normalized_weights(raw, record_count)

    if not isinstance(profile_weights, Mapping) or not profile_weights:
        raise ValueError("locked profile weights must be a non-empty object")
    observed_profile_counts = Counter(item.profile_id for item in observations)
    if set(profile_weights) != set(observed_profile_counts):
        missing = sorted(set(observed_profile_counts) - set(profile_weights))
        empty = sorted(set(profile_weights) - set(observed_profile_counts))
        details = []
        if missing:
            details.append(f"missing locked weights for profiles {','.join(missing)}")
        if empty:
            details.append(f"locked profiles have no usable records: {','.join(empty)}")
        raise ValueError("; ".join(details))
    parsed_profiles: dict[str, float] = {}
    for profile_id, weight in profile_weights.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError("locked profile weights must be finite positive numbers")
        parsed_profiles[str(profile_id)] = float(weight)
    profile_total = sum(parsed_profiles.values())
    profile_target = {
        profile_id: weight / profile_total
        for profile_id, weight in parsed_profiles.items()
    }
    profile_segments: dict[str, str] = {}
    for item in observations:
        previous = profile_segments.setdefault(item.profile_id, item.segment_id)
        if previous != item.segment_id:
            raise ValueError("a grounded profile cannot span multiple reported segments")
    represented_segment_targets: dict[str, float] = defaultdict(float)
    for profile_id, weight in profile_target.items():
        represented_segment_targets[profile_segments[profile_id]] += weight
    for segment_id, weight in target.items():
        if not math.isclose(
            represented_segment_targets.get(segment_id, 0.0),
            weight,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("locked profile weights must reconcile to locked segment weights")
    raw = [
        profile_target[item.profile_id]
        / (observed_profile_counts[item.profile_id] / record_count)
        for item in observations
    ]
    return _normalized_weights(raw, record_count)


def compute_analysis_weights(
    observations: Sequence[Mapping[str, Any] | _Observation],
    segment_weights: Mapping[str, float] | None,
    *,
    profile_weights: Mapping[str, float] | None = None,
) -> tuple[float, ...]:
    """Compute frozen-mixture weights for usable whole records.

    When profile weights are supplied they are the primary fixed strata and
    must reconcile exactly to the locked segment mixture.
    """

    normalized = _normalize_observations(observations)
    return tuple(
        float(item)
        for item in _weights_for_normalized(
            normalized,
            segment_weights,
            profile_weights,
        )
    )


def profile_conditioned_connectivity(
    observations: Sequence[Mapping[str, Any] | _Observation],
    creative_ids: Sequence[str],
) -> dict[str, Any]:
    """Report profile-specific and leave-one-profile-out graph support."""

    normalized = _normalize_observations(observations)
    roster = tuple(sorted(set(creative_ids)))
    profiles = tuple(sorted({item.profile_id for item in normalized}))
    by_profile = {
        profile_id: _comparison_graph_connected(
            roster,
            tuple(item for item in normalized if item.profile_id == profile_id),
        )
        for profile_id in profiles
    }
    after_omitting = {
        profile_id: _comparison_graph_connected(
            roster,
            tuple(item for item in normalized if item.profile_id != profile_id),
        )
        for profile_id in profiles
    }
    return {
        "profile_count": len(profiles),
        "profile_graphs_connected": all(by_profile.values()) if by_profile else False,
        "by_profile_connected": by_profile,
        "survives_any_one_profile_removal": (
            all(after_omitting.values()) if len(profiles) >= 2 else False
        ),
        "connected_after_omitting": after_omitting,
        "disconnected_after_omitting": sorted(
            profile_id
            for profile_id, connected in after_omitting.items()
            if not connected
        ),
    }


def usable_participation_counts(
    observations: Sequence[Mapping[str, Any] | _Observation],
    creative_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Count only accepted usable best-worst blocks for each creative."""

    normalized = _normalize_observations(observations)
    roster = tuple(sorted(set(creative_ids or _creative_roster(observations))))
    counts = {creative_id: 0 for creative_id in roster}
    for observation in normalized:
        for creative_id in observation.block_ids:
            counts.setdefault(creative_id, 0)
            counts[creative_id] += 1
    return dict(sorted(counts.items()))


def usable_participation_counts_by_profile(
    observations: Sequence[Mapping[str, Any] | _Observation],
    creative_ids: Sequence[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Count accepted creative participations inside each grounded profile."""

    normalized = _normalize_observations(observations)
    roster = tuple(sorted(set(creative_ids or _creative_roster(observations))))
    profiles = tuple(sorted({item.profile_id for item in normalized}))
    counts = {
        profile_id: {creative_id: 0 for creative_id in roster}
        for profile_id in profiles
    }
    for observation in normalized:
        for creative_id in observation.block_ids:
            counts[observation.profile_id][creative_id] += 1
    return counts


def _comparison_graph_connected(
    creative_ids: Sequence[str], observations: Sequence[_Observation]
) -> bool:
    roster = tuple(creative_ids)
    if len(roster) < 2:
        return False
    neighbors: dict[str, set[str]] = {creative_id: set() for creative_id in roster}
    for observation in observations:
        for position, left in enumerate(observation.block_ids):
            if left not in neighbors:
                continue
            for right in observation.block_ids[position + 1 :]:
                if right not in neighbors:
                    continue
                neighbors[left].add(right)
                neighbors[right].add(left)
    visited: set[str] = set()
    pending = [roster[0]]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(neighbors[current] - visited, reverse=True))
    return visited == set(roster)


def _invalid_fit(
    *,
    connected: bool,
    identified: bool,
    message: str,
    observation_count: int,
    creative_count: int,
) -> MaxDiffFit:
    return MaxDiffFit(
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
        creative_count=creative_count,
    )


def _fit_normalized(
    observations: Sequence[_Observation],
    config: MaxDiffConfig,
    weights: Sequence[float],
    creative_ids: Sequence[str],
) -> MaxDiffFit:
    roster = tuple(sorted(set(creative_ids)))
    connected = _comparison_graph_connected(roster, observations)
    identified = len(roster) >= 2 and bool(observations) and connected
    if not observations:
        return _invalid_fit(
            connected=False,
            identified=False,
            message="no usable MaxDiff blocks",
            observation_count=0,
            creative_count=len(roster),
        )
    if not connected:
        return _invalid_fit(
            connected=False,
            identified=False,
            message="comparison graph is disconnected",
            observation_count=len(observations),
            creative_count=len(roster),
        )
    if not identified:
        return _invalid_fit(
            connected=connected,
            identified=False,
            message="MaxDiff model is not identified",
            observation_count=len(observations),
            creative_count=len(roster),
        )

    index_by_id = {creative_id: index for index, creative_id in enumerate(roster)}
    indexed = tuple(
        IndexedObservation(
            tuple(index_by_id[item] for item in observation.block_ids),
            index_by_id[observation.best_id],
            index_by_id[observation.worst_id],
        )
        for observation in observations
    )
    normalized_weights = _normalized_weights(weights, len(observations))

    def objective(values: np.ndarray) -> float:
        return maxdiff_loss_and_gradient(
            values, indexed, normalized_weights, config.penalty_lambda
        )[0]

    def gradient(values: np.ndarray) -> np.ndarray:
        return maxdiff_loss_and_gradient(
            values, indexed, normalized_weights, config.penalty_lambda
        )[1]

    constraint = LinearConstraint(np.ones((1, len(roster))), np.asarray([0.0]), np.asarray([0.0]))
    try:
        optimized = minimize(
            objective,
            np.zeros(len(roster), dtype=float),
            method="SLSQP",
            jac=gradient,
            constraints=(constraint,),
            options={"ftol": config.optimizer_tolerance, "maxiter": 1000, "disp": False},
        )
    except (FloatingPointError, ValueError, ArithmeticError) as exc:
        return _invalid_fit(
            connected=True,
            identified=True,
            message=f"optimizer failure: {exc}",
            observation_count=len(observations),
            creative_count=len(roster),
        )

    values = np.asarray(optimized.x, dtype=float)
    constraint_residual = abs(float(values.sum()))
    finite = bool(np.all(np.isfinite(values)) and math.isfinite(float(optimized.fun)))
    converged = bool(
        optimized.success
        and finite
        and constraint_residual <= max(1e-9, config.optimizer_tolerance * 10)
    )
    if not converged:
        return MaxDiffFit(
            utilities={},
            ranked_ids=(),
            success=False,
            connected=True,
            identified=True,
            converged=False,
            loss=float(optimized.fun) if math.isfinite(float(optimized.fun)) else None,
            projected_gradient_norm=None,
            iterations=int(getattr(optimized, "nit", 0)),
            message=str(optimized.message),
            observation_count=len(observations),
            creative_count=len(roster),
        )

    values -= float(values.mean())
    final_gradient = gradient(values)
    projected = final_gradient - float(final_gradient.mean())
    utilities = {creative_id: float(values[index_by_id[creative_id]]) for creative_id in roster}
    tie_tolerance = max(
        float(config.optimizer_tolerance), _MINIMUM_UTILITY_TIE_TOLERANCE
    )
    ranked = tuple(
        sorted(
            roster,
            key=lambda item: (-round(utilities[item] / tie_tolerance), item),
        )
    )
    return MaxDiffFit(
        utilities=utilities,
        ranked_ids=ranked,
        success=True,
        connected=True,
        identified=True,
        converged=True,
        loss=float(objective(values)),
        projected_gradient_norm=float(np.linalg.norm(projected)),
        iterations=int(getattr(optimized, "nit", 0)),
        message=str(optimized.message),
        observation_count=len(observations),
        creative_count=len(roster),
    )


def fit_maxdiff(
    observations: Sequence[Mapping[str, Any] | _Observation],
    config: MaxDiffConfig,
    *,
    segment_weights: Mapping[str, float] | None = None,
    profile_weights: Mapping[str, float] | None = None,
    analysis_weights: Sequence[float] | None = None,
    creative_ids: Sequence[str] | None = None,
) -> MaxDiffFit:
    """Fit a connected, sum-to-zero joint MaxDiff model or return refusal diagnostics."""

    normalized = _normalize_observations(observations)
    roster = tuple(
        sorted(
            set(_creative_roster(observations) if creative_ids is None else creative_ids)
        )
    )
    observed_creatives = {
        creative_id for observation in normalized for creative_id in observation.block_ids
    }
    outside_roster = sorted(observed_creatives - set(roster))
    if outside_roster:
        raise ValueError(
            "usable observations contain creatives outside the locked roster: "
            + ",".join(outside_roster)
        )
    if analysis_weights is not None and segment_weights is not None:
        raise ValueError("provide analysis_weights or segment_weights, not both")
    weights = (
        _normalized_weights(analysis_weights, len(normalized))
        if analysis_weights is not None
        else _weights_for_normalized(normalized, segment_weights, profile_weights)
    )
    return _fit_normalized(normalized, config, weights, roster)


def _block_resilient(creative_ids: Sequence[str], observations: Sequence[_Observation]) -> bool:
    if len(observations) < 2:
        return False
    return all(
        _comparison_graph_connected(
            creative_ids, observations[:index] + observations[index + 1 :]
        )
        for index in range(len(observations))
    )


def _bootstrap_stability(
    observations: tuple[_Observation, ...],
    creative_ids: tuple[str, ...],
    segment_weights: Mapping[str, float],
    top_k: int,
    config: MaxDiffConfig,
    profile_weights: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    rng = np.random.default_rng(config.seed)
    strata: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        stratum_id = observation.profile_id if profile_weights is not None else observation.segment_id
        strata[stratum_id].append(observation)
    ordered_strata = tuple(sorted(strata))
    inclusion_counts = {creative_id: 0 for creative_id in creative_ids}
    successful = 0
    disconnected = 0
    nonconvergent = 0
    for _ in range(config.bootstrap_count):
        sampled: list[_Observation] = []
        for stratum_id in ordered_strata:
            records = strata[stratum_id]
            selected = rng.integers(0, len(records), size=len(records))
            sampled.extend(records[int(index)] for index in selected)
        sample_tuple = tuple(sampled)
        weights = _weights_for_normalized(
            sample_tuple,
            segment_weights,
            profile_weights,
        )
        fitted = _fit_normalized(sample_tuple, config, weights, creative_ids)
        if not fitted.connected or not fitted.identified:
            disconnected += 1
            continue
        if not fitted.success:
            nonconvergent += 1
            continue
        successful += 1
        for creative_id in fitted.ranked_ids[:top_k]:
            inclusion_counts[creative_id] += 1
    frequencies = {
        creative_id: (inclusion_counts[creative_id] / successful if successful else 0.0)
        for creative_id in creative_ids
    }
    fit_rate = successful / config.bootstrap_count
    diagnostics = {
        "resample_unit": "whole_synthetic_replicate_record",
        "stratification": (
            "locked_grounded_profile" if profile_weights is not None else "locked_segment"
        ),
        "seed": config.seed,
        "requested_fits": config.bootstrap_count,
        "successful_fits": successful,
        "disconnected_fits": disconnected,
        "nonconvergent_fits": nonconvergent,
        "successful_fit_rate": fit_rate,
        "successful_fit_floor": config.successful_fit_floor,
    }
    return frequencies, diagnostics


def _archetype_sensitivity(
    observations: tuple[_Observation, ...],
    creative_ids: tuple[str, ...],
    segment_weights: Mapping[str, float],
    base_top_k: tuple[str, ...],
    top_k: int,
    config: MaxDiffConfig,
) -> dict[str, Any]:
    archetypes = tuple(sorted({item.archetype_id for item in observations}))
    results: list[dict[str, Any]] = []
    changed: list[str] = []
    successful = 0
    for archetype_id in archetypes:
        retained = tuple(item for item in observations if item.archetype_id != archetype_id)
        try:
            weights = _weights_for_normalized(retained, segment_weights)
            fitted = _fit_normalized(retained, config, weights, creative_ids)
        except ValueError as exc:
            results.append(
                {
                    "omitted_archetype_id": archetype_id,
                    "fit_status": "unavailable",
                    "reason": str(exc),
                    "ranked_ids": [],
                    "top_k_changed": True,
                }
            )
            changed.append(archetype_id)
            continue
        fit_status = "successful" if fitted.success else "unavailable"
        changed_top_k = not fitted.success or set(fitted.ranked_ids[:top_k]) != set(
            base_top_k
        )
        if fitted.success:
            successful += 1
        if changed_top_k:
            changed.append(archetype_id)
        results.append(
            {
                "omitted_archetype_id": archetype_id,
                "fit_status": fit_status,
                "reason": None if fitted.success else fitted.message,
                "ranked_ids": list(fitted.ranked_ids),
                "top_k_changed": changed_top_k,
            }
        )
    return {
        "method": "leave_one_persona_archetype_out",
        "unique_archetypes": len(archetypes),
        "attempted_fits": len(archetypes),
        "successful_fits": successful,
        "successful_fit_rate": successful / len(archetypes) if archetypes else 0.0,
        "top_k_consistent": bool(archetypes) and not changed,
        "top_k_changed_for": changed,
        "results": results,
    }


def classify_top_k_frequency(
    frequency: float, clear_finalist_threshold: float, clear_non_finalist_threshold: float
) -> str:
    """Apply the inclusive 0.90/0.10 product-rule boundaries."""

    if not isinstance(frequency, (int, float)) or not math.isfinite(frequency):
        return "unresolved"
    if frequency >= clear_finalist_threshold:
        return "clear_finalist"
    if frequency <= clear_non_finalist_threshold:
        return "clear_non_finalist"
    return "boundary_candidate"


def _default_recovery_config(config: MaxDiffConfig) -> dict[str, Any]:
    return {
        "version": "unversioned-exploratory-default",
        "calibration_status": "exploratory_only",
        "library_size_bands": [],
        "shortlist_size_bands": [],
        "segment_count": {"minimum": 1, "maximum": 100},
        "tie_inability_band": {"minimum_rate": 0.0, "maximum_rate": 1.0},
        "utility_separation_band": {
            "minimum_log_utility_gap": 0.0,
            "maximum_log_utility_gap": 1.0e12,
        },
        "planned_participation_floor": 9,
        "usable_participation_floor": 8,
        "bootstrap_count": config.bootstrap_count,
        "successful_fit_floor": config.successful_fit_floor,
        "shortlist_thresholds": {
            "clear_finalist": config.clear_finalist_threshold,
            "clear_non_finalist": config.clear_non_finalist_threshold,
        },
    }


def _number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"recovery configuration {key} must be a finite number")
    return float(value)


def _validate_recovery_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("recovery configuration must be an object")
    required = {
        "version",
        "calibration_status",
        "library_size_bands",
        "shortlist_size_bands",
        "segment_count",
        "tie_inability_band",
        "utility_separation_band",
        "planned_participation_floor",
        "usable_participation_floor",
        "bootstrap_count",
        "successful_fit_floor",
        "shortlist_thresholds",
    }
    if set(payload) != required:
        missing = sorted(required - set(payload))
        extra = sorted(set(payload) - required)
        raise ValueError(
            "recovery configuration keys are not canonical"
            f" (missing={missing}, extra={extra})"
        )
    if not _is_non_empty_string(payload.get("version")):
        raise ValueError("recovery configuration version must be a non-empty string")
    if payload.get("calibration_status") not in {"exploratory_only", "calibrated"}:
        raise ValueError("recovery configuration calibration_status is invalid")
    calibrated = payload.get("calibration_status") == "calibrated"
    for bands_key in ("library_size_bands", "shortlist_size_bands"):
        bands = payload.get(bands_key)
        if not _is_array(bands):
            raise ValueError(f"recovery configuration {bands_key} must be an array")
        if calibrated and not bands:
            raise ValueError(
                f"calibrated recovery configuration {bands_key} must be non-empty"
            )
        names: set[str] = set()
        for band in bands:
            if not isinstance(band, Mapping):
                raise ValueError(f"recovery configuration {bands_key} entries must be objects")
            if set(band) != {"name", "minimum", "maximum"}:
                raise ValueError(
                    f"recovery configuration {bands_key} entries are not canonical"
                )
            name = band.get("name")
            if not _is_non_empty_string(name) or name in names:
                raise ValueError(
                    f"recovery configuration {bands_key} names must be unique non-empty strings"
                )
            names.add(name)
            minimum = band.get("minimum")
            maximum = band.get("maximum")
            if (
                isinstance(minimum, bool)
                or not isinstance(minimum, int)
                or minimum < 1
                or isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or maximum < minimum
            ):
                raise ValueError(f"recovery configuration {bands_key} range is invalid")

    segment_range = payload.get("segment_count")
    if not isinstance(segment_range, Mapping) or set(segment_range) != {
        "minimum",
        "maximum",
    }:
        raise ValueError("recovery configuration segment_count is not canonical")
    segment_minimum = segment_range.get("minimum")
    segment_maximum = segment_range.get("maximum")
    if (
        isinstance(segment_minimum, bool)
        or not isinstance(segment_minimum, int)
        or segment_minimum < 1
        or isinstance(segment_maximum, bool)
        or not isinstance(segment_maximum, int)
        or segment_maximum < segment_minimum
    ):
        raise ValueError("recovery configuration segment_count range is invalid")

    tie_range = payload.get("tie_inability_band")
    if not isinstance(tie_range, Mapping) or set(tie_range) != {
        "minimum_rate",
        "maximum_rate",
    }:
        raise ValueError("recovery configuration tie_inability_band is not canonical")
    tie_minimum = _number(tie_range, "minimum_rate")
    tie_maximum = _number(tie_range, "maximum_rate")
    if not 0 <= tie_minimum <= tie_maximum <= 1:
        raise ValueError("recovery configuration tie_inability_band range is invalid")

    utility_range = payload.get("utility_separation_band")
    if not isinstance(utility_range, Mapping) or set(utility_range) != {
        "minimum_log_utility_gap",
        "maximum_log_utility_gap",
    }:
        raise ValueError("recovery configuration utility_separation_band is not canonical")
    utility_minimum = _number(utility_range, "minimum_log_utility_gap")
    utility_maximum = _number(utility_range, "maximum_log_utility_gap")
    if not 0 <= utility_minimum <= utility_maximum:
        raise ValueError("recovery configuration utility_separation_band range is invalid")
    for integer_key in (
        "planned_participation_floor",
        "usable_participation_floor",
        "bootstrap_count",
    ):
        value = payload.get(integer_key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"recovery configuration {integer_key} must be a positive integer")
    successful_floor = _number(payload, "successful_fit_floor")
    if not 0 <= successful_floor <= 1:
        raise ValueError("recovery configuration successful_fit_floor is invalid")
    thresholds = payload.get("shortlist_thresholds")
    if not isinstance(thresholds, Mapping) or set(thresholds) != {
        "clear_finalist",
        "clear_non_finalist",
    }:
        raise ValueError("recovery configuration shortlist_thresholds are not canonical")
    upper = _number(thresholds, "clear_finalist")
    lower = _number(thresholds, "clear_non_finalist")
    if not 0 <= lower < upper <= 1:
        raise ValueError("recovery configuration shortlist thresholds are invalid")
    if calibrated:
        if payload["bootstrap_count"] != _REQUIRED_BOOTSTRAP_COUNT:
            raise ValueError(
                "calibrated recovery configuration bootstrap_count must equal 2000"
            )
        if successful_floor != _MINIMUM_SUCCESSFUL_FIT_FLOOR:
            raise ValueError(
                "calibrated recovery configuration successful_fit_floor must equal 0.95"
            )
        if (
            upper != _CLEAR_FINALIST_THRESHOLD
            or lower != _CLEAR_NON_FINALIST_THRESHOLD
        ):
            raise ValueError(
                "calibrated recovery configuration shortlist thresholds must equal 0.90/0.10"
            )
    return dict(payload)


def _in_any_band(value: float, bands: Sequence[Mapping[str, Any]]) -> bool:
    if not bands:
        return False
    return any(float(band["minimum"]) <= value <= float(band["maximum"]) for band in bands)


def _screening_record_rates(
    observations: Sequence[Mapping[str, Any] | _Observation],
) -> dict[str, float | int]:
    total = 0
    no_distinction = 0
    inability = 0
    usable = 0
    for payload in observations:
        if isinstance(payload, _Observation):
            total += 1
            usable += 1
            continue
        if not isinstance(payload, Mapping):
            continue
        total += 1
        choice = payload.get("comparative_choice")
        status = choice.get("status") if isinstance(choice, Mapping) else None
        if status == "no_meaningful_difference":
            no_distinction += 1
        if status == "unable_to_judge":
            inability += 1
        if _is_usable(payload) and status in {None, "best_worst"}:
            usable += 1
    denominator = total or 1
    return {
        "accepted_record_count": total,
        "usable_record_count": usable,
        "no_distinction_count": no_distinction,
        "inability_count": inability,
        "no_distinction_rate": no_distinction / denominator,
        "inability_rate": inability / denominator,
        "combined_tie_inability_rate": (no_distinction + inability) / denominator,
    }


def _invalid_screening_result(
    reasons: Sequence[str],
    top_k: int,
    version: str,
    diagnostics: Mapping[str, Any],
    creative_ids: Sequence[str] = (),
    validity_status: str = "invalid",
) -> ScreeningResult:
    roster = tuple(sorted(set(creative_ids)))
    return ScreeningResult(
        validity_status=validity_status,
        validity_reasons=tuple(dict.fromkeys(reasons)),
        utilities={},
        ranked_ids=(),
        top_k_inclusion_frequencies={},
        classifications={creative_id: "unresolved" for creative_id in roster},
        archetype_sensitivity={
            "method": "leave_one_persona_archetype_out",
            "unique_archetypes": 0,
            "attempted_fits": 0,
            "successful_fits": 0,
            "successful_fit_rate": 0.0,
            "top_k_consistent": False,
            "top_k_changed_for": [],
            "results": [],
        },
        diagnostics=dict(diagnostics),
        requested_top_k=top_k,
        recovery_config_version=version,
    )


def screen_shortlist(
    observations: Sequence[Mapping[str, Any] | _Observation],
    segment_weights: Mapping[str, float],
    *,
    top_k: int,
    config: MaxDiffConfig | None = None,
    recovery_config: Mapping[str, Any] | None = None,
    creative_ids: Sequence[str] | None = None,
    planned_participations_per_creative: int | Mapping[str, int] | None = None,
    planned_participations_per_profile: (
        Mapping[str, Mapping[str, int]] | None
    ) = None,
    collection_open: bool = False,
    profile_weights: Mapping[str, float] | None = None,
) -> ScreeningResult:
    """Fit weighted MaxDiff and apply coverage, bootstrap, and sensitivity gates."""

    model_config = config or MaxDiffConfig(penalty_lambda=0.1)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    if not isinstance(collection_open, bool):
        raise ValueError("collection_open must be a boolean")
    recovery = _validate_recovery_config(
        recovery_config or _default_recovery_config(model_config)
    )
    version = str(recovery["version"])
    roster = tuple(
        sorted(
            set(_creative_roster(observations) if creative_ids is None else creative_ids)
        )
    )
    if top_k > len(roster):
        raise ValueError("top_k cannot exceed the creative roster size")
    record_rates = _screening_record_rates(observations)
    try:
        normalized = _normalize_observations(observations)
        observed_creatives = {
            creative_id for item in normalized for creative_id in item.block_ids
        }
        outside_roster = sorted(observed_creatives - set(roster))
        if outside_roster:
            return _invalid_screening_result(
                (
                    (
                        "collection_open_or_required_jobs_missing",
                        "response_creative_out_of_roster",
                    )
                    if collection_open
                    else ("response_creative_out_of_roster",)
                ),
                top_k,
                version,
                {
                    "error": "usable observations contain creatives outside the locked roster: "
                    + ",".join(outside_roster),
                    "record_rates": record_rates,
                },
                roster,
                validity_status="incomplete" if collection_open else "invalid",
            )
        weights = _weights_for_normalized(
            normalized,
            segment_weights,
            profile_weights,
        )
    except ValueError as exc:
        return _invalid_screening_result(
            (
                ("collection_open_or_required_jobs_missing", "corrupt_screening_records")
                if collection_open
                else ("corrupt_screening_records",)
            ),
            top_k,
            version,
            {"error": str(exc), "record_rates": record_rates},
            roster,
            validity_status="incomplete" if collection_open else "invalid",
        )

    usable_floor = int(recovery["usable_participation_floor"])
    participation = usable_participation_counts(observations, roster)
    usable_coverage = bool(participation) and all(
        count >= usable_floor for count in participation.values()
    )
    profile_participation = usable_participation_counts_by_profile(
        observations,
        roster,
    )
    profile_usable_coverage = (
        True
        if profile_weights is None
        else set(profile_participation) == set(profile_weights)
        and all(
            count >= usable_floor
            for profile_counts in profile_participation.values()
            for count in profile_counts.values()
        )
    )
    fitted = _fit_normalized(normalized, model_config, weights, roster)
    base_diagnostics = {
        "accepted_response_records": len(observations),
        "accepted_unique_replicates": len(
            {
                str(item.get("synthetic_replicate_id"))
                for item in observations
                if isinstance(item, Mapping)
                and isinstance(item.get("synthetic_replicate_id"), str)
            }
        ),
        "accepted_response_records_by_segment": dict(
            sorted(
                Counter(
                    str(item.get("segment_id"))
                    for item in observations
                    if isinstance(item, Mapping)
                    and isinstance(item.get("segment_id"), str)
                ).items()
            )
        ),
        "accepted_response_records_by_context_stratum": dict(
            sorted(
                Counter(
                    str(item.get("context_stratum_id"))
                    for item in observations
                    if isinstance(item, Mapping)
                    and isinstance(item.get("context_stratum_id"), str)
                ).items()
            )
        ),
        "connected": fitted.connected,
        "identified": fitted.identified,
        "converged": fitted.converged,
        "fit": fitted.as_dict(),
        "record_rates": record_rates,
        "usable_participations_per_creative": participation,
        "usable_participations_per_profile": profile_participation,
        "usable_participation_floor": usable_floor,
    }
    invalid_reasons: list[str] = []
    if not normalized:
        invalid_reasons.append("no_usable_maxdiff_blocks")
    if not fitted.connected:
        invalid_reasons.append("comparison_graph_disconnected")
    if not fitted.identified:
        invalid_reasons.append("maxdiff_model_unidentified")
    if fitted.identified and not fitted.converged:
        invalid_reasons.append("maxdiff_model_nonconvergent")
    if invalid_reasons:
        if not usable_coverage or not profile_usable_coverage:
            invalid_reasons.append("usable_participation_floor_not_met")
        if collection_open:
            invalid_reasons.insert(0, "collection_open_or_required_jobs_missing")
        return _invalid_screening_result(
            invalid_reasons,
            top_k,
            version,
            base_diagnostics,
            roster,
            validity_status="incomplete" if collection_open else "invalid",
        )

    overall_resilience = _block_resilient(roster, normalized)
    profile_connectivity = profile_conditioned_connectivity(normalized, roster)
    per_segment: dict[str, dict[str, Any]] = {}
    for segment_id in sorted(segment_weights):
        segment_records = tuple(item for item in normalized if item.segment_id == segment_id)
        per_segment[segment_id] = {
            "usable_record_count": len(segment_records),
            "connected": _comparison_graph_connected(roster, segment_records),
            "one_block_deletion_resilient": _block_resilient(roster, segment_records),
            "usable_participations_per_creative": {
                creative_id: sum(
                    creative_id in item.block_ids for item in segment_records
                )
                for creative_id in roster
            },
        }
    segment_connected = all(item["connected"] for item in per_segment.values())
    segment_resilient = all(
        item["one_block_deletion_resilient"] for item in per_segment.values()
    )

    frequencies, bootstrap = _bootstrap_stability(
        normalized,
        roster,
        segment_weights,
        top_k,
        model_config,
        profile_weights,
    )
    sensitivity = _archetype_sensitivity(
        normalized,
        roster,
        segment_weights,
        tuple(fitted.ranked_ids[:top_k]),
        top_k,
        model_config,
    )

    tie_range = recovery["tie_inability_band"]
    utility_range = recovery["utility_separation_band"]
    utility_separation = max(fitted.utilities.values()) - min(fitted.utilities.values())
    segment_range = recovery["segment_count"]
    dimension_gates = {
        "library_size_band": _in_any_band(len(roster), recovery["library_size_bands"]),
        "shortlist_size_band": _in_any_band(top_k, recovery["shortlist_size_bands"]),
        "segment_count_band": (
            float(segment_range["minimum"])
            <= len(segment_weights)
            <= float(segment_range["maximum"])
        ),
        "tie_inability_band": (
            float(tie_range["minimum_rate"])
            <= float(record_rates["combined_tie_inability_rate"])
            <= float(tie_range["maximum_rate"])
        ),
        "utility_separation_band": (
            float(utility_range["minimum_log_utility_gap"])
            <= utility_separation
            <= float(utility_range["maximum_log_utility_gap"])
        ),
    }
    planned_floor = int(recovery["planned_participation_floor"])
    if planned_participations_per_creative is None:
        planned_gate = False
    elif isinstance(planned_participations_per_creative, Mapping):
        planned_gate = set(planned_participations_per_creative) >= set(roster) and all(
            isinstance(planned_participations_per_creative.get(creative_id), int)
            and planned_participations_per_creative[creative_id] >= planned_floor
            for creative_id in roster
        )
    else:
        planned_gate = (
            isinstance(planned_participations_per_creative, int)
            and not isinstance(planned_participations_per_creative, bool)
            and planned_participations_per_creative >= planned_floor
        )
    profile_planned_gate = (
        True
        if profile_weights is None
        else isinstance(planned_participations_per_profile, Mapping)
        and set(planned_participations_per_profile) == set(profile_weights)
        and all(
            isinstance(profile_counts, Mapping)
            and set(profile_counts) >= set(roster)
            and all(
                isinstance(profile_counts.get(creative_id), int)
                and not isinstance(profile_counts.get(creative_id), bool)
                and profile_counts[creative_id] >= planned_floor
                for creative_id in roster
            )
            for profile_counts in planned_participations_per_profile.values()
        )
    )
    config_match = (
        model_config.bootstrap_count == int(recovery["bootstrap_count"])
        and model_config.successful_fit_floor
        == float(recovery["successful_fit_floor"])
        and model_config.clear_finalist_threshold
        == float(recovery["shortlist_thresholds"]["clear_finalist"])
        and model_config.clear_non_finalist_threshold
        == float(recovery["shortlist_thresholds"]["clear_non_finalist"])
    )
    gates = {
        "connected": fitted.connected and segment_connected,
        "identified": fitted.identified,
        "usable_coverage": usable_coverage,
        "planned_coverage": planned_gate,
        "profile_usable_coverage": profile_usable_coverage,
        "profile_planned_coverage": profile_planned_gate,
        "block_resilience": overall_resilience and segment_resilient,
        "profile_conditioned_connectivity": (
            True
            if profile_weights is None
            else profile_connectivity["survives_any_one_profile_removal"]
        ),
        "converged": fitted.converged,
        "stability": bootstrap["successful_fit_rate"] >= model_config.successful_fit_floor,
        "archetype_sensitivity": sensitivity["top_k_consistent"],
        "versioned_dimensions": all(dimension_gates.values()),
        "model_matches_recovery_config": config_match,
    }

    reasons: list[str] = []
    reason_by_gate = {
        "connected": "reported_segment_graph_not_connected",
        "usable_coverage": "usable_participation_floor_not_met",
        "planned_coverage": "planned_participation_floor_not_met",
        "profile_usable_coverage": "profile_usable_participation_floor_not_met",
        "profile_planned_coverage": "profile_planned_participation_floor_not_met",
        "block_resilience": "one_block_deletion_resilience_not_met",
        "profile_conditioned_connectivity": "profile_conditioned_graph_not_connected",
        "stability": "bootstrap_successful_fit_floor_not_met",
        "archetype_sensitivity": "shortlist_sensitive_to_archetype_omission",
        "versioned_dimensions": "recovery_configuration_band_not_met",
        "model_matches_recovery_config": "manifest_model_recovery_configuration_mismatch",
    }
    for gate_name in (
        "connected",
        "usable_coverage",
        "planned_coverage",
        "profile_usable_coverage",
        "profile_planned_coverage",
        "block_resilience",
        "profile_conditioned_connectivity",
        "stability",
        "archetype_sensitivity",
        "versioned_dimensions",
        "model_matches_recovery_config",
    ):
        if not gates[gate_name]:
            reasons.append(reason_by_gate[gate_name])
    calibrated = recovery["calibration_status"] == "calibrated"
    if not calibrated:
        reasons.append("recovery_configuration_exploratory_only")
    validity_status = "valid" if calibrated and all(gates.values()) else "exploratory"
    if collection_open:
        validity_status = "incomplete"
        reasons.insert(0, "collection_open_or_required_jobs_missing")
    threshold_classifications = {
        creative_id: classify_top_k_frequency(
            frequencies[creative_id],
            model_config.clear_finalist_threshold,
            model_config.clear_non_finalist_threshold,
        )
        for creative_id in roster
    }
    classifications = (
        threshold_classifications
        if validity_status == "valid"
        else {creative_id: "unresolved" for creative_id in roster}
    )
    diagnostics = {
        **base_diagnostics,
        "analysis_weighting": {
            "method": (
                "locked_profile_weight_over_realized_usable_share"
                if profile_weights is not None
                else "locked_segment_weight_over_realized_usable_share"
            ),
            "normalized_mean_weight": float(np.mean(weights)),
            "segment_weights": dict(sorted(segment_weights.items())),
            "profile_weights": (
                None if profile_weights is None else dict(sorted(profile_weights.items()))
            ),
        },
        "usable_participations_per_creative": participation,
        "usable_participations_per_profile": profile_participation,
        "usable_participation_floor": usable_floor,
        "overall_one_block_deletion_resilient": overall_resilience,
        "segment_diagnostics": per_segment,
        "profile_conditioned_connectivity": profile_connectivity,
        "bootstrap": bootstrap,
        "utility_separation": utility_separation,
        "versioned_dimension_gates": dimension_gates,
        "gates": gates,
    }
    return ScreeningResult(
        validity_status=validity_status,
        validity_reasons=tuple(reasons),
        utilities=fitted.utilities,
        ranked_ids=fitted.ranked_ids,
        top_k_inclusion_frequencies=frequencies,
        classifications=classifications,
        archetype_sensitivity=sensitivity,
        diagnostics=diagnostics,
        requested_top_k=top_k,
        recovery_config_version=version,
    )


__all__ = [
    "IndexedObservation",
    "MaxDiffConfig",
    "MaxDiffFit",
    "ScreeningResult",
    "classify_top_k_frequency",
    "compute_analysis_weights",
    "fit_maxdiff",
    "maxdiff_loss_and_gradient",
    "profile_conditioned_connectivity",
    "screen_shortlist",
    "usable_participation_counts",
    "usable_participation_counts_by_profile",
]
