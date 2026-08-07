"""Public Task 11 workflow interfaces for outcome-data preparation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from audience_panel_builder.population.validation.contracts import (
    read_protected_authority_secret,
)

from .adapters.amazon_dsp import AmazonDSPAdapter
from .adapters.dv360 import DV360Adapter
from .adapters.generic_programmatic import (
    GenericAdmissionProfile,
    GenericProgrammaticAdapter,
)
from .adapters.google_ads import GoogleAdsAdapter
from .adapters.linkedin import LinkedInAdsAdapter
from .adapters.meta import MetaInsightsAdapter
from .adapters.tiktok import TikTokAdsAdapter
from .adapters.trade_desk import TradeDeskAdapter
from .adapters.xandr import XandrAdapter
from .capabilities import (
    AdapterCapability,
    load_capability_registry,
    resolve_adapter,
)
from .common import (
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from .container_safety import ContainerInventory, inspect_container
from .contracts import (
    CORRECTION_REQUEST_VERSION,
    IMPORT_EVENT_VERSION,
    READINESS_VERSION,
    SOURCE_GOVERNANCE_RECORD_VERSION,
    SOURCE_MANIFEST_VERSION,
    validate_import_event,
    validate_correction_request,
    validate_readiness_report,
    validate_source_governance_input,
    validate_source_governance_record,
    validate_source_manifest,
)
from .matching import match_normalized_rows
from .normalization import (
    authenticate_effective_evidence_status,
    authenticate_normalized_batch,
    effective_evidence_status_snapshot,
)
from .privacy import (
    admit_source,
    container_inventory_sha256,
    pre_scan_obvious_privacy,
)
from .publication import (
    ANALYTICAL_IDENTITY_VERSION,
    GENERATION_VERSION,
    ImportConflict,
    StudyState,
    commit_import_generation,
    recover_study,
    replay_authenticated_ledger,
    validate_complete_staged_generation,
)
from .reporting import render_matching_report, render_readiness_report
from .runtime_guard import require_approved_runtime
from .source_snapshot import snapshot_source
from .study_authority import (
    IMPORT_EVENT_DOMAIN,
    authenticate_study_receipt,
    authority_hmac,
    import_event_authority_projection,
)
from .validation_handoff import (
    build_validation_observation,
    validate_validation_handoff,
    validate_validation_handoff_document,
)


class ImportSafetyError(ContractError):
    """One or more source bytes could not be admitted safely."""

    def __init__(
        self,
        message: str,
        *,
        rejection_receipts: tuple[Path, ...] = (),
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.rejection_receipts = rejection_receipts
        self.reason_codes = reason_codes


@dataclass(frozen=True)
class SourceInput:
    path: Path
    governance_input: dict[str, object]
    adapter_context: dict[str, object] | None = None


@dataclass(frozen=True)
class ImportRequest:
    study_root: Path
    sources: tuple[SourceInput, ...]
    authority_registry: Path
    authority_secret_file: Path
    imported_at: str
    import_id: str


@dataclass(frozen=True)
class CorrectionInput:
    correction_id: str
    requested_at: str
    actor: str
    reason_code: str
    reason: str
    supersedes_import_id: str
    supersedes_observation_ids: tuple[str, ...]
    expected_analytical_identity_sha256: str | None = None


@dataclass(frozen=True)
class ImportWorkflowResult:
    import_id: str
    import_digest: str
    generation_path: Path
    ledger_digest: str
    analytical_identity_sha256: str
    evidence_status: str
    operational_status: str
    readiness_report_json: Path
    readiness_report_markdown: Path
    matching_report: Path
    validation_handoff_written: bool
    source_count: int
    matched_row_count: int
    quarantined_row_count: int


@dataclass(frozen=True)
class _AdmittedRun:
    source_id: str
    source: SourceInput
    inventory: ContainerInventory
    capability: AdapterCapability
    admission: object
    admitted: object
    governance: dict[str, object]
    governance_record: dict[str, object]
    profile: GenericAdmissionProfile | None
    normalization_context: dict[str, object] | None
    expected_observation_ids: tuple[str, ...]


_ADAPTER_TYPES = {
    "meta-insights-api-json-v1": MetaInsightsAdapter,
    "google-ads-api-v23-ad-daily-json": GoogleAdsAdapter,
    "linkedin-ads-reporting-api-json-v1": LinkedInAdsAdapter,
    "tiktok-reporting-api-json-v1": TikTokAdsAdapter,
    "dv360-bid-manager-v2-standard-csv-v1": DV360Adapter,
    "dv360-bid-manager-v2-standard-xlsx-v1": DV360Adapter,
    "trade-desk-report-template-csv-v1": TradeDeskAdapter,
    "trade-desk-report-template-tsv-v1": TradeDeskAdapter,
    "trade-desk-report-type-xlsx-v1": TradeDeskAdapter,
    "xandr-advertiser-analytics-csv-v1": XandrAdapter,
    "xandr-advertiser-analytics-excel-tsv-v1": XandrAdapter,
    "xandr-advertiser-analytics-xlsx-v1": XandrAdapter,
    "generic-dsp-mapping-v1": GenericProgrammaticAdapter,
    "amazon-unified-reporting-ui-csv-v1": AmazonDSPAdapter,
    "amazon-unified-reporting-ui-xlsx-v1": AmazonDSPAdapter,
    "amazon-unified-reporting-api-json-v1": AmazonDSPAdapter,
}

_IDENTITY_ROW_KEYS = {
    "registration_id", "registration_sha256", "delivery_map_sha256",
    "delivery_mapping_id", "delivery_mapping_sha256",
    "campaign_plan_sha256", "platform", "platform_campaign_id",
    "platform_ad_group_id", "platform_ad_id", "platform_creative_id",
    "block_id", "study_id", "arm_id", "batch_id", "segment_ids",
    "creative_id", "variant_id", "asset_sha256", "panel_sha256",
    "package_sha256", "run_id", "result_sha256", "metric_id",
    "measurement_window", "attribution_window",
}

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REJECTION_RECEIPT_DOMAIN = (
    b"audience-ad-testing-lab/outcome-source-rejection/v1\x00"
)


def _private_directory_identity(
    info: os.stat_result, label: str
) -> tuple[int, int, int, int]:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ImportConflict(f"{label} has an unsafe identity")
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
    )


class _RetainedStudyRoot:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.descriptor = -1
        self.identity: tuple[int, int, int, int] | None = None

    @staticmethod
    def _flags() -> int:
        return (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )

    def __enter__(self) -> "_RetainedStudyRoot":
        try:
            self.descriptor = os.open(self.path, self._flags())
            self.identity = _private_directory_identity(
                os.fstat(self.descriptor), "study root"
            )
            self.verify_live_path()
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *args: object) -> None:
        del args
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def verify_live_path(self) -> None:
        if self.descriptor < 0 or self.identity is None:
            raise ImportConflict("study root descriptor is not retained")
        try:
            descriptor_identity = _private_directory_identity(
                os.fstat(self.descriptor), "retained study root"
            )
            path_identity = _private_directory_identity(
                self.path.lstat(), "live study root path"
            )
        except OSError as exc:
            raise ImportConflict("study root path identity changed") from exc
        if (
            descriptor_identity != self.identity
            or path_identity != self.identity
        ):
            raise ImportConflict("study root path identity changed")


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be a timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must be timezone-aware")
    return parsed


def _closed_context(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "adapter_registration", "reporting_metadata", "source_binding"
    }:
        raise ContractError(
            "named exact adapters require one closed source context"
        )
    registration = value["adapter_registration"]
    metadata = value["reporting_metadata"]
    if not isinstance(registration, Mapping) or not isinstance(metadata, Mapping):
        raise ContractError("source context sections must be objects")
    return {
        "adapter_registration": deepcopy(dict(registration)),
        "reporting_metadata": deepcopy(dict(metadata)),
        "source_binding": _source_binding(value["source_binding"]),
    }


def _generic_context(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "adapter_id", "mapping", "reporting_metadata",
        "delivery_map_sha256", "source_binding",
    }:
        raise ContractError(
            "generic sources require one closed explicit mapping context"
        )
    if value["adapter_id"] != "generic-dsp-mapping-v1":
        raise ContractError("generic adapter_id is not approved")
    mapping = value["mapping"]
    metadata = value["reporting_metadata"]
    if not isinstance(mapping, Mapping) or not isinstance(metadata, Mapping):
        raise ContractError("generic mapping context sections must be objects")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in mapping.items()
    ):
        raise ContractError("generic mapping must contain only strings")
    digest = value["delivery_map_sha256"]
    if not isinstance(digest, str):
        raise ContractError("generic delivery-map binding is invalid")
    return {
        "adapter_id": value["adapter_id"],
        "mapping": deepcopy(dict(mapping)),
        "reporting_metadata": deepcopy(dict(metadata)),
        "delivery_map_sha256": digest,
        "source_binding": _source_binding(value["source_binding"]),
    }


def _source_binding(value: object) -> dict[str, str]:
    keys = {
        "source_sha256", "inventory_sha256", "study_id",
        "delivery_map_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ContractError("source binding is not one closed exact contract")
    for key in ("source_sha256", "inventory_sha256", "delivery_map_sha256"):
        digest = value[key]
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise ContractError("source binding digest is invalid")
    study_id = value["study_id"]
    if not isinstance(study_id, str) or not study_id:
        raise ContractError("source binding study identity is invalid")
    return {key: str(value[key]) for key in keys}


def _authenticate_source_context(
    *,
    context: Mapping[str, object],
    snapshot: object,
    inventory: ContainerInventory,
    study: object,
) -> None:
    binding = context["source_binding"]
    assert isinstance(binding, Mapping)
    expected = {
        "source_sha256": snapshot.source_sha256,
        "inventory_sha256": container_inventory_sha256(inventory),
        "study_id": study.delivery_map["study_id"],
        "delivery_map_sha256": study.delivery_map["delivery_map_sha256"],
    }
    if binding != expected:
        raise ContractError(
            "source context does not bind the exact source, inventory, and study"
        )


def _row_references(inventory: ContainerInventory) -> tuple[str, ...]:
    order = {table: index for index, table in enumerate(inventory.tables)}
    rows = sorted(
        {(cell.table, cell.row_number) for cell in inventory.cells},
        key=lambda item: (order.get(item[0], len(order)), item[1]),
    )
    if len(rows) != inventory.row_count:
        raise ContractError("source row inventory is incomplete")
    return tuple(f"{table}:{number}" for table, number in rows)


def _physical_rows(
    inventory: ContainerInventory,
) -> list[tuple[str, dict[str, str]]]:
    table_order = {table: index for index, table in enumerate(inventory.tables)}
    values: dict[tuple[str, int], dict[str, str]] = {}
    for cell in inventory.cells:
        row = values.setdefault((cell.table, cell.row_number), {})
        if cell.column_name in row:
            raise ContractError("source inventory contains duplicate cells")
        row[cell.column_name] = cell.value
    rows = [
        (f"{table}:{row_number}", row)
        for (table, row_number), row in sorted(
            values.items(),
            key=lambda item: (
                table_order.get(item[0][0], len(table_order)), item[0][1]
            ),
        )
    ]
    if len(rows) != inventory.row_count:
        raise ContractError("source row inventory is incomplete")
    return rows


def _observation_ids(
    *,
    inventory: ContainerInventory,
    capability: AdapterCapability,
    source_sha256: str,
    metric_id: str,
) -> tuple[str, ...]:
    return tuple(sorted(
        "observation-" + sha256_json({
            "adapter_id": capability.adapter_id,
            "source_sha256": source_sha256,
            "source_row_reference": reference,
            "metric_id": metric_id,
        }).removeprefix("sha256:")
        for reference in _row_references(inventory)
    ))


def _generic_admission_inputs(
    *,
    context: Mapping[str, object],
    inventory: ContainerInventory,
    source_sha256: str,
    source_id: str,
    import_id: str,
    export_timestamp: str,
    study: object,
) -> tuple[dict[str, object], dict[str, object]]:
    checked = _generic_context(context)
    delivery_digest = study.delivery_map["delivery_map_sha256"]
    if checked["delivery_map_sha256"] != delivery_digest:
        raise ContractError(
            "generic context does not bind the authenticated delivery map"
        )
    mapping = checked["mapping"]
    metadata = checked["reporting_metadata"]
    assert isinstance(mapping, dict) and isinstance(metadata, dict)
    conversion_sources = [
        source for source, target in mapping.items()
        if target == "conversion_value"
    ]
    currency_sources = [
        source for source, target in mapping.items() if target == "currency"
    ]
    if len(conversion_sources) != 1 or len(currency_sources) != 1:
        raise ContractError(
            "generic mapping requires one conversion and currency source"
        )
    physical = _physical_rows(inventory)
    currencies = {
        row.get(currency_sources[0]) for _reference, row in physical
    }
    if len(currencies) != 1 or None in currencies or "" in currencies:
        raise ContractError("generic source currency must be one exact value")
    metric = study.registration["primary_metric"]
    assert isinstance(metric, Mapping)
    expected_metadata = {
        "source_container": metadata.get("source_container"),
        "source_platform": "generic_dsp",
        "headers": metadata.get("headers"),
        "header_fingerprint": metadata.get("header_fingerprint"),
        "mapping_profile_id": metadata.get("mapping_profile_id"),
        "stable_id_targets": metadata.get("stable_id_targets"),
        "timezone": "UTC",
        "time_basis": "authenticated_source_reporting_day",
        "currency": next(iter(currencies)),
        "attribution_semantics": "registered_attribution_window",
        "attribution_windows": [metric["attribution_window"]],
        "conversion_metric": conversion_sources[0],
        "admitted_null_tokens": [],
        "null_value_state": "null",
        "aggregate_level": "already_aggregate",
        "currency_inferred": False,
        "currency_conversion": False,
        "cross_platform_reach_deduplication": False,
        "reconstructed_attribution": False,
        "platform_proof_basis": "declared_not_filename",
        "mixed_time_bases": False,
        "automatic_adapter_promotion": False,
        "conversion_value_state": "observed",
        "latency_state": "mature",
        "observed_at": export_timestamp,
        "omitted_zero_behavior": (
            "omitted_metrics_are_unknown_not_zero"
        ),
    }
    if metadata != expected_metadata:
        raise ContractError(
            "generic reporting metadata is not the exact approved context"
        )
    registration = {
        "study_id": study.delivery_map["study_id"],
        "registration_id": study.registration["registration_id"],
        "metric_id": metric["name"],
        "registered_source_metric": conversion_sources[0],
        "outcomes_accessed": True,
        "sealed_delivery_map": deepcopy(study.delivery_map),
        "approved_mapping": deepcopy(mapping),
        "approved_mapping_profile_id": metadata["mapping_profile_id"],
        "approved_header_fingerprint": metadata["header_fingerprint"],
        "approved_source_container": metadata["source_container"],
        "time_basis": metadata["time_basis"],
        "currency": metadata["currency"],
        "attribution_semantics": metadata["attribution_semantics"],
        "attribution_windows": deepcopy(metadata["attribution_windows"]),
    }
    payload = {
        "source_id": source_id,
        "import_id": import_id,
        "source_sha256": source_sha256,
        "mapping": deepcopy(mapping),
        "reporting_metadata": deepcopy(metadata),
        "rows": [
            {
                "source_row_reference": reference,
                "values": deepcopy(row),
            }
            for reference, row in physical
        ],
    }
    return registration, payload


def _event_envelope(
    *, study: object, import_id: str, imported_at: str,
    imported_by: str, source_manifest_sha256: str,
    observation_ids: tuple[str, ...], authority_secret: bytes,
) -> dict[str, object]:
    event = validate_import_event({
        "schema_version": IMPORT_EVENT_VERSION,
        "import_id": import_id,
        "study_id": study.delivery_map["study_id"],
        "imported_at": imported_at,
        "imported_by": imported_by,
        "source_manifest_sha256": source_manifest_sha256,
        "observation_ids": list(observation_ids),
        "import_event_sha256": None,
    })
    return {
        "event": event,
        "event_hmac_sha256": authority_hmac(
            domain=IMPORT_EVENT_DOMAIN,
            payload=import_event_authority_projection(
                event,
                registration_id=str(study.registration["registration_id"]),
                receipt_sha256=str(
                    study.registration_receipt["receipt_sha256"]
                ),
            ),
            secret=authority_secret,
        ),
    }


def _write_file(
    root: Path, relative: str, raw: bytes, role: str,
    files: list[dict[str, object]], *, source_id: str | None = None,
) -> Path:
    path = root / relative
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    files.append({
        "relative_path": relative,
        "sha256": sha256_bytes(raw),
        "byte_count": len(raw),
        "role": role,
        "source_id": source_id,
    })
    return path


def _analytical_identity(
    matched: tuple[dict[str, object], ...],
) -> dict[str, object]:
    observations = sorted(
        [{
            key: deepcopy(item["delivery_binding"][key])
            for key in _IDENTITY_ROW_KEYS
        } for item in matched],
        key=canonical_json_bytes,
    )
    if not observations:
        raise ContractError("an import requires at least one matched row")
    document: dict[str, object] = {
        "schema_version": ANALYTICAL_IDENTITY_VERSION,
        "observations": observations,
        "analytical_identity_sha256": None,
    }
    document["analytical_identity_sha256"] = sha256_json(document)
    return document


def _correction_static_projection(row: Mapping[str, object]) -> dict[str, object]:
    reporting = deepcopy(dict(row["reporting"]))
    reporting.pop("observed_at", None)
    reporting.pop("latency_state", None)
    outcome = row["outcome"]
    spend = row["spend"]
    exposure = row["exposure"]
    projection = row["validation_projection"]
    assert (
        isinstance(outcome, Mapping)
        and isinstance(spend, Mapping)
        and isinstance(exposure, Mapping)
        and isinstance(projection, Mapping)
    )
    aggregate = projection.get("aggregate")
    aggregate_shape = (
        sorted(str(key) for key in aggregate)
        if isinstance(aggregate, Mapping) else None
    )
    return {
        "schema_version": row["schema_version"],
        "study_id": row["study_id"],
        "registration_id": row["registration_id"],
        "platform": row["platform"],
        "adapter": deepcopy(row["adapter"]),
        "account": deepcopy(row["account"]),
        "campaign": deepcopy(row["campaign"]),
        "ad_group": deepcopy(row["ad_group"]),
        "ad": deepcopy(row["ad"]),
        "creative": deepcopy(row["creative"]),
        "reporting": reporting,
        "attribution": deepcopy(row["attribution"]),
        "currency": deepcopy(row["currency"]),
        "spend_contract": {
            "source_metric": spend["source_metric"],
            "source_unit": spend["source_unit"],
        },
        "exposure_contract": {
            str(key): sorted(str(field) for field in value if field != "value")
            for key, value in exposure.items()
            if isinstance(value, Mapping)
        },
        "outcome_contract": {
            "metric_id": outcome["metric_id"],
            "source_metric": outcome["source_metric"],
            "omitted_zero_behavior": outcome["omitted_zero_behavior"],
        },
        "platform_semantics": deepcopy(row["platform_semantics"]),
        "validation_contract": {
            key: deepcopy(projection[key])
            for key in (
                "evidence_status", "metric_family", "measurement_window",
                "attribution_window", "assignment", "confidence_level",
                "permission_confirmed",
            )
        } | {"aggregate_shape": aggregate_shape},
    }


def _aggregate_handoff(
    handoffs: list[dict[str, object]],
) -> dict[str, object]:
    registration = handoffs[0]["registration_binding"]
    if any(item["registration_binding"] != registration for item in handoffs):
        raise ContractError("source handoffs do not share one registration")
    rows = sorted(
        [row for item in handoffs for row in item["normalized_observations"]],
        key=lambda row: str(row["observation_id"]),
    )
    bindings = {
        str(binding["observation_id"]): binding
        for item in handoffs for binding in item["observation_bindings"]
    }
    observations = sorted(
        [row for item in handoffs for row in item["validation_observations"]],
        key=lambda row: str(row["observation_id"]),
    )
    document = {
        "schema_version": "outcome-validation-handoff-v1",
        "registration_binding": deepcopy(registration),
        "normalized_observations": rows,
        "observation_bindings": [
            bindings[str(row["observation_id"])] for row in rows
        ],
        "validation_observations": observations,
        "handoff_sha256": None,
    }
    document["handoff_sha256"] = sha256_json(document)
    return validate_validation_handoff_document(document)


def _first_outcome_accessed_at(study: object, exports: list[str]) -> str:
    chronology = study.registration_receipt["chronology"]
    candidates = [
        str(event["occurred_at"])
        for event in chronology["events"]
        if event["event_type"] in {
            "outcome_access_started", "reported_outcome_access"
        }
    ]
    return min(candidates or exports, key=lambda value: _timestamp(value, "access"))


def _write_rejection_receipts(
    *,
    retained_root: _RetainedStudyRoot,
    study_id: str,
    import_id: str,
    authority_secret: bytes,
    rejected: list[tuple[int, object, tuple[str, ...], int]],
) -> tuple[Path, ...]:
    directory_name = "rejections"
    directory_path = retained_root.path / directory_name
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )

    root_fd = retained_root.descriptor
    child_fd = -1
    created_child = False
    try:
        retained_root.verify_live_path()
        root_identity = retained_root.identity
        if root_fd < 0 or root_identity is None:
            raise ImportConflict("authenticated study root is not retained")
        try:
            os.mkdir(directory_name, 0o700, dir_fd=root_fd)
            created_child = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise ImportConflict(
                "rejection directory cannot be created safely"
            ) from exc
        try:
            child_fd = os.open(directory_name, directory_flags, dir_fd=root_fd)
        except OSError as exc:
            raise ImportConflict("rejection directory is not a safe directory") from exc
        child_identity = _private_directory_identity(
            os.fstat(child_fd), "rejection directory"
        )
        if child_identity[0] != root_identity[0]:
            raise ImportConflict("rejection directory is on a different device")

        def verify_directories() -> None:
            retained_root.verify_live_path()
            if (
                _private_directory_identity(
                    os.fstat(root_fd), "study root"
                ) != root_identity
            ):
                raise ImportConflict("study root identity changed")
            try:
                child_path_info = os.stat(
                    directory_name, dir_fd=root_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise ImportConflict("rejection publication identity changed") from exc
            if (
                _private_directory_identity(
                    child_path_info, "rejection directory path"
                ) != child_identity
                or _private_directory_identity(
                    os.fstat(child_fd), "rejection directory"
                ) != child_identity
            ):
                raise ImportConflict("rejection directory identity changed")

        verify_directories()
        if created_child:
            os.fsync(root_fd)
        receipts: list[Path] = []
        for index, snapshot, reason_codes, row_count in rejected:
            if (
                not reason_codes
                or any(
                    not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
                    for code in reason_codes
                )
            ):
                raise ImportConflict("rejection reason codes are invalid")
            name = f"{import_id}-source-{index}.json"
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", name):
                raise ImportConflict("rejection receipt name is invalid")
            payload = {
                "schema_version": "outcome-source-rejection-v1",
                "study_id": study_id,
                "import_id": import_id,
                "source_id": f"source-{index}",
                "source_sha256": snapshot.source_sha256,
                "byte_count": snapshot.byte_length,
                "row_count": row_count,
                "status": "rejected",
                "reason_codes": list(sorted(set(reason_codes))),
            }
            document = {
                **payload,
                "receipt_hmac_sha256": authority_hmac(
                    domain=_REJECTION_RECEIPT_DOMAIN,
                    payload=payload,
                    secret=authority_secret,
                ),
                "rejection_sha256": None,
            }
            document["rejection_sha256"] = sha256_json(document)
            raw = canonical_json_bytes(document)
            verify_directories()
            descriptor = -1
            try:
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=child_fd,
                )
            except FileExistsError as exc:
                raise ImportConflict("rejection receipt already exists") from exc
            except OSError as exc:
                raise ImportConflict(
                    "rejection receipt cannot be created safely"
                ) from exc
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                    or info.st_dev != child_identity[0]
                ):
                    raise ImportConflict("rejection receipt has an unsafe identity")
                offset = 0
                while offset < len(raw):
                    written = os.write(descriptor, raw[offset:])
                    if written <= 0:
                        raise ImportConflict(
                            "rejection receipt write made no progress"
                        )
                    offset += written
                os.fsync(descriptor)
                path_info = os.stat(name, dir_fd=child_fd, follow_symlinks=False)
                if (
                    path_info.st_dev != info.st_dev
                    or path_info.st_ino != info.st_ino
                    or path_info.st_nlink != 1
                    or not stat.S_ISREG(path_info.st_mode)
                ):
                    raise ImportConflict("rejection receipt identity changed")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            verify_directories()
            os.fsync(child_fd)
            receipts.append(directory_path / name)
        return tuple(receipts)
    finally:
        if child_fd >= 0:
            os.close(child_fd)


def pair_source_arguments(
    sources: list[Path],
    governance: list[Path],
    contexts: list[Path],
) -> tuple[tuple[Path, Path, Path | None], ...]:
    """Pair repeatable CLI paths without reading any supplied file."""

    if not sources or len(sources) != len(governance):
        raise ValueError(
            "source and source-governance must be exact index pairs"
        )
    if contexts and len(contexts) != len(sources):
        raise ValueError(
            "source-context must form exact index pairs when supplied"
        )
    paired_contexts: list[Path | None] = (
        list(contexts) if contexts else [None] * len(sources)
    )
    return tuple(zip(sources, governance, paired_contexts, strict=True))


def validate_study(
    study_root: Path,
    *,
    authority_registry: Path,
    authority_secret_file: Path,
) -> StudyState:
    require_approved_runtime("validate_study")
    _study, authority = authenticate_study_receipt(
        study_root=study_root,
        authority_registry=authority_registry,
        authority_secret_file=authority_secret_file,
    )
    return replay_authenticated_ledger(study_root, authority=authority)


def recover_study_from_paths(
    study_root: Path,
    *,
    authority_registry: Path,
    authority_secret_file: Path,
) -> StudyState:
    require_approved_runtime("recover_study")
    _study, authority = authenticate_study_receipt(
        study_root=study_root,
        authority_registry=authority_registry,
        authority_secret_file=authority_secret_file,
    )
    return recover_study(study_root=study_root, authority=authority)


def import_results(
    request: ImportRequest,
    correction: CorrectionInput | None = None,
) -> ImportWorkflowResult:
    require_approved_runtime("import_results")
    if type(request) is not ImportRequest or not request.sources:
        raise ContractError("import request requires at least one source")
    with _RetainedStudyRoot(request.study_root) as retained_root:
        return _import_results_retained(
            request, correction, retained_root=retained_root
        )


def _import_results_retained(
    request: ImportRequest,
    correction: CorrectionInput | None,
    *,
    retained_root: _RetainedStudyRoot,
) -> ImportWorkflowResult:
    if correction is not None and (
        type(correction) is not CorrectionInput or len(request.sources) != 1
    ):
        raise ImportConflict("a correction requires exactly one source")
    _timestamp(request.imported_at, "imported_at")
    study, authority = authenticate_study_receipt(
        study_root=request.study_root,
        authority_registry=request.authority_registry,
        authority_secret_file=request.authority_secret_file,
    )
    retained_root.verify_live_path()
    effective_status_authority = authenticate_effective_evidence_status(
        authenticated_study=study,
        study_authority=authority,
    )
    (
        effective_evidence_status,
        effective_ledger_digest,
    ) = effective_evidence_status_snapshot(
        effective_status_authority,
        authenticated_study=study,
        study_authority=authority,
    )
    superseded_generation = None
    if correction is not None:
        superseded_generation = validate_complete_staged_generation(
            request.study_root
            / "imports"
            / correction.supersedes_import_id,
            authority=authority,
        )
        if (
            correction.expected_analytical_identity_sha256 is not None
            and correction.expected_analytical_identity_sha256
            != superseded_generation.analytical_identity_sha256
        ):
            raise ImportConflict(
                "correction expected analytical identity is mismatched"
            )
        prior_event_ids = superseded_generation.event.get("observation_ids")
        requested_ids = correction.supersedes_observation_ids
        if (
            not isinstance(prior_event_ids, list)
            or not prior_event_ids
            or any(not isinstance(item, str) or not item for item in prior_event_ids)
            or len(set(prior_event_ids)) != len(prior_event_ids)
            or len(set(requested_ids)) != len(requested_ids)
            or len(requested_ids) != len(prior_event_ids)
            or set(requested_ids) != set(prior_event_ids)
        ):
            raise ImportConflict(
                "correction must supersede every authenticated prior "
                "observation exactly once"
            )
    authority_secret = read_protected_authority_secret(
        request.authority_secret_file
    )
    registry = load_capability_registry(
        Path(__file__).resolve().parents[2]
        / "references"
        / "platform-capabilities.json"
    )
    metric = study.registration["primary_metric"]
    if not isinstance(metric, Mapping):
        raise ContractError("authenticated primary metric is invalid")
    metric_id = str(metric["name"])
    imported_by = str(study.registration["registered_by"])

    with tempfile.TemporaryDirectory(prefix="outcome-prep-") as temporary:
        temporary_root = Path(temporary)
        temporary_root.chmod(0o700)
        snapshots: list[
            tuple[
                SourceInput, object, ContainerInventory, object,
                dict[str, object], dict[str, object], bool,
            ]
        ] = []
        for source in request.sources:
            if type(source) is not SourceInput:
                raise ContractError("source input is invalid")
            snapshot = snapshot_source(
                source.path, staging_root=temporary_root / "snapshots"
            )
            inventory = inspect_container(snapshot)
            decision = pre_scan_obvious_privacy(
                inventory, source_name=snapshot.original_path.name
            )
            governance = validate_source_governance_input(
                source.governance_input
            )
            raw_context = source.adapter_context
            generic = (
                isinstance(raw_context, Mapping)
                and "adapter_id" in raw_context
            )
            context = (
                _generic_context(raw_context)
                if generic else _closed_context(raw_context)
            )
            _authenticate_source_context(
                context=context,
                snapshot=snapshot,
                inventory=inventory,
                study=study,
            )
            snapshots.append((
                source, snapshot, inventory, decision,
                governance, context, generic,
            ))

        source_pairs: dict[str, tuple[str, str]] = {}
        for (
            _source, snapshot, _inventory, _decision,
            governance, context, _generic,
        ) in snapshots:
            pair = (sha256_json(governance), sha256_json(context))
            prior = source_pairs.setdefault(snapshot.source_sha256, pair)
            if prior != pair:
                raise ContractError(
                    "duplicate source bytes have conflicting context or governance"
                )
        rejected = [
            (
                index, snapshot, tuple(decision.blocked_categories),
                inventory.row_count,
            )
            for index, (
                _source, snapshot, inventory, decision,
                _governance, _context, _generic,
            ) in enumerate(
                snapshots, start=1
            )
            if decision.status != "pre_scan_clear"
        ]
        if rejected:
            receipts = _write_rejection_receipts(
                retained_root=retained_root,
                study_id=str(study.delivery_map["study_id"]),
                import_id=request.import_id,
                authority_secret=authority_secret,
                rejected=rejected,
            )
            reason_codes = tuple(sorted({
                code
                for _index, _snapshot, codes, _row_count in rejected
                for code in codes
            }))
            raise ImportSafetyError(
                "source rejected; receipts="
                + ",".join(str(path) for path in receipts)
                + "; reason_codes=" + ",".join(reason_codes),
                rejection_receipts=receipts,
                reason_codes=reason_codes,
            )

        stage = temporary_root / "generation"
        stage.mkdir(mode=0o700)
        admitted_runs: list[_AdmittedRun] = []
        for index, (
            source, snapshot, inventory, decision,
            governance, context, generic,
        ) in enumerate(
            snapshots, start=1
        ):
            if generic:
                capability = next(
                    item for item in registry
                    if item.adapter_id == context["adapter_id"]
                )
            else:
                detection = resolve_adapter(inventory, registry)
                if (
                    detection.adapter_id is None
                    or detection.confidence != "exact"
                ):
                    raise ContractError(
                        "source has no exact admitted adapter variant; generic "
                        "sources require explicit mapping context"
                    )
                capability = next(
                    item for item in registry
                    if item.adapter_id == detection.adapter_id
                )
            if capability.maturity == "blocked":
                raise ContractError("exact adapter variant is blocked")
            adapter_type = _ADAPTER_TYPES.get(capability.adapter_id)
            if adapter_type is None:
                raise ContractError("selected adapter is not implemented")
            source_id = f"source-{index}"
            adapter = adapter_type(capability)
            profile = None
            if capability.platform == "generic_dsp":
                registration, payload = _generic_admission_inputs(
                    context=context,
                    inventory=inventory,
                    source_sha256=snapshot.source_sha256,
                    source_id=source_id,
                    import_id=request.import_id,
                    export_timestamp=str(governance["export_timestamp"]),
                    study=study,
                )
                profile = adapter.approved_profile(
                    payload,
                    registration=registration,
                    capability=capability,
                )
                detection = adapter.detect(inventory, profile=profile)
                if (
                    detection.adapter_id != capability.adapter_id
                    or detection.confidence != "explicit"
                ):
                    raise ContractError(
                        "source does not match the approved generic profile"
                    )
                adapter_inventory = adapter.inventory(
                    inventory, capability, profile=profile
                )
            else:
                registration = context["adapter_registration"]
                adapter_inventory = adapter.inventory(inventory, capability)
            normalization_context = (
                None if capability.platform == "generic_dsp" else {
                    "adapter_registration": deepcopy(
                        context["adapter_registration"]
                    ),
                    "reporting_metadata": deepcopy(
                        context["reporting_metadata"]
                    ),
                }
            )
            validation = adapter.validate(
                adapter_inventory,
                registration=registration,
                governance=governance,
                capability=capability,
            )
            if not validation.accepted:
                safe_codes = tuple(sorted(set(validation.errors)))
                receipts = _write_rejection_receipts(
                    retained_root=retained_root,
                    study_id=str(study.delivery_map["study_id"]),
                    import_id=request.import_id,
                    authority_secret=authority_secret,
                    rejected=[(
                        index, snapshot, safe_codes, inventory.row_count,
                    )],
                )
                raise ImportSafetyError(
                    "source rejected; receipts="
                    + ",".join(str(path) for path in receipts)
                    + "; reason_codes=" + ",".join(safe_codes),
                    rejection_receipts=receipts,
                    reason_codes=safe_codes,
                )
            if profile is None:
                admission = adapter.admission_validation(
                    inventory,
                    source_sha256=snapshot.source_sha256,
                    validation=validation,
                    registration=registration,
                    governance=governance,
                    normalization_context=normalization_context,
                )
            else:
                admission = adapter.admission_validation(
                    inventory,
                    source_sha256=snapshot.source_sha256,
                    validation=validation,
                    registration=registration,
                    governance=governance,
                    profile=profile,
                )
            suffix = source.path.suffix.lower()
            admitted = admit_source(
                snapshot,
                inventory,
                decision,
                admission,
                stage / "source-files" / f"{source_id}{suffix}",
            )
            governance_record = validate_source_governance_record(
                {
                    "schema_version": SOURCE_GOVERNANCE_RECORD_VERSION,
                    "governance_input": governance,
                    "source_governance_record_sha256": None,
                },
                trusted_runtime={
                    "observed_minimum_group_size": (
                        validation.observed_minimum_group_size
                    ),
                    "protected_staging_location": str(temporary_root),
                    "source_filename": source.path.name,
                    "source_sha256": admitted.source_sha256,
                    "aggregate_only": True,
                    "person_level_data": False,
                    "adapter_name": capability.adapter_id,
                    "adapter_version": capability.adapter_version,
                },
            )
            admitted_runs.append(_AdmittedRun(
                source_id=source_id,
                source=source,
                inventory=inventory,
                capability=capability,
                admission=admission,
                admitted=admitted,
                governance=governance,
                governance_record=governance_record,
                profile=profile,
                normalization_context=normalization_context,
                expected_observation_ids=_observation_ids(
                    inventory=inventory,
                    capability=capability,
                    source_sha256=admitted.source_sha256,
                    metric_id=metric_id,
                ),
            ))

        source_manifest = validate_source_manifest({
            "schema_version": SOURCE_MANIFEST_VERSION,
            "source_manifest_id": f"manifest-{request.import_id}",
            "study_id": study.delivery_map["study_id"],
            "import_id": request.import_id,
            "sources": [{
                "source_id": run.source_id,
                "source_sha256": run.admitted.source_sha256,
                "admission_sha256": run.admitted.admission_sha256,
            } for run in admitted_runs],
            "source_manifest_sha256": None,
        })

        all_matched: list[dict[str, object]] = []
        all_quarantined: list[dict[str, object]] = []
        source_handoffs: list[dict[str, object]] = []
        for run in admitted_runs:
            envelope = _event_envelope(
                study=study,
                import_id=request.import_id,
                imported_at=request.imported_at,
                imported_by=imported_by,
                source_manifest_sha256=str(
                    source_manifest["source_manifest_sha256"]
                ),
                observation_ids=run.expected_observation_ids,
                authority_secret=authority_secret,
            )
            try:
                batch = authenticate_normalized_batch(
                    authenticated_study=study,
                    study_authority=authority,
                    source_inventory=run.inventory,
                    admission_validation=run.admission,
                    admitted_source=run.admitted,
                    governance_input=run.governance,
                    adapter_context=(
                        run.normalization_context
                        if run.profile is None else None
                    ),
                    profile=run.profile,
                    source_manifest=source_manifest,
                    import_event_envelope=envelope,
                    effective_status_authority=effective_status_authority,
                )
            except ContractError as exc:
                if correction is None or isinstance(exc, ImportConflict):
                    raise
                raise ImportConflict(
                    "correction changes creative, metric, window, or "
                    "analytical identity"
                ) from exc
            matched = match_normalized_rows(
                authenticated_batch=batch,
                authenticated_study=study,
                study_authority=authority,
            )
            all_matched.extend(matched.matched)
            all_quarantined.extend(matched.quarantined)
            if effective_evidence_status == "preregistered_holdout":
                if correction is not None and not matched.matched:
                    raise ImportConflict(
                        "correction changes creative, metric, window, or "
                        "analytical identity"
                    )
                observations = [
                    build_validation_observation(
                        observation_id=str(
                            item["normalized_observation"]["observation_id"]
                        ),
                        authenticated_batch=batch,
                        authenticated_study=study,
                        study_authority=authority,
                    )
                    for item in matched.matched
                ]
                source_handoffs.append(validate_validation_handoff(
                    authenticated_batch=batch,
                    authenticated_study=study,
                    study_authority=authority,
                    validation_observations=observations,
                ))

        canonical_matched = tuple(sorted(
            all_matched,
            key=lambda item: str(
                item["normalized_observation"]["observation_id"]
            ),
        ))
        if correction is not None and not canonical_matched:
            raise ImportConflict("correction changes analytical identity")
        identity = _analytical_identity(canonical_matched)
        correction_request = None
        if correction is not None:
            assert superseded_generation is not None
            if (
                identity["analytical_identity_sha256"]
                != superseded_generation.analytical_identity_sha256
            ):
                raise ImportConflict("correction changes analytical identity")
            if superseded_generation.handoff is not None:
                prior_rows = superseded_generation.handoff[
                    "normalized_observations"
                ]
            else:
                prior_rows = json.loads(
                    (
                        superseded_generation.root
                        / "normalized-observations.json"
                    ).read_text(encoding="utf-8")
                )
            current_rows = [
                item["normalized_observation"] for item in canonical_matched
            ]
            prior_ids = [row.get("observation_id") for row in prior_rows]
            current_ids = [row.get("observation_id") for row in current_rows]
            if (
                len(prior_rows) != len(correction.supersedes_observation_ids)
                or len(current_rows) != len(prior_rows)
                or len(set(prior_ids)) != len(prior_ids)
                or len(set(current_ids)) != len(current_ids)
                or set(prior_ids) != set(correction.supersedes_observation_ids)
            ):
                raise ImportConflict(
                    "correction observations do not form one complete supersession"
                )

            def identity_map(
                rows: list[Mapping[str, object]], label: str
            ) -> dict[str, dict[str, object]]:
                result: dict[str, dict[str, object]] = {}
                for row in rows:
                    projection = _correction_static_projection(row)
                    key = sha256_json(projection)
                    if key in result:
                        raise ImportConflict(
                            f"correction {label} immutable identities are duplicated"
                        )
                    result[key] = projection
                return result

            prior_by_identity = identity_map(prior_rows, "prior")
            current_by_identity = identity_map(current_rows, "replacement")
            if (
                len(prior_by_identity) != len(prior_rows)
                or len(current_by_identity) != len(current_rows)
                or set(prior_by_identity) != set(current_by_identity)
            ):
                raise ImportConflict(
                    "correction changes creative, metric, window, or other "
                    "immutable analytical fields"
                )
            prior_sources = superseded_generation.source_manifest["sources"]
            replacement_sources = source_manifest["sources"]
            if (
                not isinstance(prior_sources, list)
                or len(prior_sources) != 1
                or not isinstance(replacement_sources, list)
                or len(replacement_sources) != 1
            ):
                raise ImportConflict(
                    "correction requires one superseded and replacement source"
                )
            if (
                prior_sources[0]["source_sha256"]
                == replacement_sources[0]["source_sha256"]
            ):
                raise ImportConflict(
                    "correction replacement source bytes are unchanged"
                )
            correction_request = validate_correction_request(
                {
                    "schema_version": CORRECTION_REQUEST_VERSION,
                    "correction_id": correction.correction_id,
                    "study_id": study.delivery_map["study_id"],
                    "requested_at": correction.requested_at,
                    "actor": correction.actor,
                    "reason_code": correction.reason_code,
                    "reason": correction.reason,
                    "supersedes_import_id": (
                        correction.supersedes_import_id
                    ),
                    "supersedes_observation_ids": list(
                        correction.supersedes_observation_ids
                    ),
                    "expected_analytical_identity_sha256": (
                        superseded_generation.analytical_identity_sha256
                    ),
                    "replacement_source_sha256": replacement_sources[0][
                        "source_sha256"
                    ],
                    "correction_request_sha256": None,
                },
                trusted_correction_context={
                    "superseded_import": {
                        "import_id": correction.supersedes_import_id,
                        "source_sha256": prior_sources[0]["source_sha256"],
                    },
                    "replacement_source": {
                        "source_manifest_id": source_manifest[
                            "source_manifest_id"
                        ],
                        "source_sha256": replacement_sources[0][
                            "source_sha256"
                        ],
                    },
                },
            )
        handoff = (
            _aggregate_handoff(source_handoffs)
            if effective_evidence_status == "preregistered_holdout"
            else None
        )
        maturities = {run.capability.maturity for run in admitted_runs}
        adapter_maturity = (
            "export_verified"
            if maturities == {"export_verified"}
            else "schema_tested"
        )
        evidence_status = effective_evidence_status
        operational_status = (
            "blocked" if evidence_status == "blocked"
            else "descriptive_only" if evidence_status == "descriptive_only"
            else "contract_ready" if adapter_maturity == "export_verified"
            else "incomplete"
        )
        reasons = {
            "blocked": ["evidence is blocked by authenticated study state"],
            "descriptive_only": [
                "study chronology is permanently descriptive only"
            ],
            "contract_ready": [
                "all exact adapter variants have export verification"
            ],
            "incomplete": [
                "export verification is missing for an exact adapter variant"
            ],
        }[operational_status]
        readiness = validate_readiness_report({
            "schema_version": READINESS_VERSION,
            "study_id": study.delivery_map["study_id"],
            "import_id": request.import_id,
            "evidence_status": evidence_status,
            "operational_status": operational_status,
            "adapter_maturity": adapter_maturity,
            "reasons": reasons,
            "readiness_sha256": None,
        })
        matching_text = render_matching_report(
            matched=canonical_matched,
            quarantined=all_quarantined,
        )
        readiness_text = render_readiness_report(readiness)

        files: list[dict[str, object]] = []
        for run in admitted_runs:
            relative = run.admitted.source_path.relative_to(
                stage.resolve()
            ).as_posix()
            raw = run.admitted.source_path.read_bytes()
            files.append({
                "relative_path": relative,
                "sha256": sha256_bytes(raw),
                "byte_count": len(raw),
                "role": "accepted_source",
                "source_id": run.source_id,
            })
        _write_file(
            stage, "source-manifest.json",
            canonical_json_bytes(source_manifest), "source_manifest", files,
        )
        _write_file(
            stage, "normalized-observations.json",
            canonical_json_bytes([
                item["normalized_observation"] for item in canonical_matched
            ]), "supporting_record", files,
        )
        _write_file(
            stage, "observation-bindings.json",
            canonical_json_bytes([
                item["delivery_binding"] for item in canonical_matched
            ]), "supporting_record", files,
        )
        _write_file(
            stage, "source-governance-records.json",
            canonical_json_bytes([
                run.governance_record for run in admitted_runs
            ]), "supporting_record", files,
        )
        _write_file(
            stage, "matching-report.md", matching_text.encode("utf-8"),
            "supporting_record", files,
        )
        _write_file(
            stage, "readiness-report.json", canonical_json_bytes(readiness),
            "supporting_record", files,
        )
        _write_file(
            stage, "readiness-report.md", readiness_text.encode("utf-8"),
            "supporting_record", files,
        )
        if handoff is not None:
            _write_file(
                stage, "validation-handoff.json",
                canonical_json_bytes(handoff), "validation_handoff", files,
            )
        _write_file(
            stage, "analytical-identity.json",
            canonical_json_bytes(identity), "analytical_identity", files,
        )
        if correction_request is not None:
            _write_file(
                stage, "correction-request.json",
                canonical_json_bytes(correction_request),
                "correction_request", files,
            )
        all_observation_ids = tuple(sorted(
            observation_id
            for run in admitted_runs
            for observation_id in run.expected_observation_ids
        ))
        final_event = _event_envelope(
            study=study,
            import_id=request.import_id,
            imported_at=request.imported_at,
            imported_by=imported_by,
            source_manifest_sha256=str(
                source_manifest["source_manifest_sha256"]
            ),
            observation_ids=all_observation_ids,
            authority_secret=authority_secret,
        )["event"]
        _write_file(
            stage, "import-event.json", canonical_json_bytes(final_event),
            "import_event", files,
        )

        exports = [
            str(run.governance["export_timestamp"])
            for run in admitted_runs
        ]
        source_exported_at = max(
            exports, key=lambda value: _timestamp(value, "export_timestamp")
        )
        chronology = study.registration_receipt["chronology"]["events"]
        delivery_started_at = next(
            str(event["occurred_at"])
            for event in chronology
            if event["event_type"] == "delivery_started"
        )
        generation = {
            "schema_version": GENERATION_VERSION,
            "study_id": study.delivery_map["study_id"],
            "registration_id": study.registration["registration_id"],
            "registration_sha256": study.registration["registration_sha256"],
            "registration_receipt_sha256": study.registration_receipt[
                "receipt_sha256"
            ],
            "import_id": request.import_id,
            "imported_at": request.imported_at,
            "imported_by": imported_by,
            "previous_evidence_status": effective_evidence_status,
            "next_evidence_status": evidence_status,
            "delivery_started_at": delivery_started_at,
            "first_outcome_accessed_at": _first_outcome_accessed_at(
                study, exports
            ),
            "source_exported_at": source_exported_at,
            "source_manifest_sha256": source_manifest[
                "source_manifest_sha256"
            ],
            "validation_handoff_sha256": (
                None if handoff is None else handoff["handoff_sha256"]
            ),
            "analytical_identity_sha256": identity[
                "analytical_identity_sha256"
            ],
            "correction_id": (
                None if correction_request is None
                else correction_request["correction_id"]
            ),
            "correction_request_sha256": (
                None if correction_request is None
                else correction_request["correction_request_sha256"]
            ),
            "supersedes_import_id": (
                None if correction_request is None
                else correction_request["supersedes_import_id"]
            ),
            "superseded_observation_ids": (
                [] if correction_request is None
                else correction_request["supersedes_observation_ids"]
            ),
            "files": sorted(files, key=lambda item: item["relative_path"]),
            "generation_sha256": None,
        }
        generation["generation_sha256"] = sha256_json(generation)
        manifest_path = stage / "generation-manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(generation))
        manifest_path.chmod(0o600)

        retained_root.verify_live_path()
        commit = commit_import_generation(
            study_root=request.study_root,
            staged_generation=stage,
            expected_previous_ledger_digest=effective_ledger_digest,
            authority=authority,
        )
        return ImportWorkflowResult(
            import_id=commit.import_id,
            import_digest=commit.import_digest,
            generation_path=commit.generation_path,
            ledger_digest=commit.ledger_digest,
            analytical_identity_sha256=commit.analytical_identity_sha256,
            evidence_status=evidence_status,
            operational_status=operational_status,
            readiness_report_json=(
                commit.generation_path / "readiness-report.json"
            ),
            readiness_report_markdown=(
                commit.generation_path / "readiness-report.md"
            ),
            matching_report=commit.generation_path / "matching-report.md",
            validation_handoff_written=handoff is not None,
            source_count=len(admitted_runs),
            matched_row_count=len(canonical_matched),
            quarantined_row_count=len(all_quarantined),
        )


__all__ = [
    "CorrectionInput",
    "ImportRequest",
    "ImportSafetyError",
    "ImportWorkflowResult",
    "SourceInput",
    "import_results",
    "pair_source_arguments",
    "recover_study_from_paths",
    "validate_study",
]
