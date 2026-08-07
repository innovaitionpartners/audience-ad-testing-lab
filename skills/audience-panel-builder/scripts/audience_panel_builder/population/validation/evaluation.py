"""Held-out Tier 4 ordering gates and narrow claim issuance.

Comparisons embed validation observations and closed evidence projections
that contract validation recomputes. Evaluation never promotes a caller
status flag, inferred power, or incomplete sibling-family summary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from itertools import combinations

from ...common import ContractError, require_timestamp, sha256_json
from .contracts import (
    CLAIM_FAMILY_VERSION,
    EVALUATION_VERSION,
    TIER4_CLAIM_TEXT,
    TIER4_CLAIM_VERSION,
    TIER4_REFRESH_TRIGGERS,
    TIER4_REQUIRED_DISCLAIMER,
    authenticate_preregistration_design,
    project_synthetic_result_binding,
    require_design_approval,
    validate_claim_family,
    validate_comparison,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_tier4_claim,
)
from .statistics import (
    InsufficientUncertaintyError,
    bca_block_interval,
    block_pairwise_agreement,
    complete_block_sign_permutation_p,
    holm_adjust,
    kendall_tau_b,
)


_MINIMUM_BLOCKS = 12
_MINIMUM_CREATIVES_PER_BLOCK = 3
_MINIMUM_ARMS = 36
_MINIMUM_BATCHES = 3
_MINIMUM_BLOCK_COVERAGE = 0.80
_MINIMUM_ARM_COVERAGE = 0.90
_MAXIMUM_MISSINGNESS = 0.10
_MINIMUM_DETERMINATE_PAIRS = 0.70
_MINIMUM_PLANNED_SAMPLE = 0.90
_MINIMUM_POWER = 0.80
_MINIMUM_TAU = 0.40
_MINIMUM_AGREEMENT = 0.60
_MINIMUM_SEGMENT_WEIGHT = 0.10
_MINIMUM_SEGMENT_BLOCKS = 6
_MINIMUM_SEGMENT_ARMS = 18
_MINIMUM_SEGMENT_COVERAGE = 0.80
_MINIMUM_SEGMENT_AGREEMENT = 0.55


def _comparison_set_sha256(comparisons: Sequence[dict[str, object]]) -> str:
    """Bind all comparisons for one registration in registered block order."""
    ordered = sorted(
        comparisons,
        key=lambda item: str(item["block_binding"]["block_id"]),
    )
    return sha256_json([item["comparison_sha256"] for item in ordered])


def _status(code: str) -> dict[str, object]:
    return {"status": code}


def _ranking_values(groups: object, *, path: str) -> tuple[list[float], list[str]]:
    if not isinstance(groups, list) or not groups:
        raise ContractError(f"{path} must be a non-empty ordering")
    ranks: list[float] = []
    ids: list[str] = []
    for rank, group in enumerate(groups, start=1):
        if not isinstance(group, list) or not group:
            raise ContractError(f"{path} groups must be non-empty arrays")
        for creative_id in group:
            if not isinstance(creative_id, str):
                raise ContractError(f"{path} creative IDs must be strings")
            ids.append(creative_id)
            ranks.append(float(rank))
    return ranks, ids


def _block_tau(comparison: dict[str, object]) -> float:
    synthetic, synthetic_ids = _ranking_values(
        comparison["synthetic_ordering"], path="comparison.synthetic_ordering",
    )
    observed, observed_ids = _ranking_values(
        comparison["observed_ordering"], path="comparison.observed_ordering",
    )
    if set(synthetic_ids) != set(observed_ids) or len(synthetic_ids) != len(observed_ids):
        raise ContractError("comparison orderings must contain the same creative IDs")
    observed_by_id = dict(zip(observed_ids, observed))
    return kendall_tau_b(synthetic, [observed_by_id[item] for item in synthetic_ids])


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _reason(limitations: list[str], code: str) -> None:
    item = f"reason-code:{code}"
    if item not in limitations:
        limitations.append(item)


def _interval_payload(interval: object | None) -> dict[str, object]:
    if interval is None:
        return {
            "available": False,
            "point": None,
            "two_sided_lower": None,
            "two_sided_upper": None,
            "one_sided_lower": None,
        }
    return {
        "available": True,
        "point": float(interval.point),
        "two_sided_lower": float(interval.two_sided_lower),
        "two_sided_upper": float(interval.two_sided_upper),
        "one_sided_lower": float(interval.one_sided_lower),
    }


def _is_material_segment(segment: Mapping[str, object]) -> bool:
    return (
        segment["must_cover"] is True
        or float(segment["effective_panel_weight"]) >= _MINIMUM_SEGMENT_WEIGHT
    )


def _material_segment_gate(
    *,
    eligible_blocks: int,
    creative_arms: int,
    block_coverage: float,
    tau_interval: object | None,
    agreement_interval: object | None,
) -> tuple[bool, bool, bool]:
    """Return ``(sparse, clear_reversal, passes)`` at exact C1 boundaries."""
    sparse = (
        eligible_blocks < _MINIMUM_SEGMENT_BLOCKS
        or creative_arms < _MINIMUM_SEGMENT_ARMS
        or block_coverage < _MINIMUM_SEGMENT_COVERAGE
        or tau_interval is None
        or agreement_interval is None
    )
    tau_reversal = bool(
        tau_interval is not None
        and tau_interval.point < 0
        and tau_interval.two_sided_upper < 0
    )
    agreement_reversal = bool(
        agreement_interval is not None
        and agreement_interval.point < 0.50
        and agreement_interval.two_sided_upper < 0.50
    )
    reversal = tau_reversal or agreement_reversal
    passes = bool(
        not sparse
        and not reversal
        and tau_interval.point > 0
        and agreement_interval.point >= _MINIMUM_SEGMENT_AGREEMENT
    )
    return sparse, reversal, passes


def _influence_diagnostics(
    *,
    block_ids: Sequence[str],
    tau_values: Sequence[float],
    agreement_values: Sequence[float],
    study_ids: Sequence[str],
    family_alpha: float,
    seed: int,
) -> dict[str, object]:
    """Calculate descriptive block and batch leave-out sensitivity."""

    if (
        len(block_ids) != len(tau_values)
        or len(tau_values) != len(agreement_values)
        or len(tau_values) != len(study_ids)
        or len(tau_values) < 3
    ):
        return {
            "status": "unavailable",
            "maximum_block_contribution": (
                1.0 / len(tau_values) if tau_values else 1.0
            ),
            "leave_one_block": [],
            "leave_one_batch": [],
        }

    def result(indices: Sequence[int]) -> dict[str, object]:
        if len(indices) < 2:
            return {
                "tau": 0.0,
                "agreement": 0.0,
                "one_sided_p_value": 1.0,
                "registered_point_and_raw_p_thresholds_retained": False,
            }
        taus = [float(tau_values[index]) for index in indices]
        agreements = [float(agreement_values[index]) for index in indices]
        tau = sum(taus) / len(taus)
        agreement = sum(agreements) / len(agreements)
        p_value = complete_block_sign_permutation_p(taus, seed=seed)
        return {
            "tau": tau,
            "agreement": agreement,
            "one_sided_p_value": p_value,
            "registered_point_and_raw_p_thresholds_retained": (
                tau >= _MINIMUM_TAU
                and agreement >= _MINIMUM_AGREEMENT
                and p_value <= family_alpha
            ),
        }

    all_indices = tuple(range(len(tau_values)))
    leave_one_block = [{
        "block_id": str(block_ids[omitted]),
        **result(tuple(
            index for index in all_indices if index != omitted
        )),
    } for omitted in all_indices]
    leave_one_batch = [{
        "study_id": omitted_study,
        **result(tuple(
            index for index in all_indices
            if study_ids[index] != omitted_study
        )),
    } for omitted_study in sorted(set(study_ids))]
    stable = all(
        bool(row["registered_point_and_raw_p_thresholds_retained"])
        for row in (*leave_one_block, *leave_one_batch)
    )
    return {
        "status": (
            "all_leave_outs_meet_registered_point_and_raw_p_thresholds"
            if stable
            else "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds"
        ),
        "maximum_block_contribution": 1.0 / len(tau_values),
        "leave_one_block": leave_one_block,
        "leave_one_batch": leave_one_batch,
    }


def _ordering_relation(groups: object, left: str, right: str, *, observed: bool) -> str:
    _, identifiers = _ranking_values(groups, path="comparison.ordering")
    rank_by_id: dict[str, int] = {}
    for rank, group in enumerate(groups, start=1):  # type: ignore[union-attr]
        for creative_id in group:
            rank_by_id[creative_id] = rank
    left_rank, right_rank = rank_by_id[left], rank_by_id[right]
    if left_rank == right_rank:
        return "observed_equivalent" if observed else "synthetic_tie"
    if left_rank < right_rank:
        return "observed_a_above_b" if observed else "synthetic_a_above_b"
    return "observed_b_above_a" if observed else "synthetic_b_above_a"


def _comparison_ordering_is_exact(comparison: dict[str, object]) -> bool:
    mapped = {row["creative_binding"]["creative_id"] for row in comparison["arm_mappings"]}
    try:
        _, synthetic_ids = _ranking_values(comparison["synthetic_ordering"], path="comparison.synthetic_ordering")
        _, observed_ids = _ranking_values(comparison["observed_ordering"], path="comparison.observed_ordering")
    except ContractError:
        return False
    if len(synthetic_ids) != len(set(synthetic_ids)) or len(observed_ids) != len(set(observed_ids)):
        return False
    if set(synthetic_ids) != mapped or set(observed_ids) != mapped:
        return False
    expected = {frozenset(pair) for pair in combinations(mapped, 2)}
    actual: set[frozenset[str]] = set()
    for pair in comparison["pairwise_comparisons"]:
        left, right = pair["creative_a"], pair["creative_b"]
        identity = frozenset((left, right))
        if left == right or left not in mapped or right not in mapped or identity in actual:
            return False
        actual.add(identity)
        if pair["synthetic_direction"] != _ordering_relation(comparison["synthetic_ordering"], left, right, observed=False):
            return False
        expected_observed = _ordering_relation(comparison["observed_ordering"], left, right, observed=True)
        # Indeterminacy can be evidence-grade despite a rank serialization;
        # every determinate direction must still agree with the ordering.
        if pair["observed_direction"] != "observed_indeterminate" and pair["observed_direction"] != expected_observed:
            return False
    return actual == expected


def _validate_bound_comparisons(
    registration: dict[str, object], comparisons: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str], bool]:
    """Validate every closed comparison and prove its registration binding."""
    registered = validate_preregistration(registration)
    validated: list[dict[str, object]] = []
    reasons: list[str] = []
    invalid = False
    expected_blocks = {
        block["block_id"]: block for block in registered["validation_blocks"]
    }
    seen: set[str] = set()
    eligible = {
        item["creative_id"]: item["creative_sha256"]
        for item in registered["synthetic_surface"]["eligible_creatives"]
    }
    compact = project_synthetic_result_binding(registered["synthetic_surface"])
    for payload in comparisons:
        try:
            comparison = validate_comparison(payload)
        except ContractError:
            invalid = True
            _reason(reasons, "invalid-comparison")
            continue
        block = comparison["block_binding"]
        block_id = block["block_id"]
        if any(
            observation["holdout_status"] != "eligible_held_out"
            for observation in comparison["observations"]
        ):
            invalid = True
            _reason(reasons, "non-held-out-or-leaked-observation")
            continue
        if block_id in seen:
            invalid = True
            _reason(reasons, "duplicate-validation-block")
            continue
        seen.add(block_id)
        registered_block = expected_blocks.get(block_id)
        binding = comparison["registration_binding"]
        if (
            binding["registration_id"] != registered["registration_id"]
            or binding["registration_sha256"] != registered["registration_sha256"]
            or comparison["panel_binding"] != registered["panel_binding"]
            or comparison["synthetic_result_binding"] != compact
            or comparison["metric_binding"] != registered["primary_metric"]
            or registered_block is None
            or block["study_id"] != registered_block["study_id"]
        ):
            invalid = True
            _reason(reasons, "comparison-binding-mismatch")
        else:
            planned = set(registered_block["planned_arm_ids"])
            mapped = {row["arm_id"] for row in comparison["arm_mappings"]}
            if not mapped.issubset(planned):
                invalid = True
                _reason(reasons, "unplanned-arm")
            for row in comparison["arm_mappings"]:
                creative = row["creative_binding"]
                if eligible.get(creative["creative_id"]) != creative["creative_sha256"]:
                    invalid = True
                    _reason(reasons, "ineligible-creative-binding")
            if not _comparison_ordering_is_exact(comparison):
                invalid = True
                _reason(reasons, "comparison-ordering-or-pairs-mismatch")
        validated.append(comparison)
    return validated, reasons, invalid


def _family_for_registration(
    registration: dict[str, object], comparisons: Sequence[dict[str, object]],
    claim_family: dict[str, object], reasons: list[str],
) -> tuple[bool, bool]:
    """Recompute every sibling p-value and gate the exact current Holm value."""
    try:
        family = validate_claim_family(claim_family)
    except ContractError:
        _reason(reasons, "invalid-claim-family")
        return False, False
    members = family["member_registration_ids"]
    try:
        index = members.index(registration["registration_id"])
    except ValueError:
        _reason(reasons, "missing-claim-family-member")
        return False, False
    embedded = family["member_preregistrations"][index]
    if embedded != registration:
        _reason(reasons, "post-outcome-family-edit")
        return False, False
    if family["member_comparison_sha256"][index] != _comparison_set_sha256(comparisons):
        _reason(reasons, "comparison-hash-mismatch")
        return False, False
    current_embedded = family["member_comparisons"][index]
    supplied_hashes = [
        item["comparison_sha256"] for item in sorted(
            comparisons,
            key=lambda item: str(item["block_binding"]["block_id"]),
        )
    ]
    embedded_hashes = [
        item["comparison_sha256"] for item in sorted(
            current_embedded,
            key=lambda item: str(item["block_binding"]["block_id"]),
        )
    ]
    if supplied_hashes != embedded_hashes:
        _reason(reasons, "current-member-comparison-evidence-mismatch")
        return False, False
    recomputed_values: list[float] = []
    for member_index, member_registration in enumerate(
        family["member_preregistrations"],
    ):
        member_comparisons, member_reasons, member_invalid = (
            _validate_bound_comparisons(
                member_registration,
                family["member_comparisons"][member_index],
            )
        )
        if member_invalid or member_reasons or not member_comparisons:
            _reason(reasons, "invalid-sibling-comparison-evidence")
            return False, False
        try:
            recomputed_values.append(complete_block_sign_permutation_p(
                [_block_tau(item) for item in member_comparisons],
                seed=member_registration["analysis_rules"]["bootstrap_seed"],
            ))
        except (ContractError, InsufficientUncertaintyError):
            _reason(reasons, "family-statistics-unusable")
            return False, False
    if recomputed_values != family["member_one_sided_p_values"]:
        _reason(reasons, "family-p-value-mismatch")
        return False, False
    adjusted = holm_adjust(recomputed_values)
    if adjusted != family["adjusted_p_values"]:
        _reason(reasons, "invalid-holm-adjustment")
        return False, False
    if adjusted[index] > family["family_alpha"]:
        _reason(reasons, "holm-adjusted-failure")
        return True, False
    return True, True


def build_claim_family(
    *,
    registrations: Sequence[dict[str, object]],
    comparisons_by_registration: Mapping[str, Sequence[dict[str, object]]],
    built_at: str,
    authority_registry: object,
) -> dict[str, object]:
    """Build a complete preregistered Holm family from recomputed block p-values.

    ``built_at`` is deliberately accepted for the public interface but is not
    added to the closed family schema.
    """
    require_timestamp(built_at, "built_at")
    if not registrations:
        raise ContractError("registrations must be non-empty")
    members = [
        authenticate_preregistration_design(
            item, authority_registry=authority_registry,
        )[0]
        for item in registrations
    ]
    ids = [item["registration_id"] for item in members]
    if len(ids) != len(set(ids)):
        raise ContractError("registrations must have unique registration IDs")
    rules = members[0]["multiplicity_rules"]
    if any(item["multiplicity_rules"] != rules for item in members):
        raise ContractError("registrations must bind one exact claim family")
    if rules["member_registration_ids"] != ids:
        raise ContractError("registrations must be supplied in preregistered family order")
    if set(comparisons_by_registration) != set(ids):
        raise ContractError("comparisons_by_registration must exactly match family members")
    comparison_hashes: list[str] = []
    p_values: list[float] = []
    for registration in members:
        comparisons, reasons, invalid = _validate_bound_comparisons(
            registration, comparisons_by_registration[registration["registration_id"]],
        )
        if invalid or reasons or not comparisons:
            raise ContractError("claim family members require valid bound comparisons")
        try:
            values = [_block_tau(item) for item in comparisons]
            seed = registration["analysis_rules"]["bootstrap_seed"]
            p_value = complete_block_sign_permutation_p(values, seed=seed)
        except (ContractError, InsufficientUncertaintyError) as exc:
            raise ContractError("claim family member has unusable complete-block statistics") from exc
        comparison_hashes.append(_comparison_set_sha256(comparisons))
        p_values.append(p_value)
    document: dict[str, object] = {
        "schema_version": CLAIM_FAMILY_VERSION,
        "family_id": rules["family_id"],
        "family_alpha": rules["family_alpha"],
        "member_registration_ids": ids,
        "member_comparison_sha256": comparison_hashes,
        "member_one_sided_p_values": p_values,
        "correction_method": rules["correction_method"],
        "adjusted_p_values": holm_adjust(p_values),
        "member_preregistrations": members,
        "member_comparisons": [
            deepcopy(list(comparisons_by_registration[registration_id]))
            for registration_id in ids
        ],
        "complete": True,
        "family_sha256": None,
    }
    document["family_sha256"] = sha256_json(document)
    return validate_claim_family(document)


def _invalid_evaluation_without_valid_comparisons(
    *,
    registration: dict[str, object],
    comparisons: Sequence[dict[str, object]],
    evaluated_at: str,
    limitations: list[str],
    claim_family: dict[str, object],
) -> dict[str, object]:
    """Return an auditable invalid result when every supplied comparison fails."""
    planned_blocks = list(registration["validation_blocks"])
    inventory: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, payload in enumerate(comparisons):
        raw_block = payload.get("block_binding")
        block_id = (
            raw_block.get("block_id")
            if isinstance(raw_block, Mapping)
            else planned_blocks[min(index, len(planned_blocks) - 1)]["block_id"]
        )
        if not isinstance(block_id, str) or block_id in seen:
            continue
        seen.add(block_id)
        supplied_hash = payload.get("comparison_sha256")
        comparison_hash = (
            supplied_hash
            if isinstance(supplied_hash, str)
            and supplied_hash.startswith("sha256:")
            and len(supplied_hash) == 71
            else sha256_json(payload)
        )
        inventory.append({
            "block_id": block_id,
            "comparison_sha256": comparison_hash,
        })
    if not inventory:
        inventory = [{
            "block_id": planned_blocks[0]["block_id"],
            "comparison_sha256": sha256_json(list(comparisons)),
        }]
    sample_rows = [{
        "block_id": block["block_id"],
        "planned_effective_sample": float(block["planned_effective_sample"]),
        "achieved_effective_sample": 0.0,
        "achieved_ratio": 0.0,
    } for block in planned_blocks]
    power = registration["study_design_power"]
    segments = [{
        "segment_id": segment["segment_id"],
        "material": True,
        "must_cover": segment["must_cover"],
        "effective_panel_weight": segment["effective_panel_weight"],
        "eligible_blocks": 0,
        "planned_blocks": len(segment["planned_block_ids"]),
        "block_coverage": 0.0,
        "creative_arms": 0,
        "tau": _interval_payload(None),
        "agreement": _interval_payload(None),
        "clear_reversal": False,
        "status": "limitations",
    } for segment in registration["segment_inventory"]
    if _is_material_segment(segment)]
    document: dict[str, object] = {
        "schema_version": EVALUATION_VERSION,
        "evaluation_id": (
            f"{registration['registration_id']}-held-out-evaluation"
        ),
        "evaluated_at": evaluated_at,
        "registration_binding": {
            "registration_id": registration["registration_id"],
            "registration_sha256": registration["registration_sha256"],
        },
        "panel_binding": deepcopy(registration["panel_binding"]),
        "claim_scope": deepcopy(registration["claim_scope"]),
        "metric_binding": deepcopy(registration["primary_metric"]),
        "block_inventory": inventory,
        "coverage": {
            "status": "incomplete",
            "block_rate": 0.0,
            "arm_rate": 0.0,
            "mapping_rate": 0.0,
        },
        "missingness": {
            "status": "none",
            "eligible_exposure_count": 0,
            "missing_outcome_count": 0,
            "rate": 0.0,
        },
        "sample_sufficiency": {
            "status": "insufficient",
            "minimum_achieved_ratio": 0.0,
            "blocks": sample_rows,
        },
        "independence": _status("dependent"),
        "leakage": _status("leaked"),
        "multiplicity": _status("incomplete"),
        "repeated_looks": _status("none"),
        "power": {
            "status": (
                "sufficient"
                if power["design_status"] == "approved"
                and power["documented_power"] >= 0.80
                else "insufficient"
            ),
            "documented_power": power["documented_power"],
            "smallest_effect_of_interest": power["smallest_effect_of_interest"],
            "method": power["method"],
            "design_status": power["design_status"],
        },
        "overall_diagnostics": {
            "status": "fail",
            "tau": _interval_payload(None),
            "agreement": _interval_payload(None),
            "determinate_pair_coverage": 0.0,
            "one_sided_p_value": 1.0,
            "holm_adjusted_p_value": 1.0,
        },
        "segment_diagnostics": segments,
        "influence_diagnostics": {
            "status": "unavailable",
            "maximum_block_contribution": 1.0,
            "leave_one_block": [],
            "leave_one_batch": [],
        },
        "preregistration": deepcopy(registration),
        "comparisons": deepcopy(list(comparisons)),
        "claim_family": deepcopy(claim_family),
        "gate_results": {"all_required_gates_passed": False},
        "decision": {"status": "invalid"},
        "limitations": limitations,
        "evaluation_sha256": None,
    }
    document["evaluation_sha256"] = sha256_json(document)
    return validate_held_out_evaluation(document)


def evaluate_held_out_ordering(
    *,
    registration: dict[str, object],
    comparisons: Sequence[dict[str, object]],
    claim_family: dict[str, object],
    evaluated_at: str,
    design_approval: object,
    authority_registry: object,
) -> dict[str, object]:
    """Apply closed C1 gates in the prescribed invalid/limited/negative order."""
    registered = validate_preregistration(registration)
    require_design_approval(design_approval, registered)
    evaluated = require_timestamp(evaluated_at, "evaluated_at")
    checked, limitations, invalid = _validate_bound_comparisons(registered, comparisons)
    try:
        family = validate_claim_family(claim_family)
        for member in family["member_preregistrations"]:
            authenticate_preregistration_design(
                member, authority_registry=authority_registry,
            )
    except ContractError:
        invalid = True
        _reason(limitations, "untrusted-or-invalid-claim-family")
    if not checked:
        if not comparisons:
            raise ContractError(
                "at least one comparison is required for a closed evaluation inventory"
            )
        return _invalid_evaluation_without_valid_comparisons(
            registration=registered,
            comparisons=comparisons,
            evaluated_at=evaluated_at,
            limitations=limitations,
            claim_family=claim_family,
        )
    if registered["status"] != "registered":
        invalid = True
        _reason(limitations, "registration-not-registered")
    if evaluated < require_timestamp(registered["registered_at"], "registration.registered_at"):
        invalid = True
        _reason(limitations, "evaluation-before-registration")
    planned_blocks = list(registered["validation_blocks"])
    planned_by_id = {block["block_id"]: block for block in planned_blocks}
    complete = checked
    block_coverage = _ratio(len(complete), len(planned_blocks))
    planned_arms = sum(len(block["planned_arm_ids"]) for block in planned_blocks)
    mapped_arm_identities = {
        (item["block_binding"]["block_id"], row["arm_id"])
        for item in complete for row in item["arm_mappings"]
    }
    mapped_arms = len(mapped_arm_identities)
    arm_coverage = _ratio(mapped_arms, planned_arms)
    study_ids = [
        str(item["block_binding"]["study_id"]) for item in complete
    ]
    block_ids = [
        str(item["block_binding"]["block_id"]) for item in complete
    ]
    studies = set(study_ids)
    contribution = _ratio(1, len(complete))
    mapping_complete = all(
        {row["arm_id"] for row in item["arm_mappings"]}
        == set(planned_by_id[item["block_binding"]["block_id"]]["planned_arm_ids"])
        for item in complete
        if item["block_binding"]["block_id"] in planned_by_id
    )
    if not mapping_complete:
        _reason(limitations, "incomplete-arm-mapping")

    total_exposures = sum(
        int(item["block_evidence"]["eligible_exposure_count"])
        for item in complete
    )
    total_missing = sum(
        int(item["block_evidence"]["missing_outcome_count"])
        for item in complete
    )
    missing_rate = _ratio(total_missing, total_exposures)
    missing_ok = missing_rate <= _MAXIMUM_MISSINGNESS
    sample_rows = [{
        "block_id": item["block_binding"]["block_id"],
        "planned_effective_sample": float(
            item["block_evidence"]["planned_effective_sample"],
        ),
        "achieved_effective_sample": float(
            item["block_evidence"]["achieved_effective_sample"],
        ),
        "achieved_ratio": (
            float(item["block_evidence"]["achieved_effective_sample"])
            / float(item["block_evidence"]["planned_effective_sample"])
        ),
    } for item in sorted(
        complete, key=lambda row: str(row["block_binding"]["block_id"]),
    )]
    minimum_sample_ratio = min(
        row["achieved_ratio"] for row in sample_rows
    )

    tau_values: list[float] = []
    agreement_values: list[float] = []
    determinate = total_pairs = 0
    stats_usable = True
    seed = registered["analysis_rules"]["bootstrap_seed"]
    resamples = registered["analysis_rules"]["bootstrap_resamples"]
    try:
        for item in complete:
            tau_values.append(_block_tau(item))
            agreement, pair_coverage = block_pairwise_agreement(item)
            agreement_values.append(agreement)
            pairs = item["pairwise_comparisons"]
            total_pairs += len(pairs)
            determinate += round(pair_coverage * len(pairs))
        tau_interval = bca_block_interval(tau_values, seed=seed, resamples=resamples)
        agreement_interval = bca_block_interval(agreement_values, seed=seed, resamples=resamples)
        p_value = complete_block_sign_permutation_p(tau_values, seed=seed)
    except (ContractError, InsufficientUncertaintyError):
        stats_usable = False
        tau_interval = agreement_interval = None
        p_value = 1.0
        _reason(limitations, "insufficient-uncertainty")
    determinate_coverage = _ratio(determinate, total_pairs)

    family_complete, holm_passes = _family_for_registration(
        registered, checked, claim_family, limitations,
    )
    holm_adjusted = 1.0
    if family_complete:
        try:
            validated_family = validate_claim_family(claim_family)
            family_index = validated_family["member_registration_ids"].index(
                registered["registration_id"],
            )
            holm_adjusted = float(
                validated_family["adjusted_p_values"][family_index],
            )
        except (ContractError, ValueError):
            family_complete = False
            holm_passes = False
    registered_minimum_blocks = max(
        _MINIMUM_BLOCKS,
        int(registered["eligibility_thresholds"]["minimum_blocks"]),
    )
    registered_minimum_coverage = max(
        _MINIMUM_BLOCK_COVERAGE,
        float(registered["eligibility_thresholds"]["minimum_coverage"]),
    )
    coverage_ok = (
        mapping_complete
        and block_coverage >= registered_minimum_coverage
        and arm_coverage >= _MINIMUM_ARM_COVERAGE
    )
    equal_weighting = registered["analysis_rules"]["block_weighting"] == "equal"
    sample_ok = (
        len(complete) >= registered_minimum_blocks
        and all(len(item["arm_mappings"]) >= _MINIMUM_CREATIVES_PER_BLOCK for item in complete)
        and mapped_arms >= _MINIMUM_ARMS
        and len(studies) >= _MINIMUM_BATCHES
        and all(
            row["achieved_ratio"] >= _MINIMUM_PLANNED_SAMPLE
            for row in sample_rows
        )
    )
    independent = (
        len(complete) >= registered_minimum_blocks
        and len({
            item["block_binding"]["block_id"] for item in complete
        }) == len(complete)
    )
    influence_diagnostics = (
        _influence_diagnostics(
            block_ids=block_ids,
            tau_values=tau_values,
            agreement_values=agreement_values,
            study_ids=study_ids,
            family_alpha=float(
                registered["multiplicity_rules"]["family_alpha"]
            ),
            seed=int(seed),
        )
        if stats_usable
        else {
            "status": "unavailable",
            "maximum_block_contribution": contribution,
            "leave_one_block": [],
            "leave_one_batch": [],
        }
    )
    power_evidence = registered["study_design_power"]
    power_ok = (
        power_evidence["design_status"] == "approved"
        and float(power_evidence["documented_power"]) >= _MINIMUM_POWER
    )
    uncertainty_ok = (
        stats_usable and tau_interval is not None and agreement_interval is not None
        and registered["analysis_rules"]["bootstrap_resamples"] == 20_000
    )
    statistical_pass = (
        uncertainty_ok
        and tau_interval.point >= _MINIMUM_TAU
        and tau_interval.one_sided_lower > 0
        and agreement_interval.point >= _MINIMUM_AGREEMENT
        and agreement_interval.one_sided_lower > 0.50
        and holm_passes
    )
    segment_diagnostics: list[dict[str, object]] = []
    any_segment_sparse = False
    any_segment_reversal = False
    any_segment_weak = False
    for segment in registered["segment_inventory"]:
        material = _is_material_segment(segment)
        if not material:
            continue
        planned_segment_blocks = set(segment["planned_block_ids"])
        segment_rows: list[tuple[dict[str, object], dict[str, object]]] = []
        for item in complete:
            if item["block_binding"]["block_id"] not in planned_segment_blocks:
                continue
            row = next((
                evidence for evidence in item["segment_evidence"]
                if evidence["segment_id"] == segment["segment_id"]
            ), None)
            if row is not None and len(row["arm_ids"]) >= 2:
                segment_rows.append((item, row))
        segment_blocks = len(segment_rows)
        segment_arms = sum(len(row["arm_ids"]) for _, row in segment_rows)
        segment_coverage = _ratio(
            segment_blocks, len(planned_segment_blocks),
        )
        segment_tau_interval = segment_agreement_interval = None
        try:
            segment_taus = [
                _block_tau({
                    "synthetic_ordering": row["synthetic_ordering"],
                    "observed_ordering": row["observed_ordering"],
                })
                for _, row in segment_rows
            ]
            segment_tau_interval = bca_block_interval(
                segment_taus, seed=seed, resamples=resamples,
            )
        except (ContractError, InsufficientUncertaintyError):
            pass
        try:
            segment_agreements = [
                block_pairwise_agreement({
                    "pairwise_comparisons": row["pairwise_comparisons"],
                })[0]
                for _, row in segment_rows
            ]
            segment_agreement_interval = bca_block_interval(
                segment_agreements, seed=seed, resamples=resamples,
            )
        except (ContractError, InsufficientUncertaintyError):
            pass
        sparse, reversal, passes = _material_segment_gate(
            eligible_blocks=segment_blocks,
            creative_arms=segment_arms,
            block_coverage=segment_coverage,
            tau_interval=segment_tau_interval,
            agreement_interval=segment_agreement_interval,
        )
        any_segment_sparse = any_segment_sparse or sparse
        any_segment_reversal = any_segment_reversal or reversal
        any_segment_weak = any_segment_weak or (not sparse and not passes)
        segment_diagnostics.append({
            "segment_id": segment["segment_id"],
            "material": True,
            "must_cover": segment["must_cover"],
            "effective_panel_weight": segment["effective_panel_weight"],
            "eligible_blocks": segment_blocks,
            "planned_blocks": len(planned_segment_blocks),
            "block_coverage": segment_coverage,
            "creative_arms": segment_arms,
            "tau": _interval_payload(segment_tau_interval),
            "agreement": _interval_payload(segment_agreement_interval),
            "clear_reversal": reversal,
            "status": (
                "fail" if reversal
                else "limitations" if sparse
                else "pass" if passes
                else "fail"
            ),
        })

    if not independent:
        _reason(limitations, "minimum-independent-blocks")
    if len(studies) < _MINIMUM_BATCHES:
        _reason(limitations, "minimum-independent-batches")
    if not equal_weighting:
        _reason(limitations, "equal-block-weighting-required")
    if not coverage_ok:
        _reason(limitations, "coverage-threshold")
    if not missing_ok:
        _reason(limitations, "missingness-threshold")
    if determinate_coverage < _MINIMUM_DETERMINATE_PAIRS:
        _reason(limitations, "determinate-pair-coverage")
    if not sample_ok:
        _reason(limitations, "sample-sufficiency")
    if not power_ok:
        _reason(limitations, "power-threshold")
    if registered["analysis_rules"]["bootstrap_resamples"] != 20_000:
        _reason(limitations, "bootstrap-resamples-must-equal-20000")
    if not family_complete:
        _reason(limitations, "incomplete-claim-family")
    if not holm_passes:
        _reason(limitations, "holm-adjusted-failure")
    if any_segment_sparse:
        _reason(limitations, "material-segment-sparse")
    if any_segment_reversal:
        _reason(limitations, "material-segment-reversal")
    if any_segment_weak and not any_segment_reversal:
        _reason(limitations, "material-segment-gate-failure")

    limited = not all((coverage_ok, missing_ok, sample_ok, independent, equal_weighting, power_ok, uncertainty_ok, family_complete, determinate_coverage >= _MINIMUM_DETERMINATE_PAIRS, not any_segment_sparse))
    negative = not statistical_pass or any_segment_reversal or any_segment_weak
    if invalid:
        decision = "invalid"
    elif any_segment_reversal:
        decision = "tier4_not_supported"
    elif limited:
        decision = "evaluated_with_limitations"
    elif negative:
        decision = "tier4_not_supported"
    else:
        decision = "tier4_supported"
    document: dict[str, object] = {
        "schema_version": EVALUATION_VERSION,
        "evaluation_id": f"{registered['registration_id']}-held-out-evaluation",
        "evaluated_at": evaluated_at,
        "registration_binding": {"registration_id": registered["registration_id"], "registration_sha256": registered["registration_sha256"]},
        "panel_binding": deepcopy(registered["panel_binding"]),
        "claim_scope": deepcopy(registered["claim_scope"]),
        "metric_binding": deepcopy(registered["primary_metric"]),
        "block_inventory": [{"block_id": item["block_binding"]["block_id"], "comparison_sha256": item["comparison_sha256"]} for item in sorted(checked, key=lambda item: str(item["block_binding"]["block_id"]))],
        "coverage": {
            "status": "complete" if coverage_ok else "incomplete",
            "block_rate": block_coverage,
            "arm_rate": arm_coverage,
            "mapping_rate": _ratio(
                sum(item["mapping_coverage"]["mapped_arms"] for item in complete),
                sum(item["mapping_coverage"]["expected_arms"] for item in complete),
            ),
        },
        "missingness": {
            "status": (
                "none" if total_missing == 0
                else "within_threshold" if missing_ok
                else "excessive"
            ),
            "eligible_exposure_count": total_exposures,
            "missing_outcome_count": total_missing,
            "rate": missing_rate,
        },
        "sample_sufficiency": {
            "status": "sufficient" if sample_ok else "insufficient",
            "minimum_achieved_ratio": minimum_sample_ratio,
            "blocks": sample_rows,
        },
        "independence": _status("independent" if independent else "dependent"),
        "leakage": _status("clear" if not invalid else "leaked"),
        "multiplicity": _status("complete" if family_complete else "incomplete"),
        "repeated_looks": _status("none"),
        "power": {
            "status": "sufficient" if power_ok else "insufficient",
            "documented_power": power_evidence["documented_power"],
            "smallest_effect_of_interest": power_evidence["smallest_effect_of_interest"],
            "method": power_evidence["method"],
            "design_status": power_evidence["design_status"],
        },
        "overall_diagnostics": {
            "status": "pass" if statistical_pass else "fail",
            "tau": _interval_payload(tau_interval),
            "agreement": _interval_payload(agreement_interval),
            "determinate_pair_coverage": determinate_coverage,
            "one_sided_p_value": p_value,
            "holm_adjusted_p_value": holm_adjusted,
        },
        "segment_diagnostics": segment_diagnostics,
        "influence_diagnostics": influence_diagnostics,
        "preregistration": deepcopy(registered),
        "comparisons": deepcopy(checked),
        "claim_family": deepcopy(claim_family),
        "gate_results": {"all_required_gates_passed": decision == "tier4_supported"},
        # Contract v1 closes decision to status only.  Reason codes therefore
        # live in limitations rather than reopening the approved schema.
        "decision": {"status": decision},
        "limitations": limitations,
        "evaluation_sha256": None,
    }
    document["evaluation_sha256"] = sha256_json(document)
    return validate_held_out_evaluation(document)


def issue_tier4_claim(
    *, evaluation: dict[str, object], issued_at: str, expires_at: str,
    design_approval: object, authority_registry: object,
) -> dict[str, object]:
    """Issue the only permitted active claim: a narrow supported-use statement."""
    checked = validate_held_out_evaluation(evaluation)
    require_design_approval(design_approval, checked["preregistration"])
    replayed = evaluate_held_out_ordering(
        registration=checked["preregistration"],
        comparisons=checked["comparisons"],
        claim_family=checked["claim_family"],
        evaluated_at=checked["evaluated_at"],
        design_approval=design_approval,
        authority_registry=authority_registry,
    )
    if replayed != checked:
        raise ContractError(
            "evaluation must exactly match recomputed preregistration, "
            "comparison, family, Holm, block, and segment evidence"
        )
    if checked["decision"]["status"] != "tier4_supported":
        raise ContractError("only tier4_supported evaluations may issue a Tier 4 claim")
    if require_timestamp(issued_at, "issued_at") < require_timestamp(checked["evaluated_at"], "evaluation.evaluated_at"):
        raise ContractError("issued_at must not precede evaluation.evaluated_at")
    document: dict[str, object] = {
        "schema_version": TIER4_CLAIM_VERSION,
        "claim_id": f"{checked['evaluation_id']}-claim",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "status": "active",
        "panel_binding": deepcopy(checked["panel_binding"]),
        "registration_binding": deepcopy(checked["registration_binding"]),
        "evaluation_binding": {"evaluation_id": checked["evaluation_id"], "evaluation_sha256": checked["evaluation_sha256"]},
        "claim_scope": deepcopy(checked["claim_scope"]),
        "claim_text": TIER4_CLAIM_TEXT,
        "required_disclaimer": TIER4_REQUIRED_DISCLAIMER,
        "diagnostic_summary": {"status": "tier4_supported"},
        "limitations": deepcopy(checked["limitations"]),
        "refresh_triggers": deepcopy(TIER4_REFRESH_TRIGGERS),
        "claim_sha256": None,
    }
    document["claim_sha256"] = sha256_json(document)
    return validate_tier4_claim(document)
