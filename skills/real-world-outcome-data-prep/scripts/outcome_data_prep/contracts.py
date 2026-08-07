"""Closed, self-hashed boundary documents for outcome-data preparation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import math
import re

from .common import (
    ContractError,
    closed_object,
    require_enum,
    require_identifier,
    require_nonnegative_integer,
    require_numeric_string,
    require_numeric_string_or_number,
    require_string,
    require_string_list,
    sha256_json,
)


STUDY_SETUP_VERSION = "outcome-study-setup-v1"
DELIVERY_MAP_VERSION = "outcome-delivery-map-v1"
CREATIVE_MANIFEST_VERSION = "outcome-creative-manifest-v1"
REGISTRATION_RECEIPT_VERSION = "outcome-registration-receipt-v1"
AUTHENTICATED_REGISTRATION_RECEIPT_VERSION = "outcome-registration-receipt-v2"
SOURCE_GOVERNANCE_INPUT_VERSION = "outcome-source-governance-input-v1"
SOURCE_GOVERNANCE_RECORD_VERSION = "outcome-source-governance-record-v1"
SOURCE_MANIFEST_VERSION = "outcome-source-manifest-v1"
CORRECTION_REQUEST_VERSION = "outcome-correction-request-v1"
NORMALIZED_OBSERVATION_VERSION = "normalized-outcome-observation-v1"
OBSERVATION_BINDING_VERSION = "outcome-observation-binding-v1"
READINESS_VERSION = "outcome-prep-readiness-v1"
IMPORT_EVENT_VERSION = "outcome-import-event-v1"

EVIDENCE_STATUSES = {"preregistered_holdout", "descriptive_only", "blocked"}
OPERATIONAL_STATUSES = {"contract_ready", "incomplete", "descriptive_only", "blocked"}
ADAPTER_MATURITY = {"schema_tested", "export_verified", "blocked"}

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")

_STUDY_SETUP_KEYS = {
    "schema_version", "study_id", "prepared_at", "prepared_by", "study_name",
    "planned_start_at", "planned_end_at", "outcome_access_after", "primary_metric",
    "audience_definition", "study_setup_sha256",
}
_DELIVERY_MAP_KEYS = {
    "schema_version", "study_id", "registration_id",
    "sealed_before_outcome_access", "mappings", "chronology",
    "delivery_map_sha256",
}
_DELIVERY_MAPPING_KEYS = {
    "mapping_id", "platform", "platform_campaign_id",
    "platform_ad_group_id", "platform_ad_id", "platform_creative_id",
    "block_id", "study_id", "arm_id", "batch_id", "segment_ids",
    "creative_id", "variant_id", "asset_sha256",
    "campaign_plan_sha256",
}
_CREATIVE_MANIFEST_KEYS = {
    "schema_version", "registration_id", "creatives", "creative_manifest_sha256",
}
_CREATIVE_KEYS = {
    "creative_id", "variant_id", "asset_sha256", "role",
    "predicted_rank", "predicted_group",
}
_REGISTRATION_RECEIPT_KEYS = {
    "schema_version", "registration_id", "study_id", "registered_at",
    "registered_by", "study_setup_sha256", "delivery_map_sha256",
    "creative_manifest_sha256", "registration_receipt_sha256",
}
_AUTHENTICATED_REGISTRATION_RECEIPT_KEYS = {
    "schema_version", "study_id", "registration_id", "registration_sha256",
    "delivery_map_sha256", "creative_manifest_sha256", "chronology",
    "evidence_status", "receipt_sha256", "receipt_hmac_sha256",
}
_CHRONOLOGY_KEYS = {"events"}
_CHRONOLOGY_EVENT_KEYS = {
    "event_type", "occurred_at", "evidence_source_sha256", "attested_by",
    "attested_at", "authority_id",
}
_SOURCE_GOVERNANCE_INPUT_KEYS = {
    "schema_version", "data_owner", "system_of_record", "permission_reference", "confirmer",
    "allowed_purpose", "retention_policy", "minimum_group_size_rule",
    "restricted_fields_removed_attestation", "export_method", "export_timestamp",
    "source_governance_input_sha256",
}
_SOURCE_GOVERNANCE_RECORD_KEYS = {
    "schema_version", "governance_input", "observed_minimum_group_size",
    "protected_staging_location", "source_filename", "source_sha256", "aggregate_only",
    "person_level_data", "adapter_name", "adapter_version", "source_governance_record_sha256",
}
_SOURCE_GOVERNANCE_RECORD_REQUEST_KEYS = {
    "schema_version", "governance_input", "source_governance_record_sha256",
}
_TRUSTED_RUNTIME_SOURCE_FACT_KEYS = {
    "observed_minimum_group_size", "protected_staging_location", "source_filename",
    "source_sha256", "aggregate_only", "person_level_data", "adapter_name", "adapter_version",
}
_TRUSTED_CORRECTION_CONTEXT_KEYS = {"superseded_import", "replacement_source"}
_TRUSTED_SUPERSEDED_IMPORT_KEYS = {"import_id", "source_sha256"}
_TRUSTED_REPLACEMENT_SOURCE_KEYS = {"source_manifest_id", "source_sha256"}
_SOURCE_MANIFEST_KEYS = {
    "schema_version", "source_manifest_id", "study_id", "import_id", "sources",
    "source_manifest_sha256",
}
_CORRECTION_REQUEST_KEYS = {
    "schema_version", "correction_id", "study_id", "requested_at", "actor", "reason_code",
    "reason", "supersedes_import_id", "supersedes_observation_ids",
    "expected_analytical_identity_sha256", "replacement_source_sha256",
    "correction_request_sha256",
}
_NORMALIZED_OBSERVATION_KEYS = {
    "schema_version", "observation_id", "study_id", "registration_id", "import_id",
    "source_id", "source_sha256", "source_row_reference", "platform", "adapter",
    "account", "campaign", "ad_group", "ad", "creative", "reporting", "attribution",
    "currency", "spend", "exposure", "outcome", "platform_semantics",
    "validation_projection",
    "normalized_observation_sha256",
}
_NORMALIZED_ADAPTER_KEYS = {
    "adapter_id", "adapter_version", "maturity",
}
_NORMALIZED_IDENTITY_KEYS = {"platform_id"}
_NORMALIZED_REPORTING_KEYS = {
    "start_date", "end_date", "timezone", "basis", "request_level",
    "time_increment", "segment_grain", "latency_state", "observed_at",
}
_NORMALIZED_ATTRIBUTION_KEYS = {"report_time", "windows"}
_NORMALIZED_CURRENCY_KEYS = {"code", "basis"}
_NORMALIZED_SPEND_KEYS = {
    "value", "decimal", "source_numeric_text", "source_metric", "source_unit",
}
_NORMALIZED_EXPOSURE_KEYS = {"impressions", "clicks"}
_NORMALIZED_COUNT_KEYS = {"value", "source_numeric_text"}
_NORMALIZED_OUTCOME_KEYS = {
    "metric_id", "source_metric", "value", "decimal", "source_numeric_text",
    "value_state", "omitted_zero_behavior",
}
_NORMALIZED_PLATFORM_SEMANTICS_KEYS = {
    "billed_currency", "currency_relationship", "privacy_review_state",
    "demographic_truncation_state", "click_semantic", "optimization_event",
    "delivery_state", "skan_state", "search_term_id", "search_term_state",
}
_NORMALIZED_VALUE_STATES = {
    "observed", "observed_zero", "null", "absent", "suppressed",
    "omitted_zero", "fractional", "modeled", "estimated",
}
_NORMALIZED_LATENCY_STATES = {"mature", "immature"}
_NORMALIZED_CURRENCY_RELATIONSHIPS = {
    "not_applicable",
    "local_currency_equals_billed_currency",
    "local_currency_distinct_from_billed_currency",
}
_NORMALIZED_PRIVACY_REVIEW_STATES = {
    "not_applicable",
    "aggregate_privacy_reviewed",
    "privacy_suppressed",
    "no_access",
}
_NORMALIZED_DEMOGRAPHIC_TRUNCATION_STATES = {
    "not_applicable",
    "top_100_categories",
}
_NORMALIZED_CLICK_SEMANTICS = {
    "not_applicable",
    "all_clicks",
    "destination_clicks",
}
_NORMALIZED_DELIVERY_STATES = {
    "not_applicable",
    "standard",
    "delayed",
}
_NORMALIZED_SKAN_STATES = {
    "not_applicable",
    "non_skan",
    "skan_delayed",
}
_NORMALIZED_SEARCH_TERM_STATES = {
    "not_applicable",
    "not_reported",
    "observed",
    "unknown",
}
_VALIDATION_PROJECTION_KEYS = {
    "status", "evidence_status", "metric_family", "measurement_window",
    "attribution_window", "aggregate", "eligible_exposure_count",
    "missing_outcome_count", "effective_sample_size", "assignment",
    "confidence_level", "permission_confirmed", "outcome_accessed_at",
    "limitations",
}
_VALIDATION_ASSIGNMENT_KEYS = {"design", "unit", "leakage_detected"}
_METRIC_FAMILIES = {
    "binary_proportion", "continuous_mean", "event_rate",
}
_OBSERVATION_BINDING_KEYS = {
    "schema_version", "observation_id", "registration_id",
    "registration_sha256", "normalized_observation_sha256",
    "delivery_map_sha256", "delivery_mapping_id",
    "delivery_mapping_sha256", "campaign_plan_sha256",
    "platform", "platform_campaign_id",
    "platform_ad_group_id", "platform_ad_id", "platform_creative_id",
    "block_id", "study_id", "arm_id", "batch_id", "segment_ids",
    "creative_id", "variant_id", "asset_sha256", "panel_sha256",
    "package_sha256", "run_id", "result_sha256", "metric_id",
    "measurement_window", "attribution_window",
    "source_sha256", "source_row_reference", "evidence_status",
    "observation_binding_sha256",
}
_READINESS_KEYS = {
    "schema_version", "study_id", "import_id", "evidence_status", "operational_status",
    "adapter_maturity", "reasons", "readiness_sha256",
}
_IMPORT_EVENT_KEYS = {
    "schema_version", "import_id", "study_id", "imported_at", "imported_by",
    "source_manifest_sha256", "observation_ids", "import_event_sha256",
}


def _json_copy(value: object, path: str = "$") -> object:
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} object keys must be strings")
            copied[key] = _json_copy(item, f"{path}.{key}")
        return copied
    if isinstance(value, list):
        return [_json_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"{path} must contain finite numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ContractError(f"{path} must contain JSON-compatible values")


def _document(payload: object, keys: set[str], version: str, path: str) -> dict[str, object]:
    document = closed_object(_json_copy(payload, path), keys, path)
    if document["schema_version"] != version:
        raise ContractError(f"{path}.schema_version is unknown")
    return document


def _timestamp(value: object, path: str) -> str:
    result = require_string(value, path)
    if "T" not in result:
        raise ContractError(f"{path} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{path} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{path} must be a timezone-aware ISO 8601 timestamp")
    return result


def _bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"{path} must be boolean")
    return value


def _finite_number(
    value: object, path: str, *, minimum: float | None = None
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"{path} must be finite")
    if minimum is not None and value < minimum:
        raise ContractError(f"{path} must be at least {minimum}")
    return value


def _digest(value: object, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    result = require_string(value, path)
    if not _DIGEST.fullmatch(result):
        raise ContractError(f"{path} must be a prefixed SHA-256")
    return result


def _self_hash(document: dict[str, object], field: str, path: str) -> dict[str, object]:
    supplied = _digest(document[field], f"{path}.{field}", nullable=True)
    candidate = deepcopy(document)
    candidate[field] = None
    expected = sha256_json(candidate)
    if supplied is not None and supplied != expected:
        raise ContractError(f"{path}.{field} does not match canonical content")
    document[field] = expected
    return document


def _nonempty_objects(value: object, path: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be a list")
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ContractError(f"{path}[{index}] must be an object")
        result.append(dict(_json_copy(item, f"{path}[{index}]")))
    return result


def validate_study_setup_input(payload: object) -> dict[str, object]:
    document = _document(payload, _STUDY_SETUP_KEYS, STUDY_SETUP_VERSION, "study_setup")
    require_identifier(document["study_id"], "study_setup.study_id")
    _timestamp(document["prepared_at"], "study_setup.prepared_at")
    require_identifier(document["prepared_by"], "study_setup.prepared_by")
    require_string(document["study_name"], "study_setup.study_name")
    start = _timestamp(document["planned_start_at"], "study_setup.planned_start_at")
    end = _timestamp(document["planned_end_at"], "study_setup.planned_end_at")
    access = _timestamp(document["outcome_access_after"], "study_setup.outcome_access_after")
    if datetime.fromisoformat(start.replace("Z", "+00:00")) > datetime.fromisoformat(end.replace("Z", "+00:00")):
        raise ContractError("study_setup.planned_start_at must not follow planned_end_at")
    if datetime.fromisoformat(end.replace("Z", "+00:00")) > datetime.fromisoformat(access.replace("Z", "+00:00")):
        raise ContractError("study_setup.planned_end_at must not follow outcome_access_after")
    require_string(document["primary_metric"], "study_setup.primary_metric")
    if not isinstance(document["audience_definition"], Mapping):
        raise ContractError("study_setup.audience_definition must be an object")
    return _self_hash(document, "study_setup_sha256", "study_setup")


def validate_delivery_map(payload: object) -> dict[str, object]:
    document = _document(payload, _DELIVERY_MAP_KEYS, DELIVERY_MAP_VERSION, "delivery_map")
    require_identifier(document["study_id"], "delivery_map.study_id")
    require_identifier(document["registration_id"], "delivery_map.registration_id")
    _bool(document["sealed_before_outcome_access"], "delivery_map.sealed_before_outcome_access")
    mappings = _nonempty_objects(document["mappings"], "delivery_map.mappings")
    mapping_ids: set[str] = set()
    for index, raw_mapping in enumerate(mappings):
        path = f"delivery_map.mappings[{index}]"
        mapping = closed_object(raw_mapping, _DELIVERY_MAPPING_KEYS, path)
        for field in (
            "mapping_id", "platform", "platform_campaign_id",
            "platform_ad_group_id", "platform_ad_id",
            "platform_creative_id", "block_id", "study_id", "arm_id",
            "batch_id", "creative_id", "variant_id",
        ):
            require_identifier(mapping[field], f"{path}.{field}")
        require_string_list(mapping["segment_ids"], f"{path}.segment_ids")
        _digest(mapping["asset_sha256"], f"{path}.asset_sha256")
        _digest(
            mapping["campaign_plan_sha256"],
            f"{path}.campaign_plan_sha256",
        )
        if mapping["study_id"] != document["study_id"]:
            raise ContractError(
                f"{path}.study_id must match delivery_map.study_id"
            )
        mapping_id = str(mapping["mapping_id"])
        if mapping_id in mapping_ids:
            raise ContractError("delivery_map mappings must have unique mapping_id values")
        mapping_ids.add(mapping_id)
    document["mappings"] = mappings
    document["chronology"] = validate_chronology(
        document["chronology"], path="delivery_map.chronology"
    )
    return _self_hash(document, "delivery_map_sha256", "delivery_map")


def validate_creative_manifest(payload: object) -> dict[str, object]:
    document = _document(payload, _CREATIVE_MANIFEST_KEYS, CREATIVE_MANIFEST_VERSION, "creative_manifest")
    require_identifier(document["registration_id"], "creative_manifest.registration_id")
    creatives = _nonempty_objects(document["creatives"], "creative_manifest.creatives")
    creative_ids: set[str] = set()
    for index, raw_creative in enumerate(creatives):
        path = f"creative_manifest.creatives[{index}]"
        creative = closed_object(raw_creative, _CREATIVE_KEYS, path)
        for field in ("creative_id", "variant_id"):
            require_identifier(creative[field], f"{path}.{field}")
        require_string(creative["role"], f"{path}.role")
        _digest(creative["asset_sha256"], f"{path}.asset_sha256")
        require_nonnegative_integer(creative["predicted_rank"], f"{path}.predicted_rank")
        require_nonnegative_integer(creative["predicted_group"], f"{path}.predicted_group")
        creative_id = str(creative["creative_id"])
        if creative_id in creative_ids:
            raise ContractError("creative_manifest creatives must have unique creative_id values")
        creative_ids.add(creative_id)
    document["creatives"] = creatives
    return _self_hash(document, "creative_manifest_sha256", "creative_manifest")


def validate_chronology(
    payload: object, *, path: str = "chronology"
) -> dict[str, object]:
    document = closed_object(_json_copy(payload, path), _CHRONOLOGY_KEYS, path)
    events = _nonempty_objects(document["events"], f"{path}.events")
    checked: list[dict[str, object]] = []
    for index, raw_event in enumerate(events):
        event_path = f"{path}.events[{index}]"
        event = closed_object(raw_event, _CHRONOLOGY_EVENT_KEYS, event_path)
        require_identifier(event["event_type"], f"{event_path}.event_type")
        _timestamp(event["occurred_at"], f"{event_path}.occurred_at")
        _digest(
            event["evidence_source_sha256"],
            f"{event_path}.evidence_source_sha256",
        )
        for field in ("attested_by", "authority_id"):
            require_identifier(event[field], f"{event_path}.{field}")
        _timestamp(event["attested_at"], f"{event_path}.attested_at")
        checked.append(event)
    document["events"] = checked
    return document


def validate_registration_receipt(payload: object) -> dict[str, object]:
    document = _document(payload, _REGISTRATION_RECEIPT_KEYS, REGISTRATION_RECEIPT_VERSION, "registration_receipt")
    for field in ("registration_id", "study_id"):
        require_identifier(document[field], f"registration_receipt.{field}")
    _timestamp(document["registered_at"], "registration_receipt.registered_at")
    require_identifier(document["registered_by"], "registration_receipt.registered_by")
    for field in (
        "study_setup_sha256", "delivery_map_sha256",
        "creative_manifest_sha256",
    ):
        _digest(document[field], f"registration_receipt.{field}")
    return _self_hash(
        document, "registration_receipt_sha256", "registration_receipt"
    )


def validate_authenticated_registration_receipt(
    payload: object,
) -> dict[str, object]:
    document = _document(
        payload,
        _AUTHENTICATED_REGISTRATION_RECEIPT_KEYS,
        AUTHENTICATED_REGISTRATION_RECEIPT_VERSION,
        "authenticated_registration_receipt",
    )
    for field in ("registration_id", "study_id"):
        require_identifier(
            document[field], f"authenticated_registration_receipt.{field}"
        )
    for field in (
        "registration_sha256", "delivery_map_sha256",
        "creative_manifest_sha256",
    ):
        _digest(
            document[field],
            f"authenticated_registration_receipt.{field}",
        )
    document["chronology"] = validate_chronology(
        document["chronology"],
        path="authenticated_registration_receipt.chronology",
    )
    require_enum(
        document["evidence_status"],
        EVIDENCE_STATUSES,
        "authenticated_registration_receipt.evidence_status",
    )
    _digest(
        document["receipt_hmac_sha256"],
        "authenticated_registration_receipt.receipt_hmac_sha256",
    )
    supplied = _digest(
        document["receipt_sha256"],
        "authenticated_registration_receipt.receipt_sha256",
        nullable=True,
    )
    candidate = deepcopy(document)
    candidate["receipt_sha256"] = None
    candidate["receipt_hmac_sha256"] = None
    expected = sha256_json(candidate)
    if supplied is not None and supplied != expected:
        raise ContractError(
            "authenticated_registration_receipt.receipt_sha256 does not "
            "match canonical content"
        )
    document["receipt_sha256"] = expected
    return document


def validate_source_governance_input(payload: object) -> dict[str, object]:
    document = _document(payload, _SOURCE_GOVERNANCE_INPUT_KEYS, SOURCE_GOVERNANCE_INPUT_VERSION, "source_governance_input")
    for field in (
        "data_owner", "system_of_record", "permission_reference", "confirmer", "allowed_purpose",
        "retention_policy", "minimum_group_size_rule", "export_method",
    ):
        require_string(document[field], f"source_governance_input.{field}")
    _bool(document["restricted_fields_removed_attestation"], "source_governance_input.restricted_fields_removed_attestation")
    _timestamp(document["export_timestamp"], "source_governance_input.export_timestamp")
    return _self_hash(document, "source_governance_input_sha256", "source_governance_input")


def validate_source_governance_record(
    payload: object, *, trusted_runtime: Mapping[str, object]
) -> dict[str, object]:
    """Seal user governance facts with runtime-derived source observations.

    ``payload`` deliberately excludes source/staging facts. The importing
    transaction must pass those facts through the keyword-only trusted runtime
    channel after it has inspected the uploaded bytes and protected stage.
    """

    request = _document(
        payload,
        _SOURCE_GOVERNANCE_RECORD_REQUEST_KEYS,
        SOURCE_GOVERNANCE_RECORD_VERSION,
        "source_governance_record",
    )
    runtime = closed_object(
        _json_copy(trusted_runtime, "trusted_runtime"),
        _TRUSTED_RUNTIME_SOURCE_FACT_KEYS,
        "trusted_runtime",
    )
    document = {
        "schema_version": request["schema_version"],
        "governance_input": validate_source_governance_input(request["governance_input"]),
        **runtime,
        "source_governance_record_sha256": request["source_governance_record_sha256"],
    }
    require_nonnegative_integer(document["observed_minimum_group_size"], "source_governance_record.observed_minimum_group_size")
    for field in ("protected_staging_location", "source_filename", "adapter_name", "adapter_version"):
        require_string(document[field], f"source_governance_record.{field}")
    _digest(document["source_sha256"], "source_governance_record.source_sha256")
    if not _bool(document["aggregate_only"], "source_governance_record.aggregate_only"):
        raise ContractError("source_governance_record.aggregate_only must be true")
    if _bool(document["person_level_data"], "source_governance_record.person_level_data"):
        raise ContractError("source_governance_record.person_level_data must be false")
    return _self_hash(document, "source_governance_record_sha256", "source_governance_record")


def validate_source_manifest(payload: object) -> dict[str, object]:
    document = _document(payload, _SOURCE_MANIFEST_KEYS, SOURCE_MANIFEST_VERSION, "source_manifest")
    for field in ("source_manifest_id", "study_id", "import_id"):
        require_identifier(document[field], f"source_manifest.{field}")
    sources = _nonempty_objects(document["sources"], "source_manifest.sources")
    identifiers: set[str] = set()
    for index, source in enumerate(sources):
        source_id = require_identifier(source.get("source_id"), f"source_manifest.sources[{index}].source_id")
        if source_id in identifiers:
            raise ContractError("source_manifest.sources must have unique source_id values")
        identifiers.add(source_id)
    return _self_hash(document, "source_manifest_sha256", "source_manifest")


def _trusted_correction_context(value: object) -> dict[str, dict[str, object]]:
    """Validate transaction-owned old and newly staged source identities."""

    context = closed_object(
        _json_copy(value, "trusted_correction_context"),
        _TRUSTED_CORRECTION_CONTEXT_KEYS,
        "trusted_correction_context",
    )
    superseded_import = closed_object(
        context["superseded_import"],
        _TRUSTED_SUPERSEDED_IMPORT_KEYS,
        "trusted_correction_context.superseded_import",
    )
    replacement_source = closed_object(
        context["replacement_source"],
        _TRUSTED_REPLACEMENT_SOURCE_KEYS,
        "trusted_correction_context.replacement_source",
    )
    require_identifier(
        superseded_import["import_id"],
        "trusted_correction_context.superseded_import.import_id",
    )
    _digest(
        superseded_import["source_sha256"],
        "trusted_correction_context.superseded_import.source_sha256",
    )
    require_identifier(
        replacement_source["source_manifest_id"],
        "trusted_correction_context.replacement_source.source_manifest_id",
    )
    _digest(
        replacement_source["source_sha256"],
        "trusted_correction_context.replacement_source.source_sha256",
    )
    return {
        "superseded_import": superseded_import,
        "replacement_source": replacement_source,
    }


def validate_correction_request(
    payload: object, *, trusted_correction_context: Mapping[str, object]
) -> dict[str, object]:
    """Bind a correction to its prior import and newly staged source snapshot.

    The transaction layer supplies the closed trusted context after resolving
    the old import and staging the replacement export. A corrected export is
    expected to have different bytes from the superseded source.
    """

    document = _document(payload, _CORRECTION_REQUEST_KEYS, CORRECTION_REQUEST_VERSION, "correction_request")
    for field in ("correction_id", "study_id", "actor", "reason_code", "supersedes_import_id"):
        require_identifier(document[field], f"correction_request.{field}")
    _timestamp(document["requested_at"], "correction_request.requested_at")
    require_string(document["reason"], "correction_request.reason")
    observation_ids = require_string_list(document["supersedes_observation_ids"], "correction_request.supersedes_observation_ids")
    if not observation_ids:
        raise ContractError("correction_request.supersedes_observation_ids must not be empty")
    for field in ("expected_analytical_identity_sha256", "replacement_source_sha256"):
        _digest(document[field], f"correction_request.{field}")
    trusted = _trusted_correction_context(trusted_correction_context)
    if document["supersedes_import_id"] != trusted["superseded_import"]["import_id"]:
        raise ContractError(
            "correction_request.supersedes_import_id must match the trusted superseded import identity"
        )
    if document["replacement_source_sha256"] != trusted["replacement_source"]["source_sha256"]:
        raise ContractError(
            "correction_request.replacement_source_sha256 must match the trusted replacement source snapshot"
        )
    return _self_hash(document, "correction_request_sha256", "correction_request")


def _validate_validation_projection(
    payload: object, *, path: str
) -> dict[str, object]:
    projection = closed_object(payload, _VALIDATION_PROJECTION_KEYS, path)
    status = require_enum(
        projection["status"], {"available", "unavailable"}, f"{path}.status"
    )
    require_enum(
        projection["evidence_status"],
        EVIDENCE_STATUSES,
        f"{path}.evidence_status",
    )
    limitations = require_string_list(
        projection["limitations"], f"{path}.limitations"
    )
    if status == "unavailable":
        unavailable_fields = (
            "metric_family",
            "measurement_window",
            "attribution_window",
            "aggregate",
            "eligible_exposure_count",
            "missing_outcome_count",
            "effective_sample_size",
            "assignment",
            "confidence_level",
            "permission_confirmed",
            "outcome_accessed_at",
        )
        if any(projection[field] is not None for field in unavailable_fields):
            raise ContractError(f"{path} unavailable fields must be null")
        projection["limitations"] = limitations
        return projection

    family = require_enum(
        projection["metric_family"],
        _METRIC_FAMILIES,
        f"{path}.metric_family",
    )
    for field in ("measurement_window", "attribution_window"):
        require_string(projection[field], f"{path}.{field}")
    aggregate_keys = {
        "binary_proportion": {
            "success_count", "eligible_exposure_count",
        },
        "continuous_mean": {
            "sample_count", "mean", "standard_deviation",
        },
        "event_rate": {"event_count", "exposure_time"},
    }[family]
    aggregate = closed_object(
        projection["aggregate"], aggregate_keys, f"{path}.aggregate"
    )
    for field, value in aggregate.items():
        if field in {
            "success_count", "eligible_exposure_count", "sample_count",
            "event_count",
        }:
            require_nonnegative_integer(value, f"{path}.aggregate.{field}")
        else:
            _finite_number(
                value,
                f"{path}.aggregate.{field}",
                minimum=None if field == "mean" else 0,
            )
    if (
        family == "binary_proportion"
        and aggregate["success_count"] > aggregate["eligible_exposure_count"]
    ):
        raise ContractError(
            f"{path}.aggregate.success_count cannot exceed "
            "eligible_exposure_count"
        )
    if family == "event_rate" and aggregate["exposure_time"] <= 0:
        raise ContractError(
            f"{path}.aggregate.exposure_time must be positive"
        )
    eligible = require_nonnegative_integer(
        projection["eligible_exposure_count"],
        f"{path}.eligible_exposure_count",
    )
    missing = require_nonnegative_integer(
        projection["missing_outcome_count"],
        f"{path}.missing_outcome_count",
    )
    if missing > eligible:
        raise ContractError(
            f"{path}.missing_outcome_count cannot exceed eligible exposures"
        )
    effective = _finite_number(
        projection["effective_sample_size"],
        f"{path}.effective_sample_size",
        minimum=0,
    )
    if effective > eligible - missing:
        raise ContractError(
            f"{path}.effective_sample_size cannot exceed analyzable outcomes"
        )
    if family == "binary_proportion":
        aggregate_count = aggregate["eligible_exposure_count"]
    elif family == "continuous_mean":
        aggregate_count = aggregate["sample_count"]
    else:
        aggregate_count = None
    if aggregate_count is not None and aggregate_count != eligible - missing:
        raise ContractError(
            f"{path}.aggregate denominator must equal analyzable outcomes"
        )
    assignment = closed_object(
        projection["assignment"],
        _VALIDATION_ASSIGNMENT_KEYS,
        f"{path}.assignment",
    )
    for field in ("design", "unit"):
        require_string(assignment[field], f"{path}.assignment.{field}")
    _bool(
        assignment["leakage_detected"],
        f"{path}.assignment.leakage_detected",
    )
    confidence = _finite_number(
        projection["confidence_level"],
        f"{path}.confidence_level",
    )
    if not 0 < confidence < 1:
        raise ContractError(
            f"{path}.confidence_level must be between zero and one"
        )
    _bool(
        projection["permission_confirmed"],
        f"{path}.permission_confirmed",
    )
    _timestamp(
        projection["outcome_accessed_at"],
        f"{path}.outcome_accessed_at",
    )
    projection["aggregate"] = aggregate
    projection["assignment"] = assignment
    projection["limitations"] = limitations
    return projection


def validate_normalized_observation(payload: object) -> dict[str, object]:
    path = "normalized_observation"
    document = _document(
        payload,
        _NORMALIZED_OBSERVATION_KEYS,
        NORMALIZED_OBSERVATION_VERSION,
        path,
    )
    for field in (
        "observation_id",
        "study_id",
        "registration_id",
        "import_id",
        "source_id",
        "platform",
    ):
        require_identifier(document[field], f"{path}.{field}")
    _digest(document["source_sha256"], f"{path}.source_sha256")
    require_string(
        document["source_row_reference"],
        f"{path}.source_row_reference",
    )

    adapter = closed_object(
        document["adapter"], _NORMALIZED_ADAPTER_KEYS, f"{path}.adapter"
    )
    require_identifier(adapter["adapter_id"], f"{path}.adapter.adapter_id")
    require_identifier(
        adapter["adapter_version"], f"{path}.adapter.adapter_version"
    )
    require_enum(
        adapter["maturity"],
        ADAPTER_MATURITY,
        f"{path}.adapter.maturity",
    )

    for field in ("account", "campaign", "ad_group", "ad", "creative"):
        identity = closed_object(
            document[field],
            _NORMALIZED_IDENTITY_KEYS,
            f"{path}.{field}",
        )
        require_identifier(
            identity["platform_id"],
            f"{path}.{field}.platform_id",
        )

    reporting = closed_object(
        document["reporting"],
        _NORMALIZED_REPORTING_KEYS,
        f"{path}.reporting",
    )
    for field in ("start_date", "end_date"):
        value = require_string(
            reporting[field], f"{path}.reporting.{field}"
        )
        try:
            date_value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ContractError(
                f"{path}.reporting.{field} must be an ISO 8601 date"
            ) from exc
        if field == "start_date":
            reporting_start = date_value
        else:
            reporting_end = date_value
    if reporting_start > reporting_end:
        raise ContractError(
            f"{path}.reporting.start_date must not follow end_date"
        )
    for field in ("timezone", "basis"):
        require_string(reporting[field], f"{path}.reporting.{field}")
    for field in ("request_level", "time_increment"):
        if reporting[field] is not None:
            require_string(reporting[field], f"{path}.reporting.{field}")
    segment_grain = require_string_list(
        reporting["segment_grain"],
        f"{path}.reporting.segment_grain",
    )
    if not segment_grain or len(set(segment_grain)) != len(segment_grain):
        raise ContractError(
            f"{path}.reporting.segment_grain must be non-empty and unique"
        )
    require_enum(
        reporting["latency_state"],
        _NORMALIZED_LATENCY_STATES,
        f"{path}.reporting.latency_state",
    )
    _timestamp(reporting["observed_at"], f"{path}.reporting.observed_at")

    attribution = closed_object(
        document["attribution"],
        _NORMALIZED_ATTRIBUTION_KEYS,
        f"{path}.attribution",
    )
    require_string(
        attribution["report_time"], f"{path}.attribution.report_time"
    )
    windows = require_string_list(
        attribution["windows"], f"{path}.attribution.windows"
    )
    if len(set(windows)) != len(windows):
        raise ContractError(
            f"{path}.attribution.windows must not contain duplicates"
        )

    currency = closed_object(
        document["currency"],
        _NORMALIZED_CURRENCY_KEYS,
        f"{path}.currency",
    )
    code = require_string(currency["code"], f"{path}.currency.code")
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ContractError(f"{path}.currency.code must be ISO-style uppercase")
    require_string(currency["basis"], f"{path}.currency.basis")

    spend = closed_object(
        document["spend"], _NORMALIZED_SPEND_KEYS, f"{path}.spend"
    )
    spend_value_text = require_numeric_string_or_number(
        spend["value"], f"{path}.spend.value"
    )
    spend_decimal = require_numeric_string(
        spend["decimal"], f"{path}.spend.decimal"
    )
    spend_source_text = require_numeric_string(
        spend["source_numeric_text"],
        f"{path}.spend.source_numeric_text",
    )
    require_string(spend["source_metric"], f"{path}.spend.source_metric")
    source_unit = require_string(
        spend["source_unit"], f"{path}.spend.source_unit"
    )
    if Decimal(spend_value_text) < 0:
        raise ContractError(f"{path}.spend.value must be non-negative")
    if Decimal(spend_value_text) != Decimal(spend_decimal):
        raise ContractError(
            f"{path}.spend.decimal must equal the numeric spend value"
        )
    if source_unit == "micros":
        micros = Decimal(spend_source_text)
        if (
            micros != micros.to_integral_value()
            or micros < 0
            or micros / Decimal(1_000_000) != Decimal(spend_decimal)
        ):
            raise ContractError(
                f"{path}.spend.source_numeric_text must preserve exact micros"
            )
    elif Decimal(spend_source_text) != Decimal(spend_decimal):
        raise ContractError(
            f"{path}.spend.source_numeric_text must equal spend.decimal"
        )

    exposure = closed_object(
        document["exposure"],
        _NORMALIZED_EXPOSURE_KEYS,
        f"{path}.exposure",
    )
    for field in ("impressions", "clicks"):
        count = closed_object(
            exposure[field],
            _NORMALIZED_COUNT_KEYS,
            f"{path}.exposure.{field}",
        )
        count_value_text = require_numeric_string_or_number(
            count["value"], f"{path}.exposure.{field}.value"
        )
        count_source_text = require_numeric_string(
            count["source_numeric_text"],
            f"{path}.exposure.{field}.source_numeric_text",
        )
        count_value = Decimal(count_value_text)
        if (
            count_value < 0
            or count_value != count_value.to_integral_value()
            or count_value != Decimal(count_source_text)
        ):
            raise ContractError(
                f"{path}.exposure.{field} must preserve a non-negative integral count"
            )

    outcome = closed_object(
        document["outcome"],
        _NORMALIZED_OUTCOME_KEYS,
        f"{path}.outcome",
    )
    require_identifier(outcome["metric_id"], f"{path}.outcome.metric_id")
    require_string(
        outcome["source_metric"], f"{path}.outcome.source_metric"
    )
    value_state = require_enum(
        outcome["value_state"],
        _NORMALIZED_VALUE_STATES,
        f"{path}.outcome.value_state",
    )
    require_string(
        outcome["omitted_zero_behavior"],
        f"{path}.outcome.omitted_zero_behavior",
    )
    missing_states = {"null", "absent", "suppressed", "omitted_zero"}
    if value_state in missing_states:
        if any(
            outcome[field] is not None
            for field in ("value", "decimal", "source_numeric_text")
        ):
            raise ContractError(
                f"{path}.outcome missing-state values must be null"
            )
    else:
        outcome_value_text = require_numeric_string_or_number(
            outcome["value"], f"{path}.outcome.value"
        )
        outcome_decimal = require_numeric_string(
            outcome["decimal"], f"{path}.outcome.decimal"
        )
        outcome_source_text = require_numeric_string(
            outcome["source_numeric_text"],
            f"{path}.outcome.source_numeric_text",
        )
        outcome_value = Decimal(outcome_value_text)
        if outcome_value < 0:
            raise ContractError(f"{path}.outcome.value must be non-negative")
        if (
            outcome_value != Decimal(outcome_decimal)
            or outcome_value != Decimal(outcome_source_text)
        ):
            raise ContractError(
                f"{path}.outcome.source_numeric_text must equal the preserved value"
            )
        is_integral = outcome_value == outcome_value.to_integral_value()
        if value_state == "observed_zero":
            canonical = outcome_value == 0
        elif value_state == "fractional":
            canonical = outcome_value != 0 and not is_integral
        elif value_state == "observed":
            canonical = outcome_value > 0 and is_integral
        else:
            canonical = value_state in {"modeled", "estimated"}
        if not canonical:
            raise ContractError(
                f"{path}.outcome.value_state is not canonical for its value"
            )

    semantics_path = f"{path}.platform_semantics"
    semantics = closed_object(
        document["platform_semantics"],
        _NORMALIZED_PLATFORM_SEMANTICS_KEYS,
        semantics_path,
    )
    currency_relationship = require_enum(
        semantics["currency_relationship"],
        _NORMALIZED_CURRENCY_RELATIONSHIPS,
        f"{semantics_path}.currency_relationship",
    )
    billed_currency = semantics["billed_currency"]
    if currency_relationship == "not_applicable":
        if billed_currency is not None:
            raise ContractError(
                f"{semantics_path}.currency_relationship requires "
                "billed_currency to be null"
            )
    else:
        billed_code = require_string(
            billed_currency,
            f"{semantics_path}.billed_currency",
        )
        if not re.fullmatch(r"[A-Z]{3}", billed_code):
            raise ContractError(
                f"{semantics_path}.billed_currency must be ISO-style uppercase"
            )
        same_currency = billed_code == code
        expected_relationship = (
            "local_currency_equals_billed_currency"
            if same_currency
            else "local_currency_distinct_from_billed_currency"
        )
        if currency_relationship != expected_relationship:
            raise ContractError(
                f"{semantics_path}.currency_relationship does not match "
                "local and billed currency"
            )

    require_enum(
        semantics["privacy_review_state"],
        _NORMALIZED_PRIVACY_REVIEW_STATES,
        f"{semantics_path}.privacy_review_state",
    )
    require_enum(
        semantics["demographic_truncation_state"],
        _NORMALIZED_DEMOGRAPHIC_TRUNCATION_STATES,
        f"{semantics_path}.demographic_truncation_state",
    )
    require_enum(
        semantics["click_semantic"],
        _NORMALIZED_CLICK_SEMANTICS,
        f"{semantics_path}.click_semantic",
    )
    optimization_event = semantics["optimization_event"]
    if optimization_event is not None:
        require_identifier(
            optimization_event,
            f"{semantics_path}.optimization_event",
        )
    delivery_state = require_enum(
        semantics["delivery_state"],
        _NORMALIZED_DELIVERY_STATES,
        f"{semantics_path}.delivery_state",
    )
    skan_state = require_enum(
        semantics["skan_state"],
        _NORMALIZED_SKAN_STATES,
        f"{semantics_path}.skan_state",
    )
    if (delivery_state == "not_applicable") != (
        skan_state == "not_applicable"
    ):
        raise ContractError(
            f"{semantics_path} delivery and SKAN applicability must match"
        )
    if skan_state == "skan_delayed" and delivery_state != "delayed":
        raise ContractError(
            f"{semantics_path}.skan_state requires delayed delivery"
        )

    search_term_state = require_enum(
        semantics["search_term_state"],
        _NORMALIZED_SEARCH_TERM_STATES,
        f"{semantics_path}.search_term_state",
    )
    search_term_id = semantics["search_term_id"]
    if search_term_state in {"not_applicable", "not_reported"}:
        if search_term_id is not None:
            raise ContractError(
                f"{semantics_path}.search_term_id must be null when "
                "the search_term state has no row value"
            )
    else:
        if search_term_state == "unknown":
            if search_term_id != "-1":
                raise ContractError(
                    f"{semantics_path} unknown search_term requires "
                    "the -1 sentinel"
                )
        else:
            checked_search_term_id = require_identifier(
                search_term_id,
                f"{semantics_path}.search_term_id",
            )
            if checked_search_term_id == "-1":
                raise ContractError(
                    f"{semantics_path} observed search_term cannot use "
                    "the -1 sentinel"
                )
    document["validation_projection"] = _validate_validation_projection(
        document["validation_projection"],
        path=f"{path}.validation_projection",
    )
    projection = document["validation_projection"]
    if projection["status"] == "available":
        target_field = {
            "binary_proportion": "success_count",
            "continuous_mean": "mean",
            "event_rate": "event_count",
        }[projection["metric_family"]]
        reported = outcome["value"]
        if reported is None or Decimal(str(reported)) != Decimal(
            str(projection["aggregate"][target_field])
        ):
            raise ContractError(
                f"{path}.validation_projection aggregate must preserve "
                "the exact reported outcome value"
            )
    return _self_hash(
        document,
        "normalized_observation_sha256",
        path,
    )


def validate_observation_binding(payload: object) -> dict[str, object]:
    path = "observation_binding"
    document = _document(
        payload, _OBSERVATION_BINDING_KEYS, OBSERVATION_BINDING_VERSION, path
    )
    for field in (
        "observation_id", "registration_id", "delivery_mapping_id",
        "platform", "platform_campaign_id", "platform_ad_group_id",
        "platform_ad_id", "platform_creative_id", "block_id", "study_id",
        "arm_id", "batch_id", "creative_id", "variant_id", "run_id",
        "metric_id",
    ):
        require_identifier(document[field], f"{path}.{field}")
    for field in (
        "registration_sha256", "normalized_observation_sha256",
        "delivery_map_sha256", "delivery_mapping_sha256", "asset_sha256",
        "panel_sha256", "package_sha256", "result_sha256",
        "source_sha256", "campaign_plan_sha256",
    ):
        _digest(document[field], f"{path}.{field}")
    segment_ids = require_string_list(
        document["segment_ids"], f"{path}.segment_ids"
    )
    if segment_ids != sorted(segment_ids) or len(segment_ids) != len(
        set(segment_ids)
    ):
        raise ContractError(
            f"{path}.segment_ids must be unique and canonically sorted"
        )
    for field in (
        "measurement_window", "attribution_window",
        "source_row_reference",
    ):
        require_string(document[field], f"{path}.{field}")
    require_enum(
        document["evidence_status"],
        EVIDENCE_STATUSES,
        f"{path}.evidence_status",
    )
    document["segment_ids"] = segment_ids
    delivery_projection = {
        "mapping_id": document["delivery_mapping_id"],
        "platform": document["platform"],
        "platform_campaign_id": document["platform_campaign_id"],
        "platform_ad_group_id": document["platform_ad_group_id"],
        "platform_ad_id": document["platform_ad_id"],
        "platform_creative_id": document["platform_creative_id"],
        "block_id": document["block_id"],
        "study_id": document["study_id"],
        "arm_id": document["arm_id"],
        "batch_id": document["batch_id"],
        "segment_ids": document["segment_ids"],
        "creative_id": document["creative_id"],
        "variant_id": document["variant_id"],
        "asset_sha256": document["asset_sha256"],
        "campaign_plan_sha256": document["campaign_plan_sha256"],
    }
    if document["delivery_mapping_sha256"] != sha256_json(
        delivery_projection
    ):
        raise ContractError(
            f"{path}.delivery_mapping_sha256 does not bind the exact mapping"
        )
    return _self_hash(
        document, "observation_binding_sha256", path
    )


def validate_readiness_report(payload: object) -> dict[str, object]:
    document = _document(payload, _READINESS_KEYS, READINESS_VERSION, "readiness")
    for field in ("study_id", "import_id"):
        require_identifier(document[field], f"readiness.{field}")
    evidence_status = require_enum(document["evidence_status"], EVIDENCE_STATUSES, "readiness.evidence_status")
    operational_status = require_enum(document["operational_status"], OPERATIONAL_STATUSES, "readiness.operational_status")
    maturity = require_enum(document["adapter_maturity"], ADAPTER_MATURITY, "readiness.adapter_maturity")
    require_string_list(document["reasons"], "readiness.reasons")
    if operational_status == "contract_ready" and maturity != "export_verified":
        raise ContractError("contract_ready requires an export_verified exact adapter variant")
    if evidence_status == "blocked" or maturity == "blocked":
        expected = "blocked"
    elif evidence_status == "descriptive_only":
        expected = "descriptive_only"
    elif maturity == "export_verified":
        expected = "contract_ready"
    else:
        expected = "incomplete"
    if operational_status != expected:
        raise ContractError(f"readiness.operational_status must be derived as {expected}")
    return _self_hash(document, "readiness_sha256", "readiness")


def validate_import_event(payload: object) -> dict[str, object]:
    document = _document(payload, _IMPORT_EVENT_KEYS, IMPORT_EVENT_VERSION, "import_event")
    for field in ("import_id", "study_id", "imported_by"):
        require_identifier(document[field], f"import_event.{field}")
    _timestamp(document["imported_at"], "import_event.imported_at")
    _digest(document["source_manifest_sha256"], "import_event.source_manifest_sha256")
    observation_ids = require_string_list(document["observation_ids"], "import_event.observation_ids")
    if not observation_ids:
        raise ContractError("import_event.observation_ids must not be empty")
    return _self_hash(document, "import_event_sha256", "import_event")
