"""Deterministic Release B2 v3 audience package compiler and validator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping
import zipfile

from .audience_package import (
    MAX_TOTAL_BYTES,
    PackageSafetyError,
    PackageValidationError,
    _atomic_write,
    _canonical_json,
    _reject_output_symlink_components,
    _sha256,
    _validate_report_html,
    read_safe_archive_manifest,
    read_safe_archive_members,
)
from .audience_research_v3 import (
    AudienceResearchV3ValidationError,
    validate_audience_research_v3,
    validate_v3_runtime_authority,
)


PACKAGE_SCHEMA_VERSION_V3 = "audience-panel-package-v3"
GENERATOR_VERSION_V3 = "2.0.0"
PACKAGE_FILES_V3 = (
    "audience-research-brief.json",
    "saved-audience-panel.json",
    "audience-population-frame.json",
    "panel-composition-plan.json",
    "panel-validity-profile.json",
    "panel-workflow-state.json",
    "research-report-inputs.json",
    "audience-research-report.html",
    "audience-research-report-manifest.json",
    "source-inventory.json",
    "verbatim-inventory.json",
    "panel-construction-audit.json",
    "README.txt",
)
ARCHIVE_FILES_V3 = PACKAGE_FILES_V3 + ("package-manifest.json",)
PANEL_REVIEW_MANIFEST_MEMBER = "panel-review-manifest.json"
REVIEW_BOUND_PACKAGE_FILES_V3 = PACKAGE_FILES_V3[:-1] + (
    PANEL_REVIEW_MANIFEST_MEMBER,
    "README.txt",
)
REVIEW_BOUND_ARCHIVE_FILES_V3 = REVIEW_BOUND_PACKAGE_FILES_V3 + (
    "package-manifest.json",
)
AUTHORIZED_RUNTIME_AUTHORITY_MEMBER = (
    "authorized-audience-runtime-authority.json"
)
TIER3_PACKAGE_FILES_V3 = PACKAGE_FILES_V3 + (
    AUTHORIZED_RUNTIME_AUTHORITY_MEMBER,
)
TIER3_ARCHIVE_FILES_V3 = TIER3_PACKAGE_FILES_V3 + (
    "package-manifest.json",
)
REVIEW_BOUND_TIER3_PACKAGE_FILES_V3 = REVIEW_BOUND_PACKAGE_FILES_V3 + (
    AUTHORIZED_RUNTIME_AUTHORITY_MEMBER,
)
REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3 = (
    REVIEW_BOUND_TIER3_PACKAGE_FILES_V3 + ("package-manifest.json",)
)
LEGACY_MIGRATION_PACKAGE_FILES_V3 = PACKAGE_FILES_V3 + (
    "migration-provenance.json",
    "source-v2-package.zip",
)
LEGACY_MIGRATION_ARCHIVE_FILES_V3 = (
    LEGACY_MIGRATION_PACKAGE_FILES_V3 + ("package-manifest.json",)
)
REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3 = (
    REVIEW_BOUND_PACKAGE_FILES_V3
    + ("migration-provenance.json", "source-v2-package.zip")
)
REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3 = (
    REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3
    + ("package-manifest.json",)
)
MAX_SOURCE_V2_PACKAGE_BYTES = MAX_TOTAL_BYTES + 5 * 1024 * 1024
MAX_LEGACY_V3_TOTAL_BYTES = MAX_TOTAL_BYTES + MAX_SOURCE_V2_PACKAGE_BYTES
MAX_LEGACY_V3_ARCHIVE_BYTES = MAX_LEGACY_V3_TOTAL_BYTES + 5 * 1024 * 1024
_INPUT_NAMES = {
    "brief": "audience-research-brief.json",
    "panel": "saved-audience-panel.json",
    "population_frame": "audience-population-frame.json",
    "composition": "panel-composition-plan.json",
    "validity": "panel-validity-profile.json",
    "workflow_state": "panel-workflow-state.json",
    "report_inputs": "research-report-inputs.json",
    "report": "audience-research-report.html",
    "report_manifest": "audience-research-report-manifest.json",
    "source_inventory": "source-inventory.json",
    "verbatim_inventory": "verbatim-inventory.json",
    "audit": "panel-construction-audit.json",
}
_LEGACY_MIGRATION_INPUT_NAMES = {
    "migration_provenance": "migration-provenance.json",
    "source_v2_package": "source-v2-package.zip",
}
_AUTHORIZED_RUNTIME_AUTHORITY_INPUT_NAME = {
    "authorized_runtime_authority":
        AUTHORIZED_RUNTIME_AUTHORITY_MEMBER,
}
_PANEL_REVIEW_INPUT_NAME = {
    "panel_review_manifest": PANEL_REVIEW_MANIFEST_MEMBER,
}
_JSON_INPUT_NAMES = frozenset(_INPUT_NAMES) - {"report"}
_MANIFEST_KEYS = {
    "schema_version", "generator_version", "panel_id", "panel_version",
    "brief_id", "workflow_id", "generated_at", "tier", "evidence_basis",
    "bindings", "files",
}
_BINDING_KEYS = {
    "workflow_state_sha256", "population_frame_sha256",
    "composition_plan_sha256", "validity_profile_sha256",
    "report_inputs_sha256", "report_manifest_sha256",
    "construction_audit_sha256",
}
_REVIEW_BOUND_BINDING_KEYS = _BINDING_KEYS | {
    "panel_review_manifest_sha256",
}
_LEGACY_MIGRATION_BINDING_KEYS = _BINDING_KEYS | {
    "migration_provenance_sha256",
    "source_v2_package_sha256",
}
_TIER3_BINDING_KEYS = _BINDING_KEYS | {
    "authorized_runtime_authority_sha256",
}
_REVIEW_BOUND_LEGACY_MIGRATION_BINDING_KEYS = (
    _REVIEW_BOUND_BINDING_KEYS
    | {"migration_provenance_sha256", "source_v2_package_sha256"}
)
_REVIEW_BOUND_TIER3_BINDING_KEYS = _REVIEW_BOUND_BINDING_KEYS | {
    "authorized_runtime_authority_sha256",
}
_FILE_KEYS = {"path", "sha256", "byte_count"}
_REPORT_MANIFEST_V2_INPUT_PATHS_V3 = (
    "brief.json",
    "composition-plan.json",
    "evidence-ledger.json",
    "finding-support.json",
    "panel-review-manifest.json",
    "plan.json",
    "population-frame.json",
    "report-inputs.json",
    "saved-audience-panel.json",
    "scored-sources.json",
    "source-inventory.json",
    "synthesis-matrix.json",
    "validity-profile.json",
    "verbatim-inventory.json",
    "workflow-state.json",
)
_REPORT_ENTRY_KEYS = {"path", "sha256", "bytes"}


@dataclass(frozen=True)
class PackageBuildResultV3:
    output_dir: Path
    package_zip_path: Path
    package_manifest_sha256: str
    package_zip_sha256: str
    panel_id: str
    panel_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "built",
            "output_dir": str(self.output_dir),
            "panel_id": self.panel_id,
            "panel_version": self.panel_version,
            "package_manifest_sha256": self.package_manifest_sha256,
            "package_zip_sha256": self.package_zip_sha256,
            "package_zip_path": str(self.package_zip_path),
        }


def _json_bytes(
    payload: object,
    label: str,
    *,
    nullable: bool = False,
) -> tuple[dict[str, object] | None, bytes]:
    if payload is None and nullable:
        return None, _canonical_json(None)
    if not isinstance(payload, Mapping):
        raise PackageValidationError(f"{label} must be a JSON object")
    try:
        canonical = _canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise PackageValidationError(f"{label} contains a non-canonical value") from exc
    return dict(payload), canonical


def _read_inputs(
    inputs: Mapping[str, Path],
) -> tuple[dict[str, object], dict[str, bytes]]:
    base_inputs = set(_INPUT_NAMES)
    review_inputs = base_inputs | set(_PANEL_REVIEW_INPUT_NAME)
    tier3_inputs = (
        base_inputs | set(_AUTHORIZED_RUNTIME_AUTHORITY_INPUT_NAME)
    )
    legacy_inputs = base_inputs | set(_LEGACY_MIGRATION_INPUT_NAMES)
    review_tier3_inputs = tier3_inputs | set(_PANEL_REVIEW_INPUT_NAME)
    review_legacy_inputs = legacy_inputs | set(_PANEL_REVIEW_INPUT_NAME)
    input_keys = frozenset(inputs)
    if input_keys not in {
        frozenset(base_inputs),
        frozenset(tier3_inputs),
        frozenset(legacy_inputs),
        frozenset(review_inputs),
        frozenset(review_tier3_inputs),
        frozenset(review_legacy_inputs),
    }:
        expected = (
            (review_legacy_inputs if "panel_review_manifest" in inputs else legacy_inputs)
            if set(inputs) & set(_LEGACY_MIGRATION_INPUT_NAMES)
            else (
                (review_tier3_inputs if "panel_review_manifest" in inputs else tier3_inputs)
                if set(inputs)
                & set(_AUTHORIZED_RUNTIME_AUTHORITY_INPUT_NAME)
                else (review_inputs if "panel_review_manifest" in inputs else base_inputs)
            )
        )
        missing = sorted(expected - set(inputs))
        unknown = sorted(set(inputs) - expected)
        detail = ", ".join(([f"missing: {', '.join(missing)}"] if missing else []) + ([f"unknown: {', '.join(unknown)}"] if unknown else []))
        raise PackageValidationError(f"v3 package inputs do not match the contract ({detail})")
    documents: dict[str, object] = {}
    files: dict[str, bytes] = {}
    for key, member in _INPUT_NAMES.items():
        path = Path(inputs[key])
        if path.is_symlink() or not path.is_file():
            raise PackageSafetyError(f"{key} input must be a regular file")
        data = path.read_bytes()
        if key == "report":
            try:
                _validate_report_html(data.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise PackageValidationError("report must be UTF-8") from exc
            files[member] = data
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(f"{key} input is not valid UTF-8 JSON") from exc
        document, canonical = _json_bytes(
            payload,
            key,
            nullable=key in {"workflow_state", "audit"},
        )
        if data != canonical:
            raise PackageValidationError(f"{key} input must use canonical JSON encoding")
        documents[key] = document
        files[member] = canonical
    if "panel_review_manifest" in inputs:
        key = "panel_review_manifest"
        member = _PANEL_REVIEW_INPUT_NAME[key]
        path = Path(inputs[key])
        if path.is_symlink() or not path.is_file():
            raise PackageSafetyError(
                "panel_review_manifest input must be a regular file"
            )
        data = path.read_bytes()
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(
                "panel_review_manifest input is not valid UTF-8 JSON"
            ) from exc
        document, canonical = _json_bytes(payload, key)
        if data != canonical:
            raise PackageValidationError(
                "panel_review_manifest input must use canonical JSON encoding"
            )
        documents[key] = document
        files[member] = canonical
    if input_keys in {frozenset(tier3_inputs), frozenset(review_tier3_inputs)}:
        key = "authorized_runtime_authority"
        member = _AUTHORIZED_RUNTIME_AUTHORITY_INPUT_NAME[key]
        path = Path(inputs[key])
        if path.is_symlink() or not path.is_file():
            raise PackageSafetyError(
                "authorized_runtime_authority input must be a regular file"
            )
        data = path.read_bytes()
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(
                "authorized_runtime_authority input is not valid UTF-8 JSON"
            ) from exc
        document, canonical = _json_bytes(
            payload,
            "authorized_runtime_authority",
        )
        if data != canonical:
            raise PackageValidationError(
                "authorized_runtime_authority input must use canonical JSON encoding"
            )
        documents[key] = document
        files[member] = canonical
    if input_keys in {frozenset(legacy_inputs), frozenset(review_legacy_inputs)}:
        provenance_path = Path(inputs["migration_provenance"])
        source_path = Path(inputs["source_v2_package"])
        for key, path in (
            ("migration_provenance", provenance_path),
            ("source_v2_package", source_path),
        ):
            if path.is_symlink() or not path.is_file():
                raise PackageSafetyError(f"{key} input must be a regular file")
        provenance_bytes = provenance_path.read_bytes()
        try:
            provenance_value = json.loads(provenance_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(
                "migration_provenance input is not valid UTF-8 JSON"
            ) from exc
        provenance, canonical = _json_bytes(
            provenance_value,
            "migration_provenance",
        )
        if provenance_bytes != canonical:
            raise PackageValidationError(
                "migration_provenance input must use canonical JSON encoding"
            )
        documents["migration_provenance"] = provenance
        files["migration-provenance.json"] = canonical
        files["source-v2-package.zip"] = source_path.read_bytes()
    report_manifest = documents.get("report_manifest")
    if (
        isinstance(report_manifest, Mapping)
        and report_manifest.get("schema_version")
        == "audience-research-report-manifest-v2"
        and "panel_review_manifest" not in documents
    ):
        raise PackageValidationError(
            "report manifest v2 requires panel_review_manifest input"
        )
    return documents, files


def _bare_json_digest(document: object) -> str:
    return _sha256(_canonical_json(document))


def _validate_report_support(
    *,
    brief: Mapping[str, object],
    report_inputs: Mapping[str, object],
    report_manifest: Mapping[str, object],
    report_bytes: bytes,
    source_inventory: Mapping[str, object],
    verbatim_inventory: Mapping[str, object],
    panel: Mapping[str, object],
    population_frame: Mapping[str, object],
    composition: Mapping[str, object],
    validity: Mapping[str, object],
    workflow_state: object,
    panel_review_manifest: Mapping[str, object] | None,
) -> None:
    """Validate the package-contained report boundary without unbundled ledgers."""

    panel_scripts = Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
    import sys
    if str(panel_scripts) not in sys.path:
        sys.path.insert(0, str(panel_scripts))
    try:
        from audience_panel_builder.construction_audit import (
            _validate_panel_review_manifest_document,
        )
        from audience_panel_builder.reporting import validate_report_inputs
    except ImportError as exc:  # pragma: no cover - installation boundary.
        raise PackageValidationError("Audience Panel Builder report validators are unavailable") from exc
    try:
        canonical_inputs = validate_report_inputs(report_inputs)
    except ValueError as exc:
        raise PackageValidationError(f"report inputs validation failed: {exc}") from exc
    report_schema = (
        report_manifest.get("schema_version")
        if isinstance(report_manifest, Mapping)
        else None
    )
    report_input_entries = (
        report_manifest.get("inputs")
        if isinstance(report_manifest, Mapping)
        else None
    )
    workflow_snapshot_sha256 = _bare_json_digest(workflow_state)
    if report_schema == "audience-research-report-manifest-v2":
        workflow_entries = [
            item
            for item in report_input_entries
            if isinstance(item, Mapping)
            and item.get("path") == "workflow-state.json"
        ] if isinstance(report_input_entries, list) else []
        if (
            len(workflow_entries) != 1
            or set(workflow_entries[0]) != {"path", "sha256", "bytes"}
        ):
            raise PackageValidationError(
                "report manifest v2 must bind exactly one pre-audit workflow-state.json snapshot"
            )
        workflow_snapshot_sha256 = workflow_entries[0]["sha256"]
    expected_input_bindings = {
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        # V2 reports bind the pre-audit/pre-approval W0 snapshot. The packaged
        # workflow is W1 and separately binds report inputs, audit, and review approval.
        "workflow_state_sha256": workflow_snapshot_sha256,
        "frame_sha256": (
            _bare_json_digest(population_frame)
            if population_frame["eligibility"]
            in {"eligible_tier_2", "eligible_tier_3"}
            else None
        ),
        "composition_sha256": _bare_json_digest(composition),
        "validity_sha256": _bare_json_digest(validity),
        "source_inventory_sha256": _bare_json_digest(source_inventory),
        "verbatim_inventory_sha256": _bare_json_digest(verbatim_inventory),
    }
    if any(
        canonical_inputs[key] != expected
        for key, expected in expected_input_bindings.items()
    ):
        raise PackageValidationError(
            "report inputs must exactly bind the packaged documents and inventories"
        )
    if not isinstance(report_manifest, Mapping) or set(report_manifest) != {
        "schema_version", "panel_id", "panel_version", "generated_at",
        "report_inputs_sha256", "inputs", "outputs",
    }:
        raise PackageValidationError("report manifest keys do not match the contract")
    if report_manifest["schema_version"] not in {
        "audience-research-report-manifest-v1",
        "audience-research-report-manifest-v2",
    }:
        raise PackageValidationError("report manifest schema version is invalid")
    if report_manifest["panel_id"] != canonical_inputs["panel_id"] or report_manifest["panel_version"] != canonical_inputs["panel_version"]:
        raise PackageValidationError("report manifest identity does not match report inputs")
    if report_manifest["report_inputs_sha256"] != _bare_json_digest(canonical_inputs):
        raise PackageValidationError("report manifest does not bind report inputs")
    if report_manifest["schema_version"] == "audience-research-report-manifest-v2":
        if not isinstance(panel_review_manifest, Mapping):
            raise PackageValidationError(
                "report manifest v2 requires panel-review-manifest.json"
            )
        try:
            canonical_review_manifest = _validate_panel_review_manifest_document(
                panel_review_manifest,
                panel=panel,
            )
        except ValueError as exc:
            raise PackageValidationError(
                f"panel review manifest validation failed: {exc}"
            ) from exc
        inputs = report_manifest["inputs"]
        if not isinstance(inputs, list):
            raise PackageValidationError("report manifest inputs are invalid")
        paths = [
            item.get("path") if isinstance(item, Mapping) else None
            for item in inputs
        ]
        if tuple(paths) != _REPORT_MANIFEST_V2_INPUT_PATHS_V3:
            raise PackageValidationError(
                "report manifest v2 inputs must be the exact sorted v3 input path list"
            )
        entries: dict[str, Mapping[str, object]] = {}
        for index, item in enumerate(inputs):
            if not isinstance(item, Mapping) or set(item) != _REPORT_ENTRY_KEYS:
                raise PackageValidationError(
                    f"report manifest v2 input record {index} is invalid"
                )
            digest = item.get("sha256")
            byte_count = item.get("bytes")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or byte_count < 0
            ):
                raise PackageValidationError(
                    f"report manifest v2 input record {index} has invalid digest or byte count"
                )
            entries[str(item["path"])] = item
        review_bytes = _canonical_json(canonical_review_manifest)
        available_inputs = {
            "brief.json": _canonical_json(brief),
            "composition-plan.json": _canonical_json(composition),
            PANEL_REVIEW_MANIFEST_MEMBER: review_bytes,
            "population-frame.json": _canonical_json(population_frame),
            "report-inputs.json": _canonical_json(canonical_inputs),
            "saved-audience-panel.json": _canonical_json(panel),
            "source-inventory.json": _canonical_json(source_inventory),
            "validity-profile.json": _canonical_json(validity),
            "verbatim-inventory.json": _canonical_json(verbatim_inventory),
        }
        for path, expected in available_inputs.items():
            entry = entries[path]
            if (
                entry["sha256"] != _sha256(expected)
                or entry["bytes"] != len(expected)
            ):
                raise PackageValidationError(
                    f"report manifest v2 input {path} does not bind the exact canonical bytes"
                )
        if not isinstance(workflow_state, Mapping):
            raise PackageValidationError(
                "report manifest v2 requires an approved workflow state"
            )
        review_digest = _sha256(review_bytes)
        approvals = workflow_state.get("approvals")
        if not isinstance(approvals, list) or not any(
            isinstance(approval, Mapping)
            and approval.get("scope") == "panel_construction"
            and approval.get("status") == "approved"
            and approval.get("target_sha256") == review_digest
            for approval in approvals
        ):
            raise PackageValidationError(
                "panel_construction approval must bind the exact panel review manifest"
            )
    elif panel_review_manifest is not None:
        raise PackageValidationError(
            "panel review manifest is forbidden with report manifest v1"
        )
    outputs = report_manifest["outputs"]
    if not isinstance(outputs, list) or [item.get("path") if isinstance(item, Mapping) else None for item in outputs] != [
        "audience-research-report.html", "source-inventory.json", "verbatim-inventory.json",
    ]:
        raise PackageValidationError("report manifest outputs do not match the contract")
    expected_outputs = {
        "audience-research-report.html": report_bytes,
        "source-inventory.json": _canonical_json(source_inventory),
        "verbatim-inventory.json": _canonical_json(verbatim_inventory),
    }
    for entry in outputs:
        if not isinstance(entry, Mapping) or set(entry) != {"path", "sha256", "bytes"}:
            raise PackageValidationError("report manifest output record is invalid")
        expected = expected_outputs[str(entry["path"])]
        if entry["sha256"] != _sha256(expected) or entry["bytes"] != len(expected):
            raise PackageValidationError("report manifest output hash or byte count is invalid")


def _require_evidence_specificity(
    brief: Mapping[str, object],
    panel: Mapping[str, object],
) -> None:
    """Independently enforce evidence specificity at the package boundary."""

    panel_scripts = (
        Path(__file__).resolve().parents[3]
        / "audience-panel-builder"
        / "scripts"
    )
    import sys

    if str(panel_scripts) not in sys.path:
        sys.path.insert(0, str(panel_scripts))
    try:
        from audience_panel_builder.review import audit_evidence_specificity
    except ImportError as exc:  # pragma: no cover - installation boundary.
        raise PackageValidationError(
            "Audience Panel Builder specificity validator is unavailable"
        ) from exc
    specificity = audit_evidence_specificity(brief, panel)
    if specificity.get("status") == "fail":
        failed_profiles = sorted(
            str(row.get("persona_archetype_id"))
            for row in specificity.get("profiles", [])
            if isinstance(row, Mapping) and row.get("status") == "fail"
        )
        raise PackageValidationError(
            "audience evidence specificity failed for distinct archetypes: "
            + ", ".join(failed_profiles)
        )


def _validate_documents(
    documents: Mapping[str, object],
    report_bytes: bytes,
    *,
    files: Mapping[str, bytes],
) -> tuple[dict[str, object] | None, ...]:
    try:
        _validate_report_html(report_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PackageValidationError("report must be UTF-8") from exc
    report_manifest = documents.get("report_manifest")
    if (
        isinstance(report_manifest, Mapping)
        and report_manifest.get("schema_version")
        == "audience-research-report-manifest-v2"
    ):
        audit = documents.get("audit")
        bindings = audit.get("input_bindings") if isinstance(audit, Mapping) else None
        actual_report_manifest_sha256 = _bare_json_digest(report_manifest)
        if (
            not isinstance(bindings, Mapping)
            or bindings.get("report_manifest_sha256")
            != actual_report_manifest_sha256
        ):
            raise PackageValidationError(
                "construction audit report_manifest_sha256 must bind the exact packaged report manifest"
            )
    try:
        canonical = validate_audience_research_v3(
            documents["brief"], documents["panel"],
            frame=documents["population_frame"], composition=documents["composition"],
            validity=documents["validity"], workflow_state=documents["workflow_state"],
            construction_audit=documents["audit"],
            panel_review_manifest_sha256=(
                _bare_json_digest(documents["panel_review_manifest"])
                if isinstance(documents.get("panel_review_manifest"), Mapping)
                else None
            ),
            current_report_inputs_sha256=(
                _bare_json_digest(documents["report_inputs"])
                if documents["report_manifest"].get("schema_version")
                == "audience-research-report-manifest-v2"
                else None
            ),
            current_report_manifest_sha256=(
                _bare_json_digest(documents["report_manifest"])
                if documents["report_manifest"].get("schema_version")
                == "audience-research-report-manifest-v2"
                else None
            ),
        )
    except (AudienceResearchV3ValidationError, ValueError) as exc:
        raise PackageValidationError(f"v3 audience document validation failed: {exc}") from exc
    _validate_report_support(
        brief=canonical[0], report_inputs=documents["report_inputs"], report_manifest=documents["report_manifest"],
        report_bytes=report_bytes, source_inventory=documents["source_inventory"],
        verbatim_inventory=documents["verbatim_inventory"],
        panel=canonical[1], population_frame=canonical[2],
        composition=canonical[3], validity=canonical[4],
        workflow_state=canonical[5],
        panel_review_manifest=documents.get("panel_review_manifest"),
    )
    _require_evidence_specificity(canonical[0], canonical[1])
    panel = canonical[1]
    composition = canonical[3]
    profile_ids = {item["profile_id"] for item in composition["profiles"]}
    grounded_ids = {item["grounded_profile_id"] for item in panel["grounded_context_profiles"]}
    if profile_ids != grounded_ids:
        raise PackageValidationError("composition profile IDs must exactly match saved-panel grounded profile IDs")
    authority = documents.get("authorized_runtime_authority")
    if canonical[0]["panel_tier"] == "tier_3":
        if not isinstance(authority, Mapping):
            raise PackageValidationError(
                "Tier 3 packages require authorized_runtime_authority"
            )
        try:
            canonical_authority = validate_v3_runtime_authority(
                canonical[0],
                canonical[1],
                canonical[2],
                authority,
            )
        except ValueError as exc:
            raise PackageValidationError(
                f"Tier 3 runtime authority validation failed: {exc}"
            ) from exc
        if authority != canonical_authority:
            raise PackageValidationError(
                "Tier 3 runtime authority is not canonical"
            )
    elif authority is not None:
        raise PackageValidationError(
            "authorized_runtime_authority is forbidden outside Tier 3"
        )
    _validate_authenticated_legacy_migration(
        documents=documents,
        files=files,
        canonical=canonical,
    )
    return canonical


def _validate_authenticated_legacy_migration(
    *,
    documents: Mapping[str, object],
    files: Mapping[str, bytes],
    canonical: tuple[dict[str, object] | None, ...],
) -> None:
    """Authenticate a migration by validating and deterministically rerunning it."""

    panel = canonical[1]
    if not isinstance(panel, Mapping):
        raise PackageValidationError("canonical v3 panel is missing")
    audit_binding = panel["audit_binding"]
    legacy = (
        isinstance(audit_binding, Mapping)
        and audit_binding.get("applicability") == "legacy_v2_migration"
    )
    has_provenance = "migration_provenance" in documents
    has_source = "source-v2-package.zip" in files
    if not legacy:
        if has_provenance or has_source:
            raise PackageValidationError(
                "migration authentication inputs are forbidden for non-migration packages"
            )
        return
    if not has_provenance or not has_source:
        raise PackageValidationError(
            "legacy_v2_migration requires canonical migration provenance and the original validated v2 archive"
        )
    provenance = documents["migration_provenance"]
    if not isinstance(provenance, Mapping):
        raise PackageValidationError(
            "migration provenance must be a JSON object"
        )
    migrated_at = provenance.get("migrated_at")
    migrated_by = provenance.get("migrated_by")
    target = provenance.get("target")
    if (
        not isinstance(migrated_at, str)
        or not migrated_at
        or not isinstance(migrated_by, str)
        or not migrated_by
        or not isinstance(target, Mapping)
        or not isinstance(target.get("panel_version"), str)
    ):
        raise PackageValidationError(
            "migration provenance lacks deterministic rerun parameters"
        )

    panel_builder_scripts = (
        Path(__file__).resolve().parents[3]
        / "audience-panel-builder"
        / "scripts"
    )
    import sys

    if str(panel_builder_scripts) not in sys.path:
        sys.path.insert(0, str(panel_builder_scripts))
    try:
        from audience_panel_builder.population.migration import (
            migrate_v2_to_v3,
        )
    except ImportError as exc:  # pragma: no cover - installation boundary.
        raise PackageValidationError(
            "Audience Panel Builder migration validator is unavailable"
        ) from exc

    expected_documents = {
        "audience-research-brief-v3.json": documents["brief"],
        "saved-audience-panel-v3.json": documents["panel"],
        "panel-composition-plan.json": documents["composition"],
        "panel-validity-profile.json": documents["validity"],
        "migration-provenance.json": provenance,
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix=".authenticate-v3-migration-"
        ) as temporary:
            root = Path(temporary)
            source = root / "source-v2-package.zip"
            source.write_bytes(files["source-v2-package.zip"])
            output = root / "rerun"
            migrate_v2_to_v3(
                v2_package_path=source,
                new_panel_version=str(target["panel_version"]),
                migrated_at=migrated_at,
                migrated_by=migrated_by,
                output_dir=output,
            )
            rerun = {
                name: json.loads((output / name).read_text(encoding="utf-8"))
                for name in expected_documents
            }
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageValidationError(
            f"legacy migration authentication failed: {exc}"
        ) from exc
    if rerun != expected_documents:
        raise PackageValidationError(
            "legacy migration documents or provenance do not equal the authenticated deterministic rerun"
        )
    if canonical[2] != provenance.get("no_defensible_frame_result"):
        raise PackageValidationError(
            "legacy migration population-frame result does not equal authenticated provenance"
        )


def _readme_bytes(brief: Mapping[str, object], panel: Mapping[str, object], frame: Mapping[str, object]) -> bytes:
    frame_note = (
        "A usable population frame is included for the stated tier."
        if frame["eligibility"] in {"eligible_tier_2", "eligible_tier_3"}
        else "No defensible population frame is claimed; this Tier 1 package remains directional."
    )
    return (
        "Ad Testing Lab audience package v3\n\n"
        f"Panel: {panel['panel_id']} version {panel['version']}\n"
        f"Research brief: {brief['brief_id']}\n"
        f"Tier: {brief['panel_tier']} ({brief['evidence_basis']})\n\n"
        f"{frame_note}\n\n"
        "Start with audience-research-report.html. The JSON files are canonical, immutable records.\n"
        "Do not add raw CRM records, direct identifiers, or individual-level data to this package.\n"
    ).encode("utf-8")


def _manifest(
    *,
    brief: Mapping[str, object],
    panel: Mapping[str, object],
    workflow: Mapping[str, object] | None,
    files: Mapping[str, bytes],
) -> dict[str, object]:
    legacy_migration = "migration-provenance.json" in files
    tier3_authority = AUTHORIZED_RUNTIME_AUTHORITY_MEMBER in files
    review_bound = PANEL_REVIEW_MANIFEST_MEMBER in files
    package_files = (
        (
            REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3
            if review_bound
            else LEGACY_MIGRATION_PACKAGE_FILES_V3
        )
        if legacy_migration
        else (
            (
                REVIEW_BOUND_TIER3_PACKAGE_FILES_V3
                if review_bound
                else TIER3_PACKAGE_FILES_V3
            )
            if tier3_authority
            else (
                REVIEW_BOUND_PACKAGE_FILES_V3
                if review_bound
                else PACKAGE_FILES_V3
            )
        )
    )
    bindings = {
        "workflow_state_sha256": _sha256(files["panel-workflow-state.json"]),
        "population_frame_sha256": _sha256(files["audience-population-frame.json"]),
        "composition_plan_sha256": _sha256(files["panel-composition-plan.json"]),
        "validity_profile_sha256": _sha256(files["panel-validity-profile.json"]),
        "report_inputs_sha256": _sha256(files["research-report-inputs.json"]),
        "report_manifest_sha256": _sha256(files["audience-research-report-manifest.json"]),
        "construction_audit_sha256": _sha256(files["panel-construction-audit.json"]),
    }
    if legacy_migration:
        bindings.update(
            {
                "migration_provenance_sha256": _sha256(
                    files["migration-provenance.json"]
                ),
                "source_v2_package_sha256": _sha256(
                    files["source-v2-package.zip"]
                ),
            }
        )
    if tier3_authority:
        bindings["authorized_runtime_authority_sha256"] = _sha256(
            files[AUTHORIZED_RUNTIME_AUTHORITY_MEMBER]
        )
    if review_bound:
        bindings["panel_review_manifest_sha256"] = _sha256(
            files[PANEL_REVIEW_MANIFEST_MEMBER]
        )
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION_V3,
        "generator_version": GENERATOR_VERSION_V3,
        "panel_id": panel["panel_id"], "panel_version": panel["version"],
        "brief_id": brief["brief_id"],
        "workflow_id": (
            workflow["workflow_id"]
            if workflow is not None
            else brief["workflow_state_binding"]
        ),
        "generated_at": brief["approval"]["approved_at"],
        "tier": brief["panel_tier"], "evidence_basis": brief["evidence_basis"],
        "bindings": bindings,
        "files": {
            name: {"path": name, "sha256": _sha256(files[name]), "byte_count": len(files[name])}
            for name in package_files
        },
    }


def _zip_bytes(files: Mapping[str, bytes]) -> bytes:
    review_bound = PANEL_REVIEW_MANIFEST_MEMBER in files
    archive_files = (
        (
            REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3
            if review_bound
            else LEGACY_MIGRATION_ARCHIVE_FILES_V3
        )
        if "migration-provenance.json" in files
        else (
            (
                REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3
                if review_bound
                else TIER3_ARCHIVE_FILES_V3
            )
            if AUTHORIZED_RUNTIME_AUTHORITY_MEMBER in files
            else (
                REVIEW_BOUND_ARCHIVE_FILES_V3
                if review_bound
                else ARCHIVE_FILES_V3
            )
        )
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        for name in archive_files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.extra = b""
            info.comment = b""
            archive.writestr(info, files[name])
        archive.comment = b""
    return output.getvalue()


def build_audience_package_v3(*, inputs: dict[str, Path], output_dir: Path) -> PackageBuildResultV3:
    """Validate, bind, and atomically materialize a deterministic v3 package."""

    documents, files = _read_inputs(inputs)
    canonical = _validate_documents(
        documents,
        files["audience-research-report.html"],
        files=files,
    )
    brief, panel, frame, _composition, _validity, workflow, _audit = canonical
    files["README.txt"] = _readme_bytes(brief, panel, frame)
    files["package-manifest.json"] = _canonical_json(_manifest(
        brief=brief, panel=panel, workflow=workflow, files=files,
    ))
    zip_data = _zip_bytes(files)
    root = Path(output_dir)
    _reject_output_symlink_components(root)
    if root.exists() and (root.is_symlink() or not root.is_dir() or any(root.iterdir())):
        raise PackageSafetyError("output directory must be absent or an empty real directory")
    root.parent.mkdir(parents=True, exist_ok=True)
    _reject_output_symlink_components(root)
    stage = Path(tempfile.mkdtemp(prefix=".audience-package-v3-", dir=root.parent))
    os.chmod(stage, 0o700)
    try:
        review_bound = PANEL_REVIEW_MANIFEST_MEMBER in files
        archive_files = (
            (
                REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3
                if review_bound
                else LEGACY_MIGRATION_ARCHIVE_FILES_V3
            )
            if "migration-provenance.json" in files
            else (
                (
                    REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3
                    if review_bound
                    else TIER3_ARCHIVE_FILES_V3
                )
                if AUTHORIZED_RUNTIME_AUTHORITY_MEMBER in files
                else (
                    REVIEW_BOUND_ARCHIVE_FILES_V3
                    if review_bound
                    else ARCHIVE_FILES_V3
                )
            )
        )
        for name in archive_files:
            _atomic_write(stage / name, files[name])
        _atomic_write(stage / "audience-panel-package-v3.zip", zip_data)
        validate_package_archive_v3(stage / "audience-panel-package-v3.zip")
        if root.exists():
            root.rmdir()
        os.replace(stage, root)
        os.chmod(root, 0o700)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return PackageBuildResultV3(
        root, root / "audience-panel-package-v3.zip", _sha256(files["package-manifest.json"]),
        _sha256(zip_data), str(panel["panel_id"]), str(panel["version"]),
    )


def archive_files_v3_for_manifest(value: object) -> tuple[str, ...]:
    """Choose only an exact published v3 archive allowlist from its manifest."""

    if not isinstance(value, Mapping):
        raise PackageValidationError("package manifest must be an object")
    records = value.get("files")
    if not isinstance(records, Mapping):
        raise PackageValidationError("manifest files must be an object")
    if set(records) == set(PACKAGE_FILES_V3):
        return ARCHIVE_FILES_V3
    if set(records) == set(REVIEW_BOUND_PACKAGE_FILES_V3):
        return REVIEW_BOUND_ARCHIVE_FILES_V3
    if set(records) == set(TIER3_PACKAGE_FILES_V3):
        return TIER3_ARCHIVE_FILES_V3
    if set(records) == set(REVIEW_BOUND_TIER3_PACKAGE_FILES_V3):
        return REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3
    if set(records) == set(LEGACY_MIGRATION_PACKAGE_FILES_V3):
        return LEGACY_MIGRATION_ARCHIVE_FILES_V3
    if set(records) == set(REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3):
        return REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3
    raise PackageValidationError("manifest file allowlist is invalid")


def read_v3_archive_manifest(
    source: Path | str | bytes | bytearray,
) -> tuple[bytes, bytes]:
    """Read a v3 manifest under the bounded nested-v2 exception."""

    return read_safe_archive_manifest(
        source,
        entry_size_overrides={
            "source-v2-package.zip": MAX_SOURCE_V2_PACKAGE_BYTES,
        },
        max_total_bytes=MAX_LEGACY_V3_TOTAL_BYTES,
        max_archive_bytes=MAX_LEGACY_V3_ARCHIVE_BYTES,
    )


def read_v3_archive_members(
    source: Path | str | bytes | bytearray,
    *,
    allowed_files: tuple[str, ...],
) -> dict[str, bytes]:
    """Read exact v3 members; only the nested v2 source gets more space."""

    if allowed_files in {
        LEGACY_MIGRATION_ARCHIVE_FILES_V3,
        REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3,
    }:
        return read_safe_archive_members(
            source,
            allowed_files=allowed_files,
            entry_size_overrides={
                "source-v2-package.zip": MAX_SOURCE_V2_PACKAGE_BYTES,
            },
            max_total_bytes=MAX_LEGACY_V3_TOTAL_BYTES,
            max_archive_bytes=MAX_LEGACY_V3_ARCHIVE_BYTES,
        )
    return read_safe_archive_members(source, allowed_files=allowed_files)


def _manifest_document(value: object) -> dict[str, object]:
    manifest, _canonical = _json_bytes(value, "package manifest")
    if set(manifest) != _MANIFEST_KEYS:
        raise PackageValidationError("manifest keys do not match the package contract")
    if manifest["schema_version"] != PACKAGE_SCHEMA_VERSION_V3 or manifest["generator_version"] != GENERATOR_VERSION_V3:
        raise PackageValidationError("unsupported package schema or generator")
    for key in ("panel_id", "panel_version", "brief_id", "workflow_id", "generated_at", "tier", "evidence_basis"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise PackageValidationError(f"manifest {key} is invalid")
    bindings = manifest["bindings"]
    archive_files = archive_files_v3_for_manifest(manifest)
    legacy_migration = archive_files in {
        LEGACY_MIGRATION_ARCHIVE_FILES_V3,
        REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3,
    }
    tier3_authority = archive_files in {
        TIER3_ARCHIVE_FILES_V3,
        REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3,
    }
    review_bound = archive_files in {
        REVIEW_BOUND_ARCHIVE_FILES_V3,
        REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3,
        REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3,
    }
    expected_binding_keys = (
        (
            _REVIEW_BOUND_LEGACY_MIGRATION_BINDING_KEYS
            if review_bound
            else _LEGACY_MIGRATION_BINDING_KEYS
        )
        if legacy_migration
        else (
            (
                _REVIEW_BOUND_TIER3_BINDING_KEYS
                if review_bound
                else _TIER3_BINDING_KEYS
            )
            if tier3_authority
            else (
                _REVIEW_BOUND_BINDING_KEYS
                if review_bound
                else _BINDING_KEYS
            )
        )
    )
    if (
        not isinstance(bindings, Mapping)
        or set(bindings) != expected_binding_keys
    ):
        raise PackageValidationError("manifest bindings do not match the package contract")
    for digest in bindings.values():
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise PackageValidationError("manifest binding hash is invalid")
    records = manifest["files"]
    expected_package_files = (
        (
            REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3
            if review_bound
            else LEGACY_MIGRATION_PACKAGE_FILES_V3
        )
        if legacy_migration
        else (
            (
                REVIEW_BOUND_TIER3_PACKAGE_FILES_V3
                if review_bound
                else TIER3_PACKAGE_FILES_V3
            )
            if tier3_authority
            else (
                REVIEW_BOUND_PACKAGE_FILES_V3
                if review_bound
                else PACKAGE_FILES_V3
            )
        )
    )
    if (
        not isinstance(records, Mapping)
        or set(records) != set(expected_package_files)
    ):
        raise PackageValidationError("manifest file allowlist is invalid")
    for name, record in records.items():
        if not isinstance(record, Mapping) or set(record) != _FILE_KEYS or record.get("path") != name:
            raise PackageValidationError(f"manifest record is invalid for {name}")
        if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64 or any(ch not in "0123456789abcdef" for ch in record["sha256"]):
            raise PackageValidationError(f"manifest hash is invalid for {name}")
        if isinstance(record.get("byte_count"), bool) or not isinstance(record["byte_count"], int) or record["byte_count"] < 0:
            raise PackageValidationError(f"manifest byte count is invalid for {name}")
    return manifest


def _validate_package_archive_v3_snapshot(raw: bytes) -> dict[str, object]:
    """Validate one immutable v3 archive-byte snapshot without extracting it."""

    _snapshot, manifest_bytes = read_v3_archive_manifest(raw)
    try:
        manifest_value = json.loads(manifest_bytes.decode("utf-8"))
        archive_files = archive_files_v3_for_manifest(manifest_value)
        files = read_v3_archive_members(
            raw,
            allowed_files=archive_files,
        )
        documents = {
            key: json.loads(files[member].decode("utf-8"))
            for key, member in _INPUT_NAMES.items() if key != "report"
        }
        if PANEL_REVIEW_MANIFEST_MEMBER in files:
            documents["panel_review_manifest"] = json.loads(
                files[PANEL_REVIEW_MANIFEST_MEMBER].decode("utf-8")
            )
        if archive_files in {
            LEGACY_MIGRATION_ARCHIVE_FILES_V3,
            REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3,
        }:
            documents["migration_provenance"] = json.loads(
                files["migration-provenance.json"].decode("utf-8")
            )
        if archive_files in {
            TIER3_ARCHIVE_FILES_V3,
            REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3,
        }:
            documents["authorized_runtime_authority"] = json.loads(
                files[AUTHORIZED_RUNTIME_AUTHORITY_MEMBER].decode(
                    "utf-8"
                )
            )
        report = files["audience-research-report.html"]
        report.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError("package contains invalid UTF-8 JSON") from exc
    manifest = _manifest_document(manifest_value)
    if files["package-manifest.json"] != _canonical_json(manifest):
        raise PackageValidationError("package manifest is not canonical JSON")
    for key, member in _INPUT_NAMES.items():
        if key == "report":
            continue
        _document, canonical = _json_bytes(
            documents[key],
            key,
            nullable=key in {"workflow_state", "audit"},
        )
        if files[member] != canonical:
            raise PackageValidationError(f"{key} package member is not canonical JSON")
    if "panel_review_manifest" in documents:
        _document, canonical_review_manifest = _json_bytes(
            documents["panel_review_manifest"],
            "panel_review_manifest",
        )
        if files[PANEL_REVIEW_MANIFEST_MEMBER] != canonical_review_manifest:
            raise PackageValidationError(
                "panel review manifest package member is not canonical JSON"
            )
    if "migration_provenance" in documents:
        _document, canonical_provenance = _json_bytes(
            documents["migration_provenance"],
            "migration_provenance",
        )
        if files["migration-provenance.json"] != canonical_provenance:
            raise PackageValidationError(
                "migration provenance package member is not canonical JSON"
            )
    if "authorized_runtime_authority" in documents:
        _document, canonical_authority = _json_bytes(
            documents["authorized_runtime_authority"],
            "authorized_runtime_authority",
        )
        if (
            files[AUTHORIZED_RUNTIME_AUTHORITY_MEMBER]
            != canonical_authority
        ):
            raise PackageValidationError(
                "authorized runtime authority package member is not canonical JSON"
            )
    canonical = _validate_documents(documents, report, files=files)
    brief, panel, _frame, _composition, _validity, workflow, _audit = canonical
    expected_readme = _readme_bytes(brief, panel, canonical[2])
    if files["README.txt"] != expected_readme:
        raise PackageValidationError("README does not match the canonical package records")
    workflow_id = (
        workflow["workflow_id"]
        if workflow is not None
        else brief["workflow_state_binding"]
    )
    if (
        manifest["panel_id"],
        manifest["panel_version"],
        manifest["brief_id"],
        manifest["workflow_id"],
    ) != (
        panel["panel_id"],
        panel["version"],
        brief["brief_id"],
        workflow_id,
    ):
        raise PackageValidationError("manifest identity does not match package documents")
    if manifest["generated_at"] != brief["approval"]["approved_at"] or manifest["tier"] != brief["panel_tier"] or manifest["evidence_basis"] != brief["evidence_basis"]:
        raise PackageValidationError("manifest tier metadata does not match package documents")
    package_files = archive_files[:-1]
    for name in package_files:
        record = manifest["files"][name]
        if record["sha256"] != _sha256(files[name]) or record["byte_count"] != len(files[name]):
            raise PackageValidationError(f"manifest hash or byte count mismatch for {name}")
    expected_bindings = _manifest(brief=brief, panel=panel, workflow=workflow, files=files)["bindings"]
    if manifest["bindings"] != expected_bindings:
        raise PackageValidationError("manifest bindings do not match package documents")
    expected_zip = _zip_bytes(files)
    # ZIP bytes must be deterministic, including ordering, timestamps, and permissions.
    if raw != expected_zip:
        raise PackageValidationError("package ZIP bytes are not deterministic")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION_V3, "status": "valid",
        "panel_id": panel["panel_id"], "panel_version": panel["version"], "brief_id": brief["brief_id"],
        "package_manifest_sha256": _sha256(files["package-manifest.json"]),
        "package_zip_sha256": _sha256(expected_zip),
    }


def validate_package_archive_v3(package_path: Path) -> dict[str, object]:
    """Validate an untrusted v3 package without extracting it."""

    raw, _manifest = read_v3_archive_manifest(package_path)
    return _validate_package_archive_v3_snapshot(raw)


__all__ = [
    "ARCHIVE_FILES_V3", "AUTHORIZED_RUNTIME_AUTHORITY_MEMBER",
    "GENERATOR_VERSION_V3",
    "LEGACY_MIGRATION_ARCHIVE_FILES_V3",
    "LEGACY_MIGRATION_PACKAGE_FILES_V3", "PACKAGE_FILES_V3",
    "PANEL_REVIEW_MANIFEST_MEMBER", "REVIEW_BOUND_ARCHIVE_FILES_V3",
    "REVIEW_BOUND_PACKAGE_FILES_V3",
    "REVIEW_BOUND_LEGACY_MIGRATION_ARCHIVE_FILES_V3",
    "REVIEW_BOUND_LEGACY_MIGRATION_PACKAGE_FILES_V3",
    "REVIEW_BOUND_TIER3_ARCHIVE_FILES_V3",
    "REVIEW_BOUND_TIER3_PACKAGE_FILES_V3",
    "PACKAGE_SCHEMA_VERSION_V3", "PackageBuildResultV3",
    "TIER3_ARCHIVE_FILES_V3", "TIER3_PACKAGE_FILES_V3",
    "archive_files_v3_for_manifest", "build_audience_package_v3",
    "read_v3_archive_manifest", "read_v3_archive_members",
    "validate_package_archive_v3",
]
