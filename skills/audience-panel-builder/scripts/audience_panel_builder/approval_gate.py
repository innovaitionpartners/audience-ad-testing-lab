"""Hash-bound gates for canonical panel packaging and registration."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


SIBLING_SCRIPTS = Path(__file__).resolve().parents[3] / "audience-ad-testing-lab" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_package import build_audience_package  # noqa: E402

from .common import ContractError, require_identifier, require_string
from .construction_audit import (
    require_passing_construction_audit_for_documents,
)
from .workflow_state import require_approved_scope, validate_workflow_state


PACKAGE_PROPOSAL_SCHEMA_VERSION = "audience-panel-package-proposal-v1"


def _require_digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _load_object(path: Path, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must contain a JSON object")
    return payload


def _require_approved_state(workflow_state: dict[str, object]) -> dict[str, object]:
    state = validate_workflow_state(workflow_state)
    if state["state"] != "approved":
        raise ContractError("workflow state must be approved for canonical packaging")
    return state


def _require_binding(
    state: dict[str, object],
    *,
    key: str,
    actual: str,
    label: str,
) -> None:
    if state["bindings"][key] != actual:
        raise ContractError(f"{label} SHA-256 does not match workflow binding")


def _require_release_a_approval_chain(
    *,
    workflow_state: dict[str, object],
    panel_id: str,
    panel_version: str,
    brief_sha256: str,
    panel_sha256: str,
    synthesis_sha256: str,
    report_inputs_sha256: str,
    audit_sha256: str,
    panel_review_manifest_sha256: str,
) -> dict[str, object]:
    state = _require_approved_state(workflow_state)
    if state["panel_id"] != panel_id:
        raise ContractError("panel ID does not match workflow state")
    if state["panel_version"] != panel_version:
        raise ContractError("panel version does not match workflow state")
    _require_binding(state, key="brief_sha256", actual=brief_sha256, label="brief")
    _require_binding(state, key="panel_sha256", actual=panel_sha256, label="panel")
    _require_binding(
        state,
        key="report_inputs_sha256",
        actual=report_inputs_sha256,
        label="report inputs",
    )
    _require_binding(state, key="audit_sha256", actual=audit_sha256, label="audit")
    require_approved_scope(
        state,
        scope="evidence_synthesis",
        target_sha256=synthesis_sha256,
    )
    require_approved_scope(
        state,
        scope="panel_construction",
        target_sha256=panel_review_manifest_sha256,
    )
    return state


def build_package_proposal(
    *,
    workflow_state: dict[str, object],
    construction_audit: dict[str, object],
    brief_path: Path,
    panel_path: Path,
    ledger_path: Path,
    finding_support_path: Path,
    synthesis_path: Path,
    report_manifest_path: Path,
    panel_review_manifest_path: Path,
) -> dict[str, object]:
    """Calculate deterministic v2 package digests without retaining package bytes."""

    brief = _load_object(Path(brief_path), "brief")
    panel = _load_object(Path(panel_path), "panel")
    audit_result = require_passing_construction_audit_for_documents(
        construction_audit,
        brief=brief,
        panel=panel,
        evidence_ledger=_load_object(Path(ledger_path), "evidence ledger"),
        finding_support=_load_object(
            Path(finding_support_path),
            "finding support",
        ),
        synthesis_matrix=_load_object(
            Path(synthesis_path),
            "synthesis matrix",
        ),
        report_manifest=_load_object(
            Path(report_manifest_path),
            "report manifest",
        ),
        panel_review_manifest=_load_object(
            Path(panel_review_manifest_path),
            "panel review manifest",
        ),
    )
    canonical_brief = audit_result["brief"]
    canonical_panel = audit_result["panel"]
    panel_id = str(canonical_panel["panel_id"])
    panel_version = str(canonical_panel["version"])

    _require_release_a_approval_chain(
        workflow_state=workflow_state,
        panel_id=panel_id,
        panel_version=panel_version,
        brief_sha256=str(audit_result["brief_sha256"]),
        panel_sha256=str(audit_result["panel_sha256"]),
        synthesis_sha256=str(audit_result["synthesis_matrix_sha256"]),
        report_inputs_sha256=str(audit_result["report_inputs_sha256"]),
        audit_sha256=str(audit_result["audit_sha256"]),
        panel_review_manifest_sha256=str(
            audit_result["panel_review_manifest_sha256"]
        ),
    )

    with tempfile.TemporaryDirectory(prefix="audience-panel-package-proposal-") as temporary:
        result = build_audience_package(
            canonical_brief,
            canonical_panel,
            Path(temporary) / "package",
            generator_version="1.0.0",
        )
        return {
            "schema_version": PACKAGE_PROPOSAL_SCHEMA_VERSION,
            "panel_id": panel_id,
            "panel_version": panel_version,
            "brief_sha256": audit_result["brief_sha256"],
            "panel_sha256": audit_result["panel_sha256"],
            "audit_sha256": audit_result["audit_sha256"],
            "panel_review_manifest_sha256": audit_result[
                "panel_review_manifest_sha256"
            ],
            "package_manifest_sha256": result.package_manifest_sha256,
            "package_manifest_byte_count": result.manifest_path.stat().st_size,
            "package_sha256": result.package_zip_sha256,
            "package_byte_count": result.package_zip_path.stat().st_size,
        }


def require_package_build_ready(
    *,
    workflow_state: dict[str, object],
    panel_id: str,
    panel_version: str,
    brief_sha256: str,
    panel_sha256: str,
    synthesis_sha256: str,
    report_inputs_sha256: str,
    audit_sha256: str,
    panel_review_manifest_sha256: str,
    proposed_package_sha256: str,
) -> None:
    """Require exact approved workflow bindings before package materialization."""

    brief_digest = _require_digest(brief_sha256, "brief_sha256")
    panel_digest = _require_digest(panel_sha256, "panel_sha256")
    synthesis_digest = _require_digest(synthesis_sha256, "synthesis_sha256")
    report_inputs_digest = _require_digest(
        report_inputs_sha256,
        "report_inputs_sha256",
    )
    audit_digest = _require_digest(audit_sha256, "audit_sha256")
    review_manifest_digest = _require_digest(
        panel_review_manifest_sha256,
        "panel_review_manifest_sha256",
    )
    package_digest = _require_digest(
        proposed_package_sha256,
        "proposed_package_sha256",
    )
    state = _require_release_a_approval_chain(
        workflow_state=workflow_state,
        panel_id=require_identifier(panel_id, "panel_id"),
        panel_version=require_string(panel_version, "panel_version"),
        brief_sha256=brief_digest,
        panel_sha256=panel_digest,
        synthesis_sha256=synthesis_digest,
        report_inputs_sha256=report_inputs_digest,
        audit_sha256=audit_digest,
        panel_review_manifest_sha256=review_manifest_digest,
    )
    require_approved_scope(
        state,
        scope="package_registration",
        target_sha256=package_digest,
    )
    _require_binding(state, key="package_sha256", actual=package_digest, label="package")


def require_registration_ready(
    *,
    workflow_state: dict[str, object],
    panel_id: str,
    panel_version: str,
    brief_sha256: str,
    panel_sha256: str,
    synthesis_sha256: str,
    report_inputs_sha256: str,
    package_sha256: str,
    audit_sha256: str,
    panel_review_manifest_sha256: str,
) -> None:
    """Require exact approved workflow bindings before library registration."""

    package_digest = _require_digest(package_sha256, "package_sha256")
    brief_digest = _require_digest(brief_sha256, "brief_sha256")
    panel_digest = _require_digest(panel_sha256, "panel_sha256")
    synthesis_digest = _require_digest(synthesis_sha256, "synthesis_sha256")
    report_inputs_digest = _require_digest(
        report_inputs_sha256,
        "report_inputs_sha256",
    )
    audit_digest = _require_digest(audit_sha256, "audit_sha256")
    review_manifest_digest = _require_digest(
        panel_review_manifest_sha256,
        "panel_review_manifest_sha256",
    )
    state = _require_release_a_approval_chain(
        workflow_state=workflow_state,
        panel_id=require_identifier(panel_id, "panel_id"),
        panel_version=require_string(panel_version, "panel_version"),
        brief_sha256=brief_digest,
        panel_sha256=panel_digest,
        synthesis_sha256=synthesis_digest,
        report_inputs_sha256=report_inputs_digest,
        audit_sha256=audit_digest,
        panel_review_manifest_sha256=review_manifest_digest,
    )
    require_approved_scope(
        state,
        scope="package_registration",
        target_sha256=package_digest,
    )
    _require_binding(state, key="package_sha256", actual=package_digest, label="package")


__all__ = [
    "PACKAGE_PROPOSAL_SCHEMA_VERSION",
    "build_package_proposal",
    "require_package_build_ready",
    "require_registration_ready",
]
