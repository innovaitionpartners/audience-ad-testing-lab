"""Five-stage adapter protocol and exact pre-admission privacy validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Protocol, runtime_checkable

from ..capabilities import (
    AdapterCapability,
    Detection,
    resolve_adapter,
)
from ..container_safety import ContainerInventory, InventoryCell
from ..common import sha256_json
from ..privacy import (
    AdapterAdmissionValidation,
    container_inventory_sha256,
    normalize_header,
)


class AdapterError(ValueError):
    pass


@dataclass(frozen=True)
class AdapterInventory:
    capability: AdapterCapability
    tables: tuple[str, ...]
    headers: tuple[tuple[str, ...], ...]
    row_count: int
    reporting_metadata: dict[str, object]


@dataclass(frozen=True)
class AdapterValidation:
    accepted: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    observed_minimum_group_size: int | None = None
    inventory_sha256: str | None = None


@dataclass(frozen=True)
class AdapterResult:
    adapter_id: str
    adapter_version: str
    maturity: str
    source_sha256: str
    source_rows: int
    normalized_rows: tuple[dict[str, object], ...]
    quarantined_rows: tuple[dict[str, object], ...]
    mapping_report: dict[str, object]


@runtime_checkable
class OutcomeAdapter(Protocol):
    adapter_id: str

    def detect(self, inventory: ContainerInventory) -> Detection: ...

    def inventory(
        self,
        inventory: ContainerInventory,
        capability: AdapterCapability,
    ) -> AdapterInventory: ...

    def validate(
        self,
        inventory: AdapterInventory,
        *,
        registration: Mapping[str, object],
        governance: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterValidation: ...

    def normalize(
        self,
        inventory: AdapterInventory,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterResult: ...

    def explain(
        self,
        *,
        inventory: AdapterInventory,
        validation: AdapterValidation,
        result: AdapterResult,
    ) -> dict[str, object]: ...


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NO_PROFILE_SHA256 = sha256_json({"profile": "not_applicable"})
_PROHIBITED_BUSINESS_TOKENS = frozenset(
    {
        "aov",
        "analytics",
        "clv",
        "crm",
        "customer",
        "gmv",
        "ltv",
        "retention",
        "revenue",
        "roas",
        "sales",
    }
)
_PROHIBITED_BUSINESS_ACRONYMS = frozenset(
    {"aov", "aovs", "clv", "clvs", "gmv", "gmvs", "ltv", "ltvs", "roas"}
)
_BUSINESS_PART_CANONICAL = {
    "ads": "ad",
    "advertisements": "advertising",
    "amounts": "amount",
    "counts": "count",
    "customers": "customer",
    "orders": "order",
    "purchases": "purchase",
    "rates": "rate",
    "returns": "return",
    "revenues": "revenue",
    "spends": "spend",
    "totals": "total",
    "values": "value",
    "volumes": "volume",
}
_PROHIBITED_BUSINESS_ORDERED_FAMILIES = (
    (
        frozenset({"lifetime"}),
        frozenset({"value"}),
    ),
    (
        frozenset({"gross"}),
        frozenset({"merchandise"}),
        frozenset({"value", "volume"}),
    ),
    (
        frozenset({"return"}),
        frozenset({"on"}),
        frozenset({"ad", "advertising"}),
        frozenset({"spend"}),
    ),
    (
        frozenset({"order", "purchase"}),
        frozenset({"amount", "revenue", "value"}),
    ),
)
_PURCHASE_ORDER_NOUNS = frozenset({"order", "purchase"})
_COUNT_RATE_MARKERS = frozenset({"count", "rate"})
_ALLOWED_PLATFORM_BUSINESS_LABEL_IDENTITIES = frozenset({"customer_id"})


def normalize_business_data_label(value: str) -> str:
    if not isinstance(value, str):
        raise AdapterError("business data label is invalid")
    separated = re.sub(
        r"(?<=[A-Z])(?=[A-Z][a-z])",
        "_",
        value.strip(),
    )
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", separated)
    return re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")


def _matches_ordered_business_family(
    parts: tuple[str, ...], family: tuple[frozenset[str], ...]
) -> bool:
    alternative_index = 0
    for part in parts:
        if part not in family[alternative_index]:
            continue
        alternative_index += 1
        if alternative_index == len(family):
            return True
    return False


def _has_unexempted_purchase_order_total(parts: tuple[str, ...]) -> bool:
    for noun_index, part in enumerate(parts):
        if part not in _PURCHASE_ORDER_NOUNS:
            continue
        for total_index in range(noun_index + 1, len(parts)):
            if parts[total_index] != "total":
                continue
            between = parts[noun_index + 1 : total_index]
            if not (set(between) & _COUNT_RATE_MARKERS):
                return True
    return False


def is_prohibited_business_data_label(value: str) -> bool:
    normalized = normalize_business_data_label(value)
    raw_parts = tuple(part for part in normalized.split("_") if part)
    compact = "".join(raw_parts)
    if compact in _PROHIBITED_BUSINESS_ACRONYMS:
        return True
    parts = tuple(
        _BUSINESS_PART_CANONICAL.get(part, part) for part in raw_parts
    )
    if set(parts) & _PROHIBITED_BUSINESS_TOKENS:
        return True
    if any(
        _matches_ordered_business_family(parts, family)
        for family in _PROHIBITED_BUSINESS_ORDERED_FAMILIES
    ):
        return True
    return _has_unexempted_purchase_order_total(parts)


def prohibited_business_data_fields(
    fields: object,
    *,
    allowed_platform_identities: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if not isinstance(fields, (list, tuple, set, frozenset)):
        raise AdapterError("business data field collection is invalid")
    if any(not isinstance(field, str) or not field for field in fields):
        raise AdapterError("business data field is invalid")
    requested_allowed = {
        normalize_business_data_label(field)
        for field in allowed_platform_identities
    }
    if not requested_allowed.issubset(
        _ALLOWED_PLATFORM_BUSINESS_LABEL_IDENTITIES
    ):
        raise AdapterError("business data platform identity exception is invalid")
    return tuple(
        sorted(
            field
            for field in fields
            if normalize_business_data_label(field) not in requested_allowed
            and is_prohibited_business_data_label(field)
        )
    )


def require_no_prohibited_business_data(
    fields: object,
    *,
    context: str,
    allowed_platform_identities: tuple[str, ...] = (),
) -> None:
    prohibited = prohibited_business_data_fields(
        fields,
        allowed_platform_identities=allowed_platform_identities,
    )
    if prohibited:
        raise AdapterError(
            f"{context} contains prohibited business data fields: "
            f"{list(prohibited)}"
        )


def _minimum_group_size_rule(governance: Mapping[str, object]) -> int:
    source: Mapping[str, object] = governance
    nested = governance.get("governance_input")
    if isinstance(nested, Mapping):
        source = nested
    value = source.get("minimum_group_size_rule")
    if isinstance(value, bool):
        raise AdapterError("minimum_group_size_rule is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError("minimum_group_size_rule is invalid") from exc
    if str(result) != str(value) or result < 0:
        raise AdapterError("minimum_group_size_rule is invalid")
    return result


def _observed_minimum(
    inventory: ContainerInventory,
    field: str,
    exact_headers: tuple[str, ...],
) -> tuple[int | None, str | None]:
    if not inventory.headers or all(
        field not in headers for headers in inventory.headers
    ):
        return None, "missing_registered_group_size"
    if not _inventory_rows_are_complete(inventory, exact_headers):
        return None, "malformed_inventory_structure"
    values = [
        cell.value
        for cell in inventory.cells
        if cell.column_name == field
    ]
    if not values:
        return None, "unparseable_registered_group_size"
    parsed: list[int] = []
    for value in values:
        try:
            number = Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return None, "unparseable_registered_group_size"
        if (
            not number.is_finite()
            or number < 0
            or number != number.to_integral_value()
        ):
            return None, "unparseable_registered_group_size"
        parsed.append(int(number))
    return min(parsed), None


def _inventory_rows_are_complete(
    inventory: ContainerInventory,
    exact_headers: tuple[str, ...],
) -> bool:
    exact_header_set = set(exact_headers)
    if (
        not exact_headers
        or len(exact_header_set) != len(exact_headers)
        or isinstance(inventory.row_count, bool)
        or not isinstance(inventory.row_count, int)
        or inventory.row_count < 0
        or len(inventory.tables) != len(inventory.headers)
        or len(set(inventory.tables)) != len(inventory.tables)
    ):
        return False
    table_headers: dict[str, tuple[str, ...]] = {}
    for table, headers in zip(
        inventory.tables, inventory.headers, strict=True
    ):
        if (
            not isinstance(table, str)
            or not table
            or not isinstance(headers, tuple)
            or not headers
            or any(
                not isinstance(header, str) or not header
                for header in headers
            )
            or len(set(headers)) != len(headers)
            or set(headers) != exact_header_set
        ):
            return False
        table_headers[table] = headers

    coordinates: set[tuple[str, int, str]] = set()
    rows: dict[tuple[str, int], set[str]] = {}
    for cell in inventory.cells:
        if (
            type(cell) is not InventoryCell
            or cell.table not in table_headers
            or isinstance(cell.row_number, bool)
            or not isinstance(cell.row_number, int)
            or cell.row_number < 1
            or cell.column_name not in table_headers[cell.table]
            or not isinstance(cell.value, str)
        ):
            return False
        coordinate = (cell.table, cell.row_number, cell.column_name)
        if coordinate in coordinates:
            return False
        coordinates.add(coordinate)
        rows.setdefault(
            (cell.table, cell.row_number), set()
        ).add(cell.column_name)

    if len(rows) != inventory.row_count:
        return False
    return all(
        columns == set(table_headers[table])
        for (table, _), columns in rows.items()
    )


class ExactVariantAdapter:
    """Closed structural adapter; platform modules own semantic normalization."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("adapter capability is invalid")
        self.capability = capability
        self.adapter_id = capability.adapter_id

    def detect(self, inventory: ContainerInventory) -> Detection:
        return resolve_adapter(inventory, (self.capability,))

    def inventory(
        self,
        inventory: ContainerInventory,
        capability: AdapterCapability,
        *,
        require_exact_schema: bool = True,
    ) -> AdapterInventory:
        if not isinstance(inventory, ContainerInventory):
            raise AdapterError("container inventory is invalid")
        if (
            type(capability) is not AdapterCapability
            or capability != self.capability
        ):
            raise AdapterError("adapter capability does not match adapter")
        detection = self.detect(inventory)
        if require_exact_schema and detection.adapter_id != self.adapter_id:
            raise AdapterError("container does not match exact adapter variant")
        all_headers = tuple(
            header
            for table_headers in inventory.headers
            for header in table_headers
        )
        normalized_headers = {
            normalize_header(header) for header in all_headers
        }
        prohibited = tuple(
            sorted(
                field
                for field in capability.prohibited_fields
                if normalize_header(field) in normalized_headers
            )
        )
        prohibited_business = prohibited_business_data_fields(
            all_headers,
            allowed_platform_identities=(
                tuple(
                    field
                    for field in capability.non_personal_identity_fields
                    if normalize_business_data_label(field)
                    in _ALLOWED_PLATFORM_BUSINESS_LABEL_IDENTITIES
                )
            ),
        )
        exact_headers = tuple(
            capability.identity_fields
            + capability.required_fields
            + capability.metric_fields
        )
        observed, group_error = _observed_minimum(
            inventory,
            capability.group_size_field,
            exact_headers,
        )
        metadata: dict[str, object] = {
            "media_type": inventory.media_type,
            "inventory_sha256": container_inventory_sha256(inventory),
            "exact_schema_match": detection.adapter_id == self.adapter_id,
            "detected_adapter_id": detection.adapter_id,
            "prohibited_fields_present": prohibited,
            "prohibited_business_fields_present": prohibited_business,
            "observed_minimum_group_size": observed,
            "group_size_error": group_error,
        }
        return AdapterInventory(
            capability=capability,
            tables=tuple(inventory.tables),
            headers=tuple(tuple(headers) for headers in inventory.headers),
            row_count=inventory.row_count,
            reporting_metadata=metadata,
        )

    def validate(
        self,
        inventory: AdapterInventory,
        *,
        registration: Mapping[str, object],
        governance: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterValidation:
        if type(inventory) is not AdapterInventory:
            raise AdapterError("adapter inventory is invalid")
        if not isinstance(registration, Mapping) or not isinstance(
            governance, Mapping
        ):
            raise AdapterError("adapter validation context is invalid")
        if (
            type(capability) is not AdapterCapability
            or capability != self.capability
            or inventory.capability != capability
        ):
            raise AdapterError("adapter capability does not match inventory")
        errors: set[str] = set()
        metadata = inventory.reporting_metadata
        if metadata.get("exact_schema_match") is not True:
            errors.add("unsupported_exact_variant")
        if capability.maturity == "blocked":
            errors.add("exact_variant_unavailable")
        prohibited = metadata.get("prohibited_fields_present")
        if not isinstance(prohibited, tuple):
            raise AdapterError("adapter inventory validation metadata is invalid")
        if prohibited:
            errors.add("prohibited_field")
        prohibited_business = metadata.get(
            "prohibited_business_fields_present"
        )
        if not isinstance(prohibited_business, tuple):
            raise AdapterError("adapter inventory validation metadata is invalid")
        if prohibited_business:
            errors.add("prohibited_business_data")
        group_error = metadata.get("group_size_error")
        if group_error is not None:
            if group_error not in {
                "malformed_inventory_structure",
                "missing_registered_group_size",
                "unparseable_registered_group_size",
            }:
                raise AdapterError(
                    "adapter inventory validation metadata is invalid"
                )
            errors.add(group_error)
        observed = metadata.get("observed_minimum_group_size")
        if observed is not None and (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or observed < 0
        ):
            raise AdapterError("adapter inventory group size is invalid")
        try:
            minimum = _minimum_group_size_rule(governance)
        except AdapterError:
            minimum = None
            errors.add("invalid_minimum_group_size_rule")
        if (
            minimum is not None
            and isinstance(observed, int)
            and observed < minimum
        ):
            errors.add("minimum_group_size_below_rule")
        inventory_sha256 = metadata.get("inventory_sha256")
        if (
            not isinstance(inventory_sha256, str)
            or not _DIGEST.fullmatch(inventory_sha256)
        ):
            raise AdapterError("adapter inventory binding is invalid")
        warnings = (
            ("schema_tested_not_export_verified",)
            if capability.maturity == "schema_tested"
            else ()
        )
        return AdapterValidation(
            accepted=not errors,
            errors=tuple(sorted(errors)),
            warnings=warnings,
            observed_minimum_group_size=observed,
            inventory_sha256=inventory_sha256,
        )

    def admission_validation(
        self,
        inventory: ContainerInventory,
        *,
        source_sha256: str,
        validation: AdapterValidation,
        registration: Mapping[str, object],
        governance: Mapping[str, object],
        normalization_context: Mapping[str, object] | None = None,
    ) -> AdapterAdmissionValidation:
        if not isinstance(inventory, ContainerInventory):
            raise AdapterError("container inventory is invalid")
        if not isinstance(source_sha256, str) or not _DIGEST.fullmatch(
            source_sha256
        ):
            raise AdapterError("source binding is invalid")
        if type(validation) is not AdapterValidation:
            raise AdapterError("adapter validation is invalid")
        derived_inventory = self.inventory(
            inventory,
            self.capability,
            require_exact_schema=False,
        )
        derived_validation = self.validate(
            derived_inventory,
            registration=registration,
            governance=governance,
            capability=self.capability,
        )
        if validation.inventory_sha256 != derived_validation.inventory_sha256:
            raise AdapterError(
                "adapter validation inventory binding mismatch"
            )
        if validation != derived_validation:
            raise AdapterError(
                "adapter validation does not match validated result"
            )
        if (
            not validation.accepted
            or validation.errors
            or validation.observed_minimum_group_size is None
        ):
            raise AdapterError("adapter validation is not accepted")
        inventory_sha256 = container_inventory_sha256(inventory)
        if validation.inventory_sha256 != inventory_sha256:
            raise AdapterError("adapter validation inventory binding mismatch")
        return AdapterAdmissionValidation(
            adapter_id=self.capability.adapter_id,
            adapter_version=self.capability.adapter_version,
            source_sha256=source_sha256,
            inventory_sha256=inventory_sha256,
            profile_sha256=(
                _NO_PROFILE_SHA256
                if normalization_context is None
                else sha256_json(dict(normalization_context))
            ),
            adapter_validation_sha256=sha256_json(asdict(validation)),
            governance_sha256=sha256_json(dict(governance)),
            accepted=True,
            observed_minimum_group_size=(
                validation.observed_minimum_group_size
            ),
            errors=(),
        )

    def normalize(
        self,
        inventory: AdapterInventory,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterResult:
        raise AdapterError(
            "semantic normalization requires a platform adapter"
        )

    def explain(
        self,
        *,
        inventory: AdapterInventory,
        validation: AdapterValidation,
        result: AdapterResult,
    ) -> dict[str, object]:
        if (
            type(inventory) is not AdapterInventory
            or type(validation) is not AdapterValidation
            or type(result) is not AdapterResult
        ):
            raise AdapterError("adapter explanation inputs are invalid")
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.capability.adapter_version,
            "maturity": self.capability.maturity,
            "accepted": validation.accepted,
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
            "source_rows": result.source_rows,
        }
