"""Strict, hash-bound workflow state for Audience Panel Builder."""

from __future__ import annotations

from typing import Any

from .common import (
    ContractError,
    canonical_json_bytes,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_timestamp,
    sha256_json,
)


WORKFLOW_STATE_SCHEMA_VERSION = "panel-workflow-state-v1"
WORKFLOW_STATES = frozenset(
    {"draft", "dogfood", "provisional", "approved", "needs_refresh", "retired"}
)
APPROVAL_SCOPES = frozenset(
    {
        "evidence_synthesis",
        "panel_construction",
        "dogfood",
        "package_registration",
        "calibration",
    }
)

_TOP_KEYS = {
    "schema_version",
    "workflow_id",
    "panel_id",
    "panel_version",
    "state",
    "updated_at",
    "approvals",
    "bindings",
}
_APPROVAL_KEYS = {
    "scope", "status", "approved_by", "approved_at", "target_sha256", "note"
}
_BINDING_KEYS = {
    "brief_sha256",
    "panel_sha256",
    "report_inputs_sha256",
    "audit_sha256",
    "package_sha256",
}
_APPROVAL_STATUSES = {"pending", "approved", "rejected"}
_TRANSITIONS = {
    "draft": {"dogfood", "provisional", "approved", "retired"},
    "dogfood": {"draft", "provisional", "approved", "retired"},
    "provisional": {"draft", "approved", "needs_refresh", "retired"},
    "approved": {"needs_refresh", "retired"},
    "needs_refresh": {"draft", "approved", "retired"},
    "retired": set(),
}


def _require_digest(value: Any, path: str) -> str:
    digest = require_string(value, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _validate_approval(value: Any, path: str) -> dict[str, object]:
    approval = require_object(value, _APPROVAL_KEYS, path)
    scope = require_enum(approval["scope"], APPROVAL_SCOPES, f"{path}.scope")
    status = require_enum(approval["status"], _APPROVAL_STATUSES, f"{path}.status")
    approved_by = require_string(approval["approved_by"], f"{path}.approved_by", allow_empty=True)
    approved_at = require_string(approval["approved_at"], f"{path}.approved_at", allow_empty=True)
    if status == "pending":
        if approved_by or approved_at:
            raise ContractError(f"{path}.approved_by and {path}.approved_at must be empty for pending")
    else:
        if not approved_by:
            raise ContractError(f"{path}.approved_by must be non-empty for {status}")
        if not approved_at:
            raise ContractError(f"{path}.approved_at must be non-empty for {status}")
        require_timestamp(approved_at, f"{path}.approved_at")
    return {
        "scope": scope,
        "status": status,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "target_sha256": _require_digest(approval["target_sha256"], f"{path}.target_sha256"),
        "note": require_string(approval["note"], f"{path}.note", allow_empty=True),
    }


def validate_workflow_state(payload: object) -> dict[str, object]:
    """Validate one closed workflow state and return its canonical-shaped data."""

    state = require_object(payload, _TOP_KEYS, "$")
    if state["schema_version"] != WORKFLOW_STATE_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {WORKFLOW_STATE_SCHEMA_VERSION}"
        )
    approvals: list[dict[str, object]] = []
    seen_scopes: set[str] = set()
    for index, raw_approval in enumerate(require_array(state["approvals"], "$.approvals")):
        approval = _validate_approval(raw_approval, f"$.approvals[{index}]")
        scope = str(approval["scope"])
        if scope in seen_scopes:
            raise ContractError(f"$.approvals[{index}].scope is duplicated")
        seen_scopes.add(scope)
        approvals.append(approval)

    bindings = require_object(state["bindings"], _BINDING_KEYS, "$.bindings")
    validated_bindings: dict[str, object] = {}
    for key in sorted(_BINDING_KEYS):
        value = bindings[key]
        if value is not None:
            value = _require_digest(value, f"$.bindings.{key}")
        validated_bindings[key] = value

    updated_at = require_string(state["updated_at"], "$.updated_at")
    require_timestamp(updated_at, "$.updated_at")
    workflow_status = require_enum(state["state"], WORKFLOW_STATES, "$.state")
    if workflow_status == "approved":
        approval_statuses = {
            str(approval["scope"]): str(approval["status"])
            for approval in approvals
        }
        for scope in ("evidence_synthesis", "panel_construction"):
            if approval_statuses.get(scope) != "approved":
                raise ContractError(f"approved requires {scope} approval")

    return {
        "schema_version": state["schema_version"],
        "workflow_id": require_identifier(state["workflow_id"], "$.workflow_id"),
        "panel_id": require_identifier(state["panel_id"], "$.panel_id"),
        "panel_version": require_string(state["panel_version"], "$.panel_version"),
        "state": workflow_status,
        "updated_at": updated_at,
        "approvals": approvals,
        "bindings": validated_bindings,
    }


def canonical_workflow_state_bytes(payload: object) -> bytes:
    """Return the shared canonical JSON encoding of a validated workflow state."""

    return canonical_json_bytes(validate_workflow_state(payload))


def workflow_state_sha256(payload: object) -> str:
    """Return the unprefixed SHA-256 digest used by this contract's bindings."""

    return sha256_json(validate_workflow_state(payload)).removeprefix("sha256:")


def require_approved_scope(
    payload: object,
    *,
    scope: str,
    target_sha256: str,
) -> dict[str, object]:
    """Require one approved scope row bound to the exact current target hash."""

    requested_scope = require_enum(scope, APPROVAL_SCOPES, "scope")
    requested_target = _require_digest(target_sha256, "target_sha256")
    state = validate_workflow_state(payload)
    for approval in state["approvals"]:
        if approval["scope"] == requested_scope:
            if approval["status"] != "approved":
                raise ContractError(f"{requested_scope} approval must be approved")
            if approval["target_sha256"] != requested_target:
                raise ContractError(
                    f"{requested_scope} approval must match the exact target SHA-256"
                )
            return approval
    raise ContractError(f"{requested_scope} approval is required")


def transition_workflow_state(
    payload: object,
    *,
    next_state: str,
    updated_at: str,
) -> dict[str, object]:
    """Return a validated state after one allowed, approval-gated transition."""

    state = validate_workflow_state(payload)
    requested_state = require_enum(next_state, WORKFLOW_STATES, "next_state")
    require_timestamp(updated_at, "updated_at")
    current_state = str(state["state"])
    if current_state == "retired":
        raise ContractError("retired workflow state is terminal")
    if requested_state not in _TRANSITIONS[current_state]:
        raise ContractError(f"{current_state} may not transition to {requested_state}")
    if requested_state == "approved":
        approval_statuses = {
            str(approval["scope"]): str(approval["status"])
            for approval in state["approvals"]
        }
        for scope in ("evidence_synthesis", "panel_construction"):
            if approval_statuses.get(scope) != "approved":
                raise ContractError(f"approved requires {scope} approval")
    state["state"] = requested_state
    state["updated_at"] = updated_at
    return validate_workflow_state(state)
