"""Deterministic aggregation for complete-set first-round response records."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import random
import re
from typing import Any, Mapping, Sequence


PRODUCTION_RESAMPLES = 2000
CALIBRATION_POLICY_VERSION = "complete-exposure-calibration-v2"
PROFILE_STRATIFIED_POLICY_VERSION = "complete-exposure-profile-stratified-v1"
_TIE_TOLERANCE = 1e-12
_NEAR_DUPLICATE_TOKEN_SIMILARITY = 0.90


def _rank_scores(ranking: Sequence[str]) -> dict[str, float]:
    denominator = len(ranking) - 1
    return {
        creative_id: (len(ranking) - position - 1) / denominator
        for position, creative_id in enumerate(ranking)
    }


def _weighted_utilities(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    segment_weights: Mapping[str, float],
) -> dict[str, float]:
    by_segment: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_segment[str(record["segment_id"])].append(record)

    total_weight = sum(float(segment_weights[segment_id]) for segment_id in by_segment)
    if not math.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("segment weights represented by usable records must sum positive")

    means = {creative_id: 0.0 for creative_id in creative_ids}
    for segment_id, segment_records in sorted(by_segment.items()):
        weight = float(segment_weights[segment_id]) / total_weight
        segment_sums = {creative_id: 0.0 for creative_id in creative_ids}
        for record in segment_records:
            evaluation = record["complete_set_evaluation"]
            for creative_id, value in _rank_scores(
                evaluation["preference_ranking"]
            ).items():
                segment_sums[creative_id] += value
        for creative_id in creative_ids:
            means[creative_id] += weight * (
                segment_sums[creative_id] / len(segment_records)
            )

    center = sum(means.values()) / len(creative_ids)
    return {
        creative_id: means[creative_id] - center for creative_id in creative_ids
    }


def _ranked_ids(utilities: Mapping[str, float]) -> list[str]:
    return sorted(utilities, key=lambda creative_id: (-utilities[creative_id], creative_id))


def _cutoff_membership(
    utilities: Mapping[str, float], top_k: int
) -> tuple[dict[str, float], bool]:
    ordered = _ranked_ids(utilities)
    cutoff = utilities[ordered[top_k - 1]]
    above = [
        creative_id
        for creative_id in ordered
        if utilities[creative_id] > cutoff + _TIE_TOLERANCE
    ]
    tied = [
        creative_id
        for creative_id in ordered
        if math.isclose(
            utilities[creative_id], cutoff, rel_tol=0.0, abs_tol=_TIE_TOLERANCE
        )
    ]
    remaining = top_k - len(above)
    fraction = remaining / len(tied)
    membership = {creative_id: 0.0 for creative_id in ordered}
    for creative_id in above:
        membership[creative_id] = 1.0
    for creative_id in tied:
        membership[creative_id] = fraction
    return membership, 0 < remaining < len(tied)


def _bootstrap_stability(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    segment_weights: Mapping[str, float],
    top_k: int,
    seed: int,
    resamples: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    by_segment: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_segment[str(record["segment_id"])].append(record)
    inclusion = Counter({creative_id: 0.0 for creative_id in creative_ids})
    cutoff_tie_resamples = 0
    for _ in range(resamples):
        sampled: list[Mapping[str, Any]] = []
        for segment_id in sorted(by_segment):
            values = by_segment[segment_id]
            sampled.extend(rng.choice(values) for _ in range(len(values)))
        utilities = _weighted_utilities(sampled, creative_ids, segment_weights)
        membership, cutoff_tied = _cutoff_membership(utilities, top_k)
        inclusion.update(membership)
        cutoff_tie_resamples += int(cutoff_tied)
    return {
        "requested_fits": resamples,
        "successful_fit_count": resamples,
        "successful_fit_rate": 1.0,
        "resample_unit": "whole_synthetic_replicate_record",
        "stratification": "locked_segment",
        "cutoff_tie_resamples": cutoff_tie_resamples,
        "frequencies": {
            creative_id: inclusion[creative_id] / resamples
            for creative_id in creative_ids
        },
    }


def _aggregate_complete_exposure_v2(
    records: Sequence[Mapping[str, Any]],
    *,
    study_id: str,
    creative_ids: Sequence[str],
    top_k: int,
    segment_weights: Mapping[str, float],
    seed: int,
    resamples: int = PRODUCTION_RESAMPLES,
    collection_open: bool = False,
    expected_job_slots: int | None = None,
    minimum_usable_records_per_segment: int = 8,
    finalist_inclusion_threshold: float = 0.90,
    nonfinalist_inclusion_threshold: float = 0.10,
    minimum_archetype_diversity: int = 2,
    minimum_evaluable_archetype_exclusions: int = 2,
    recovery_config_version: str = CALIBRATION_POLICY_VERSION,
) -> dict[str, Any]:
    """Aggregate valid method-locked complete-set records without population inference."""

    roster = tuple(creative_ids)
    if not 2 <= len(roster) <= 6 or len(set(roster)) != len(roster):
        raise ValueError("complete exposure requires 2-6 unique creative IDs")
    if not 1 <= top_k <= len(roster):
        raise ValueError("requested shortlist must not exceed the complete set")
    if resamples != PRODUCTION_RESAMPLES:
        raise ValueError("complete-exposure production aggregation requires 2000 resamples")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    for field, value in (
        ("minimum_archetype_diversity", minimum_archetype_diversity),
        (
            "minimum_evaluable_archetype_exclusions",
            minimum_evaluable_archetype_exclusions,
        ),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(f"{field} must be an integer of at least two")
    if not records:
        raise ValueError("complete exposure requires accepted response records")

    accepted_segments = {str(record.get("segment_id")) for record in records}
    if accepted_segments != set(segment_weights):
        raise ValueError(
            "manifest audience-lock segment IDs must exactly match accepted response segment IDs"
        )
    usable = [
        record
        for record in records
        if record.get("usable_complete_exposure_observation") is True
    ]
    accepted_replicates = {
        str(record["synthetic_replicate_id"]) for record in records
    }
    total_calls = sum(
        len(record.get("runtime_attempts", ()))
        for record in records
        if isinstance(record.get("runtime_attempts"), Sequence)
    )
    base_diagnostics = {
        "accepted_response_records": len(records),
        "accepted_unique_replicates": len(accepted_replicates),
        "accepted_response_records_by_segment": dict(
            sorted(Counter(str(record.get("segment_id")) for record in records).items())
        ),
        "accepted_response_records_by_context_stratum": dict(
            sorted(
                Counter(
                    str(record.get("context_stratum_id"))
                    for record in records
                    if isinstance(record.get("context_stratum_id"), str)
                ).items()
            )
        ),
        "unique_job_slots_consumed": len(accepted_replicates),
        "total_model_calls": total_calls,
        "usable_observation_count": len(usable),
        "unusable_observation_count": len(records) - len(usable),
        "complete_set_size": len(roster),
    }

    if collection_open or (
        expected_job_slots is not None and len(accepted_replicates) < expected_job_slots
    ):
        return _empty_result(
            study_id,
            roster,
            top_k,
            "incomplete",
            ["collection_open_or_required_jobs_missing"],
            base_diagnostics,
        )
    if not usable:
        return _empty_result(
            study_id,
            roster,
            top_k,
            "invalid",
            ["no_usable_complete_set_observations"],
            base_diagnostics,
        )

    usable_by_segment = Counter(str(record["segment_id"]) for record in usable)
    below_floor = sorted(
        segment_id
        for segment_id in segment_weights
        if usable_by_segment[segment_id] < minimum_usable_records_per_segment
    )
    base_diagnostics["usable_observations_by_segment"] = dict(
        sorted(usable_by_segment.items())
    )
    base_diagnostics["minimum_usable_records_per_segment"] = (
        minimum_usable_records_per_segment
    )

    utilities = _weighted_utilities(usable, roster, segment_weights)
    ranked = _ranked_ids(utilities)
    membership, cutoff_tied = _cutoff_membership(utilities, top_k)
    bootstrap = _bootstrap_stability(
        usable, roster, segment_weights, top_k, seed, resamples
    )
    preliminary = ranked[:top_k]
    preliminary_set = set(preliminary)
    frequencies = bootstrap["frequencies"]
    stability_passed = all(
        frequencies[creative_id] >= finalist_inclusion_threshold
        for creative_id in preliminary_set
    ) and all(
        frequencies[creative_id] <= nonfinalist_inclusion_threshold
        for creative_id in roster
        if creative_id not in preliminary_set
    )
    if cutoff_tied:
        validity = "exploratory"
        selection_status = "unresolved"
        reasons = ["complete_set_cutoff_tie"]
        proposed: list[str] = []
        classifications = {creative_id: "unresolved" for creative_id in roster}
    else:
        validity = "valid"
        selection_status = "resolved"
        reasons = []
        proposed = ranked[:top_k]
        proposed_set = set(proposed)
        classifications = {
            creative_id: (
                "proposed_finalist" if creative_id in proposed_set else "not_proposed"
            )
            for creative_id in roster
        }

    archetypes = sorted(
        {str(record.get("persona_archetype_id")) for record in usable}
    )
    sensitivity_results: list[dict[str, Any]] = []
    unevaluable_exclusions: list[str] = []
    baseline = set(proposed)
    for archetype_id in archetypes:
        subset = [
            record
            for record in usable
            if str(record.get("persona_archetype_id")) != archetype_id
        ]
        if not subset:
            unevaluable_exclusions.append(archetype_id)
            continue
        subset_utilities = _weighted_utilities(subset, roster, segment_weights)
        subset_membership, subset_tied = _cutoff_membership(subset_utilities, top_k)
        subset_ranked = _ranked_ids(subset_utilities)
        subset_top = set() if subset_tied else set(subset_ranked[:top_k])
        sensitivity_results.append(
            {
                "omitted_archetype_id": archetype_id,
                "ranked_ids": subset_ranked,
                "top_k_changed": subset_top != baseline,
                "cutoff_tied": subset_tied,
                "fractional_membership": subset_membership,
            }
        )
    changed_for = [
        item["omitted_archetype_id"]
        for item in sensitivity_results
        if item["top_k_changed"]
    ]
    evaluable_exclusions = len(sensitivity_results)
    sensitivity_evaluable = (
        len(archetypes) >= minimum_archetype_diversity
        and evaluable_exclusions >= minimum_evaluable_archetype_exclusions
    )
    sensitivity_passed = sensitivity_evaluable and not changed_for

    if validity == "valid" and (
        below_floor or not stability_passed or not sensitivity_passed
    ):
        validity = "exploratory"
        selection_status = "unresolved"
        reasons = []
        if below_floor:
            reasons.append("usable_record_floor_not_met")
        if not stability_passed:
            reasons.append("conditional_stability_gate_not_met")
        if not sensitivity_evaluable:
            reasons.append("archetype_sensitivity_unevaluable")
        if changed_for:
            reasons.append("archetype_sensitivity_gate_not_met")
        proposed = []
        classifications = {creative_id: "unresolved" for creative_id in roster}

    diagnostics = {
        **base_diagnostics,
        "bootstrap": {key: value for key, value in bootstrap.items() if key != "frequencies"},
        "gates": {
            "complete_set_coverage": True,
            "closed_collection": True,
            "usable_record_floor": not below_floor,
            "cutoff_resolved": not cutoff_tied,
            "conditional_stability": stability_passed,
            "archetype_sensitivity": sensitivity_passed,
        },
    }
    return {
        "study_id": study_id,
        "method": "complete_exposure",
        "estimand": "centered_complete_set_normalized_rank_utility",
        "stability_diagnostic": "conditional_within_run_top_k_inclusion_frequency",
        "requested_top_k": top_k,
        "utilities": utilities,
        "ranked_ids": ranked,
        "top_k_inclusion_frequencies": bootstrap["frequencies"],
        "classifications": classifications,
        "selection_status": selection_status,
        "proposed_finalist_ids": proposed,
        "archetype_sensitivity": {
            "method": "leave_one_persona_archetype_out",
            "unique_archetypes": len(archetypes),
            "minimum_archetype_diversity": minimum_archetype_diversity,
            "attempted_exclusions": len(archetypes),
            "evaluable_exclusions": evaluable_exclusions,
            "minimum_evaluable_exclusions": minimum_evaluable_archetype_exclusions,
            "unevaluable_exclusion_ids": unevaluable_exclusions,
            "evaluability_gate_passed": sensitivity_evaluable,
            "top_k_consistent": sensitivity_passed,
            "top_k_changed_for": changed_for,
            "results": sensitivity_results,
        },
        "model_diagnostics": diagnostics,
        "recovery_config_version": recovery_config_version,
        "validity_status": validity,
        "validity_reasons": reasons,
        "interpretation_limits": [
            "The complete-set signal is conditional only on this recorded synthetic run.",
            "Conditional stability is not population uncertainty or human alignment.",
            "Results do not estimate survey preference, campaign performance, conversion, or revenue impact.",
        ],
    }


def _profile_id(record: Mapping[str, Any]) -> str:
    value = record.get("grounded_profile_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "profile-stratified complete exposure requires grounded_profile_id "
            "on every accepted response record"
        )
    return value


def _validate_profile_design(
    records: Sequence[Mapping[str, Any]],
    profile_weights: Mapping[str, float],
    segment_weights: Mapping[str, float],
) -> dict[str, str]:
    if not isinstance(profile_weights, Mapping) or not profile_weights:
        raise ValueError("profile_weights must be a non-empty mapping")
    if not isinstance(segment_weights, Mapping) or not segment_weights:
        raise ValueError("segment_weights must be a non-empty mapping")
    normalized_segment_weights: dict[str, float] = {}
    for raw_segment_id, raw_weight in segment_weights.items():
        if not isinstance(raw_segment_id, str) or not raw_segment_id:
            raise ValueError("segment_weights keys must be non-empty strings")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError("segment weights must be finite positive numbers")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("segment weights must be finite positive numbers")
        normalized_segment_weights[raw_segment_id] = weight
    if not math.isclose(
        sum(normalized_segment_weights.values()),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("frozen segment weights must sum to one")
    normalized_weights: dict[str, float] = {}
    for raw_profile_id, raw_weight in profile_weights.items():
        if not isinstance(raw_profile_id, str) or not raw_profile_id:
            raise ValueError("profile_weights keys must be non-empty strings")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError("profile weights must be finite positive numbers")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("profile weights must be finite positive numbers")
        normalized_weights[raw_profile_id] = weight

    accepted_profiles = {_profile_id(record) for record in records}
    if accepted_profiles != set(normalized_weights):
        raise ValueError(
            "frozen profile weight IDs must exactly match accepted response profile IDs"
        )

    profile_segments: dict[str, str] = {}
    for record in records:
        profile_id = _profile_id(record)
        segment_id = str(record.get("segment_id"))
        previous = profile_segments.setdefault(profile_id, segment_id)
        if previous != segment_id:
            raise ValueError(
                f"grounded profile {profile_id!r} is bound to multiple segments"
            )
    if set(profile_segments.values()) != set(segment_weights):
        raise ValueError(
            "manifest audience-lock segment IDs must exactly match profile segment IDs"
        )

    by_segment: dict[str, float] = defaultdict(float)
    for profile_id, segment_id in profile_segments.items():
        by_segment[segment_id] += normalized_weights[profile_id]
    for segment_id, total in sorted(by_segment.items()):
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                "within-segment profile weights must sum to one; "
                f"{segment_id!r} sums to {total}"
            )
    return profile_segments


def _profile_floor_map(
    value: int | Mapping[str, int], profile_ids: Sequence[str]
) -> dict[str, int]:
    if isinstance(value, bool):
        raise ValueError("minimum usable records per profile must be positive integers")
    if isinstance(value, int):
        if value < 1:
            raise ValueError("minimum usable records per profile must be at least one")
        return {profile_id: value for profile_id in profile_ids}
    if not isinstance(value, Mapping) or set(value) != set(profile_ids):
        raise ValueError(
            "per-profile usable floors must exactly match frozen profile weight IDs"
        )
    floors: dict[str, int] = {}
    for profile_id in profile_ids:
        floor = value[profile_id]
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
            raise ValueError("minimum usable records per profile must be positive integers")
        floors[profile_id] = floor
    return floors


def _weighted_profile_utilities(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    segment_weights: Mapping[str, float],
    profile_weights: Mapping[str, float],
    profile_segments: Mapping[str, str],
) -> dict[str, float]:
    by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_profile[_profile_id(record)].append(record)

    active_segments = {profile_segments[profile_id] for profile_id in by_profile}
    if active_segments != set(segment_weights):
        raise ValueError("every frozen segment must retain at least one usable profile")
    total_segment_weight = sum(float(value) for value in segment_weights.values())
    if not math.isfinite(total_segment_weight) or total_segment_weight <= 0:
        raise ValueError("segment weights must sum positive")

    profile_means: dict[str, dict[str, float]] = {}
    for profile_id, profile_records in sorted(by_profile.items()):
        sums = {creative_id: 0.0 for creative_id in creative_ids}
        for record in profile_records:
            ranking = record["complete_set_evaluation"]["preference_ranking"]
            for creative_id, score in _rank_scores(ranking).items():
                sums[creative_id] += score
        profile_means[profile_id] = {
            creative_id: sums[creative_id] / len(profile_records)
            for creative_id in creative_ids
        }

    segment_profiles: dict[str, list[str]] = defaultdict(list)
    for profile_id in by_profile:
        segment_profiles[profile_segments[profile_id]].append(profile_id)
    combined = {creative_id: 0.0 for creative_id in creative_ids}
    for segment_id, active_profile_ids in sorted(segment_profiles.items()):
        within_segment_total = sum(
            float(profile_weights[profile_id]) for profile_id in active_profile_ids
        )
        if not math.isfinite(within_segment_total) or within_segment_total <= 0:
            raise ValueError("represented profile weights must sum positive")
        segment_weight = float(segment_weights[segment_id]) / total_segment_weight
        for profile_id in active_profile_ids:
            profile_weight = float(profile_weights[profile_id]) / within_segment_total
            for creative_id in creative_ids:
                combined[creative_id] += (
                    segment_weight
                    * profile_weight
                    * profile_means[profile_id][creative_id]
                )

    center = sum(combined.values()) / len(creative_ids)
    return {
        creative_id: combined[creative_id] - center for creative_id in creative_ids
    }


def _response_material(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "complete_set_evaluation": record.get("complete_set_evaluation"),
        "per_creative_reactions": record.get("per_creative_reactions"),
    }


def _material_text(record: Mapping[str, Any]) -> str:
    return json.dumps(
        _response_material(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _material_tokens(record: Mapping[str, Any]) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _material_text(record).lower()))


def _near_duplicate(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    left_ranking = left["complete_set_evaluation"]["preference_ranking"]
    right_ranking = right["complete_set_evaluation"]["preference_ranking"]
    if list(left_ranking) != list(right_ranking):
        return False
    left_tokens = _material_tokens(left)
    right_tokens = _material_tokens(right)
    union = left_tokens | right_tokens
    if not union:
        return True
    return len(left_tokens & right_tokens) / len(union) >= _NEAR_DUPLICATE_TOKEN_SIMILARITY


def _profile_pattern_clusters(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[list[Mapping[str, Any]]]], dict[str, Any]]:
    by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_profile[_profile_id(record)].append(record)

    clusters_by_profile: dict[str, list[list[Mapping[str, Any]]]] = {}
    exact_pairs_by_profile: dict[str, int] = {}
    near_pairs_by_profile: dict[str, int] = {}
    redundant_by_profile: dict[str, int] = {}
    for profile_id, values in sorted(by_profile.items()):
        clusters: list[list[Mapping[str, Any]]] = []
        for record in values:
            for cluster in clusters:
                if _near_duplicate(record, cluster[0]):
                    cluster.append(record)
                    break
            else:
                clusters.append([record])
        clusters_by_profile[profile_id] = clusters
        redundant_by_profile[profile_id] = len(values) - len(clusters)

        exact_pairs = 0
        near_pairs = 0
        for left_index, left in enumerate(values):
            left_text = _material_text(left)
            for right in values[left_index + 1 :]:
                if left_text == _material_text(right):
                    exact_pairs += 1
                elif _near_duplicate(left, right):
                    near_pairs += 1
        exact_pairs_by_profile[profile_id] = exact_pairs
        near_pairs_by_profile[profile_id] = near_pairs

    usable_count = len(records)
    cluster_count = sum(len(clusters) for clusters in clusters_by_profile.values())
    possible_pairs = sum(
        len(values) * (len(values) - 1) // 2 for values in by_profile.values()
    )
    exact_pairs = sum(exact_pairs_by_profile.values())
    near_pairs = sum(near_pairs_by_profile.values())
    return clusters_by_profile, {
        "method": "within_grounded_profile_response_content_clustering",
        "near_duplicate_token_similarity_threshold": _NEAR_DUPLICATE_TOKEN_SIMILARITY,
        "usable_record_count": usable_count,
        "effective_pattern_cluster_count": cluster_count,
        "effective_pattern_clusters_by_grounded_profile": {
            profile_id: len(clusters)
            for profile_id, clusters in sorted(clusters_by_profile.items())
        },
        "records_redundant_within_grounded_profile": dict(
            sorted(redundant_by_profile.items())
        ),
        "exact_duplicate_pairs_by_grounded_profile": dict(
            sorted(exact_pairs_by_profile.items())
        ),
        "near_duplicate_pairs_by_grounded_profile": dict(
            sorted(near_pairs_by_profile.items())
        ),
        "within_profile_pair_count": possible_pairs,
        "exact_duplicate_pair_rate": exact_pairs / possible_pairs if possible_pairs else 0.0,
        "near_duplicate_pair_rate": near_pairs / possible_pairs if possible_pairs else 0.0,
        "stability_resampling_uses_pattern_clusters": True,
    }


def _bootstrap_profile_stability(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    segment_weights: Mapping[str, float],
    profile_weights: Mapping[str, float],
    profile_segments: Mapping[str, str],
    top_k: int,
    seed: int,
    resamples: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = random.Random(seed)
    clusters_by_profile, duplication = _profile_pattern_clusters(records)
    inclusion = Counter({creative_id: 0.0 for creative_id in creative_ids})
    cutoff_tie_resamples = 0
    for _ in range(resamples):
        sampled: list[Mapping[str, Any]] = []
        for profile_id in sorted(clusters_by_profile):
            clusters = clusters_by_profile[profile_id]
            sampled.extend(
                rng.choice(clusters)[0] for _ in range(len(clusters))
            )
        utilities = _weighted_profile_utilities(
            sampled,
            creative_ids,
            segment_weights,
            profile_weights,
            profile_segments,
        )
        membership, cutoff_tied = _cutoff_membership(utilities, top_k)
        inclusion.update(membership)
        cutoff_tie_resamples += int(cutoff_tied)
    return (
        {
            "requested_fits": resamples,
            "successful_fit_count": resamples,
            "successful_fit_rate": 1.0,
            "resample_unit": "whole_synthetic_execution_record",
            "stratification": "locked_grounded_profile",
            "near_duplicate_cluster_adjusted": True,
            "effective_resample_units": duplication[
                "effective_pattern_cluster_count"
            ],
            "cutoff_tie_resamples": cutoff_tie_resamples,
            "frequencies": {
                creative_id: inclusion[creative_id] / resamples
                for creative_id in creative_ids
            },
        },
        duplication,
    )


def _profile_disagreement(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    top_k: int,
    overall_top_k: Sequence[str],
) -> dict[str, Any]:
    by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_profile[_profile_id(record)].append(record)
    results: list[dict[str, Any]] = []
    resolved_patterns: set[tuple[str, ...]] = set()
    overall = set(overall_top_k)
    profiles_differing: list[str] = []
    for profile_id, values in sorted(by_profile.items()):
        utilities = _weighted_utilities(
            values,
            creative_ids,
            {str(values[0]["segment_id"]): 1.0},
        )
        membership, tied = _cutoff_membership(utilities, top_k)
        ranked = _ranked_ids(utilities)
        selected = [] if tied else ranked[:top_k]
        if selected:
            resolved_patterns.add(tuple(sorted(selected)))
        if tied or set(selected) != overall:
            profiles_differing.append(profile_id)
        results.append(
            {
                "grounded_profile_id": profile_id,
                "usable_records": len(values),
                "utilities": utilities,
                "ranked_ids": ranked,
                "top_k_ids": selected,
                "cutoff_tied": tied,
                "fractional_membership": membership,
            }
        )
    present = len(resolved_patterns) > 1 or any(item["cutoff_tied"] for item in results)
    return {
        "method": "within_grounded_profile_top_k_comparison",
        "present": present,
        "distinct_resolved_top_k_count": len(resolved_patterns),
        "profiles_differing_from_overall_top_k": profiles_differing,
        "shortlist_fragile": False,
        "results": results,
    }


def _leave_one_out_sensitivity(
    records: Sequence[Mapping[str, Any]],
    creative_ids: Sequence[str],
    top_k: int,
    segment_weights: Mapping[str, float],
    profile_weights: Mapping[str, float],
    profile_segments: Mapping[str, str],
    baseline: set[str],
    *,
    field: str,
    method: str,
    omitted_key: str,
    minimum_diversity: int,
    minimum_evaluable_exclusions: int,
) -> dict[str, Any]:
    values = sorted({str(record.get(field)) for record in records})
    if len(values) == 1:
        noun = "grounded_profiles" if field == "grounded_profile_id" else "archetypes"
        return {
            "method": method,
            f"unique_{noun}": 1,
            f"minimum_{noun[:-1]}_diversity": minimum_diversity,
            "attempted_exclusions": 0,
            "evaluable_exclusions": 0,
            "minimum_evaluable_exclusions": minimum_evaluable_exclusions,
            "unevaluable_exclusion_ids": [],
            "evaluability_gate_passed": True,
            "top_k_consistent": True,
            "top_k_changed_for": [],
            "results": [],
            "not_applicable": True,
            "not_applicable_reason": "single_locked_scope_cannot_be_leave_one_out_tested",
        }
    results: list[dict[str, Any]] = []
    unevaluable: list[str] = []
    for omitted in values:
        subset = [record for record in records if str(record.get(field)) != omitted]
        remaining_segments = {str(record["segment_id"]) for record in subset}
        if not subset or remaining_segments != set(segment_weights):
            unevaluable.append(omitted)
            continue
        utilities = _weighted_profile_utilities(
            subset,
            creative_ids,
            segment_weights,
            profile_weights,
            profile_segments,
        )
        membership, cutoff_tied = _cutoff_membership(utilities, top_k)
        ranked = _ranked_ids(utilities)
        selected = set() if cutoff_tied else set(ranked[:top_k])
        results.append(
            {
                omitted_key: omitted,
                "ranked_ids": ranked,
                "top_k_changed": selected != baseline,
                "cutoff_tied": cutoff_tied,
                "fractional_membership": membership,
            }
        )
    changed_for = [
        item[omitted_key] for item in results if item["top_k_changed"]
    ]
    evaluable = (
        len(values) >= minimum_diversity
        and len(results) >= minimum_evaluable_exclusions
    )
    passed = evaluable and not changed_for
    noun = "grounded_profiles" if field == "grounded_profile_id" else "archetypes"
    return {
        "method": method,
        f"unique_{noun}": len(values),
        f"minimum_{noun[:-1]}_diversity": minimum_diversity,
        "attempted_exclusions": len(values),
        "evaluable_exclusions": len(results),
        "minimum_evaluable_exclusions": minimum_evaluable_exclusions,
        "unevaluable_exclusion_ids": unevaluable,
        "evaluability_gate_passed": evaluable,
        "top_k_consistent": passed,
        "top_k_changed_for": changed_for,
        "results": results,
    }


def _empty_profile_sensitivity(
    minimum_diversity: int,
    minimum_evaluable_exclusions: int,
) -> dict[str, Any]:
    return {
        "method": "leave_one_grounded_profile_out",
        "unique_grounded_profiles": 0,
        "minimum_grounded_profile_diversity": minimum_diversity,
        "attempted_exclusions": 0,
        "evaluable_exclusions": 0,
        "minimum_evaluable_exclusions": minimum_evaluable_exclusions,
        "unevaluable_exclusion_ids": [],
        "evaluability_gate_passed": False,
        "top_k_consistent": False,
        "top_k_changed_for": [],
        "results": [],
    }


def _empty_profile_result(
    study_id: str,
    roster: Sequence[str],
    top_k: int,
    validity: str,
    reasons: list[str],
    diagnostics: Mapping[str, Any],
    *,
    recovery_config_version: str,
    minimum_grounded_profile_diversity: int,
    minimum_evaluable_grounded_profile_exclusions: int,
) -> dict[str, Any]:
    result = _empty_result(
        study_id, roster, top_k, validity, reasons, diagnostics
    )
    result["grounded_profile_sensitivity"] = _empty_profile_sensitivity(
        minimum_grounded_profile_diversity,
        minimum_evaluable_grounded_profile_exclusions,
    )
    result["recovery_config_version"] = recovery_config_version
    result["interpretation_limits"] = [
        "No complete-set shortlist is reported while the run is incomplete or invalid.",
        "Grounded profiles are reusable blueprints; executions are not independent humans.",
        "Results do not establish human-response or campaign-performance validity.",
    ]
    return result


def _aggregate_complete_exposure_profile_stratified(
    records: Sequence[Mapping[str, Any]],
    *,
    study_id: str,
    creative_ids: Sequence[str],
    top_k: int,
    segment_weights: Mapping[str, float],
    profile_weights: Mapping[str, float],
    seed: int,
    resamples: int,
    collection_open: bool,
    expected_job_slots: int | None,
    minimum_usable_records_per_segment: int,
    minimum_usable_records_per_profile: int | Mapping[str, int],
    finalist_inclusion_threshold: float,
    nonfinalist_inclusion_threshold: float,
    minimum_archetype_diversity: int,
    minimum_evaluable_archetype_exclusions: int,
    minimum_grounded_profile_diversity: int,
    minimum_evaluable_grounded_profile_exclusions: int,
    recovery_config_version: str,
) -> dict[str, Any]:
    roster = tuple(creative_ids)
    if not 2 <= len(roster) <= 6 or len(set(roster)) != len(roster):
        raise ValueError("complete exposure requires 2-6 unique creative IDs")
    if not 1 <= top_k <= len(roster):
        raise ValueError("requested shortlist must not exceed the complete set")
    if resamples != PRODUCTION_RESAMPLES:
        raise ValueError("complete-exposure production aggregation requires 2000 resamples")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    for field, value in (
        ("minimum_archetype_diversity", minimum_archetype_diversity),
        (
            "minimum_evaluable_archetype_exclusions",
            minimum_evaluable_archetype_exclusions,
        ),
        ("minimum_grounded_profile_diversity", minimum_grounded_profile_diversity),
        (
            "minimum_evaluable_grounded_profile_exclusions",
            minimum_evaluable_grounded_profile_exclusions,
        ),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(f"{field} must be an integer of at least two")
    if not records:
        raise ValueError("complete exposure requires accepted response records")

    accepted_segments = {str(record.get("segment_id")) for record in records}
    if accepted_segments != set(segment_weights):
        raise ValueError(
            "manifest audience-lock segment IDs must exactly match accepted response segment IDs"
        )
    profile_segments = _validate_profile_design(records, profile_weights, segment_weights)
    floors = _profile_floor_map(
        minimum_usable_records_per_profile, sorted(profile_weights)
    )
    usable = [
        record
        for record in records
        if record.get("usable_complete_exposure_observation") is True
    ]
    accepted_replicates = {str(record["synthetic_replicate_id"]) for record in records}
    total_calls = sum(
        len(record.get("runtime_attempts", ()))
        for record in records
        if isinstance(record.get("runtime_attempts"), Sequence)
    )
    accepted_by_profile = Counter(_profile_id(record) for record in records)
    base_diagnostics: dict[str, Any] = {
        "accepted_response_records": len(records),
        "accepted_unique_replicates": len(accepted_replicates),
        "accepted_response_records_by_segment": dict(
            sorted(Counter(str(record.get("segment_id")) for record in records).items())
        ),
        "accepted_response_records_by_grounded_profile": dict(
            sorted(accepted_by_profile.items())
        ),
        "accepted_response_records_by_context_stratum": dict(
            sorted(
                Counter(
                    str(record.get("context_stratum_id"))
                    for record in records
                    if isinstance(record.get("context_stratum_id"), str)
                ).items()
            )
        ),
        "unique_job_slots_consumed": len(accepted_replicates),
        "total_model_calls": total_calls,
        "usable_observation_count": len(usable),
        "unusable_observation_count": len(records) - len(usable),
        "complete_set_size": len(roster),
        "weighting": {
            "method": "profile_then_segment_frozen_weights",
            "within_segment_profile_weights": dict(sorted(profile_weights.items())),
            "segment_weights": dict(sorted(segment_weights.items())),
            "grounded_profile_segments": dict(sorted(profile_segments.items())),
        },
    }

    if collection_open or (
        expected_job_slots is not None and len(accepted_replicates) < expected_job_slots
    ):
        return _empty_profile_result(
            study_id,
            roster,
            top_k,
            "incomplete",
            ["collection_open_or_required_jobs_missing"],
            base_diagnostics,
            recovery_config_version=recovery_config_version,
            minimum_grounded_profile_diversity=minimum_grounded_profile_diversity,
            minimum_evaluable_grounded_profile_exclusions=(
                minimum_evaluable_grounded_profile_exclusions
            ),
        )
    if not usable:
        return _empty_profile_result(
            study_id,
            roster,
            top_k,
            "invalid",
            ["no_usable_complete_set_observations"],
            base_diagnostics,
            recovery_config_version=recovery_config_version,
            minimum_grounded_profile_diversity=minimum_grounded_profile_diversity,
            minimum_evaluable_grounded_profile_exclusions=(
                minimum_evaluable_grounded_profile_exclusions
            ),
        )

    usable_by_segment = Counter(str(record["segment_id"]) for record in usable)
    usable_by_profile = Counter(_profile_id(record) for record in usable)
    below_segment_floor = sorted(
        segment_id
        for segment_id in segment_weights
        if usable_by_segment[segment_id] < minimum_usable_records_per_segment
    )
    below_profile_floor = sorted(
        profile_id
        for profile_id, floor in floors.items()
        if usable_by_profile[profile_id] < floor
    )
    base_diagnostics.update(
        {
            "usable_observations_by_segment": dict(sorted(usable_by_segment.items())),
            "usable_observations_by_grounded_profile": {
                profile_id: usable_by_profile[profile_id]
                for profile_id in sorted(profile_weights)
            },
            "minimum_usable_records_per_segment": minimum_usable_records_per_segment,
            "minimum_usable_records_by_grounded_profile": dict(sorted(floors.items())),
            "grounded_profiles_below_usable_floor": below_profile_floor,
        }
    )
    profiles_without_usable = sorted(
        profile_id for profile_id in profile_weights if usable_by_profile[profile_id] == 0
    )
    if profiles_without_usable:
        base_diagnostics["grounded_profiles_without_usable_records"] = profiles_without_usable
        return _empty_profile_result(
            study_id,
            roster,
            top_k,
            "exploratory",
            ["grounded_profile_usable_record_floor_not_met"],
            base_diagnostics,
            recovery_config_version=recovery_config_version,
            minimum_grounded_profile_diversity=minimum_grounded_profile_diversity,
            minimum_evaluable_grounded_profile_exclusions=(
                minimum_evaluable_grounded_profile_exclusions
            ),
        )

    utilities = _weighted_profile_utilities(
        usable, roster, segment_weights, profile_weights, profile_segments
    )
    ranked = _ranked_ids(utilities)
    membership, cutoff_tied = _cutoff_membership(utilities, top_k)
    bootstrap, duplication = _bootstrap_profile_stability(
        usable,
        roster,
        segment_weights,
        profile_weights,
        profile_segments,
        top_k,
        seed,
        resamples,
    )
    preliminary = ranked[:top_k]
    preliminary_set = set(preliminary)
    frequencies = bootstrap["frequencies"]
    stability_passed = all(
        frequencies[creative_id] >= finalist_inclusion_threshold
        for creative_id in preliminary_set
    ) and all(
        frequencies[creative_id] <= nonfinalist_inclusion_threshold
        for creative_id in roster
        if creative_id not in preliminary_set
    )
    if cutoff_tied:
        validity = "exploratory"
        selection_status = "unresolved"
        reasons = ["complete_set_cutoff_tie"]
        proposed: list[str] = []
        classifications = {creative_id: "unresolved" for creative_id in roster}
    else:
        validity = "valid"
        selection_status = "resolved"
        reasons = []
        proposed = ranked[:top_k]
        proposed_set = set(proposed)
        classifications = {
            creative_id: (
                "proposed_finalist" if creative_id in proposed_set else "not_proposed"
            )
            for creative_id in roster
        }

    baseline = set(proposed)
    archetype_sensitivity = _leave_one_out_sensitivity(
        usable,
        roster,
        top_k,
        segment_weights,
        profile_weights,
        profile_segments,
        baseline,
        field="persona_archetype_id",
        method="leave_one_persona_archetype_out",
        omitted_key="omitted_archetype_id",
        minimum_diversity=minimum_archetype_diversity,
        minimum_evaluable_exclusions=minimum_evaluable_archetype_exclusions,
    )
    profile_sensitivity = _leave_one_out_sensitivity(
        usable,
        roster,
        top_k,
        segment_weights,
        profile_weights,
        profile_segments,
        baseline,
        field="grounded_profile_id",
        method="leave_one_grounded_profile_out",
        omitted_key="omitted_grounded_profile_id",
        minimum_diversity=minimum_grounded_profile_diversity,
        minimum_evaluable_exclusions=minimum_evaluable_grounded_profile_exclusions,
    )
    disagreement = _profile_disagreement(usable, roster, top_k, preliminary)
    disagreement["shortlist_fragile"] = bool(
        profile_sensitivity["top_k_changed_for"]
        or not profile_sensitivity["evaluability_gate_passed"]
    )

    archetype_passed = archetype_sensitivity["top_k_consistent"]
    profile_passed = profile_sensitivity["top_k_consistent"]
    if validity == "valid" and (
        below_segment_floor
        or below_profile_floor
        or not stability_passed
        or not archetype_passed
        or not profile_passed
    ):
        validity = "exploratory"
        selection_status = "unresolved"
        reasons = []
        if below_segment_floor:
            reasons.append("usable_record_floor_not_met")
        if below_profile_floor:
            reasons.append("grounded_profile_usable_record_floor_not_met")
        if not stability_passed:
            reasons.append("conditional_stability_gate_not_met")
        if not archetype_sensitivity["evaluability_gate_passed"]:
            reasons.append("archetype_sensitivity_unevaluable")
        elif archetype_sensitivity["top_k_changed_for"]:
            reasons.append("archetype_sensitivity_gate_not_met")
        if not profile_sensitivity["evaluability_gate_passed"]:
            reasons.append("grounded_profile_sensitivity_unevaluable")
        elif profile_sensitivity["top_k_changed_for"]:
            reasons.append("grounded_profile_sensitivity_gate_not_met")
        proposed = []
        classifications = {creative_id: "unresolved" for creative_id in roster}

    diagnostics = {
        **base_diagnostics,
        "response_duplication": duplication,
        "grounded_profile_disagreement": disagreement,
        "bootstrap": {
            key: value for key, value in bootstrap.items() if key != "frequencies"
        },
        "gates": {
            "complete_set_coverage": True,
            "closed_collection": True,
            "usable_record_floor": not below_segment_floor,
            "grounded_profile_usable_record_floor": not below_profile_floor,
            "cutoff_resolved": not cutoff_tied,
            "conditional_stability": stability_passed,
            "archetype_sensitivity": archetype_passed,
            "grounded_profile_sensitivity": profile_passed,
        },
    }
    return {
        "study_id": study_id,
        "method": "complete_exposure",
        "estimand": "centered_complete_set_normalized_rank_utility",
        "stability_diagnostic": "conditional_within_run_top_k_inclusion_frequency",
        "requested_top_k": top_k,
        "utilities": utilities,
        "ranked_ids": ranked,
        "top_k_inclusion_frequencies": bootstrap["frequencies"],
        "classifications": classifications,
        "selection_status": selection_status,
        "proposed_finalist_ids": proposed,
        "archetype_sensitivity": archetype_sensitivity,
        "grounded_profile_sensitivity": profile_sensitivity,
        "model_diagnostics": diagnostics,
        "recovery_config_version": recovery_config_version,
        "validity_status": validity,
        "validity_reasons": reasons,
        "interpretation_limits": [
            "The complete-set signal is conditional only on this recorded synthetic run.",
            "Grounded profiles are reusable blueprints; executions are not independent humans.",
            "Conditional stability is not population uncertainty or human alignment.",
            "Results do not estimate survey preference, campaign performance, "
            "conversion, or revenue impact.",
            (
                "The run is conditional on one locked grounded profile; cross-profile "
                "sensitivity is not applicable and no broader audience heterogeneity claim is supported."
                if len(profile_weights) == 1
                else "Grounded-profile sensitivity is conditional on the frozen modeled profile set."
            ),
        ],
    }


def aggregate_complete_exposure(
    records: Sequence[Mapping[str, Any]],
    *,
    study_id: str,
    creative_ids: Sequence[str],
    top_k: int,
    segment_weights: Mapping[str, float],
    seed: int,
    resamples: int = PRODUCTION_RESAMPLES,
    collection_open: bool = False,
    expected_job_slots: int | None = None,
    minimum_usable_records_per_segment: int = 8,
    finalist_inclusion_threshold: float = 0.90,
    nonfinalist_inclusion_threshold: float = 0.10,
    minimum_archetype_diversity: int = 2,
    minimum_evaluable_archetype_exclusions: int = 2,
    recovery_config_version: str | None = None,
    profile_weights: Mapping[str, float] | None = None,
    minimum_usable_records_per_profile: int | Mapping[str, int] = 1,
    minimum_grounded_profile_diversity: int = 2,
    minimum_evaluable_grounded_profile_exclusions: int = 2,
) -> dict[str, Any]:
    """Aggregate complete-set records using v2 or profile-stratified semantics.

    Calls that omit ``profile_weights`` retain the frozen v2 segment-only path.
    New policy versions pass within-segment frozen ``profile_weights`` and use
    profile-stratified estimation, resampling, floors, and sensitivity.
    """

    if profile_weights is None:
        return _aggregate_complete_exposure_v2(
            records,
            study_id=study_id,
            creative_ids=creative_ids,
            top_k=top_k,
            segment_weights=segment_weights,
            seed=seed,
            resamples=resamples,
            collection_open=collection_open,
            expected_job_slots=expected_job_slots,
            minimum_usable_records_per_segment=minimum_usable_records_per_segment,
            finalist_inclusion_threshold=finalist_inclusion_threshold,
            nonfinalist_inclusion_threshold=nonfinalist_inclusion_threshold,
            minimum_archetype_diversity=minimum_archetype_diversity,
            minimum_evaluable_archetype_exclusions=minimum_evaluable_archetype_exclusions,
            recovery_config_version=(
                recovery_config_version or CALIBRATION_POLICY_VERSION
            ),
        )
    return _aggregate_complete_exposure_profile_stratified(
        records,
        study_id=study_id,
        creative_ids=creative_ids,
        top_k=top_k,
        segment_weights=segment_weights,
        profile_weights=profile_weights,
        seed=seed,
        resamples=resamples,
        collection_open=collection_open,
        expected_job_slots=expected_job_slots,
        minimum_usable_records_per_segment=minimum_usable_records_per_segment,
        minimum_usable_records_per_profile=minimum_usable_records_per_profile,
        finalist_inclusion_threshold=finalist_inclusion_threshold,
        nonfinalist_inclusion_threshold=nonfinalist_inclusion_threshold,
        minimum_archetype_diversity=minimum_archetype_diversity,
        minimum_evaluable_archetype_exclusions=minimum_evaluable_archetype_exclusions,
        minimum_grounded_profile_diversity=minimum_grounded_profile_diversity,
        minimum_evaluable_grounded_profile_exclusions=(
            minimum_evaluable_grounded_profile_exclusions
        ),
        recovery_config_version=(
            recovery_config_version or PROFILE_STRATIFIED_POLICY_VERSION
        ),
    )


def _empty_result(
    study_id: str,
    roster: Sequence[str],
    top_k: int,
    validity: str,
    reasons: list[str],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "study_id": study_id,
        "method": "complete_exposure",
        "estimand": "centered_complete_set_normalized_rank_utility",
        "stability_diagnostic": "conditional_within_run_top_k_inclusion_frequency",
        "requested_top_k": top_k,
        "utilities": {},
        "ranked_ids": [],
        "top_k_inclusion_frequencies": {},
        "classifications": {creative_id: "unresolved" for creative_id in roster},
        "selection_status": validity,
        "proposed_finalist_ids": [],
        "archetype_sensitivity": {
            "method": "leave_one_persona_archetype_out",
            "unique_archetypes": 0,
            "minimum_archetype_diversity": 2,
            "attempted_exclusions": 0,
            "evaluable_exclusions": 0,
            "minimum_evaluable_exclusions": 2,
            "unevaluable_exclusion_ids": [],
            "evaluability_gate_passed": False,
            "top_k_consistent": False,
            "top_k_changed_for": [],
            "results": [],
        },
        "model_diagnostics": dict(diagnostics),
        "recovery_config_version": CALIBRATION_POLICY_VERSION,
        "validity_status": validity,
        "validity_reasons": reasons,
        "interpretation_limits": [
            "No complete-set shortlist is reported while the run is incomplete or invalid.",
            "Results do not establish human-response or campaign-performance validity.",
        ],
    }


__all__ = [
    "CALIBRATION_POLICY_VERSION",
    "PROFILE_STRATIFIED_POLICY_VERSION",
    "PRODUCTION_RESAMPLES",
    "aggregate_complete_exposure",
]
