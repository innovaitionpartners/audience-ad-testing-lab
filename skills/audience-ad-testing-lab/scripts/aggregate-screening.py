#!/usr/bin/env python3
"""Aggregate method-aware screening, boundary, and finalist response records."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from audience_lab.complete_exposure import (
    CALIBRATION_POLICY_VERSION,
    PROFILE_STRATIFIED_POLICY_VERSION,
    aggregate_complete_exposure,
)
from audience_lab.boundary_aggregation import canonical_boundary_result
from audience_lab.contracts import (
    load_json,
    validate_boundary_profile_attachments,
    validate_manifest,
)
from audience_lab.finalists import aggregate_finalists
from audience_lab.maxdiff import MaxDiffConfig, screen_shortlist
from audience_lab.pairwise import PairwiseConfig, resolve_boundary
from audience_lab.responses import (
    validate_dispatch_audit_job_bindings,
    validate_job,
    validate_response,
    validate_response_job_bindings,
)


INVALID_EXIT = 4


def _complete_profile_contract(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, int]] | None:
    capacity = manifest.get("dynamic_complete_exposure_capacity")
    if not isinstance(capacity, Mapping):
        return None
    rows = capacity.get("core_allocation_by_profile")
    if not isinstance(rows, list) or not rows:
        raise ValueError("dynamic capacity must contain per-profile core allocations")
    weights: dict[str, float] = {}
    floors: dict[str, int] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"dynamic profile allocation[{index}] must be an object")
        profile_id = row.get("grounded_profile_id")
        weight = row.get("target_within_segment_weight")
        floor = row.get("minimum_usable_records")
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("dynamic profile allocation IDs must be non-empty strings")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError("dynamic within-segment profile weights must be numeric")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
            raise ValueError("dynamic per-profile usable floors must be positive integers")
        weights[profile_id] = float(weight)
        floors[profile_id] = floor
    return weights, floors


def _partial_profile_contract(
    manifest: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
    creative_ids: Sequence[str],
) -> tuple[dict[str, float], dict[str, dict[str, int]]] | None:
    """Return frozen v3 profile weights and planned creative participation.

    Older partial-exposure manifests have no profile-weight envelope and retain
    their segment-stratified interpretation.  A v3 manifest must bind every
    profile weight and every planned block through the frozen jobs envelope so
    aggregation can enforce profile-conditioned coverage without trusting a
    caller-supplied count summary.
    """

    profile_rosters = manifest.get("audience_profile_rosters")
    if profile_rosters is None:
        return None
    screening_roster = (
        profile_rosters.get("screening")
        if isinstance(profile_rosters, Mapping)
        else None
    )
    raw_weights = (
        screening_roster.get("profile_diagnostics")
        if isinstance(screening_roster, Mapping)
        else None
    )
    if not isinstance(raw_weights, list) or not raw_weights:
        raise ValueError("v3 screening profile diagnostics must be a non-empty array")

    profile_weights: dict[str, float] = {}
    for index, row in enumerate(raw_weights):
        if not isinstance(row, Mapping):
            raise ValueError(f"profile_weights[{index}] must be an object")
        profile_id = row.get("grounded_profile_id")
        weight = row.get("target_weight")
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("profile weights require grounded_profile_id")
        if profile_id in profile_weights:
            raise ValueError("profile weights must have unique grounded profile IDs")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError("profile target weights must be positive numbers")
        profile_weights[profile_id] = float(weight)

    if not isinstance(jobs_payload, Mapping):
        raise ValueError("profile-aware partial exposure requires the frozen jobs envelope")
    raw_jobs = jobs_payload.get("synthetic_replicate_jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError("profile-aware partial exposure requires frozen screening jobs")
    roster = tuple(sorted(creative_ids))
    planned = {
        profile_id: {creative_id: 0 for creative_id in roster}
        for profile_id in sorted(profile_weights)
    }
    for index, job in enumerate(raw_jobs):
        if not isinstance(job, Mapping):
            raise ValueError(f"screening job[{index}] must be an object")
        profile_id = job.get("grounded_profile_id")
        variation_ids = job.get("variation_ids")
        if profile_id not in planned:
            raise ValueError(
                f"screening job[{index}] must bind a frozen grounded profile"
            )
        if (
            not isinstance(variation_ids, list)
            or len(variation_ids) != 4
            or len(set(variation_ids)) != 4
            or not set(variation_ids) <= set(roster)
        ):
            raise ValueError(f"screening job[{index}] must bind four roster creatives")
        for creative_id in variation_ids:
            planned[str(profile_id)][str(creative_id)] += 1
    return profile_weights, planned


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    screening = commands.add_parser("screening", help="aggregate the method-specific first round")
    screening.add_argument("--manifest", required=True, type=Path)
    screening.add_argument("--jobs", type=Path)
    screening.add_argument("--responses", required=True, type=Path)
    screening.add_argument("--dispatch-audit", type=Path)
    screening.add_argument("--recovery-config", type=Path)
    screening.add_argument("--output", required=True, type=Path)
    boundary = commands.add_parser(
        "boundary", help="resolve a frozen shortlist boundary with Davidson choices"
    )
    boundary.add_argument("--manifest", required=True, type=Path)
    boundary.add_argument("--screening-results", required=True, type=Path)
    boundary.add_argument("--responses", required=True, type=Path)
    boundary.add_argument("--output", required=True, type=Path)
    finalists = commands.add_parser(
        "finalists", help="aggregate approved complete-set finalist responses"
    )
    finalists.add_argument("--manifest", required=True, type=Path)
    finalists.add_argument("--screening-results", required=True, type=Path)
    finalists.add_argument("--boundary-results", type=Path)
    finalists.add_argument("--approval", required=True, type=Path)
    finalists.add_argument("--jobs", type=Path)
    finalists.add_argument("--responses", required=True, type=Path)
    finalists.add_argument("--output", required=True, type=Path)
    return parser


def _read_jsonl(path: Path, record_label: str = "responses") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{record_label} line {line_number} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"{record_label} line {line_number} must contain a JSON object"
            )
        records.append(payload)
    if not records:
        raise ValueError(f"{record_label} file contains no records")
    return records


def _deterministic_seed(manifest: Mapping[str, Any]) -> int:
    assignment = manifest.get("assignment")
    assignment_seed = (
        assignment.get("randomization_seed") if isinstance(assignment, Mapping) else ""
    )
    material = f"{manifest.get('study_id', '')}|{assignment_seed}|screening-bootstrap-v1"
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def _boundary_deterministic_seed(manifest: Mapping[str, Any]) -> int:
    assignment = manifest.get("assignment")
    assignment_seed = (
        assignment.get("randomization_seed") if isinstance(assignment, Mapping) else ""
    )
    material = f"{manifest.get('study_id', '')}|{assignment_seed}|boundary-bootstrap-v1"
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def _invalid_payload(
    *,
    study_id: str | None,
    reasons: list[str],
    errors: list[str],
    requested_top_k: int | None = None,
    recovery_config_version: str | None = None,
) -> dict[str, Any]:
    return {
        "study_id": study_id,
        "estimand": "centered_protocol_relative_log_utility",
        "stability_diagnostic": "conditional_within_run_top_k_inclusion_frequency",
        "requested_top_k": requested_top_k,
        "utilities": {},
        "ranked_ids": [],
        "top_k_inclusion_frequencies": {},
        "classifications": {},
        "selection_status": "invalid",
        "proposed_finalist_ids": [],
        "archetype_sensitivity": {
            "method": "leave_one_persona_archetype_out",
            "unique_archetypes": 0,
            "attempted_fits": 0,
            "successful_fits": 0,
            "successful_fit_rate": 0.0,
            "top_k_consistent": False,
            "top_k_changed_for": [],
            "results": [],
        },
        "model_diagnostics": {"input_errors": errors},
        "recovery_config_version": recovery_config_version,
        "validity_status": "invalid",
        "validity_reasons": list(dict.fromkeys(reasons)),
        "interpretation_limits": [
            "No global utility is reported for invalid input or an unidentified model.",
            "Results do not establish human-response or campaign-performance validity.",
        ],
    }


def _invalid_boundary_payload(
    *,
    study_id: str | None,
    reasons: list[str],
    errors: list[str],
    candidate_ids: list[str] | None = None,
    clear_finalist_ids: list[str] | None = None,
    clear_non_finalist_ids: list[str] | None = None,
    manifest: Mapping[str, Any] | None = None,
    screening: Mapping[str, Any] | None = None,
    realized_boundary_calls: int = 0,
) -> dict[str, Any]:
    candidates = sorted(candidate_ids or [])
    clear_finalists = sorted(clear_finalist_ids or [])
    clear_non_finalists = sorted(clear_non_finalist_ids or [])
    capacity = manifest.get("synthetic_replicate_capacity") if manifest else None
    boundary_plan = screening.get("boundary_plan") if screening else None
    boundary_reserved = (
        capacity.get("boundary_reserved") if isinstance(capacity, Mapping) else None
    )
    finalist_reserved = (
        capacity.get("finalist_reserved") if isinstance(capacity, Mapping) else None
    )
    available_boundary_reserve = (
        boundary_plan.get("available_boundary_reserve")
        if isinstance(boundary_plan, Mapping)
        else None
    )
    if (
        isinstance(available_boundary_reserve, int)
        and not isinstance(available_boundary_reserve, bool)
        and available_boundary_reserve >= 0
    ):
        boundary_jobs_remaining: int | None = max(
            available_boundary_reserve - realized_boundary_calls, 0
        )
        boundary_jobs_over_reserve: int | None = max(
            realized_boundary_calls - available_boundary_reserve, 0
        )
    else:
        boundary_jobs_remaining = None
        boundary_jobs_over_reserve = None
    return {
        "study_id": study_id,
        "status": "invalid",
        "status_reasons": list(dict.fromkeys(reasons)),
        "estimand": "centered_pairwise_davidson_log_utility",
        "stability_diagnostic": (
            "conditional_within_run_boundary_slot_inclusion_frequency"
        ),
        "boundary_candidate_ids": candidates,
        "frozen_clear_finalist_ids": clear_finalists,
        "frozen_clear_non_finalist_ids": clear_non_finalists,
        "selected_boundary_ids": [],
        "proposed_finalist_ids": clear_finalists,
        "utilities": {},
        "ranked_ids": [],
        "conditional_inclusion_frequencies": {},
        "classifications": {
            candidate_id: "unresolved" for candidate_id in candidates
        },
        "model_diagnostics": {"input_errors": errors},
        "decision_audit": {
            "policy_version": "connected-davidson-boundary-v1",
            "model_scope": "frozen_boundary_candidates_only",
            "maxdiff_utilities_pooled": False,
            "clear_groups_frozen": True,
            "frozen_clear_finalist_ids": clear_finalists,
            "frozen_clear_non_finalist_ids": clear_non_finalists,
            "boundary_candidate_ids": candidates,
            "remaining_finalist_slots": None,
            "inclusion_policy": {
                "clear_finalist": ">=0.90",
                "clear_non_finalist": "<=0.10",
                "boundary_candidate": "strictly_between_0.10_and_0.90",
                "cutoff_tie_policy": "symmetric_fractional_inclusion",
                "cutoff_tie_tolerance": "max(optimizer_tolerance,1e-12)",
            },
            "predeclaration": {
                "source": "unavailable_or_invalid",
                "boundary_jobs_per_wave": None,
                "boundary_waves_max": None,
            },
            "reserve": {
                "boundary_reserved": boundary_reserved,
                "available_boundary_reserve": available_boundary_reserve,
                "boundary_jobs_observed": realized_boundary_calls,
                "boundary_jobs_consumed": realized_boundary_calls,
                "boundary_jobs_remaining": boundary_jobs_remaining,
                "boundary_jobs_over_reserve": boundary_jobs_over_reserve,
                "finalist_reserved_before": finalist_reserved,
                "finalist_reserved_after": finalist_reserved,
                "finalist_reserve_consumed": 0,
            },
            "waves": [],
            "selection_decisions": [],
            "next_wave_job_ids": [],
            "stopping_decision": {
                "reason": "invalid_boundary_input",
                "wave": None,
                "resolved": False,
            },
        },
        "interpretation_limits": [
            "No pairwise utility is reported for invalid input.",
            "Pairwise and MaxDiff utilities are never pooled or assumed to share a scale.",
            "Results do not establish human-response or campaign-performance validity.",
        ],
    }


def _write_output(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _preserve_audience_authority(
    payload: dict[str, Any], manifest: Mapping[str, Any] | None
) -> None:
    """Carry an exact v2 audience binding into downstream dispatch authorities."""

    if not isinstance(manifest, Mapping):
        return
    audience_package = manifest.get("audience_package")
    audience_lock = manifest.get("audience_lock")
    if isinstance(audience_package, Mapping) and isinstance(audience_lock, Mapping):
        payload["audience_package"] = dict(audience_package)
        payload["audience_lock"] = dict(audience_lock)


def _manifest_creative_roster(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("manifest.outputs must be an object")
    creative_asset_hashes = outputs.get("creative_asset_hashes")
    if not isinstance(creative_asset_hashes, Mapping) or not creative_asset_hashes:
        raise ValueError(
            "manifest.outputs.creative_asset_hashes must lock a non-empty creative roster"
        )
    creative_ids = tuple(creative_asset_hashes)
    if not all(isinstance(item, str) and item.strip() for item in creative_ids):
        raise ValueError(
            "manifest.outputs.creative_asset_hashes keys must be non-empty creative IDs"
        )
    return tuple(sorted(creative_ids))


def _manifest_context_stratum_keys(
    manifest: Mapping[str, Any],
) -> set[tuple[str, str]] | None:
    assignment = manifest.get("assignment")
    raw_strata = (
        assignment.get("context_strata") if isinstance(assignment, Mapping) else None
    )
    if raw_strata is None:
        return None
    if not isinstance(raw_strata, list) or not raw_strata:
        raise ValueError(
            "manifest.assignment.context_strata must be a non-empty array when supplied"
        )
    keys: set[tuple[str, str]] = set()
    for index, stratum in enumerate(raw_strata):
        if not isinstance(stratum, Mapping):
            raise ValueError(f"manifest assignment context_strata[{index}] is invalid")
        segment_id = stratum.get("segment_id")
        context_stratum_id = stratum.get("context_stratum_id")
        if (
            not isinstance(segment_id, str)
            or not segment_id.strip()
            or not isinstance(context_stratum_id, str)
            or not context_stratum_id.strip()
        ):
            raise ValueError(
                f"manifest assignment context_strata[{index}] requires stable IDs"
            )
        key = (segment_id, context_stratum_id)
        if key in keys:
            raise ValueError("manifest assignment context stratum IDs must be unique")
        keys.add(key)
    return keys


def _input_violations(
    manifest: Mapping[str, Any], records: list[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    errors: list[str] = []
    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        reasons.append("manifest_contract_invalid")
        errors.extend(f"manifest: {error}" for error in manifest_errors)

    roster: set[str] | None
    try:
        roster = set(_manifest_creative_roster(manifest))
    except ValueError as exc:
        roster = None
        if "manifest_contract_invalid" not in reasons:
            reasons.append("manifest_contract_invalid")
        errors.append(f"manifest: {exc}")

    expected_study_id = manifest.get("study_id")
    for index, record in enumerate(records):
        record_errors = validate_response(record)
        if record_errors:
            if "response_contract_invalid" not in reasons:
                reasons.append("response_contract_invalid")
            errors.extend(f"response[{index}]: {error}" for error in record_errors)
        if record.get("record_type") != "screening_response":
            if "non_screening_record_present" not in reasons:
                reasons.append("non_screening_record_present")
            errors.append(f"response[{index}].record_type must be screening_response")
        if record.get("study_id") != expected_study_id:
            if "response_study_id_mismatch" not in reasons:
                reasons.append("response_study_id_mismatch")
            errors.append(
                f"response[{index}].study_id does not match manifest.study_id"
            )
        assigned_ids = record.get("assigned_variation_ids")
        if roster is not None and isinstance(assigned_ids, list):
            outside_roster = sorted(
                {
                    creative_id
                    for creative_id in assigned_ids
                    if isinstance(creative_id, str) and creative_id not in roster
                }
            )
            if outside_roster:
                if "response_creative_out_of_roster" not in reasons:
                    reasons.append("response_creative_out_of_roster")
                errors.append(
                    f"response[{index}].assigned_variation_ids outside manifest roster: "
                    + ",".join(outside_roster)
                )

    for field, reason in (
        ("response_id", "duplicate_response_id"),
        ("synthetic_replicate_id", "duplicate_synthetic_replicate_id"),
    ):
        values = [record.get(field) for record in records if isinstance(record.get(field), str)]
        duplicates = sorted(
            str(value) for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            reasons.append(reason)
            errors.append(f"duplicate {field} values: {','.join(duplicates)}")

    audience_lock = manifest.get("audience_lock")
    segment_weights = (
        audience_lock.get("segment_weights") if isinstance(audience_lock, Mapping) else None
    )
    response_segments = {
        record.get("segment_id")
        for record in records
        if isinstance(record.get("segment_id"), str)
    }
    segment_lock_matches = isinstance(segment_weights, Mapping) and (
        response_segments <= set(segment_weights)
        if manifest.get("collection_open") is True
        else response_segments == set(segment_weights)
    )
    if not segment_lock_matches:
        reasons.append("response_segment_lock_mismatch")
        errors.append(
            "manifest audience-lock segment IDs must cover accepted response segment IDs; "
            "closed runs require an exact match"
        )
    return reasons, errors


def _partial_binding_violations(
    manifest: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]],
    dispatch_audit: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    """Bind partial-exposure observations to one frozen screening plan."""

    reasons: list[str] = []
    errors: list[str] = []
    if not isinstance(jobs_payload, Mapping):
        return ["planned_jobs_missing"], [
            "partial_exposure_maxdiff requires the frozen --jobs envelope"
        ]
    for field, expected in (
        ("study_id", manifest.get("study_id")),
        ("method", "partial_exposure_maxdiff"),
        ("record_type", "screening_response"),
    ):
        if jobs_payload.get(field) != expected:
            reasons.append("planned_jobs_envelope_mismatch")
            errors.append(f"jobs.{field} must equal {expected!r}")
    raw_jobs = jobs_payload.get("synthetic_replicate_jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        return [*reasons, "planned_jobs_missing"], [
            *errors,
            "jobs.synthetic_replicate_jobs must be a non-empty array",
        ]

    roster = set(_manifest_creative_roster(manifest))
    audience_lock = manifest.get("audience_lock")
    segment_weights = (
        audience_lock.get("segment_weights")
        if isinstance(audience_lock, Mapping)
        else None
    )
    locked_segments = set(segment_weights) if isinstance(segment_weights, Mapping) else set()
    context_keys = _manifest_context_stratum_keys(manifest)
    for index, job in enumerate(raw_jobs):
        if not isinstance(job, Mapping):
            reasons.append("planned_job_contract_invalid")
            errors.append(f"job[{index}] must be an object")
            continue
        job_errors = validate_job(job)
        if job_errors:
            reasons.append("planned_job_contract_invalid")
            errors.extend(f"job[{index}]: {error}" for error in job_errors)
        if job.get("study_id") != manifest.get("study_id"):
            reasons.append("planned_job_manifest_mismatch")
            errors.append(f"job[{index}].study_id must match the manifest")
        if job.get("method") != "partial_exposure_maxdiff":
            reasons.append("planned_job_manifest_mismatch")
            errors.append(f"job[{index}].method must be partial_exposure_maxdiff")
        if job.get("record_type") != "screening_response":
            reasons.append("planned_job_stage_mismatch")
            errors.append(f"job[{index}].record_type must be screening_response")
        variation_ids = job.get("variation_ids")
        if (
            not isinstance(variation_ids, list)
            or len(variation_ids) != 4
            or len(set(variation_ids)) != 4
            or not set(variation_ids) <= roster
        ):
            reasons.append("planned_job_roster_mismatch")
            errors.append(
                f"job[{index}].variation_ids must contain four unique manifest creatives"
            )
        if job.get("segment_id") not in locked_segments:
            reasons.append("planned_job_segment_mismatch")
            errors.append(f"job[{index}].segment_id must be a locked manifest segment")
        if context_keys is not None and (
            job.get("segment_id"), job.get("context_stratum_id")
        ) not in context_keys:
            reasons.append("planned_job_context_stratum_mismatch")
            errors.append(
                f"job[{index}] must match a manifest-locked segment/context stratum"
            )

    assignment = manifest.get("assignment")
    if not isinstance(assignment, Mapping) or assignment.get("block_size") != 4:
        reasons.append("partial_assignment_contract_invalid")
        errors.append("manifest.assignment.block_size must equal 4")
    capacity = manifest.get("synthetic_replicate_capacity")
    screening_planned = (
        capacity.get("screening_planned") if isinstance(capacity, Mapping) else None
    )
    if screening_planned != len(raw_jobs):
        reasons.append("screening_reserve_job_mismatch")
        errors.append(
            "manifest screening_planned must exactly equal the frozen screening job count"
        )
    if isinstance(capacity, Mapping):
        boundary_reserved = capacity.get("boundary_reserved")
        finalist_reserved = capacity.get("finalist_reserved")
        ceiling = manifest.get("maximum_synthetic_panelists")
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (screening_planned, boundary_reserved, finalist_reserved)
            )
            or isinstance(ceiling, bool)
            or not isinstance(ceiling, int)
            or screening_planned + boundary_reserved + finalist_reserved > ceiling
            or capacity.get("ceiling_satisfied") is not True
        ):
            reasons.append("binding_ceiling_not_satisfied")
            errors.append(
                "screening, boundary, and finalist reserves must fit the binding ceiling"
            )

    binding_errors = validate_response_job_bindings(
        raw_jobs,
        records,
        require_exact_set=False,
    )
    if binding_errors:
        reasons.append("response_planned_job_mismatch")
        errors.extend(f"exact job binding: {error}" for error in binding_errors)

    planned_replicates = {
        str(job.get("synthetic_replicate_id"))
        for job in raw_jobs
        if isinstance(job, Mapping)
        and isinstance(job.get("synthetic_replicate_id"), str)
    }
    accepted_replicates = {
        str(record.get("synthetic_replicate_id"))
        for record in records
        if isinstance(record.get("synthetic_replicate_id"), str)
    }
    missing = planned_replicates - accepted_replicates
    if missing and dispatch_audit is None:
        reasons.append("dispatch_audit_missing_for_incomplete_collection")
        errors.append(
            "authorized incomplete partial exposure requires --dispatch-audit"
        )
    if dispatch_audit is not None:
        runtime = manifest.get("runtime")
        retry_limit = (
            runtime.get("retry_limit_per_return")
            if isinstance(runtime, Mapping)
            else None
        )
        audit_errors = validate_dispatch_audit_job_bindings(
            raw_jobs,
            records,
            dispatch_audit,
            retry_limit_per_return=retry_limit,
        )
        if audit_errors:
            reasons.append("dispatch_audit_job_binding_mismatch")
            errors.extend(f"dispatch audit binding: {error}" for error in audit_errors)
    return list(dict.fromkeys(reasons)), errors


_COMPLETE_POLICY = {
    "version": CALIBRATION_POLICY_VERSION,
    "scope": "conditional_synthetic_run_only",
    "planned_jobs_per_segment": 9,
    "minimum_usable_records_per_segment": 8,
    "bootstrap_resamples": 2000,
    "finalist_inclusion_threshold": 0.90,
    "nonfinalist_inclusion_threshold": 0.10,
    "cutoff_tie_policy": "no_point_estimate_only_decision",
    "archetype_sensitivity": "leave_one_persona_archetype_out_top_k_consistent",
    "minimum_archetype_diversity": 2,
    "minimum_evaluable_archetype_exclusions": 2,
    "calibration_basis": "deterministic_task9_adversarial_recovery_fixtures",
    "human_market_calibration": False,
}

_PROFILE_STRATIFIED_COMPLETE_POLICY = {
    "version": PROFILE_STRATIFIED_POLICY_VERSION,
    "scope": "conditional_synthetic_run_only",
    "capacity_policy_version": "dynamic-complete-exposure-experimental-v1",
    "usable_floor_source": "manifest_dynamic_capacity_by_profile",
    "weighting": "frozen_profile_then_segment",
    "bootstrap_resamples": 2000,
    "finalist_inclusion_threshold": 0.90,
    "nonfinalist_inclusion_threshold": 0.10,
    "cutoff_tie_policy": "no_point_estimate_only_decision",
    "archetype_sensitivity": "leave_one_persona_archetype_out_top_k_consistent",
    "grounded_profile_sensitivity": "leave_one_grounded_profile_out_top_k_consistent",
    "minimum_archetype_diversity": 2,
    "minimum_evaluable_archetype_exclusions": 2,
    "minimum_grounded_profile_diversity": 2,
    "minimum_evaluable_grounded_profile_exclusions": 2,
    "calibration_basis": "profile_balanced_24_30_36_experimental_pending",
    "human_market_calibration": False,
}


def _complete_binding_violations(
    manifest: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]],
    policy: Mapping[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Bind complete-set responses to the frozen plan and calibrated policy."""

    reasons: list[str] = []
    errors: list[str] = []
    if policy is None:
        return ["complete_calibration_policy_missing"], [
            "complete_exposure requires a versioned --recovery-config"
        ]
    expected_policy = (
        _PROFILE_STRATIFIED_COMPLETE_POLICY
        if policy.get("version") == PROFILE_STRATIFIED_POLICY_VERSION
        else _COMPLETE_POLICY
    )
    if set(policy) != set(expected_policy):
        reasons.append("complete_calibration_policy_mismatch")
        errors.append("complete calibration policy keys must match the canonical allowlist")
    for field, expected in expected_policy.items():
        if policy.get(field) != expected:
            reasons.append("complete_calibration_policy_mismatch")
            errors.append(
                f"complete calibration {field} must equal {expected!r}"
            )
    model = manifest.get("model")
    bound_version = (
        model.get("complete_exposure_calibration_version")
        if isinstance(model, Mapping)
        else None
    )
    if bound_version != policy.get("version"):
        reasons.append("complete_calibration_manifest_binding_mismatch")
        errors.append(
            "manifest.model.complete_exposure_calibration_version must match the supplied policy"
        )

    if not isinstance(jobs_payload, Mapping):
        reasons.append("planned_jobs_missing")
        errors.append("complete_exposure requires the frozen --jobs envelope")
        return list(dict.fromkeys(reasons)), errors
    for field, expected in (
        ("study_id", manifest.get("study_id")),
        ("method", "complete_exposure"),
        ("record_type", "screening_response"),
    ):
        if jobs_payload.get(field) != expected:
            reasons.append("planned_jobs_envelope_mismatch")
            errors.append(f"jobs.{field} must equal {expected!r}")
    raw_jobs = jobs_payload.get("synthetic_replicate_jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        reasons.append("planned_jobs_missing")
        errors.append("jobs.synthetic_replicate_jobs must be a non-empty array")
        return list(dict.fromkeys(reasons)), errors

    roster = set(_manifest_creative_roster(manifest))
    audience_lock = manifest.get("audience_lock")
    segment_weights = (
        audience_lock.get("segment_weights")
        if isinstance(audience_lock, Mapping)
        else {}
    )
    jobs_by_segment: Counter[str] = Counter()
    jobs_by_replicate: dict[str, Mapping[str, Any]] = {}
    context_keys = _manifest_context_stratum_keys(manifest)
    for index, job in enumerate(raw_jobs):
        if not isinstance(job, Mapping):
            reasons.append("planned_job_contract_invalid")
            errors.append(f"job[{index}] must be an object")
            continue
        job_errors = validate_job(job)
        if job_errors:
            reasons.append("planned_job_contract_invalid")
            errors.extend(f"job[{index}]: {error}" for error in job_errors)
        segment_id = job.get("segment_id")
        if isinstance(segment_id, str):
            jobs_by_segment[segment_id] += 1
        replicate_id = job.get("synthetic_replicate_id")
        if not isinstance(replicate_id, str) or replicate_id in jobs_by_replicate:
            reasons.append("planned_job_identity_invalid")
            errors.append(f"job[{index}] has a missing or duplicate replicate ID")
        else:
            jobs_by_replicate[replicate_id] = job
        for field in ("variation_ids", "shown_order"):
            value = job.get(field)
            if not isinstance(value, list) or set(value) != roster or len(value) != len(roster):
                reasons.append("complete_set_plan_roster_mismatch")
                errors.append(f"job[{index}].{field} must be the full manifest roster")
        if context_keys is not None and (
            job.get("segment_id"), job.get("context_stratum_id")
        ) not in context_keys:
            reasons.append("planned_job_context_stratum_mismatch")
            errors.append(
                f"job[{index}] must match a manifest-locked segment/context stratum"
            )

    expected_segments = set(segment_weights) if isinstance(segment_weights, Mapping) else set()
    profile_policy = expected_policy is _PROFILE_STRATIFIED_COMPLETE_POLICY
    dynamic_capacity = manifest.get("dynamic_complete_exposure_capacity")
    if profile_policy:
        expected_allocations = (
            dynamic_capacity.get("core_allocation_by_segment")
            if isinstance(dynamic_capacity, Mapping)
            else None
        )
        if (
            not isinstance(expected_allocations, Mapping)
            or set(expected_allocations) != expected_segments
            or dict(jobs_by_segment)
            != {str(key): value for key, value in expected_allocations.items()}
        ):
            reasons.append("complete_set_profile_capacity_mismatch")
            errors.append("complete exposure jobs must match the frozen dynamic segment allocation")
        if (
            not isinstance(dynamic_capacity, Mapping)
            or dynamic_capacity.get("policy_version")
            != expected_policy["capacity_policy_version"]
        ):
            reasons.append("complete_set_profile_capacity_mismatch")
            errors.append("dynamic complete-exposure capacity policy binding is missing or stale")
    elif set(jobs_by_segment) != expected_segments or any(
        jobs_by_segment[segment_id] != 9 for segment_id in expected_segments
    ):
        reasons.append("complete_set_plan_not_nine_per_segment")
        errors.append("complete exposure must predeclare exactly nine jobs per locked segment")
    assignment = manifest.get("assignment")
    planned = (
        assignment.get("planned_participations_per_creative")
        if isinstance(assignment, Mapping)
        else None
    )
    if not profile_policy and planned != 9:
        reasons.append("complete_set_plan_not_nine_per_segment")
        errors.append("assignment.planned_participations_per_creative must be exactly nine")
    usable_plan = (
        assignment.get("usable_participations_per_creative")
        if isinstance(assignment, Mapping)
        else None
    )
    if not profile_policy and (
        not isinstance(usable_plan, Mapping)
        or set(usable_plan) != roster
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 8
            or value > 9
            for value in usable_plan.values()
        )
    ):
        reasons.append("complete_set_usable_floor_not_predeclared")
        errors.append(
            "assignment usable participations must predeclare 8-9 full-roster observations per creative"
        )
    capacity = manifest.get("synthetic_replicate_capacity")
    screening_planned = (
        capacity.get("screening_planned") if isinstance(capacity, Mapping) else None
    )
    expected_screening = (
        dynamic_capacity.get("core_planned_executions")
        if profile_policy and isinstance(dynamic_capacity, Mapping)
        else 9 * len(expected_segments)
    )
    if screening_planned != expected_screening or screening_planned != len(raw_jobs):
        reasons.append("complete_set_screening_reserve_mismatch")
        errors.append("screening_planned must equal the frozen complete-exposure core")

    binding_errors = validate_response_job_bindings(
        raw_jobs,
        records,
        require_exact_set=manifest.get("collection_open") is not True,
    )
    if binding_errors:
        reasons.append("response_planned_job_mismatch")
        errors.extend(f"exact job binding: {error}" for error in binding_errors)
    return list(dict.fromkeys(reasons)), errors


def _validate_finalist_job_binding(
    manifest: Mapping[str, Any],
    approval: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
) -> None:
    if not isinstance(jobs_payload, Mapping):
        raise ValueError("finalist aggregation requires the frozen --jobs envelope")
    jobs = jobs_payload.get("synthetic_replicate_jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("finalist jobs must be a non-empty array")
    capacity = manifest.get("synthetic_replicate_capacity")
    reserve = (
        capacity.get("finalist_reserved") if isinstance(capacity, Mapping) else None
    )
    if isinstance(reserve, bool) or not isinstance(reserve, int) or len(jobs) > reserve:
        raise ValueError("finalist jobs exceed the frozen finalist reserve")
    approved = set(approval.get("approved_finalist_ids", ()))
    jobs_by_replicate: dict[str, Mapping[str, Any]] = {}
    for index, job in enumerate(jobs):
        if not isinstance(job, Mapping):
            raise ValueError(f"finalist job[{index}] must be an object")
        errors = validate_job(job)
        if errors:
            raise ValueError(
                f"finalist job[{index}] is invalid: " + "; ".join(errors)
            )
        if job.get("record_type") != "finalist_response":
            raise ValueError(f"finalist job[{index}] record_type is invalid")
        if set(job.get("variation_ids", ())) != approved:
            raise ValueError(f"finalist job[{index}] must match the approved roster")
        replicate_id = str(job.get("synthetic_replicate_id"))
        if replicate_id in jobs_by_replicate:
            raise ValueError("finalist job replicate IDs must be unique")
        jobs_by_replicate[replicate_id] = job
    binding_errors = validate_response_job_bindings(jobs, records)
    if binding_errors:
        raise ValueError(
            "finalist exact job binding failed: " + "; ".join(binding_errors)
        )


def _frozen_screening_groups(
    screening: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    classifications = screening.get("classifications")
    if not isinstance(classifications, Mapping):
        return [], [], []
    clear_finalists = sorted(
        creative_id
        for creative_id, classification in classifications.items()
        if isinstance(creative_id, str) and classification == "clear_finalist"
    )
    boundary_candidates = sorted(
        creative_id
        for creative_id, classification in classifications.items()
        if isinstance(creative_id, str) and classification == "boundary_candidate"
    )
    clear_non_finalists = sorted(
        creative_id
        for creative_id, classification in classifications.items()
        if isinstance(creative_id, str) and classification == "clear_non_finalist"
    )
    return clear_finalists, boundary_candidates, clear_non_finalists


def _freeze_boundary_plan(
    payload: dict[str, Any], manifest: Mapping[str, Any]
) -> None:
    """Freeze all authorized boundary jobs in the screening output before dispatch."""

    if payload.get("validity_status") != "valid":
        return
    clear_finalists, candidates, _ = _frozen_screening_groups(payload)
    requested = manifest.get("requested_shortlist_size")
    if not isinstance(requested, int) or isinstance(requested, bool):
        raise ValueError("manifest requested shortlist size is invalid")
    remaining_slots = requested - len(clear_finalists)
    if not candidates:
        if remaining_slots != 0:
            raise ValueError(
                "valid screening result has no boundary candidates but finalist slots remain"
            )
        payload["selection_status"] = "resolved"
        payload["proposed_finalist_ids"] = clear_finalists
        return
    if remaining_slots <= 0 or remaining_slots >= len(candidates):
        raise ValueError("frozen boundary candidate group cannot resolve the remaining slots")

    capacity = manifest.get("synthetic_replicate_capacity")
    if not isinstance(capacity, Mapping):
        raise ValueError("manifest synthetic_replicate_capacity must be an object")
    jobs_per_wave = capacity.get("boundary_jobs_per_wave")
    waves_max = capacity.get("boundary_waves_max")
    reserved = capacity.get("boundary_reserved")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (jobs_per_wave, waves_max, reserved)
    ):
        raise ValueError("boundary candidates require positive frozen boundary capacity")
    if reserved != jobs_per_wave * waves_max:
        raise ValueError("boundary reserve must bind jobs per wave and maximum waves")
    pairs = list(itertools.combinations(sorted(candidates), 2))
    if not pairs:
        raise ValueError("boundary plan requires at least one candidate pair")
    profile_rosters = manifest.get("audience_profile_rosters")
    boundary_roster = (
        profile_rosters.get("boundary_reserve")
        if isinstance(profile_rosters, Mapping)
        else None
    )
    assignments: list[dict[str, Any]] = []
    for wave in range(1, waves_max + 1):
        for position in range(1, jobs_per_wave + 1):
            pair = pairs[(position - 1 + wave - 1) % len(pairs)]
            assignment: dict[str, Any] = {
                "pair_assignment_id": (
                    f"boundary-wave-{wave:02d}-job-{position:04d}"
                ),
                "wave": wave,
                "variation_ids": list(pair),
            }
            if isinstance(boundary_roster, Mapping):
                frozen_assignments = boundary_roster.get("assignments")
                frozen_index = len(assignments)
                if (
                    not isinstance(frozen_assignments, list)
                    or frozen_index >= len(frozen_assignments)
                    or not isinstance(
                        frozen_assignments[frozen_index], Mapping
                    )
                ):
                    raise ValueError(
                        "v3 boundary profile roster does not cover every reserve slot"
                    )
                frozen = frozen_assignments[frozen_index]
                assignment.update(
                    {
                        "audience_slot_id": frozen["slot_id"],
                        "grounded_profile_id": frozen[
                            "grounded_profile_id"
                        ],
                        "reported_segment_id": frozen[
                            "reported_segment_id"
                        ],
                        "structural_group_id": frozen[
                            "structural_group_id"
                        ],
                        "profile_snapshot_sha256": frozen[
                            "profile_snapshot_sha256"
                        ],
                    }
                )
            assignments.append(assignment)
    payload["selection_status"] = "boundary_required"
    payload["proposed_finalist_ids"] = []
    boundary_plan = {
        "plan_version": "predeclared-boundary-v1",
        "frozen_before_dispatch": True,
        "available_boundary_reserve": reserved,
        "predeclared_pair_assignments": assignments,
    }
    if isinstance(boundary_roster, Mapping):
        validate_boundary_profile_attachments(boundary_plan, boundary_roster)
    payload["boundary_plan"] = boundary_plan


def _boundary_input_violations(
    manifest: Mapping[str, Any],
    screening: Mapping[str, Any],
    records: list[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    errors: list[str] = []

    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        reasons.append("manifest_contract_invalid")
        errors.extend(f"manifest: {error}" for error in manifest_errors)
    try:
        roster = set(_manifest_creative_roster(manifest))
    except ValueError as exc:
        roster = set()
        if "manifest_contract_invalid" not in reasons:
            reasons.append("manifest_contract_invalid")
        errors.append(f"manifest: {exc}")

    expected_study_id = manifest.get("study_id")
    if screening.get("study_id") != expected_study_id:
        reasons.append("screening_result_study_id_mismatch")
        errors.append("screening result study_id does not match manifest.study_id")
    if screening.get("validity_status") != "valid":
        reasons.append("screening_result_not_valid")
        errors.append("boundary resolution requires a valid frozen screening result")
    profile_rosters = manifest.get("audience_profile_rosters")
    boundary_roster = (
        profile_rosters.get("boundary_reserve")
        if isinstance(profile_rosters, Mapping)
        else None
    )
    if isinstance(boundary_roster, Mapping):
        try:
            validate_boundary_profile_attachments(
                screening.get("boundary_plan"),
                boundary_roster,
            )
        except (TypeError, ValueError) as exc:
            reasons.append("boundary_profile_attachment_invalid")
            errors.append(str(exc))
    requested_top_k = screening.get("requested_top_k")
    if requested_top_k != manifest.get("requested_shortlist_size"):
        reasons.append("screening_result_shortlist_mismatch")
        errors.append("screening requested_top_k does not match the manifest shortlist")

    classifications = screening.get("classifications")
    allowed_classifications = {
        "clear_finalist",
        "boundary_candidate",
        "clear_non_finalist",
    }
    if not isinstance(classifications, Mapping):
        reasons.append("screening_classifications_missing")
        errors.append("screening classifications must be an object")
        boundary_candidates: set[str] = set()
    else:
        classification_ids = set(classifications)
        if not all(isinstance(item, str) and item.strip() for item in classification_ids):
            reasons.append("screening_classification_id_invalid")
            errors.append("screening classification IDs must be non-empty strings")
        if roster and classification_ids != roster:
            reasons.append("screening_classification_roster_mismatch")
            errors.append(
                "screening classification IDs must exactly match the manifest creative roster"
            )
        invalid_values = sorted(
            {
                str(value)
                for value in classifications.values()
                if value not in allowed_classifications
            }
        )
        if invalid_values:
            reasons.append("screening_classification_invalid")
            errors.append(
                "valid frozen screening classifications cannot contain: "
                + ",".join(invalid_values)
            )
        boundary_candidates = {
            str(creative_id)
            for creative_id, classification in classifications.items()
            if classification == "boundary_candidate"
        }

    boundary_plan = screening.get("boundary_plan")
    if not isinstance(boundary_plan, Mapping):
        reasons.append("predeclared_boundary_plan_missing")
        errors.append(
            "screening result must freeze a boundary_plan before pairwise dispatch"
        )
    elif not isinstance(boundary_plan.get("predeclared_pair_assignments"), list):
        reasons.append("predeclared_pair_assignments_missing")
        errors.append(
            "boundary_plan.predeclared_pair_assignments must be an array"
        )

    response_segments: set[str] = set()
    for index, record in enumerate(records):
        record_errors = validate_response(record)
        if record_errors:
            if "response_contract_invalid" not in reasons:
                reasons.append("response_contract_invalid")
            errors.extend(f"response[{index}]: {error}" for error in record_errors)
        if record.get("record_type") != "boundary_response":
            if "non_boundary_record_present" not in reasons:
                reasons.append("non_boundary_record_present")
            errors.append(f"response[{index}].record_type must be boundary_response")
        if record.get("study_id") != expected_study_id:
            if "response_study_id_mismatch" not in reasons:
                reasons.append("response_study_id_mismatch")
            errors.append(
                f"response[{index}].study_id does not match manifest.study_id"
            )
        segment_id = record.get("segment_id")
        if isinstance(segment_id, str):
            response_segments.add(segment_id)
        assigned_ids = record.get("assigned_variation_ids")
        if isinstance(assigned_ids, list):
            outside_roster = sorted(
                {
                    creative_id
                    for creative_id in assigned_ids
                    if isinstance(creative_id, str) and roster and creative_id not in roster
                }
            )
            if outside_roster:
                if "response_creative_out_of_roster" not in reasons:
                    reasons.append("response_creative_out_of_roster")
                errors.append(
                    f"response[{index}].assigned_variation_ids outside manifest roster: "
                    + ",".join(outside_roster)
                )
            outside_boundary = sorted(
                {
                    creative_id
                    for creative_id in assigned_ids
                    if isinstance(creative_id, str)
                    and creative_id not in boundary_candidates
                }
            )
            if outside_boundary:
                if "out_of_scope_pairwise_response" not in reasons:
                    reasons.append("out_of_scope_pairwise_response")
                errors.append(
                    f"response[{index}] compares outside frozen boundary candidates: "
                    + ",".join(outside_boundary)
                )

    for field, reason in (
        ("response_id", "duplicate_response_id"),
        ("synthetic_replicate_id", "duplicate_synthetic_replicate_id"),
    ):
        values = [
            record.get(field)
            for record in records
            if isinstance(record.get(field), str)
        ]
        duplicates = sorted(
            str(value) for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            reasons.append(reason)
            errors.append(f"duplicate {field} values: {','.join(duplicates)}")

    audience_lock = manifest.get("audience_lock")
    segment_weights = (
        audience_lock.get("segment_weights")
        if isinstance(audience_lock, Mapping)
        else None
    )
    known_segments = (
        set(segment_weights) if isinstance(segment_weights, Mapping) else set()
    )
    unknown_segments = response_segments - known_segments
    if not isinstance(segment_weights, Mapping) or unknown_segments:
        reasons.append("response_segment_lock_mismatch")
        if unknown_segments:
            errors.append(
                "boundary responses contain segments outside the manifest "
                "audience lock: "
                + ",".join(sorted(unknown_segments))
            )
        else:
            errors.append("manifest audience-lock segment weights must be an object")
    return list(dict.fromkeys(reasons)), errors


def _boundary_capacity_violations(
    manifest: Mapping[str, Any], boundary_plan: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    errors: list[str] = []
    capacity = manifest.get("synthetic_replicate_capacity")
    if not isinstance(capacity, Mapping):
        return ["boundary_capacity_invalid"], [
            "manifest.synthetic_replicate_capacity must be an object"
        ]
    parsed: dict[str, int] = {}
    for field in (
        "screening_planned",
        "boundary_reserved",
        "boundary_jobs_per_wave",
        "boundary_waves_max",
        "finalist_reserved",
    ):
        value = capacity.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append("boundary_capacity_invalid")
            errors.append(f"synthetic_replicate_capacity.{field} must be non-negative")
        else:
            parsed[field] = value
    required = set(
        (
            "screening_planned",
            "boundary_reserved",
            "boundary_jobs_per_wave",
            "boundary_waves_max",
            "finalist_reserved",
        )
    )
    if set(parsed) == required:
        binding = parsed["boundary_jobs_per_wave"] * parsed["boundary_waves_max"]
        if parsed["boundary_reserved"] != binding:
            reasons.append("boundary_reserve_not_binding")
            errors.append(
                "boundary_reserved must equal boundary_jobs_per_wave * boundary_waves_max"
            )
        required_total = (
            parsed["screening_planned"]
            + parsed["boundary_reserved"]
            + parsed["finalist_reserved"]
        )
        ceiling = manifest.get("maximum_synthetic_panelists")
        if (
            isinstance(ceiling, bool)
            or not isinstance(ceiling, int)
            or required_total > ceiling
            or capacity.get("ceiling_satisfied") is not True
        ):
            reasons.append("binding_ceiling_not_satisfied")
            errors.append(
                "screening, boundary, and finalist reserves must fit the binding ceiling"
            )
        available = boundary_plan.get("available_boundary_reserve")
        if (
            isinstance(available, bool)
            or not isinstance(available, int)
            or available < 0
            or available > parsed["boundary_reserved"]
        ):
            reasons.append("available_boundary_reserve_invalid")
            errors.append(
                "boundary_plan.available_boundary_reserve must be within boundary_reserved"
            )
    return list(dict.fromkeys(reasons)), errors


def _screening_command(args: argparse.Namespace) -> int:
    manifest: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    jobs: dict[str, Any] | None = None
    dispatch_audit: list[dict[str, Any]] | None = None
    try:
        manifest = load_json(args.manifest)
        if args.recovery_config is None:
            raise ValueError(
                f"{manifest.get('method')} requires --recovery-config"
            )
        recovery = load_json(args.recovery_config)
        if args.jobs is not None:
            jobs = load_json(args.jobs)
        records = _read_jsonl(args.responses)
        if args.dispatch_audit is not None:
            dispatch_audit = _read_jsonl(args.dispatch_audit, "dispatch audit")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        payload = _invalid_payload(
            study_id=manifest.get("study_id") if manifest else None,
            reasons=["corrupt_aggregation_input"],
            errors=[str(exc)],
            requested_top_k=(manifest or {}).get("requested_shortlist_size"),
            recovery_config_version=(recovery or {}).get("version"),
        )
        try:
            _write_output(args.output, payload)
        except (OSError, UnicodeError, ValueError) as output_exc:
            print(f"unable to write invalid result: {output_exc}", file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return INVALID_EXIT

    try:
        reasons, errors = _input_violations(manifest, records)
        if manifest.get("method") == "complete_exposure":
            binding_reasons, binding_errors = _complete_binding_violations(
                manifest, jobs, records, recovery
            )
            reasons.extend(binding_reasons)
            errors.extend(binding_errors)
        elif manifest.get("method") == "partial_exposure_maxdiff":
            binding_reasons, binding_errors = _partial_binding_violations(
                manifest,
                jobs,
                records,
                dispatch_audit,
            )
            reasons.extend(binding_reasons)
            errors.extend(binding_errors)
    except (KeyError, TypeError, ValueError) as exc:
        payload = _invalid_payload(
            study_id=manifest.get("study_id"),
            reasons=["corrupt_aggregation_input"],
            errors=[str(exc)],
            requested_top_k=manifest.get("requested_shortlist_size"),
            recovery_config_version=(recovery or {}).get("version"),
        )
        try:
            _write_output(args.output, payload)
        except (OSError, UnicodeError, ValueError) as output_exc:
            print(f"unable to write invalid result: {output_exc}", file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return INVALID_EXIT
    if reasons:
        payload = _invalid_payload(
            study_id=manifest.get("study_id"),
            reasons=reasons,
            errors=errors,
            requested_top_k=manifest.get("requested_shortlist_size"),
            recovery_config_version=(recovery or {}).get("version"),
        )
        try:
            _write_output(args.output, payload)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"unable to write invalid result: {exc}", file=sys.stderr)
            return 2
        print("screening aggregation input is invalid", file=sys.stderr)
        return INVALID_EXIT

    model = manifest.get("model")
    audience_lock = manifest.get("audience_lock")
    assignment = manifest.get("assignment")
    try:
        if not isinstance(model, Mapping):
            raise ValueError("manifest.model must be an object")
        if not isinstance(audience_lock, Mapping):
            raise ValueError("manifest.audience_lock must be an object")
        if not isinstance(assignment, Mapping):
            raise ValueError("manifest.assignment must be an object")
        creative_ids = _manifest_creative_roster(manifest)
        if manifest.get("method") == "complete_exposure":
            if assignment.get("block_size") != len(creative_ids):
                raise ValueError(
                    "complete_exposure manifest.assignment.block_size must equal the creative roster size"
                )
            if model.get("bootstrap_count") != 2000:
                raise ValueError(
                    "complete_exposure manifest.model.bootstrap_count must be exactly 2000"
                )
            profile_contract = (
                _complete_profile_contract(manifest)
                if recovery.get("version") == PROFILE_STRATIFIED_POLICY_VERSION
                else None
            )
            profile_arguments = (
                {}
                if profile_contract is None
                else {
                    "profile_weights": profile_contract[0],
                    "minimum_usable_records_per_profile": profile_contract[1],
                    "minimum_grounded_profile_diversity": recovery[
                        "minimum_grounded_profile_diversity"
                    ],
                    "minimum_evaluable_grounded_profile_exclusions": recovery[
                        "minimum_evaluable_grounded_profile_exclusions"
                    ],
                }
            )
            payload = aggregate_complete_exposure(
                records,
                study_id=manifest["study_id"],
                creative_ids=creative_ids,
                top_k=manifest["requested_shortlist_size"],
                segment_weights=audience_lock["segment_weights"],
                seed=_deterministic_seed(manifest),
                resamples=model["bootstrap_count"],
                collection_open=manifest.get("collection_open") is True,
                expected_job_slots=(
                    manifest.get("synthetic_replicate_capacity", {}).get(
                        "screening_planned"
                    )
                    if isinstance(
                        manifest.get("synthetic_replicate_capacity"), Mapping
                    )
                    else None
                ),
                minimum_usable_records_per_segment=(
                    1
                    if profile_contract is not None
                    else recovery["minimum_usable_records_per_segment"]
                ),
                finalist_inclusion_threshold=recovery[
                    "finalist_inclusion_threshold"
                ],
                nonfinalist_inclusion_threshold=recovery[
                    "nonfinalist_inclusion_threshold"
                ],
                minimum_archetype_diversity=recovery[
                    "minimum_archetype_diversity"
                ],
                minimum_evaluable_archetype_exclusions=recovery[
                    "minimum_evaluable_archetype_exclusions"
                ],
                recovery_config_version=recovery["version"],
                **profile_arguments,
            )
        else:
            if recovery is None:
                raise ValueError(
                    "partial_exposure_maxdiff requires a recovery configuration"
                )
            if manifest.get("method") != "partial_exposure_maxdiff":
                raise ValueError("unsupported screening method")
            if model.get("maxdiff_version") != "joint-maxdiff-v1":
                raise ValueError("manifest.model.maxdiff_version is unsupported")
            if model.get("penalty_type") != "l2":
                raise ValueError("manifest.model.penalty_type must be l2")
            if assignment.get("block_size") != 4:
                raise ValueError("manifest.assignment.block_size must be 4")
            config = MaxDiffConfig(
                penalty_lambda=model.get("penalty_lambda"),
                optimizer_tolerance=model.get("optimizer_tolerance"),
                bootstrap_count=model.get("bootstrap_count"),
                successful_fit_floor=recovery.get("successful_fit_floor"),
                clear_finalist_threshold=model.get("clear_finalist_threshold"),
                clear_non_finalist_threshold=model.get("clear_non_finalist_threshold"),
                seed=_deterministic_seed(manifest),
            )
            partial_profile_contract = _partial_profile_contract(
                manifest,
                jobs,
                creative_ids,
            )
            result = screen_shortlist(
                records,
                audience_lock["segment_weights"],
                top_k=manifest["requested_shortlist_size"],
                config=config,
                recovery_config=recovery,
                creative_ids=creative_ids,
                planned_participations_per_creative=assignment.get(
                    "planned_participations_per_creative"
                ),
                planned_participations_per_profile=(
                    None
                    if partial_profile_contract is None
                    else partial_profile_contract[1]
                ),
                collection_open=manifest.get("collection_open") is True,
                profile_weights=(
                    None
                    if partial_profile_contract is None
                    else partial_profile_contract[0]
                ),
            )
            payload = {
                "study_id": manifest["study_id"],
                "method": "partial_exposure_maxdiff",
                **result.as_dict(),
            }
            _freeze_boundary_plan(payload, manifest)
    except (KeyError, TypeError, ValueError) as exc:
        payload = _invalid_payload(
            study_id=manifest.get("study_id"),
            reasons=["corrupt_aggregation_configuration"],
            errors=[str(exc)],
            requested_top_k=manifest.get("requested_shortlist_size"),
            recovery_config_version=(recovery or {}).get("version"),
        )

    _preserve_audience_authority(payload, manifest)
    try:
        _write_output(args.output, payload)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"unable to write screening result: {exc}", file=sys.stderr)
        return 2
    status = payload["validity_status"]
    print(f"validity_status={status} output={args.output}")
    return INVALID_EXIT if status == "invalid" else 0


def _boundary_command(args: argparse.Namespace) -> int:
    manifest: dict[str, Any] | None = None
    screening: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []
    try:
        manifest = load_json(args.manifest)
        screening = load_json(args.screening_results)
        records = _read_jsonl(args.responses)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        payload = _invalid_boundary_payload(
            study_id=(manifest or {}).get("study_id"),
            reasons=["corrupt_boundary_input"],
            errors=[str(exc)],
            manifest=manifest,
            screening=screening,
            realized_boundary_calls=len(records),
        )
        try:
            _write_output(args.output, payload)
        except (OSError, UnicodeError, ValueError) as output_exc:
            print(f"unable to write invalid boundary result: {output_exc}", file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return INVALID_EXIT

    clear_finalists, boundary_candidates, clear_non_finalists = (
        _frozen_screening_groups(screening)
    )
    try:
        reasons, errors = _boundary_input_violations(manifest, screening, records)
        boundary_plan = screening.get("boundary_plan")
        if isinstance(boundary_plan, Mapping):
            capacity_reasons, capacity_errors = _boundary_capacity_violations(
                manifest, boundary_plan
            )
            reasons.extend(capacity_reasons)
            errors.extend(capacity_errors)
    except (KeyError, TypeError, ValueError) as exc:
        reasons = ["corrupt_boundary_input"]
        errors = [str(exc)]
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        payload = _invalid_boundary_payload(
            study_id=manifest.get("study_id"),
            reasons=reasons,
            errors=errors,
            candidate_ids=boundary_candidates,
            clear_finalist_ids=clear_finalists,
            clear_non_finalist_ids=clear_non_finalists,
            manifest=manifest,
            screening=screening,
            realized_boundary_calls=len(records),
        )
        try:
            _write_output(args.output, payload)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"unable to write invalid boundary result: {exc}", file=sys.stderr)
            return 2
        print("boundary aggregation input is invalid", file=sys.stderr)
        return INVALID_EXIT

    model = manifest.get("model")
    capacity = manifest.get("synthetic_replicate_capacity")
    audience_lock = manifest.get("audience_lock")
    boundary_plan = screening.get("boundary_plan")
    try:
        if not isinstance(model, Mapping):
            raise ValueError("manifest.model must be an object")
        if not isinstance(capacity, Mapping):
            raise ValueError("manifest.synthetic_replicate_capacity must be an object")
        if not isinstance(audience_lock, Mapping):
            raise ValueError("manifest.audience_lock must be an object")
        if not isinstance(boundary_plan, Mapping):
            raise ValueError("screening boundary_plan must be an object")
        if boundary_plan.get("plan_version") != "predeclared-boundary-v1":
            raise ValueError("boundary_plan.plan_version is unsupported")
        if manifest.get("method") != "partial_exposure_maxdiff":
            raise ValueError("boundary aggregation requires partial_exposure_maxdiff")
        if model.get("pairwise_model") != "davidson":
            raise ValueError("manifest.model.pairwise_model must be davidson")
        if model.get("clear_finalist_threshold") != 0.90:
            raise ValueError("pairwise clear-finalist threshold is fixed at 0.90")
        if model.get("clear_non_finalist_threshold") != 0.10:
            raise ValueError("pairwise clear-non-finalist threshold is fixed at 0.10")
        if model.get("bootstrap_count") != 2000:
            raise ValueError(
                "manifest.model.bootstrap_count must be exactly 2000 for "
                "production boundary aggregation"
            )
        config = PairwiseConfig(
            tie_parameter=model.get("pairwise_tie_parameter"),
            penalty_lambda=model.get("pairwise_penalty_lambda"),
            optimizer_tolerance=model.get("pairwise_optimizer_tolerance"),
            bootstrap_count=model.get("bootstrap_count"),
            successful_fit_floor=0.95,
            seed=_boundary_deterministic_seed(manifest),
        )
        requested_top_k = manifest.get("requested_shortlist_size")
        if (
            isinstance(requested_top_k, bool)
            or not isinstance(requested_top_k, int)
            or requested_top_k < 1
        ):
            raise ValueError("manifest.requested_shortlist_size must be positive")
        slots = requested_top_k - len(clear_finalists)
        if slots < 0:
            raise ValueError("frozen clear finalists exceed the requested shortlist")
        result = resolve_boundary(
            records,
            slots,
            config,
            candidate_ids=boundary_candidates,
            segment_weights=audience_lock.get("segment_weights"),
            predeclared_pair_assignments=boundary_plan.get(
                "predeclared_pair_assignments"
            ),
            boundary_jobs_per_wave=capacity.get("boundary_jobs_per_wave"),
            boundary_waves_max=capacity.get("boundary_waves_max"),
            boundary_reserved=capacity.get("boundary_reserved"),
            available_boundary_reserve=boundary_plan.get(
                "available_boundary_reserve"
            ),
            finalist_reserved=capacity.get("finalist_reserved"),
            clear_finalist_ids=clear_finalists,
            clear_non_finalist_ids=clear_non_finalists,
        )
        payload = {"study_id": manifest["study_id"], **result.to_dict()}
    except (KeyError, TypeError, ValueError) as exc:
        payload = _invalid_boundary_payload(
            study_id=manifest.get("study_id"),
            reasons=["corrupt_boundary_configuration"],
            errors=[str(exc)],
            candidate_ids=boundary_candidates,
            clear_finalist_ids=clear_finalists,
            clear_non_finalist_ids=clear_non_finalists,
            manifest=manifest,
            screening=screening,
            realized_boundary_calls=len(records),
        )

    _preserve_audience_authority(payload, manifest)
    if payload.get("status") != "invalid":
        try:
            canonical = canonical_boundary_result(
                manifest,
                screening,
                records,
            )
        except ValueError as exc:
            payload = _invalid_boundary_payload(
                study_id=manifest.get("study_id"),
                reasons=["canonical_boundary_authentication_failed"],
                errors=[str(exc)],
                candidate_ids=boundary_candidates,
                clear_finalist_ids=clear_finalists,
                clear_non_finalist_ids=clear_non_finalists,
                manifest=manifest,
                screening=screening,
                realized_boundary_calls=len(records),
            )
            _preserve_audience_authority(payload, manifest)
        else:
            if payload != canonical:
                raise AssertionError(
                    "boundary CLI and canonical boundary resolver drifted"
                )
            payload = canonical
    try:
        _write_output(args.output, payload)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"unable to write boundary result: {exc}", file=sys.stderr)
        return 2
    status = payload["status"]
    print(f"status={status} output={args.output}")
    return INVALID_EXIT if status == "invalid" else 0


def _finalist_command(args: argparse.Namespace) -> int:
    manifest: dict[str, Any] | None = None
    try:
        manifest = load_json(args.manifest)
        screening = load_json(args.screening_results)
        approval = load_json(args.approval)
        boundary = load_json(args.boundary_results) if args.boundary_results else None
        jobs = load_json(args.jobs) if args.jobs else None
        records = _read_jsonl(args.responses)
        manifest_errors = validate_manifest(manifest)
        if manifest_errors:
            raise ValueError("manifest is invalid: " + "; ".join(manifest_errors))
        _validate_finalist_job_binding(manifest, approval, jobs, records)
        payload = aggregate_finalists(
            manifest,
            screening,
            approval,
            records,
            boundary=boundary,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        payload = {
            "study_id": (manifest or {}).get("study_id"),
            "status": "invalid",
            "approved_finalist_ids": [],
            "roster_decision": {"status": "invalid", "override": False},
            "deterministic_proposed_finalist_ids": [],
            "accepted_response_records": 0,
            "accepted_unique_replicates": 0,
            "unique_job_slots_consumed": 0,
            "total_model_calls": 0,
            "first_choice_counts": {},
            "conditional_first_choice_share": {},
            "rubric_summary": {},
            "validation_errors": [str(exc)],
            "interpretation_limits": [
                "No finalist aggregate is reported for invalid input."
            ],
        }
    _preserve_audience_authority(payload, manifest)
    try:
        _write_output(args.output, payload)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"unable to write finalist result: {exc}", file=sys.stderr)
        return 2
    status = payload["status"]
    print(f"status={status} output={args.output}")
    if status == "invalid":
        print(payload["validation_errors"][0], file=sys.stderr)
        return INVALID_EXIT
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "screening":
        return _screening_command(args)
    if args.command == "boundary":
        return _boundary_command(args)
    if args.command == "finalists":
        return _finalist_command(args)
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
