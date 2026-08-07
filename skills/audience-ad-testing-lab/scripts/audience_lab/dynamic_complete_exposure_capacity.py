"""Profile-aware complete-exposure capacity planning.

This module plans conditional synthetic executions, not human respondents.  It
uses frozen segment and within-segment profile weights to find the smallest
integer allocation that satisfies the experimental repeatability policy and
the authorized screening ceiling.  The policy is intentionally versioned so
future empirical calibration can replace its provisional floors without
reinterpreting closed runs.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping, Sequence


SCHEMA_VERSION = "dynamic-complete-exposure-capacity-v1"
POLICY_VERSION = "dynamic-complete-exposure-experimental-v1"
DEFAULT_MAXIMUM_ABSOLUTE_DEVIATION = Fraction(1, 20)


def _fraction(value: object, field: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _apportion(
    total: int,
    weights: Mapping[str, Fraction],
    minimums: Mapping[str, int],
) -> dict[str, int] | None:
    """Allocate ``total`` deterministically with lower bounds.

    Each remaining unit goes to the largest deficit against the final weighted
    target.  Lexicographic IDs break exact ties, making the result independent
    of source-object ordering.
    """

    keys = sorted(weights)
    if set(keys) != set(minimums):
        raise ValueError("allocation weights and minimums must cover the same IDs")
    if total < sum(minimums.values()):
        return None
    weight_total = sum((weights[key] for key in keys), Fraction(0))
    if weight_total <= 0:
        raise ValueError("allocation weights must have positive total")
    normalized = {key: weights[key] / weight_total for key in keys}
    counts = {key: minimums[key] for key in keys}
    for _ in range(total - sum(counts.values())):
        largest_deficit = max(
            normalized[key] * total - counts[key] for key in keys
        )
        selected = min(
            key
            for key in keys
            if normalized[key] * total - counts[key] == largest_deficit
        )
        counts[selected] += 1
    return counts


def _candidate_allocation(
    *,
    total: int,
    profile_targets: Mapping[str, Fraction],
    conditional_targets: Mapping[str, Fraction],
    profile_segments: Mapping[str, str],
    segment_targets: Mapping[str, Fraction],
    profile_minimums: Mapping[str, int],
) -> dict[str, object] | None:
    segment_minimums = {
        segment_id: sum(
            profile_minimums[profile_id]
            for profile_id in profile_minimums
            if profile_segments[profile_id] == segment_id
        )
        for segment_id in sorted(segment_targets)
    }
    segment_counts = _apportion(total, segment_targets, segment_minimums)
    if segment_counts is None:
        return None

    profile_counts: dict[str, int] = {}
    for segment_id in sorted(segment_targets):
        scoped_ids = sorted(
            profile_id
            for profile_id in profile_targets
            if profile_segments[profile_id] == segment_id
        )
        scoped_counts = _apportion(
            segment_counts[segment_id],
            {
                profile_id: conditional_targets[profile_id]
                for profile_id in scoped_ids
            },
            {
                profile_id: profile_minimums[profile_id]
                for profile_id in scoped_ids
            },
        )
        if scoped_counts is None:
            return None
        profile_counts.update(scoped_counts)

    profile_deviations = {
        profile_id: abs(Fraction(count, total) - profile_targets[profile_id])
        for profile_id, count in profile_counts.items()
    }
    segment_deviations = {
        segment_id: abs(
            Fraction(segment_counts[segment_id], total)
            - segment_targets[segment_id]
        )
        for segment_id in segment_counts
    }
    conditional_deviations: dict[str, Fraction] = {}
    for profile_id, count in profile_counts.items():
        segment_id = profile_segments[profile_id]
        conditional_deviations[profile_id] = abs(
            Fraction(count, segment_counts[segment_id])
            - conditional_targets[profile_id]
        )
    exact = not any(
        deviation
        for deviation in (
            *profile_deviations.values(),
            *segment_deviations.values(),
            *conditional_deviations.values(),
        )
    )
    return {
        "profile_counts": profile_counts,
        "segment_counts": segment_counts,
        "profile_deviations": profile_deviations,
        "segment_deviations": segment_deviations,
        "conditional_deviations": conditional_deviations,
        "exact": exact,
    }


def _within_tolerance(
    candidate: Mapping[str, object],
    tolerance: Fraction,
) -> bool:
    return all(
        deviation <= tolerance
        for key in (
            "profile_deviations",
            "segment_deviations",
            "conditional_deviations",
        )
        for deviation in candidate[key].values()  # type: ignore[union-attr]
    )


def _find_core(
    *,
    lower_bound: int,
    authorized_maximum: int,
    tolerance: Fraction,
    candidate_arguments: Mapping[str, object],
) -> tuple[int, dict[str, object], str]:
    def candidates(start: int, stop: int):
        for total in range(start, stop + 1):
            candidate = _candidate_allocation(
                total=total,
                **candidate_arguments,  # type: ignore[arg-type]
            )
            if candidate is not None:
                yield total, candidate

    if authorized_maximum >= lower_bound:
        for total, candidate in candidates(lower_bound, authorized_maximum):
            if candidate["exact"]:
                return total, candidate, "exact_frozen_weights_within_authorized_ceiling"
        for total, candidate in candidates(lower_bound, authorized_maximum):
            if _within_tolerance(candidate, tolerance):
                return total, candidate, "bounded_weight_approximation_within_authorized_ceiling"

    search_start = max(lower_bound, authorized_maximum + 1)
    search_stop = search_start + 100_000
    for total, candidate in candidates(search_start, search_stop):
        if _within_tolerance(candidate, tolerance):
            return total, candidate, "minimum_required_capacity_exceeds_authorized_ceiling"
    raise ValueError("no finite profile-aware capacity allocation was found")


def _find_reserve_unit(
    *,
    maximum_size: int,
    tolerance: Fraction,
    candidate_arguments: Mapping[str, object],
) -> tuple[int, dict[str, object], str] | None:
    profile_targets = candidate_arguments["profile_targets"]
    profile_minimums = {profile_id: 1 for profile_id in profile_targets}  # type: ignore[union-attr]
    reserve_arguments = {**candidate_arguments, "profile_minimums": profile_minimums}
    lower = len(profile_minimums)
    if maximum_size < lower:
        return None
    available: list[tuple[int, dict[str, object]]] = []
    for total in range(lower, maximum_size + 1):
        candidate = _candidate_allocation(
            total=total,
            **reserve_arguments,  # type: ignore[arg-type]
        )
        if candidate is not None:
            available.append((total, candidate))
    for total, candidate in available:
        if candidate["exact"]:
            return total, candidate, "exact_weight_compatible_reserve_unit"
    for total, candidate in available:
        if _within_tolerance(candidate, tolerance):
            return total, candidate, "bounded_weight_compatible_reserve_unit"
    return None


def plan_dynamic_complete_exposure_capacity(
    *,
    profiles: Sequence[Mapping[str, object]],
    segment_weights: Mapping[str, object],
    creative_count: int,
    maximum_total_executions: int,
    finalist_reserved: int,
    maximum_absolute_deviation: float = 0.05,
) -> dict[str, object]:
    """Plan core and balanced reserve execution capacity.

    The provisional v1 policy uses the number of creative presentation
    positions, subject to the experimental five-record floor, as each profile's
    usable floor. Failures are handled only by separate balanced reserve blocks.
    This is a structural counterbalancing rule pending the planned empirical
    24/30/36 calibration; it is not a human-sample power calculation.
    """

    creative_count = _positive_int(creative_count, "creative_count")
    maximum_total_executions = _nonnegative_int(
        maximum_total_executions,
        "maximum_total_executions",
    )
    finalist_reserved = _nonnegative_int(finalist_reserved, "finalist_reserved")
    maximum_screening_executions = max(
        0, maximum_total_executions - finalist_reserved
    )
    tolerance = _fraction(
        maximum_absolute_deviation,
        "maximum_absolute_deviation",
    )
    if tolerance <= 0 or tolerance > DEFAULT_MAXIMUM_ABSOLUTE_DEVIATION:
        raise ValueError("maximum_absolute_deviation must be greater than zero and at most 0.05")

    normalized_segment_weights = {
        str(segment_id): _fraction(weight, f"segment_weights.{segment_id}")
        for segment_id, weight in segment_weights.items()
    }
    if not normalized_segment_weights or any(
        not segment_id.strip() or weight <= 0
        for segment_id, weight in normalized_segment_weights.items()
    ):
        raise ValueError("segment weights must use non-empty IDs and positive weights")
    segment_total = sum(normalized_segment_weights.values(), Fraction(0))
    normalized_segment_weights = {
        segment_id: weight / segment_total
        for segment_id, weight in normalized_segment_weights.items()
    }

    normalized_profiles: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(profiles):
        if source.get("eligible") is not True:
            continue
        profile_id = source.get("grounded_profile_id")
        segment_id = source.get("reported_segment_id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError(f"profiles[{index}].grounded_profile_id is required")
        if profile_id in seen_ids:
            raise ValueError("grounded profile IDs must be unique")
        seen_ids.add(profile_id)
        if not isinstance(segment_id, str) or segment_id not in normalized_segment_weights:
            raise ValueError("every eligible profile must reference a weighted segment")
        conditional = _fraction(
            source.get("conditional_effective_weight"),
            f"profiles[{index}].conditional_effective_weight",
        )
        if conditional <= 0:
            raise ValueError("every eligible profile needs a positive conditional weight")
        normalized_profiles.append(
            {
                "grounded_profile_id": profile_id,
                "reported_segment_id": segment_id,
                "conditional_weight": conditional,
            }
        )
    if not normalized_profiles:
        raise ValueError("at least one eligible weighted profile is required")
    normalized_profiles.sort(key=lambda item: str(item["grounded_profile_id"]))

    profiles_by_segment = {
        segment_id: [
            item
            for item in normalized_profiles
            if item["reported_segment_id"] == segment_id
        ]
        for segment_id in sorted(normalized_segment_weights)
    }
    if any(not items for items in profiles_by_segment.values()):
        raise ValueError("every weighted segment needs an eligible profile")

    conditional_targets: dict[str, Fraction] = {}
    profile_targets: dict[str, Fraction] = {}
    profile_segments: dict[str, str] = {}
    for segment_id, items in profiles_by_segment.items():
        conditional_total = sum(
            (item["conditional_weight"] for item in items),  # type: ignore[misc]
            Fraction(0),
        )
        for item in items:
            profile_id = str(item["grounded_profile_id"])
            conditional = item["conditional_weight"] / conditional_total  # type: ignore[operator]
            conditional_targets[profile_id] = conditional
            profile_targets[profile_id] = (
                normalized_segment_weights[segment_id] * conditional
            )
            profile_segments[profile_id] = segment_id

    minimum_usable = {
        profile_id: max(5, creative_count)
        for profile_id in profile_targets
    }
    planned_minimums = dict(minimum_usable)
    lower_bound = sum(planned_minimums.values())
    candidate_arguments: dict[str, object] = {
        "profile_targets": profile_targets,
        "conditional_targets": conditional_targets,
        "profile_segments": profile_segments,
        "segment_targets": normalized_segment_weights,
        "profile_minimums": planned_minimums,
    }
    core_total, core, selection = _find_core(
        lower_bound=lower_bound,
        authorized_maximum=maximum_screening_executions,
        tolerance=tolerance,
        candidate_arguments=candidate_arguments,
    )

    remaining = max(0, maximum_screening_executions - core_total)
    reserve_unit = _find_reserve_unit(
        maximum_size=remaining,
        tolerance=tolerance,
        candidate_arguments=candidate_arguments,
    )
    reserve_blocks: list[dict[str, object]] = []
    if reserve_unit is not None:
        block_size, reserve, reserve_basis = reserve_unit
        for index in range(1, remaining // block_size + 1):
            reserve_blocks.append(
                {
                    "reserve_block_id": f"complete-exposure-reserve-{index:02d}",
                    "planned_executions": block_size,
                    "allocation_by_segment": reserve["segment_counts"],
                    "allocation_by_profile": reserve["profile_counts"],
                    "weight_fidelity": {
                        "exact": reserve["exact"],
                        "maximum_absolute_deviation": float(tolerance),
                        "selection_basis": reserve_basis,
                    },
                    "activation_rules": [
                        "a preregistered usable-record floor is not met",
                        "a preregistered repeatability or integrity gate is not met",
                        "never activate from the identity of the leading creative",
                    ],
                    "assignment_rule": (
                        "bind each slot to its frozen profile and the next "
                        "deterministic presentation-order role"
                    ),
                }
            )

    reserved_screening = sum(
        int(block["planned_executions"]) for block in reserve_blocks
    )
    profile_rows = [
        {
            "grounded_profile_id": profile_id,
            "reported_segment_id": profile_segments[profile_id],
            "target_global_weight": float(profile_targets[profile_id]),
            "target_within_segment_weight": float(conditional_targets[profile_id]),
            "minimum_usable_records": minimum_usable[profile_id],
            "failure_handling": "balanced_reserve_blocks",
            "planned_executions": core["profile_counts"][profile_id],  # type: ignore[index]
        }
        for profile_id in sorted(profile_targets)
    ]
    authorized = core_total <= maximum_screening_executions
    maximum_screening_slots = core_total + reserved_screening
    required_total_with_reserve = maximum_screening_slots + finalist_reserved
    authorized_total_capacity_satisfied = (
        authorized and required_total_with_reserve <= maximum_total_executions
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_status": "experimental_pending_empirical_repeatability_calibration",
        "calibration_basis": (
            "frozen_profile_weights_plus_complete_exposure_presentation_balance;"
            "not_human_market_calibration"
        ),
        "core_planned_executions": core_total,
        "core_allocation_by_segment": core["segment_counts"],
        "core_allocation_by_profile": profile_rows,
        "global_minimum_usable_records": sum(minimum_usable.values()),
        "balanced_reserve_blocks": reserve_blocks,
        "screening_reserved": reserved_screening,
        "maximum_screening_executions": maximum_screening_executions,
        "maximum_screening_slots": maximum_screening_slots,
        "finalist_reserved": finalist_reserved,
        "required_total_with_reserve": required_total_with_reserve,
        "authorized_total_execution_ceiling": maximum_total_executions,
        "maximum_authorized_unique_execution_slots": min(
            required_total_with_reserve,
            maximum_total_executions,
        ),
        "authorized_capacity_satisfied": authorized,
        "authorized_total_capacity_satisfied": (
            authorized_total_capacity_satisfied
        ),
        "authorized_capacity_shortfall": max(
            0, core_total + finalist_reserved - maximum_total_executions
        ),
        "weight_fidelity": {
            "exact": core["exact"],
            "maximum_absolute_deviation": float(tolerance),
            "selection_basis": selection,
            "profile_absolute_deviation": {
                profile_id: float(deviation)
                for profile_id, deviation in core[
                    "profile_deviations"
                ].items()  # type: ignore[union-attr]
            },
            "segment_absolute_deviation": {
                segment_id: float(deviation)
                for segment_id, deviation in core[
                    "segment_deviations"
                ].items()  # type: ignore[union-attr]
            },
            "within_segment_profile_absolute_deviation": {
                profile_id: float(deviation)
                for profile_id, deviation in core[
                    "conditional_deviations"
                ].items()  # type: ignore[union-attr]
            },
        },
        "selection_rationale": [
            (
                "Each grounded profile needs at least five usable records or enough "
                "records to cover all creative presentation positions, whichever is larger."
            ),
            "Frozen segment and within-segment profile weights determine integer allocation.",
            (
                "The smallest exact in-ceiling design is preferred; otherwise "
                "the declared 0.05 tolerance applies."
            ),
            "Balanced reserves are frozen before collection and cannot be winner-triggered.",
        ],
        "stop_extend_failure_rules": {
            "stop": "stop after the core when all preregistered gates pass",
            "extend": "activate only the next whole balanced reserve block",
            "unresolved": "return unresolved when the frozen maximum cannot pass",
            "replacement": (
                "replace a failed execution only with the same profile and "
                "presentation-order role under a fresh execution ID"
            ),
        },
        "count_semantics": {
            "grounded_audience_profiles": len(profile_targets),
            "planned_isolated_synthetic_executions": core_total,
            "minimum_usable_feedback_records": sum(minimum_usable.values()),
            "maximum_reserved_synthetic_executions": reserved_screening,
            "finalist_reserved_synthetic_executions": finalist_reserved,
            "human_respondents": 0,
            "human_sample_independence": False,
            "one_execution_yields_at_most_one_accepted_feedback_record": True,
        },
    }


__all__ = [
    "DEFAULT_MAXIMUM_ABSOLUTE_DEVIATION",
    "POLICY_VERSION",
    "SCHEMA_VERSION",
    "plan_dynamic_complete_exposure_capacity",
]
