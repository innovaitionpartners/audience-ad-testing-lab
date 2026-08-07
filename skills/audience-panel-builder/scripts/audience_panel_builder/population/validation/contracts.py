"""Canonical, closed contracts for Tier 4 held-out ordering validation.

These validators intentionally validate documents, not the underlying panel or
Ad Testing result files.  Later adapters authenticate those source bytes.  At
this boundary we make every declared identity, scope, chronology, and outcome
projection explicit and immutable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any
import weakref

from ...common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_timestamp,
    sha256_json,
)


PREREGISTRATION_VERSION = "panel-validation-preregistration-v1"
SHARED_OUTCOME_EVIDENCE_VERSION = "panel-shared-outcome-evidence-v1"
VALIDATION_OBSERVATION_VERSION = "panel-validation-observation-v1"
COMPARISON_VERSION = "panel-synthetic-outcome-comparison-v1"
EVALUATION_VERSION = "panel-held-out-evaluation-v1"
TIER4_CLAIM_VERSION = "panel-tier4-claim-v1"
CLAIM_FAMILY_VERSION = "panel-validation-claim-family-v1"
AUTHORITY_REGISTRY_VERSION = "panel-validation-authority-registry-v1"
TIER4_CLAIM_TEXT = (
    "This held-out validation supports using the panel to prioritize "
    "creatives within the registered scope."
)
TIER4_REQUIRED_DISCLAIMER = (
    "Applies only to the registered panel, frozen ordering, metric, and "
    "held-out outcome scope."
)
TIER4_REFRESH_TRIGGERS = [
    "claim expiry",
    "panel or frozen-ordering change",
    "scope or metric change",
]
PRODUCTION_AUTHORITY_REGISTRY_ID = "innovaition-tier4-authority-v1"
_PRODUCTION_AUTHORITY_SECRET_SHA256 = (
    "sha256:a42bc5c08d8a588d74f16c730d96637f33ab7628137b1ffa02d12961cdbe34be"
)

HOLDOUT_STATUSES = frozenset({
    "eligible_held_out", "in_sample", "leaked", "mismatched", "descriptive_only",
})
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PERSON_LEVEL_KEYS = frozenset({
    "person_id", "user_id", "email", "phone", "address", "ip_address",
    "individual_id", "customer_id", "respondent_id",
})

_PREREGISTRATION_KEYS = {
    "schema_version", "registration_id", "registered_at", "registered_by", "status",
    "panel_binding", "synthetic_surface", "claim_scope", "primary_metric",
    "secondary_metrics", "validation_blocks", "holdout_partition", "prior_outcome_access",
    "analysis_rules", "eligibility_thresholds", "segment_rules", "multiplicity_rules",
    "interim_analysis_rules", "study_design_power", "segment_inventory",
    "approval", "registration_sha256",
}
_SHARED_KEYS = {
    "schema_version", "shared_evidence_id", "study_id", "block_id", "arm_id",
    "creative_binding", "outcome_scope", "metric", "metric_family", "units",
    "assignment", "windows", "aggregate", "precision", "sample", "missingness",
    "segment_ids", "exclusions", "source", "outcome_accessed_at", "limitations",
    "shared_evidence_sha256",
}
_OBSERVATION_KEYS = {
    "schema_version", "observation_id", "registration_binding",
    "shared_outcome_evidence_binding", "block_id", "arm_id", "creative_binding",
    "synthetic_binding", "panel_binding", "claim_scope", "outcome_scope", "metric",
    "metric_family", "units", "assignment", "windows", "aggregate", "precision",
    "sample", "missingness", "segment_ids", "exclusions", "source", "outcome_accessed_at",
    "holdout_status", "limitations", "observation_sha256",
}
_COMPARISON_KEYS = {
    "schema_version", "comparison_id", "registration_binding", "panel_binding",
    "synthetic_result_binding", "block_binding", "metric_binding", "observations",
    "arm_mappings",
    "mapping_coverage", "observed_ordering", "synthetic_ordering", "pairwise_comparisons",
    "block_evidence", "segment_evidence", "comparison_sha256",
}
_EVALUATION_KEYS = {
    "schema_version", "evaluation_id", "evaluated_at", "registration_binding",
    "panel_binding", "claim_scope", "metric_binding", "block_inventory", "coverage",
    "missingness", "sample_sufficiency", "independence", "leakage", "multiplicity",
    "repeated_looks", "power", "overall_diagnostics", "segment_diagnostics",
    "influence_diagnostics",
    "preregistration", "comparisons", "claim_family",
    "gate_results", "decision", "limitations", "evaluation_sha256",
}
_CLAIM_KEYS = {
    "schema_version", "claim_id", "issued_at", "expires_at", "status", "panel_binding",
    "registration_binding", "evaluation_binding", "claim_scope", "claim_text",
    "required_disclaimer", "diagnostic_summary", "limitations", "refresh_triggers", "claim_sha256",
}
_FAMILY_KEYS = {
    "schema_version", "family_id", "family_alpha", "member_registration_ids",
    "member_comparison_sha256", "member_one_sided_p_values", "correction_method",
    "adjusted_p_values", "member_preregistrations", "member_comparisons",
    "complete", "family_sha256",
}
_AUTHORITY_REGISTRY_KEYS = {
    "schema_version", "registry_id", "entries", "registry_sha256",
    "registry_hmac_sha256",
}
_AUTHORITY_ENTRY_KEYS = {
    "authority_id", "registration_id", "approved_at", "registered_at",
    "authority_root_sha256", "authority_index_sha256",
    "design_evidence_sha256",
}


def _authority_secret_fingerprint_for_registry(registry_id: str) -> str | None:
    """Return the runtime-pinned trust identity for one production registry."""

    if registry_id == PRODUCTION_AUTHORITY_REGISTRY_ID:
        return _PRODUCTION_AUTHORITY_SECRET_SHA256
    return None


def read_protected_authority_secret(path: Path) -> bytes:
    """Read one owner-only regular secret file without following symlinks."""

    if not isinstance(path, Path):
        raise ContractError("authority secret path must be a Path")
    try:
        before = path.lstat()
    except OSError as exc:
        raise ContractError("authority secret file is unreadable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ContractError(
            "authority secret file must be one non-symlink regular file"
        )
    if before.st_mode & 0o077:
        raise ContractError("authority secret file must be owner-only")
    if hasattr(os, "geteuid") and before.st_uid != os.geteuid():
        raise ContractError(
            "authority secret file must be owned by the current runtime user"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError(
            "authority secret file could not be opened safely"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_uid)
        ):
            raise ContractError("authority secret file identity changed")
        value = os.read(descriptor, 4097)
        if len(value) > 4096:
            raise ContractError("authority secret file is too large")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
             after.st_ctime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size,
                opened.st_mtime_ns, opened.st_ctime_ns)
        ):
            raise ContractError("authority secret file changed while read")
        return value
    finally:
        os.close(descriptor)
_SEALED_SYNTHETIC_SURFACE_KEYS = {
    "surface", "method", "stage", "run_id", "result_path", "result_sha256",
    "result_bytes_sha256", "manifest_sha256", "lineage_bundle_sha256",
    "producer_evidence_sha256", "producer_semantics_sha256", "frozen_at",
    "producer_evidence_sealed_at", "eligible_creatives",
}
_SYNTHETIC_SURFACE_IDENTITIES = {
    "complete_exposure_ordering": {
        "method": "complete_exposure",
        "stage": "screening",
        "result_path": "screening-model-results.json",
    },
    "maxdiff_screening_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "screening",
        "result_path": "screening-model-results.json",
    },
    "pairwise_boundary_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "boundary",
        "result_path": "boundary-results.json",
    },
}
_COMPACT_SYNTHETIC_BINDING_KEYS = {"surface", "run_id", "result_sha256"}
_COMPLETE_EXPOSURE_TIE_HANDLING = {
    "ordering_equivalence", "ordering_tiebreak",
}
_BUCKETED_TIE_HANDLING = {
    "ordering_equivalence", "ordering_tiebreak",
    "effective_ordering_tolerance", "rounding_rule",
}


def _finite_json(value: object, path: str = "$") -> None:
    """Reject values that cannot be represented by canonical JSON safely."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{path} must contain only finite numbers")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} object keys must be strings")
            if key in _PERSON_LEVEL_KEYS:
                raise ContractError(f"{path}.{key} is a forbidden person-level field")
            _finite_json(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_json(item, f"{path}[{index}]")
        return
    raise ContractError(f"{path} must contain JSON-compatible values")


def _canonical(payload: object, keys: set[str], path: str) -> dict[str, object]:
    _finite_json(payload, path)
    document = dict(require_object(payload, keys, path))
    return deepcopy(document)


def _digest(value: object, path: str) -> str:
    digest = require_string(value, path)
    if not SHA256_RE.fullmatch(digest):
        raise ContractError(f"{path} must be a prefixed SHA-256")
    return digest


def _validate_self_hash(document: Mapping[str, object], *, field: str, path: str) -> str:
    supplied = _digest(document[field], f"{path}.{field}")
    unhashed = deepcopy(dict(document))
    unhashed[field] = None
    if sha256_json(unhashed) != supplied:
        raise ContractError(f"{path}.{field} does not match canonical bytes")
    return supplied


def _number(value: object, path: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{path} must be a finite number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ContractError(f"{path} must be at least {minimum}")
    return number


def _nonnegative_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{path} must be a non-negative integer")
    return value


def _positive_integer(value: object, path: str) -> int:
    result = _nonnegative_integer(value, path)
    if result == 0:
        raise ContractError(f"{path} must be a positive integer")
    return result


def _unique_identifiers(value: object, path: str) -> list[str]:
    result = [require_identifier(item, f"{path}[{index}]") for index, item in enumerate(require_array(value, path, nonempty=True))]
    if len(result) != len(set(result)):
        raise ContractError(f"{path} must contain unique values")
    return result


def _panel_binding(value: object, path: str) -> dict[str, object]:
    result = _canonical(value, {"panel_id", "panel_version", "panel_sha256", "package_sha256"}, path)
    require_identifier(result["panel_id"], f"{path}.panel_id")
    require_string(result["panel_version"], f"{path}.panel_version")
    _digest(result["panel_sha256"], f"{path}.panel_sha256")
    _digest(result["package_sha256"], f"{path}.package_sha256")
    return result


def _synthetic_binding(value: object, path: str) -> dict[str, object]:
    result = _canonical(value, _COMPACT_SYNTHETIC_BINDING_KEYS, path)
    require_enum(result["surface"], set(_SYNTHETIC_SURFACE_IDENTITIES), f"{path}.surface")
    require_identifier(result["run_id"], f"{path}.run_id")
    _digest(result["result_sha256"], f"{path}.result_sha256")
    return result


def _sealed_synthetic_surface(value: object, path: str) -> dict[str, object]:
    """Validate the full producer-authenticated preregistration surface."""
    result = _canonical(value, _SEALED_SYNTHETIC_SURFACE_KEYS, path)
    surface = require_enum(result["surface"], set(_SYNTHETIC_SURFACE_IDENTITIES), f"{path}.surface")
    identity = _SYNTHETIC_SURFACE_IDENTITIES[surface]
    for field, expected in identity.items():
        actual = require_string(result[field], f"{path}.{field}")
        if actual != expected:
            raise ContractError(f"{path}.{field} must be {expected} for {surface}")
    require_identifier(result["run_id"], f"{path}.run_id")
    for field in (
        "result_sha256", "result_bytes_sha256", "manifest_sha256",
        "lineage_bundle_sha256", "producer_evidence_sha256",
        "producer_semantics_sha256",
    ):
        _digest(result[field], f"{path}.{field}")
    require_timestamp(result["frozen_at"], f"{path}.frozen_at")
    require_timestamp(
        result["producer_evidence_sealed_at"],
        f"{path}.producer_evidence_sealed_at",
    )
    creatives = require_array(result["eligible_creatives"], f"{path}.eligible_creatives", nonempty=True)
    creative_ids: list[str] = []
    for index, creative in enumerate(creatives):
        binding = _creative_binding(creative, f"{path}.eligible_creatives[{index}]")
        creative_ids.append(binding["creative_id"])
    if creative_ids != sorted(creative_ids):
        raise ContractError(f"{path}.eligible_creatives must be sorted by creative_id")
    if len(creative_ids) != len(set(creative_ids)):
        raise ContractError(f"{path}.eligible_creatives must contain unique creative IDs")
    return result


def project_synthetic_result_binding(
    surface: Mapping[str, object],
) -> dict[str, object]:
    """Return the sole compact synthetic identity permitted downstream."""
    validated = _sealed_synthetic_surface(surface, "synthetic_surface")
    return {
        key: deepcopy(validated[key])
        for key in ("surface", "run_id", "result_sha256")
    }


def _tie_handling(value: object, path: str, *, surface: str) -> dict[str, object]:
    if surface == "complete_exposure_ordering":
        result = _canonical(value, _COMPLETE_EXPOSURE_TIE_HANDLING, path)
        fixed = {
            "ordering_equivalence": "exact-utility-equality-v1",
            "ordering_tiebreak": "creative-id-serialization-only-v1",
        }
    else:
        result = _canonical(value, _BUCKETED_TIE_HANDLING, path)
        fixed = {
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "rounding_rule": "python-half-even-v1",
        }
        tolerance = _number(
            result["effective_ordering_tolerance"],
            f"{path}.effective_ordering_tolerance",
        )
        if tolerance <= 0:
            raise ContractError(f"{path}.effective_ordering_tolerance must be positive")
    for field, expected in fixed.items():
        actual = require_string(result[field], f"{path}.{field}")
        if actual != expected:
            raise ContractError(f"{path}.{field} must be {expected} for {surface}")
    return result


def _creative_binding(value: object, path: str) -> dict[str, object]:
    result = _canonical(value, {"creative_id", "creative_sha256"}, path)
    require_identifier(result["creative_id"], f"{path}.creative_id")
    _digest(result["creative_sha256"], f"{path}.creative_sha256")
    return result


def _outcome_scope(value: object, path: str) -> dict[str, object]:
    keys = {"cohort_id", "segment_id", "channel", "placement", "objective", "geography", "validation_window"}
    result = _canonical(value, keys, path)
    for key in keys:
        require_string(result[key], f"{path}.{key}")
    return result


def _claim_scope(value: object, path: str) -> dict[str, object]:
    result = _canonical(value, {"panel_binding", "synthetic_binding", "outcome_scope"}, path)
    _panel_binding(result["panel_binding"], f"{path}.panel_binding")
    _synthetic_binding(result["synthetic_binding"], f"{path}.synthetic_binding")
    _outcome_scope(result["outcome_scope"], f"{path}.outcome_scope")
    return result


def _metric(value: object, path: str) -> dict[str, object]:
    keys = {"name", "definition", "direction", "exposure_unit", "outcome_unit", "measurement_window", "attribution_window", "practical_equivalence_margin", "smallest_effect_of_interest"}
    result = _canonical(value, keys, path)
    for key in keys - {"direction", "practical_equivalence_margin", "smallest_effect_of_interest"}:
        require_string(result[key], f"{path}.{key}")
    require_enum(result["direction"], {"higher_is_better", "lower_is_better"}, f"{path}.direction")
    _number(result["practical_equivalence_margin"], f"{path}.practical_equivalence_margin", minimum=0)
    _number(result["smallest_effect_of_interest"], f"{path}.smallest_effect_of_interest", minimum=0)
    return result


def _outcome_fields(document: Mapping[str, object], path: str) -> None:
    _creative_binding(document["creative_binding"], f"{path}.creative_binding")
    _outcome_scope(document["outcome_scope"], f"{path}.outcome_scope")
    _metric(document["metric"], f"{path}.metric")
    family = require_enum(document["metric_family"], {"binary_proportion", "continuous_mean", "event_rate"}, f"{path}.metric_family")
    units = _canonical(document["units"], {"exposure", "outcome"}, f"{path}.units")
    windows = _canonical(document["windows"], {"measurement", "attribution"}, f"{path}.windows")
    for key in ("exposure", "outcome"):
        require_string(units[key], f"{path}.units.{key}")
    for key in ("measurement", "attribution"):
        require_string(windows[key], f"{path}.windows.{key}")
    assignment = _canonical(document["assignment"], {"design", "unit", "leakage_detected"}, f"{path}.assignment")
    require_string(assignment["design"], f"{path}.assignment.design")
    require_string(assignment["unit"], f"{path}.assignment.unit")
    if not isinstance(assignment["leakage_detected"], bool):
        raise ContractError(f"{path}.assignment.leakage_detected must be a boolean")
    aggregate_keys = {
        "binary_proportion": {"success_count", "eligible_exposure_count"},
        "continuous_mean": {"sample_count", "mean", "standard_deviation"},
        "event_rate": {"event_count", "exposure_time"},
    }[family]
    aggregate = _canonical(document["aggregate"], aggregate_keys, f"{path}.aggregate")
    # Metric adapters make the family-specific sufficiency decision, but no
    # anonymous or nonfinite aggregate fields may cross this boundary.
    for key, item in aggregate.items():
        if key in {"success_count", "eligible_exposure_count", "sample_count", "event_count"}:
            _nonnegative_integer(item, f"{path}.aggregate.{key}")
        else:
            _number(item, f"{path}.aggregate.{key}", minimum=0 if key != "mean" else None)
    if family == "binary_proportion" and aggregate["success_count"] > aggregate["eligible_exposure_count"]:
        raise ContractError(f"{path}.aggregate.success_count cannot exceed eligible_exposure_count")
    precision = _canonical(document["precision"], {"confidence_level"}, f"{path}.precision")
    level = _number(precision["confidence_level"], f"{path}.precision.confidence_level")
    if not 0 < level < 1:
        raise ContractError(f"{path}.precision.confidence_level must be between zero and one")
    sample = _canonical(
        document["sample"],
        {"eligible_exposure_count", "effective_sample_size"},
        f"{path}.sample",
    )
    _nonnegative_integer(sample["eligible_exposure_count"], f"{path}.sample.eligible_exposure_count")
    effective_sample = _number(
        sample["effective_sample_size"],
        f"{path}.sample.effective_sample_size",
        minimum=0,
    )
    if effective_sample > sample["eligible_exposure_count"]:
        raise ContractError(
            f"{path}.sample.effective_sample_size cannot exceed eligible exposures"
        )
    missingness = _canonical(
        document["missingness"],
        {
            "status", "eligible_exposure_count", "missing_outcome_count",
            "rate",
        },
        f"{path}.missingness",
    )
    require_string(missingness["status"], f"{path}.missingness.status")
    denominator = _nonnegative_integer(
        missingness["eligible_exposure_count"],
        f"{path}.missingness.eligible_exposure_count",
    )
    missing_count = _nonnegative_integer(
        missingness["missing_outcome_count"],
        f"{path}.missingness.missing_outcome_count",
    )
    if missing_count > denominator:
        raise ContractError(
            f"{path}.missingness.missing_outcome_count cannot exceed eligible_exposure_count"
        )
    if denominator != sample["eligible_exposure_count"]:
        raise ContractError(
            f"{path}.missingness eligible exposures must match sample"
        )
    rate = _number(missingness["rate"], f"{path}.missingness.rate", minimum=0)
    if rate > 1:
        raise ContractError(f"{path}.missingness.rate must be at most one")
    expected_rate = missing_count / denominator if denominator else 0.0
    if not math.isclose(rate, expected_rate, rel_tol=0.0, abs_tol=1e-12):
        raise ContractError(
            f"{path}.missingness.rate must equal the authenticated exposure counts"
        )
    expected_missingness_status = "none" if missing_count == 0 else "present"
    if missingness["status"] != expected_missingness_status:
        raise ContractError(
            f"{path}.missingness.status must be {expected_missingness_status}"
        )
    analyzable_count = denominator - missing_count
    if effective_sample > analyzable_count:
        raise ContractError(
            f"{path}.sample.effective_sample_size cannot exceed analyzable outcomes"
        )
    if family == "binary_proportion":
        aggregate_count = aggregate["eligible_exposure_count"]
    elif family == "continuous_mean":
        aggregate_count = aggregate["sample_count"]
    else:
        aggregate_count = None
        if float(aggregate["exposure_time"]) <= 0:
            raise ContractError(
                f"{path}.aggregate.exposure_time must be positive"
            )
    if aggregate_count is not None:
        if not math.isclose(
            float(aggregate_count), float(analyzable_count),
            rel_tol=0.0, abs_tol=1e-12,
        ):
            raise ContractError(
                f"{path}.aggregate denominator must equal analyzable outcomes"
            )
    segment_ids = _unique_identifiers(
        document["segment_ids"], f"{path}.segment_ids",
    )
    if segment_ids != sorted(segment_ids):
        raise ContractError(f"{path}.segment_ids must be canonical sorted order")
    exclusions = require_array(document["exclusions"], f"{path}.exclusions")
    for index, item in enumerate(exclusions):
        require_string(item, f"{path}.exclusions[{index}]")
    source = _canonical(document["source"], {"source_id", "source_sha256", "permission_confirmed"}, f"{path}.source")
    require_identifier(source["source_id"], f"{path}.source.source_id")
    _digest(source["source_sha256"], f"{path}.source.source_sha256")
    if not isinstance(source["permission_confirmed"], bool):
        raise ContractError(f"{path}.source.permission_confirmed must be a boolean")
    if source["permission_confirmed"] is not True:
        raise ContractError(f"{path}.source.permission_confirmed must be true for evaluation-grade evidence")
    require_timestamp(document["outcome_accessed_at"], f"{path}.outcome_accessed_at")
    limitations = require_array(document["limitations"], f"{path}.limitations")
    for index, item in enumerate(limitations):
        require_string(item, f"{path}.limitations[{index}]")


def _registration_binding(value: object, path: str, *, extended: bool) -> dict[str, object]:
    keys = {"registration_id", "registration_sha256"}
    if extended:
        keys |= {
            "registered_at", "status", "prior_outcome_access_sha256",
            "prior_outcome_access_hashes", "holdout_partition", "claim_scope",
            "multiplicity_rules", "preregistration",
        }
    result = _canonical(value, keys, path)
    require_identifier(result["registration_id"], f"{path}.registration_id")
    _digest(result["registration_sha256"], f"{path}.registration_sha256")
    if extended:
        require_timestamp(result["registered_at"], f"{path}.registered_at")
        require_enum(result["status"], {"registered", "withdrawn"}, f"{path}.status")
        _digest(result["prior_outcome_access_sha256"], f"{path}.prior_outcome_access_sha256")
        hashes = require_array(result["prior_outcome_access_hashes"], f"{path}.prior_outcome_access_hashes")
        if len(hashes) != len(set(hashes)):
            raise ContractError(f"{path}.prior_outcome_access_hashes must contain unique values")
        for index, digest in enumerate(hashes):
            _digest(digest, f"{path}.prior_outcome_access_hashes[{index}]")
        if sha256_json(hashes) != result["prior_outcome_access_sha256"]:
            raise ContractError(f"{path}.prior_outcome_access_sha256 does not match canonical bytes")
        preregistration = validate_preregistration(result["preregistration"])
        if preregistration["status"] != "registered":
            raise ContractError(f"{path}.preregistration must be registered")
        copied_fields = {
            "registration_id": "registration_id",
            "registration_sha256": "registration_sha256",
            "registered_at": "registered_at",
            "status": "status",
            "holdout_partition": "holdout_partition",
            "claim_scope": "claim_scope",
            "multiplicity_rules": "multiplicity_rules",
        }
        for binding_field, registration_field in copied_fields.items():
            if result[binding_field] != preregistration[registration_field]:
                raise ContractError(f"{path}.{binding_field} must match the sealed preregistration")
        expected_hashes = [entry["access_sha256"] for entry in preregistration["prior_outcome_access"]]
        if hashes != expected_hashes:
            raise ContractError(f"{path}.prior_outcome_access_hashes must match the sealed preregistration")
    return result


def _design_projection(document: Mapping[str, object]) -> dict[str, object]:
    """Project every preregistered fact that an outcome-blind approver authorizes."""
    power = dict(document["study_design_power"])  # type: ignore[arg-type]
    power.pop("evidence_sha256", None)
    power.pop("approval_sha256", None)
    segments: list[dict[str, object]] = []
    for raw_segment in document["segment_inventory"]:  # type: ignore[union-attr]
        segment = dict(raw_segment)
        segment.pop("evidence_sha256", None)
        segment.pop("approval_sha256", None)
        segments.append(segment)
    approval = document["approval"]
    assert isinstance(approval, Mapping)
    return {
        "schema_version": document["schema_version"],
        "registration_id": document["registration_id"],
        "registered_at": document["registered_at"],
        "registered_by": document["registered_by"],
        "status": document["status"],
        "panel_binding": deepcopy(document["panel_binding"]),
        "synthetic_surface": deepcopy(document["synthetic_surface"]),
        "claim_scope": deepcopy(document["claim_scope"]),
        "primary_metric": deepcopy(document["primary_metric"]),
        "secondary_metrics": deepcopy(document["secondary_metrics"]),
        "validation_blocks": deepcopy(document["validation_blocks"]),
        "holdout_partition": deepcopy(document["holdout_partition"]),
        "prior_outcome_access": deepcopy(document["prior_outcome_access"]),
        "analysis_rules": deepcopy(document["analysis_rules"]),
        "eligibility_thresholds": deepcopy(document["eligibility_thresholds"]),
        "segment_rules": deepcopy(document["segment_rules"]),
        "multiplicity_rules": deepcopy(document["multiplicity_rules"]),
        "interim_analysis_rules": deepcopy(document["interim_analysis_rules"]),
        "study_design_power": power,
        "segment_inventory": segments,
        "approval": {
            "approved_at": approval["approved_at"],
            "approved_by": approval["approved_by"],
            "authority_root_sha256": approval["authority_root_sha256"],
            "authority_index_sha256": approval["authority_index_sha256"],
        },
    }


def design_evidence_sha256(
    payload: object, *, authority_root_sha256: str,
    authority_index_sha256: str,
) -> str:
    """Calculate the complete design digest named by a trusted registry entry."""
    document = _canonical(payload, _PREREGISTRATION_KEYS, "preregistration")
    approval = dict(document["approval"])  # type: ignore[arg-type]
    approval["authority_root_sha256"] = _digest(
        authority_root_sha256, "authority_root_sha256",
    )
    approval["authority_index_sha256"] = _digest(
        authority_index_sha256, "authority_index_sha256",
    )
    document["approval"] = approval
    return sha256_json(_design_projection(document))


def _build_authority_api():
    """Load trusted registry entries and mint exact non-serializable authority."""

    @dataclass(frozen=True)
    class RegistryState:
        entries: tuple[tuple[str, object], ...]

    registry_states: weakref.WeakKeyDictionary[
        object, RegistryState
    ] = weakref.WeakKeyDictionary()

    @dataclass(frozen=True)
    class ApprovalState:
        design_evidence_sha256: str
        approval_sha256: str
        authority_root_sha256: str
        authority_index_sha256: str

    approval_states: weakref.WeakKeyDictionary[
        object, ApprovalState
    ] = weakref.WeakKeyDictionary()

    class RegistryCapability:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args: object, **kwargs: object):
            del cls, args, kwargs
            raise ContractError(
                "ValidatedAuthorityRegistry can only be created by "
                "load_trusted_authority_registry"
            )

        def __setattr__(self, name: str, value: object) -> None:
            del self, name, value
            raise ContractError("validated authority registries are immutable")

    RegistryCapability.__name__ = "ValidatedAuthorityRegistry"
    RegistryCapability.__qualname__ = "ValidatedAuthorityRegistry"

    def require(capability: object, document: Mapping[str, object]) -> None:
        if not isinstance(capability, ApprovalCapability):
            raise ContractError("validated design approval capability is invalid")
        state = approval_states.get(capability)
        if state is None:
            raise ContractError("validated design approval capability is inactive")
        approval = document["approval"]
        projection_sha256 = sha256_json(_design_projection(document))
        if (
            projection_sha256 != state.design_evidence_sha256
            or approval["design_evidence_sha256"] != state.design_evidence_sha256
            or approval["approval_sha256"] != state.approval_sha256
            or approval["authority_root_sha256"] != state.authority_root_sha256
            or approval["authority_index_sha256"] != state.authority_index_sha256
        ):
            raise ContractError(
                "validated design approval capability does not authorize this exact design"
            )

    class ApprovalCapability:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args: object, **kwargs: object):
            del cls, args, kwargs
            raise ContractError(
                "ValidatedDesignApproval can only be created by "
                "approve_preregistration_design"
            )

        def __setattr__(self, name: str, value: object) -> None:
            del self, name, value
            raise ContractError("validated design approval capabilities are immutable")

    ApprovalCapability.__name__ = "ValidatedDesignApproval"
    ApprovalCapability.__qualname__ = "ValidatedDesignApproval"

    def load_registry(
        path: Path, *, authority_secret: bytes,
    ) -> RegistryCapability:
        if not isinstance(path, Path):
            raise ContractError("trusted authority registry path must be a Path")
        if (
            not isinstance(authority_secret, bytes)
            or len(authority_secret) < 32
        ):
            raise ContractError(
                "trusted authority registry requires an out-of-band "
                "authority secret of at least 32 bytes"
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ContractError("trusted authority registry is unreadable") from exc
        if len(raw) > 1_000_000:
            raise ContractError("trusted authority registry is too large")
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                "trusted authority registry must be canonical JSON"
            ) from exc
        document = _canonical(
            payload, _AUTHORITY_REGISTRY_KEYS, "authority_registry",
        )
        if document["schema_version"] != AUTHORITY_REGISTRY_VERSION:
            raise ContractError("authority_registry.schema_version is unknown")
        registry_id = require_identifier(
            document["registry_id"], "authority_registry.registry_id",
        )
        expected_secret_sha256 = _authority_secret_fingerprint_for_registry(
            registry_id,
        )
        if expected_secret_sha256 is None:
            raise ContractError(
                "authority_registry.registry_id is not a runtime-pinned "
                "production trust identity"
            )
        supplied_secret_sha256 = (
            "sha256:" + hashlib.sha256(authority_secret).hexdigest()
        )
        if not hmac.compare_digest(
            supplied_secret_sha256, expected_secret_sha256,
        ):
            raise ContractError(
                "authority secret does not match the runtime-pinned "
                "production trust identity"
            )
        entries = require_array(
            document["entries"], "authority_registry.entries", nonempty=True,
        )
        checked_entries: list[dict[str, object]] = []
        keys: list[tuple[str, str]] = []
        for index, raw_entry in enumerate(entries):
            entry = _canonical(
                raw_entry, _AUTHORITY_ENTRY_KEYS,
                f"authority_registry.entries[{index}]",
            )
            authority_id = require_identifier(
                entry["authority_id"],
                f"authority_registry.entries[{index}].authority_id",
            )
            registration_id = require_identifier(
                entry["registration_id"],
                f"authority_registry.entries[{index}].registration_id",
            )
            require_timestamp(
                entry["approved_at"],
                f"authority_registry.entries[{index}].approved_at",
            )
            require_timestamp(
                entry["registered_at"],
                f"authority_registry.entries[{index}].registered_at",
            )
            for field in (
                "authority_root_sha256", "authority_index_sha256",
                "design_evidence_sha256",
            ):
                _digest(
                    entry[field],
                    f"authority_registry.entries[{index}].{field}",
                )
            keys.append((authority_id, registration_id))
            checked_entries.append(entry)
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ContractError(
                "authority_registry entries must be unique and sorted by "
                "authority_id and registration_id"
            )
        supplied_registry_sha = _digest(
            document["registry_sha256"],
            "authority_registry.registry_sha256",
        )
        unhashed = deepcopy(document)
        unhashed["registry_sha256"] = None
        unhashed["registry_hmac_sha256"] = None
        if sha256_json(unhashed) != supplied_registry_sha:
            raise ContractError(
                "authority_registry.registry_sha256 does not match canonical bytes"
            )
        supplied_hmac = require_string(
            document["registry_hmac_sha256"],
            "authority_registry.registry_hmac_sha256",
        )
        if not SHA256_RE.fullmatch(supplied_hmac):
            raise ContractError(
                "authority_registry.registry_hmac_sha256 must be a prefixed SHA-256"
            )
        unsigned = deepcopy(document)
        unsigned["registry_hmac_sha256"] = None
        expected_hmac = "sha256:" + hmac.new(
            authority_secret,
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied_hmac, expected_hmac):
            raise ContractError(
                "trusted authority registry HMAC authentication failed"
            )
        capability = object.__new__(RegistryCapability)
        registry_states[capability] = RegistryState(
            entries=tuple(
                tuple(sorted(entry.items())) for entry in checked_entries
            ),
        )
        return capability

    def approve(
        payload: object, *, authority_registry: object,
        authority_id: str,
    ) -> tuple[dict[str, object], ApprovalCapability]:
        document = _canonical(payload, _PREREGISTRATION_KEYS, "preregistration")
        if not isinstance(authority_registry, RegistryCapability):
            raise ContractError("validated authority registry capability is invalid")
        registry_state = registry_states.get(authority_registry)
        if registry_state is None:
            raise ContractError("validated authority registry capability is inactive")
        checked_authority_id = require_identifier(authority_id, "authority_id")
        matching = [
            dict(entry) for entry in registry_state.entries
            if dict(entry)["authority_id"] == checked_authority_id
            and dict(entry)["registration_id"] == document["registration_id"]
        ]
        if len(matching) != 1:
            raise ContractError(
                "trusted authority registry has no unique entry for this design"
            )
        entry = matching[0]
        root_sha = str(entry["authority_root_sha256"])
        index_sha = str(entry["authority_index_sha256"])
        approval = dict(document["approval"])  # type: ignore[arg-type]
        if (
            approval["approved_by"] != entry["authority_id"]
            or approval["approved_at"] != entry["approved_at"]
            or document["registered_at"] != entry["registered_at"]
            or document["registered_by"] != entry["authority_id"]
        ):
            raise ContractError(
                "preregistration authority and chronology must match the "
                "trusted registry entry"
            )
        approval.update({
            "authority_root_sha256": root_sha,
            "authority_index_sha256": index_sha,
        })
        document["approval"] = approval
        design_sha = sha256_json(_design_projection(document))
        if design_sha != entry["design_evidence_sha256"]:
            raise ContractError(
                "trusted authority registry entry does not authorize this "
                "exact complete design"
            )
        power = dict(document["study_design_power"])  # type: ignore[arg-type]
        power["evidence_sha256"] = sha256_json(
            _design_projection(document)["study_design_power"],
        )
        segments: list[dict[str, object]] = []
        for raw_segment in document["segment_inventory"]:  # type: ignore[union-attr]
            segment = dict(raw_segment)
            evidence = dict(segment)
            evidence.pop("evidence_sha256", None)
            evidence.pop("approval_sha256", None)
            segment["evidence_sha256"] = sha256_json(evidence)
            segments.append(segment)
        approval.update({
            "authority_root_sha256": root_sha,
            "authority_index_sha256": index_sha,
            "design_evidence_sha256": design_sha,
        })
        approval_payload = {
            "approved_at": approval["approved_at"],
            "approved_by": approval["approved_by"],
            "authority_root_sha256": root_sha,
            "authority_index_sha256": index_sha,
            "design_evidence_sha256": design_sha,
        }
        approval_sha = sha256_json(approval_payload)
        approval["approval_sha256"] = approval_sha
        power["approval_sha256"] = approval_sha
        for segment in segments:
            segment["approval_sha256"] = approval_sha
        document["study_design_power"] = power
        document["segment_inventory"] = segments
        document["approval"] = approval
        capability = object.__new__(ApprovalCapability)
        approval_states[capability] = ApprovalState(
            design_evidence_sha256=design_sha,
            approval_sha256=approval_sha,
            authority_root_sha256=root_sha,
            authority_index_sha256=index_sha,
        )
        return document, capability

    return RegistryCapability, ApprovalCapability, load_registry, approve, require


(
    ValidatedAuthorityRegistry,
    ValidatedDesignApproval,
    load_trusted_authority_registry,
    approve_preregistration_design,
    require_design_approval,
) = _build_authority_api()
del _build_authority_api


def authenticate_preregistration_design(
    payload: object, *, authority_registry: object,
) -> tuple[dict[str, object], object]:
    """Authenticate one already sealed design against the live trusted registry."""

    document = validate_preregistration(payload)
    selected_registry = authority_registry
    if isinstance(authority_registry, Mapping):
        selected_registry = authority_registry.get(document["registration_id"])
        if selected_registry is None:
            raise ContractError(
                "trusted authority registry set does not authorize this "
                "registration"
            )
    approved, capability = approve_preregistration_design(
        document,
        authority_registry=selected_registry,
        authority_id=str(document["approval"]["approved_by"]),
    )
    if approved != document:
        raise ContractError(
            "sealed preregistration is not the exact immutable design "
            "authorized by the trusted registry"
        )
    require_design_approval(capability, document)
    return document, capability


def validate_preregistration(payload: object) -> dict[str, object]:
    document = _canonical(payload, _PREREGISTRATION_KEYS, "preregistration")
    if document["schema_version"] != PREREGISTRATION_VERSION:
        raise ContractError("preregistration.schema_version is unknown")
    require_identifier(document["registration_id"], "preregistration.registration_id")
    registered_at = require_timestamp(document["registered_at"], "preregistration.registered_at")
    require_identifier(document["registered_by"], "preregistration.registered_by")
    require_enum(document["status"], {"registered", "withdrawn"}, "preregistration.status")
    _panel_binding(document["panel_binding"], "preregistration.panel_binding")
    synthetic_surface = _sealed_synthetic_surface(
        document["synthetic_surface"], "preregistration.synthetic_surface",
    )
    frozen_at = require_timestamp(
        synthetic_surface["frozen_at"], "preregistration.synthetic_surface.frozen_at",
    )
    producer_evidence_sealed_at = require_timestamp(
        synthetic_surface["producer_evidence_sealed_at"],
        "preregistration.synthetic_surface.producer_evidence_sealed_at",
    )
    if frozen_at > producer_evidence_sealed_at:
        raise ContractError(
            "preregistration.synthetic_surface.frozen_at must not follow producer_evidence_sealed_at"
        )
    if producer_evidence_sealed_at > registered_at:
        raise ContractError(
            "preregistration.synthetic_surface.producer_evidence_sealed_at must not follow registered_at"
        )
    scope = _claim_scope(document["claim_scope"], "preregistration.claim_scope")
    projected_synthetic_binding = project_synthetic_result_binding(synthetic_surface)
    if (
        scope["panel_binding"] != document["panel_binding"]
        or scope["synthetic_binding"] != projected_synthetic_binding
    ):
        raise ContractError("preregistration.claim_scope must bind the exact panel and synthetic surface")
    _metric(document["primary_metric"], "preregistration.primary_metric")
    secondary = require_array(document["secondary_metrics"], "preregistration.secondary_metrics")
    for index, item in enumerate(secondary):
        _metric(item, f"preregistration.secondary_metrics[{index}]")
    blocks = require_array(document["validation_blocks"], "preregistration.validation_blocks", nonempty=True)
    block_ids: set[str] = set()
    planned_segment_memberships: dict[tuple[str, str], list[str]] = {}
    for index, block in enumerate(blocks):
        item = _canonical(
            block,
            {
                "block_id", "study_id", "planned_arm_ids",
                "planned_effective_sample", "planned_segment_membership",
            },
            f"preregistration.validation_blocks[{index}]",
        )
        block_id = require_identifier(item["block_id"], f"preregistration.validation_blocks[{index}].block_id")
        require_identifier(item["study_id"], f"preregistration.validation_blocks[{index}].study_id")
        planned_arm_ids = _unique_identifiers(
            item["planned_arm_ids"],
            f"preregistration.validation_blocks[{index}].planned_arm_ids",
        )
        memberships = require_array(
            item["planned_segment_membership"],
            f"preregistration.validation_blocks[{index}].planned_segment_membership",
            nonempty=True,
        )
        membership_arm_ids: list[str] = []
        for membership_index, raw_membership in enumerate(memberships):
            membership = _canonical(
                raw_membership, {"arm_id", "segment_ids"},
                (
                    f"preregistration.validation_blocks[{index}]"
                    f".planned_segment_membership[{membership_index}]"
                ),
            )
            arm_id = require_identifier(
                membership["arm_id"],
                (
                    f"preregistration.validation_blocks[{index}]"
                    f".planned_segment_membership[{membership_index}].arm_id"
                ),
            )
            segment_membership = _unique_identifiers(
                membership["segment_ids"],
                (
                    f"preregistration.validation_blocks[{index}]"
                    f".planned_segment_membership[{membership_index}].segment_ids"
                ),
            )
            if segment_membership != sorted(segment_membership):
                raise ContractError(
                    "preregistration validation-block segment memberships "
                    "must use canonical sorted segment IDs"
                )
            membership_arm_ids.append(arm_id)
            planned_segment_memberships[(block_id, arm_id)] = segment_membership
        if membership_arm_ids != sorted(membership_arm_ids):
            raise ContractError(
                "preregistration validation-block segment memberships must be sorted by arm_id"
            )
        if membership_arm_ids != sorted(planned_arm_ids):
            raise ContractError(
                "preregistration validation-block segment memberships must exactly cover planned arms"
            )
        _number(
            item["planned_effective_sample"],
            f"preregistration.validation_blocks[{index}].planned_effective_sample",
            minimum=0,
        )
        if item["planned_effective_sample"] <= 0:
            raise ContractError(
                f"preregistration.validation_blocks[{index}].planned_effective_sample must be positive"
            )
        if block_id in block_ids:
            raise ContractError("preregistration.validation_blocks must contain unique block IDs")
        block_ids.add(block_id)
    partition = _canonical(document["holdout_partition"], {"partition_unit", "held_out_ids"}, "preregistration.holdout_partition")
    partition_unit = require_enum(partition["partition_unit"], {"block", "campaign", "time_batch"}, "preregistration.holdout_partition.partition_unit")
    held_out = _unique_identifiers(partition["held_out_ids"], "preregistration.holdout_partition.held_out_ids")
    studies_by_block = {
        str(block["block_id"]): str(block["study_id"]) for block in blocks
    }
    study_ids = set(studies_by_block.values())
    if partition_unit == "block":
        if not set(held_out).issubset(block_ids):
            raise ContractError(
                "preregistration.holdout_partition held-out block IDs must "
                "be registered validation blocks"
            )
        held_out_blocks = set(held_out)
        for study_id in study_ids:
            study_blocks = {
                block_id for block_id, candidate in studies_by_block.items()
                if candidate == study_id
            }
            if held_out_blocks & study_blocks not in (set(), study_blocks):
                raise ContractError(
                    "preregistration.holdout_partition must never split one "
                    "campaign or time batch between fitting and validation"
                )
    else:
        if not set(held_out).issubset(study_ids):
            raise ContractError(
                "campaign/time-batch holdout IDs must be registered study IDs"
            )
    access = require_array(document["prior_outcome_access"], "preregistration.prior_outcome_access")
    for index, item in enumerate(access):
        entry = _canonical(item, {"access_sha256", "accessed_at", "kind"}, f"preregistration.prior_outcome_access[{index}]")
        _digest(entry["access_sha256"], f"preregistration.prior_outcome_access[{index}].access_sha256")
        accessed_at = require_timestamp(entry["accessed_at"], f"preregistration.prior_outcome_access[{index}].accessed_at")
        if frozen_at >= accessed_at:
            raise ContractError(
                "preregistration.synthetic_surface.frozen_at must strictly precede every prior outcome access"
            )
        require_string(entry["kind"], f"preregistration.prior_outcome_access[{index}].kind")
    analysis = _canonical(document["analysis_rules"], {"tie_handling", "block_weighting", "bootstrap_seed", "bootstrap_resamples", "confidence_levels", "missingness_treatment", "pass_rule", "downgrade_rule", "stop_rule", "scope_narrowing_rule"}, "preregistration.analysis_rules")
    _tie_handling(
        analysis["tie_handling"],
        "preregistration.analysis_rules.tie_handling",
        surface=synthetic_surface["surface"],  # type: ignore[arg-type]
    )
    require_string(analysis["block_weighting"], "preregistration.analysis_rules.block_weighting")
    if isinstance(analysis["bootstrap_seed"], bool) or not isinstance(analysis["bootstrap_seed"], int):
        raise ContractError("preregistration.analysis_rules.bootstrap_seed must be an integer")
    if isinstance(analysis["bootstrap_resamples"], bool) or not isinstance(analysis["bootstrap_resamples"], int) or analysis["bootstrap_resamples"] < 1:
        raise ContractError("preregistration.analysis_rules.bootstrap_resamples must be a positive integer")
    for index, level in enumerate(require_array(analysis["confidence_levels"], "preregistration.analysis_rules.confidence_levels", nonempty=True)):
        number = _number(level, f"preregistration.analysis_rules.confidence_levels[{index}]")
        if not 0 < number < 1:
            raise ContractError("preregistration.analysis_rules.confidence_levels must be between zero and one")
    for key in {"missingness_treatment", "pass_rule", "downgrade_rule", "stop_rule", "scope_narrowing_rule"}:
        require_string(analysis[key], f"preregistration.analysis_rules.{key}")
    eligibility = _canonical(document["eligibility_thresholds"], {"minimum_blocks", "minimum_coverage"}, "preregistration.eligibility_thresholds")
    _positive_integer(eligibility["minimum_blocks"], "preregistration.eligibility_thresholds.minimum_blocks")
    coverage = _number(eligibility["minimum_coverage"], "preregistration.eligibility_thresholds.minimum_coverage", minimum=0)
    if coverage > 1: raise ContractError("preregistration.eligibility_thresholds.minimum_coverage must be at most one")
    segments = _canonical(document["segment_rules"], {"materiality_threshold", "rule"}, "preregistration.segment_rules")
    materiality = _number(segments["materiality_threshold"], "preregistration.segment_rules.materiality_threshold", minimum=0)
    if materiality != 0.10:
        raise ContractError(
            "preregistration.segment_rules.materiality_threshold must equal 0.10"
        )
    require_string(segments["rule"], "preregistration.segment_rules.rule")
    multiplicity = _canonical(document["multiplicity_rules"], {"family_id", "family_alpha", "member_registration_ids", "correction_method"}, "preregistration.multiplicity_rules")
    require_identifier(multiplicity["family_id"], "preregistration.multiplicity_rules.family_id")
    members = _unique_identifiers(multiplicity["member_registration_ids"], "preregistration.multiplicity_rules.member_registration_ids")
    if document["registration_id"] not in members:
        raise ContractError("preregistration.multiplicity_rules must include this registration")
    alpha = _number(multiplicity["family_alpha"], "preregistration.multiplicity_rules.family_alpha")
    if not 0 < alpha < 1: raise ContractError("preregistration.multiplicity_rules.family_alpha must be between zero and one")
    require_enum(multiplicity["correction_method"], {"holm"}, "preregistration.multiplicity_rules.correction_method")
    interim = _canonical(document["interim_analysis_rules"], {"allowed", "maximum_looks"}, "preregistration.interim_analysis_rules")
    if not isinstance(interim["allowed"], bool): raise ContractError("preregistration.interim_analysis_rules.allowed must be a boolean")
    maximum_looks = _positive_integer(interim["maximum_looks"], "preregistration.interim_analysis_rules.maximum_looks")
    if interim["allowed"] is not False or maximum_looks != 1:
        raise ContractError(
            "preregistration.interim_analysis_rules v1 permits one final "
            "analysis only; interim looks require a future evidence-bearing schema"
        )
    power = _canonical(
        document["study_design_power"],
        {
            "design_status", "method", "smallest_effect_of_interest",
            "documented_power", "evidence_sha256", "approval_sha256",
        },
        "preregistration.study_design_power",
    )
    require_enum(
        power["design_status"], {"approved", "not_approved"},
        "preregistration.study_design_power.design_status",
    )
    require_string(power["method"], "preregistration.study_design_power.method")
    effect = _number(
        power["smallest_effect_of_interest"],
        "preregistration.study_design_power.smallest_effect_of_interest",
        minimum=0,
    )
    if effect != document["primary_metric"]["smallest_effect_of_interest"]:
        raise ContractError(
            "preregistration.study_design_power must bind the primary metric smallest effect of interest"
        )
    documented_power = _number(
        power["documented_power"],
        "preregistration.study_design_power.documented_power",
        minimum=0,
    )
    if documented_power > 1:
        raise ContractError(
            "preregistration.study_design_power.documented_power must be at most one"
        )
    _digest(
        power["evidence_sha256"],
        "preregistration.study_design_power.evidence_sha256",
    )
    _digest(
        power["approval_sha256"],
        "preregistration.study_design_power.approval_sha256",
    )
    inventory = require_array(
        document["segment_inventory"],
        "preregistration.segment_inventory",
        nonempty=True,
    )
    segment_ids: list[str] = []
    for index, raw_segment in enumerate(inventory):
        segment = _canonical(
            raw_segment,
            {
                "segment_id", "must_cover", "effective_panel_weight",
                "planned_block_ids", "evidence_sha256", "approval_sha256",
            },
            f"preregistration.segment_inventory[{index}]",
        )
        segment_id = require_identifier(
            segment["segment_id"],
            f"preregistration.segment_inventory[{index}].segment_id",
        )
        segment_ids.append(segment_id)
        if not isinstance(segment["must_cover"], bool):
            raise ContractError(
                f"preregistration.segment_inventory[{index}].must_cover must be a boolean"
            )
        weight = _number(
            segment["effective_panel_weight"],
            f"preregistration.segment_inventory[{index}].effective_panel_weight",
            minimum=0,
        )
        if weight > 1:
            raise ContractError(
                f"preregistration.segment_inventory[{index}].effective_panel_weight must be at most one"
            )
        planned_segment_blocks = _unique_identifiers(
            segment["planned_block_ids"],
            f"preregistration.segment_inventory[{index}].planned_block_ids",
        )
        if not set(planned_segment_blocks).issubset(block_ids):
            raise ContractError(
                f"preregistration.segment_inventory[{index}].planned_block_ids must be registered validation blocks"
            )
        _digest(
            segment["evidence_sha256"],
            f"preregistration.segment_inventory[{index}].evidence_sha256",
        )
        _digest(
            segment["approval_sha256"],
            f"preregistration.segment_inventory[{index}].approval_sha256",
        )
    if segment_ids != sorted(segment_ids) or len(segment_ids) != len(set(segment_ids)):
        raise ContractError(
            "preregistration.segment_inventory must be unique and sorted by segment_id"
        )
    registered_segment_ids = set(segment_ids)
    for (block_id, arm_id), memberships in planned_segment_memberships.items():
        if not set(memberships).issubset(registered_segment_ids):
            raise ContractError(
                "preregistration validation-block segment memberships must use "
                f"registered segments ({block_id}/{arm_id})"
            )
    membership_blocks_by_segment = {
        segment_id: {
            block_id
            for (block_id, _arm_id), memberships
            in planned_segment_memberships.items()
            if segment_id in memberships
        }
        for segment_id in registered_segment_ids
    }
    for index, segment in enumerate(inventory):
        if (
            set(segment["planned_block_ids"])
            != membership_blocks_by_segment[segment["segment_id"]]
        ):
            raise ContractError(
                "preregistration.segment_inventory"
                f"[{index}].planned_block_ids must exactly equal the "
                "validation blocks whose planned arms contain that segment"
            )
    if not any(
        segment["must_cover"] is True
        or float(segment["effective_panel_weight"]) >= materiality
        for segment in inventory
    ):
        raise ContractError(
            "preregistration.segment_inventory must contain at least one material segment"
        )
    approval = _canonical(
        document["approval"],
        {
            "approved_at", "approved_by", "authority_root_sha256",
            "authority_index_sha256", "design_evidence_sha256",
            "approval_sha256",
        },
        "preregistration.approval",
    )
    approved_at = require_timestamp(approval["approved_at"], "preregistration.approval.approved_at")
    if approved_at > registered_at: raise ContractError("preregistration.approval.approved_at must precede registration")
    require_identifier(approval["approved_by"], "preregistration.approval.approved_by")
    _digest(approval["authority_root_sha256"], "preregistration.approval.authority_root_sha256")
    _digest(approval["authority_index_sha256"], "preregistration.approval.authority_index_sha256")
    expected_design_sha = sha256_json(_design_projection(document))
    if approval["design_evidence_sha256"] != expected_design_sha:
        raise ContractError(
            "preregistration.approval.design_evidence_sha256 must bind the exact approved design"
        )
    expected_approval_sha = sha256_json({
        "approved_at": approval["approved_at"],
        "approved_by": approval["approved_by"],
        "authority_root_sha256": approval["authority_root_sha256"],
        "authority_index_sha256": approval["authority_index_sha256"],
        "design_evidence_sha256": approval["design_evidence_sha256"],
    })
    if approval["approval_sha256"] != expected_approval_sha:
        raise ContractError(
            "preregistration.approval.approval_sha256 must bind authority and design evidence"
        )
    _digest(approval["approval_sha256"], "preregistration.approval.approval_sha256")
    if power["approval_sha256"] != approval["approval_sha256"]:
        raise ContractError(
            "preregistration.study_design_power approval must match registration approval"
        )
    if any(
        segment["approval_sha256"] != approval["approval_sha256"]
        for segment in inventory
    ):
        raise ContractError(
            "preregistration.segment_inventory approvals must match registration approval"
        )
    expected_power_evidence_sha = sha256_json(
        _design_projection(document)["study_design_power"],
    )
    if power["evidence_sha256"] != expected_power_evidence_sha:
        raise ContractError(
            "preregistration.study_design_power.evidence_sha256 must bind the exact power design"
        )
    for raw_segment in inventory:
        segment_evidence = dict(raw_segment)
        supplied_evidence_sha = segment_evidence.pop("evidence_sha256")
        segment_evidence.pop("approval_sha256")
        if supplied_evidence_sha != sha256_json(segment_evidence):
            raise ContractError(
                "preregistration.segment_inventory evidence must bind the exact segment design"
            )
    _validate_self_hash(document, field="registration_sha256", path="preregistration")
    return deepcopy(document)


def seal_preregistration(
    payload: object, *, design_approval: object,
) -> dict[str, object]:
    document = _canonical(payload, _PREREGISTRATION_KEYS, "preregistration")
    if document["registration_sha256"] is not None:
        raise ContractError("preregistration.registration_sha256 must be null before sealing")
    if document["status"] != "registered":
        raise ContractError("only registered preregistrations may be sealed")
    require_design_approval(design_approval, document)
    document["registration_sha256"] = sha256_json({**document, "registration_sha256": None})
    return validate_preregistration(document)


def validate_shared_outcome_evidence(payload: object) -> dict[str, object]:
    document = _canonical(payload, _SHARED_KEYS, "shared_outcome_evidence")
    if document["schema_version"] != SHARED_OUTCOME_EVIDENCE_VERSION: raise ContractError("shared_outcome_evidence.schema_version is unknown")
    for key in ("shared_evidence_id", "study_id", "block_id", "arm_id"): require_identifier(document[key], f"shared_outcome_evidence.{key}")
    _outcome_fields(document, "shared_outcome_evidence")
    _validate_self_hash(document, field="shared_evidence_sha256", path="shared_outcome_evidence")
    return deepcopy(document)


def project_shared_outcome_evidence(observation: dict[str, object]) -> dict[str, object]:
    """Return the outcome-only canonical evidence represented by an observation."""
    binding = require_object(observation.get("shared_outcome_evidence_binding"), {"shared_evidence_id", "study_id", "shared_evidence_sha256"}, "observation.shared_outcome_evidence_binding")
    result = {key: deepcopy(observation[key]) for key in _SHARED_KEYS if key not in {"schema_version", "shared_evidence_id", "study_id", "shared_evidence_sha256"}}
    result.update({"schema_version": SHARED_OUTCOME_EVIDENCE_VERSION, "shared_evidence_id": binding["shared_evidence_id"], "study_id": binding["study_id"], "shared_evidence_sha256": None})
    result["shared_evidence_sha256"] = sha256_json(result)
    return result


def _derived_holdout_status(document: Mapping[str, object]) -> str:
    registration = document["registration_binding"]
    assert isinstance(registration, Mapping)
    source = document["source"]
    assignment = document["assignment"]
    assert isinstance(source, Mapping) and isinstance(assignment, Mapping)
    if registration["status"] != "registered":
        return "descriptive_only"
    preregistration = registration["preregistration"]
    assert isinstance(preregistration, Mapping)
    registered_block = next(
        block for block in preregistration["validation_blocks"]
        if block["block_id"] == document["block_id"]
    )
    partition = registration["holdout_partition"]
    assert isinstance(partition, Mapping)
    partition_identity = (
        document["block_id"]
        if partition["partition_unit"] == "block"
        else registered_block["study_id"]
    )
    if partition_identity not in partition["held_out_ids"]:
        return "in_sample"
    if document["claim_scope"] != registration["claim_scope"]:
        return "mismatched"
    if assignment["leakage_detected"] is True:
        return "leaked"
    prior_hashes = registration["prior_outcome_access_hashes"]
    assert isinstance(prior_hashes, list)
    if source["source_sha256"] in prior_hashes:
        return "in_sample"
    if assignment["design"] != "randomized":
        return "mismatched"
    return "eligible_held_out"


def validate_validation_observation(payload: object) -> dict[str, object]:
    document = _canonical(payload, _OBSERVATION_KEYS, "observation")
    if document["schema_version"] != VALIDATION_OBSERVATION_VERSION: raise ContractError("observation.schema_version is unknown")
    require_identifier(document["observation_id"], "observation.observation_id")
    registration = _registration_binding(document["registration_binding"], "observation.registration_binding", extended=True)
    outcome_accessed_at = require_timestamp(document["outcome_accessed_at"], "observation.outcome_accessed_at")
    preregistration = registration["preregistration"]
    assert isinstance(preregistration, Mapping)
    synthetic_surface = preregistration["synthetic_surface"]
    assert isinstance(synthetic_surface, Mapping)
    frozen_at = require_timestamp(
        synthetic_surface["frozen_at"],
        "observation.registration_binding.preregistration.synthetic_surface.frozen_at",
    )
    if frozen_at >= outcome_accessed_at:
        raise ContractError("observation synthetic surface must be frozen before outcome access")
    if require_timestamp(registration["registered_at"], "observation.registration_binding.registered_at") >= outcome_accessed_at:
        raise ContractError("observation must be registered before outcome access")
    binding = _canonical(document["shared_outcome_evidence_binding"], {"shared_evidence_id", "study_id", "shared_evidence_sha256"}, "observation.shared_outcome_evidence_binding")
    require_identifier(binding["shared_evidence_id"], "observation.shared_outcome_evidence_binding.shared_evidence_id")
    require_identifier(binding["study_id"], "observation.shared_outcome_evidence_binding.study_id")
    _digest(binding["shared_evidence_sha256"], "observation.shared_outcome_evidence_binding.shared_evidence_sha256")
    require_identifier(document["block_id"], "observation.block_id")
    require_identifier(document["arm_id"], "observation.arm_id")
    registered_block = next(
        (
            block for block in preregistration["validation_blocks"]
            if block["block_id"] == document["block_id"]
        ),
        None,
    )
    if registered_block is None:
        raise ContractError("observation.block_id must be a registered validation block")
    if document["arm_id"] not in registered_block["planned_arm_ids"]:
        raise ContractError("observation.arm_id must be planned for the registered validation block")
    planned_membership = next(
        (
            membership["segment_ids"]
            for membership in registered_block["planned_segment_membership"]
            if membership["arm_id"] == document["arm_id"]
        ),
        None,
    )
    if document["segment_ids"] != planned_membership:
        raise ContractError(
            "observation.segment_ids must exactly match the preregistered "
            "block-and-arm segment membership"
        )
    if binding["study_id"] != registered_block["study_id"]:
        raise ContractError("observation shared evidence study_id must match the registered validation block")
    _outcome_fields(document, "observation")
    _synthetic_binding(document["synthetic_binding"], "observation.synthetic_binding")
    _panel_binding(document["panel_binding"], "observation.panel_binding")
    scope = _claim_scope(document["claim_scope"], "observation.claim_scope")
    if scope["outcome_scope"] != document["outcome_scope"]:
        raise ContractError("observation.claim_scope outcome subset must equal outcome_scope")
    if scope["panel_binding"] != document["panel_binding"] or scope["synthetic_binding"] != document["synthetic_binding"]:
        raise ContractError("observation.claim_scope must bind the exact panel and synthetic result")
    if scope != registration["claim_scope"]:
        raise ContractError("observation.claim_scope must match the sealed preregistration")
    if document["synthetic_binding"] != project_synthetic_result_binding(synthetic_surface):
        raise ContractError("observation.synthetic_binding must match the sealed synthetic projection")
    projected = project_shared_outcome_evidence(document)
    if binding["shared_evidence_id"] != projected["shared_evidence_id"] or binding["shared_evidence_sha256"] != projected["shared_evidence_sha256"]:
        raise ContractError("observation.shared_outcome_evidence_binding does not match outcome-only projection")
    derived = _derived_holdout_status(document)
    supplied = require_enum(document["holdout_status"], set(HOLDOUT_STATUSES), "observation.holdout_status")
    if supplied != derived:
        raise ContractError(f"observation.holdout_status must be derived as {derived}")
    _validate_self_hash(document, field="observation_sha256", path="observation")
    return deepcopy(document)


def validate_comparison(payload: object) -> dict[str, object]:
    document = _canonical(payload, _COMPARISON_KEYS, "comparison")
    if document["schema_version"] != COMPARISON_VERSION: raise ContractError("comparison.schema_version is unknown")
    require_identifier(document["comparison_id"], "comparison.comparison_id")
    registration_binding = _registration_binding(
        document["registration_binding"],
        "comparison.registration_binding",
        extended=False,
    )
    _panel_binding(document["panel_binding"], "comparison.panel_binding")
    _synthetic_binding(document["synthetic_result_binding"], "comparison.synthetic_result_binding")
    block = _canonical(document["block_binding"], {"block_id", "study_id"}, "comparison.block_binding")
    require_identifier(block["block_id"], "comparison.block_binding.block_id"); require_identifier(block["study_id"], "comparison.block_binding.study_id")
    _metric(document["metric_binding"], "comparison.metric_binding")
    observations = require_array(
        document["observations"], "comparison.observations", nonempty=True,
    )
    validated_observations = [
        validate_validation_observation(item) for item in observations
    ]
    observation_by_arm: dict[str, dict[str, object]] = {}
    for index, observation in enumerate(validated_observations):
        arm_id = str(observation["arm_id"])
        if arm_id in observation_by_arm:
            raise ContractError("comparison.observations must contain unique arm IDs")
        observation_by_arm[arm_id] = observation
        compact_registration = observation["registration_binding"]
        assert isinstance(compact_registration, Mapping)
        if (
            compact_registration["registration_id"]
            != registration_binding["registration_id"]
            or compact_registration["registration_sha256"]
            != registration_binding["registration_sha256"]
            or observation["block_id"] != block["block_id"]
            or compact_registration["preregistration"]["panel_binding"]
            != document["panel_binding"]
            or observation["synthetic_binding"]
            != document["synthetic_result_binding"]
            or observation["metric"] != document["metric_binding"]
        ):
            raise ContractError(
                f"comparison.observations[{index}] must bind the exact comparison registration, block, panel, synthetic result, and metric"
            )
    mappings = require_array(document["arm_mappings"], "comparison.arm_mappings", nonempty=True)
    creative_ids: set[str] = set()
    arm_ids: set[str] = set()
    for index, item in enumerate(mappings):
        row = _canonical(item, {"arm_id", "creative_binding", "observation_sha256"}, f"comparison.arm_mappings[{index}]")
        arm_id = require_identifier(row["arm_id"], f"comparison.arm_mappings[{index}].arm_id")
        creative = _creative_binding(row["creative_binding"], f"comparison.arm_mappings[{index}].creative_binding")
        _digest(row["observation_sha256"], f"comparison.arm_mappings[{index}].observation_sha256")
        if creative["creative_id"] in creative_ids: raise ContractError("comparison.arm_mappings must map each creative once")
        creative_ids.add(creative["creative_id"])
        if arm_id in arm_ids: raise ContractError("comparison.arm_mappings must map each arm once")
        arm_ids.add(arm_id)
        observation = observation_by_arm.get(arm_id)
        if (
            observation is None
            or observation["creative_binding"] != creative
            or observation["observation_sha256"] != row["observation_sha256"]
        ):
            raise ContractError(
                "comparison.arm_mappings observation hashes must exactly match embedded validated observations"
            )
    if arm_ids != set(observation_by_arm):
        raise ContractError(
            "comparison.arm_mappings must exactly cover embedded validated observations"
        )
    coverage = _canonical(document["mapping_coverage"], {"expected_arms", "mapped_arms"}, "comparison.mapping_coverage")
    expected_arms = _positive_integer(coverage["expected_arms"], "comparison.mapping_coverage.expected_arms")
    mapped_arms = _positive_integer(coverage["mapped_arms"], "comparison.mapping_coverage.mapped_arms")
    if expected_arms != mapped_arms or mapped_arms != len(mappings): raise ContractError("comparison.mapping_coverage must be complete")
    for name in ("observed_ordering", "synthetic_ordering"):
        groups = require_array(document[name], f"comparison.{name}", nonempty=True)
        flattened: list[str] = []
        for index, group in enumerate(groups): flattened.extend(_unique_identifiers(group, f"comparison.{name}[{index}]"))
        if len(flattened) != len(set(flattened)) or set(flattened) != creative_ids: raise ContractError(f"comparison.{name} must contain each mapped creative exactly once")
    pairs = require_array(document["pairwise_comparisons"], "comparison.pairwise_comparisons")
    for index, item in enumerate(pairs):
        row = _canonical(item, {"creative_a", "creative_b", "synthetic_direction", "observed_direction"}, f"comparison.pairwise_comparisons[{index}]")
        for key in ("creative_a", "creative_b"): require_identifier(row[key], f"comparison.pairwise_comparisons[{index}].{key}")
        require_enum(row["synthetic_direction"], {"synthetic_a_above_b", "synthetic_b_above_a", "synthetic_tie"}, f"comparison.pairwise_comparisons[{index}].synthetic_direction")
        require_enum(row["observed_direction"], {"observed_a_above_b", "observed_b_above_a", "observed_equivalent", "observed_indeterminate"}, f"comparison.pairwise_comparisons[{index}].observed_direction")
    ordered_arms = sorted(arm_ids)
    ordered_observations = [observation_by_arm[arm_id] for arm_id in ordered_arms]
    preregistration = ordered_observations[0]["registration_binding"]["preregistration"]
    registered_block = next(
        (
            item for item in preregistration["validation_blocks"]
            if item["block_id"] == block["block_id"]
        ),
        None,
    )
    if registered_block is None or registered_block["study_id"] != block["study_id"]:
        raise ContractError(
            "comparison.block_binding must match the embedded preregistration"
        )
    block_evidence = _canonical(
        document["block_evidence"],
        {
            "observation_sha256", "eligible_exposure_count",
            "missing_outcome_count", "planned_effective_sample",
            "achieved_effective_sample",
        },
        "comparison.block_evidence",
    )
    observation_hashes = [
        _digest(value, f"comparison.block_evidence.observation_sha256[{index}]")
        for index, value in enumerate(require_array(
            block_evidence["observation_sha256"],
            "comparison.block_evidence.observation_sha256",
            nonempty=True,
        ))
    ]
    expected_hashes = [
        str(observation["observation_sha256"])
        for observation in ordered_observations
    ]
    if observation_hashes != expected_hashes:
        raise ContractError(
            "comparison.block_evidence observation hashes must exactly match arm-ordered observations"
        )
    expected_exposures = sum(
        int(observation["missingness"]["eligible_exposure_count"])
        for observation in ordered_observations
    )
    expected_missing = sum(
        int(observation["missingness"]["missing_outcome_count"])
        for observation in ordered_observations
    )
    expected_achieved = sum(
        float(observation["sample"]["effective_sample_size"])
        for observation in ordered_observations
    )
    expected_planned = float(registered_block["planned_effective_sample"])
    if (
        _nonnegative_integer(
            block_evidence["eligible_exposure_count"],
            "comparison.block_evidence.eligible_exposure_count",
        ) != expected_exposures
        or _nonnegative_integer(
            block_evidence["missing_outcome_count"],
            "comparison.block_evidence.missing_outcome_count",
        ) != expected_missing
        or not math.isclose(
            _number(
                block_evidence["planned_effective_sample"],
                "comparison.block_evidence.planned_effective_sample",
                minimum=0,
            ),
            expected_planned,
            rel_tol=0,
            abs_tol=1e-12,
        )
        or not math.isclose(
            _number(
                block_evidence["achieved_effective_sample"],
                "comparison.block_evidence.achieved_effective_sample",
                minimum=0,
            ),
            expected_achieved,
            rel_tol=0,
            abs_tol=1e-12,
        )
    ):
        raise ContractError(
            "comparison.block_evidence must be derived from the embedded observations and preregistered block plan"
        )

    def filtered_ordering(groups: object, selected: set[str]) -> list[list[str]]:
        return [
            [str(value) for value in group if value in selected]
            for group in groups
            if any(value in selected for value in group)
        ]

    segment_rows = require_array(
        document["segment_evidence"], "comparison.segment_evidence",
    )
    expected_segment_ids = sorted({
        str(segment_id)
        for observation in ordered_observations
        for segment_id in observation["segment_ids"]
    })
    actual_segment_ids: list[str] = []
    for index, raw_segment in enumerate(segment_rows):
        segment = _canonical(
            raw_segment,
            {
                "segment_id", "observation_sha256", "arm_ids",
                "observed_ordering", "synthetic_ordering",
                "pairwise_comparisons",
            },
            f"comparison.segment_evidence[{index}]",
        )
        segment_id = require_identifier(
            segment["segment_id"],
            f"comparison.segment_evidence[{index}].segment_id",
        )
        actual_segment_ids.append(segment_id)
        expected_segment_observations = [
            observation for observation in ordered_observations
            if segment_id in observation["segment_ids"]
        ]
        expected_segment_arms = [
            str(observation["arm_id"])
            for observation in expected_segment_observations
        ]
        supplied_segment_arms = _unique_identifiers(
            segment["arm_ids"],
            f"comparison.segment_evidence[{index}].arm_ids",
        )
        if supplied_segment_arms != expected_segment_arms:
            raise ContractError(
                "comparison.segment_evidence arm IDs must be derived from embedded observations"
            )
        supplied_segment_hashes = [
            _digest(
                value,
                f"comparison.segment_evidence[{index}].observation_sha256[{hash_index}]",
            )
            for hash_index, value in enumerate(require_array(
                segment["observation_sha256"],
                f"comparison.segment_evidence[{index}].observation_sha256",
                nonempty=True,
            ))
        ]
        if supplied_segment_hashes != [
            observation["observation_sha256"]
            for observation in expected_segment_observations
        ]:
            raise ContractError(
                "comparison.segment_evidence observation hashes must match embedded observations"
            )
        selected_creatives = {
            str(observation["creative_binding"]["creative_id"])
            for observation in expected_segment_observations
        }
        if segment["observed_ordering"] != filtered_ordering(
            document["observed_ordering"], selected_creatives,
        ) or segment["synthetic_ordering"] != filtered_ordering(
            document["synthetic_ordering"], selected_creatives,
        ):
            raise ContractError(
                "comparison.segment_evidence orderings must be exact segment projections"
            )
        expected_segment_pairs = [
            pair for pair in pairs
            if pair["creative_a"] in selected_creatives
            and pair["creative_b"] in selected_creatives
        ]
        if segment["pairwise_comparisons"] != expected_segment_pairs:
            raise ContractError(
                "comparison.segment_evidence pairs must be the exact segment projection"
            )
    if actual_segment_ids != expected_segment_ids:
        raise ContractError(
            "comparison.segment_evidence must exactly cover observed preregistered segments in canonical order"
        )
    _validate_self_hash(document, field="comparison_sha256", path="comparison")
    return deepcopy(document)


def _status_object(value: object, path: str, allowed: set[str]) -> str:
    result = _canonical(value, {"status"}, path)
    return require_enum(result["status"], allowed, f"{path}.status")


def _evaluation_interval(
    value: object, path: str,
) -> dict[str, float] | None:
    interval = _canonical(
        value,
        {
            "available", "point", "two_sided_lower", "two_sided_upper",
            "one_sided_lower",
        },
        path,
    )
    if not isinstance(interval["available"], bool):
        raise ContractError(f"{path}.available must be a boolean")
    fields = (
        "point", "two_sided_lower", "two_sided_upper", "one_sided_lower",
    )
    if interval["available"] is False:
        if any(interval[field] is not None for field in fields):
            raise ContractError(
                f"{path} unavailable interval values must be null"
            )
        return None
    return {
        field: _number(interval[field], f"{path}.{field}")
        for field in fields
    }


def validate_held_out_evaluation(payload: object) -> dict[str, object]:
    document = _canonical(payload, _EVALUATION_KEYS, "evaluation")
    if document["schema_version"] != EVALUATION_VERSION: raise ContractError("evaluation.schema_version is unknown")
    require_identifier(document["evaluation_id"], "evaluation.evaluation_id"); require_timestamp(document["evaluated_at"], "evaluation.evaluated_at")
    registration_binding = _registration_binding(document["registration_binding"], "evaluation.registration_binding", extended=False); _panel_binding(document["panel_binding"], "evaluation.panel_binding")
    _claim_scope(document["claim_scope"], "evaluation.claim_scope"); _metric(document["metric_binding"], "evaluation.metric_binding")
    preregistration = validate_preregistration(document["preregistration"])
    if (
        registration_binding["registration_id"] != preregistration["registration_id"]
        or registration_binding["registration_sha256"] != preregistration["registration_sha256"]
        or document["panel_binding"] != preregistration["panel_binding"]
        or document["claim_scope"] != preregistration["claim_scope"]
        or document["metric_binding"] != preregistration["primary_metric"]
    ):
        raise ContractError(
            "evaluation must bind the exact embedded preregistration"
        )
    require_array(document["comparisons"], "evaluation.comparisons", nonempty=True)
    require_object(document["claim_family"], _FAMILY_KEYS, "evaluation.claim_family")
    inventory = require_array(document["block_inventory"], "evaluation.block_inventory", nonempty=True)
    ids: set[str] = set()
    for index, item in enumerate(inventory):
        row = _canonical(item, {"block_id", "comparison_sha256"}, f"evaluation.block_inventory[{index}]"); block_id = require_identifier(row["block_id"], f"evaluation.block_inventory[{index}].block_id"); _digest(row["comparison_sha256"], f"evaluation.block_inventory[{index}].comparison_sha256")
        if block_id in ids: raise ContractError("evaluation.block_inventory must contain unique block IDs")
        ids.add(block_id)
    coverage = _canonical(
        document["coverage"],
        {"status", "block_rate", "arm_rate", "mapping_rate"},
        "evaluation.coverage",
    )
    coverage_status = require_enum(
        coverage["status"], {"complete", "incomplete"},
        "evaluation.coverage.status",
    )
    for key in ("block_rate", "arm_rate", "mapping_rate"):
        value = _number(coverage[key], f"evaluation.coverage.{key}", minimum=0)
        if value > 1:
            raise ContractError(f"evaluation.coverage.{key} must be at most one")
    expected_coverage_status = (
        "complete"
        if (
            float(coverage["block_rate"]) >= max(
                0.80,
                float(
                    preregistration["eligibility_thresholds"][
                        "minimum_coverage"
                    ]
                ),
            )
            and float(coverage["arm_rate"]) >= 0.90
            and float(coverage["mapping_rate"]) == 1.0
        )
        else "incomplete"
    )
    if coverage_status != expected_coverage_status:
        raise ContractError(
            "evaluation.coverage.status must be derived from numeric coverage"
        )
    missingness = _canonical(
        document["missingness"],
        {
            "status", "eligible_exposure_count", "missing_outcome_count",
            "rate",
        },
        "evaluation.missingness",
    )
    missingness_status = require_enum(
        missingness["status"], {"none", "within_threshold", "excessive"},
        "evaluation.missingness.status",
    )
    exposure_count = _nonnegative_integer(
        missingness["eligible_exposure_count"],
        "evaluation.missingness.eligible_exposure_count",
    )
    missing_count = _nonnegative_integer(
        missingness["missing_outcome_count"],
        "evaluation.missingness.missing_outcome_count",
    )
    if missing_count > exposure_count:
        raise ContractError(
            "evaluation.missingness.missing_outcome_count cannot exceed eligible exposures"
        )
    missing_rate = _number(
        missingness["rate"], "evaluation.missingness.rate", minimum=0,
    )
    expected_missing_rate = missing_count / exposure_count if exposure_count else 0.0
    if not math.isclose(missing_rate, expected_missing_rate, rel_tol=0, abs_tol=1e-12):
        raise ContractError(
            "evaluation.missingness.rate must equal the authenticated counts"
        )
    expected_missingness_status = (
        "none" if missing_count == 0
        else "within_threshold" if missing_rate <= 0.10
        else "excessive"
    )
    if missingness_status != expected_missingness_status:
        raise ContractError(
            "evaluation.missingness.status must be derived from authenticated counts"
        )
    samples = _canonical(
        document["sample_sufficiency"],
        {"status", "minimum_achieved_ratio", "blocks"},
        "evaluation.sample_sufficiency",
    )
    sample_status = require_enum(
        samples["status"], {"sufficient", "insufficient"},
        "evaluation.sample_sufficiency.status",
    )
    _number(
        samples["minimum_achieved_ratio"],
        "evaluation.sample_sufficiency.minimum_achieved_ratio",
        minimum=0,
    )
    sample_blocks = require_array(
        samples["blocks"], "evaluation.sample_sufficiency.blocks", nonempty=True,
    )
    derived_sample_ratios: list[float] = []
    for index, raw_block in enumerate(sample_blocks):
        sample_block = _canonical(
            raw_block,
            {
                "block_id", "planned_effective_sample",
                "achieved_effective_sample", "achieved_ratio",
            },
            f"evaluation.sample_sufficiency.blocks[{index}]",
        )
        require_identifier(
            sample_block["block_id"],
            f"evaluation.sample_sufficiency.blocks[{index}].block_id",
        )
        planned = _number(
            sample_block["planned_effective_sample"],
            f"evaluation.sample_sufficiency.blocks[{index}].planned_effective_sample",
            minimum=0,
        )
        achieved = _number(
            sample_block["achieved_effective_sample"],
            f"evaluation.sample_sufficiency.blocks[{index}].achieved_effective_sample",
            minimum=0,
        )
        ratio = _number(
            sample_block["achieved_ratio"],
            f"evaluation.sample_sufficiency.blocks[{index}].achieved_ratio",
            minimum=0,
        )
        if planned <= 0 or not math.isclose(
            ratio, achieved / planned, rel_tol=0, abs_tol=1e-12,
        ):
            raise ContractError(
                "evaluation.sample_sufficiency block ratio must match planned and achieved evidence"
            )
        derived_sample_ratios.append(ratio)
    expected_minimum_ratio = min(derived_sample_ratios)
    if not math.isclose(
        float(samples["minimum_achieved_ratio"]), expected_minimum_ratio,
        rel_tol=0.0, abs_tol=1e-12,
    ):
        raise ContractError(
            "evaluation.sample_sufficiency.minimum_achieved_ratio must match blocks"
        )
    expected_sample_status = (
        "sufficient"
        if (
            all(value >= 0.90 for value in derived_sample_ratios)
            and len(sample_blocks) >= max(
                12,
                int(
                    preregistration["eligibility_thresholds"][
                        "minimum_blocks"
                    ]
                ),
            )
        )
        else "insufficient"
    )
    if sample_status != expected_sample_status:
        raise ContractError(
            "evaluation.sample_sufficiency.status must be derived from numeric evidence"
        )
    power = _canonical(
        document["power"],
        {
            "status", "documented_power", "smallest_effect_of_interest",
            "method", "design_status",
        },
        "evaluation.power",
    )
    power_status = require_enum(
        power["status"], {"sufficient", "insufficient"},
        "evaluation.power.status",
    )
    documented_power = _number(
        power["documented_power"],
        "evaluation.power.documented_power",
        minimum=0,
    )
    if documented_power > 1:
        raise ContractError("evaluation.power.documented_power must be at most one")
    _number(
        power["smallest_effect_of_interest"],
        "evaluation.power.smallest_effect_of_interest",
        minimum=0,
    )
    require_string(power["method"], "evaluation.power.method")
    require_enum(
        power["design_status"], {"approved", "not_approved"},
        "evaluation.power.design_status",
    )
    expected_power_status = (
        "sufficient"
        if power["design_status"] == "approved" and documented_power >= 0.80
        else "insufficient"
    )
    if power_status != expected_power_status:
        raise ContractError(
            "evaluation.power.status must be derived from approved numeric evidence"
        )
    diagnostic_allowed = {
        "independence": {"independent", "dependent"},
        "leakage": {"clear", "leaked"},
        "multiplicity": {"complete", "incomplete"},
        "repeated_looks": {"none", "controlled", "excessive"},
    }
    diagnostic_statuses = {
        name: _status_object(document[name], f"evaluation.{name}", allowed)
        for name, allowed in diagnostic_allowed.items()
    }
    influence = _canonical(
        document["influence_diagnostics"],
        {
            "status", "maximum_block_contribution",
            "leave_one_block", "leave_one_batch",
        },
        "evaluation.influence_diagnostics",
    )
    influence_status = require_enum(
        influence["status"],
        {
            "all_leave_outs_meet_registered_point_and_raw_p_thresholds",
            "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds",
            "unavailable",
        },
        "evaluation.influence_diagnostics.status",
    )
    maximum_contribution = _number(
        influence["maximum_block_contribution"],
        "evaluation.influence_diagnostics.maximum_block_contribution",
        minimum=0,
    )
    if maximum_contribution > 1:
        raise ContractError(
            "evaluation.influence_diagnostics.maximum_block_contribution "
            "must be at most one"
        )

    def validate_influence_rows(
        value: object, *, identity_key: str, path: str,
    ) -> list[dict[str, object]]:
        if not isinstance(value, list):
            raise ContractError(f"{path} must be an array")
        rows: list[dict[str, object]] = []
        identities: set[str] = set()
        for index, raw in enumerate(value):
            row_path = f"{path}[{index}]"
            row = _canonical(
                raw,
                {
                    identity_key, "tau", "agreement",
                    "one_sided_p_value",
                    "registered_point_and_raw_p_thresholds_retained",
                },
                row_path,
            )
            identity = require_string(row[identity_key], f"{row_path}.{identity_key}")
            if identity in identities:
                raise ContractError(f"{path} identities must be unique")
            identities.add(identity)
            _number(row["tau"], f"{row_path}.tau", minimum=-1)
            agreement = _number(
                row["agreement"], f"{row_path}.agreement", minimum=0,
            )
            p_value = _number(
                row["one_sided_p_value"],
                f"{row_path}.one_sided_p_value",
                minimum=0,
            )
            if agreement > 1 or p_value > 1:
                raise ContractError(
                    f"{row_path} agreement and p-value must be at most one"
                )
            if not isinstance(
                row["registered_point_and_raw_p_thresholds_retained"],
                bool,
            ):
                raise ContractError(
                    f"{row_path}.registered_point_and_raw_p_thresholds_retained "
                    "must be a boolean"
                )
            rows.append(row)
        return rows

    leave_one_block = validate_influence_rows(
        influence["leave_one_block"],
        identity_key="block_id",
        path="evaluation.influence_diagnostics.leave_one_block",
    )
    leave_one_batch = validate_influence_rows(
        influence["leave_one_batch"],
        identity_key="study_id",
        path="evaluation.influence_diagnostics.leave_one_batch",
    )
    influence_rows = [*leave_one_block, *leave_one_batch]
    expected_influence_status = (
        "unavailable"
        if not influence_rows
        else "all_leave_outs_meet_registered_point_and_raw_p_thresholds"
        if all(
            bool(
                row[
                    "registered_point_and_raw_p_thresholds_retained"
                ]
            )
            for row in influence_rows
        )
        else "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds"
    )
    if influence_status != expected_influence_status:
        raise ContractError(
            "evaluation.influence_diagnostics.status must be derived from "
            "the disclosed leave-out results"
        )
    overall = _canonical(
        document["overall_diagnostics"],
        {
            "status", "tau", "agreement", "determinate_pair_coverage",
            "one_sided_p_value", "holm_adjusted_p_value",
        },
        "evaluation.overall_diagnostics",
    )
    overall_status = require_enum(
        overall["status"], {"pass", "fail"},
        "evaluation.overall_diagnostics.status",
    )
    overall_intervals: dict[str, dict[str, float] | None] = {}
    for name in ("tau", "agreement"):
        overall_intervals[name] = _evaluation_interval(
            overall[name],
            f"evaluation.overall_diagnostics.{name}",
        )
    _number(
        overall["determinate_pair_coverage"],
        "evaluation.overall_diagnostics.determinate_pair_coverage",
        minimum=0,
    )
    for name in ("one_sided_p_value", "holm_adjusted_p_value"):
        value = _number(
            overall[name], f"evaluation.overall_diagnostics.{name}",
            minimum=0,
        )
        if value > 1:
            raise ContractError(
                f"evaluation.overall_diagnostics.{name} must be at most one"
            )
    expected_overall_status = (
        "pass"
        if (
            overall_intervals["tau"] is not None
            and overall_intervals["agreement"] is not None
            and
            preregistration["analysis_rules"]["bootstrap_resamples"] == 20_000
            and
            float(overall_intervals["tau"]["point"]) >= 0.40
            and float(overall_intervals["tau"]["one_sided_lower"]) > 0
            and float(overall_intervals["agreement"]["point"]) >= 0.60
            and float(overall_intervals["agreement"]["one_sided_lower"]) > 0.50
            and float(overall["determinate_pair_coverage"]) >= 0.70
            and float(overall["holm_adjusted_p_value"])
            <= float(preregistration["multiplicity_rules"]["family_alpha"])
        )
        else "fail"
    )
    if overall_status != expected_overall_status:
        raise ContractError(
            "evaluation.overall_diagnostics.status must be derived from numeric evidence"
        )
    segments = require_array(
        document["segment_diagnostics"], "evaluation.segment_diagnostics",
        nonempty=True,
    )
    segment_statuses: list[str] = []
    for index, raw_segment in enumerate(segments):
        segment = _canonical(
            raw_segment,
            {
                "segment_id", "material", "must_cover",
                "effective_panel_weight", "eligible_blocks", "planned_blocks",
                "block_coverage", "creative_arms", "tau", "agreement",
                "clear_reversal", "status",
            },
            f"evaluation.segment_diagnostics[{index}]",
        )
        require_identifier(
            segment["segment_id"],
            f"evaluation.segment_diagnostics[{index}].segment_id",
        )
        for field in ("material", "must_cover", "clear_reversal"):
            if not isinstance(segment[field], bool):
                raise ContractError(
                    f"evaluation.segment_diagnostics[{index}].{field} must be a boolean"
                )
        _number(
            segment["effective_panel_weight"],
            f"evaluation.segment_diagnostics[{index}].effective_panel_weight",
            minimum=0,
        )
        _nonnegative_integer(
            segment["eligible_blocks"],
            f"evaluation.segment_diagnostics[{index}].eligible_blocks",
        )
        _positive_integer(
            segment["planned_blocks"],
            f"evaluation.segment_diagnostics[{index}].planned_blocks",
        )
        block_coverage = _number(
            segment["block_coverage"],
            f"evaluation.segment_diagnostics[{index}].block_coverage",
            minimum=0,
        )
        if block_coverage > 1:
            raise ContractError(
                f"evaluation.segment_diagnostics[{index}].block_coverage must be at most one"
            )
        _nonnegative_integer(
            segment["creative_arms"],
            f"evaluation.segment_diagnostics[{index}].creative_arms",
        )
        segment_intervals: dict[str, dict[str, float] | None] = {}
        for name in ("tau", "agreement"):
            segment_intervals[name] = _evaluation_interval(
                segment[name],
                f"evaluation.segment_diagnostics[{index}].{name}",
            )
        supplied_segment_status = require_enum(
            segment["status"], {"pass", "limitations", "fail"},
            f"evaluation.segment_diagnostics[{index}].status",
        )
        sparse = (
            int(segment["eligible_blocks"]) < 6
            or int(segment["creative_arms"]) < 18
            or float(segment["block_coverage"]) < 0.80
            or segment_intervals["tau"] is None
            or segment_intervals["agreement"] is None
        )
        reversal = (
            (
                segment_intervals["tau"] is not None
                and
                float(segment_intervals["tau"]["point"]) < 0
                and float(segment_intervals["tau"]["two_sided_upper"]) < 0
            )
            or (
                segment_intervals["agreement"] is not None
                and
                float(segment_intervals["agreement"]["point"]) < 0.50
                and float(segment_intervals["agreement"]["two_sided_upper"]) < 0.50
            )
        )
        expected_segment_status = (
            "fail" if reversal
            else "limitations" if sparse
            else "pass"
            if (
                segment_intervals["tau"] is not None
                and segment_intervals["agreement"] is not None
                and
                float(segment_intervals["tau"]["point"]) > 0
                and float(segment_intervals["agreement"]["point"]) >= 0.55
            )
            else "fail"
        )
        if bool(segment["clear_reversal"]) != reversal:
            raise ContractError(
                "evaluation segment clear_reversal must be derived independently "
                "from tau or agreement evidence"
            )
        if supplied_segment_status != expected_segment_status:
            raise ContractError(
                "evaluation segment status must be derived from numeric evidence"
            )
        segment_statuses.append(supplied_segment_status)
    gates = _canonical(document["gate_results"], {"all_required_gates_passed"}, "evaluation.gate_results")
    if not isinstance(gates["all_required_gates_passed"], bool): raise ContractError("evaluation.gate_results.all_required_gates_passed must be a boolean")
    decision = _canonical(document["decision"], {"status"}, "evaluation.decision")
    require_enum(decision["status"], {"tier4_supported", "tier4_not_supported", "evaluated_with_limitations", "invalid"}, "evaluation.decision.status")
    gate_passes = {
        "independence": {"independent"},
        "leakage": {"clear"}, "multiplicity": {"complete"},
        "repeated_looks": {"none", "controlled"},
    }
    all_diagnostics_pass = (
        all(diagnostic_statuses[name] in allowed for name, allowed in gate_passes.items())
        and coverage_status == "complete"
        and missingness_status in {"none", "within_threshold"}
        and sample_status == "sufficient"
        and power_status == "sufficient"
        and overall_status == "pass"
        and all(status == "pass" for status in segment_statuses)
    )
    if gates["all_required_gates_passed"] != all_diagnostics_pass:
        raise ContractError("evaluation.gate_results must match diagnostic statuses")
    if decision["status"] == "tier4_supported" and not all_diagnostics_pass:
        raise ContractError("evaluation cannot promote tier4_supported when required gates fail")
    if decision["status"] == "tier4_supported":
        validated_comparisons = [
            validate_comparison(item) for item in document["comparisons"]
        ]
        family = validate_claim_family(document["claim_family"])
        expected_inventory = [{
            "block_id": item["block_binding"]["block_id"],
            "comparison_sha256": item["comparison_sha256"],
        } for item in sorted(
            validated_comparisons,
            key=lambda item: str(item["block_binding"]["block_id"]),
        )]
        if document["block_inventory"] != expected_inventory:
            raise ContractError(
                "evaluation.block_inventory must exactly match embedded comparisons"
            )
        if preregistration not in family["member_preregistrations"]:
            raise ContractError(
                "evaluation claim family must embed the exact preregistration"
            )
    require_array(document["limitations"], "evaluation.limitations")
    _validate_self_hash(document, field="evaluation_sha256", path="evaluation")
    return deepcopy(document)


def validate_tier4_claim(payload: object) -> dict[str, object]:
    document = _canonical(payload, _CLAIM_KEYS, "claim")
    if document["schema_version"] != TIER4_CLAIM_VERSION: raise ContractError("claim.schema_version is unknown")
    for key in ("claim_id",): require_identifier(document[key], f"claim.{key}")
    issued, expires = require_timestamp(document["issued_at"], "claim.issued_at"), require_timestamp(document["expires_at"], "claim.expires_at")
    if expires <= issued: raise ContractError("claim.expires_at must follow claim.issued_at")
    require_enum(document["status"], {"active", "expired", "superseded", "withdrawn", "invalidated"}, "claim.status")
    _panel_binding(document["panel_binding"], "claim.panel_binding"); _registration_binding(document["registration_binding"], "claim.registration_binding", extended=False)
    evaluation = _canonical(document["evaluation_binding"], {"evaluation_id", "evaluation_sha256"}, "claim.evaluation_binding"); require_identifier(evaluation["evaluation_id"], "claim.evaluation_binding.evaluation_id"); _digest(evaluation["evaluation_sha256"], "claim.evaluation_binding.evaluation_sha256")
    scope = _claim_scope(document["claim_scope"], "claim.claim_scope")
    if scope["panel_binding"] != document["panel_binding"]: raise ContractError("claim.claim_scope must bind the exact panel")
    require_string(document["claim_text"], "claim.claim_text"); require_string(document["required_disclaimer"], "claim.required_disclaimer")
    if document["claim_text"] != TIER4_CLAIM_TEXT:
        raise ContractError("claim.claim_text must equal the closed Tier 4 v1 claim")
    if document["required_disclaimer"] != TIER4_REQUIRED_DISCLAIMER:
        raise ContractError(
            "claim.required_disclaimer must equal the closed Tier 4 v1 disclaimer"
        )
    _status_object(document["diagnostic_summary"], "claim.diagnostic_summary", {"tier4_supported", "tier4_not_supported", "evaluated_with_limitations", "invalid"})
    for name in ("limitations", "refresh_triggers"):
        values = require_array(document[name], f"claim.{name}")
        for index, value in enumerate(values): require_string(value, f"claim.{name}[{index}]")
    if document["refresh_triggers"] != TIER4_REFRESH_TRIGGERS:
        raise ContractError(
            "claim.refresh_triggers must equal the closed Tier 4 v1 triggers"
        )
    _validate_self_hash(document, field="claim_sha256", path="claim")
    return deepcopy(document)


def validate_claim_family(payload: object) -> dict[str, object]:
    document = _canonical(payload, _FAMILY_KEYS, "claim_family")
    if document["schema_version"] != CLAIM_FAMILY_VERSION: raise ContractError("claim_family.schema_version is unknown")
    require_identifier(document["family_id"], "claim_family.family_id")
    alpha = _number(document["family_alpha"], "claim_family.family_alpha")
    if not 0 < alpha < 1: raise ContractError("claim_family.family_alpha must be between zero and one")
    registrations = _unique_identifiers(document["member_registration_ids"], "claim_family.member_registration_ids")
    preregistrations = require_array(document["member_preregistrations"], "claim_family.member_preregistrations", nonempty=True)
    if len(preregistrations) != len(registrations):
        raise ContractError("claim_family preregistrations must exactly match members")
    registered_members: list[dict[str, object]] = []
    for index, payload in enumerate(preregistrations):
        registration = validate_preregistration(payload)
        if registration["status"] != "registered":
            raise ContractError(f"claim_family.member_preregistrations[{index}] must be registered")
        registered_members.append(registration)
    if [registration["registration_id"] for registration in registered_members] != registrations:
        raise ContractError("claim_family member registrations must exactly match preregistered membership")
    comparisons = require_array(document["member_comparison_sha256"], "claim_family.member_comparison_sha256", nonempty=True)
    if len(comparisons) != len(registrations) or len(set(comparisons)) != len(comparisons): raise ContractError("claim_family member comparison hashes must exactly match members")
    for index, value in enumerate(comparisons): _digest(value, f"claim_family.member_comparison_sha256[{index}]")
    p_values = require_array(document["member_one_sided_p_values"], "claim_family.member_one_sided_p_values", nonempty=True)
    adjusted = require_array(document["adjusted_p_values"], "claim_family.adjusted_p_values", nonempty=True)
    if len(p_values) != len(registrations) or len(adjusted) != len(registrations): raise ContractError("claim_family p-values must exactly match members")
    for name, values in (("member_one_sided_p_values", p_values), ("adjusted_p_values", adjusted)):
        for index, value in enumerate(values):
            number = _number(value, f"claim_family.{name}[{index}]")
            if not 0 <= number <= 1: raise ContractError(f"claim_family.{name} values must be between zero and one")
    require_enum(document["correction_method"], {"holm"}, "claim_family.correction_method")
    member_comparisons = require_array(
        document["member_comparisons"],
        "claim_family.member_comparisons",
        nonempty=True,
    )
    if len(member_comparisons) != len(registrations):
        raise ContractError(
            "claim_family.member_comparisons must exactly match registered members"
        )
    for member_index, raw_member in enumerate(member_comparisons):
        member = require_array(
            raw_member,
            f"claim_family.member_comparisons[{member_index}]",
            nonempty=True,
        )
        checked_member = [validate_comparison(item) for item in member]
        member_registration_id = registrations[member_index]
        block_ids: set[str] = set()
        for comparison_index, comparison in enumerate(checked_member):
            if (
                comparison["registration_binding"]["registration_id"]
                != member_registration_id
            ):
                raise ContractError(
                    f"claim_family.member_comparisons[{member_index}][{comparison_index}] binds the wrong member"
                )
            block_id = comparison["block_binding"]["block_id"]
            if block_id in block_ids:
                raise ContractError(
                    f"claim_family.member_comparisons[{member_index}] contains duplicate blocks"
                )
            block_ids.add(block_id)
        ordered = sorted(
            checked_member,
            key=lambda item: str(item["block_binding"]["block_id"]),
        )
        member_hash = sha256_json([
            item["comparison_sha256"] for item in ordered
        ])
        if member_hash != comparisons[member_index]:
            raise ContractError(
                f"claim_family.member_comparisons[{member_index}] does not match member comparison hash"
            )
    for index, registration in enumerate(registered_members):
        rules = registration["multiplicity_rules"]
        assert isinstance(rules, Mapping)
        if (
            rules["family_id"] != document["family_id"]
            or rules["family_alpha"] != document["family_alpha"]
            or rules["correction_method"] != document["correction_method"]
            or rules["member_registration_ids"] != registrations
        ):
            raise ContractError(
                f"claim_family.member_preregistrations[{index}] does not bind the exact preregistered family"
            )
    if document["complete"] is not True: raise ContractError("claim_family must be complete")
    _validate_self_hash(document, field="family_sha256", path="claim_family")
    return deepcopy(document)
