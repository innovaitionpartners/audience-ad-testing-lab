"""Normalized evidence ledger and exact finding-support validation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping, Sequence

from .common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    require_url,
    sha256_json,
)


LEDGER_SCHEMA_VERSION = "audience-evidence-ledger-v1"
STRUCTURED_BATCH_SCHEMA_VERSION = "audience-structured-evidence-batch-v1"
FINDING_SUPPORT_SCHEMA_VERSION = "audience-finding-support-v1"

_STRUCTURED_KEYS = {
    "schema_version", "batch_id", "created_at", "source_adapter",
    "source_schema_version", "input_sha256", "permission", "source_status",
    "items",
}
_STRUCTURED_ITEM_KEYS = {
    "evidence_item_id", "source_url", "item_type", "content_summary",
    "text_fidelity", "content_sha256", "source_pointer", "upstream_source_ids",
    "use_constraints", "quality_flags",
}
_SOCIAL_KEYS = {
    "schema_version", "batch_id", "created_at", "source_adapter",
    "source_schema_version", "input_sha256", "query", "window_start",
    "window_end", "source_status", "collection", "observations",
    "coverage_warnings",
}
_OBSERVATION_KEYS = {
    "observation_id", "platform", "source_item_id", "source_url",
    "published_at", "collected_at", "unit_of_analysis", "title",
    "text_excerpt", "text_fidelity", "content_sha256", "engagement",
    "relevance_score", "cluster_id", "role_status", "author_group_token",
    "freshness_verdict", "json_pointer", "use_constraints", "quality_flags",
}
_LEDGER_KEYS = {
    "schema_version", "ledger_id", "created_at", "plan_id", "imports",
    "evidence_items", "summary",
}
_IMPORT_KEYS = {
    "import_id", "source_adapter", "source_schema_version", "input_sha256",
    "permission", "source_status", "accepted_count", "rejected_count",
    "deduplicated_count",
}
_LEDGER_ITEM_KEYS = {
    "evidence_item_id", "import_id", "source_url", "item_type",
    "content_summary", "text_fidelity", "content_sha256", "source_pointer",
    "upstream_source_ids", "use_constraints", "quality_flags",
}
_SUPPORT_KEYS = {
    "schema_version", "created_at", "ledger_sha256", "findings",
}
_SUPPORT_ITEM_KEYS = {
    "finding_id", "evidence_id", "evidence_item_ids", "support_role",
    "analyst_note",
}
_SUPPORT_ROLES = {"supports", "qualifies", "contradicts"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _import_id(batch_id: str, input_sha256: str) -> str:
    digest = hashlib.sha256(
        f"{batch_id}\0{input_sha256}".encode("utf-8")
    ).hexdigest()[:16]
    return f"import-{digest}"


def _validate_structured_batch(payload: Any) -> dict[str, Any]:
    batch = require_object(payload, _STRUCTURED_KEYS, "$")
    if batch["schema_version"] != STRUCTURED_BATCH_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {STRUCTURED_BATCH_SCHEMA_VERSION}"
        )
    require_identifier(batch["batch_id"], "$.batch_id")
    require_timestamp(batch["created_at"], "$.created_at")
    require_string(batch["source_adapter"], "$.source_adapter")
    require_string(batch["source_schema_version"], "$.source_schema_version")
    require_string(batch["input_sha256"], "$.input_sha256")
    require_enum(batch["permission"], {"allowed", "conditional"}, "$.permission")
    require_string(batch["source_status"], "$.source_status")
    seen: set[str] = set()
    for index, raw in enumerate(
        require_array(batch["items"], "$.items", nonempty=True)
    ):
        path = f"$.items[{index}]"
        item = require_object(raw, _STRUCTURED_ITEM_KEYS, path)
        item_id = require_identifier(
            item["evidence_item_id"], f"{path}.evidence_item_id"
        )
        if item_id in seen:
            raise ContractError(f"{path}.evidence_item_id is duplicated")
        seen.add(item_id)
        require_url(item["source_url"], f"{path}.source_url")
        for key in (
            "item_type", "content_summary", "text_fidelity",
            "content_sha256", "source_pointer",
        ):
            require_string(item[key], f"{path}.{key}")
        for key in ("upstream_source_ids", "use_constraints", "quality_flags"):
            require_string_array(item[key], f"{path}.{key}")
    return dict(batch)


def _social_to_import(payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batch = require_object(payload, _SOCIAL_KEYS, "$")
    if batch["schema_version"] != "social-observation-batch-v1":
        raise ContractError("$.schema_version must equal social-observation-batch-v1")
    batch_id = require_identifier(batch["batch_id"], "$.batch_id")
    require_timestamp(batch["created_at"], "$.created_at")
    import_id = _import_id(batch_id, batch["input_sha256"])
    source_status = batch["source_status"]
    if not isinstance(source_status, Mapping):
        raise ContractError("$.source_status must be an object")
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(
        require_array(batch["observations"], "$.observations")
    ):
        path = f"$.observations[{index}]"
        observation = require_object(raw, _OBSERVATION_KEYS, path)
        item_id = require_identifier(
            observation["observation_id"], f"{path}.observation_id"
        )
        require_url(
            observation["source_url"], f"{path}.source_url", allow_empty=True
        )
        items.append(
            {
                "evidence_item_id": item_id,
                "import_id": import_id,
                "source_url": observation["source_url"],
                "item_type": f"social_{observation['unit_of_analysis']}",
                "content_summary": observation["text_excerpt"],
                "text_fidelity": observation["text_fidelity"],
                "content_sha256": observation["content_sha256"],
                "source_pointer": observation["json_pointer"],
                "upstream_source_ids": [],
                "use_constraints": list(observation["use_constraints"]),
                "quality_flags": list(observation["quality_flags"]),
            }
        )
    import_record = {
        "import_id": import_id,
        "source_adapter": batch["source_adapter"],
        "source_schema_version": batch["source_schema_version"],
        "input_sha256": batch["input_sha256"],
        "permission": batch["collection"]["permitted_use"],
        "source_status": dict(sorted(source_status.items())),
        "accepted_count": len(items),
        "rejected_count": 0,
        "deduplicated_count": 0,
    }
    return import_record, items


def _structured_to_import(
    payload: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batch = _validate_structured_batch(payload)
    import_id = _import_id(batch["batch_id"], batch["input_sha256"])
    items = [
        {
            **item,
            "import_id": import_id,
        }
        for item in batch["items"]
    ]
    import_record = {
        "import_id": import_id,
        "source_adapter": batch["source_adapter"],
        "source_schema_version": batch["source_schema_version"],
        "input_sha256": batch["input_sha256"],
        "permission": batch["permission"],
        "source_status": batch["source_status"],
        "accepted_count": len(items),
        "rejected_count": 0,
        "deduplicated_count": 0,
    }
    return import_record, items


def build_evidence_ledger(
    plan_id: str,
    batches: Sequence[Any],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    require_identifier(plan_id, "$.plan_id")
    if not batches:
        raise ContractError("at least one evidence batch is required")
    imports: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, str], str] = {}
    seen_ids: set[str] = set()
    for batch_index, payload in enumerate(batches):
        if not isinstance(payload, Mapping):
            raise ContractError(f"$batches[{batch_index}] must be an object")
        version = payload.get("schema_version")
        if version == "social-observation-batch-v1":
            import_record, incoming = _social_to_import(payload)
        elif version == STRUCTURED_BATCH_SCHEMA_VERSION:
            import_record, incoming = _structured_to_import(payload)
        else:
            raise ContractError(
                f"$batches[{batch_index}] has unsupported schema_version"
            )
        accepted = 0
        deduplicated = 0
        for item in incoming:
            item_id = item["evidence_item_id"]
            if item_id in seen_ids:
                raise ContractError(f"evidence item ID is duplicated: {item_id}")
            seen_ids.add(item_id)
            key = (item["source_url"], item["content_sha256"])
            if key in seen_keys:
                deduplicated += 1
                continue
            seen_keys[key] = item_id
            items.append(item)
            accepted += 1
        import_record["accepted_count"] = accepted
        import_record["deduplicated_count"] = deduplicated
        imports.append(import_record)
    imports.sort(key=lambda item: item["import_id"])
    items.sort(key=lambda item: item["evidence_item_id"])
    timestamp = created_at or _utc_now()
    require_timestamp(timestamp, "$.created_at")
    ledger_token = hashlib.sha256(
        f"{plan_id}\0{sha256_json(imports)}\0{sha256_json(items)}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_id": f"{plan_id}-ledger-{ledger_token}",
        "created_at": timestamp,
        "plan_id": plan_id,
        "imports": imports,
        "evidence_items": items,
        "summary": {
            "imports": len(imports),
            "accepted_items": len(items),
            "deduplicated_items": sum(
                item["deduplicated_count"] for item in imports
            ),
            "rejected_items": sum(item["rejected_count"] for item in imports),
        },
    }


def validate_evidence_ledger(payload: Any) -> dict[str, Any]:
    ledger = require_object(payload, _LEDGER_KEYS, "$")
    if ledger["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise ContractError(f"$.schema_version must equal {LEDGER_SCHEMA_VERSION}")
    require_identifier(ledger["ledger_id"], "$.ledger_id")
    require_identifier(ledger["plan_id"], "$.plan_id")
    require_timestamp(ledger["created_at"], "$.created_at")
    import_ids: set[str] = set()
    for index, raw in enumerate(require_array(ledger["imports"], "$.imports")):
        path = f"$.imports[{index}]"
        item = require_object(raw, _IMPORT_KEYS, path)
        import_id = require_identifier(item["import_id"], f"{path}.import_id")
        if import_id in import_ids:
            raise ContractError(f"{path}.import_id is duplicated")
        import_ids.add(import_id)
    item_ids: set[str] = set()
    for index, raw in enumerate(
        require_array(ledger["evidence_items"], "$.evidence_items")
    ):
        path = f"$.evidence_items[{index}]"
        item = require_object(raw, _LEDGER_ITEM_KEYS, path)
        item_id = require_identifier(
            item["evidence_item_id"], f"{path}.evidence_item_id"
        )
        if item_id in item_ids:
            raise ContractError(f"{path}.evidence_item_id is duplicated")
        item_ids.add(item_id)
        if item["import_id"] not in import_ids:
            raise ContractError(f"{path}.import_id does not resolve")
    if not isinstance(ledger["summary"], Mapping):
        raise ContractError("$.summary must be an object")
    return dict(ledger)


def validate_finding_support(
    payload: Any,
    ledger_payload: Any,
) -> dict[str, Any]:
    ledger = validate_evidence_ledger(ledger_payload)
    support = require_object(payload, _SUPPORT_KEYS, "$")
    if support["schema_version"] != FINDING_SUPPORT_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {FINDING_SUPPORT_SCHEMA_VERSION}"
        )
    require_timestamp(support["created_at"], "$.created_at")
    if support["ledger_sha256"] != sha256_json(ledger):
        raise ContractError("$.ledger_sha256 does not match the exact ledger")
    item_ids = {
        item["evidence_item_id"] for item in ledger["evidence_items"]
    }
    for index, raw in enumerate(
        require_array(support["findings"], "$.findings", nonempty=True)
    ):
        path = f"$.findings[{index}]"
        item = require_object(raw, _SUPPORT_ITEM_KEYS, path)
        require_identifier(item["finding_id"], f"{path}.finding_id")
        require_identifier(item["evidence_id"], f"{path}.evidence_id")
        referenced = require_string_array(
            item["evidence_item_ids"],
            f"{path}.evidence_item_ids",
            nonempty=True,
        )
        unresolved = sorted(set(referenced) - item_ids)
        if unresolved:
            raise ContractError(
                f"{path}.evidence_item_ids do not resolve: {', '.join(unresolved)}"
            )
        require_enum(item["support_role"], _SUPPORT_ROLES, f"{path}.support_role")
        require_string(item["analyst_note"], f"{path}.analyst_note")
    return dict(support)
