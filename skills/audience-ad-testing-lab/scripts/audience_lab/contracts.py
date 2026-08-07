"""Shared public data-contract validation for screening studies."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from enum import Enum
from pathlib import Path
import re
from typing import Any, Mapping


class ValidityStatus(str, Enum):
    """The deterministic validity state of a closed or active study."""

    VALID = "valid"
    EXPLORATORY = "exploratory"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


SUPPORTED_CREATIVE_FORMATS = {
    "copy_only",
    "static_image",
    "carousel",
    "video_representation",
}
SUPPORTED_METHODS = {"complete_exposure", "partial_exposure_maxdiff"}
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_BARE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BOUNDARY_SLOT_PATTERN_TEXT = (
    r"^boundary-wave-(0[1-9]|[1-9][0-9]+)-job-([0-9]{4})$"
)
_BOUNDARY_SLOT_PATTERN = re.compile(BOUNDARY_SLOT_PATTERN_TEXT)
_AUDIENCE_PACKAGE_KEYS = {
    "panel_id", "panel_version", "panel_sha256", "panel_byte_count", "brief_id",
    "brief_sha256", "brief_byte_count", "package_manifest_sha256",
    "package_manifest_byte_count", "package_zip_sha256", "package_zip_byte_count",
    "resolved_snapshot_path",
}
_V2_AUDIENCE_LOCK_KEYS = {
    "persona_research_brief_id", "panel_id", "panel_version", "segment_weights",
    "segment_names", "archetype_names", "segment_weight_provenance",
    "unique_archetypes", "unique_grounded_context_profiles", "attribute_provenance",
}
_V3_AUDIENCE_PACKAGE_KEYS = {
    "schema_version", "generator_version", "package_manifest_sha256",
    "package_zip_sha256", "panel_id", "panel_version", "tier", "evidence_basis",
}
_V3_PLAN_FIELDS = {
    "audience_profile_rosters",
    "audience_allocation_fidelity",
    "audience_run_claim",
}
_V3_ROSTER_KEYS = {
    "schema_version",
    "envelope_sha256",
    "screening",
    "boundary_reserve",
    "finalist_reserve",
    "combined_sha256",
}
_V3_STAGE_KEYS = ("screening", "boundary_reserve", "finalist_reserve")
_V3_CAPACITY_KEYS = {
    "screening_planned",
    "boundary_reserved",
    "finalist_reserved",
    "required_total",
    "ceiling",
    "ceiling_satisfied",
    "boundary_jobs_per_wave",
    "boundary_waves_max",
    "shortfall",
}
_BOUNDARY_NOT_APPLICABLE_KEYS = {
    "schema_version",
    "stage",
    "stage_roster_id",
    "status",
    "reason",
    "assignments",
    "fidelity",
}
_BOUNDARY_NOT_APPLICABLE_FIDELITY_KEYS = {
    "status",
    "allocation_basis",
}
_BOUNDARY_ATTACHMENT_KEYS = {
    "pair_assignment_id",
    "wave",
    "variation_ids",
    "audience_slot_id",
    "grounded_profile_id",
    "reported_segment_id",
    "structural_group_id",
    "profile_snapshot_sha256",
}
_BASE_JOBS_ENVELOPE_KEYS = {
    "study_id",
    "method",
    "record_type",
    "synthetic_replicate_jobs",
}
_V3_JOBS_ENVELOPE_KEYS = _BASE_JOBS_ENVELOPE_KEYS | {
    "audience_allocation_subset",
    "audience_run_claim",
    "audience_dispatch",
}
_V3_AUDIENCE_DISPATCH_KEYS = {
    "stage",
    "newly_authorized_slot_ids",
}
_LINEAGE_OUTPUTS = {
    "accepted_responses": "panelist-responses.jsonl",
    "raw_provider_returns": "raw-provider-returns.jsonl",
    "rejected_attempts": "rejected-attempts.jsonl",
    "dispatch_audit": "dispatch-audit.jsonl",
}


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from *path*, rejecting other JSON top-level types."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def resolve_validity(gates: Mapping[str, bool]) -> ValidityStatus:
    """Resolve model validity without allowing regularization to repair disconnection."""

    if gates.get("collection_open", False):
        return ValidityStatus.INCOMPLETE
    if not gates.get("connected", False) or not gates.get("identified", False):
        return ValidityStatus.INVALID
    required = ("usable_coverage", "block_resilience", "converged", "stability")
    return ValidityStatus.VALID if all(gates.get(name, False) for name in required) else ValidityStatus.EXPLORATORY


def _canonical_sha256(value: object) -> str:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _is_prefixed_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def validate_boundary_profile_attachments(
    boundary_plan: Mapping[str, Any],
    roster: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind creative pairs to every frozen boundary profile slot, unchanged."""

    if not isinstance(boundary_plan, Mapping):
        raise ValueError("boundary plan must be an object")
    if not isinstance(roster, Mapping) or roster.get("stage") != "boundary":
        raise ValueError("boundary profile roster must be a boundary allocation plan")
    frozen = roster.get("assignments")
    attached = boundary_plan.get("predeclared_pair_assignments")
    if not isinstance(frozen, list) or not frozen:
        raise ValueError("boundary profile roster assignments must be non-empty")
    if not isinstance(attached, list) or len(attached) != len(frozen):
        raise ValueError(
            "creative-pair attachments must exactly cover the frozen boundary roster"
        )
    for index, (assignment, attachment) in enumerate(zip(frozen, attached)):
        if not isinstance(assignment, Mapping) or not isinstance(
            attachment, Mapping
        ):
            raise ValueError("boundary assignments and attachments must be objects")
        if set(attachment) != _BOUNDARY_ATTACHMENT_KEYS:
            raise ValueError(
                f"boundary attachment[{index}] keys do not match the allowlist"
            )
        variation_ids = attachment.get("variation_ids")
        if (
            not isinstance(variation_ids, list)
            or len(variation_ids) != 2
            or len(set(variation_ids)) != 2
            or not all(
                isinstance(creative_id, str) and creative_id.strip()
                for creative_id in variation_ids
            )
        ):
            raise ValueError(
                f"boundary attachment[{index}] must contain one creative pair"
            )
        slot_id = assignment.get("slot_id")
        if (
            attachment.get("pair_assignment_id") != slot_id
            or attachment.get("audience_slot_id") != slot_id
        ):
            raise ValueError(
                "boundary creative attachment changed frozen slot identity or order"
            )
        slot_match = (
            _BOUNDARY_SLOT_PATTERN.fullmatch(slot_id)
            if isinstance(slot_id, str)
            else None
        )
        if (
            slot_match is None
            or attachment.get("wave") != int(slot_match.group(1))
        ):
            raise ValueError(
                "boundary creative attachment changed frozen wave authority"
            )
        for field in (
            "grounded_profile_id",
            "reported_segment_id",
            "structural_group_id",
            "profile_snapshot_sha256",
        ):
            if attachment.get(field) != assignment.get(field):
                raise ValueError(
                    f"boundary creative attachment changed frozen {field}"
                )
    return copy.deepcopy(dict(boundary_plan))


def _validate_boundary_not_applicable(
    payload: object,
    *,
    study_id: object,
    allocation_basis: object,
) -> dict[str, Any]:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != _BOUNDARY_NOT_APPLICABLE_KEYS
    ):
        raise ValueError(
            "boundary not-applicable keys must match the contract exactly"
        )
    expected = {
        "schema_version": "audience-profile-allocation-not-applicable-v1",
        "stage": "boundary",
        "stage_roster_id": f"{study_id}:boundary-reserve",
        "status": "not_applicable",
        "reason": "method_complete_exposure",
        "assignments": [],
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ValueError(
                f"boundary not-applicable {field} does not match the contract"
            )
    fidelity = payload.get("fidelity")
    if (
        not isinstance(fidelity, Mapping)
        or set(fidelity) != _BOUNDARY_NOT_APPLICABLE_FIDELITY_KEYS
        or fidelity.get("status") != "not_applicable"
        or fidelity.get("allocation_basis") != allocation_basis
    ):
        raise ValueError(
            "boundary not-applicable fidelity does not match the derived allocation basis"
        )
    return copy.deepcopy(dict(payload))


def _validate_v3_plan_fields(payload: Mapping[str, Any]) -> list[str]:
    from .audience_allocation import validate_allocation_plan

    study_id = payload.get("study_id")
    if not isinstance(study_id, str) or not study_id.strip():
        return ["study_id must be a non-empty string for v3 authority"]

    errors: list[str] = []
    rosters = payload.get("audience_profile_rosters")
    if not isinstance(rosters, Mapping) or set(rosters) != _V3_ROSTER_KEYS:
        return [
            "audience_profile_rosters keys must exactly match the v3 allowlist"
        ]
    if rosters.get("schema_version") != "audience-profile-rosters-v1":
        errors.append("audience_profile_rosters.schema_version is unsupported")
    if not _is_prefixed_sha256(rosters.get("envelope_sha256")):
        errors.append(
            "audience_profile_rosters.envelope_sha256 must be a SHA-256 hash"
        )
    expected_stages = {
        "screening": "screening",
        "finalist_reserve": "finalist",
    }
    validated: dict[str, dict[str, object]] = {}
    for name, expected_stage in expected_stages.items():
        try:
            plan = validate_allocation_plan(rosters.get(name))
        except (TypeError, ValueError) as exc:
            errors.append(f"audience_profile_rosters.{name} is invalid: {exc}")
            continue
        if plan["stage"] != expected_stage:
            errors.append(
                f"audience_profile_rosters.{name}.stage must be {expected_stage}"
            )
        validated[name] = plan
    screening_basis = (
        validated["screening"]["fidelity"]["allocation_basis"]
        if "screening" in validated
        else None
    )
    boundary_not_applicable = None
    boundary_payload = rosters.get("boundary_reserve")
    if (
        isinstance(boundary_payload, Mapping)
        and boundary_payload.get("schema_version")
        == "audience-profile-allocation-not-applicable-v1"
    ):
        try:
            boundary_not_applicable = _validate_boundary_not_applicable(
                boundary_payload,
                study_id=payload.get("study_id"),
                allocation_basis=screening_basis,
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                f"audience_profile_rosters.boundary_reserve is invalid: {exc}"
            )
    else:
        try:
            boundary_plan = validate_allocation_plan(boundary_payload)
        except (TypeError, ValueError) as exc:
            errors.append(
                f"audience_profile_rosters.boundary_reserve is invalid: {exc}"
            )
        else:
            if boundary_plan["stage"] != "boundary":
                errors.append(
                    "audience_profile_rosters.boundary_reserve.stage must be boundary"
                )
            validated["boundary_reserve"] = boundary_plan
    method = payload.get("method")
    assignment = payload.get("assignment")
    capacity = payload.get("synthetic_replicate_capacity")
    maximum_panelists = payload.get("maximum_synthetic_panelists")
    try:
        assignment_sha256 = _canonical_sha256(assignment)
    except (TypeError, ValueError):
        assignment_sha256 = None
        errors.append(
            "assignment must be finite canonical JSON before authority hashing"
        )
    combined_input = {
        "schema_version": rosters.get("schema_version"),
        "study_id": study_id,
        "method": method,
        "maximum_synthetic_panelists": maximum_panelists,
        "synthetic_replicate_capacity": capacity,
        "assignment_sha256": assignment_sha256,
        "envelope_sha256": rosters.get("envelope_sha256"),
        "screening": rosters.get("screening"),
        "boundary_reserve": rosters.get("boundary_reserve"),
        "finalist_reserve": rosters.get("finalist_reserve"),
    }
    try:
        expected_combined_sha256 = _canonical_sha256(combined_input)
    except (TypeError, ValueError):
        expected_combined_sha256 = None
        errors.append(
            "combined roster authority must contain only finite canonical JSON"
        )
    if (
        expected_combined_sha256 is not None
        and rosters.get("combined_sha256") != expected_combined_sha256
    ):
        errors.append(
            "audience_profile_rosters.combined_sha256 does not bind the study, method, capacity, assignment, envelope, and stage plans"
        )

    expected_stage_authority = {
        "screening": ("screening", "screening"),
        "boundary_reserve": ("boundary", "boundary-reserve"),
        "finalist_reserve": ("finalist", "finalist-reserve"),
    }
    if isinstance(study_id, str) and study_id:
        stable_seed = None
        if isinstance(assignment, Mapping):
            assignment_seed = assignment.get("seed")
            if isinstance(assignment_seed, int) and not isinstance(
                assignment_seed, bool
            ):
                stable_seed = (
                    f"{study_id}:{assignment_seed}:"
                    "audience-profile-allocation-v1"
                )
        for stage_key, (_stage, roster_suffix) in (
            expected_stage_authority.items()
        ):
            stage_payload = rosters.get(stage_key)
            if not isinstance(stage_payload, Mapping):
                continue
            if stage_payload.get("stage_roster_id") != (
                f"{study_id}:{roster_suffix}"
            ):
                errors.append(
                    f"audience_profile_rosters.{stage_key}.stage_roster_id must bind the exact study"
                )
            if (
                stage_payload.get("schema_version")
                != "audience-profile-allocation-not-applicable-v1"
                and stage_payload.get("stable_seed") != stable_seed
            ):
                errors.append(
                    f"audience_profile_rosters.{stage_key}.stable_seed must bind the exact study and assignment seed"
                )

    fidelity = payload.get("audience_allocation_fidelity")
    if not isinstance(fidelity, Mapping) or set(fidelity) != set(_V3_STAGE_KEYS):
        errors.append(
            "audience_allocation_fidelity keys must exactly match all three stages"
        )
    else:
        expected_fidelity = {
            stage: validated[stage]["fidelity"]
            for stage in ("screening", "finalist_reserve")
            if stage in validated
        }
        if "boundary_reserve" in validated:
            expected_fidelity["boundary_reserve"] = validated[
                "boundary_reserve"
            ]["fidelity"]
        elif boundary_not_applicable is not None:
            expected_fidelity["boundary_reserve"] = boundary_not_applicable[
                "fidelity"
            ]
        if (
            set(expected_fidelity) == set(_V3_STAGE_KEYS)
            and fidelity != expected_fidelity
        ):
            errors.append(
                "audience_allocation_fidelity must copy exact full-reserve fidelity"
            )
    if (
        "screening" in validated
        and payload.get("audience_run_claim")
        != validated["screening"]["claim_effect"]
    ):
        errors.append(
            "audience_run_claim must equal the complete screening roster claim"
        )

    screening_jobs = (
        assignment.get("synthetic_replicate_jobs")
        if isinstance(assignment, Mapping)
        else None
    )
    if "screening" in validated:
        if not isinstance(screening_jobs, list) or not screening_jobs:
            errors.append(
                "v3 screening profile roster requires the original synthetic replicate jobs"
            )
        else:
            expected_screening_pairs = [
                (
                    job.get("synthetic_replicate_id"),
                    job.get("segment_id"),
                )
                for job in screening_jobs
                if isinstance(job, Mapping)
            ]
            actual_screening_pairs = [
                (item["slot_id"], item["reported_segment_id"])
                for item in validated["screening"]["assignments"]
            ]
            if (
                len(expected_screening_pairs) != len(screening_jobs)
                or expected_screening_pairs != actual_screening_pairs
            ):
                errors.append(
                    "screening profile roster must preserve exact synthetic replicate ID and segment pairs in order"
                )

    audience_lock = payload.get("audience_lock")
    segment_weights = (
        audience_lock.get("segment_weights")
        if isinstance(audience_lock, Mapping)
        else None
    )
    segment_ids = (
        sorted(segment_weights)
        if isinstance(segment_weights, Mapping)
        and all(isinstance(key, str) for key in segment_weights)
        else []
    )
    if (
        not isinstance(capacity, Mapping)
        or set(capacity) != _V3_CAPACITY_KEYS
    ):
        errors.append(
            "v3 manifests require the exact synthetic_replicate_capacity schema"
        )
    else:
        integer_fields = _V3_CAPACITY_KEYS - {"ceiling_satisfied"}
        capacity_integers_valid = not any(
            isinstance(capacity[field], bool)
            or not isinstance(capacity[field], int)
            or capacity[field] < 0
            for field in integer_fields
        )
        if not capacity_integers_valid:
            errors.append(
                "synthetic_replicate_capacity integer fields must be nonnegative integers"
            )
        if not isinstance(capacity["ceiling_satisfied"], bool):
            errors.append(
                "synthetic_replicate_capacity.ceiling_satisfied must be a boolean"
            )
        if (
            isinstance(maximum_panelists, bool)
            or not isinstance(maximum_panelists, int)
            or maximum_panelists < 1
            or capacity["ceiling"] != maximum_panelists
        ):
            errors.append(
                "synthetic_replicate_capacity.ceiling must equal maximum_synthetic_panelists"
            )
        screening_count = (
            len(validated["screening"]["assignments"])
            if "screening" in validated
            else None
        )
        boundary_count = (
            0
            if boundary_not_applicable is not None
            else (
                len(validated["boundary_reserve"]["assignments"])
                if "boundary_reserve" in validated
                else None
            )
        )
        finalist_count = (
            len(validated["finalist_reserve"]["assignments"])
            if "finalist_reserve" in validated
            else None
        )
        if (
            screening_count is not None
            and capacity["screening_planned"] != screening_count
        ):
            errors.append(
                "screening_planned must equal the frozen screening roster count"
            )
        if (
            boundary_count is not None
            and capacity["boundary_reserved"] != boundary_count
        ):
            errors.append(
                "boundary_reserved must equal the frozen boundary roster count"
            )
        if (
            finalist_count is not None
            and capacity["finalist_reserved"] != finalist_count
        ):
            errors.append(
                "finalist_reserved must equal the frozen finalist roster count"
            )
        if capacity_integers_valid:
            expected_required = (
                capacity["screening_planned"]
                + capacity["boundary_reserved"]
                + capacity["finalist_reserved"]
            )
            expected_shortfall = max(
                0, expected_required - capacity["ceiling"]
            )
            expected_satisfied = expected_shortfall == 0
            if capacity["required_total"] != expected_required:
                errors.append(
                    "required_total must equal screening, boundary, and finalist capacity"
                )
            if capacity["shortfall"] != expected_shortfall:
                errors.append(
                    "shortfall must equal max(0, required_total - ceiling)"
                )
            if capacity["ceiling_satisfied"] is not expected_satisfied:
                errors.append(
                    "ceiling_satisfied must equal the exact capacity algebra"
                )
            if not expected_satisfied or capacity["shortfall"] != 0:
                errors.append(
                    "v3 dispatch authority requires satisfied capacity with zero shortfall"
                )
        jobs_per_wave = capacity.get("boundary_jobs_per_wave")
        waves_max = capacity.get("boundary_waves_max")
        boundary_reserved = capacity.get("boundary_reserved")
        finalist_reserved = capacity.get("finalist_reserved")
        method = payload.get("method")
        if boundary_not_applicable is not None:
            if method != "complete_exposure":
                errors.append(
                    "boundary not-applicable is allowed only for complete_exposure"
                )
            if (
                not isinstance(boundary_reserved, int)
                or isinstance(boundary_reserved, bool)
                or boundary_reserved != 0
                or not isinstance(jobs_per_wave, int)
                or isinstance(jobs_per_wave, bool)
                or jobs_per_wave != 0
                or not isinstance(waves_max, int)
                or isinstance(waves_max, bool)
                or waves_max != 0
            ):
                errors.append(
                    "complete_exposure boundary not-applicable requires exact zero boundary capacity"
                )
        else:
            if method != "partial_exposure_maxdiff":
                errors.append(
                    "complete_exposure must use the boundary not-applicable record"
                )
            if (
                isinstance(boundary_reserved, bool)
                or not isinstance(boundary_reserved, int)
                or boundary_reserved < 1
                or isinstance(jobs_per_wave, bool)
                or not isinstance(jobs_per_wave, int)
                or jobs_per_wave < 1
                or isinstance(waves_max, bool)
                or not isinstance(waves_max, int)
                or waves_max < 1
                or boundary_reserved != jobs_per_wave * waves_max
            ):
                errors.append(
                    "partial_exposure_maxdiff requires an ordinary positive boundary reserve"
                )
        if (
            "boundary_reserve" in validated
            and isinstance(jobs_per_wave, int)
            and not isinstance(jobs_per_wave, bool)
            and isinstance(waves_max, int)
            and not isinstance(waves_max, bool)
        ):
            expected_boundary = [
                (
                    f"boundary-wave-{wave:02d}-job-{position:04d}",
                    segment_ids[index % len(segment_ids)] if segment_ids else None,
                )
                for index, (wave, position) in enumerate(
                    (
                        (wave, position)
                        for wave in range(1, waves_max + 1)
                        for position in range(1, jobs_per_wave + 1)
                    )
                )
            ]
            actual_boundary = [
                (item["slot_id"], item["reported_segment_id"])
                for item in validated["boundary_reserve"]["assignments"]
            ]
            if actual_boundary != expected_boundary:
                errors.append(
                    "boundary profile roster must preserve exact reserve ID, segment, wave, and position order"
                )
        if (
            "finalist_reserve" in validated
            and isinstance(finalist_reserved, int)
            and not isinstance(finalist_reserved, bool)
        ):
            expected_finalist_ids = [
                f"finalist-{index:04d}"
                for index in range(1, finalist_reserved + 1)
            ]
            actual_finalist_ids = [
                item["slot_id"]
                for item in validated["finalist_reserve"]["assignments"]
            ]
            if actual_finalist_ids != expected_finalist_ids:
                errors.append(
                    "finalist profile roster must preserve exact global reserve ID order"
                )

    profiles = payload.get("grounded_context_profiles")
    if (
        not isinstance(profiles, list)
        or not profiles
        or not all(isinstance(item, Mapping) for item in profiles)
    ):
        errors.append(
            "v3 manifests require a non-empty grounded_context_profiles collection"
        )
        return errors
    profile_by_id: dict[str, Mapping[str, Any]] = {}
    profile_contract_valid = True
    for index, item in enumerate(profiles):
        profile_id = item.get("grounded_profile_id")
        segment_id = item.get("reported_segment_id")
        group_id = item.get("structural_group_id")
        effective_weight = item.get("effective_weight")
        must_cover = item.get("must_cover_group_ids")
        if not isinstance(profile_id, str) or not profile_id.strip():
            errors.append(
                f"grounded_context_profiles[{index}].grounded_profile_id is required"
            )
            profile_contract_valid = False
            continue
        if profile_id in profile_by_id:
            errors.append(
                "grounded_context_profiles must have unique v3 profile identities"
            )
            profile_contract_valid = False
            continue
        if not isinstance(segment_id, str) or not segment_id.strip():
            errors.append(
                f"grounded_context_profiles[{index}].reported_segment_id is required"
            )
            profile_contract_valid = False
        if not isinstance(group_id, str) or not group_id.strip():
            errors.append(
                f"grounded_context_profiles[{index}].structural_group_id is required"
            )
            profile_contract_valid = False
        if (
            isinstance(effective_weight, bool)
            or not isinstance(effective_weight, (int, float))
            or not math.isfinite(effective_weight)
            or effective_weight < 0
        ):
            errors.append(
                f"grounded_context_profiles[{index}].effective_weight must be finite and nonnegative"
            )
            profile_contract_valid = False
        if (
            not isinstance(must_cover, list)
            or not all(
                isinstance(group, str) and group.strip()
                for group in must_cover
            )
            or len(must_cover) != len(set(must_cover))
        ):
            errors.append(
                f"grounded_context_profiles[{index}].must_cover_group_ids is invalid"
            )
            profile_contract_valid = False
        if not _is_prefixed_sha256(item.get("profile_snapshot_sha256")):
            errors.append(
                f"grounded_context_profiles[{index}].profile_snapshot_sha256 is invalid"
            )
            profile_contract_valid = False
        if not isinstance(item.get("eligible"), bool):
            errors.append(
                f"grounded_context_profiles[{index}].eligible must be a boolean"
            )
            profile_contract_valid = False
        profile_by_id[profile_id] = item

    profile_ids = set(profile_by_id)
    for stage in _V3_STAGE_KEYS:
        if stage not in validated:
            continue
        diagnostics = validated[stage]["profile_diagnostics"]
        diagnostic_ids = {
            item["grounded_profile_id"] for item in diagnostics
        }
        if diagnostic_ids != profile_ids:
            errors.append(
                f"{stage} profile diagnostics must exactly cover grounded_context_profiles"
            )
        for diagnostic in diagnostics:
            profile = profile_by_id.get(diagnostic["grounded_profile_id"])
            if profile is None:
                continue
            for field in (
                "reported_segment_id",
                "structural_group_id",
                "profile_snapshot_sha256",
                "eligible",
                "must_cover_group_ids",
            ):
                if diagnostic[field] != profile.get(field):
                    errors.append(
                        f"{stage} profile diagnostic {field} does not match the frozen profile"
                    )
        for item in validated[stage]["assignments"]:
            profile = profile_by_id.get(item["grounded_profile_id"])
            if profile is None:
                errors.append(
                    f"{stage} assignment references an unknown grounded profile"
                )
                continue
            for field in (
                "reported_segment_id",
                "structural_group_id",
                "profile_snapshot_sha256",
            ):
                if item[field] != profile.get(field):
                    errors.append(
                        f"{stage} assignment {field} does not match the frozen profile"
                    )

    valid_segment_weights = (
        isinstance(segment_weights, Mapping)
        and segment_weights
        and all(
            isinstance(segment_id, str)
            and segment_id
            and not isinstance(weight, bool)
            and isinstance(weight, (int, float))
            and math.isfinite(weight)
            and weight >= 0
            for segment_id, weight in segment_weights.items()
        )
    )
    if profile_contract_valid and valid_segment_weights:
        eligible = [
            profile for profile in profile_by_id.values()
            if profile["eligible"]
        ]
        global_total = math.fsum(
            float(profile["effective_weight"]) for profile in eligible
        )
        segment_totals = {
            segment_id: math.fsum(
                float(profile["effective_weight"])
                for profile in eligible
                if profile["reported_segment_id"] == segment_id
            )
            for segment_id in segment_weights
        }
        for stage in _V3_STAGE_KEYS:
            if stage not in validated:
                continue
            for diagnostic in validated[stage]["profile_diagnostics"]:
                profile = profile_by_id.get(
                    diagnostic["grounded_profile_id"]
                )
                if profile is None:
                    continue
                expected = 0.0
                if profile["eligible"]:
                    if stage == "finalist_reserve":
                        if global_total > 0:
                            expected = (
                                float(profile["effective_weight"])
                                / global_total
                            )
                    else:
                        segment_id = profile["reported_segment_id"]
                        segment_total = segment_totals.get(segment_id, 0.0)
                        if segment_total > 0:
                            expected = (
                                float(segment_weights[segment_id])
                                * float(profile["effective_weight"])
                                / segment_total
                            )
                if not math.isclose(
                    float(diagnostic["target_weight"]),
                    expected,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    errors.append(
                        f"{stage} profile diagnostic target_weight does not match grounded profile effective_weight"
                    )
    return errors


def validate_v3_dispatch_authority(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the frozen v3 fields needed before any panelist dispatch."""

    if not isinstance(payload, Mapping):
        raise ValueError("v3 dispatch authority must be an object")
    package = payload.get("audience_package")
    if (
        not isinstance(package, Mapping)
        or package.get("schema_version") != "audience-panel-package-v3"
    ):
        raise ValueError(
            "v3 dispatch authority requires an audience-panel-package-v3 binding"
        )
    present = _V3_PLAN_FIELDS & set(payload)
    if present != _V3_PLAN_FIELDS:
        raise ValueError(
            "v3 dispatch authority requires all frozen audience allocation fields"
        )
    errors = _validate_v3_plan_fields(payload)
    if errors:
        raise ValueError("; ".join(errors))
    return copy.deepcopy(dict(payload))


def canonical_v3_dispatch_cores(
    *,
    authority: Mapping[str, Any],
    dispatch_authority: Mapping[str, Any],
    allocation_plan: Mapping[str, Any],
    selected_slot_ids: list[str],
) -> list[dict[str, Any]]:
    """Rebuild exact stage cores from frozen authorities, never persisted jobs."""

    from .audience_allocation import validate_allocation_plan
    from .finalists import validate_roster_approval

    validated_authority = validate_v3_dispatch_authority(authority)
    plan = validate_allocation_plan(allocation_plan)
    if (
        not isinstance(selected_slot_ids, list)
        or not selected_slot_ids
        or not all(
            isinstance(slot_id, str) and slot_id
            for slot_id in selected_slot_ids
        )
        or len(selected_slot_ids) != len(set(selected_slot_ids))
    ):
        raise ValueError(
            "canonical v3 dispatch requires unique selected slot IDs"
        )
    stage_key = {
        "screening": "screening",
        "boundary": "boundary_reserve",
        "finalist": "finalist_reserve",
    }[plan["stage"]]
    if validated_authority["audience_profile_rosters"][stage_key] != plan:
        raise ValueError(
            "canonical dispatch plan must equal the frozen authority roster"
        )
    frozen_ids = [
        assignment["slot_id"] for assignment in plan["assignments"]
    ]
    selected_set = set(selected_slot_ids)
    if (
        any(slot_id not in frozen_ids for slot_id in selected_slot_ids)
        or [slot_id for slot_id in frozen_ids if slot_id in selected_set]
        != selected_slot_ids
    ):
        raise ValueError(
            "canonical v3 dispatch slots must preserve frozen roster order"
        )
    assignment_by_slot = {
        assignment["slot_id"]: assignment
        for assignment in plan["assignments"]
    }

    if plan["stage"] == "screening":
        if dispatch_authority != validated_authority:
            raise ValueError(
                "screening dispatch authority must equal the complete frozen study authority"
            )
        assignment = validated_authority.get("assignment")
        jobs = (
            assignment.get("synthetic_replicate_jobs")
            if isinstance(assignment, Mapping)
            else None
        )
        if not isinstance(jobs, list) or not jobs:
            raise ValueError(
                "screening dispatch authority requires frozen assignment jobs"
            )
        by_slot: dict[str, Mapping[str, Any]] = {}
        for index, job in enumerate(jobs):
            if not isinstance(job, Mapping):
                raise ValueError(
                    f"screening assignment job[{index}] must be an object"
                )
            slot_id = job.get("synthetic_replicate_id")
            if not isinstance(slot_id, str) or not slot_id:
                raise ValueError(
                    f"screening assignment job[{index}] slot ID is required"
                )
            if slot_id in by_slot:
                raise ValueError(
                    "screening assignment job slot IDs must be unique"
                )
            by_slot[slot_id] = job
        if list(by_slot) != frozen_ids:
            raise ValueError(
                "screening assignment jobs must equal the frozen roster order"
            )
        return [copy.deepcopy(dict(by_slot[slot_id])) for slot_id in selected_slot_ids]

    if plan["stage"] == "boundary":
        boundary_plan = dispatch_authority.get("boundary_plan")
        if not isinstance(boundary_plan, Mapping):
            raise ValueError(
                "boundary dispatch authority requires the frozen boundary plan"
            )
        validated_boundary = validate_boundary_profile_attachments(
            boundary_plan,
            plan,
        )
        attachments = validated_boundary["predeclared_pair_assignments"]
        by_slot = {
            attachment["pair_assignment_id"]: attachment
            for attachment in attachments
        }
        cores: list[dict[str, Any]] = []
        frozen_position = {
            frozen_slot_id: index
            for index, frozen_slot_id in enumerate(frozen_ids)
        }
        for slot_id in selected_slot_ids:
            attachment = by_slot.get(slot_id)
            if attachment is None:
                raise ValueError(
                    "boundary dispatch slot is absent from frozen creative attachments"
                )
            variation_ids = list(attachment["variation_ids"])
            assignment = assignment_by_slot[slot_id]
            cores.append(
                {
                    "synthetic_replicate_id": slot_id,
                    "segment_id": assignment["reported_segment_id"],
                    "variation_ids": variation_ids,
                    "shown_order": (
                        variation_ids
                        if frozen_position[slot_id] % 2 == 0
                        else list(reversed(variation_ids))
                    ),
                    "assigned_variation_ids": variation_ids,
                    "pair_assignment_id": slot_id,
                    "boundary_wave": attachment["wave"],
                }
            )
        return cores

    approved_ids, _decision = validate_roster_approval(
        validated_authority,
        dispatch_authority,
    )
    return [
        {
            "synthetic_replicate_id": slot_id,
            "segment_id": assignment_by_slot[slot_id][
                "reported_segment_id"
            ],
            "variation_ids": approved_ids,
            "shown_order": (
                approved_ids
                if index % 2 == 0
                else list(reversed(approved_ids))
            ),
        }
        for index, slot_id in enumerate(selected_slot_ids)
    ]


def validate_v3_jobs_envelope(
    payload: Mapping[str, Any],
    *,
    allocation_plan: Mapping[str, Any],
    authority: Mapping[str, Any],
    audience_resolution: Path | str,
    dispatch_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Authorize one worker-ready v3 envelope against its immutable run unit."""

    from .audience_allocation import (
        validate_allocation_plan,
        validate_allocation_subset,
    )
    from .planning import load_reusable_v3_audience_resolution
    from .responses import validate_job

    if not isinstance(payload, Mapping) or set(payload) != _V3_JOBS_ENVELOPE_KEYS:
        raise ValueError("v3 jobs envelope keys do not match the allowlist")
    validated_authority = validate_v3_dispatch_authority(authority)
    envelope, envelope_bytes = load_reusable_v3_audience_resolution(
        audience_resolution
    )
    plan = validate_allocation_plan(allocation_plan)
    stage_key = {
        "screening": "screening",
        "boundary": "boundary_reserve",
        "finalist": "finalist_reserve",
    }[plan["stage"]]
    rosters = validated_authority["audience_profile_rosters"]
    if rosters[stage_key] != plan:
        raise ValueError(
            "v3 jobs allocation plan must equal the frozen authority roster"
        )
    expected_envelope_hash = "sha256:" + hashlib.sha256(
        envelope_bytes
    ).hexdigest()
    if rosters["envelope_sha256"] != expected_envelope_hash:
        raise ValueError(
            "v3 jobs authority does not bind the canonical audience envelope"
        )
    for field in (
        "audience_package",
        "audience_lock",
        "grounded_context_profiles",
    ):
        if validated_authority.get(field) != envelope.get(field):
            raise ValueError(
                f"v3 jobs authority {field} does not match the canonical audience"
            )
    expected_record_type = {
        "screening": "screening_response",
        "boundary": "boundary_response",
        "finalist": "finalist_response",
    }[plan["stage"]]
    if payload.get("study_id") != validated_authority.get("study_id"):
        raise ValueError("v3 jobs study_id does not match the frozen authority")
    if payload.get("method") != validated_authority.get("method"):
        raise ValueError("v3 jobs method does not match the frozen authority")
    if payload.get("record_type") != expected_record_type:
        raise ValueError("v3 jobs record_type does not match the frozen stage")
    subset = validate_allocation_subset(
        payload.get("audience_allocation_subset"),
        plan=plan,
    )
    if subset["claim_effect"] == "requires_user_decision":
        raise ValueError(
            "v3 worker-ready jobs cannot carry a decision-required subset"
        )
    if payload.get("audience_run_claim") != subset["claim_effect"]:
        raise ValueError(
            "v3 jobs audience_run_claim must equal the validated subset claim"
        )
    dispatch = payload.get("audience_dispatch")
    if (
        not isinstance(dispatch, Mapping)
        or set(dispatch) != _V3_AUDIENCE_DISPATCH_KEYS
        or dispatch.get("stage") != plan["stage"]
    ):
        raise ValueError("v3 audience_dispatch does not match the frozen stage")
    slot_ids = dispatch.get("newly_authorized_slot_ids")
    if (
        not isinstance(slot_ids, list)
        or not slot_ids
        or not all(isinstance(slot_id, str) and slot_id for slot_id in slot_ids)
        or len(slot_ids) != len(set(slot_ids))
    ):
        raise ValueError(
            "v3 newly_authorized_slot_ids must contain unique frozen slot IDs"
        )
    selected_slot_ids = subset["selected_slot_ids"]
    if any(slot_id not in selected_slot_ids for slot_id in slot_ids):
        raise ValueError(
            "v3 newly authorized slots must lie inside the validated subset"
        )
    if plan["stage"] == "boundary":
        selected_waves = [
            int(_BOUNDARY_SLOT_PATTERN.fullmatch(slot_id).group(1))
            for slot_id in selected_slot_ids
        ]
        current_wave = max(selected_waves)
        newly_authorized_waves = {
            int(_BOUNDARY_SLOT_PATTERN.fullmatch(slot_id).group(1))
            for slot_id in slot_ids
        }
        if newly_authorized_waves != {current_wave}:
            raise ValueError(
                "v3 boundary dispatch must authorize exactly one new frozen wave"
            )
        frozen_wave_ids = [
            assignment["slot_id"]
            for assignment in plan["assignments"]
            if int(
                _BOUNDARY_SLOT_PATTERN.fullmatch(
                    assignment["slot_id"]
                ).group(1)
            )
            == current_wave
        ]
        current_wave_ids = [
            slot_id
            for slot_id, wave in zip(
                selected_slot_ids, selected_waves, strict=True
            )
            if wave == current_wave
        ]
        expected_selected_ids = [
            assignment["slot_id"]
            for assignment in plan["assignments"]
            if int(
                _BOUNDARY_SLOT_PATTERN.fullmatch(
                    assignment["slot_id"]
                ).group(1)
            )
            <= current_wave
        ]
        if (
            slot_ids != current_wave_ids
            or slot_ids != frozen_wave_ids
            or selected_slot_ids != expected_selected_ids
        ):
            raise ValueError(
                "v3 boundary dispatch must preserve one complete current wave and its cumulative prefix"
            )
    elif slot_ids != selected_slot_ids:
        raise ValueError(
            "v3 dispatch slots must equal the validated selected prefix"
        )
    jobs = payload.get("synthetic_replicate_jobs")
    if not isinstance(jobs, list) or len(jobs) != len(slot_ids):
        raise ValueError(
            "v3 jobs must exactly cover the newly authorized dispatch slots"
        )
    if not all(isinstance(job, Mapping) for job in jobs):
        raise ValueError("v3 jobs must contain only objects")
    canonical_cores = canonical_v3_dispatch_cores(
        authority=validated_authority,
        dispatch_authority=dispatch_authority,
        allocation_plan=plan,
        selected_slot_ids=slot_ids,
    )
    for field in (
        "synthetic_replicate_id",
        "response_id",
        "dispatch_id",
    ):
        identities = [job.get(field) for job in jobs]
        if (
            not all(isinstance(value, str) and value for value in identities)
            or len(identities) != len(set(identities))
        ):
            raise ValueError(
                f"v3 jobs must contain unique non-empty {field} values"
            )
    assignments = {
        assignment["slot_id"]: assignment
        for assignment in plan["assignments"]
    }
    profiles = {
        profile["grounded_profile_id"]: profile
        for profile in envelope["grounded_context_profiles"]
    }
    if [job.get("audience_slot_id") for job in jobs] != slot_ids:
        raise ValueError(
            "v3 jobs must preserve newly authorized audience slot order"
        )
    for index, (job, canonical_core) in enumerate(
        zip(jobs, canonical_cores, strict=True)
    ):
        if job.get("study_id") != payload.get("study_id"):
            raise ValueError(
                f"v3 job[{index}] study_id must match the jobs envelope"
            )
        assignment = assignments.get(job["audience_slot_id"])
        if assignment is None:
            raise ValueError(f"v3 job[{index}] references an unknown frozen slot")
        profile = profiles.get(assignment["grounded_profile_id"])
        if profile is None:
            raise ValueError(
                f"v3 job[{index}] assignment references an unknown canonical profile"
            )
        if (
            profile.get("reported_segment_id")
            != assignment["reported_segment_id"]
            or profile.get("segment_id")
            != assignment["reported_segment_id"]
            or profile.get("structural_group_id")
            != assignment["structural_group_id"]
            or profile.get("profile_snapshot_sha256")
            != assignment["profile_snapshot_sha256"]
        ):
            raise ValueError(
                f"v3 job[{index}] frozen assignment does not match its canonical profile"
            )
        slot_id = assignment["slot_id"]
        expected_identity = {
            "synthetic_replicate_id": slot_id,
            "response_id": f"{expected_record_type}-{slot_id}",
            "dispatch_id": f"dispatch-{expected_record_type}-{slot_id}",
        }
        for field, expected in expected_identity.items():
            if job.get(field) != expected:
                raise ValueError(
                    f"v3 job[{index}] {field} does not match the frozen dispatch core"
                )
        for field in ("segment_id", "variation_ids", "shown_order"):
            if job.get(field) != canonical_core[field]:
                raise ValueError(
                    f"v3 job[{index}] {field} does not match the frozen dispatch core"
                )
        expected_blind_labels = {
            creative_id: chr(ord("A") + position)
            for position, creative_id in enumerate(
                canonical_core["shown_order"]
            )
        }
        if job.get("blind_labels") != expected_blind_labels:
            raise ValueError(
                f"v3 job[{index}] blind_labels do not match the frozen shown order"
            )
        if (
            plan["stage"] == "screening"
            and job.get("context_stratum_id")
            != canonical_core.get("context_stratum_id")
        ):
            raise ValueError(
                f"v3 job[{index}] context_stratum_id does not match the frozen screening core"
            )
        for field in (
            "inclusion_probability",
            "pair_assignment_id",
            "boundary_wave",
            "assigned_variation_ids",
        ):
            if field in canonical_core:
                if job.get(field) != canonical_core[field]:
                    raise ValueError(
                        f"v3 job[{index}] {field} does not match the frozen dispatch core"
                    )
            elif field in job:
                raise ValueError(
                    f"v3 job[{index}] contains unexpected frozen-core field {field}"
                )
        for field in (
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            if job.get(field) != assignment[field]:
                raise ValueError(
                    f"v3 job[{index}] {field} does not match the frozen assignment"
                )
        if job.get("segment_id") != assignment["reported_segment_id"]:
            raise ValueError(
                f"v3 job[{index}] segment_id does not match the frozen assignment"
            )
        for field in (
            "profile_snapshot",
            "context_stratum_id",
            "persona_archetype_id",
            "context_attribute_provenance",
        ):
            if job.get(field) != profile.get(field):
                raise ValueError(
                    f"v3 job[{index}] {field} does not match the canonical profile"
                )
        errors = validate_job(job)
        if errors:
            raise ValueError(
                f"v3 job[{index}] violates the existing job contract: "
                + "; ".join(errors)
            )
    return copy.deepcopy(dict(payload))


def validate_manifest(payload: Mapping[str, Any]) -> list[str]:
    """Return public manifest-contract violations, preserving explicit validity caveats."""

    errors: list[str] = []
    required = (
        "study_id",
        "study_version",
        "creative_format",
        "method",
        "requested_shortlist_size",
        "maximum_synthetic_panelists",
        "synthetic_replicate_capacity",
        "audience_lock",
        "assignment",
        "model",
        "runtime",
        "outputs",
        "external_validity",
        "validity_status",
        "validity_reasons",
    )
    for key in required:
        if key not in payload:
            errors.append(f"missing manifest field: {key}")

    creative_format = payload.get("creative_format")
    if not isinstance(creative_format, str) or creative_format not in SUPPORTED_CREATIVE_FORMATS:
        errors.append("creative_format must name exactly one supported format")

    method = payload.get("method")
    if method not in SUPPORTED_METHODS:
        errors.append("method must be complete_exposure or partial_exposure_maxdiff")

    assignment = payload.get("assignment")
    audience_lock = payload.get("audience_lock")
    audience_package = payload.get("audience_package")
    present_v3_fields = _V3_PLAN_FIELDS & set(payload)
    is_v3 = (
        isinstance(audience_package, Mapping)
        and audience_package.get("schema_version")
        == "audience-panel-package-v3"
    )
    if is_v3:
        if present_v3_fields != _V3_PLAN_FIELDS:
            errors.append(
                "v3 manifests require audience_profile_rosters, audience_allocation_fidelity, and audience_run_claim together"
            )
        else:
            errors.extend(_validate_v3_plan_fields(payload))
    elif present_v3_fields:
        errors.append("v1/v2 manifests cannot contain v3 audience allocation fields")
    has_v2_lock = (
        isinstance(audience_lock, Mapping)
        and set(audience_lock) == _V2_AUDIENCE_LOCK_KEYS
    )
    if audience_package is None and (
        has_v2_lock or "grounded_context_profiles" in payload
    ):
        errors.append(
            "v2 audience fields require audience_package; stripped bindings are not legacy read-only data"
        )
    if audience_package is not None:
        if is_v3:
            if set(audience_package) != _V3_AUDIENCE_PACKAGE_KEYS:
                errors.append(
                    "audience_package keys must exactly match the v3 binding allowlist"
                )
            else:
                for key in (
                    "generator_version",
                    "panel_id",
                    "panel_version",
                    "tier",
                    "evidence_basis",
                ):
                    if (
                        not isinstance(audience_package.get(key), str)
                        or not audience_package[key].strip()
                    ):
                        errors.append(
                            f"audience_package.{key} must be a non-empty string"
                        )
                for key in (
                    "package_manifest_sha256",
                    "package_zip_sha256",
                ):
                    if (
                        not isinstance(audience_package.get(key), str)
                        or not _BARE_SHA256_PATTERN.fullmatch(
                            audience_package[key]
                        )
                    ):
                        errors.append(
                            f"audience_package.{key} must be a lowercase SHA-256 digest"
                        )
            if (
                not isinstance(audience_lock, Mapping)
                or set(audience_lock) != _V2_AUDIENCE_LOCK_KEYS
            ):
                errors.append(
                    "audience_lock keys must exactly match the v3 resolved allowlist"
                )
            else:
                for key in ("panel_id", "panel_version"):
                    if audience_lock.get(key) != audience_package.get(key):
                        errors.append(
                            f"audience_lock.{key} must match audience_package.{key}"
                        )
        elif not isinstance(audience_package, Mapping) or set(audience_package) != _AUDIENCE_PACKAGE_KEYS:
            errors.append("audience_package keys must exactly match the v2 binding allowlist")
        else:
            for key in ("panel_id", "panel_version", "brief_id"):
                if not isinstance(audience_package.get(key), str) or not audience_package[key].strip():
                    errors.append(f"audience_package.{key} must be a non-empty string")
            for key in (
                "panel_sha256", "brief_sha256", "package_manifest_sha256",
                "package_zip_sha256",
            ):
                if not isinstance(audience_package.get(key), str) or not _BARE_SHA256_PATTERN.fullmatch(audience_package[key]):
                    errors.append(f"audience_package.{key} must be a lowercase SHA-256 digest")
            for key in (
                "panel_byte_count", "brief_byte_count", "package_manifest_byte_count",
                "package_zip_byte_count",
            ):
                if (
                    isinstance(audience_package.get(key), bool)
                    or not isinstance(audience_package.get(key), int)
                    or audience_package[key] < 0
                ):
                    errors.append(f"audience_package.{key} must be a non-negative integer")
            if audience_package.get("resolved_snapshot_path") != "audience/snapshot":
                errors.append("audience_package.resolved_snapshot_path must be audience/snapshot")
            if not isinstance(audience_lock, Mapping) or set(audience_lock) != _V2_AUDIENCE_LOCK_KEYS:
                errors.append("audience_lock keys must exactly match the v2 resolved allowlist")
            else:
                identity_pairs = (
                    ("panel_id", "panel_id"),
                    ("panel_version", "panel_version"),
                    ("persona_research_brief_id", "brief_id"),
                )
                for lock_key, package_key in identity_pairs:
                    if audience_lock.get(lock_key) != audience_package.get(package_key):
                        errors.append(
                            f"audience_lock.{lock_key} must match audience_package.{package_key}"
                        )
    context_strata = (
        assignment.get("context_strata") if isinstance(assignment, Mapping) else None
    )
    if context_strata is not None:
        if (
            not isinstance(context_strata, list)
            or not context_strata
            or not all(isinstance(item, Mapping) for item in context_strata)
        ):
            errors.append(
                "assignment.context_strata must be a non-empty array of objects when supplied"
            )
        else:
            context_keys: list[tuple[str, str]] = []
            for index, item in enumerate(context_strata):
                segment_id = item.get("segment_id")
                context_stratum_id = item.get("context_stratum_id")
                if not isinstance(segment_id, str) or not segment_id.strip():
                    errors.append(
                        f"assignment.context_strata[{index}].segment_id is required"
                    )
                if (
                    not isinstance(context_stratum_id, str)
                    or not context_stratum_id.strip()
                ):
                    errors.append(
                        f"assignment.context_strata[{index}].context_stratum_id is required"
                    )
                if (
                    isinstance(segment_id, str)
                    and segment_id.strip()
                    and isinstance(context_stratum_id, str)
                    and context_stratum_id.strip()
                ):
                    context_keys.append((segment_id, context_stratum_id))
            if len(set(context_keys)) != len(context_keys):
                errors.append(
                    "assignment.context_stratum_id values must be unique within each segment"
                )
            segment_weights = (
                audience_lock.get("segment_weights")
                if isinstance(audience_lock, Mapping)
                else None
            )
            context_segments = {segment_id for segment_id, _ in context_keys}
            if (
                not isinstance(segment_weights, Mapping)
                or context_segments != set(segment_weights)
            ):
                errors.append(
                    "assignment.context_strata must exactly cover audience_lock.segment_weights"
                )

    retired_key = "arti" + "facts"
    if retired_key in payload:
        errors.append("retired manifest key is not allowed; use outputs")

    outputs = payload.get("outputs", {})
    if not isinstance(outputs, Mapping):
        errors.append("outputs must be an object")
    else:
        creative_asset_hashes = outputs.get("creative_asset_hashes")
        if not isinstance(creative_asset_hashes, Mapping):
            errors.append("outputs.creative_asset_hashes must be an object")
        elif len(creative_asset_hashes) > 100:
            errors.append("outputs.creative_asset_hashes must contain at most 100 creatives")
        else:
            for creative_id, content_hash in creative_asset_hashes.items():
                if not isinstance(creative_id, str) or not creative_id.strip():
                    errors.append(
                        "outputs.creative_asset_hashes keys must be non-empty strings"
                    )
                    break
                if not isinstance(content_hash, str) or not content_hash.strip():
                    errors.append(
                        "outputs.creative_asset_hashes values must be non-empty strings"
                    )
                    break

        for name, canonical_path in _LINEAGE_OUTPUTS.items():
            binding = outputs.get(name)
            if binding is None:
                continue
            if not isinstance(binding, Mapping):
                errors.append(f"outputs.{name} must be an object")
                continue
            if binding.get("path") != canonical_path:
                errors.append(f"outputs.{name}.path must be {canonical_path}")
            content_hash = binding.get("content_hash")
            if not isinstance(content_hash, str) or not _SHA256_PATTERN.fullmatch(
                content_hash
            ):
                errors.append(f"outputs.{name}.content_hash must be a SHA-256 hash")
            record_count = binding.get("record_count")
            if (
                not isinstance(record_count, int)
                or isinstance(record_count, bool)
                or record_count < 0
            ):
                errors.append(
                    f"outputs.{name}.record_count must be a non-negative integer"
                )

    runtime = payload.get("runtime", {})
    if not isinstance(runtime, Mapping):
        errors.append("runtime must be an object")
    else:
        retry_limit = runtime.get("retry_limit_per_return")
        if (
            isinstance(retry_limit, bool)
            or not isinstance(retry_limit, int)
            or retry_limit != 1
        ):
            errors.append(
                "runtime.retry_limit_per_return must equal the supported policy of 1"
            )

    external = payload.get("external_validity", {})
    if not isinstance(external, Mapping):
        errors.append("external_validity must be an object")
    elif external.get("human_alignment_validation") not in {
        "not_evaluated",
        "evaluated_with_limitations",
        "calibrated",
    }:
        errors.append("external_validity.human_alignment_validation is invalid")
    if payload.get("validity_status") not in {status.value for status in ValidityStatus}:
        errors.append("validity_status is invalid")
    return errors


def validate_base_response(payload: Mapping[str, Any]) -> list[str]:
    """Return base synthetic-response violations shared by every response stage."""

    errors: list[str] = []
    required = (
        "study_id",
        "response_id",
        "record_type",
        "synthetic_replicate_id",
        "reviewer_dispatch_id",
        "persona_archetype_id",
        "segment_id",
        "profile_snapshot",
        "context_attribute_provenance",
        "worker_context_isolation",
        "human_sample_independence",
        "assigned_variation_ids",
        "blind_labels",
        "shown_order",
        "reaction_protocol",
        "runtime_attempts",
        "validation",
    )
    for key in required:
        if key not in payload:
            errors.append(f"missing response field: {key}")
    if payload.get("human_sample_independence") is not False:
        errors.append("human_sample_independence must be false")
    if payload.get("worker_context_isolation") not in {"isolated", "shared_context_fallback"}:
        errors.append("worker_context_isolation is invalid")
    if payload.get("reaction_protocol") not in {"progressive_reveal", "reflective_reaction_caveat"}:
        errors.append("reaction_protocol is invalid")
    return errors
