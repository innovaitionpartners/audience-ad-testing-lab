"""Closed exact-variant capability registry and fail-closed dispatch."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from .common import ContractError, closed_object, sha256_json
from .container_safety import ContainerInventory


class CapabilityRegistryError(ValueError):
    pass


_CAPABILITY_KEYS = {
    "adapter_id",
    "platform",
    "report_type",
    "container",
    "locale",
    "schema_fingerprint",
    "row_grain",
    "identity_fields",
    "required_fields",
    "metric_fields",
    "time_basis",
    "currency_basis",
    "value_states",
    "non_personal_identity_fields",
    "prohibited_fields",
    "group_size_field",
    "official_documentation",
    "fixture_ids",
    "maturity",
    "availability_reason",
    "adapter_version",
    "reviewer",
    "verified_at",
    "contract_ready_permitted",
}
_CONTAINERS = {"csv", "tsv", "xlsx", "json", "xml"}
_MATURITY = {"schema_tested", "export_verified", "blocked"}
_BLOCKED_REASON = "blocked_pending_sanitized_sample"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CONTAINER_MEDIA_TYPES = {
    "csv": frozenset({"text/csv"}),
    "tsv": frozenset({"text/tab-separated-values"}),
    "xlsx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }
    ),
    "json": frozenset({"application/json"}),
    "xml": frozenset({"application/xml", "text/xml"}),
}


@dataclass(frozen=True)
class AdapterCapability:
    adapter_id: str
    platform: str
    report_type: str
    container: str
    locale: str
    schema_fingerprint: str
    row_grain: tuple[str, ...]
    identity_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    metric_fields: tuple[str, ...]
    time_basis: str
    currency_basis: str
    value_states: tuple[str, ...]
    non_personal_identity_fields: tuple[str, ...]
    prohibited_fields: tuple[str, ...]
    group_size_field: str
    official_documentation: tuple[str, ...]
    fixture_ids: tuple[str, ...]
    maturity: str
    availability_reason: str | None
    adapter_version: str
    reviewer: str | None
    verified_at: str | None
    contract_ready_permitted: bool


@dataclass(frozen=True)
class Detection:
    adapter_id: str | None
    status: str
    confidence: str
    reasons: tuple[str, ...]


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapabilityRegistryError(f"{path} must be a non-empty string")
    return value


def _identifier(value: object, path: str) -> str:
    result = _string(value, path)
    if not _IDENTIFIER.fullmatch(result):
        raise CapabilityRegistryError(f"{path} is not a canonical identifier")
    return result


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _string_tuple(
    value: object,
    path: str,
    *,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CapabilityRegistryError(f"{path} must be a list")
    result = tuple(_string(item, f"{path}[]") for item in value)
    if nonempty and not result:
        raise CapabilityRegistryError(f"{path} must not be empty")
    if len(set(result)) != len(result):
        raise CapabilityRegistryError(f"{path} contains duplicates")
    return result


def _record(value: object, index: int) -> AdapterCapability:
    path = f"capabilities[{index}]"
    try:
        document = closed_object(value, _CAPABILITY_KEYS, path)
    except ContractError as exc:
        raise CapabilityRegistryError(str(exc)) from exc
    adapter_id = _identifier(document["adapter_id"], f"{path}.adapter_id")
    platform = _identifier(document["platform"], f"{path}.platform")
    report_type = _identifier(document["report_type"], f"{path}.report_type")
    container = _string(document["container"], f"{path}.container")
    if container not in _CONTAINERS:
        raise CapabilityRegistryError(f"{path}.container is unsupported")
    locale = _string(document["locale"], f"{path}.locale")
    schema_fingerprint = _string(
        document["schema_fingerprint"], f"{path}.schema_fingerprint"
    )
    if not _DIGEST.fullmatch(schema_fingerprint):
        raise CapabilityRegistryError(
            f"{path}.schema_fingerprint is not a canonical digest"
        )
    row_grain = _string_tuple(
        document["row_grain"], f"{path}.row_grain", nonempty=True
    )
    identity_fields = _string_tuple(
        document["identity_fields"],
        f"{path}.identity_fields",
        nonempty=True,
    )
    required_fields = _string_tuple(
        document["required_fields"],
        f"{path}.required_fields",
        nonempty=True,
    )
    metric_fields = _string_tuple(
        document["metric_fields"],
        f"{path}.metric_fields",
        nonempty=True,
    )
    allowlist = sorted(
        set(identity_fields) | set(required_fields) | set(metric_fields)
    )
    if len(allowlist) != (
        len(identity_fields) + len(required_fields) + len(metric_fields)
    ):
        raise CapabilityRegistryError(
            f"{path} field allowlists must be disjoint"
        )
    if sha256_json(allowlist) != schema_fingerprint:
        raise CapabilityRegistryError(
            f"{path}.schema_fingerprint does not match exact field allowlist"
        )
    time_basis = _string(document["time_basis"], f"{path}.time_basis")
    currency_basis = _string(
        document["currency_basis"], f"{path}.currency_basis"
    )
    value_states = _string_tuple(
        document["value_states"], f"{path}.value_states", nonempty=True
    )
    non_personal_identity_fields = _string_tuple(
        document["non_personal_identity_fields"],
        f"{path}.non_personal_identity_fields",
        nonempty=True,
    )
    if not set(non_personal_identity_fields).issubset(identity_fields):
        raise CapabilityRegistryError(
            f"{path}.non_personal_identity_fields must be identity fields"
        )
    prohibited_fields = _string_tuple(
        document["prohibited_fields"],
        f"{path}.prohibited_fields",
        nonempty=True,
    )
    group_size_field = _string(
        document["group_size_field"], f"{path}.group_size_field"
    )
    if group_size_field not in required_fields:
        raise CapabilityRegistryError(
            f"{path}.group_size_field must be a required field"
        )
    official_documentation = _string_tuple(
        document["official_documentation"],
        f"{path}.official_documentation",
        nonempty=True,
    )
    if any(
        not item.startswith("https://") for item in official_documentation
    ):
        raise CapabilityRegistryError(
            f"{path}.official_documentation must use HTTPS"
        )
    fixture_ids = _string_tuple(
        document["fixture_ids"], f"{path}.fixture_ids", nonempty=True
    )
    for fixture_index, fixture_id in enumerate(fixture_ids):
        _identifier(fixture_id, f"{path}.fixture_ids[{fixture_index}]")
    maturity = _string(document["maturity"], f"{path}.maturity")
    if maturity not in _MATURITY:
        raise CapabilityRegistryError(f"{path}.maturity is unsupported")
    availability_reason = _optional_string(
        document["availability_reason"], f"{path}.availability_reason"
    )
    adapter_version = _identifier(
        document["adapter_version"], f"{path}.adapter_version"
    )
    reviewer = _optional_string(document["reviewer"], f"{path}.reviewer")
    verified_at = _optional_string(
        document["verified_at"], f"{path}.verified_at"
    )
    if verified_at is not None:
        try:
            datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CapabilityRegistryError(
                f"{path}.verified_at is not an ISO-8601 timestamp"
            ) from exc
    contract_ready_permitted = document["contract_ready_permitted"]
    if not isinstance(contract_ready_permitted, bool):
        raise CapabilityRegistryError(
            f"{path}.contract_ready_permitted must be boolean"
        )
    if maturity == "blocked":
        if (
            availability_reason != _BLOCKED_REASON
            or contract_ready_permitted
            or reviewer is not None
            or verified_at is not None
        ):
            raise CapabilityRegistryError(
                f"{path} blocked maturity metadata is invalid"
            )
    elif maturity == "schema_tested":
        if (
            availability_reason is not None
            or contract_ready_permitted
            or reviewer is not None
            or verified_at is not None
        ):
            raise CapabilityRegistryError(
                f"{path} schema-tested maturity metadata is invalid"
            )
    elif (
        availability_reason is not None
        or not contract_ready_permitted
        or reviewer is None
        or verified_at is None
    ):
        raise CapabilityRegistryError(
            f"{path} export-verified maturity metadata is incomplete"
        )
    return AdapterCapability(
        adapter_id=adapter_id,
        platform=platform,
        report_type=report_type,
        container=container,
        locale=locale,
        schema_fingerprint=schema_fingerprint,
        row_grain=row_grain,
        identity_fields=identity_fields,
        required_fields=required_fields,
        metric_fields=metric_fields,
        time_basis=time_basis,
        currency_basis=currency_basis,
        value_states=value_states,
        non_personal_identity_fields=non_personal_identity_fields,
        prohibited_fields=prohibited_fields,
        group_size_field=group_size_field,
        official_documentation=official_documentation,
        fixture_ids=fixture_ids,
        maturity=maturity,
        availability_reason=availability_reason,
        adapter_version=adapter_version,
        reviewer=reviewer,
        verified_at=verified_at,
        contract_ready_permitted=contract_ready_permitted,
    )


def _validate_registry(
    records: Iterable[AdapterCapability],
) -> tuple[AdapterCapability, ...]:
    result = tuple(records)
    if not result:
        raise CapabilityRegistryError("capability registry must not be empty")
    if any(type(record) is not AdapterCapability for record in result):
        raise CapabilityRegistryError(
            "capability registry contains an invalid record"
        )
    adapter_ids: set[str] = set()
    exact_signatures: set[tuple[str, str]] = set()
    fixture_ids: set[str] = set()
    for record in result:
        if record.adapter_id in adapter_ids:
            raise CapabilityRegistryError(
                f"duplicate adapter_id: {record.adapter_id}"
            )
        adapter_ids.add(record.adapter_id)
        signature = (record.container, record.schema_fingerprint)
        if signature in exact_signatures:
            raise CapabilityRegistryError(
                "capability registry has an exact signature collision"
            )
        exact_signatures.add(signature)
        overlap = fixture_ids.intersection(record.fixture_ids)
        if overlap:
            raise CapabilityRegistryError(
                "capability registry has duplicate fixture IDs"
            )
        fixture_ids.update(record.fixture_ids)
    return result


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CapabilityRegistryError(
                "capability registry has a duplicate JSON key"
            )
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise CapabilityRegistryError(
        "capability registry has a nonfinite JSON value"
    )


def load_capability_registry(
    path: Path | str,
) -> tuple[AdapterCapability, ...]:
    registry_path = Path(path)
    try:
        value = json.loads(
            registry_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except CapabilityRegistryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapabilityRegistryError(
            "capability registry could not be read"
        ) from exc
    if not isinstance(value, list):
        raise CapabilityRegistryError("capability registry must be a list")
    return _validate_registry(
        _record(item, index) for index, item in enumerate(value)
    )


def _inventory_container(inventory: ContainerInventory) -> str | None:
    matches = [
        container
        for container, media_types in _CONTAINER_MEDIA_TYPES.items()
        if inventory.media_type in media_types
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _inventory_fingerprints(
    inventory: ContainerInventory,
) -> tuple[str, ...] | None:
    if (
        not inventory.tables
        or len(inventory.tables) != len(inventory.headers)
        or len(set(inventory.tables)) != len(inventory.tables)
    ):
        return None
    fingerprints: list[str] = []
    for headers in inventory.headers:
        if (
            not headers
            or any(
                not isinstance(header, str) or not header
                for header in headers
            )
            or len(headers) != len(set(headers))
        ):
            return None
        fingerprints.append(sha256_json(sorted(headers)))
    return tuple(fingerprints)


def resolve_adapter(
    inventory: ContainerInventory,
    registry: Iterable[AdapterCapability],
) -> Detection:
    if not isinstance(inventory, ContainerInventory):
        raise CapabilityRegistryError("container inventory is invalid")
    records = tuple(registry)
    if any(type(record) is not AdapterCapability for record in records):
        raise CapabilityRegistryError(
            "capability registry contains an invalid record"
        )
    container = _inventory_container(inventory)
    fingerprints = _inventory_fingerprints(inventory)
    if container is None or fingerprints is None:
        return Detection(
            adapter_id=None,
            status="unsupported_exact_variant",
            confidence="none",
            reasons=("container_or_schema_not_admitted",),
        )
    matches = tuple(
        record
        for record in records
        if record.container == container
        and all(
            fingerprint == record.schema_fingerprint
            for fingerprint in fingerprints
        )
    )
    if not matches:
        return Detection(
            adapter_id=None,
            status="unsupported_exact_variant",
            confidence="none",
            reasons=("no_exact_container_and_header_match",),
        )
    if len(matches) != 1:
        return Detection(
            adapter_id=None,
            status="ambiguous_exact_variant",
            confidence="none",
            reasons=("exact_signature_collision",),
        )
    match = matches[0]
    if match.maturity == "blocked":
        return Detection(
            adapter_id=match.adapter_id,
            status=_BLOCKED_REASON,
            confidence="exact",
            reasons=("exact_variant_blocked",),
        )
    return Detection(
        adapter_id=match.adapter_id,
        status=match.maturity,
        confidence="exact",
        reasons=("exact_container_and_header_match",),
    )
