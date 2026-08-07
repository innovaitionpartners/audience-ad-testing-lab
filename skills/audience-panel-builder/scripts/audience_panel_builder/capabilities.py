"""Strict read-only connector capability inventory."""

from __future__ import annotations

from typing import Any

from .common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    sha256_json,
)


CAPABILITY_SCHEMA_VERSION = "connector-capability-inventory-v1"

_TOP_KEYS = {
    "schema_version", "detected_at", "runtime", "connectors",
    "unresolved_capabilities",
}
_CONNECTOR_KEYS = {
    "connector_id", "provider", "server_or_tool", "detection_method", "status",
    "schema_fingerprint", "capabilities", "scope", "constraints", "privacy_risk",
}
_STATUSES = {
    "available_verified", "available_unverified", "unavailable",
    "authentication_required", "permission_denied", "unsupported_schema",
    "not_applicable",
}
_CAPABILITIES = {
    "read_owned_posts", "read_owned_comments", "read_owned_analytics",
    "read_public_reviews", "query_saved_listening_topics",
    "query_ad_hoc_listening", "read_earned_mentions",
    "read_aggregate_listening_metrics", "read_message_text", "filter_by_date",
    "filter_by_platform", "filter_by_language", "filter_by_geography",
    "paginate", "report_total_available", "export_raw_rows",
    "search_public_web", "read_public_web",
}
_WRITE_CAPABILITY_TOKENS = {
    "publish", "reply", "delete", "tag", "write", "send", "update",
}


def validate_capability_inventory(payload: Any) -> dict[str, Any]:
    top = require_object(payload, _TOP_KEYS, "$")
    if top["schema_version"] != CAPABILITY_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {CAPABILITY_SCHEMA_VERSION}"
        )
    require_timestamp(top["detected_at"], "$.detected_at")
    require_string(top["runtime"], "$.runtime")
    seen: set[str] = set()
    for index, raw in enumerate(
        require_array(top["connectors"], "$.connectors")
    ):
        path = f"$.connectors[{index}]"
        connector = require_object(raw, _CONNECTOR_KEYS, path)
        connector_id = require_identifier(
            connector["connector_id"], f"{path}.connector_id"
        )
        if connector_id in seen:
            raise ContractError(f"{path}.connector_id is duplicated")
        seen.add(connector_id)
        for key in (
            "provider", "server_or_tool", "detection_method",
            "schema_fingerprint", "scope", "constraints", "privacy_risk",
        ):
            require_string(connector[key], f"{path}.{key}", allow_empty=True)
        require_enum(connector["status"], _STATUSES, f"{path}.status")
        capabilities = require_string_array(
            connector["capabilities"], f"{path}.capabilities"
        )
        unknown = sorted(set(capabilities) - _CAPABILITIES)
        if unknown:
            raise ContractError(
                f"{path}.capabilities has unsupported values: {', '.join(unknown)}"
            )
        for capability in capabilities:
            if any(token in capability.casefold() for token in _WRITE_CAPABILITY_TOKENS):
                raise ContractError(f"{path}.capabilities may contain only read capabilities")
    unresolved = require_string_array(
        top["unresolved_capabilities"], "$.unresolved_capabilities"
    )
    return {
        "schema_version": top["schema_version"],
        "detected_at": top["detected_at"],
        "runtime": top["runtime"],
        "connectors": [dict(item) for item in top["connectors"]],
        "unresolved_capabilities": unresolved,
    }


def verified_capabilities(payload: Any) -> set[str]:
    inventory = validate_capability_inventory(payload)
    result: set[str] = set()
    for connector in inventory["connectors"]:
        if connector["status"] == "available_verified":
            result.update(connector["capabilities"])
    return result


def capability_inventory_sha256(payload: Any) -> str:
    return sha256_json(validate_capability_inventory(payload))
