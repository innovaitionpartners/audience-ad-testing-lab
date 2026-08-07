"""Strict, creative-blind construction-audit contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
from typing import Any, Mapping


SIBLING_SCRIPTS = Path(__file__).resolve().parents[3] / "audience-ad-testing-lab" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_research import (  # noqa: E402
    AudienceResearchValidationError,
    require_valid_audience_research_pair,
)

from .common import (
    ContractError,
    canonical_json_bytes,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    sha256_json,
)
from .evidence import validate_evidence_ledger, validate_finding_support
from .review import audit_evidence_specificity
from .synthesis import validate_synthesis_matrix


CONSTRUCTION_AUDIT_SCHEMA_VERSION = "panel-construction-audit-v1"
CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION = "panel-construction-audit-v2"

_AUDIT_KEYS = {
    "schema_version", "panel_id", "panel_version", "auditor_run_id", "audited_at",
    "input_bindings", "checks", "result", "limitations",
}
_BINDING_KEYS = {
    "brief_sha256", "panel_sha256", "evidence_ledger_sha256", "finding_support_sha256",
    "synthesis_matrix_sha256", "report_manifest_sha256", "population_frame_sha256",
    "composition_plan_sha256", "validity_profile_sha256", "authorized_handoff_sha256",
}
_BINDING_V2_KEYS = _BINDING_KEYS | {"population_frame_result_sha256"}
_RELEASE_A_NULL_BINDINGS = {
    "population_frame_sha256", "composition_plan_sha256", "validity_profile_sha256",
    "authorized_handoff_sha256",
}
_CHECK_KEYS = {"check_id", "status", "evidence_paths", "finding_ids", "profile_ids", "message"}
CHECK_IDS = {
    "approved_evidence_only", "finding_support_complete", "contradictions_preserved",
    "segment_sufficiency", "profile_traceability", "inference_boundaries",
    "privacy_boundary", "count_semantics", "claim_tier", "population_frame_traceability",
    "weight_semantics", "authorized_handoff_traceability",
}
_CHECK_STATUSES = {"pass", "fail", "not_applicable"}
_RELEASE_A_NOT_APPLICABLE_CHECKS = {
    "population_frame_traceability", "authorized_handoff_traceability",
}
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_PREFIXED_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_REPORT_MANIFEST_INPUT_PATHS = (
    "brief.json", "evidence-ledger.json", "finding-support.json",
    "panel-review-manifest.json", "plan.json", "report-inputs.json",
    "saved-audience-panel.json", "scored-sources.json",
    "source-inventory.json", "synthesis-matrix.json", "verbatim-inventory.json",
    "workflow-state.json",
)
_REPORT_MANIFEST_OUTPUT_PATHS = (
    "audience-research-report.html", "source-inventory.json", "verbatim-inventory.json",
)
_REPORT_MANIFEST_INPUT_PATH_PATTERN = "|".join(
    re.escape(path) for path in _REPORT_MANIFEST_INPUT_PATHS
)
_REPORT_MANIFEST_OUTPUT_PATH_PATTERN = "|".join(
    re.escape(path) for path in _REPORT_MANIFEST_OUTPUT_PATHS
)
_DOCUMENT_PATH_RE = re.compile(
    rf"^(?:"
    rf"brief\.(?:evidence_sources|findings|segment_hypotheses)\[{_ID}\]|"
    rf"panel\.(?:segments|persona_archetypes|context_strata|grounded_context_profiles)\[{_ID}\]|"
    rf"ledger\.evidence_items\[{_ID}\]|"
    rf"finding_support\.findings\[{_ID}\]|"
    rf"synthesis\.questions\[{_ID}\]\.findings\[{_ID}\]|"
    rf"report_manifest\.inputs\[(?:{_REPORT_MANIFEST_INPUT_PATH_PATTERN})\]|"
    rf"report_manifest\.outputs\[(?:{_REPORT_MANIFEST_OUTPUT_PATH_PATTERN})\]"
    rf")$"
)
_RELEASE_B1_DOCUMENT_PATH_RE = re.compile(
    rf"^(?:"
    rf"population_frame\.(?:cells|margins|joints)\[{_ID}\]|"
    rf"composition_plan\.(?:structural_groups|overlay_hypotheses|profiles|unsupported_combinations)\[{_ID}\]|"
    rf"validity_profile\.(?:axes|profiles|diagnostics)\[{_ID}\]|"
    rf"authorized_handoff\.(?:cohorts|aggregates|evidence_items|outputs)\[{_ID}\]"
    rf")$"
)
_FORBIDDEN_PATH_TOKENS = {
    "creative", "creative_input", "creative_inputs", "creative_id", "creative_ids",
    "creative_roster", "evaluation_output", "evaluation_outputs", "performance_output",
    "performance_outputs", "campaign_outcome", "campaign_outcomes", "test_result",
    "test_results", "ctr", "conversion", "conversions", "conversion_rate", "revenue",
    "winner", "winner_label", "winner_labels", "performance_calibration", "calibration",
}
_RELEASE_B1_FORBIDDEN_PATH_TOKENS = {"outcome", "outcome_feedback"}
_REPORT_MANIFEST_KEYS = {
    "schema_version", "panel_id", "panel_version", "generated_at", "report_inputs_sha256",
    "inputs", "outputs",
}
_REPORT_ENTRY_KEYS = {"path", "sha256", "bytes"}
_REPORT_MANIFEST_SCHEMA_VERSION = "audience-research-report-manifest-v2"
_PANEL_REVIEW_MANIFEST_KEYS = {
    "schema_version", "panel_id", "panel_version", "review_revision",
    "generated_at", "canonical_panel", "review_outputs",
}
_PANEL_REVIEW_ENTRY_KEYS = {"path", "media_type", "sha256", "bytes"}
_PANEL_REVIEW_MANIFEST_SCHEMA_VERSION = "panel-review-manifest-v1"
_PANEL_REVIEW_OUTPUTS = (
    ("audience-panel-review.html", "text/html"),
    ("panel-summary.md", "text/markdown"),
)


def _require_digest(value: Any, path: str, *, nullable: bool) -> str | None:
    if value is None:
        if nullable:
            return None
        raise ContractError(f"{path} must be a lowercase SHA-256 digest")
    digest = require_string(value, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _validate_bindings_v1(value: Any, path: str) -> dict[str, str | None]:
    bindings = require_object(value, _BINDING_KEYS, path)
    result: dict[str, str | None] = {}
    for key in sorted(_BINDING_KEYS):
        result[key] = _require_digest(bindings[key], f"{path}.{key}", nullable=key in _RELEASE_A_NULL_BINDINGS)
    for key in sorted(_RELEASE_A_NULL_BINDINGS):
        if result[key] is not None:
            raise ContractError(f"{path}.{key} must be null in Release A")
    return result


def _validate_bindings_v2(value: Any, path: str) -> dict[str, str | None]:
    bindings = require_object(value, _BINDING_V2_KEYS, path)
    nullable = {"population_frame_sha256", "authorized_handoff_sha256"}
    return {
        key: _require_digest(bindings[key], f"{path}.{key}", nullable=key in nullable)
        for key in sorted(_BINDING_V2_KEYS)
    }


def _path_tokens(value: str) -> set[str]:
    components = re.findall(r"[a-z0-9]+", value.lower())
    return {
        "_".join(components[start:end])
        for start in range(len(components))
        for end in range(start + 1, min(len(components), start + 3) + 1)
    }


def _validate_evidence_path(value: Any, path: str, *, release_b1: bool) -> str:
    document_path = require_string(value, path)
    forbidden_tokens = set(_FORBIDDEN_PATH_TOKENS)
    if release_b1:
        forbidden_tokens.update(_RELEASE_B1_FORBIDDEN_PATH_TOKENS)
    if _path_tokens(document_path) & forbidden_tokens:
        raise ContractError(f"{path} has a forbidden creative or outcome document path")
    allowed = _DOCUMENT_PATH_RE.fullmatch(document_path)
    if release_b1:
        allowed = allowed or _RELEASE_B1_DOCUMENT_PATH_RE.fullmatch(document_path)
    if not allowed:
        raise ContractError(f"{path} must be an allowed canonical document path")
    return document_path


def _validate_check(
    value: Any,
    path: str,
    *,
    release_b1: bool,
    authorized_handoff_bound: bool,
) -> dict[str, object]:
    check = require_object(value, _CHECK_KEYS, path)
    check_id = require_enum(check["check_id"], CHECK_IDS, f"{path}.check_id")
    status = require_enum(check["status"], _CHECK_STATUSES, f"{path}.status")
    if release_b1 and check_id in {"population_frame_traceability", "weight_semantics"}:
        if status == "not_applicable":
            raise ContractError(
                f"{path}.status must be active for {check_id} in release_b1"
            )
    elif release_b1 and check_id == "authorized_handoff_traceability":
        if authorized_handoff_bound and status == "not_applicable":
            raise ContractError(
                f"{path}.status must be active for the Release B1 handoff binding"
            )
        if not authorized_handoff_bound and status != "not_applicable":
            raise ContractError(
                f"{path}.status must be not_applicable for the Release B1 handoff binding"
            )
    elif not release_b1 and check_id in _RELEASE_A_NOT_APPLICABLE_CHECKS:
        if status != "not_applicable":
            raise ContractError(
                f"{path}.status must be not_applicable for {check_id} in Release A applicability"
            )
    elif status == "not_applicable":
        applicability = (
            "when the Release B1 handoff binding is null"
            if release_b1
            else "for Release A unavailable checks"
        )
        raise ContractError(
            f"{path}.status may be not_applicable only {applicability}"
        )
    evidence_paths = require_array(check["evidence_paths"], f"{path}.evidence_paths")
    checked_paths = [
        _validate_evidence_path(
            item,
            f"{path}.evidence_paths[{index}]",
            release_b1=release_b1,
        )
        for index, item in enumerate(evidence_paths)
    ]
    if len(set(checked_paths)) != len(checked_paths):
        raise ContractError(f"{path}.evidence_paths must contain unique values")
    finding_ids = require_string_array(check["finding_ids"], f"{path}.finding_ids")
    for index, finding_id in enumerate(finding_ids):
        require_identifier(finding_id, f"{path}.finding_ids[{index}]")
    profile_ids = require_string_array(check["profile_ids"], f"{path}.profile_ids")
    for index, profile_id in enumerate(profile_ids):
        require_identifier(profile_id, f"{path}.profile_ids[{index}]")
    return {
        "check_id": check_id,
        "status": status,
        "evidence_paths": checked_paths,
        "finding_ids": finding_ids,
        "profile_ids": profile_ids,
        "message": require_string(check["message"], f"{path}.message", allow_empty=True),
    }


def validate_construction_audit(
    payload: object,
    *,
    expected_bindings: dict[str, str | None],
) -> dict[str, object]:
    """Validate the closed, document-blind audit envelope and exact digest bindings."""

    raw_schema_version = (
        payload.get("schema_version")
        if isinstance(payload, Mapping)
        else None
    )
    if raw_schema_version not in {
        CONSTRUCTION_AUDIT_SCHEMA_VERSION,
        CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION,
    }:
        raise ContractError(
            "$.schema_version must equal "
            f"{CONSTRUCTION_AUDIT_SCHEMA_VERSION} or "
            f"{CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION}"
        )
    release_b1 = raw_schema_version == CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION
    if release_b1:
        audit = require_object(payload, _AUDIT_KEYS | {"applicability"}, "$")
        if audit["applicability"] != "release_b1":
            raise ContractError("$.applicability must equal release_b1")
        expected = _validate_bindings_v2(expected_bindings, "expected_bindings")
        bindings = _validate_bindings_v2(audit["input_bindings"], "$.input_bindings")
        binding_keys = _BINDING_V2_KEYS
    else:
        audit = require_object(payload, _AUDIT_KEYS, "$")
        if audit["schema_version"] != CONSTRUCTION_AUDIT_SCHEMA_VERSION:
            raise ContractError(
                "$.schema_version must equal "
                f"{CONSTRUCTION_AUDIT_SCHEMA_VERSION} or {CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION}"
            )
        expected = _validate_bindings_v1(expected_bindings, "expected_bindings")
        bindings = _validate_bindings_v1(audit["input_bindings"], "$.input_bindings")
        binding_keys = _BINDING_KEYS
    audited_at = require_string(audit["audited_at"], "$.audited_at")
    if not _RFC3339_RE.fullmatch(audited_at):
        raise ContractError("$.audited_at must be a supplied RFC 3339 timestamp")
    require_timestamp(audited_at, "$.audited_at")
    for key in sorted(binding_keys):
        if bindings[key] != expected[key]:
            raise ContractError(f"$.input_bindings.{key} does not match the expected binding")
    checks: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, value in enumerate(require_array(audit["checks"], "$.checks", nonempty=True)):
        check = _validate_check(
            value,
            f"$.checks[{index}]",
            release_b1=release_b1,
            authorized_handoff_bound=bindings["authorized_handoff_sha256"] is not None,
        )
        if check["check_id"] in seen:
            raise ContractError(f"$.checks[{index}].check_id is duplicated")
        seen.add(str(check["check_id"]))
        checks.append(check)
    missing = sorted(CHECK_IDS - seen)
    if missing:
        raise ContractError("$.checks is missing required checks: " + ", ".join(missing))
    result = require_enum(audit["result"], {"pass", "fail"}, "$.result")
    expected_result = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    if result != expected_result:
        raise ContractError(f"$.result must be {expected_result} for the supplied check statuses")
    limitations = require_string_array(audit["limitations"], "$.limitations", nonempty=True)
    result_payload = {
        "schema_version": audit["schema_version"],
        "panel_id": require_identifier(audit["panel_id"], "$.panel_id"),
        "panel_version": require_string(audit["panel_version"], "$.panel_version"),
        "auditor_run_id": require_identifier(audit["auditor_run_id"], "$.auditor_run_id"),
        "audited_at": audited_at,
        "input_bindings": bindings,
        "checks": checks,
        "result": result,
        "limitations": limitations,
    }
    if release_b1:
        result_payload["applicability"] = audit["applicability"]
        return {
            "schema_version": result_payload["schema_version"],
            "applicability": result_payload["applicability"],
            "panel_id": result_payload["panel_id"],
            "panel_version": result_payload["panel_version"],
            "auditor_run_id": result_payload["auditor_run_id"],
            "audited_at": result_payload["audited_at"],
            "input_bindings": result_payload["input_bindings"],
            "checks": result_payload["checks"],
            "result": result_payload["result"],
            "limitations": result_payload["limitations"],
        }
    return result_payload


def construction_audit_sha256(payload: object) -> str:
    """Return the bare canonical SHA-256 digest of the supplied audit bytes."""

    return sha256_json(payload).removeprefix("sha256:")


def require_passing_construction_audit(
    payload: object,
    *,
    expected_bindings: dict[str, str | None],
) -> dict[str, object]:
    """Validate an audit and require all applicable checks to have passed."""

    audit = validate_construction_audit(payload, expected_bindings=expected_bindings)
    if audit["result"] != "pass":
        raise ContractError("construction audit is not passing")
    return audit


def _validate_release_b1_handoff_projection(
    value: object,
) -> tuple[dict[str, object], set[str]]:
    """Validate only the canonical handoff fields needed by the blind audit.

    The Data Lab handoff validator remains authoritative for the complete
    manifest and its on-disk references. This projection verifies that the
    already validated document has canonical output names and that a route
    cannot be relabeled while it crosses the construction-audit boundary.
    """

    handoff = require_object(
        value,
        {
            "schema_version",
            "status",
            "source_profile",
            "mapping",
            "transformation_report",
            "outputs",
            "profile_seeds",
            "privacy_permission",
            "cohort_identity",
        },
        "authorized_handoff",
    )
    if handoff["schema_version"] != "authorized-audience-handoff-v1":
        raise ContractError(
            "authorized_handoff.schema_version must equal "
            "authorized-audience-handoff-v1"
        )
    require_enum(
        handoff["status"],
        {"complete", "complete_with_loss"},
        "authorized_handoff.status",
    )
    for field, expected_path in (
        ("source_profile", "approved-source-profile.json"),
        ("mapping", "approved-mapping.json"),
        ("transformation_report", "transformation-report.json"),
    ):
        reference = require_object(
            handoff[field],
            {"path", "sha256"},
            f"authorized_handoff.{field}",
        )
        if reference["path"] != expected_path:
            raise ContractError(
                f"authorized_handoff.{field}.path must equal {expected_path}"
            )
        digest = require_string(
            reference["sha256"],
            f"authorized_handoff.{field}.sha256",
        )
        if not _PREFIXED_DIGEST_RE.fullmatch(digest):
            raise ContractError(
                f"authorized_handoff.{field}.sha256 must be a prefixed SHA-256 digest"
            )
    outputs = require_array(
        handoff["outputs"],
        "authorized_handoff.outputs",
        nonempty=True,
    )
    canonical_paths: set[str] = set()
    seen_files: set[str] = set()
    output_registry = {
        "frame-observations": (
            "structural_frame",
            "audience-frame-observation-batch-v1",
        ),
        "structured-evidence": (
            "overlay_evidence",
            "audience-structured-evidence-batch-v1",
        ),
        "social-observations": (
            "overlay_evidence",
            "social-observation-batch-v1",
        ),
        "profile-seeds": (
            "profile_seed",
            "audience-profile-seed-batch-v1",
        ),
        "outcome-feedback": (
            "outcome_feedback",
            "panel-outcome-feedback-v1",
        ),
    }
    profile_seed_paths: list[str] = []
    structural_outputs: list[dict[str, object]] = []
    for index, raw_output in enumerate(outputs):
        path = f"authorized_handoff.outputs[{index}]"
        output = require_object(
            raw_output,
            {
                "path",
                "sha256",
                "route",
                "schema_version",
                "row_count",
                "unit",
                "denominator",
                "field_count",
            },
            path,
        )
        filename = require_string(output["path"], f"{path}.path")
        if (
            Path(filename).name != filename
            or not filename.endswith(".json")
        ):
            raise ContractError(f"{path}.path must be one canonical JSON filename")
        if filename in seen_files:
            raise ContractError("authorized_handoff output paths must be unique")
        seen_files.add(filename)
        family_and_sequence = filename.removesuffix(".json").rsplit("-", 1)
        if (
            len(family_and_sequence) != 2
            or family_and_sequence[0] not in output_registry
            or len(family_and_sequence[1]) != 4
            or not family_and_sequence[1].isdigit()
        ):
            raise ContractError(f"{path}.path is not a canonical output name")
        family = family_and_sequence[0]
        route = require_enum(
            output["route"],
            {
                "structural_frame",
                "overlay_evidence",
                "profile_seed",
                "outcome_feedback",
            },
            f"{path}.route",
        )
        schema_version = require_string(
            output["schema_version"],
            f"{path}.schema_version",
        )
        if (route, schema_version) != output_registry[family]:
            raise ContractError(
                f"{path} violates semantic route separation for {route}"
            )
        digest = require_string(output["sha256"], f"{path}.sha256")
        if not _PREFIXED_DIGEST_RE.fullmatch(digest):
            raise ContractError(f"{path}.sha256 must be a prefixed SHA-256 digest")
        for field in ("unit", "denominator"):
            require_string(output[field], f"{path}.{field}")
        for field in ("row_count", "field_count"):
            count = output[field]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ContractError(f"{path}.{field} must be a nonnegative integer")
        stem = filename.removesuffix(".json")
        require_identifier(stem, f"{path}.path")
        if route == "profile_seed":
            profile_seed_paths.append(filename)
        if route == "structural_frame":
            structural_outputs.append(dict(output))
        if route != "outcome_feedback":
            canonical_paths.add(f"authorized_handoff.outputs[{stem}]")
    declared_seeds = require_string_array(
        handoff["profile_seeds"],
        "authorized_handoff.profile_seeds",
    )
    if sorted(declared_seeds) != sorted(profile_seed_paths):
        raise ContractError(
            "authorized_handoff.profile_seeds must exactly match profile-seed outputs"
        )
    privacy = require_object(
        handoff["privacy_permission"],
        {"permission_confirmed", "aggregate_only", "minimum_cell_size"},
        "authorized_handoff.privacy_permission",
    )
    for field in ("permission_confirmed", "aggregate_only"):
        if privacy[field] is not True:
            raise ContractError(
                f"authorized_handoff.privacy_permission.{field} must be true"
            )
    minimum_cell_size = privacy["minimum_cell_size"]
    if (
        isinstance(minimum_cell_size, bool)
        or not isinstance(minimum_cell_size, int)
        or minimum_cell_size < 1
    ):
        raise ContractError(
            "authorized_handoff.privacy_permission.minimum_cell_size "
            "must be a positive integer"
        )
    identity = require_object(
        handoff["cohort_identity"],
        {
            "cohort_id",
            "source_profile_sha256",
            "source_bundle_sha256",
            "structural_outputs",
        },
        "authorized_handoff.cohort_identity",
    )
    require_identifier(
        identity["cohort_id"],
        "authorized_handoff.cohort_identity.cohort_id",
    )
    for field in ("source_profile_sha256", "source_bundle_sha256"):
        digest = require_string(
            identity[field],
            f"authorized_handoff.cohort_identity.{field}",
        )
        if not _PREFIXED_DIGEST_RE.fullmatch(digest):
            raise ContractError(
                "authorized_handoff.cohort_identity."
                f"{field} must be a prefixed SHA-256 digest"
            )
    if (
        identity["source_profile_sha256"]
        != handoff["source_profile"]["sha256"]
    ):
        raise ContractError(
            "authorized_handoff.cohort_identity must bind the exact "
            "source-profile bytes"
        )
    identity_outputs = require_array(
        identity["structural_outputs"],
        "authorized_handoff.cohort_identity.structural_outputs",
        nonempty=True,
    )
    expected_identity_outputs: list[dict[str, object]] = []
    for index, raw_identity_output in enumerate(identity_outputs):
        path = (
            "authorized_handoff.cohort_identity."
            f"structural_outputs[{index}]"
        )
        output = require_object(
            raw_identity_output,
            {
                "path",
                "sha256",
                "schema_version",
                "batch_id",
                "unit",
                "denominator",
                "row_count",
            },
            path,
        )
        require_identifier(output["batch_id"], f"{path}.batch_id")
        expected_identity_outputs.append(dict(output))
    projected_structural_outputs = [
        {
            "path": output["path"],
            "sha256": output["sha256"],
            "schema_version": output["schema_version"],
            "row_count": output["row_count"],
        }
        for output in structural_outputs
    ]
    if [
        {
            key: output[key]
            for key in (
                "path",
                "sha256",
                "schema_version",
                "row_count",
            )
        }
        for output in expected_identity_outputs
    ] != projected_structural_outputs:
        raise ContractError(
            "authorized_handoff.cohort_identity structural outputs must "
            "exactly bind the handoff structural outputs"
        )
    return dict(handoff), canonical_paths


def _release_b1_population_evidence_paths(
    *,
    frame: Mapping[str, object],
    composition: Mapping[str, object],
    validity: Mapping[str, object],
    handoff_paths: set[str],
) -> tuple[set[str], dict[str, set[str]]]:
    selected_cell_ids = {
        str(cell_id)
        for group in composition["structural_groups"]
        for cell_id in group["cell_ids"]
    }
    frame_paths = {
        f"population_frame.cells[{cell['cell_id']}]"
        for cell in frame["cells"]
        if str(cell["cell_id"]) in selected_cell_ids
    }
    group_paths = {
        "composition_plan.structural_groups"
        f"[{group['structural_group_id']}]"
        for group in composition["structural_groups"]
    }
    overlay_paths = {
        "composition_plan.overlay_hypotheses"
        f"[{overlay['overlay_id']}]"
        for overlay in composition["overlay_hypotheses"]
    }
    profile_paths = {
        f"composition_plan.profiles[{profile['profile_id']}]"
        for profile in composition["profiles"]
    }
    unsupported_paths = {
        "composition_plan.unsupported_combinations"
        f"[{item['reason_code']}]"
        for item in composition["unsupported_combinations"]
    }
    validity_paths = {
        f"validity_profile.axes[{axis.replace('_', '-')}]"
        for axis in validity["axes"]
    }
    all_paths = (
        frame_paths
        | group_paths
        | overlay_paths
        | profile_paths
        | unsupported_paths
        | validity_paths
        | handoff_paths
    )
    required = {
        "population_frame_traceability": frame_paths | group_paths,
        "profile_traceability": profile_paths,
        "inference_boundaries": overlay_paths,
        "weight_semantics": group_paths | profile_paths,
        "authorized_handoff_traceability": handoff_paths,
    }
    return all_paths, required


def validate_release_b1_construction_audit_for_documents(
    payload: object,
    *,
    research_bindings: dict[str, str],
    population_frame: object,
    composition_plan: object,
    validity_profile: object,
    authorized_handoff: object | None,
) -> dict[str, object]:
    """Validate a Release B1 audit against the exact population documents.

    Research-document digests remain independently derived by their existing
    document-aware gate. This function derives every new Release B1 binding,
    requires a final validity profile, resolves the population-only evidence
    paths, and ensures that every explicit profile and route boundary was
    actually audited.
    """

    research_keys = {
        "brief_sha256",
        "panel_sha256",
        "evidence_ledger_sha256",
        "finding_support_sha256",
        "synthesis_matrix_sha256",
        "report_manifest_sha256",
    }
    raw_research = require_object(
        research_bindings,
        research_keys,
        "research_bindings",
    )
    canonical_research = {
        key: _require_digest(
            raw_research[key],
            f"research_bindings.{key}",
            nullable=False,
        )
        for key in sorted(research_keys)
    }
    try:
        from audience_lab.audience_research_v3 import (
            validate_composition_plan,
            validate_population_frame,
            validate_validity_profile,
        )

        canonical_frame = validate_population_frame(population_frame)
        canonical_composition = validate_composition_plan(
            composition_plan,
            frame=canonical_frame,
        )
        canonical_validity = validate_validity_profile(validity_profile)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if canonical_validity["binding_state"] != "panel_final":
        raise ContractError(
            "validity_profile must have binding_state panel_final"
        )
    if canonical_validity["panel_tier"] != canonical_composition["achieved_tier"]:
        raise ContractError(
            "validity_profile panel_tier must equal composition achieved_tier"
        )
    if (
        canonical_validity["evidence_basis"]
        != canonical_composition["evidence_basis"]
    ):
        raise ContractError(
            "validity_profile evidence_basis must equal composition evidence_basis"
        )
    frame_result_digest = sha256_json(canonical_frame)
    usable_frame = canonical_frame["eligibility"] in {
        "eligible_tier_2",
        "eligible_tier_3",
    }
    expected_frame_digest = frame_result_digest if usable_frame else None
    composition_digest = sha256_json(canonical_composition)
    expected_validity_bindings = canonical_validity["source_bindings"]
    if (
        expected_validity_bindings["frame_result_sha256"]
        != frame_result_digest
        or expected_validity_bindings["frame_sha256"]
        != expected_frame_digest
        or expected_validity_bindings["composition_sha256"]
        != composition_digest
    ):
        raise ContractError(
            "validity_profile must exactly bind the frame result, usable "
            "frame, and composition"
        )

    canonical_handoff: dict[str, object] | None = None
    handoff_paths: set[str] = set()
    tier_three_authorized_route = (
        canonical_composition["achieved_tier"] == "tier_3"
        and canonical_composition["evidence_basis"]
        in {"first_party_aggregate", "hybrid"}
    )
    if tier_three_authorized_route and authorized_handoff is None:
        raise ContractError(
            "Tier 3 first-party/hybrid composition requires the exact "
            "authorized handoff"
        )
    if authorized_handoff is not None:
        canonical_handoff, handoff_paths = (
            _validate_release_b1_handoff_projection(authorized_handoff)
        )
    handoff_digest = (
        None
        if canonical_handoff is None
        else sha256_json(canonical_handoff)
    )
    expected_bindings: dict[str, str | None] = {
        **canonical_research,
        "population_frame_result_sha256":
            frame_result_digest.removeprefix("sha256:"),
        "population_frame_sha256": (
            None
            if expected_frame_digest is None
            else expected_frame_digest.removeprefix("sha256:")
        ),
        "composition_plan_sha256":
            composition_digest.removeprefix("sha256:"),
        "validity_profile_sha256":
            sha256_json(canonical_validity).removeprefix("sha256:"),
        "authorized_handoff_sha256": (
            None
            if handoff_digest is None
            else handoff_digest.removeprefix("sha256:")
        ),
    }
    audit = validate_construction_audit(
        payload,
        expected_bindings=expected_bindings,
    )
    if (
        audit["schema_version"] != CONSTRUCTION_AUDIT_V2_SCHEMA_VERSION
        or audit.get("applicability") != "release_b1"
    ):
        raise ContractError(
            "Release B1 document validation requires "
            "panel-construction-audit-v2 with release_b1 applicability"
        )
    resolvable_paths, required_paths = _release_b1_population_evidence_paths(
        frame=canonical_frame,
        composition=canonical_composition,
        validity=canonical_validity,
        handoff_paths=handoff_paths,
    )
    checks = {
        str(check["check_id"]): check
        for check in audit["checks"]
    }
    for check_id, expected_paths in required_paths.items():
        if not expected_paths:
            continue
        observed = set(checks[check_id]["evidence_paths"])
        missing = sorted(expected_paths - observed)
        if missing:
            label = (
                "explicit profile"
                if check_id == "profile_traceability"
                else check_id.replace("_", " ")
            )
            raise ContractError(
                f"{label} audit paths are incomplete: " + ", ".join(missing)
            )
    for index, check in enumerate(audit["checks"]):
        unresolved = sorted(
            set(check["evidence_paths"])
            - (
                resolvable_paths
                | {
                    path
                    for path in check["evidence_paths"]
                    if _DOCUMENT_PATH_RE.fullmatch(path)
                }
            )
        )
        if unresolved:
            raise ContractError(
                f"$.checks[{index}].evidence_paths do not resolve: "
                + ", ".join(unresolved)
            )
    return {
        "audit": audit,
        "population_frame": canonical_frame,
        "composition_plan": canonical_composition,
        "validity_profile": canonical_validity,
        "authorized_handoff": canonical_handoff,
        "population_frame_result_sha256":
            expected_bindings["population_frame_result_sha256"],
        "population_frame_sha256":
            expected_bindings["population_frame_sha256"],
        "composition_plan_sha256":
            expected_bindings["composition_plan_sha256"],
        "validity_profile_sha256":
            expected_bindings["validity_profile_sha256"],
        "authorized_handoff_sha256":
            expected_bindings["authorized_handoff_sha256"],
        "audit_sha256": construction_audit_sha256(audit),
    }


def _manifest_entry_by_path(entries: list[object], label: str) -> dict[str, object]:
    return {
        entry["path"]: entry
        for entry in entries
    }


def _validate_panel_review_manifest_document(
    payload: object,
    *,
    panel: Mapping[str, object],
) -> dict[str, object]:
    """Validate the closed review-manifest contract and canonical-panel binding."""

    manifest = require_object(
        payload,
        _PANEL_REVIEW_MANIFEST_KEYS,
        "$.panel_review_manifest",
    )
    if manifest["schema_version"] != _PANEL_REVIEW_MANIFEST_SCHEMA_VERSION:
        raise ContractError(
            "$.panel_review_manifest.schema_version must equal "
            + _PANEL_REVIEW_MANIFEST_SCHEMA_VERSION
        )
    if manifest["panel_id"] != panel["panel_id"]:
        raise ContractError(
            "$.panel_review_manifest.panel_id must match the canonical panel"
        )
    if manifest["panel_version"] != panel["version"]:
        raise ContractError(
            "$.panel_review_manifest.panel_version must match the canonical panel"
        )
    revision = require_string(
        manifest["review_revision"],
        "$.panel_review_manifest.review_revision",
    )
    if not re.fullmatch(r"review-v[1-9][0-9]*", revision):
        raise ContractError(
            "$.panel_review_manifest.review_revision must match review-vN"
        )
    generated_at = require_string(
        manifest["generated_at"],
        "$.panel_review_manifest.generated_at",
    )
    if not _RFC3339_RE.fullmatch(generated_at):
        raise ContractError(
            "$.panel_review_manifest.generated_at must be RFC 3339"
        )
    require_timestamp(generated_at, "$.panel_review_manifest.generated_at")

    canonical_panel = require_object(
        manifest["canonical_panel"],
        _PANEL_REVIEW_ENTRY_KEYS,
        "$.panel_review_manifest.canonical_panel",
    )
    panel_bytes = canonical_json_bytes(panel)
    expected_panel_entry = {
        "path": "saved-audience-panel.json",
        "media_type": "application/json",
        "sha256": hashlib.sha256(panel_bytes).hexdigest(),
        "bytes": len(panel_bytes),
    }
    if canonical_panel != expected_panel_entry:
        raise ContractError(
            "$.panel_review_manifest.canonical_panel must bind the exact canonical panel bytes"
        )

    outputs = require_array(
        manifest["review_outputs"],
        "$.panel_review_manifest.review_outputs",
    )
    if len(outputs) != len(_PANEL_REVIEW_OUTPUTS):
        raise ContractError(
            "$.panel_review_manifest.review_outputs must contain the exact review files"
        )
    for index, (raw, (expected_path, expected_media_type)) in enumerate(
        zip(outputs, _PANEL_REVIEW_OUTPUTS, strict=True)
    ):
        path = f"$.panel_review_manifest.review_outputs[{index}]"
        entry = require_object(raw, _PANEL_REVIEW_ENTRY_KEYS, path)
        if entry["path"] != expected_path:
            raise ContractError(f"{path}.path must equal {expected_path}")
        if entry["media_type"] != expected_media_type:
            raise ContractError(
                f"{path}.media_type must equal {expected_media_type}"
            )
        _require_digest(entry["sha256"], f"{path}.sha256", nullable=False)
        if (
            isinstance(entry["bytes"], bool)
            or not isinstance(entry["bytes"], int)
            or entry["bytes"] < 0
        ):
            raise ContractError(f"{path}.bytes must be a nonnegative integer")
    return dict(manifest)


def _validate_report_manifest(
    payload: object,
    *,
    panel_id: str,
    panel_version: str,
    canonical_inputs: Mapping[str, object],
) -> dict[str, object]:
    manifest = require_object(payload, _REPORT_MANIFEST_KEYS, "$.report_manifest")
    if manifest["schema_version"] != _REPORT_MANIFEST_SCHEMA_VERSION:
        raise ContractError(
            f"$.report_manifest.schema_version must equal {_REPORT_MANIFEST_SCHEMA_VERSION}"
        )
    if require_identifier(manifest["panel_id"], "$.report_manifest.panel_id") != panel_id:
        raise ContractError("$.report_manifest.panel_id must match the audit panel_id")
    if require_string(manifest["panel_version"], "$.report_manifest.panel_version") != panel_version:
        raise ContractError("$.report_manifest.panel_version must match the audit panel_version")
    generated_at = require_string(manifest["generated_at"], "$.report_manifest.generated_at")
    if not _RFC3339_RE.fullmatch(generated_at):
        raise ContractError("$.report_manifest.generated_at must be RFC 3339")
    require_timestamp(generated_at, "$.report_manifest.generated_at")
    _require_digest(manifest["report_inputs_sha256"], "$.report_manifest.report_inputs_sha256", nullable=False)
    result: dict[str, object] = dict(manifest)
    validated_entries: dict[str, list[object]] = {}
    for label in ("inputs", "outputs"):
        entries = require_array(manifest[label], f"$.report_manifest.{label}")
        paths: list[str] = []
        for index, raw in enumerate(entries):
            entry_path = f"$.report_manifest.{label}[{index}]"
            entry = require_object(raw, _REPORT_ENTRY_KEYS, entry_path)
            item_path = require_string(entry["path"], f"{entry_path}.path")
            if "/" in item_path or item_path in {".", ".."}:
                raise ContractError(f"{entry_path}.path must be a report-relative file name")
            _require_digest(entry["sha256"], f"{entry_path}.sha256", nullable=False)
            if isinstance(entry["bytes"], bool) or not isinstance(entry["bytes"], int) or entry["bytes"] < 0:
                raise ContractError(f"{entry_path}.bytes must be a nonnegative integer")
            paths.append(item_path)
        expected_paths = _REPORT_MANIFEST_INPUT_PATHS if label == "inputs" else _REPORT_MANIFEST_OUTPUT_PATHS
        if tuple(paths) != expected_paths:
            raise ContractError(f"$.report_manifest.{label} paths must be the exact sorted Task 3 manifest paths")
        validated_entries[label] = entries
    inputs = _manifest_entry_by_path(validated_entries["inputs"], "inputs")
    for path, document in canonical_inputs.items():
        entry = inputs[path]
        expected_bytes = canonical_json_bytes(document)
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()
        if entry["sha256"] != expected_digest:
            raise ContractError(f"$.report_manifest.inputs[{path}] sha256 must match the supplied canonical document")
        if entry["bytes"] != len(expected_bytes):
            raise ContractError(f"$.report_manifest.inputs[{path}] bytes must match the supplied canonical document")
    report_inputs = inputs["report-inputs.json"]
    if report_inputs["sha256"] != manifest["report_inputs_sha256"]:
        raise ContractError("$.report_manifest.report_inputs_sha256 must match the report-inputs.json entry")
    return result


def _resolve_construction_audit_references(
    audit: dict[str, object],
    *,
    brief: object,
    panel: object,
    ledger: object,
    finding_support: object,
    synthesis: object,
    report_manifest: object,
    panel_review_manifest: object,
) -> dict[str, object]:
    """Validate supplied canonical documents and resolve audit references against them.

    This private resolver backs the public document-aware validators so no
    hash-only caller can claim to resolve identifiers without the documents.
    """

    try:
        require_valid_audience_research_pair(brief, panel)
    except AudienceResearchValidationError as exc:
        raise ContractError(str(exc)) from exc
    canonical_ledger = validate_evidence_ledger(ledger)
    specificity = audit_evidence_specificity(brief, panel)
    profile_traceability = next(
        (
            check
            for check in audit["checks"]
            if check["check_id"] == "profile_traceability"
        ),
        None,
    )
    if (
        specificity["status"] == "fail"
        and isinstance(profile_traceability, Mapping)
        and profile_traceability["status"] == "pass"
    ):
        failed = ", ".join(
            str(row["persona_archetype_id"])
            for row in specificity["profiles"]
            if row["status"] == "fail"
        )
        raise ContractError(
            "profile_traceability cannot pass when distinct archetypes rely only "
            "on broad findings without a justified evidence-specificity exception: "
            + failed
        )
    support = validate_finding_support(finding_support, canonical_ledger)
    matrix = validate_synthesis_matrix(synthesis, canonical_ledger, support)
    if audit["panel_id"] != panel["panel_id"]:
        raise ContractError("audit.panel_id must match the supplied validated panel")
    if audit["panel_version"] != panel["version"]:
        raise ContractError("audit.panel_version must match the supplied validated panel")
    review_manifest = _validate_panel_review_manifest_document(
        panel_review_manifest,
        panel=panel,
    )
    manifest = _validate_report_manifest(
        report_manifest,
        panel_id=panel["panel_id"],
        panel_version=panel["version"],
        canonical_inputs={
            "brief.json": brief,
            "saved-audience-panel.json": panel,
            "evidence-ledger.json": canonical_ledger,
            "finding-support.json": support,
            "synthesis-matrix.json": matrix,
            "panel-review-manifest.json": review_manifest,
        },
    )
    evidence_paths = {
        f"brief.evidence_sources[{item['evidence_id']}]" for item in brief["evidence_sources"]
    } | {
        f"brief.findings[{item['finding_id']}]" for item in brief["findings"]
    } | {
        f"brief.segment_hypotheses[{item['segment_id']}]" for item in brief["segment_hypotheses"]
    } | {
        f"panel.{collection}[{item[id_key]}]"
        for collection, id_key in (
            ("segments", "segment_id"), ("persona_archetypes", "persona_archetype_id"),
            ("context_strata", "context_stratum_id"), ("grounded_context_profiles", "grounded_profile_id"),
        )
        for item in panel[collection]
    } | {
        f"ledger.evidence_items[{item['evidence_item_id']}]" for item in canonical_ledger["evidence_items"]
    } | {
        f"finding_support.findings[{item['finding_id']}]" for item in support["findings"]
    } | {
        f"synthesis.questions[{question['question_id']}].findings[{finding['finding_id']}]"
        for question in matrix["questions"] for finding in question["findings"]
    } | {
        f"report_manifest.{label}[{item['path']}]"
        for label in ("inputs", "outputs") for item in manifest[label]
    }
    finding_ids = {item["finding_id"] for item in support["findings"]} | {
        finding["finding_id"] for question in matrix["questions"] for finding in question["findings"]
    }
    profile_ids = {item["grounded_profile_id"] for item in panel["grounded_context_profiles"]}
    for index, check in enumerate(audit["checks"]):
        unresolved_paths = sorted(set(check["evidence_paths"]) - evidence_paths)
        if unresolved_paths:
            raise ContractError(f"$.checks[{index}].evidence_paths do not resolve: " + ", ".join(unresolved_paths))
        unresolved_findings = sorted(set(check["finding_ids"]) - finding_ids)
        if unresolved_findings:
            raise ContractError(f"$.checks[{index}].finding_ids do not resolve: " + ", ".join(unresolved_findings))
        unresolved_profiles = sorted(set(check["profile_ids"]) - profile_ids)
        if unresolved_profiles:
            raise ContractError(f"$.checks[{index}].profile_ids do not resolve: " + ", ".join(unresolved_profiles))
    return audit


def validate_construction_audit_for_documents(
    payload: object,
    *,
    brief: object,
    panel: object,
    evidence_ledger: object,
    finding_support: object,
    synthesis_matrix: object,
    report_manifest: object,
    panel_review_manifest: object,
) -> dict[str, object]:
    """Validate one audit bound to the exact validated Release A inputs.

    Callers must supply every Release A audit input; no binding is copied from
    the audit under review. A structurally valid ``result: fail`` audit remains
    valid here so validation can report its outcome honestly.
    """

    try:
        require_valid_audience_research_pair(brief, panel)
    except AudienceResearchValidationError as exc:
        raise ContractError(str(exc)) from exc
    if not isinstance(brief, Mapping) or not isinstance(panel, Mapping):
        raise ContractError("brief and panel must be JSON objects")

    canonical_brief = dict(brief)
    canonical_panel = dict(panel)
    canonical_ledger = validate_evidence_ledger(evidence_ledger)
    canonical_support = validate_finding_support(
        finding_support,
        canonical_ledger,
    )
    canonical_synthesis = validate_synthesis_matrix(
        synthesis_matrix,
        canonical_ledger,
        canonical_support,
    )
    canonical_review_manifest = _validate_panel_review_manifest_document(
        panel_review_manifest,
        panel=canonical_panel,
    )
    panel_id = str(canonical_panel["panel_id"])
    panel_version = str(canonical_panel["version"])
    canonical_manifest = _validate_report_manifest(
        report_manifest,
        panel_id=panel_id,
        panel_version=panel_version,
        canonical_inputs={
            "brief.json": canonical_brief,
            "saved-audience-panel.json": canonical_panel,
            "evidence-ledger.json": canonical_ledger,
            "finding-support.json": canonical_support,
            "synthesis-matrix.json": canonical_synthesis,
            "panel-review-manifest.json": canonical_review_manifest,
        },
    )
    expected_bindings = {
        "brief_sha256": sha256_json(canonical_brief).removeprefix("sha256:"),
        "panel_sha256": sha256_json(canonical_panel).removeprefix("sha256:"),
        "evidence_ledger_sha256": sha256_json(canonical_ledger).removeprefix(
            "sha256:"
        ),
        "finding_support_sha256": sha256_json(canonical_support).removeprefix(
            "sha256:"
        ),
        "synthesis_matrix_sha256": sha256_json(
            canonical_synthesis
        ).removeprefix("sha256:"),
        "report_manifest_sha256": sha256_json(
            canonical_manifest
        ).removeprefix("sha256:"),
        "population_frame_sha256": None,
        "composition_plan_sha256": None,
        "validity_profile_sha256": None,
        "authorized_handoff_sha256": None,
    }
    audit = validate_construction_audit(
        payload,
        expected_bindings=expected_bindings,
    )
    _resolve_construction_audit_references(
        audit,
        brief=canonical_brief,
        panel=canonical_panel,
        ledger=canonical_ledger,
        finding_support=canonical_support,
        synthesis=canonical_synthesis,
        report_manifest=canonical_manifest,
        panel_review_manifest=canonical_review_manifest,
    )
    return {
        "audit": audit,
        "brief": canonical_brief,
        "panel": canonical_panel,
        "evidence_ledger": canonical_ledger,
        "finding_support": canonical_support,
        "synthesis_matrix": canonical_synthesis,
        "report_manifest": canonical_manifest,
        "panel_review_manifest": canonical_review_manifest,
        "brief_sha256": expected_bindings["brief_sha256"],
        "panel_sha256": expected_bindings["panel_sha256"],
        "evidence_ledger_sha256": expected_bindings[
            "evidence_ledger_sha256"
        ],
        "finding_support_sha256": expected_bindings[
            "finding_support_sha256"
        ],
        "synthesis_matrix_sha256": expected_bindings[
            "synthesis_matrix_sha256"
        ],
        "report_manifest_sha256": expected_bindings[
            "report_manifest_sha256"
        ],
        "panel_review_manifest_sha256": sha256_json(
            canonical_review_manifest
        ).removeprefix("sha256:"),
        "report_inputs_sha256": canonical_manifest["report_inputs_sha256"],
        "audit_sha256": construction_audit_sha256(audit),
    }


def require_passing_construction_audit_for_documents(
    payload: object,
    *,
    brief: object,
    panel: object,
    evidence_ledger: object,
    finding_support: object,
    synthesis_matrix: object,
    report_manifest: object,
    panel_review_manifest: object,
) -> dict[str, object]:
    """Require the exact document-aware Release A audit to be passing."""

    result = validate_construction_audit_for_documents(
        payload,
        brief=brief,
        panel=panel,
        evidence_ledger=evidence_ledger,
        finding_support=finding_support,
        synthesis_matrix=synthesis_matrix,
        report_manifest=report_manifest,
        panel_review_manifest=panel_review_manifest,
    )
    if result["audit"]["result"] != "pass":
        raise ContractError("construction audit is not passing")
    return result
