"""Strict v3 population, composition, validity, and feedback contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from .audience_research import (
    AudienceResearchValidationError,
    RESEARCH_BRIEF_SCHEMA_VERSION,
    SAVED_PANEL_SCHEMA_VERSION,
    require_valid_audience_research_pair,
    validate_research_brief,
    validate_saved_panel,
)


FRAME_REQUEST_VERSION = "audience-frame-request-v1"
OBSERVATION_BATCH_VERSION = "audience-frame-observation-batch-v1"
POPULATION_FRAME_VERSION = "audience-population-frame-v1"
COMPOSITION_PLAN_VERSION = "panel-composition-plan-v1"
RESEARCH_BRIEF_V3 = "audience-research-brief-v3"
SAVED_PANEL_V3 = "saved-audience-panel-v3"
OUTCOME_FEEDBACK_VERSION = "panel-outcome-feedback-v1"
VALIDITY_PROFILE_VERSION = "panel-validity-profile-v1"

PANEL_TIERS = {"tier_1", "tier_2", "tier_3", "tier_4"}
GENERIC_SYNTHETIC_AD_TESTING_USE = "Synthetic ad testing"
PUBLIC_PROXY_SYNTHETIC_AD_TESTING_USE = (
    "Directional synthetic ad testing under the named public proxy boundary"
)
AUTHORIZED_COHORT_SYNTHETIC_AD_TESTING_USE = (
    "Synthetic ad testing for the exact authorized aggregate cohort"
)
STRUCTURAL_SOURCE_REQUIRED_USES = frozenset(
    {"audience-composition", "population-framing"}
)
EVIDENCE_BASES = {
    "public",
    "licensed_aggregate",
    "first_party_aggregate",
    "hybrid",
    "none",
}
CELL_STATUSES = {"observed", "derived", "modeled", "missing"}
FRAME_ELIGIBILITY = {
    "eligible_tier_2",
    "eligible_tier_3",
    "experimental",
    "no_defensible_frame",
}
VALIDITY_AXIS_STATUSES = {
    "not_available",
    "insufficient",
    "directional",
    "supported",
    "held_out_validated",
}

WEIGHT_SEMANTICS = {
    "population_weight",
    "authorized_cohort_weight",
    "planning_allocation",
    "experimental_modeled_weight",
}
VALIDITY_AXES = {
    "structural_frame",
    "overlay_evidence",
    "allocation_fidelity",
    "outcome_calibration",
    "external_validation",
}
_STANDALONE_PANEL_DEFERRED_CODES = {
    "finding_evidence_mismatch",
    "unresolved_finding",
}
_FORBIDDEN_VALIDITY_FIELDS = {
    "confidence",
    "confidence_score",
    "overall_score",
    "composite",
    "percentage",
}
_PROHIBITED_OUTCOME_FIELDS = {
    "score",
    "rank",
    "profile_weight",
    "frame_weight",
    "panel_version",
}
_PROHIBITED_COMPOSITION_FIELDS = {
    "study_quota",
    "study_quotas",
    "quota",
    "quota_count",
    "slot",
    "slots",
    "slot_count",
    "panelist_count",
    "capacity",
    "capacity_ceiling",
    "maximum_synthetic_panelists",
    "requested_capacity",
}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BARE_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TOLERANCE = 1e-9

_V2_BRIEF_KEYS = {
    "schema_version",
    "brief_id",
    "created_at",
    "updated_at",
    "status",
    "target_audience",
    "research_mode",
    "research_depth",
    "research_questions",
    "evidence_sources",
    "findings",
    "coverage",
    "segment_hypotheses",
    "evidence_gaps",
    "privacy_confirmation",
    "approval",
}
_V3_BRIEF_EXTENSION_KEYS = {
    "panel_tier",
    "evidence_basis",
    "workflow_state_binding",
    "population_frame_result_sha256",
    "population_frame_sha256",
    "authorized_audience_import",
    "structural_findings",
    "overlay_findings",
    "claim_boundary",
    "dimensional_validity",
    "scoped_approvals",
}
_V2_PANEL_KEYS = {
    "schema_version",
    "panel_id",
    "panel_name",
    "version",
    "created_at",
    "updated_at",
    "audience_scope",
    "persona_research",
    "segments",
    "persona_archetypes",
    "context_strata",
    "grounded_context_profiles",
    "replicate_strategy",
    "calibration_history",
    "refresh_conditions",
    "governance",
}
_V3_PANEL_EXTENSION_KEYS = {
    "panel_tier",
    "evidence_basis",
    "brief_id",
    "population_frame_result_sha256",
    "population_frame_sha256",
    "composition_plan_sha256",
    "validity_profile_sha256",
    "authorized_handoff_sha256",
    "audit_binding",
    "claim_boundary",
    "package_status",
}


class AudienceResearchV3ValidationError(ValueError):
    """Raised when a v3 population or panel contract is invalid."""


def _fail(path: str, message: str) -> None:
    raise AudienceResearchV3ValidationError(f"{path} {message}")


def _object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    unknown = sorted(set(value) - keys, key=str)
    missing = sorted(keys - set(value))
    if unknown:
        _fail(path, "has unknown fields: " + ", ".join(map(str, unknown)))
    if missing:
        _fail(path, "is missing fields: " + ", ".join(missing))
    return dict(value)


def _array(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if nonempty and not value:
        _fail(path, "must not be empty")
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not allow_empty and not value.strip():
        _fail(path, "must not be empty")
    return value


def _identifier(value: Any, path: str) -> str:
    text = _string(value, path)
    if not _IDENTIFIER.fullmatch(text):
        _fail(path, "must be a lowercase hyphenated identifier")
    return text


def _version(value: Any, path: str) -> str:
    text = _string(value, path)
    if not _VERSION.fullmatch(text):
        _fail(path, "must be a semantic version")
    return text


def _enum(value: Any, allowed: set[str], path: str) -> str:
    text = _string(value, path)
    if text not in allowed:
        _fail(path, "must be one of: " + ", ".join(sorted(allowed)))
    return text


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be boolean")
    return value


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")
    return value


def _number(
    value: Any,
    path: str,
    *,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be numeric")
    result = float(value)
    if not math.isfinite(result):
        _fail(path, "must be finite")
    if minimum is not None and result < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and result > maximum:
        _fail(path, f"must be at most {maximum}")
    return result


def _date(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        date.fromisoformat(text)
    except ValueError:
        _fail(path, "must be an ISO-8601 calendar date")
    return text


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(path, "must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _fail(path, "must include a timezone")
    return text


def _digest(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    text = _string(value, path)
    if not _DIGEST.fullmatch(text):
        _fail(path, "must be a sha256: lowercase SHA-256 digest")
    return text


def _bare_digest(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    text = _string(value, path)
    if not _BARE_DIGEST.fullmatch(text):
        _fail(path, "must be a lowercase SHA-256 digest")
    return text


def _string_list(
    value: Any,
    path: str,
    *,
    nonempty: bool = False,
    identifiers: bool = False,
) -> list[str]:
    items = _array(value, path, nonempty=nonempty)
    result = [
        (
            _identifier(item, f"{path}[{index}]")
            if identifiers
            else _string(item, f"{path}[{index}]")
        )
        for index, item in enumerate(items)
    ]
    if len(result) != len(set(result)):
        _fail(path, "must contain unique values")
    return result


def _reconcile(values: list[float], path: str) -> None:
    if abs(math.fsum(values) - 1.0) > _TOLERANCE:
        _fail(path, "weights must reconcile to 1.0 ± 1e-9")


def _semantic(value: Any, path: str) -> str:
    try:
        return _enum(value, WEIGHT_SEMANTICS, path)
    except AudienceResearchV3ValidationError:
        _fail(path, "must carry one allowed weight semantic")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _fail("$", "must contain only finite recursive JSON values")
    return (text + "\n").encode("utf-8")


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _forbid_recursive_fields(
    value: Any,
    forbidden: set[str],
    path: str,
    *,
    message: str = "is a forbidden validity field",
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key in forbidden:
                _fail(f"{path}.{key}", message)
            _forbid_recursive_fields(
                child,
                forbidden,
                f"{path}.{key}",
                message=message,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_recursive_fields(
                child,
                forbidden,
                f"{path}[{index}]",
                message=message,
            )


def _validate_time_basis(value: Any, path: str) -> dict[str, object]:
    item = _object(value, {"as_of", "lookback_days"}, path)
    return {
        "as_of": _date(item["as_of"], f"{path}.as_of"),
        "lookback_days": _integer(
            item["lookback_days"], f"{path}.lookback_days", minimum=0
        ),
    }


def validate_frame_request(payload: object) -> dict[str, object]:
    """Validate one source-neutral request for a defensible population frame."""

    keys = {
        "schema_version",
        "request_id",
        "target_audience",
        "decision",
        "desired_claim",
        "geography",
        "time_basis",
        "target_unit",
        "proxy_universes",
        "required_dimensions",
        "required_joints",
        "modeled_cell_rules",
        "calibration_rules",
        "exclusions",
        "authorized_evidence_bases",
        "available_capabilities",
        "downgrade_policy",
    }
    request = _object(payload, keys, "$")
    if request["schema_version"] != FRAME_REQUEST_VERSION:
        _fail("$.schema_version", f"must equal {FRAME_REQUEST_VERSION}")
    dimensions = _string_list(
        request["required_dimensions"],
        "$.required_dimensions",
        nonempty=True,
        identifiers=True,
    )
    joints: list[list[str]] = []
    for index, raw_joint in enumerate(
        _array(request["required_joints"], "$.required_joints")
    ):
        joint = _string_list(
            raw_joint,
            f"$.required_joints[{index}]",
            nonempty=True,
            identifiers=True,
        )
        if len(joint) < 2:
            _fail(f"$.required_joints[{index}]", "must contain at least two dimensions")
        if not set(joint).issubset(dimensions):
            _fail(f"$.required_joints[{index}]", "contains an undeclared dimension")
        joints.append(joint)
    proxy_universes = []
    proxy_keys = {
        "universe_id",
        "description",
        "unit",
        "denominator",
        "exact",
    }
    for index, raw_proxy in enumerate(
        _array(request["proxy_universes"], "$.proxy_universes")
    ):
        path = f"$.proxy_universes[{index}]"
        proxy = _object(raw_proxy, proxy_keys, path)
        proxy_universes.append({
            "universe_id": _identifier(proxy["universe_id"], f"{path}.universe_id"),
            "description": _string(proxy["description"], f"{path}.description"),
            "unit": _identifier(proxy["unit"], f"{path}.unit"),
            "denominator": _identifier(proxy["denominator"], f"{path}.denominator"),
            "exact": _boolean(proxy["exact"], f"{path}.exact"),
        })
    modeled_rules = []
    modeled_rule_ids: set[str] = set()
    modeled_rule_keys = {
        "rule_id",
        "unit",
        "denominator",
        "dimension_values",
        "method",
        "structural_weight",
        "uncertainty",
        "rationale",
    }
    for index, raw_rule in enumerate(
        _array(request["modeled_cell_rules"], "$.modeled_cell_rules")
    ):
        path = f"$.modeled_cell_rules[{index}]"
        rule = _object(raw_rule, modeled_rule_keys, path)
        rule_id = _identifier(rule["rule_id"], f"{path}.rule_id")
        if rule_id in modeled_rule_ids:
            _fail(f"{path}.rule_id", "is duplicated")
        modeled_rule_ids.add(rule_id)
        modeled_rules.append({
            "rule_id": rule_id,
            "unit": _identifier(rule["unit"], f"{path}.unit"),
            "denominator": _identifier(
                rule["denominator"], f"{path}.denominator"
            ),
            "dimension_values": _dimension_values_subset(
                rule["dimension_values"],
                dimensions,
                f"{path}.dimension_values",
            ),
            "method": _enum(
                rule["method"],
                {"declared_weight"},
                f"{path}.method",
            ),
            "structural_weight": _number(
                rule["structural_weight"],
                f"{path}.structural_weight",
                minimum=0.0,
                maximum=1.0,
            ),
            "uncertainty": _validate_uncertainty(
                rule["uncertainty"],
                f"{path}.uncertainty",
                include_method=False,
            ),
            "rationale": _string(rule["rationale"], f"{path}.rationale"),
        })
    calibration_rules = []
    calibration_rule_ids: set[str] = set()
    calibration_rule_keys = {
        "rule_id",
        "unit",
        "denominator",
        "dimension_values",
        "calibration_factor",
        "rationale",
    }
    for index, raw_rule in enumerate(
        _array(request["calibration_rules"], "$.calibration_rules")
    ):
        path = f"$.calibration_rules[{index}]"
        rule = _object(raw_rule, calibration_rule_keys, path)
        rule_id = _identifier(rule["rule_id"], f"{path}.rule_id")
        if rule_id in calibration_rule_ids:
            _fail(f"{path}.rule_id", "is duplicated")
        calibration_rule_ids.add(rule_id)
        calibration_rules.append({
            "rule_id": rule_id,
            "unit": _identifier(rule["unit"], f"{path}.unit"),
            "denominator": _identifier(
                rule["denominator"], f"{path}.denominator"
            ),
            "dimension_values": _dimension_values_subset(
                rule["dimension_values"],
                dimensions,
                f"{path}.dimension_values",
            ),
            "calibration_factor": _number(
                rule["calibration_factor"],
                f"{path}.calibration_factor",
                minimum=0.0,
                maximum=3.0,
            ),
            "rationale": _string(rule["rationale"], f"{path}.rationale"),
        })
    evidence_bases = _string_list(
        request["authorized_evidence_bases"],
        "$.authorized_evidence_bases",
        nonempty=True,
    )
    for index, basis in enumerate(evidence_bases):
        _enum(basis, EVIDENCE_BASES, f"$.authorized_evidence_bases[{index}]")
    downgrade = _object(
        request["downgrade_policy"],
        {"allow_tier_1", "allow_experimental", "reason"},
        "$.downgrade_policy",
    )
    return {
        "schema_version": request["schema_version"],
        "request_id": _identifier(request["request_id"], "$.request_id"),
        "target_audience": _string(
            request["target_audience"], "$.target_audience"
        ),
        "decision": _string(request["decision"], "$.decision"),
        "desired_claim": _string(request["desired_claim"], "$.desired_claim"),
        "geography": _string_list(
            request["geography"], "$.geography", nonempty=True
        ),
        "time_basis": _validate_time_basis(request["time_basis"], "$.time_basis"),
        "target_unit": _identifier(request["target_unit"], "$.target_unit"),
        "proxy_universes": proxy_universes,
        "required_dimensions": dimensions,
        "required_joints": joints,
        "modeled_cell_rules": modeled_rules,
        "calibration_rules": calibration_rules,
        "exclusions": _string_list(request["exclusions"], "$.exclusions"),
        "authorized_evidence_bases": evidence_bases,
        "available_capabilities": _string_list(
            request["available_capabilities"],
            "$.available_capabilities",
            identifiers=True,
        ),
        "downgrade_policy": {
            "allow_tier_1": _boolean(
                downgrade["allow_tier_1"], "$.downgrade_policy.allow_tier_1"
            ),
            "allow_experimental": _boolean(
                downgrade["allow_experimental"],
                "$.downgrade_policy.allow_experimental",
            ),
            "reason": _string(
                downgrade["reason"], "$.downgrade_policy.reason"
            ),
        },
    }


def _validate_uncertainty(
    value: Any,
    path: str,
    *,
    include_method: bool,
    allow_unavailable_bounds: bool = False,
) -> dict[str, object]:
    keys = {"lower", "upper"} | ({"method"} if include_method else set())
    uncertainty = _object(value, keys, path)
    if (
        allow_unavailable_bounds
        and uncertainty["lower"] is None
        and uncertainty["upper"] is None
    ):
        result: dict[str, object] = {"lower": None, "upper": None}
        if include_method:
            result["method"] = _string(
                uncertainty["method"], f"{path}.method"
            )
        return result
    lower = _number(uncertainty["lower"], f"{path}.lower")
    upper = _number(uncertainty["upper"], f"{path}.upper")
    if lower is not None and upper is not None and lower > upper:
        _fail(path, "lower must not exceed upper")
    result: dict[str, object] = {"lower": lower, "upper": upper}
    if include_method:
        result["method"] = _string(uncertainty["method"], f"{path}.method")
    return result


def _dimension_values(
    value: Any,
    dimensions: list[str],
    path: str,
) -> dict[str, str]:
    item = _object(value, set(dimensions), path)
    return {
        dimension: _string(item[dimension], f"{path}.{dimension}")
        for dimension in dimensions
    }


def _dimension_values_subset(
    value: Any,
    dimensions: list[str],
    path: str,
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    if not value:
        _fail(path, "must not be empty")
    unknown = sorted(set(value) - set(dimensions), key=str)
    if unknown:
        _fail(
            path,
            "contains an undeclared dimension: " + ", ".join(map(str, unknown)),
        )
    return {
        _identifier(dimension, f"{path}.{dimension}"): _string(
            raw_value, f"{path}.{dimension}"
        )
        for dimension, raw_value in value.items()
    }


def validate_observation_batch(payload: object) -> dict[str, object]:
    """Validate one normalized structural observation batch."""

    keys = {
        "schema_version",
        "batch_id",
        "frame_request_id",
        "adapter_id",
        "source_family",
        "source",
        "raw_snapshot_sha256",
        "normalized_batch_sha256",
        "access",
        "geography",
        "unit",
        "denominator",
        "dimensions",
        "cells",
        "selection_notes",
        "coverage_notes",
        "citations",
    }
    batch = _object(payload, keys, "$")
    if batch["schema_version"] != OBSERVATION_BATCH_VERSION:
        _fail("$.schema_version", f"must equal {OBSERVATION_BATCH_VERSION}")
    source = _object(
        batch["source"],
        {"publisher", "program", "edition", "vintage", "retrieved_at"},
        "$.source",
    )
    access = _object(
        batch["access"],
        {"access_type", "permission_confirmed", "permitted_uses"},
        "$.access",
    )
    dimensions = _string_list(
        batch["dimensions"], "$.dimensions", nonempty=True, identifiers=True
    )
    cells = []
    cell_ids: set[str] = set()
    cell_coordinates: set[
        tuple[str, tuple[tuple[str, str], ...]]
    ] = set()
    cell_keys = {
        "cell_id",
        "dimension_values",
        "estimate",
        "uncertainty",
        "suppressed",
        "status",
        "relationship",
        "source_location",
    }
    for index, raw_cell in enumerate(
        _array(batch["cells"], "$.cells", nonempty=True)
    ):
        path = f"$.cells[{index}]"
        cell = _object(raw_cell, cell_keys, path)
        cell_id = _identifier(cell["cell_id"], f"{path}.cell_id")
        if cell_id in cell_ids:
            _fail(f"{path}.cell_id", "is duplicated")
        cell_ids.add(cell_id)
        status = _enum(cell["status"], CELL_STATUSES, f"{path}.status")
        suppressed = _boolean(cell["suppressed"], f"{path}.suppressed")
        validated_estimate = _number(
            cell["estimate"], f"{path}.estimate", nullable=True
        )
        estimate = (
            None
            if validated_estimate is None
            else cell["estimate"]
        )
        unavailable = status == "missing" or suppressed
        if unavailable and estimate is not None:
            _fail(
                f"{path}.estimate",
                "must be null for a missing or suppressed cell",
            )
        if not unavailable and estimate is None:
            _fail(f"{path}.estimate", "is required for an available cell")
        dimension_values = _dimension_values(
            cell["dimension_values"], dimensions, f"{path}.dimension_values"
        )
        relationship = _enum(
            cell["relationship"],
            {"marginal", "joint"},
            f"{path}.relationship",
        )
        coordinate = (
            relationship,
            tuple(sorted(dimension_values.items())),
        )
        if coordinate in cell_coordinates:
            _fail(path, "has a duplicate structural coordinate")
        cell_coordinates.add(coordinate)
        uncertainty = _validate_uncertainty(
            cell["uncertainty"],
            f"{path}.uncertainty",
            include_method=True,
            allow_unavailable_bounds=unavailable,
        )
        if not unavailable:
            # `finish_batch` hashes the incoming normalized JSON before this
            # validator runs. Preserve its valid integer-versus-float JSON
            # representation so the returned canonical batch retains that
            # authoritative self-hash and remains valid on a second pass.
            uncertainty["lower"] = cell["uncertainty"]["lower"]
            uncertainty["upper"] = cell["uncertainty"]["upper"]
        cells.append({
            "cell_id": cell_id,
            "dimension_values": dimension_values,
            "estimate": estimate,
            "uncertainty": uncertainty,
            "suppressed": suppressed,
            "status": status,
            "relationship": relationship,
            "source_location": _string(
                cell["source_location"], f"{path}.source_location"
            ),
        })
    canonical = {
        "schema_version": batch["schema_version"],
        "batch_id": _identifier(batch["batch_id"], "$.batch_id"),
        "frame_request_id": _identifier(
            batch["frame_request_id"], "$.frame_request_id"
        ),
        "adapter_id": _identifier(batch["adapter_id"], "$.adapter_id"),
        "source_family": _identifier(batch["source_family"], "$.source_family"),
        "source": {
            "publisher": _string(source["publisher"], "$.source.publisher"),
            "program": _string(source["program"], "$.source.program"),
            "edition": _string(source["edition"], "$.source.edition"),
            "vintage": _date(source["vintage"], "$.source.vintage"),
            "retrieved_at": _timestamp(
                source["retrieved_at"], "$.source.retrieved_at"
            ),
        },
        "raw_snapshot_sha256": _digest(
            batch["raw_snapshot_sha256"], "$.raw_snapshot_sha256"
        ),
        "normalized_batch_sha256": _digest(
            batch["normalized_batch_sha256"], "$.normalized_batch_sha256"
        ),
        "access": {
            "access_type": _enum(
                access["access_type"],
                {"public", "licensed", "authorized"},
                "$.access.access_type",
            ),
            "permission_confirmed": _boolean(
                access["permission_confirmed"], "$.access.permission_confirmed"
            ),
            "permitted_uses": _string_list(
                access["permitted_uses"],
                "$.access.permitted_uses",
                nonempty=True,
            ),
        },
        "geography": _string_list(batch["geography"], "$.geography", nonempty=True),
        "unit": _identifier(batch["unit"], "$.unit"),
        "denominator": _identifier(batch["denominator"], "$.denominator"),
        "dimensions": dimensions,
        "cells": cells,
        "selection_notes": _string(
            batch["selection_notes"], "$.selection_notes"
        ),
        "coverage_notes": _string(batch["coverage_notes"], "$.coverage_notes"),
        "citations": _string_list(
            batch["citations"], "$.citations", nonempty=True
        ),
    }
    hash_input = deepcopy(batch)
    hash_input.pop("normalized_batch_sha256", None)
    expected_normalized_hash = _sha256_json(hash_input)
    if canonical["normalized_batch_sha256"] != expected_normalized_hash:
        _fail(
            "$.normalized_batch_sha256",
            "must match the canonical batch self-hash",
        )
    return canonical


def validate_population_frame(payload: object) -> dict[str, object]:
    """Validate a reconciled structural population frame."""

    keys = {
        "schema_version",
        "frame_id",
        "frame_version",
        "built_at",
        "frame_request_id",
        "frame_request_sha256",
        "target_universe",
        "proxy_universes",
        "claim_boundary",
        "units",
        "structural_dimensions",
        "cells",
        "margins",
        "joints",
        "source_bindings",
        "coverage_assessment",
        "modeled_weight_by_dimension",
        "modeled_weight_share",
        "eligibility",
        "downgrade_reason",
    }
    frame = _object(payload, keys, "$")
    if frame["schema_version"] != POPULATION_FRAME_VERSION:
        _fail("$.schema_version", f"must equal {POPULATION_FRAME_VERSION}")
    eligibility = _enum(
        frame["eligibility"], FRAME_ELIGIBILITY, "$.eligibility"
    )
    no_frame = eligibility == "no_defensible_frame"
    dimensions = _string_list(
        frame["structural_dimensions"],
        "$.structural_dimensions",
        nonempty=True,
        identifiers=True,
    )
    units = []
    partition_ids: set[str] = set()
    unit_pairs: set[tuple[str, str]] = set()
    for index, raw_unit in enumerate(
        _array(frame["units"], "$.units", nonempty=not no_frame)
    ):
        path = f"$.units[{index}]"
        unit = _object(
            raw_unit,
            {"partition_id", "unit", "denominator", "exact"},
            path,
        )
        partition_id = _identifier(
            unit["partition_id"], f"{path}.partition_id"
        )
        if partition_id in partition_ids:
            _fail(f"{path}.partition_id", "is duplicated")
        partition_ids.add(partition_id)
        unit_name = _identifier(unit["unit"], f"{path}.unit")
        denominator = _identifier(
            unit["denominator"], f"{path}.denominator"
        )
        unit_pair = (unit_name, denominator)
        if unit_pair in unit_pairs:
            _fail(path, "duplicates a unit and denominator partition")
        unit_pairs.add(unit_pair)
        units.append({
            "partition_id": partition_id,
            "unit": unit_name,
            "denominator": denominator,
            "exact": _boolean(unit["exact"], f"{path}.exact"),
        })
    if no_frame and units:
        _fail("$.units", "must be empty for no_defensible_frame")

    cells = []
    cell_by_id: dict[str, dict[str, object]] = {}
    collection_weights: dict[
        tuple[str, str, tuple[str, ...]], list[tuple[float, str]]
    ] = {}
    cell_ids: set[str] = set()
    cell_coordinates: set[
        tuple[str, str, tuple[tuple[str, str], ...]]
    ] = set()
    cell_keys = {
        "cell_id",
        "partition_id",
        "dimension_values",
        "relationship",
        "origin",
        "modeled_rule_id",
        "status",
        "structural_weight",
        "weight_semantic",
        "uncertainty",
        "suppressed",
        "source_observations",
        "calibration_factor",
    }
    for index, raw_cell in enumerate(
        _array(frame["cells"], "$.cells", nonempty=not no_frame)
    ):
        path = f"$.cells[{index}]"
        cell = _object(raw_cell, cell_keys, path)
        cell_id = _identifier(cell["cell_id"], f"{path}.cell_id")
        if cell_id in cell_ids:
            _fail(f"{path}.cell_id", "is duplicated")
        cell_ids.add(cell_id)
        partition_id = _identifier(
            cell["partition_id"], f"{path}.partition_id"
        )
        if partition_id not in partition_ids:
            _fail(f"{path}.partition_id", "does not resolve to a frame unit")
        dimension_values = _dimension_values_subset(
            cell["dimension_values"],
            dimensions,
            f"{path}.dimension_values",
        )
        relationship = _enum(
            cell["relationship"], {"marginal", "joint"}, f"{path}.relationship"
        )
        coordinate = (
            partition_id,
            relationship,
            tuple(sorted(dimension_values.items())),
        )
        if coordinate in cell_coordinates:
            _fail(path, "has a duplicate structural coordinate")
        cell_coordinates.add(coordinate)
        if relationship == "marginal" and len(dimension_values) != 1:
            _fail(
                f"{path}.dimension_values",
                "marginal cells require exactly one dimension",
            )
        if relationship == "joint" and len(dimension_values) < 2:
            _fail(
                f"{path}.dimension_values",
                "joint cells require at least two dimensions",
            )
        origin = _enum(
            cell["origin"],
            {"source_observation", "modeled_rule", "explicit_missing"},
            f"{path}.origin",
        )
        status = _enum(cell["status"], CELL_STATUSES, f"{path}.status")
        suppressed = _boolean(cell["suppressed"], f"{path}.suppressed")
        unavailable = status == "missing" or suppressed
        if unavailable:
            if cell["structural_weight"] is not None:
                _fail(
                    f"{path}.structural_weight",
                    "must be null for a missing or suppressed cell",
                )
            if cell["weight_semantic"] is not None:
                _fail(
                    f"{path}.weight_semantic",
                    "must be null for a missing or suppressed cell",
                )
            if cell["calibration_factor"] is not None:
                _fail(
                    f"{path}.calibration_factor",
                    "must be null for a missing or suppressed cell",
                )
            weight = None
            semantic = None
            calibration = None
        else:
            weight = _number(
                cell["structural_weight"],
                f"{path}.structural_weight",
                minimum=0.0,
                maximum=1.0,
            )
            semantic = _semantic(
                cell["weight_semantic"], f"{path}.weight_semantic"
            )
            calibration = _number(
                cell["calibration_factor"],
                f"{path}.calibration_factor",
                minimum=0.0,
            )
            assert weight is not None
            collection_key = (
                partition_id,
                relationship,
                tuple(sorted(dimension_values)),
            )
            collection_weights.setdefault(collection_key, []).append(
                (weight, status)
            )
        if (
            not unavailable
            and status == "modeled"
            and semantic != "experimental_modeled_weight"
        ):
            _fail(
                f"{path}.weight_semantic",
                "modeled cells require experimental_modeled_weight",
            )
        modeled_rule_id = cell["modeled_rule_id"]
        if origin == "modeled_rule":
            modeled_rule_id = _identifier(
                modeled_rule_id, f"{path}.modeled_rule_id"
            )
            if status != "modeled":
                _fail(
                    f"{path}.status",
                    "modeled_rule origin requires modeled status",
                )
        elif modeled_rule_id is not None:
            _fail(
                f"{path}.modeled_rule_id",
                "must be null unless origin is modeled_rule",
            )
        if origin == "explicit_missing" and status != "missing":
            _fail(
                f"{path}.status",
                "explicit_missing origin requires missing status",
            )
        source_observations = []
        source_observation_ids: set[tuple[str, str]] = set()
        for source_index, raw_source in enumerate(
            _array(
                cell["source_observations"],
                f"{path}.source_observations",
                nonempty=origin == "source_observation",
            )
        ):
            source_path = f"{path}.source_observations[{source_index}]"
            source = _object(
                raw_source, {"batch_id", "cell_id"}, source_path
            )
            source_identity = (
                _identifier(source["batch_id"], f"{source_path}.batch_id"),
                _identifier(source["cell_id"], f"{source_path}.cell_id"),
            )
            if source_identity in source_observation_ids:
                _fail(source_path, "duplicates a source observation identity")
            source_observation_ids.add(source_identity)
            source_observations.append({
                "batch_id": source_identity[0],
                "cell_id": source_identity[1],
            })
        if origin != "source_observation" and source_observations:
            _fail(
                f"{path}.source_observations",
                "must be empty unless origin is source_observation",
            )
        uncertainty = _validate_uncertainty(
            cell["uncertainty"],
            f"{path}.uncertainty",
            include_method=False,
            allow_unavailable_bounds=unavailable,
        )
        if unavailable and (
            uncertainty["lower"] is not None
            or uncertainty["upper"] is not None
        ):
            _fail(
                f"{path}.uncertainty",
                "bounds must both be null for a missing or suppressed cell",
            )
        canonical_cell = {
            "cell_id": cell_id,
            "partition_id": partition_id,
            "dimension_values": dimension_values,
            "relationship": relationship,
            "origin": origin,
            "modeled_rule_id": modeled_rule_id,
            "status": status,
            "structural_weight": weight,
            "weight_semantic": semantic,
            "uncertainty": uncertainty,
            "suppressed": suppressed,
            "source_observations": source_observations,
            "calibration_factor": calibration,
        }
        cells.append(canonical_cell)
        cell_by_id[cell_id] = canonical_cell
    if no_frame and cells:
        _fail("$.cells", "must be empty for no_defensible_frame")
    for collection_key, collection in collection_weights.items():
        _reconcile(
            [weight for weight, _status in collection],
            "$.cells.structural_weight"
            f"[partition={collection_key[0]},relationship={collection_key[1]},"
            f"dimensions={','.join(collection_key[2])}]",
        )

    assigned_cells: set[str] = set()
    collection_signatures: set[tuple[str, tuple[str, ...]]] = set()

    def validate_collection_records(
        raw_records: Any,
        path: str,
        relationship: str,
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for index, raw_record in enumerate(_array(raw_records, path)):
            item_path = f"{path}[{index}]"
            record = _object(
                raw_record,
                {"partition_id", "dimensions", "cell_ids", "missing_reason"},
                item_path,
            )
            partition_id = _identifier(
                record["partition_id"], f"{item_path}.partition_id"
            )
            if partition_id not in partition_ids:
                _fail(
                    f"{item_path}.partition_id",
                    "does not resolve to a frame unit",
                )
            record_dimensions = _string_list(
                record["dimensions"],
                f"{item_path}.dimensions",
                nonempty=True,
                identifiers=True,
            )
            if not set(record_dimensions).issubset(dimensions):
                _fail(
                    f"{item_path}.dimensions",
                    "contains an undeclared dimension",
                )
            if relationship == "marginal" and len(record_dimensions) != 1:
                _fail(
                    f"{item_path}.dimensions",
                    "a margin requires exactly one dimension",
                )
            if relationship == "joint" and len(record_dimensions) < 2:
                _fail(
                    f"{item_path}.dimensions",
                    "a joint requires at least two dimensions",
                )
            signature = (partition_id, tuple(sorted(record_dimensions)))
            if signature in collection_signatures:
                _fail(item_path, "duplicates a partitioned dimension collection")
            collection_signatures.add(signature)
            record_cell_ids = _string_list(
                record["cell_ids"],
                f"{item_path}.cell_ids",
                identifiers=True,
            )
            if assigned_cells.intersection(record_cell_ids):
                _fail(
                    f"{item_path}.cell_ids",
                    "frame cells may belong to only one collection",
                )
            missing_reason_raw = record["missing_reason"]
            missing_reason = (
                None
                if missing_reason_raw is None
                else _string(
                    missing_reason_raw, f"{item_path}.missing_reason"
                )
            )
            unavailable_only = True
            for cell_id in record_cell_ids:
                if cell_id not in cell_by_id:
                    _fail(
                        f"{item_path}.cell_ids",
                        "contains a cell absent from the frame",
                    )
                referenced = cell_by_id[cell_id]
                if (
                    referenced["partition_id"] != partition_id
                    or referenced["relationship"] != relationship
                    or set(referenced["dimension_values"]) != set(record_dimensions)
                ):
                    _fail(
                        f"{item_path}.cell_ids",
                        "must preserve partition, relationship, and dimensions",
                    )
                if (
                    referenced["status"] != "missing"
                    and not referenced["suppressed"]
                ):
                    unavailable_only = False
            if unavailable_only and missing_reason is None:
                _fail(
                    f"{item_path}.missing_reason",
                    "is required for an unavailable collection",
                )
            assigned_cells.update(record_cell_ids)
            records.append({
                "partition_id": partition_id,
                "dimensions": record_dimensions,
                "cell_ids": record_cell_ids,
                "missing_reason": missing_reason,
            })
        return records

    margins = validate_collection_records(frame["margins"], "$.margins", "marginal")
    joints = validate_collection_records(frame["joints"], "$.joints", "joint")
    if assigned_cells != cell_ids:
        _fail("$.margins", "and $.joints must bind every frame cell exactly once")
    if no_frame and (margins or joints):
        _fail("$.margins", "and $.joints must be empty for no_defensible_frame")

    source_bindings = []
    seen_batches: set[str] = set()
    batch_partitions: dict[str, str] = {}
    for index, raw_binding in enumerate(
        _array(
            frame["source_bindings"],
            "$.source_bindings",
            nonempty=not no_frame,
        )
    ):
        path = f"$.source_bindings[{index}]"
        binding = _object(
            raw_binding,
            {
                "batch_id",
                "normalized_batch_sha256",
                "raw_snapshot_sha256",
                "partition_id",
                "source",
                "geography",
                "access",
                "selection_notes",
                "coverage_notes",
            },
            path,
        )
        batch_id = _identifier(binding["batch_id"], f"{path}.batch_id")
        if batch_id in seen_batches:
            _fail(f"{path}.batch_id", "is duplicated")
        seen_batches.add(batch_id)
        partition_id = _identifier(
            binding["partition_id"], f"{path}.partition_id"
        )
        if partition_id not in partition_ids:
            _fail(f"{path}.partition_id", "does not resolve to a frame unit")
        batch_partitions[batch_id] = partition_id
        source = _object(
            binding["source"],
            {"publisher", "program", "edition", "vintage", "retrieved_at"},
            f"{path}.source",
        )
        access = _object(
            binding["access"],
            {"access_type", "permission_confirmed", "permitted_uses"},
            f"{path}.access",
        )
        source_bindings.append({
            "batch_id": batch_id,
            "normalized_batch_sha256": _digest(
                binding["normalized_batch_sha256"],
                f"{path}.normalized_batch_sha256",
            ),
            "raw_snapshot_sha256": _digest(
                binding["raw_snapshot_sha256"],
                f"{path}.raw_snapshot_sha256",
            ),
            "partition_id": partition_id,
            "source": {
                "publisher": _string(
                    source["publisher"], f"{path}.source.publisher"
                ),
                "program": _string(
                    source["program"], f"{path}.source.program"
                ),
                "edition": _string(
                    source["edition"], f"{path}.source.edition"
                ),
                "vintage": _date(
                    source["vintage"], f"{path}.source.vintage"
                ),
                "retrieved_at": _timestamp(
                    source["retrieved_at"], f"{path}.source.retrieved_at"
                ),
            },
            "geography": _string_list(
                binding["geography"], f"{path}.geography", nonempty=True
            ),
            "access": {
                "access_type": _enum(
                    access["access_type"],
                    {"authorized", "public", "licensed"},
                    f"{path}.access.access_type",
                ),
                "permission_confirmed": _boolean(
                    access["permission_confirmed"],
                    f"{path}.access.permission_confirmed",
                ),
                "permitted_uses": _string_list(
                    access["permitted_uses"],
                    f"{path}.access.permitted_uses",
                    nonempty=True,
                ),
            },
            "selection_notes": _string(
                binding["selection_notes"], f"{path}.selection_notes"
            ),
            "coverage_notes": _string(
                binding["coverage_notes"], f"{path}.coverage_notes"
            ),
        })
    if no_frame and source_bindings:
        _fail("$.source_bindings", "must be empty for no_defensible_frame")
    for cell_index, cell in enumerate(cells):
        for source_index, source_observation in enumerate(
            cell["source_observations"]
        ):
            if source_observation["batch_id"] not in seen_batches:
                _fail(
                    f"$.cells[{cell_index}].source_observations[{source_index}].batch_id",
                    "does not resolve to a source binding",
                )
            if (
                batch_partitions[source_observation["batch_id"]]
                != cell["partition_id"]
            ):
                _fail(
                    f"$.cells[{cell_index}].source_observations[{source_index}].batch_id",
                    "must resolve to a source binding in the cell partition",
                )

    expected_modeled_shares: dict[tuple[str, str], float] = {}
    for (partition_id, _relationship, collection_dimensions), collection in (
        collection_weights.items()
    ):
        modeled_share = math.fsum(
            weight for weight, status in collection if status == "modeled"
        )
        for dimension in collection_dimensions:
            key = (partition_id, dimension)
            expected_modeled_shares[key] = max(
                expected_modeled_shares.get(key, 0.0),
                modeled_share,
            )
    modeled_weight_by_dimension = []
    declared_modeled_keys: set[tuple[str, str]] = set()
    modeled_row_keys = {"partition_id", "dimension", "share", "status"}
    for index, raw_row in enumerate(
        _array(
            frame["modeled_weight_by_dimension"],
            "$.modeled_weight_by_dimension",
        )
    ):
        path = f"$.modeled_weight_by_dimension[{index}]"
        row = _object(raw_row, modeled_row_keys, path)
        partition_id = _identifier(
            row["partition_id"], f"{path}.partition_id"
        )
        dimension = _identifier(row["dimension"], f"{path}.dimension")
        key = (partition_id, dimension)
        if partition_id not in partition_ids:
            _fail(f"{path}.partition_id", "does not resolve to a frame unit")
        if dimension not in dimensions:
            _fail(f"{path}.dimension", "is an undeclared dimension")
        if key in declared_modeled_keys:
            _fail(path, "duplicates a partition and dimension")
        declared_modeled_keys.add(key)
        share = _number(
            row["share"], f"{path}.share", minimum=0.0, maximum=1.0
        )
        assert share is not None
        if key not in expected_modeled_shares:
            _fail(path, "has no compatible weighted cell collection")
        if abs(share - expected_modeled_shares[key]) > _TOLERANCE:
            _fail(
                f"{path}.share",
                "must equal the effective modeled share for the dimension",
            )
        calculated_share = expected_modeled_shares[key]
        expected_status = (
            "experimental" if calculated_share > 0.30 else "supported"
        )
        status = _enum(
            row["status"], {"supported", "experimental"}, f"{path}.status"
        )
        if status != expected_status:
            _fail(f"{path}.status", f"must be {expected_status}")
        modeled_weight_by_dimension.append({
            "partition_id": partition_id,
            "dimension": dimension,
            "share": share,
            "status": status,
        })
    if declared_modeled_keys != set(expected_modeled_shares):
        _fail(
            "$.modeled_weight_by_dimension",
            "must cover every weighted partition and dimension exactly once",
        )
    declared_modeled = _number(
        frame["modeled_weight_share"],
        "$.modeled_weight_share",
        minimum=0.0,
        maximum=1.0,
    )
    assert declared_modeled is not None
    effective_modeled = max(expected_modeled_shares.values(), default=0.0)
    if abs(declared_modeled - effective_modeled) > _TOLERANCE:
        _fail(
            "$.modeled_weight_share",
            "must equal the maximum dimensional modeled share",
        )
    if no_frame and (modeled_weight_by_dimension or declared_modeled != 0.0):
        _fail(
            "$.modeled_weight_by_dimension",
            "and modeled_weight_share must be empty and zero for no_defensible_frame",
        )
    if (
        eligibility in {"eligible_tier_2", "eligible_tier_3"}
        and effective_modeled > 0.30
    ):
        _fail(
            "$.eligibility",
            "must be experimental when a dimensional modeled share exceeds 0.30",
        )

    coverage = _object(
        frame["coverage_assessment"],
        {"selection_statement", "coverage_statement", "known_gaps"},
        "$.coverage_assessment",
    )
    known_gaps = _string_list(
        coverage["known_gaps"],
        "$.coverage_assessment.known_gaps",
        nonempty=no_frame,
    )
    downgrade_reason = _string(
        frame["downgrade_reason"], "$.downgrade_reason", allow_empty=True
    )
    if eligibility in {"experimental", "no_defensible_frame"} and not downgrade_reason:
        _fail("$.downgrade_reason", "is required for a downgraded frame")
    if not no_frame and not units:
        _fail("$.units", "must not be empty")
    return {
        "schema_version": frame["schema_version"],
        "frame_id": _identifier(frame["frame_id"], "$.frame_id"),
        "frame_version": _version(frame["frame_version"], "$.frame_version"),
        "built_at": _timestamp(frame["built_at"], "$.built_at"),
        "frame_request_id": _identifier(
            frame["frame_request_id"], "$.frame_request_id"
        ),
        "frame_request_sha256": _digest(
            frame["frame_request_sha256"], "$.frame_request_sha256"
        ),
        "target_universe": _string(frame["target_universe"], "$.target_universe"),
        "proxy_universes": _string_list(
            frame["proxy_universes"],
            "$.proxy_universes",
            identifiers=True,
        ),
        "claim_boundary": _string(frame["claim_boundary"], "$.claim_boundary"),
        "units": units,
        "structural_dimensions": dimensions,
        "cells": cells,
        "margins": margins,
        "joints": joints,
        "source_bindings": source_bindings,
        "coverage_assessment": {
            "selection_statement": _string(
                coverage["selection_statement"],
                "$.coverage_assessment.selection_statement",
            ),
            "coverage_statement": _string(
                coverage["coverage_statement"],
                "$.coverage_assessment.coverage_statement",
            ),
            "known_gaps": known_gaps,
        },
        "modeled_weight_by_dimension": modeled_weight_by_dimension,
        "modeled_weight_share": declared_modeled,
        "eligibility": eligibility,
        "downgrade_reason": downgrade_reason,
    }


def validate_composition_plan(
    payload: object,
    *,
    frame: dict[str, object] | None,
) -> dict[str, object]:
    """Validate one partition-aware reusable composition basis."""

    _forbid_recursive_fields(
        payload,
        _PROHIBITED_COMPOSITION_FIELDS,
        "$",
        message="is a forbidden study quota, slot, panelist, or capacity field",
    )
    keys = {
        "schema_version",
        "composition_id",
        "plan_version",
        "built_at",
        "evidence_basis",
        "requested_tier",
        "achieved_tier",
        "tier_reason_codes",
        "lost_claims",
        "frame_binding",
        "structural_groups",
        "overlay_hypotheses",
        "profiles",
        "unsupported_combinations",
        "allocation_constraints",
        "run_allocation_rules",
        "required_diagnostics",
        "modeled_cell_share",
    }
    plan = _object(payload, keys, "$")
    if plan["schema_version"] != COMPOSITION_PLAN_VERSION:
        _fail("$.schema_version", f"must equal {COMPOSITION_PLAN_VERSION}")
    if frame is None:
        _fail("$.frame_binding", "requires a canonical population-frame result")
    validated_frame = validate_population_frame(frame)
    frame_digest = _sha256_json(validated_frame)
    usable_frame = validated_frame["eligibility"] in {
        "eligible_tier_2",
        "eligible_tier_3",
    }
    evidence_basis = _enum(
        plan["evidence_basis"], EVIDENCE_BASES, "$.evidence_basis"
    )
    provisional = evidence_basis == "none"
    if provisional and usable_frame:
        _fail(
            "$.evidence_basis",
            "evidence_basis none requires a no-frame Tier 1 result",
        )
    requested_tier = _enum(
        plan["requested_tier"], PANEL_TIERS, "$.requested_tier"
    )
    achieved_tier = _enum(
        plan["achieved_tier"],
        {"tier_1", "tier_2", "tier_3"},
        "$.achieved_tier",
    )
    reason_codes = _string_list(
        plan["tier_reason_codes"],
        "$.tier_reason_codes",
        identifiers=True,
    )
    lost_claims = _string_list(plan["lost_claims"], "$.lost_claims")

    raw_binding = _object(
        plan["frame_binding"],
        {"frame_result_sha256", "frame_sha256", "frame_id", "selection"},
        "$.frame_binding",
    )
    result_sha256 = _digest(
        raw_binding["frame_result_sha256"],
        "$.frame_binding.frame_result_sha256",
    )
    if result_sha256 != frame_digest:
        _fail(
            "$.frame_binding.frame_result_sha256",
            "must bind the canonical population-frame result",
        )
    frame_cells = {
        str(cell["cell_id"]): cell for cell in validated_frame["cells"]
    }
    selected_cells: dict[str, dict[str, object]] = {}
    selection: dict[str, object] | None = None
    frame_sha256: str | None = None
    frame_id: str | None = None
    if usable_frame:
        frame_sha256 = _digest(
            raw_binding["frame_sha256"], "$.frame_binding.frame_sha256"
        )
        if frame_sha256 != frame_digest:
            _fail(
                "$.frame_binding.frame_sha256",
                "must equal the eligible frame-result digest",
            )
        frame_id = _identifier(
            raw_binding["frame_id"], "$.frame_binding.frame_id"
        )
        if frame_id != validated_frame["frame_id"]:
            _fail("$.frame_binding.frame_id", "must match the eligible frame")
        raw_selection = _object(
            raw_binding["selection"],
            {"partition_id", "relationship", "dimensions"},
            "$.frame_binding.selection",
        )
        partition_id = _identifier(
            raw_selection["partition_id"],
            "$.frame_binding.selection.partition_id",
        )
        relationship = _enum(
            raw_selection["relationship"],
            {"marginal", "joint"},
            "$.frame_binding.selection.relationship",
        )
        dimensions = _string_list(
            raw_selection["dimensions"],
            "$.frame_binding.selection.dimensions",
            nonempty=True,
            identifiers=True,
        )
        records = validated_frame[
            "margins" if relationship == "marginal" else "joints"
        ]
        matches = [
            record
            for record in records
            if record["partition_id"] == partition_id
            and set(record["dimensions"]) == set(dimensions)
        ]
        if len(matches) != 1:
            _fail(
                "$.frame_binding.selection",
                "must resolve to exactly one partitioned frame collection",
            )
        record = matches[0]
        selected_cells = {
            cell_id: frame_cells[cell_id]
            for cell_id in record["cell_ids"]
            if (
                frame_cells[cell_id]["status"] != "missing"
                and not frame_cells[cell_id]["suppressed"]
                and frame_cells[cell_id]["structural_weight"] is not None
            )
        }
        if not selected_cells:
            _fail(
                "$.frame_binding.selection",
                "selected collection must contain available weighted cells",
            )
        _reconcile(
            [
                float(cell["structural_weight"])
                for cell in selected_cells.values()
            ],
            "$.frame_binding.selection.structural_weight",
        )
        selection = {
            "partition_id": partition_id,
            "relationship": relationship,
            "dimensions": dimensions,
        }
    else:
        for key in ("frame_sha256", "frame_id", "selection"):
            if raw_binding[key] is not None:
                _fail(
                    f"$.frame_binding.{key}",
                    "must be null for an experimental or no-frame Tier 1 input",
                )

    group_keys = {
        "structural_group_id",
        "origin",
        "cell_ids",
        "structural_finding_ids",
        "evidence_ids",
        "structural_weight",
        "weight_semantic",
        "must_cover",
    }
    groups = []
    group_by_id: dict[str, dict[str, object]] = {}
    assigned_cells: set[str] = set()
    structural_weights: list[float] = []
    for index, raw_group in enumerate(
        _array(plan["structural_groups"], "$.structural_groups", nonempty=True)
    ):
        path = f"$.structural_groups[{index}]"
        group = _object(raw_group, group_keys, path)
        group_id = _identifier(
            group["structural_group_id"], f"{path}.structural_group_id"
        )
        if group_id in group_by_id:
            _fail(f"{path}.structural_group_id", "is duplicated")
        origin = _enum(
            group["origin"],
            {"frame_cells", "tier_1_evidence", "tier_1_provisional"},
            f"{path}.origin",
        )
        cell_ids = _string_list(
            group["cell_ids"], f"{path}.cell_ids", identifiers=True
        )
        finding_ids = _string_list(
            group["structural_finding_ids"],
            f"{path}.structural_finding_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        evidence_ids = _string_list(
            group["evidence_ids"],
            f"{path}.evidence_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        if provisional and (finding_ids or evidence_ids):
            _fail(
                path,
                "evidence_basis none requires empty structural support",
            )
        if assigned_cells.intersection(cell_ids):
            _fail(f"{path}.cell_ids", "selected cells may belong to only one group")
        weight = _number(
            group["structural_weight"],
            f"{path}.structural_weight",
            minimum=0.0,
            maximum=1.0,
        )
        assert weight is not None
        semantic = _semantic(group["weight_semantic"], f"{path}.weight_semantic")
        if usable_frame:
            if origin != "frame_cells":
                _fail(f"{path}.origin", "eligible frames require frame_cells")
            if not cell_ids or not set(cell_ids).issubset(selected_cells):
                _fail(
                    f"{path}.cell_ids",
                    "must contain only available cells from the selected collection",
                )
            expected_weight = math.fsum(
                float(selected_cells[cell_id]["structural_weight"])
                for cell_id in cell_ids
            )
            if abs(weight - expected_weight) > _TOLERANCE:
                _fail(
                    f"{path}.structural_weight",
                    "must equal its selected frame-cell weights",
                )
            semantics = {
                selected_cells[cell_id]["weight_semantic"] for cell_id in cell_ids
            }
            if len(semantics) != 1 or semantic not in semantics:
                _fail(
                    f"{path}.weight_semantic",
                    "must preserve one selected frame weight semantic",
                )
        else:
            expected_origin = (
                "tier_1_provisional" if provisional else "tier_1_evidence"
            )
            if origin != expected_origin:
                _fail(
                    f"{path}.origin",
                    f"must be {expected_origin} for this Tier 1 evidence basis",
                )
            if cell_ids:
                _fail(
                    f"{path}.cell_ids",
                    "must be empty for a Tier 1 evidence group",
                )
            if semantic != "planning_allocation":
                _fail(
                    f"{path}.weight_semantic",
                    "Tier 1 evidence groups require planning_allocation",
                )
        assigned_cells.update(cell_ids)
        structural_weights.append(weight)
        canonical_group = {
            "structural_group_id": group_id,
            "origin": origin,
            "cell_ids": cell_ids,
            "structural_finding_ids": finding_ids,
            "evidence_ids": evidence_ids,
            "structural_weight": weight,
            "weight_semantic": semantic,
            "must_cover": _boolean(
                group["must_cover"], f"{path}.must_cover"
            ),
        }
        group_by_id[group_id] = canonical_group
        groups.append(canonical_group)
    _reconcile(structural_weights, "$.structural_groups.structural_weight")
    if usable_frame and assigned_cells != set(selected_cells):
        _fail(
            "$.structural_groups",
            "must partition every available cell in the selected collection",
        )

    overlay_keys = {
        "overlay_id",
        "description",
        "allocation_basis",
        "finding_ids",
        "evidence_ids",
        "topic_bindings",
    }
    overlays = []
    overlay_by_id: dict[str, dict[str, object]] = {}
    for index, raw_overlay in enumerate(
        _array(plan["overlay_hypotheses"], "$.overlay_hypotheses", nonempty=True)
    ):
        path = f"$.overlay_hypotheses[{index}]"
        overlay = _object(raw_overlay, overlay_keys, path)
        overlay_id = _identifier(overlay["overlay_id"], f"{path}.overlay_id")
        if overlay_id in overlay_by_id:
            _fail(f"{path}.overlay_id", "is duplicated")
        evidence_ids = _string_list(
            overlay["evidence_ids"],
            f"{path}.evidence_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        topic_bindings = []
        topic_ids: set[str] = set()
        for topic_index, raw_topic in enumerate(
            _array(
                overlay["topic_bindings"],
                f"{path}.topic_bindings",
                nonempty=not provisional,
            )
        ):
            topic_path = f"{path}.topic_bindings[{topic_index}]"
            topic = _object(raw_topic, {"topic_id", "evidence_ids"}, topic_path)
            topic_id = _identifier(topic["topic_id"], f"{topic_path}.topic_id")
            if topic_id in topic_ids:
                _fail(f"{topic_path}.topic_id", "is duplicated")
            topic_ids.add(topic_id)
            topic_evidence = _string_list(
                topic["evidence_ids"],
                f"{topic_path}.evidence_ids",
                nonempty=True,
                identifiers=True,
            )
            if not set(topic_evidence).issubset(evidence_ids):
                _fail(
                    f"{topic_path}.evidence_ids",
                    "must be a subset of the overlay evidence",
                )
            topic_bindings.append({
                "topic_id": topic_id,
                "evidence_ids": topic_evidence,
            })
        finding_ids = _string_list(
            overlay["finding_ids"],
            f"{path}.finding_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        allocation_basis = _enum(
            overlay["allocation_basis"],
            {"observed", "estimated", "experimental"},
            f"{path}.allocation_basis",
        )
        if provisional:
            if finding_ids or evidence_ids or topic_bindings:
                _fail(
                    path,
                    "evidence_basis none requires empty overlay support",
                )
            if allocation_basis != "experimental":
                _fail(
                    f"{path}.allocation_basis",
                    "must be experimental when evidence_basis is none",
                )
        canonical_overlay = {
            "overlay_id": overlay_id,
            "description": _string(
                overlay["description"], f"{path}.description"
            ),
            "allocation_basis": allocation_basis,
            "finding_ids": finding_ids,
            "evidence_ids": evidence_ids,
            "topic_bindings": topic_bindings,
        }
        overlay_by_id[overlay_id] = canonical_overlay
        overlays.append(canonical_overlay)

    profile_keys = {
        "profile_id",
        "structural_group_id",
        "overlay_ids",
        "support_status",
        "support_finding_ids",
        "support_evidence_ids",
        "conditional_overlay_allocation",
        "overlay_weight_semantic",
        "effective_profile_allocation",
        "effective_weight_semantic",
        "source_cell_ids",
    }
    profiles = []
    profile_ids: set[str] = set()
    supported_signatures: set[tuple[str, tuple[str, ...]]] = set()
    conditional_by_group: dict[str, list[float]] = {
        group_id: [] for group_id in group_by_id
    }
    effective_weights: list[float] = []
    modeled_effective_weight = 0.0
    used_overlay_ids: set[str] = set()
    for index, raw_profile in enumerate(
        _array(plan["profiles"], "$.profiles", nonempty=True)
    ):
        path = f"$.profiles[{index}]"
        profile = _object(raw_profile, profile_keys, path)
        profile_id = _identifier(profile["profile_id"], f"{path}.profile_id")
        if profile_id in profile_ids:
            _fail(f"{path}.profile_id", "is duplicated")
        profile_ids.add(profile_id)
        group_id = _identifier(
            profile["structural_group_id"], f"{path}.structural_group_id"
        )
        if group_id not in group_by_id:
            _fail(f"{path}.structural_group_id", "does not resolve")
        overlay_ids = _string_list(
            profile["overlay_ids"],
            f"{path}.overlay_ids",
            nonempty=True,
            identifiers=True,
        )
        if not set(overlay_ids).issubset(overlay_by_id):
            _fail(f"{path}.overlay_ids", "contains an undeclared overlay")
        signature = (group_id, tuple(sorted(overlay_ids)))
        if signature in supported_signatures:
            _fail(path, "duplicates an exact supported profile signature")
        supported_signatures.add(signature)
        used_overlay_ids.update(overlay_ids)
        source_cell_ids = _string_list(
            profile["source_cell_ids"],
            f"{path}.source_cell_ids",
            identifiers=True,
        )
        if set(source_cell_ids) != set(group_by_id[group_id]["cell_ids"]):
            _fail(
                f"{path}.source_cell_ids",
                "must exactly match its structural group cells",
            )
        conditional = _number(
            profile["conditional_overlay_allocation"],
            f"{path}.conditional_overlay_allocation",
            minimum=0.0,
            maximum=1.0,
        )
        effective = _number(
            profile["effective_profile_allocation"],
            f"{path}.effective_profile_allocation",
            minimum=0.0,
            maximum=1.0,
        )
        assert conditional is not None and effective is not None
        expected_effective = (
            float(group_by_id[group_id]["structural_weight"]) * conditional
        )
        if abs(effective - expected_effective) > _TOLERANCE:
            _fail(
                f"{path}.effective_profile_allocation",
                "must equal structural weight × conditional overlay allocation",
            )
        overlay_semantic = _semantic(
            profile["overlay_weight_semantic"],
            f"{path}.overlay_weight_semantic",
        )
        if overlay_semantic != "planning_allocation":
            _fail(
                f"{path}.overlay_weight_semantic",
                "conditional overlays require planning_allocation",
            )
        effective_semantic = _semantic(
            profile["effective_weight_semantic"],
            f"{path}.effective_weight_semantic",
        )
        if effective_semantic != group_by_id[group_id]["weight_semantic"]:
            _fail(
                f"{path}.effective_weight_semantic",
                "must preserve the structural group weight semantic",
            )
        support_status = _enum(
            profile["support_status"],
            {"provisional", "supported"},
            f"{path}.support_status",
        )
        support_finding_ids = _string_list(
            profile["support_finding_ids"],
            f"{path}.support_finding_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        support_evidence_ids = _string_list(
            profile["support_evidence_ids"],
            f"{path}.support_evidence_ids",
            nonempty=not provisional,
            identifiers=True,
        )
        expected_support_status = (
            "provisional" if provisional else "supported"
        )
        if support_status != expected_support_status:
            _fail(
                f"{path}.support_status",
                f"must be {expected_support_status} for this evidence basis",
            )
        if provisional and (support_finding_ids or support_evidence_ids):
            _fail(
                path,
                "provisional profiles require empty support bindings",
            )
        if not provisional:
            expected_finding_ids = set(
                group_by_id[group_id]["structural_finding_ids"]
            )
            expected_evidence_ids = set(group_by_id[group_id]["evidence_ids"])
            for overlay_id in overlay_ids:
                selected_overlay = overlay_by_id[overlay_id]
                expected_finding_ids.update(selected_overlay["finding_ids"])
                expected_evidence_ids.update(selected_overlay["evidence_ids"])
            if set(support_finding_ids) != expected_finding_ids:
                _fail(
                    f"{path}.support_finding_ids",
                    "must exactly equal the selected structural group and "
                    "overlay finding union",
                )
            if set(support_evidence_ids) != expected_evidence_ids:
                _fail(
                    f"{path}.support_evidence_ids",
                    "must exactly equal the selected structural group and "
                    "overlay evidence union",
                )
        conditional_by_group[group_id].append(conditional)
        effective_weights.append(effective)
        if usable_frame:
            modeled_effective_weight += conditional * math.fsum(
                float(selected_cells[cell_id]["structural_weight"])
                for cell_id in source_cell_ids
                if selected_cells[cell_id]["status"] == "modeled"
            )
        profiles.append({
            "profile_id": profile_id,
            "structural_group_id": group_id,
            "overlay_ids": overlay_ids,
            "support_status": support_status,
            "support_finding_ids": support_finding_ids,
            "support_evidence_ids": support_evidence_ids,
            "conditional_overlay_allocation": conditional,
            "overlay_weight_semantic": overlay_semantic,
            "effective_profile_allocation": effective,
            "effective_weight_semantic": effective_semantic,
            "source_cell_ids": source_cell_ids,
        })
    for group_id, weights in conditional_by_group.items():
        if not weights:
            _fail(
                "$.profiles",
                f"must include an explicit profile for structural group {group_id}",
            )
        _reconcile(
            weights,
            f"$.profiles[{group_id}].conditional_overlay_allocation",
        )
    _reconcile(effective_weights, "$.profiles.effective_profile_allocation")

    unsupported = []
    unsupported_signatures: set[tuple[str, tuple[str, ...]]] = set()
    for index, raw_combination in enumerate(
        _array(plan["unsupported_combinations"], "$.unsupported_combinations")
    ):
        path = f"$.unsupported_combinations[{index}]"
        combination = _object(
            raw_combination,
            {"structural_group_id", "overlay_ids", "reason_code", "reason"},
            path,
        )
        group_id = _identifier(
            combination["structural_group_id"],
            f"{path}.structural_group_id",
        )
        overlay_ids = _string_list(
            combination["overlay_ids"],
            f"{path}.overlay_ids",
            nonempty=True,
            identifiers=True,
        )
        if group_id not in group_by_id or not set(overlay_ids).issubset(
            overlay_by_id
        ):
            _fail(path, "must reference declared groups and overlays")
        signature = (group_id, tuple(sorted(overlay_ids)))
        if signature in unsupported_signatures:
            _fail(path, "duplicates an exact unsupported signature")
        if signature in supported_signatures:
            _fail(path, "an exact supported signature cannot be unsupported")
        unsupported_signatures.add(signature)
        unsupported.append({
            "structural_group_id": group_id,
            "overlay_ids": overlay_ids,
            "reason_code": _identifier(
                combination["reason_code"], f"{path}.reason_code"
            ),
            "reason": _string(combination["reason"], f"{path}.reason"),
        })

    frame_rank = 1
    if usable_frame:
        frame_rank = (
            3
            if (
                validated_frame["eligibility"] == "eligible_tier_3"
                and evidence_basis in {"first_party_aggregate", "hybrid"}
            )
            else 2
        )
    if any(
        overlay_by_id[overlay_id]["allocation_basis"] == "experimental"
        for overlay_id in used_overlay_ids
    ):
        frame_rank = 1
    requested_rank = int(requested_tier[-1])
    expected_achieved = f"tier_{min(requested_rank, frame_rank, 3)}"
    if achieved_tier != expected_achieved:
        _fail(
            "$.achieved_tier",
            f"must be {expected_achieved} for the supplied frame and overlays",
        )
    downgraded = achieved_tier != requested_tier
    if downgraded and (not reason_codes or not lost_claims):
        _fail(
            "$.tier_reason_codes",
            "and lost_claims must be nonempty for a tier downgrade",
        )
    if not downgraded and (reason_codes or lost_claims):
        _fail(
            "$.tier_reason_codes",
            "and lost_claims must be empty without a tier downgrade",
        )

    modeled_share = _number(
        plan["modeled_cell_share"],
        "$.modeled_cell_share",
        minimum=0.0,
        maximum=1.0,
    )
    assert modeled_share is not None
    expected_modeled_share = modeled_effective_weight if usable_frame else 0.0
    if abs(modeled_share - expected_modeled_share) > _TOLERANCE:
        _fail(
            "$.modeled_cell_share",
            "must equal modeled effective weight in the selected collection",
        )
    run_rules = _object(
        plan["run_allocation_rules"],
        {"reserve_strategy", "min_one_for_must_cover"},
        "$.run_allocation_rules",
    )
    return {
        "schema_version": plan["schema_version"],
        "composition_id": _identifier(
            plan["composition_id"], "$.composition_id"
        ),
        "plan_version": _version(plan["plan_version"], "$.plan_version"),
        "built_at": _timestamp(plan["built_at"], "$.built_at"),
        "evidence_basis": evidence_basis,
        "requested_tier": requested_tier,
        "achieved_tier": achieved_tier,
        "tier_reason_codes": reason_codes,
        "lost_claims": lost_claims,
        "frame_binding": {
            "frame_result_sha256": result_sha256,
            "frame_sha256": frame_sha256,
            "frame_id": frame_id,
            "selection": selection,
        },
        "structural_groups": groups,
        "overlay_hypotheses": overlays,
        "profiles": profiles,
        "unsupported_combinations": unsupported,
        "allocation_constraints": _string_list(
            plan["allocation_constraints"],
            "$.allocation_constraints",
            nonempty=True,
        ),
        "run_allocation_rules": {
            "reserve_strategy": _enum(
                run_rules["reserve_strategy"],
                {"largest-remainder", "minimum-coverage-first"},
                "$.run_allocation_rules.reserve_strategy",
            ),
            "min_one_for_must_cover": _boolean(
                run_rules["min_one_for_must_cover"],
                "$.run_allocation_rules.min_one_for_must_cover",
            ),
        },
        "required_diagnostics": _string_list(
            plan["required_diagnostics"],
            "$.required_diagnostics",
            nonempty=True,
            identifiers=True,
        ),
        "modeled_cell_share": modeled_share,
    }


def validate_outcome_feedback(payload: object) -> dict[str, object]:
    """Return only an immutable canonical copy and its bound source digest."""

    _forbid_recursive_fields(
        payload,
        _PROHIBITED_OUTCOME_FIELDS,
        "$",
        message="is a prohibited outcome mutation field",
    )
    keys = {
        "schema_version",
        "feedback_id",
        "panel_id",
        "study_id",
        "variant_id",
        "cohort_id",
        "metric",
        "metric_direction",
        "units",
        "windows",
        "aggregate",
        "design",
        "source",
        "holdout",
        "missingness",
        "limitations",
        "source_sha256",
    }
    feedback = _object(payload, keys, "$")
    if feedback["schema_version"] != OUTCOME_FEEDBACK_VERSION:
        _fail("$.schema_version", f"must equal {OUTCOME_FEEDBACK_VERSION}")
    metric = _object(feedback["metric"], {"name", "definition"}, "$.metric")
    units = _object(feedback["units"], {"exposure", "outcome"}, "$.units")
    windows = _object(
        feedback["windows"], {"measurement", "attribution"}, "$.windows"
    )
    aggregate = _object(
        feedback["aggregate"],
        {"numerator", "denominator", "value"},
        "$.aggregate",
    )
    numerator = _number(
        aggregate["numerator"], "$.aggregate.numerator", nullable=True
    )
    denominator = _number(
        aggregate["denominator"],
        "$.aggregate.denominator",
        nullable=True,
        minimum=0.0,
    )
    value = _number(aggregate["value"], "$.aggregate.value", nullable=True)
    if numerator is None and denominator is None and value is None:
        _fail("$.aggregate", "must contain a numerator/denominator or value")
    if denominator == 0:
        _fail("$.aggregate.denominator", "must be positive when supplied")
    source = _object(
        feedback["source"], {"source_id", "permission_confirmed"}, "$.source"
    )
    canonical = {
        "schema_version": feedback["schema_version"],
        "feedback_id": _identifier(feedback["feedback_id"], "$.feedback_id"),
        "panel_id": _identifier(feedback["panel_id"], "$.panel_id"),
        "study_id": _identifier(feedback["study_id"], "$.study_id"),
        "variant_id": _identifier(feedback["variant_id"], "$.variant_id"),
        "cohort_id": _identifier(feedback["cohort_id"], "$.cohort_id"),
        "metric": {
            "name": _identifier(metric["name"], "$.metric.name"),
            "definition": _string(metric["definition"], "$.metric.definition"),
        },
        "metric_direction": _enum(
            feedback["metric_direction"],
            {"higher_is_better", "lower_is_better", "descriptive"},
            "$.metric_direction",
        ),
        "units": {
            "exposure": _identifier(units["exposure"], "$.units.exposure"),
            "outcome": _identifier(units["outcome"], "$.units.outcome"),
        },
        "windows": {
            "measurement": _string(
                windows["measurement"], "$.windows.measurement"
            ),
            "attribution": _string(
                windows["attribution"], "$.windows.attribution"
            ),
        },
        "aggregate": {
            "numerator": numerator,
            "denominator": denominator,
            "value": value,
        },
        "design": _enum(
            feedback["design"],
            {"experimental", "observational", "modeled"},
            "$.design",
        ),
        "source": {
            "source_id": _identifier(source["source_id"], "$.source.source_id"),
            "permission_confirmed": _boolean(
                source["permission_confirmed"], "$.source.permission_confirmed"
            ),
        },
        "holdout": _boolean(feedback["holdout"], "$.holdout"),
        "missingness": _string(feedback["missingness"], "$.missingness"),
        "limitations": _string_list(
            feedback["limitations"], "$.limitations", nonempty=True
        ),
        "source_sha256": _digest(
            feedback["source_sha256"], "$.source_sha256"
        ),
    }
    return {
        "canonical_copy": deepcopy(canonical),
        "source_digest": canonical["source_sha256"],
    }


def validate_validity_profile(payload: object) -> dict[str, object]:
    """Validate five separate validity axes without a composite score."""

    _forbid_recursive_fields(payload, _FORBIDDEN_VALIDITY_FIELDS, "$")
    keys = {
        "schema_version",
        "validity_id",
        "binding_state",
        "panel_id",
        "panel_tier",
        "evidence_basis",
        "axes",
        "predeclared_validation_design",
        "held_out_outcome_evidence",
        "source_bindings",
    }
    validity = _object(payload, keys, "$")
    if validity["schema_version"] != VALIDITY_PROFILE_VERSION:
        _fail("$.schema_version", f"must equal {VALIDITY_PROFILE_VERSION}")
    binding_state = _enum(
        validity["binding_state"],
        {"frame_provisional", "panel_final"},
        "$.binding_state",
    )
    if binding_state == "frame_provisional":
        for key in ("panel_id", "panel_tier", "evidence_basis"):
            if validity[key] is not None:
                _fail(
                    f"$.{key}",
                    "must be null for frame_provisional validity",
                )
        panel_id = None
        tier = None
        evidence_basis = None
    else:
        for key in ("panel_id", "panel_tier", "evidence_basis"):
            if validity[key] is None:
                _fail(f"$.{key}", "is required for panel_final validity")
        panel_id = _identifier(validity["panel_id"], "$.panel_id")
        tier = _enum(validity["panel_tier"], PANEL_TIERS, "$.panel_tier")
        evidence_basis = _enum(
            validity["evidence_basis"], EVIDENCE_BASES, "$.evidence_basis"
        )
    axes_raw = _object(validity["axes"], VALIDITY_AXES, "$.axes")
    axes: dict[str, object] = {}
    for axis in sorted(VALIDITY_AXES):
        path = f"$.axes.{axis}"
        item = _object(
            axes_raw[axis], {"status", "coverage", "limitations"}, path
        )
        axes[axis] = {
            "status": _enum(
                item["status"], VALIDITY_AXIS_STATUSES, f"{path}.status"
            ),
            "coverage": _number(
                item["coverage"],
                f"{path}.coverage",
                nullable=True,
                minimum=0.0,
                maximum=1.0,
            ),
            "limitations": _string_list(
                item["limitations"], f"{path}.limitations"
            ),
        }
    design_raw = validity["predeclared_validation_design"]
    design: dict[str, object] | None = None
    if design_raw is not None:
        item = _object(
            design_raw,
            {"design_id", "registered_at", "holdout_definition", "metrics"},
            "$.predeclared_validation_design",
        )
        design = {
            "design_id": _identifier(
                item["design_id"], "$.predeclared_validation_design.design_id"
            ),
            "registered_at": _timestamp(
                item["registered_at"],
                "$.predeclared_validation_design.registered_at",
            ),
            "holdout_definition": _string(
                item["holdout_definition"],
                "$.predeclared_validation_design.holdout_definition",
            ),
            "metrics": _string_list(
                item["metrics"],
                "$.predeclared_validation_design.metrics",
                nonempty=True,
                identifiers=True,
            ),
        }
    held_out = _string_list(
        validity["held_out_outcome_evidence"],
        "$.held_out_outcome_evidence",
    )
    for index, source_digest in enumerate(held_out):
        _digest(source_digest, f"$.held_out_outcome_evidence[{index}]")
    if tier == "tier_4":
        if design is None:
            _fail(
                "$.predeclared_validation_design",
                "is required for Tier 4 predeclared validation",
            )
        if not held_out:
            _fail(
                "$.held_out_outcome_evidence",
                "is required for Tier 4 held-out validation",
            )
        if axes["external_validation"]["status"] != "held_out_validated":
            _fail(
                "$.axes.external_validation.status",
                "must be held_out_validated for Tier 4",
            )
    bindings_raw = _object(
        validity["source_bindings"],
        {
            "brief_sha256",
            "panel_sha256",
            "frame_result_sha256",
            "frame_sha256",
            "composition_sha256",
        },
        "$.source_bindings",
    )
    if binding_state == "frame_provisional":
        for key in ("brief_sha256", "panel_sha256", "composition_sha256"):
            if bindings_raw[key] is not None:
                _fail(
                    f"$.source_bindings.{key}",
                    "must be null for frame_provisional validity",
                )
        frame_result_sha256 = _digest(
            bindings_raw["frame_result_sha256"],
            "$.source_bindings.frame_result_sha256",
        )
        frame_sha256 = _digest(
            bindings_raw["frame_sha256"],
            "$.source_bindings.frame_sha256",
            nullable=True,
        )
        if frame_sha256 is not None and frame_sha256 != frame_result_sha256:
            _fail(
                "$.source_bindings.frame_sha256",
                "must equal frame_result_sha256 when a usable frame exists",
            )
        bindings = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": frame_result_sha256,
            "frame_sha256": frame_sha256,
            "composition_sha256": None,
        }
    else:
        for key in (
            "brief_sha256",
            "panel_sha256",
            "frame_result_sha256",
            "composition_sha256",
        ):
            if bindings_raw[key] is None:
                _fail(
                    f"$.source_bindings.{key}",
                    "is required for panel_final validity",
                )
        frame_result_sha256 = _digest(
            bindings_raw["frame_result_sha256"],
            "$.source_bindings.frame_result_sha256",
        )
        frame_sha256 = _digest(
            bindings_raw["frame_sha256"],
            "$.source_bindings.frame_sha256",
            nullable=True,
        )
        if tier == "tier_1":
            if (
                frame_sha256 is not None
                and frame_sha256 != frame_result_sha256
            ):
                _fail(
                    "$.source_bindings.frame_sha256",
                    "must equal frame_result_sha256 when Tier 1 retains an eligible frame",
                )
        elif frame_sha256 is None:
            _fail(
                "$.source_bindings.frame_sha256",
                "is required for panel_final validity above Tier 1",
            )
        elif frame_sha256 != frame_result_sha256:
            _fail(
                "$.source_bindings.frame_sha256",
                "must equal frame_result_sha256 for panel_final usable frames",
            )
        bindings = {
            "brief_sha256": _digest(
                bindings_raw["brief_sha256"],
                "$.source_bindings.brief_sha256",
            ),
            "panel_sha256": _digest(
                bindings_raw["panel_sha256"],
                "$.source_bindings.panel_sha256",
            ),
            "frame_result_sha256": frame_result_sha256,
            "frame_sha256": frame_sha256,
            "composition_sha256": _digest(
                bindings_raw["composition_sha256"],
                "$.source_bindings.composition_sha256",
            ),
        }
    return {
        "schema_version": validity["schema_version"],
        "validity_id": _identifier(validity["validity_id"], "$.validity_id"),
        "binding_state": binding_state,
        "panel_id": panel_id,
        "panel_tier": tier,
        "evidence_basis": evidence_basis,
        "axes": axes,
        "predeclared_validation_design": design,
        "held_out_outcome_evidence": held_out,
        "source_bindings": bindings,
    }


def _validate_authorized_import(value: Any, path: str) -> dict[str, object] | None:
    if value is None:
        return None
    item = _object(
        value,
        {
            "handoff_schema_version",
            "handoff_sha256",
            "status",
            "cohort_id",
            "exact_cohort_denominator",
            "selection_statement",
            "coverage_statement",
            "max_calibration_factor",
        },
        path,
    )
    if item["handoff_schema_version"] != "authorized-audience-handoff-v1":
        _fail(
            f"{path}.handoff_schema_version",
            "must equal authorized-audience-handoff-v1",
        )
    return {
        "handoff_schema_version": item["handoff_schema_version"],
        "handoff_sha256": _digest(
            item["handoff_sha256"], f"{path}.handoff_sha256"
        ),
        "status": _enum(
            item["status"], {"complete", "complete_with_loss"}, f"{path}.status"
        ),
        "cohort_id": _identifier(item["cohort_id"], f"{path}.cohort_id"),
        "exact_cohort_denominator": _string(
            item["exact_cohort_denominator"],
            f"{path}.exact_cohort_denominator",
        ),
        "selection_statement": _string(
            item["selection_statement"], f"{path}.selection_statement"
        ),
        "coverage_statement": _string(
            item["coverage_statement"], f"{path}.coverage_statement"
        ),
        "max_calibration_factor": _number(
            item["max_calibration_factor"],
            f"{path}.max_calibration_factor",
            minimum=0.0,
        ),
    }


def _validate_v3_brief(payload: object) -> dict[str, object]:
    brief = _object(payload, _V2_BRIEF_KEYS | _V3_BRIEF_EXTENSION_KEYS, "$.brief")
    if brief["schema_version"] != RESEARCH_BRIEF_V3:
        _fail("$.brief.schema_version", f"must equal {RESEARCH_BRIEF_V3}")
    tier = _enum(brief["panel_tier"], PANEL_TIERS, "$.brief.panel_tier")
    basis = _enum(
        brief["evidence_basis"], EVIDENCE_BASES, "$.brief.evidence_basis"
    )
    authorized_import = _validate_authorized_import(
        brief["authorized_audience_import"],
        "$.brief.authorized_audience_import",
    )
    if tier == "tier_3" and authorized_import is None:
        _fail(
            "$.brief.authorized_audience_import",
            f"is required for {tier.replace('_', ' ').title()}",
        )
    dimensional_validity = []
    for index, raw_dimension in enumerate(
        _array(
            brief["dimensional_validity"],
            "$.brief.dimensional_validity",
            nonempty=True,
        )
    ):
        path = f"$.brief.dimensional_validity[{index}]"
        item = _object(
            raw_dimension, {"dimension", "status", "limitations"}, path
        )
        dimensional_validity.append({
            "dimension": _identifier(item["dimension"], f"{path}.dimension"),
            "status": _enum(
                item["status"], VALIDITY_AXIS_STATUSES, f"{path}.status"
            ),
            "limitations": _string_list(
                item["limitations"], f"{path}.limitations"
            ),
        })
    approvals = []
    for index, raw_approval in enumerate(
        _array(brief["scoped_approvals"], "$.brief.scoped_approvals", nonempty=True)
    ):
        path = f"$.brief.scoped_approvals[{index}]"
        item = _object(
            raw_approval, {"scope", "status", "target_sha256"}, path
        )
        approvals.append({
            "scope": _identifier(item["scope"], f"{path}.scope"),
            "status": _enum(
                item["status"], {"approved", "rejected"}, f"{path}.status"
            ),
            "target_sha256": _digest(
                item["target_sha256"], f"{path}.target_sha256"
            ),
        })
    result = deepcopy(brief)
    result["panel_tier"] = tier
    result["evidence_basis"] = basis
    result["workflow_state_binding"] = _identifier(
        brief["workflow_state_binding"], "$.brief.workflow_state_binding"
    )
    result["population_frame_result_sha256"] = _digest(
        brief["population_frame_result_sha256"],
        "$.brief.population_frame_result_sha256",
    )
    result["population_frame_sha256"] = _digest(
        brief["population_frame_sha256"],
        "$.brief.population_frame_sha256",
        nullable=True,
    )
    if basis == "none":
        if tier != "tier_1":
            _fail(
                "$.brief.panel_tier",
                "evidence_basis none requires tier_1",
            )
        if result["population_frame_sha256"] is not None:
            _fail(
                "$.brief.population_frame_sha256",
                "evidence_basis none requires a null usable frame",
            )
    result["authorized_audience_import"] = authorized_import
    result["structural_findings"] = _string_list(
        brief["structural_findings"],
        "$.brief.structural_findings",
        nonempty=basis != "none",
        identifiers=True,
    )
    result["overlay_findings"] = _string_list(
        brief["overlay_findings"],
        "$.brief.overlay_findings",
        nonempty=basis != "none",
        identifiers=True,
    )
    if basis == "none" and (
        result["structural_findings"] or result["overlay_findings"]
    ):
        _fail(
            "$.brief",
            "evidence_basis none requires empty structural and overlay findings",
        )
    result["claim_boundary"] = _string(
        brief["claim_boundary"], "$.brief.claim_boundary"
    )
    result["dimensional_validity"] = dimensional_validity
    result["scoped_approvals"] = approvals
    return result


def _validate_v3_panel(payload: object) -> dict[str, object]:
    panel = _object(payload, _V2_PANEL_KEYS | _V3_PANEL_EXTENSION_KEYS, "$.panel")
    if panel["schema_version"] != SAVED_PANEL_V3:
        _fail("$.panel.schema_version", f"must equal {SAVED_PANEL_V3}")
    result = deepcopy(panel)
    result["panel_tier"] = _enum(
        panel["panel_tier"], PANEL_TIERS, "$.panel.panel_tier"
    )
    result["evidence_basis"] = _enum(
        panel["evidence_basis"], EVIDENCE_BASES, "$.panel.evidence_basis"
    )
    result["brief_id"] = _identifier(panel["brief_id"], "$.panel.brief_id")
    result["population_frame_result_sha256"] = _digest(
        panel["population_frame_result_sha256"],
        "$.panel.population_frame_result_sha256",
    )
    for key in (
        "population_frame_sha256",
        "authorized_handoff_sha256",
    ):
        result[key] = _digest(
            panel[key], f"$.panel.{key}", nullable=True
        )
    if result["evidence_basis"] == "none":
        if result["panel_tier"] != "tier_1":
            _fail(
                "$.panel.panel_tier",
                "evidence_basis none requires tier_1",
            )
        if result["population_frame_sha256"] is not None:
            _fail(
                "$.panel.population_frame_sha256",
                "evidence_basis none requires a null usable frame",
            )
    for key in ("composition_plan_sha256", "validity_profile_sha256"):
        result[key] = _digest(panel[key], f"$.panel.{key}")
    result["claim_boundary"] = _string(
        panel["claim_boundary"], "$.panel.claim_boundary"
    )
    result["package_status"] = _enum(
        panel["package_status"],
        {"unpackaged", "proposed", "approved"},
        "$.panel.package_status",
    )
    raw_audit_binding = panel["audit_binding"]
    if not isinstance(raw_audit_binding, Mapping):
        _fail("$.panel.audit_binding", "must be an object")
    applicability = raw_audit_binding.get("applicability")
    if applicability == "release_b1":
        audit_binding = _object(
            raw_audit_binding,
            {
                "applicability",
                "auditor_run_id",
                "audit_sha256",
                "report_inputs_sha256",
                "evidence_ledger_sha256",
                "finding_support_sha256",
                "synthesis_matrix_sha256",
                "report_manifest_sha256",
            },
            "$.panel.audit_binding",
        )
        result["audit_binding"] = {
            "applicability": "release_b1",
            "auditor_run_id": _identifier(
                audit_binding["auditor_run_id"],
                "$.panel.audit_binding.auditor_run_id",
            ),
            "audit_sha256": _bare_digest(
                audit_binding["audit_sha256"],
                "$.panel.audit_binding.audit_sha256",
            ),
            "report_inputs_sha256": _bare_digest(
                audit_binding["report_inputs_sha256"],
                "$.panel.audit_binding.report_inputs_sha256",
            ),
            "evidence_ledger_sha256": _bare_digest(
                audit_binding["evidence_ledger_sha256"],
                "$.panel.audit_binding.evidence_ledger_sha256",
            ),
            "finding_support_sha256": _bare_digest(
                audit_binding["finding_support_sha256"],
                "$.panel.audit_binding.finding_support_sha256",
            ),
            "synthesis_matrix_sha256": _bare_digest(
                audit_binding["synthesis_matrix_sha256"],
                "$.panel.audit_binding.synthesis_matrix_sha256",
            ),
            "report_manifest_sha256": _bare_digest(
                audit_binding["report_manifest_sha256"],
                "$.panel.audit_binding.report_manifest_sha256",
            ),
        }
    elif applicability == "legacy_v2_migration":
        audit_binding = _object(
            raw_audit_binding,
            {
                "applicability",
                "status",
                "source_package_sha256",
                "reason",
            },
            "$.panel.audit_binding",
        )
        if audit_binding["status"] != "not_available":
            _fail(
                "$.panel.audit_binding.status",
                "must equal not_available for legacy_v2_migration",
            )
        result["audit_binding"] = {
            "applicability": "legacy_v2_migration",
            "status": "not_available",
            "source_package_sha256": _digest(
                audit_binding["source_package_sha256"],
                "$.panel.audit_binding.source_package_sha256",
            ),
            "reason": _string(
                audit_binding["reason"],
                "$.panel.audit_binding.reason",
            ),
        }
        if result["panel_tier"] != "tier_1":
            _fail(
                "$.panel.panel_tier",
                "legacy_v2_migration requires tier_1",
            )
        if result["package_status"] != "unpackaged":
            _fail(
                "$.panel.package_status",
                "legacy_v2_migration requires unpackaged status",
            )
        if result["population_frame_sha256"] is not None:
            _fail(
                "$.panel.population_frame_sha256",
                "legacy_v2_migration requires a null usable frame",
            )
        if result["authorized_handoff_sha256"] is not None:
            _fail(
                "$.panel.authorized_handoff_sha256",
                "legacy_v2_migration requires a null authorized handoff",
            )
    else:
        _fail(
            "$.panel.audit_binding.applicability",
            "must equal release_b1 or legacy_v2_migration",
        )
    return result


def _v2_projection(document: dict[str, object], *, brief: bool) -> dict[str, object]:
    keys = _V2_BRIEF_KEYS if brief else _V2_PANEL_KEYS
    projection = {key: deepcopy(document[key]) for key in keys}
    projection["schema_version"] = (
        RESEARCH_BRIEF_SCHEMA_VERSION if brief else SAVED_PANEL_SCHEMA_VERSION
    )
    return projection


def validate_saved_panel_v3(
    payload: object,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate and return a canonical standalone saved-panel v3 copy."""

    canonical = _validate_v3_panel(payload)
    v2_panel = _v2_projection(canonical, brief=False)
    blocking_errors = [
        error
        for error in validate_saved_panel(v2_panel, now=now)
        if error.code not in _STANDALONE_PANEL_DEFERRED_CODES
    ]
    if blocking_errors:
        raise AudienceResearchValidationError(blocking_errors)
    return deepcopy(canonical)


def validate_research_brief_v3(
    payload: object,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate and return a canonical standalone research-brief v3 copy."""

    del now
    canonical = _validate_v3_brief(payload)
    v2_brief = _v2_projection(canonical, brief=True)
    blocking_errors = validate_research_brief(v2_brief)
    if blocking_errors:
        raise AudienceResearchValidationError(blocking_errors)
    return deepcopy(canonical)


def _panel_builder_contracts():
    sibling_scripts = (
        Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
    )
    if str(sibling_scripts) not in sys.path:
        sys.path.insert(0, str(sibling_scripts))
    from audience_panel_builder.construction_audit import (  # noqa: PLC0415
        require_passing_construction_audit,
    )
    from audience_panel_builder.workflow_state import (  # noqa: PLC0415
        validate_workflow_state,
    )

    return validate_workflow_state, require_passing_construction_audit


def validate_v3_runtime_permission_policy(
    brief: Mapping[str, object],
    panel: Mapping[str, object],
    frame: Mapping[str, object],
) -> None:
    """Fail closed on the exact source and runtime permission authority."""

    for index, binding in enumerate(frame["source_bindings"]):
        access = binding["access"]
        if access["permission_confirmed"] is not True:
            _fail(
                f"$.frame.source_bindings[{index}].access.permission_confirmed",
                "must be true before structural evidence can authorize runtime use",
            )
        permitted_uses = access["permitted_uses"]
        if not STRUCTURAL_SOURCE_REQUIRED_USES.issubset(permitted_uses):
            _fail(
                f"$.frame.source_bindings[{index}].access.permitted_uses",
                "must explicitly authorize audience-composition and population-framing",
            )

    tier = brief["panel_tier"]
    evidence_basis = brief["evidence_basis"]
    authorized_import = brief["authorized_audience_import"]
    audit_binding = panel["audit_binding"]
    if audit_binding["applicability"] == "legacy_v2_migration":
        expected_use = GENERIC_SYNTHETIC_AD_TESTING_USE
    elif tier == "tier_3":
        if not isinstance(authorized_import, Mapping):
            _fail(
                "$.brief.authorized_audience_import",
                "is required by the exact authorized-cohort permission policy",
            )
        if panel["authorized_handoff_sha256"] != authorized_import.get(
            "handoff_sha256"
        ):
            _fail(
                "$.panel.authorized_handoff_sha256",
                "must bind the exact handoff used by the authorized-cohort policy",
            )
        for index, binding in enumerate(frame["source_bindings"]):
            if binding["access"]["access_type"] != "authorized":
                _fail(
                    f"$.frame.source_bindings[{index}].access.access_type",
                    "must be authorized for Tier 3 runtime use",
                )
        expected_use = AUTHORIZED_COHORT_SYNTHETIC_AD_TESTING_USE
    elif tier == "tier_1" and evidence_basis == "public":
        expected_use = PUBLIC_PROXY_SYNTHETIC_AD_TESTING_USE
    else:
        expected_use = GENERIC_SYNTHETIC_AD_TESTING_USE

    governance = panel["governance"]
    if governance["allowed_uses"] != [expected_use]:
        _fail(
            "$.panel.governance.allowed_uses",
            "must equal the exact singleton permission policy derived from tier, evidence route, and handoff authority",
        )
    if governance["privacy_confirmation"]["confirmed"] is not True:
        _fail(
            "$.panel.governance.privacy_confirmation.confirmed",
            "must be true before the package can authorize runtime use",
        )


def validate_v3_runtime_authority(
    brief: Mapping[str, object],
    panel: Mapping[str, object],
    frame: Mapping[str, object],
    authority: object,
) -> dict[str, object]:
    """Bind Tier 3 runtime use to the actual handoff and structural outputs."""

    record = _object(
        authority,
        {
            "schema_version",
            "cohort_id",
            "handoff",
            "structural_outputs",
        },
        "$.authorized_runtime_authority",
    )
    if (
        record["schema_version"]
        != "authorized-audience-runtime-authority-v1"
    ):
        _fail(
            "$.authorized_runtime_authority.schema_version",
            "must equal authorized-audience-runtime-authority-v1",
        )
    if brief.get("panel_tier") != "tier_3":
        _fail(
            "$.brief.panel_tier",
            "runtime authority is available only for Tier 3",
        )
    authorized_import = brief.get("authorized_audience_import")
    if not isinstance(authorized_import, Mapping):
        _fail(
            "$.brief.authorized_audience_import",
            "is required for Tier 3 runtime authority",
        )
    cohort_id = _identifier(
        record["cohort_id"],
        "$.authorized_runtime_authority.cohort_id",
    )
    if cohort_id != authorized_import.get("cohort_id"):
        _fail(
            "$.authorized_runtime_authority.cohort_id",
            "must match the approved Tier 3 cohort",
        )

    sibling_scripts = (
        Path(__file__).resolve().parents[3]
        / "audience-panel-builder"
        / "scripts"
    )
    if str(sibling_scripts) not in sys.path:
        sys.path.insert(0, str(sibling_scripts))
    from audience_panel_builder.construction_audit import (  # noqa: PLC0415
        _validate_release_b1_handoff_projection,
    )

    try:
        handoff, _handoff_paths = (
            _validate_release_b1_handoff_projection(record["handoff"])
        )
    except ValueError as exc:
        _fail(
            "$.authorized_runtime_authority.handoff",
            f"is invalid: {exc}",
        )
    if (
        handoff["schema_version"]
        != authorized_import.get("handoff_schema_version")
        or handoff["status"] != authorized_import.get("status")
        or _sha256_json(handoff)
        != authorized_import.get("handoff_sha256")
        or panel.get("authorized_handoff_sha256")
        != authorized_import.get("handoff_sha256")
    ):
        _fail(
            "$.authorized_runtime_authority.handoff",
            "must be the exact approved handoff bound by the brief and panel",
        )
    cohort_identity = handoff["cohort_identity"]
    if cohort_id != cohort_identity["cohort_id"]:
        _fail(
            "$.authorized_runtime_authority.cohort_id",
            "must equal the cohort identity derived from the authenticated "
            "source profile",
        )
    identity_outputs = {
        item["path"]: item
        for item in cohort_identity["structural_outputs"]
    }
    if not any(
        item["denominator"]
        == authorized_import.get("exact_cohort_denominator")
        for item in identity_outputs.values()
    ):
        _fail(
            "$.authorized_runtime_authority.handoff.cohort_identity",
            "must bind the exact approved cohort denominator",
        )
    privacy = handoff["privacy_permission"]
    if (
        privacy["permission_confirmed"] is not True
        or privacy["aggregate_only"] is not True
    ):
        _fail(
            "$.authorized_runtime_authority.handoff.privacy_permission",
            "must authorize aggregate-only downstream use",
        )
    handoff_outputs = {
        item["path"]: item
        for item in handoff["outputs"]
        if item["route"] == "structural_frame"
    }
    raw_outputs = _array(
        record["structural_outputs"],
        "$.authorized_runtime_authority.structural_outputs",
        nonempty=True,
    )
    canonical_outputs: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    bindings = {
        binding["batch_id"]: binding
        for binding in frame["source_bindings"]
    }
    bound_batches: set[str] = set()
    for index, raw_output in enumerate(raw_outputs):
        path = f"$.authorized_runtime_authority.structural_outputs[{index}]"
        output = _object(raw_output, {"path", "batch"}, path)
        output_path = _string(output["path"], f"{path}.path")
        if output_path in seen_paths:
            _fail(f"{path}.path", "is duplicated")
        seen_paths.add(output_path)
        declared = handoff_outputs.get(output_path)
        if declared is None:
            _fail(
                f"{path}.path",
                "does not resolve to a structural handoff output",
            )
        identity_output = identity_outputs.get(output_path)
        if identity_output is None:
            _fail(
                f"{path}.path",
                "is absent from the authenticated cohort identity",
            )
        batch = validate_observation_batch(output["batch"])
        if batch["access"]["access_type"] != "authorized":
            _fail(
                f"{path}.batch.access.access_type",
                "must be authorized for Tier 3",
            )
        if (
            _sha256_json(batch) != declared["sha256"]
            or batch["schema_version"] != declared["schema_version"]
            or len(batch["cells"]) != declared["row_count"]
        ):
            _fail(
                path,
                "does not match the exact structural handoff output",
            )
        if identity_output != {
            "path": output_path,
            "sha256": _sha256_json(batch),
            "schema_version": batch["schema_version"],
            "batch_id": batch["batch_id"],
            "unit": batch["unit"],
            "denominator": batch["denominator"],
            "row_count": len(batch["cells"]),
        }:
            _fail(
                path,
                "does not match the source-derived cohort identity",
            )
        binding = bindings.get(batch["batch_id"])
        if binding is None:
            _fail(
                f"{path}.batch.batch_id",
                "does not resolve to the packaged population frame",
            )
        expected_binding = {
            "batch_id": batch["batch_id"],
            "normalized_batch_sha256": batch[
                "normalized_batch_sha256"
            ],
            "raw_snapshot_sha256": batch["raw_snapshot_sha256"],
            "source": batch["source"],
            "geography": batch["geography"],
            "access": batch["access"],
            "selection_notes": batch["selection_notes"],
            "coverage_notes": batch["coverage_notes"],
        }
        if any(
            binding.get(field) != value
            for field, value in expected_binding.items()
        ):
            _fail(
                path,
                "does not exactly bind the packaged population-frame source",
            )
        exact_unit = any(
            unit["partition_id"] == binding["partition_id"]
            and unit["exact"] is True
            and unit["unit"] == batch["unit"]
            and unit["denominator"] == batch["denominator"]
            for unit in frame["units"]
        )
        if not exact_unit:
            _fail(
                path,
                "does not bind one exact frame unit and denominator",
            )
        bound_batches.add(batch["batch_id"])
        canonical_outputs.append(
            {"path": output_path, "batch": batch}
        )
    if set(handoff_outputs) != seen_paths or set(bindings) != bound_batches:
        _fail(
            "$.authorized_runtime_authority.structural_outputs",
            "must exactly cover every handoff structural output and frame source binding",
        )
    return {
        "schema_version": record["schema_version"],
        "cohort_id": cohort_id,
        "handoff": deepcopy(handoff),
        "structural_outputs": canonical_outputs,
    }


def validate_audience_research_v3(
    brief: object,
    panel: object,
    *,
    frame: object | None,
    composition: object,
    validity: object,
    workflow_state: object,
    construction_audit: object,
    panel_review_manifest_sha256: str | None = None,
    current_report_inputs_sha256: str | None = None,
    current_report_manifest_sha256: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, object] | None, ...]:
    """Validate v3 documents, unchanged v2 semantics, and all source bindings."""

    canonical_brief = _validate_v3_brief(brief)
    canonical_panel = _validate_v3_panel(panel)
    v2_brief = _v2_projection(canonical_brief, brief=True)
    v2_panel = _v2_projection(canonical_panel, brief=False)
    require_valid_audience_research_pair(v2_brief, v2_panel, now=now)
    v2_brief_sha256 = _sha256_json(v2_brief).removeprefix("sha256:")
    v2_panel_sha256 = _sha256_json(v2_panel).removeprefix("sha256:")

    tier = str(canonical_brief["panel_tier"])
    if canonical_panel["panel_tier"] != tier:
        _fail("$.panel.panel_tier", "must equal the brief panel tier")
    if canonical_panel["evidence_basis"] != canonical_brief["evidence_basis"]:
        _fail("$.panel.evidence_basis", "must equal the brief evidence basis")
    if canonical_panel["brief_id"] != canonical_brief["brief_id"]:
        _fail("$.panel.brief_id", "must equal the brief ID")
    if canonical_panel["claim_boundary"] != canonical_brief["claim_boundary"]:
        _fail("$.panel.claim_boundary", "must equal the brief claim boundary")
    scoped_approval_targets = {
        "evidence-synthesis": "sha256:" + v2_brief_sha256,
        "panel-construction": "sha256:" + v2_panel_sha256,
    }
    for index, approval in enumerate(canonical_brief["scoped_approvals"]):
        expected_target = scoped_approval_targets.get(str(approval["scope"]))
        if (
            expected_target is not None
            and approval["target_sha256"] != expected_target
        ):
            _fail(
                f"$.brief.scoped_approvals[{index}].target_sha256",
                "target must bind the independently derived v2 document",
            )

    if frame is None:
        _fail("$.frame", "a population-frame result is required")
    canonical_frame_result = validate_population_frame(frame)
    canonical_frame = (
        canonical_frame_result
        if canonical_frame_result["eligibility"] in {
            "eligible_tier_2",
            "eligible_tier_3",
        }
        else None
    )
    if tier != "tier_1" and canonical_frame is None:
        _fail("$.frame", f"{tier.replace('_', ' ').title()} requires a frame")
    expected_frame_result_digest = _sha256_json(canonical_frame_result)
    expected_frame_digest = (
        None if canonical_frame is None else _sha256_json(canonical_frame)
    )
    if (
        canonical_brief["population_frame_result_sha256"]
        != expected_frame_result_digest
    ):
        _fail(
            "$.brief.population_frame_result_sha256",
            "does not match the canonical population-frame result",
        )
    if (
        canonical_panel["population_frame_result_sha256"]
        != expected_frame_result_digest
    ):
        _fail(
            "$.panel.population_frame_result_sha256",
            "does not match the canonical population-frame result",
        )
    if canonical_brief["population_frame_sha256"] != expected_frame_digest:
        _fail(
            "$.brief.population_frame_sha256",
            "does not match the canonical population frame",
        )
    if canonical_panel["population_frame_sha256"] != expected_frame_digest:
        _fail(
            "$.panel.population_frame_sha256",
            "does not match the canonical population frame",
        )
    if canonical_frame is not None:
        eligibility = canonical_frame["eligibility"]
        if tier == "tier_2" and eligibility not in {
            "eligible_tier_2",
            "eligible_tier_3",
        }:
            _fail("$.frame.eligibility", "Tier 2 requires an eligible frame")
        if tier == "tier_3" and eligibility != "eligible_tier_3":
            _fail(
                "$.frame.eligibility",
                f"{tier.replace('_', ' ').title()} requires eligible_tier_3",
            )
        if tier == "tier_4" and eligibility not in {
            "eligible_tier_2",
            "eligible_tier_3",
        }:
            _fail("$.frame.eligibility", "Tier 4 requires an eligible frame")

    canonical_composition = validate_composition_plan(
        composition,
        frame=canonical_frame_result,
    )
    if canonical_composition["evidence_basis"] != canonical_brief["evidence_basis"]:
        _fail(
            "$.composition.evidence_basis",
            "must equal the brief evidence basis",
        )
    expected_composition_tier = (
        "tier_3" if tier == "tier_4" else tier
    )
    if canonical_composition["achieved_tier"] != expected_composition_tier:
        _fail(
            "$.composition.achieved_tier",
            f"must equal {expected_composition_tier} for {tier}",
        )
    composition_digest = _sha256_json(canonical_composition)
    if canonical_panel["composition_plan_sha256"] != composition_digest:
        _fail(
            "$.panel.composition_plan_sha256",
            "does not match the canonical composition plan",
        )

    canonical_validity = validate_validity_profile(validity)
    if canonical_validity["binding_state"] != "panel_final":
        _fail("$.validity.binding_state", "must be panel_final")
    if canonical_validity["panel_id"] != canonical_panel["panel_id"]:
        _fail("$.validity.panel_id", "must equal the panel ID")
    if canonical_validity["panel_tier"] != tier:
        _fail("$.validity.panel_tier", "must equal the brief panel tier")
    if canonical_validity["evidence_basis"] != canonical_brief["evidence_basis"]:
        _fail("$.validity.evidence_basis", "must equal the brief evidence basis")
    expected_validity_bindings = {
        "brief_sha256": _sha256_json(v2_brief),
        "panel_sha256": _sha256_json(v2_panel),
        "frame_result_sha256": expected_frame_result_digest,
        "frame_sha256": expected_frame_digest,
        "composition_sha256": composition_digest,
    }
    if canonical_validity["source_bindings"] != expected_validity_bindings:
        _fail(
            "$.validity.source_bindings",
            "must exactly bind the v2 projections, frame, and composition",
        )
    canonical_validity_digest = _sha256_json(canonical_validity)
    if canonical_panel["validity_profile_sha256"] != canonical_validity_digest:
        _fail(
            "$.panel.validity_profile_sha256",
            "does not match the canonical validity profile",
        )

    authorized_import = canonical_brief["authorized_audience_import"]
    if tier == "tier_3":
        if authorized_import is None:
            _fail(
                "$.brief.authorized_audience_import",
                f"is required for {tier.replace('_', ' ').title()}",
            )
        declared_calibration_limit = float(
            authorized_import["max_calibration_factor"]
        )
        if declared_calibration_limit > 3.0:
            _fail(
                "$.brief.authorized_audience_import.max_calibration_factor",
                "must be at most 3.0 for Tier 3",
            )
        if (
            canonical_panel["authorized_handoff_sha256"]
            != authorized_import["handoff_sha256"]
        ):
            _fail(
                "$.panel.authorized_handoff_sha256",
                "must exactly bind the authorized handoff",
            )
        assert canonical_frame is not None
        for index, cell in enumerate(canonical_frame["cells"]):
            calibration_factor = cell["calibration_factor"]
            if calibration_factor is not None and float(
                calibration_factor
            ) > min(
                3.0,
                declared_calibration_limit,
            ):
                _fail(
                    f"$.frame.cells[{index}].calibration_factor",
                    "must not exceed 3.0 or the authorized handoff declaration",
                )
        declared_denominator = authorized_import["exact_cohort_denominator"]
        if not any(
            unit["exact"] and unit["denominator"] == declared_denominator
            for unit in canonical_frame["units"]
        ):
            _fail(
                "$.frame.units",
                "Tier 3 requires an exact frame denominator matching the authorized handoff",
            )
        coverage = canonical_frame["coverage_assessment"]
        if not coverage["selection_statement"]:
            _fail(
                "$.frame.coverage_assessment.selection_statement",
                "is required for Tier 3",
            )
        if not coverage["coverage_statement"]:
            _fail(
                "$.frame.coverage_assessment.coverage_statement",
                "is required for Tier 3",
            )
    elif canonical_panel["authorized_handoff_sha256"] is not None:
        if authorized_import is None or (
            canonical_panel["authorized_handoff_sha256"]
            != authorized_import["handoff_sha256"]
        ):
            _fail(
                "$.panel.authorized_handoff_sha256",
                "must match the optional authorized import",
            )

    validate_v3_runtime_permission_policy(
        canonical_brief,
        canonical_panel,
        canonical_frame_result,
    )

    audit_binding = canonical_panel["audit_binding"]
    if audit_binding["applicability"] == "legacy_v2_migration":
        if workflow_state is not None or construction_audit is not None:
            _fail(
                "$.panel.audit_binding",
                "legacy_v2_migration requires workflow_state and "
                "construction_audit to be null",
            )
        return (
            canonical_brief,
            canonical_panel,
            canonical_frame_result,
            canonical_composition,
            canonical_validity,
            None,
            None,
        )
    if workflow_state is None or construction_audit is None:
        _fail(
            "$.panel.audit_binding",
            "release_b1 requires workflow_state and construction_audit",
        )

    validate_workflow_state, require_passing_audit = _panel_builder_contracts()
    canonical_workflow = validate_workflow_state(workflow_state)
    if canonical_workflow["workflow_id"] != canonical_brief["workflow_state_binding"]:
        _fail("$.workflow_state.workflow_id", "does not match the brief binding")
    if canonical_workflow["panel_id"] != canonical_panel["panel_id"]:
        _fail("$.workflow_state.panel_id", "does not match the panel")
    if canonical_workflow["panel_version"] != canonical_panel["version"]:
        _fail("$.workflow_state.panel_version", "does not match the panel")
    if canonical_workflow["state"] != "approved":
        _fail("$.workflow_state.state", "must be approved")
    current_review_chain = (
        panel_review_manifest_sha256 is not None
        and current_report_inputs_sha256 is not None
        and current_report_manifest_sha256 is not None
    )
    expected_workflow_bindings = {
        "brief_sha256": v2_brief_sha256,
        "panel_sha256": v2_panel_sha256,
        "report_inputs_sha256": (
            current_report_inputs_sha256
            if current_review_chain
            else audit_binding["report_inputs_sha256"]
        ),
        "audit_sha256": (
            _sha256_json(construction_audit).removeprefix("sha256:")
            if current_review_chain
            else audit_binding["audit_sha256"]
        ),
        "package_sha256": None,
    }
    if canonical_workflow["bindings"] != expected_workflow_bindings:
        _fail(
            "$.workflow_state.bindings",
            "must exactly match independently derived document bindings",
        )
    expected_workflow_targets = {
        "evidence_synthesis": v2_brief_sha256,
        "panel_construction": (
            panel_review_manifest_sha256
            if panel_review_manifest_sha256 is not None
            else v2_panel_sha256
        ),
    }
    for index, approval in enumerate(canonical_workflow["approvals"]):
        expected_target = expected_workflow_targets.get(str(approval["scope"]))
        if (
            expected_target is not None
            and approval["target_sha256"] != expected_target
        ):
            _fail(
                f"$.workflow_state.approvals[{index}].target_sha256",
                (
                    "target must bind the exact panel review manifest"
                    if approval["scope"] == "panel_construction"
                    and panel_review_manifest_sha256 is not None
                    else "target must bind the independently derived v2 document"
                ),
            )

    if not isinstance(construction_audit, Mapping):
        _fail("$.construction_audit", "must be an object")
    expected_audit_bindings = {
        "brief_sha256": v2_brief_sha256,
        "panel_sha256": v2_panel_sha256,
        "evidence_ledger_sha256": audit_binding["evidence_ledger_sha256"],
        "finding_support_sha256": audit_binding["finding_support_sha256"],
        "synthesis_matrix_sha256": audit_binding["synthesis_matrix_sha256"],
        "report_manifest_sha256": (
            current_report_manifest_sha256
            if current_review_chain
            else audit_binding["report_manifest_sha256"]
        ),
        "population_frame_result_sha256":
            expected_frame_result_digest.removeprefix("sha256:"),
        "population_frame_sha256": (
            None
            if expected_frame_digest is None
            else expected_frame_digest.removeprefix("sha256:")
        ),
        "composition_plan_sha256":
            composition_digest.removeprefix("sha256:"),
        "validity_profile_sha256":
            canonical_validity_digest.removeprefix("sha256:"),
        "authorized_handoff_sha256": (
            None
            if canonical_panel["authorized_handoff_sha256"] is None
            else str(
                canonical_panel["authorized_handoff_sha256"]
            ).removeprefix("sha256:")
        ),
    }
    canonical_audit = require_passing_audit(
        construction_audit,
        expected_bindings=expected_audit_bindings,
    )
    canonical_audit_sha256 = _sha256_json(canonical_audit).removeprefix(
        "sha256:"
    )
    if (
        not current_review_chain
        and canonical_audit_sha256 != audit_binding["audit_sha256"]
    ):
        _fail(
            "$.construction_audit",
            "audit digest does not match $.panel.audit_binding.audit_sha256",
        )
    if (
        canonical_audit["schema_version"] != "panel-construction-audit-v2"
        or canonical_audit.get("applicability") != "release_b1"
    ):
        _fail(
            "$.construction_audit",
            "must be the Release B1 panel-construction-audit-v2 contract",
        )
    if (
        canonical_audit["auditor_run_id"]
        != canonical_panel["audit_binding"]["auditor_run_id"]
    ):
        _fail(
            "$.construction_audit.auditor_run_id",
            "does not match the panel audit binding",
        )
    if canonical_audit["panel_id"] != canonical_panel["panel_id"]:
        _fail("$.construction_audit.panel_id", "does not match the panel")
    if canonical_audit["panel_version"] != canonical_panel["version"]:
        _fail("$.construction_audit.panel_version", "does not match the panel")

    documents = [
        canonical_brief,
        canonical_panel,
        canonical_frame_result,
    ]
    documents.extend(
        [
            canonical_composition,
            canonical_validity,
            canonical_workflow,
            canonical_audit,
        ]
    )
    return tuple(documents)


__all__ = [
    "AudienceResearchV3ValidationError",
    "CELL_STATUSES",
    "COMPOSITION_PLAN_VERSION",
    "EVIDENCE_BASES",
    "FRAME_ELIGIBILITY",
    "FRAME_REQUEST_VERSION",
    "OBSERVATION_BATCH_VERSION",
    "OUTCOME_FEEDBACK_VERSION",
    "PANEL_TIERS",
    "POPULATION_FRAME_VERSION",
    "RESEARCH_BRIEF_V3",
    "SAVED_PANEL_V3",
    "VALIDITY_AXIS_STATUSES",
    "VALIDITY_PROFILE_VERSION",
    "validate_audience_research_v3",
    "validate_composition_plan",
    "validate_frame_request",
    "validate_observation_batch",
    "validate_outcome_feedback",
    "validate_population_frame",
    "validate_research_brief_v3",
    "validate_saved_panel_v3",
    "validate_v3_runtime_authority",
    "validate_validity_profile",
]
