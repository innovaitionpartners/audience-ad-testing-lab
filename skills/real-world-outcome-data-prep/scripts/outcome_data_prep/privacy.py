from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .container_safety import (
    ContainerInventory,
    ContainerSafetyError,
    InventoryMetadata,
    inspect_container,
)
from .common import canonical_json_bytes, sha256_bytes, sha256_json
from .source_snapshot import SourceSnapshot, stable_stat_identity


class PrivacyAdmissionError(ValueError):
    pass


_ISSUED_ADMISSIONS: dict[
    str,
    tuple[str, tuple[int, int, int, int, int]],
] = {}


@dataclass(frozen=True)
class PrivacyDecision:
    status: str
    observed_minimum_group_size: int | None
    blocked_categories: tuple[str, ...]


@dataclass(frozen=True)
class AdmittedSource:
    source_path: Path
    source_sha256: str
    byte_length: int
    source_name: str
    snapshot_sha256: str
    inventory_sha256: str
    pre_scan_sha256: str
    adapter_validation_sha256: str
    admission_sha256: str


@dataclass(frozen=True)
class AdapterAdmissionValidation:
    adapter_id: str
    adapter_version: str
    source_sha256: str
    inventory_sha256: str
    profile_sha256: str
    adapter_validation_sha256: str
    governance_sha256: str
    accepted: bool
    observed_minimum_group_size: int | None
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier_pattern = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
        if (
            not isinstance(self.adapter_id, str)
            or not re.fullmatch(identifier_pattern, self.adapter_id)
            or not isinstance(self.adapter_version, str)
            or not re.fullmatch(identifier_pattern, self.adapter_version)
        ):
            raise PrivacyAdmissionError(
                "adapter privacy validation identity is invalid"
            )
        digest_pattern = r"sha256:[0-9a-f]{64}"
        digests = (
            self.source_sha256,
            self.inventory_sha256,
            self.profile_sha256,
            self.adapter_validation_sha256,
            self.governance_sha256,
        )
        if any(
            not isinstance(value, str)
            or not re.fullmatch(digest_pattern, value)
            for value in digests
        ):
            raise PrivacyAdmissionError(
                "adapter privacy validation binding is invalid"
            )
        if not isinstance(self.accepted, bool):
            raise PrivacyAdmissionError(
                "adapter privacy validation decision is invalid"
            )
        if (
            self.observed_minimum_group_size is not None
            and (
                isinstance(self.observed_minimum_group_size, bool)
                or not isinstance(self.observed_minimum_group_size, int)
                or self.observed_minimum_group_size < 0
            )
        ):
            raise PrivacyAdmissionError(
                "adapter privacy validation group size is invalid"
            )
        if (
            not isinstance(self.errors, tuple)
            or any(
                not isinstance(error, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", error)
                for error in self.errors
            )
        ):
            raise PrivacyAdmissionError(
                "adapter privacy validation errors are invalid"
            )
        if self.accepted and (
            self.errors or self.observed_minimum_group_size is None
        ):
            raise PrivacyAdmissionError(
                "accepted adapter privacy validation is incomplete"
            )
        if not self.accepted and not self.errors:
            raise PrivacyAdmissionError(
                "rejected adapter privacy validation must explain its category"
            )


OBVIOUS_PERSON_LEVEL_HEADERS = {
    "name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "address",
    "ip_address",
    "device_id",
    "cookie_id",
    "user_id",
    "person_id",
    "lead_id",
    "contact_id",
    "respondent_id",
}
SECRET_HEADERS = frozenset(
    {
        "api_key",
        "api_secret",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "client_secret",
        "private_key",
        "secret_key",
        "password",
        "passwd",
    }
)
VALUE_PATTERNS = {
    "email": re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "url_query": re.compile(r"(?i)https?://\S+\?\S+"),
}
SECRET_VALUE_PATTERNS = {
    "private_key": re.compile(
        r"(?i)-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    ),
    "cloud_credential": re.compile(
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
        r"\bAIza[0-9A-Za-z_-]{35}\b"
    ),
    "access_token": re.compile(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b|"
        r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b|"
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        r"\.[A-Za-z0-9_-]{10,}\b"
    ),
}
PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
DEVICE_PATTERN = re.compile(r"(?i)\b[0-9a-f]{32,64}\b")
PHONE_HEADERS = frozenset({"phone", "phone_number", "contact_phone"})
DEVICE_HEADERS = frozenset({"device_id", "cookie_id", "advertising_id"})


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def value_pattern_categories(header: str, value: str) -> set[str]:
    categories = {
        category
        for category, pattern in VALUE_PATTERNS.items()
        if pattern.search(value)
    }
    categories.update(
        category
        for category, pattern in SECRET_VALUE_PATTERNS.items()
        if pattern.search(value)
    )
    if header in PHONE_HEADERS and PHONE_PATTERN.search(value):
        categories.add("phone")
    if header in DEVICE_HEADERS and DEVICE_PATTERN.search(value):
        categories.add("device_token")
    return categories


def _nested_json_categories(value: str) -> set[str]:
    if not value.startswith(("{", "[")):
        return set()
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        return set()
    blocked: set[str] = set()

    def visit(item: object, inherited_header: str = "") -> None:
        if isinstance(item, dict):
            for raw_key, nested in item.items():
                header = normalize_header(raw_key)
                if header in OBVIOUS_PERSON_LEVEL_HEADERS:
                    blocked.add("person_level_identifier")
                if header in SECRET_HEADERS:
                    blocked.add("secret_header")
                visit(nested, header)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, inherited_header)
        elif item is not None:
            blocked.update(value_pattern_categories(inherited_header, str(item)))

    visit(parsed)
    return blocked


def _name_privacy_categories(value: object, *, label: str) -> set[str]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PrivacyAdmissionError(f"{label} is invalid")
    blocked = value_pattern_categories("", value)
    normalized = normalize_header(value)
    if normalized in OBVIOUS_PERSON_LEVEL_HEADERS:
        blocked.add("person_level_identifier")
    if normalized in SECRET_HEADERS:
        blocked.add("secret_header")
    return blocked


def pre_scan_obvious_privacy(
    inventory: ContainerInventory,
    *,
    source_name: str | None = None,
) -> PrivacyDecision:
    if not isinstance(inventory, ContainerInventory):
        raise PrivacyAdmissionError("container inventory is invalid")
    blocked: set[str] = set()
    if source_name is not None:
        if (
            not isinstance(source_name, str)
            or not source_name
            or Path(source_name).name != source_name
        ):
            raise PrivacyAdmissionError("source basename is invalid")
        blocked.update(
            _name_privacy_categories(source_name, label="source basename")
        )
    if not isinstance(inventory.tables, tuple):
        raise PrivacyAdmissionError("container table inventory is invalid")
    for table in inventory.tables:
        blocked.update(
            _name_privacy_categories(table, label="container table name")
        )
    for table_headers in inventory.headers:
        for raw_header in table_headers:
            header = normalize_header(raw_header)
            if header in OBVIOUS_PERSON_LEVEL_HEADERS:
                blocked.add("person_level_identifier")
            if header in SECRET_HEADERS:
                blocked.add("secret_header")
    for cell in inventory.cells:
        header = normalize_header(cell.column_name)
        if header in OBVIOUS_PERSON_LEVEL_HEADERS:
            blocked.add("person_level_identifier")
        if header in SECRET_HEADERS:
            blocked.add("secret_header")
        blocked.update(value_pattern_categories(header, cell.value))
        blocked.update(_nested_json_categories(cell.value))
    if not isinstance(inventory.raw_values, tuple):
        raise PrivacyAdmissionError("raw worksheet value inventory is invalid")
    for item in inventory.raw_values:
        if (
            type(item) is not InventoryMetadata
            or not all(
                isinstance(value, str)
                for value in (item.source, item.name, item.value)
            )
        ):
            raise PrivacyAdmissionError(
                "raw worksheet value inventory is invalid"
            )
        normalized = normalize_header(item.value)
        if normalized in OBVIOUS_PERSON_LEVEL_HEADERS:
            blocked.add("person_level_identifier")
        if normalized in SECRET_HEADERS:
            blocked.add("secret_header")
        context = normalize_header(item.name)
        blocked.update(value_pattern_categories(context, item.value))
        blocked.update(_nested_json_categories(item.value))
    if not isinstance(inventory.logical_values, tuple):
        raise PrivacyAdmissionError("logical shared string inventory is invalid")
    for item in inventory.logical_values:
        if (
            type(item) is not InventoryMetadata
            or not all(
                isinstance(value, str)
                for value in (item.source, item.name, item.value)
            )
        ):
            raise PrivacyAdmissionError(
                "logical shared string inventory is invalid"
            )
        normalized = normalize_header(item.value)
        if normalized in OBVIOUS_PERSON_LEVEL_HEADERS:
            blocked.add("person_level_identifier")
        if normalized in SECRET_HEADERS:
            blocked.add("secret_header")
        context = normalize_header(item.name)
        blocked.update(value_pattern_categories(context, item.value))
        blocked.update(_nested_json_categories(item.value))
    if not isinstance(inventory.metadata, tuple):
        raise PrivacyAdmissionError("container metadata inventory is invalid")
    for item in inventory.metadata:
        if (
            type(item) is not InventoryMetadata
            or not all(
                isinstance(value, str)
                for value in (item.source, item.name, item.value)
            )
        ):
            raise PrivacyAdmissionError("container metadata inventory is invalid")
        context = normalize_header(item.name)
        schema_name_inventory = item.name.endswith(
            (
                ":namespace_prefix",
                ":namespace_uri",
                ":qualified_attribute_name",
                ":qualified_element_name",
            )
        )
        for field, raw_value in (
            ("source", item.source),
            ("name", item.name),
            ("value", item.value),
        ):
            normalized = normalize_header(raw_value)
            semantic_names = {normalized}
            if (
                field == "value"
                and item.name.endswith(
                    (
                        ":qualified_attribute_name",
                        ":qualified_element_name",
                    )
                )
                and raw_value.startswith("{")
                and "}" in raw_value
            ):
                semantic_names.add(
                    normalize_header(raw_value.rsplit("}", 1)[-1])
                )
            for semantic_name in semantic_names:
                # `name` is an ordinary OOXML schema name, not a data value.
                # Other person/secret-like schema names remain fail-closed.
                if schema_name_inventory and semantic_name == "name":
                    continue
                if semantic_name in OBVIOUS_PERSON_LEVEL_HEADERS:
                    blocked.add("person_level_identifier")
                if semantic_name in SECRET_HEADERS:
                    blocked.add("secret_header")
            blocked.update(value_pattern_categories(context, raw_value))
            blocked.update(_nested_json_categories(raw_value))
    return PrivacyDecision(
        status="blocked_person_level" if blocked else "pre_scan_clear",
        observed_minimum_group_size=None,
        blocked_categories=tuple(sorted(blocked)),
    )


def container_inventory_sha256(inventory: ContainerInventory) -> str:
    if not isinstance(inventory, ContainerInventory):
        raise PrivacyAdmissionError("container inventory is invalid")
    document: dict[str, object] = {
        "media_type": inventory.media_type,
        "tables": list(inventory.tables),
        "headers": [list(headers) for headers in inventory.headers],
        "cells": [
            {
                "table": cell.table,
                "row_number": cell.row_number,
                "column_name": cell.column_name,
                "value": cell.value,
            }
            for cell in inventory.cells
        ],
        "row_count": inventory.row_count,
    }
    if inventory.metadata:
        document["metadata"] = [
            {
                "source": item.source,
                "name": item.name,
                "value": item.value,
            }
            for item in inventory.metadata
        ]
    if inventory.raw_values:
        document["raw_values"] = [
            {
                "source": item.source,
                "name": item.name,
                "value": item.value,
            }
            for item in inventory.raw_values
        ]
    if inventory.logical_values:
        document["logical_values"] = [
            {
                "source": item.source,
                "name": item.name,
                "value": item.value,
            }
            for item in inventory.logical_values
        ]
    return sha256_bytes(canonical_json_bytes(document))


def privacy_decision_sha256(decision: PrivacyDecision) -> str:
    if type(decision) is not PrivacyDecision:
        raise PrivacyAdmissionError("privacy decision is invalid")
    return sha256_json(asdict(decision))


def adapter_admission_validation_sha256(
    validation: AdapterAdmissionValidation,
) -> str:
    if type(validation) is not AdapterAdmissionValidation:
        raise PrivacyAdmissionError(
            "adapter privacy validation is invalid"
        )
    return sha256_json(asdict(validation))


def source_snapshot_sha256(snapshot: SourceSnapshot) -> str:
    if type(snapshot) is not SourceSnapshot:
        raise PrivacyAdmissionError("source snapshot is invalid")
    return sha256_json(
        {
            "original_path": str(snapshot.original_path),
            "staged_path": str(snapshot.staged_path),
            "byte_length": snapshot.byte_length,
            "source_sha256": snapshot.source_sha256,
            "media_type": snapshot.media_type,
            "stat_identity": list(snapshot.stat_identity),
        }
    )


def _admitted_source_document(
    admitted: AdmittedSource,
    *,
    include_hash: bool,
) -> dict[str, object]:
    document: dict[str, object] = {
        "source_path": str(admitted.source_path),
        "source_sha256": admitted.source_sha256,
        "byte_length": admitted.byte_length,
        "source_name": admitted.source_name,
        "snapshot_sha256": admitted.snapshot_sha256,
        "inventory_sha256": admitted.inventory_sha256,
        "pre_scan_sha256": admitted.pre_scan_sha256,
        "adapter_validation_sha256": (
            admitted.adapter_validation_sha256
        ),
    }
    if include_hash:
        document["admission_sha256"] = admitted.admission_sha256
    return document


def _admitted_source_sha256(admitted: AdmittedSource) -> str:
    return sha256_json(
        _admitted_source_document(admitted, include_hash=False)
    )


def _adapter_validation_matches(
    adapter_validation: object,
    *,
    snapshot: SourceSnapshot,
    inventory: ContainerInventory,
) -> bool:
    return (
        type(adapter_validation) is AdapterAdmissionValidation
        and adapter_validation.accepted
        and not adapter_validation.errors
        and adapter_validation.observed_minimum_group_size is not None
        and adapter_validation.source_sha256 == snapshot.source_sha256
        and adapter_validation.inventory_sha256
        == container_inventory_sha256(inventory)
    )


def admit_source(
    snapshot: SourceSnapshot,
    inventory: ContainerInventory,
    pre_scan: PrivacyDecision,
    adapter_validation: object,
    destination: Path,
) -> AdmittedSource:
    if not isinstance(snapshot, SourceSnapshot) or not isinstance(
        inventory, ContainerInventory
    ):
        raise PrivacyAdmissionError("source admission inputs are invalid")
    expected_pre_scan = pre_scan_obvious_privacy(
        inventory, source_name=snapshot.original_path.name
    )
    if (
        not isinstance(pre_scan, PrivacyDecision)
        or pre_scan != expected_pre_scan
        or pre_scan.status != "pre_scan_clear"
        or pre_scan.blocked_categories
    ):
        raise PrivacyAdmissionError("source did not pass the privacy pre-scan")
    if not _adapter_validation_matches(
        adapter_validation,
        snapshot=snapshot,
        inventory=inventory,
    ):
        raise PrivacyAdmissionError("source did not pass adapter privacy validation")

    destination_path = Path(destination)
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = destination_path.parent.lstat()
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
            parent_stat.st_mode
        ):
            raise PrivacyAdmissionError(
                "durable destination parent must be a directory"
            )
        source_stat = snapshot.staged_path.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(
            source_stat.st_mode
        ):
            raise PrivacyAdmissionError("staged source is unavailable")
        source_descriptor = os.open(
            snapshot.staged_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        destination_descriptor = os.open(
            destination_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except PrivacyAdmissionError:
        if source_descriptor is not None:
            os.close(source_descriptor)
        raise
    except OSError as exc:
        if source_descriptor is not None:
            os.close(source_descriptor)
        raise PrivacyAdmissionError("durable source could not be created") from exc

    admitted = False
    try:
        assert source_descriptor is not None
        assert destination_descriptor is not None
        opened = os.fstat(source_descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            source_stat.st_ctime_ns,
        ):
            raise PrivacyAdmissionError("staged source identity changed")
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(source_descriptor, 1_048_576)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination_descriptor, remaining)
                if written <= 0:
                    raise PrivacyAdmissionError("durable source write failed")
                remaining = remaining[written:]
        source_after = os.fstat(source_descriptor)
        source_sha256 = "sha256:" + digest.hexdigest()
        if (
            opened.st_size != source_after.st_size
            or opened.st_mtime_ns != source_after.st_mtime_ns
            or opened.st_ctime_ns != source_after.st_ctime_ns
            or length != snapshot.byte_length
            or source_sha256 != snapshot.source_sha256
        ):
            raise PrivacyAdmissionError("staged source no longer matches snapshot")
        os.fsync(destination_descriptor)
        admitted = True
        draft = AdmittedSource(
            source_path=destination_path.resolve(),
            source_sha256=source_sha256,
            byte_length=length,
            source_name=snapshot.original_path.name,
            snapshot_sha256=source_snapshot_sha256(snapshot),
            inventory_sha256=container_inventory_sha256(inventory),
            pre_scan_sha256=privacy_decision_sha256(pre_scan),
            adapter_validation_sha256=(
                adapter_admission_validation_sha256(adapter_validation)
            ),
            admission_sha256="sha256:" + ("0" * 64),
        )
        result = AdmittedSource(
            **{
                **_admitted_source_document(draft, include_hash=False),
                "source_path": draft.source_path,
                "admission_sha256": _admitted_source_sha256(draft),
            }
        )
        durable_stat = os.fstat(destination_descriptor)
        _ISSUED_ADMISSIONS[result.admission_sha256] = (
            str(result.source_path),
            stable_stat_identity(durable_stat),
        )
        return result
    except PrivacyAdmissionError:
        raise
    except OSError as exc:
        raise PrivacyAdmissionError("durable source copy failed") from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if not admitted:
            try:
                destination_path.unlink(missing_ok=True)
            except OSError as exc:
                raise PrivacyAdmissionError(
                    "failed durable source cleanup"
                ) from exc


def authenticate_admitted_source(
    admitted: object,
    inventory: ContainerInventory,
    pre_scan: PrivacyDecision,
    adapter_validation: AdapterAdmissionValidation,
) -> AdmittedSource:
    if type(admitted) is not AdmittedSource:
        raise PrivacyAdmissionError(
            "durable source admission receipt is required"
        )
    digest_pattern = r"sha256:[0-9a-f]{64}"
    if (
        not isinstance(admitted.source_path, Path)
        or not isinstance(admitted.source_name, str)
        or not admitted.source_name
        or Path(admitted.source_name).name != admitted.source_name
        or Path(admitted.source_name).suffix.lower()
        not in {".csv", ".tsv", ".json", ".xlsx", ".zip"}
        or isinstance(admitted.byte_length, bool)
        or not isinstance(admitted.byte_length, int)
        or admitted.byte_length < 0
        or any(
            not isinstance(value, str)
            or not re.fullmatch(digest_pattern, value)
            for value in (
                admitted.source_sha256,
                admitted.snapshot_sha256,
                admitted.inventory_sha256,
                admitted.pre_scan_sha256,
                admitted.adapter_validation_sha256,
                admitted.admission_sha256,
            )
        )
    ):
        raise PrivacyAdmissionError(
            "durable source admission receipt is invalid"
        )
    expected_pre_scan = pre_scan_obvious_privacy(
        inventory, source_name=admitted.source_name
    )
    inventory_sha256 = container_inventory_sha256(inventory)
    validation_sha256 = adapter_admission_validation_sha256(
        adapter_validation
    )
    if (
        admitted.source_sha256 != adapter_validation.source_sha256
        or admitted.inventory_sha256 != inventory_sha256
        or admitted.inventory_sha256
        != adapter_validation.inventory_sha256
        or pre_scan != expected_pre_scan
        or admitted.pre_scan_sha256
        != privacy_decision_sha256(expected_pre_scan)
        or admitted.adapter_validation_sha256 != validation_sha256
        or admitted.admission_sha256
        != _admitted_source_sha256(admitted)
    ):
        raise PrivacyAdmissionError(
            "durable source admission chain does not match"
        )
    try:
        source_stat = admitted.source_path.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(
            source_stat.st_mode
        ):
            raise PrivacyAdmissionError(
                "durable admitted source is unavailable"
            )
        if _ISSUED_ADMISSIONS.get(admitted.admission_sha256) != (
            str(admitted.source_path),
            stable_stat_identity(source_stat),
        ):
            raise PrivacyAdmissionError(
                "durable source was not issued by admission"
            )
        durable_snapshot = SourceSnapshot(
            original_path=Path(admitted.source_name),
            staged_path=admitted.source_path,
            byte_length=admitted.byte_length,
            source_sha256=admitted.source_sha256,
            media_type="application/octet-stream",
            stat_identity=stable_stat_identity(source_stat),
        )
        durable_inventory = inspect_container(durable_snapshot)
    except (OSError, ContainerSafetyError) as exc:
        raise PrivacyAdmissionError(
            "durable admitted source could not be authenticated"
        ) from exc
    if durable_inventory != inventory:
        raise PrivacyAdmissionError(
            "durable admitted source inventory does not match"
        )
    return admitted
