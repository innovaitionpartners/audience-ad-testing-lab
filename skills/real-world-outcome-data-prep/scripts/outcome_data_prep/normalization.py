"""Operation-local authentication of normalized aggregate outcome batches."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from types import MappingProxyType
import weakref

from .adapters.base import AdapterResult
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
from .capabilities import AdapterCapability, load_capability_registry
from .common import ContractError, sha256_json
from .container_safety import ContainerInventory
from .contracts import (
    validate_normalized_observation,
    validate_source_governance_input,
    validate_source_manifest,
)
from .privacy import (
    AdapterAdmissionValidation,
    AdmittedSource,
    authenticate_admitted_source,
    pre_scan_obvious_privacy,
)
from .study_authority import (
    AuthenticatedStudy,
    StudyAuthority,
    authenticate_import_event,
    verify_study_authority,
)


class NormalizedBatchError(ContractError):
    """A normalized batch failed its operation-local authority boundary."""


class AuthenticatedNormalizedBatch:
    """Non-serializable proof of one source reinspection and adapter rerun."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        raise NormalizedBatchError(
            "AuthenticatedNormalizedBatch can only be minted by "
            "authenticate_normalized_batch"
        )


class EffectiveEvidenceStatusAuthority:
    """Non-serializable proof of one authenticated ledger replay."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        raise NormalizedBatchError(
            "EffectiveEvidenceStatusAuthority can only be minted from "
            "authenticated ledger replay"
        )


@dataclass(frozen=True)
class _EffectiveStatusState:
    authenticated_study: AuthenticatedStudy
    study_authority: StudyAuthority
    evidence_status: str
    ledger_digest: str | None


_EFFECTIVE_STATUS_STATES: weakref.WeakKeyDictionary[
    EffectiveEvidenceStatusAuthority, _EffectiveStatusState
] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class _BatchInputs:
    authenticated_study: AuthenticatedStudy
    study_authority: StudyAuthority
    effective_status_authority: EffectiveEvidenceStatusAuthority
    source_inventory: ContainerInventory
    admission_validation: AdapterAdmissionValidation
    admitted_source: AdmittedSource
    governance_input: dict[str, object]
    adapter_context: dict[str, object] | None
    profile: GenericAdmissionProfile | None
    source_manifest: dict[str, object]
    import_event_envelope: dict[str, object]


@dataclass(frozen=True)
class _BatchState:
    inputs: _BatchInputs
    rows: tuple[dict[str, object], ...]
    batch_sha256: str


_BATCH_STATES: weakref.WeakKeyDictionary[
    AuthenticatedNormalizedBatch, _BatchState
] = weakref.WeakKeyDictionary()


def authenticate_effective_evidence_status(
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> EffectiveEvidenceStatusAuthority:
    """Mint an operation-local status capability from live ledger replay."""

    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    # Imported lazily because publication validates normalized handoffs and
    # therefore imports this module during initialization.
    from .publication import replay_authenticated_ledger

    replayed = replay_authenticated_ledger(
        study.study_root, authority=study_authority
    )
    if replayed.ledger_digest != study.ledger_digest:
        raise NormalizedBatchError(
            "authenticated ledger replay does not match the sealed study view"
        )
    capability = object.__new__(EffectiveEvidenceStatusAuthority)
    _EFFECTIVE_STATUS_STATES[capability] = _EffectiveStatusState(
        authenticated_study=authenticated_study,
        study_authority=study_authority,
        evidence_status=replayed.current_evidence_status,
        ledger_digest=replayed.ledger_digest,
    )
    return capability


def effective_evidence_status_snapshot(
    capability: object,
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> tuple[str, str | None]:
    """Read one already-authenticated operation-local status snapshot."""

    if not isinstance(capability, EffectiveEvidenceStatusAuthority):
        raise NormalizedBatchError(
            "effective evidence status authority is required"
        )
    state = _EFFECTIVE_STATUS_STATES.get(capability)
    if state is None or (
        state.authenticated_study is not authenticated_study
        or state.study_authority is not study_authority
    ):
        raise NormalizedBatchError(
            "effective evidence status authority is not study-bound"
        )
    return state.evidence_status, state.ledger_digest


def verify_effective_evidence_status(
    capability: object,
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> str:
    """Reauthenticate a status capability against the current ledger."""

    evidence_status, ledger_digest = effective_evidence_status_snapshot(
        capability,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    from .publication import replay_authenticated_ledger

    replayed = replay_authenticated_ledger(
        study.study_root, authority=study_authority
    )
    if (
        replayed.ledger_digest != ledger_digest
        or replayed.current_evidence_status != evidence_status
    ):
        raise NormalizedBatchError(
            "effective evidence status changed during the operation"
        )
    return evidence_status


@dataclass(frozen=True)
class _AdapterImplementation:
    adapter_type: type
    init: object
    inventory: object
    validate: object
    admission_validation: object
    normalize: object
    approved_profile: object | None = None


def _canonical_input(value: object) -> object:
    """Project trusted inputs into deterministic JSON for mutation guards."""

    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_input(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_input(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_input(item) for item in value]
    if isinstance(value, (set, frozenset)):
        projected = [_canonical_input(item) for item in value]
        return sorted(projected, key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise NormalizedBatchError(
        f"trusted adapter input type is unsupported: {type(value).__name__}"
    )


def _input_digest(value: object) -> str:
    return sha256_json(_canonical_input(value))


def _guarded_adapter_call(
    function: object,
    adapter: object,
    /,
    *args: object,
    **kwargs: object,
) -> object:
    """Invoke adapter code with isolated inputs and reject any mutation."""

    isolated_args = deepcopy(args)
    isolated_kwargs = deepcopy(kwargs)
    call_inputs: dict[str, object] = {
        "args": isolated_args,
        "kwargs": isolated_kwargs,
    }
    before = _input_digest(call_inputs)
    receiver_before = _input_digest(vars(adapter))
    try:
        result = function(  # type: ignore[operator]
            adapter, *isolated_args, **isolated_kwargs
        )
    except Exception as exc:
        if (
            _input_digest(call_inputs) != before
            or _input_digest(vars(adapter)) != receiver_before
        ):
            raise NormalizedBatchError(
                "adapter mutated isolated trusted input or receiver state"
            ) from exc
        raise
    if (
        _input_digest(call_inputs) != before
        or _input_digest(vars(adapter)) != receiver_before
    ):
        raise NormalizedBatchError(
            "adapter mutated isolated trusted input or receiver state"
        )
    return result


def _implementation(
    adapter_type: type, *, generic: bool = False
) -> _AdapterImplementation:
    return _AdapterImplementation(
        adapter_type=adapter_type,
        init=adapter_type.__init__,
        inventory=adapter_type.inventory,
        validate=adapter_type.validate,
        admission_validation=adapter_type.admission_validation,
        normalize=adapter_type.normalize,
        approved_profile=(adapter_type.approved_profile if generic else None),
    )


_IMPLEMENTATIONS_BY_ID = MappingProxyType({
    "meta-insights-api-json-v1": _implementation(MetaInsightsAdapter),
    "google-ads-api-v23-ad-daily-json": _implementation(GoogleAdsAdapter),
    "linkedin-ads-reporting-api-json-v1": _implementation(LinkedInAdsAdapter),
    "tiktok-reporting-api-json-v1": _implementation(TikTokAdsAdapter),
    "dv360-bid-manager-v2-standard-csv-v1": _implementation(DV360Adapter),
    "dv360-bid-manager-v2-standard-xlsx-v1": _implementation(DV360Adapter),
    "trade-desk-report-template-csv-v1": _implementation(TradeDeskAdapter),
    "trade-desk-report-template-tsv-v1": _implementation(TradeDeskAdapter),
    "trade-desk-report-type-xlsx-v1": _implementation(TradeDeskAdapter),
    "xandr-advertiser-analytics-csv-v1": _implementation(XandrAdapter),
    "xandr-advertiser-analytics-excel-tsv-v1": _implementation(XandrAdapter),
    "xandr-advertiser-analytics-xlsx-v1": _implementation(XandrAdapter),
    "generic-dsp-mapping-v1": _implementation(
        GenericProgrammaticAdapter, generic=True
    ),
})
_CAPABILITY_REGISTRY = tuple(
    load_capability_registry(
        Path(__file__).resolve().parents[2]
        / "references"
        / "platform-capabilities.json"
    )
)
_CAPABILITIES_BY_ID = MappingProxyType({
    capability.adapter_id: capability for capability in _CAPABILITY_REGISTRY
})


def _timestamp(value: object, path: str) -> datetime:
    if not isinstance(value, str):
        raise NormalizedBatchError(f"{path} must be a timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalizedBatchError(f"{path} must be a timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise NormalizedBatchError(f"{path} must be timezone-aware")
    return result


def _physical_rows(
    inventory: ContainerInventory, headers: tuple[str, ...]
) -> list[tuple[str, dict[str, str]]]:
    table_order = {table: index for index, table in enumerate(inventory.tables)}
    values: dict[tuple[str, int], dict[str, str]] = {}
    for cell in inventory.cells:
        key = (cell.table, cell.row_number)
        row = values.setdefault(key, {})
        if cell.column_name in row:
            raise NormalizedBatchError("admitted source has duplicate cells")
        row[cell.column_name] = cell.value
    ordered: list[tuple[str, dict[str, str]]] = []
    for (table, row_number), row in sorted(
        values.items(),
        key=lambda item: (
            table_order.get(item[0][0], len(table_order)),
            item[0][1],
        ),
    ):
        if set(row) != set(headers):
            raise NormalizedBatchError(
                "admitted source row does not match authenticated profile"
            )
        ordered.append((f"{table}:{row_number}", row))
    if len(ordered) != inventory.row_count:
        raise NormalizedBatchError("admitted source row inventory is incomplete")
    return ordered


def _resolve_implementation(
    admission: AdapterAdmissionValidation,
    _implementations: Mapping[str, _AdapterImplementation] = (
        _IMPLEMENTATIONS_BY_ID
    ),
    _capabilities: Mapping[str, AdapterCapability] = _CAPABILITIES_BY_ID,
) -> tuple[_AdapterImplementation, AdapterCapability, object]:
    capability = _capabilities.get(admission.adapter_id)
    if capability is None:
        raise NormalizedBatchError("adapter capability is not registered")
    if (
        capability.adapter_version != admission.adapter_version
        or capability.maturity == "blocked"
    ):
        raise NormalizedBatchError(
            capability.availability_reason or "adapter capability is unavailable"
        )
    implementation = _implementations.get(capability.adapter_id)
    if implementation is None:
        raise NormalizedBatchError(
            "registered capability has no semantic implementation"
        )
    adapter_type = implementation.adapter_type
    expected = {
        "__init__": implementation.init,
        "inventory": implementation.inventory,
        "validate": implementation.validate,
        "admission_validation": implementation.admission_validation,
        "normalize": implementation.normalize,
    }
    if implementation.approved_profile is not None:
        expected["approved_profile"] = implementation.approved_profile
    if any(
        getattr(adapter_type, name) is not method
        for name, method in expected.items()
    ):
        raise NormalizedBatchError(
            "registered adapter implementation was replaced"
        )
    adapter = object.__new__(adapter_type)
    capability_digest = _input_digest(capability)
    isolated_capability = deepcopy(capability)
    implementation.init(adapter, isolated_capability)  # type: ignore[operator]
    if (
        _input_digest(capability) != capability_digest
        or _input_digest(isolated_capability) != capability_digest
    ):
        raise NormalizedBatchError(
            "adapter initializer mutated trusted capability data"
        )
    if type(adapter) is not adapter_type or adapter.capability != capability:
        raise NormalizedBatchError("registered adapter construction failed")
    return implementation, capability, adapter


def _json_cell(value: str, path: str) -> object:
    if not value.startswith(("{", "[")):
        raise NormalizedBatchError(f"{path} must be canonical nested JSON")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise NormalizedBatchError(f"{path} is malformed") from exc
    if json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) != value:
        raise NormalizedBatchError(f"{path} is not canonical")
    return parsed


def _closed_context(
    inputs: _BatchInputs,
    capability: AdapterCapability,
) -> tuple[dict[str, object], dict[str, object]]:
    raw = inputs.adapter_context
    if not isinstance(raw, Mapping) or set(raw) != {
        "adapter_registration",
        "reporting_metadata",
    }:
        raise NormalizedBatchError("adapter normalization context is not closed")
    context = deepcopy(dict(raw))
    if sha256_json(context) != inputs.admission_validation.profile_sha256:
        raise NormalizedBatchError(
            "adapter normalization context is not admission-bound"
        )
    registration = context["adapter_registration"]
    metadata = context["reporting_metadata"]
    if not isinstance(registration, Mapping) or not isinstance(metadata, Mapping):
        raise NormalizedBatchError("adapter normalization context is invalid")
    registration = deepcopy(dict(registration))
    metadata = deepcopy(dict(metadata))
    metric = inputs.authenticated_study.registration["primary_metric"]
    assert isinstance(metric, Mapping)
    expected_identity = {
        "study_id": inputs.authenticated_study.delivery_map["study_id"],
        "registration_id": inputs.authenticated_study.registration[
            "registration_id"
        ],
        "metric_id": metric["name"],
    }
    if any(registration.get(key) != value for key, value in expected_identity.items()):
        raise NormalizedBatchError(
            "adapter registration does not bind the authenticated study"
        )
    if capability.adapter_id != inputs.admission_validation.adapter_id:
        raise NormalizedBatchError("adapter context capability is mismatched")
    return registration, metadata


def _exact_documents(
    *,
    inputs: _BatchInputs,
    capability: AdapterCapability,
    source_id: str,
    import_id: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, str]]]:
    registration, metadata = _closed_context(inputs, capability)
    headers = tuple(
        capability.identity_fields
        + capability.required_fields
        + capability.metric_fields
    )
    physical_rows = _physical_rows(inputs.source_inventory, headers)
    logical_rows: list[dict[str, object]] = []
    physical: dict[str, dict[str, str]] = {}
    for reference, values in physical_rows:
        physical[reference] = values
        row: dict[str, object]
        if capability.platform == "meta_ads":
            row = dict(values)
            row["actions"] = _json_cell(values["actions"], "actions")
        elif capability.platform == "google_ads":
            row = {
                "customer": {"id": values["customer.id"]},
                "campaign": {"id": values["campaign.id"]},
                "ad_group": {"id": values["ad_group.id"]},
                "ad_group_ad": {"ad": {"id": values["ad_group_ad.ad.id"]}},
                "segments": {"date": values["segments.date"]},
                "metrics": {
                    field.removeprefix("metrics."): values[field]
                    for field in capability.required_fields
                    + capability.metric_fields
                },
            }
        elif capability.platform == "linkedin_ads":
            row = {
                "pivotValues": [
                    values["account"],
                    values["campaign"],
                    values["creative"],
                ],
                "dateRange": {
                    "start": values["dateRange.start"],
                    "end": values["dateRange.end"],
                },
                **{
                    field: values[field]
                    for field in capability.required_fields
                    + capability.metric_fields
                },
            }
        else:
            row = dict(values)
        row["source_row_reference"] = reference
        logical_rows.append(row)
    payload = {
        "source_id": source_id,
        "import_id": import_id,
        "source_sha256": inputs.admitted_source.source_sha256,
        "reporting_metadata": metadata,
        "rows": logical_rows,
    }
    return registration, payload, physical


def _source_manifest_binding(
    manifest: Mapping[str, object], admitted: AdmittedSource
) -> tuple[dict[str, object], str]:
    checked = validate_source_manifest(manifest)
    sources = checked["sources"]
    assert isinstance(sources, list)
    matches: list[dict[str, object]] = []
    for raw in sources:
        if not isinstance(raw, Mapping) or set(raw) != {
            "source_id",
            "source_sha256",
            "admission_sha256",
        }:
            raise NormalizedBatchError(
                "source manifest source binding is not closed"
            )
        source = dict(raw)
        if (
            source["source_sha256"] == admitted.source_sha256
            and source["admission_sha256"] == admitted.admission_sha256
        ):
            matches.append(source)
    if len(matches) != 1:
        raise NormalizedBatchError(
            "source manifest does not bind the exact admitted source"
        )
    return checked, str(matches[0]["source_id"])


def _generic_documents(
    *,
    study: AuthenticatedStudy,
    profile: GenericAdmissionProfile,
    inventory: ContainerInventory,
    admitted: AdmittedSource,
    governance: Mapping[str, object],
    source_id: str,
    import_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    mapping = dict(profile.source_to_canonical)
    physical = _physical_rows(inventory, profile.headers)
    currency_source = next(
        (source for source, target in mapping.items() if target == "currency"),
        None,
    )
    if currency_source is None:
        raise NormalizedBatchError(
            "generic admitted source must map an explicit currency field"
        )
    currencies = {row[currency_source] for _, row in physical}
    if len(currencies) != 1 or not next(iter(currencies)):
        raise NormalizedBatchError(
            "generic admitted source currency is not one exact value"
        )
    metric = study.registration["primary_metric"]
    assert isinstance(metric, Mapping)
    attribution_window = str(metric["attribution_window"])
    conversion_source = next(
        source
        for source, target in mapping.items()
        if target == "conversion_value"
    )
    registration = {
        "study_id": study.delivery_map["study_id"],
        "registration_id": study.registration["registration_id"],
        "metric_id": metric["name"],
        "registered_source_metric": conversion_source,
        "outcomes_accessed": True,
        "sealed_delivery_map": deepcopy(study.delivery_map),
        "approved_mapping": mapping,
        "approved_mapping_profile_id": profile.mapping_profile_id,
        "approved_header_fingerprint": profile.header_fingerprint,
        "approved_source_container": profile.source_container,
        "time_basis": "authenticated_source_reporting_day",
        "currency": next(iter(currencies)),
        "attribution_semantics": "registered_attribution_window",
        "attribution_windows": [attribution_window],
    }
    payload = {
        "source_id": source_id,
        "import_id": import_id,
        "source_sha256": admitted.source_sha256,
        "mapping": mapping,
        "reporting_metadata": {
            "source_container": profile.source_container,
            "source_platform": profile.source_platform,
            "headers": list(profile.headers),
            "header_fingerprint": profile.header_fingerprint,
            "mapping_profile_id": profile.mapping_profile_id,
            "stable_id_targets": list(profile.stable_id_targets),
            "timezone": "UTC",
            "time_basis": "authenticated_source_reporting_day",
            "currency": next(iter(currencies)),
            "attribution_semantics": "registered_attribution_window",
            "attribution_windows": [attribution_window],
            "conversion_metric": conversion_source,
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
            "observed_at": governance["export_timestamp"],
            "omitted_zero_behavior": (
                "omitted_metrics_are_unknown_not_zero"
            ),
        },
        "rows": [
            {"source_row_reference": reference, "values": deepcopy(row)}
            for reference, row in physical
        ],
    }
    return registration, payload


def _integer(value: object, path: str) -> int:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NormalizedBatchError(f"{path} is not numeric") from exc
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise NormalizedBatchError(f"{path} must be a nonnegative count")
    return int(parsed)


def _number(value: object, path: str, *, positive: bool = False) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NormalizedBatchError(f"{path} is not numeric") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise NormalizedBatchError(f"{path} is outside its valid range")
    return float(parsed)


def _decimal_json_number(value: Decimal) -> int | float:
    """Render one physical decimal without trusting adapter helpers."""

    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _outcome_value_state(value: Decimal, quality: str) -> str:
    """Derive the closed outcome state independently of adapter helpers."""

    if quality != "observed":
        return quality
    if value == 0:
        return "observed_zero"
    if value != value.to_integral_value():
        return "fractional"
    return "observed"


def _platform_semantics(
    *,
    billed_currency: str | None = None,
    currency_relationship: str = "not_applicable",
    privacy_review_state: str = "not_applicable",
    demographic_truncation_state: str = "not_applicable",
    click_semantic: str = "not_applicable",
    optimization_event: str | None = None,
    delivery_state: str = "not_applicable",
    skan_state: str = "not_applicable",
    search_term_id: str | None = None,
    search_term_state: str = "not_applicable",
) -> dict[str, object]:
    """Build the verifier's closed semantics allowlist independently."""

    return {
        "billed_currency": billed_currency,
        "currency_relationship": currency_relationship,
        "privacy_review_state": privacy_review_state,
        "demographic_truncation_state": demographic_truncation_state,
        "click_semantic": click_semantic,
        "optimization_event": optimization_event,
        "delivery_state": delivery_state,
        "skan_state": skan_state,
        "search_term_id": search_term_id,
        "search_term_state": search_term_state,
    }


def _row_path(row: Mapping[str, object], *parts: str) -> object:
    value: object = row
    for part in parts:
        if not isinstance(value, Mapping) or part not in value:
            raise NormalizedBatchError(
                "normalized adapter result omits a consequential field"
            )
        value = value[part]
    return value


def _selected_physical_fields(
    *,
    capability: AdapterCapability,
    registration: Mapping[str, object],
    values: Mapping[str, str],
) -> dict[str, object]:
    platform = capability.platform
    if platform == "meta_ads":
        key = str(registration["conversion_event_key"])
        actions = _json_cell(values["actions"], "actions")
        if not isinstance(actions, list):
            raise NormalizedBatchError("Meta actions must be an array")
        matches = [
            item for item in actions
            if isinstance(item, Mapping) and item.get("action_type") == key
        ]
        if len(matches) != 1 or "value" not in matches[0]:
            raise NormalizedBatchError(
                "registered Meta outcome is not physically unique"
            )
        return {
            "ids": (
                values["account_id"], values["campaign_id"],
                values["adset_id"], values["ad_id"], values["ad_id"],
            ),
            "dates": (values["date_start"], values["date_stop"]),
            "impressions": values["impressions"],
            "clicks": values["clicks"],
            "spend": values["spend"],
            "spend_metric": "spend",
            "outcome": str(matches[0]["value"]),
            "outcome_metric": key,
        }
    if platform == "google_ads":
        metric = str(registration["registered_source_metric"])
        field = "metrics." + metric
        return {
            "ids": (
                values["customer.id"], values["campaign.id"],
                values["ad_group.id"], values["ad_group_ad.ad.id"],
                values["ad_group_ad.ad.id"],
            ),
            "dates": (values["segments.date"], values["segments.date"]),
            "impressions": values["metrics.impressions"],
            "clicks": values["metrics.clicks"],
            "spend": values["metrics.cost_micros"],
            "spend_metric": "cost_micros",
            "outcome": values[field],
            "outcome_metric": metric,
        }
    if platform == "linkedin_ads":
        metric = str(registration["conversion_source_field"])
        return {
            "ids": (
                values["account"], values["campaign"], "not_applicable",
                values["creative"], values["creative"],
            ),
            "dates": (values["dateRange.start"], values["dateRange.end"]),
            "impressions": values["impressions"],
            "clicks": values["clicks"],
            "spend": values["costInLocalCurrency"],
            "spend_metric": "costInLocalCurrency",
            "outcome": values[metric],
            "outcome_metric": metric,
        }
    if platform == "tiktok_ads":
        metric = str(registration["registered_source_metric"])
        click_metric = str(registration["registered_click_metric"])
        return {
            "ids": (
                values["advertiser_id"], values["campaign_id"],
                values["adgroup_id"], values["ad_id"], values["ad_id"],
            ),
            "dates": (values["stat_time_day"], values["stat_time_day"]),
            "impressions": values["impressions"],
            "clicks": values[click_metric],
            "spend": values["spend"],
            "spend_metric": "spend",
            "outcome": values[metric],
            "outcome_metric": metric,
        }
    metric = str(registration["registered_source_metric"])
    if platform == "dv360":
        identity = (
            "Advertiser ID", "Campaign ID", "Line Item ID", "Creative ID",
            "Creative ID", "Date",
        )
    elif platform == "the_trade_desk":
        identity_fields = capability.identity_fields
        identity = (
            identity_fields[0], identity_fields[1], identity_fields[2],
            identity_fields[3], identity_fields[3], identity_fields[4],
        )
    elif platform == "xandr":
        identity_fields = capability.identity_fields
        identity = (
            identity_fields[0], identity_fields[1], identity_fields[3],
            identity_fields[4], identity_fields[4], identity_fields[5],
        )
    else:
        raise NormalizedBatchError("physical verification is not implemented")
    return {
        "ids": tuple(values[field] for field in identity[:5]),
        "dates": (values[identity[5]], values[identity[5]]),
        "impressions": values[capability.required_fields[0]],
        "clicks": values[capability.required_fields[1]],
        "spend": values[capability.required_fields[2]],
        "spend_metric": capability.required_fields[2],
        "outcome": values[metric],
        "outcome_metric": metric,
    }


def _verify_generic_row(
    *,
    row: Mapping[str, object],
    values: Mapping[str, str],
    reference: str,
    mapping: Mapping[str, str],
    capability: AdapterCapability,
    registration: Mapping[str, object],
    metadata: Mapping[str, object],
    admitted: AdmittedSource,
    source_id: str,
    import_id: str,
) -> None:
    """Reconstruct every consequential generic value from admitted facts."""

    reverse = {target: source for source, target in mapping.items()}
    middle_target = next(
        target for target in ("line_item_id", "ad_group_id") if target in reverse
    )
    creative_target = "creative_id" if "creative_id" in reverse else "ad_id"
    campaign_id = values[reverse["campaign_id"]]
    middle_id = values[reverse[middle_target]]
    creative_id = values[reverse[creative_target]]
    ad_id = values[reverse["ad_id"]] if "ad_id" in reverse else creative_id
    reporting_date = values[reverse["date"]]
    currency_code = values[reverse["currency"]]
    if currency_code != metadata["currency"]:
        raise NormalizedBatchError("generic currency is not physical")
    impressions_text = values[reverse["impressions"]]
    clicks_text = values[reverse["clicks"]]
    spend_text = values[reverse["spend"]]
    outcome_text = values[reverse["conversion_value"]]
    impressions = Decimal(impressions_text)
    clicks = Decimal(clicks_text)
    spend = Decimal(spend_text)
    outcome = Decimal(outcome_text)
    observation_id = "observation-" + sha256_json({
        "adapter_id": capability.adapter_id,
        "source_sha256": admitted.source_sha256,
        "source_row_reference": reference,
        "metric_id": registration["metric_id"],
    }).removeprefix("sha256:")
    expected = {
        "schema_version": "normalized-outcome-observation-v1",
        "observation_id": observation_id,
        "study_id": registration["study_id"],
        "registration_id": registration["registration_id"],
        "import_id": import_id,
        "source_id": source_id,
        "source_sha256": admitted.source_sha256,
        "source_row_reference": reference,
        "platform": capability.platform,
        "adapter": {
            "adapter_id": capability.adapter_id,
            "adapter_version": capability.adapter_version,
            "maturity": capability.maturity,
        },
        "account": {"platform_id": "not_applicable"},
        "campaign": {"platform_id": campaign_id},
        "ad_group": {"platform_id": middle_id},
        "ad": {"platform_id": ad_id},
        "creative": {"platform_id": creative_id},
        "reporting": {
            "start_date": reporting_date,
            "end_date": reporting_date,
            "timezone": metadata["timezone"],
            "basis": metadata["time_basis"],
            "request_level": creative_target.removesuffix("_id"),
            "time_increment": "1",
            "segment_grain": list(mapping.values()),
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        },
        "attribution": {
            "report_time": metadata["attribution_semantics"],
            "windows": metadata["attribution_windows"],
        },
        "currency": {
            "code": currency_code,
            "basis": capability.currency_basis,
        },
        "spend": {
            "value": _decimal_json_number(spend),
            "decimal": spend_text,
            "source_numeric_text": spend_text,
            "source_metric": reverse["spend"],
            "source_unit": "declared_currency",
        },
        "exposure": {
            "impressions": {
                "value": _decimal_json_number(impressions),
                "source_numeric_text": impressions_text,
            },
            "clicks": {
                "value": _decimal_json_number(clicks),
                "source_numeric_text": clicks_text,
            },
        },
        "outcome": {
            "metric_id": registration["metric_id"],
            "source_metric": reverse["conversion_value"],
            "value": _decimal_json_number(outcome),
            "decimal": outcome_text,
            "source_numeric_text": outcome_text,
            "value_state": _outcome_value_state(
                outcome, str(metadata["conversion_value_state"])
            ),
            "omitted_zero_behavior": metadata["omitted_zero_behavior"],
        },
        "platform_semantics": _platform_semantics(
            click_semantic="all_clicks",
            delivery_state=(
                "standard"
                if metadata["latency_state"] == "mature"
                else "delayed"
            ),
            skan_state="non_skan",
        ),
    }
    for field, expected_value in expected.items():
        if row.get(field) != expected_value:
            raise NormalizedBatchError(
                f"generic normalized {field} is not physical"
            )


def _verify_physical_row(
    *,
    row: Mapping[str, object],
    values: Mapping[str, str],
    reference: str,
    capability: AdapterCapability,
    registration: Mapping[str, object],
    metadata: Mapping[str, object],
    admitted: AdmittedSource,
    source_id: str,
    import_id: str,
) -> None:
    selected = _selected_physical_fields(
        capability=capability,
        registration=registration,
        values=values,
    )
    raw_selected_outcome = str(selected["outcome"])
    missing_outcome_tokens = (
        {"<5", "NO_ACCESS", ""}
        if capability.platform == "linkedin_ads"
        else ({"<5", ""} if capability.platform == "tiktok_ads" else set())
    )
    expected_outcome_source: str | None = (
        None
        if raw_selected_outcome in missing_outcome_tokens
        else raw_selected_outcome
    )
    identities = tuple(
        _row_path(row, field, "platform_id")
        for field in ("account", "campaign", "ad_group", "ad", "creative")
    )
    direct = {
        "source_sha256": row.get("source_sha256"),
        "source_row_reference": row.get("source_row_reference"),
        "platform": row.get("platform"),
        "adapter_id": _row_path(row, "adapter", "adapter_id"),
        "adapter_version": _row_path(row, "adapter", "adapter_version"),
        "maturity": _row_path(row, "adapter", "maturity"),
        "identities": identities,
        "dates": (
            _row_path(row, "reporting", "start_date"),
            _row_path(row, "reporting", "end_date"),
        ),
        "impressions": _row_path(
            row, "exposure", "impressions", "source_numeric_text"
        ),
        "clicks": _row_path(
            row, "exposure", "clicks", "source_numeric_text"
        ),
        "spend": _row_path(row, "spend", "source_numeric_text"),
        "spend_metric": _row_path(row, "spend", "source_metric"),
        "outcome": _row_path(row, "outcome", "source_numeric_text"),
        "outcome_metric": _row_path(row, "outcome", "source_metric"),
    }
    expected = {
        "source_sha256": admitted.source_sha256,
        "source_row_reference": reference,
        "platform": capability.platform,
        "adapter_id": capability.adapter_id,
        "adapter_version": capability.adapter_version,
        "maturity": capability.maturity,
        "identities": selected["ids"],
        "dates": selected["dates"],
        "impressions": selected["impressions"],
        "clicks": selected["clicks"],
        "spend": selected["spend"],
        "spend_metric": selected["spend_metric"],
        "outcome": expected_outcome_source,
        "outcome_metric": selected["outcome_metric"],
    }
    if direct != expected:
        raise NormalizedBatchError(
            "adapter result does not match admitted physical row"
        )
    observation_id = "observation-" + sha256_json({
        "adapter_id": capability.adapter_id,
        "source_sha256": admitted.source_sha256,
        "source_row_reference": reference,
        "metric_id": registration["metric_id"],
    }).removeprefix("sha256:")
    common = {
        "schema_version": "normalized-outcome-observation-v1",
        "observation_id": observation_id,
        "study_id": registration["study_id"],
        "registration_id": registration["registration_id"],
        "import_id": import_id,
        "source_id": source_id,
        "source_sha256": admitted.source_sha256,
        "source_row_reference": reference,
        "platform": capability.platform,
        "adapter": {
            "adapter_id": capability.adapter_id,
            "adapter_version": capability.adapter_version,
            "maturity": capability.maturity,
        },
        "account": {"platform_id": selected["ids"][0]},
        "campaign": {"platform_id": selected["ids"][1]},
        "ad_group": {"platform_id": selected["ids"][2]},
        "ad": {"platform_id": selected["ids"][3]},
        "creative": {"platform_id": selected["ids"][4]},
    }
    if any(row.get(field) != value for field, value in common.items()):
        raise NormalizedBatchError("adapter audit identity is not physical")
    physical_impressions = _integer(selected["impressions"], "impressions")
    physical_clicks = _integer(selected["clicks"], "clicks")
    expected_exposure = {
        "impressions": {
            "value": physical_impressions,
            "source_numeric_text": selected["impressions"],
        },
        "clicks": {
            "value": physical_clicks,
            "source_numeric_text": selected["clicks"],
        },
    }
    if row.get("exposure") != expected_exposure:
        raise NormalizedBatchError("adapter exposure is not physical")
    physical_spend = Decimal(str(selected["spend"]))
    if capability.platform == "google_ads":
        physical_spend /= Decimal(1_000_000)
    spend_decimal = format(physical_spend, "f")
    spend_units: dict[str, object] = {
        "meta_ads": "account_currency_units",
        "google_ads": "micros",
        "linkedin_ads": "local_currency_units",
        "the_trade_desk": "advertiser_currency",
        "xandr": "advertiser_currency",
    }
    if capability.platform == "tiktok_ads":
        spend_units[capability.platform] = (
            "estimated_advertiser_currency"
            if metadata["spend_value_state"] == "estimated"
            else "advertiser_currency"
        )
    elif capability.platform == "dv360":
        spend_units[capability.platform] = metadata["cost_basis"]
    expected_spend = {
        "value": _decimal_json_number(physical_spend),
        "decimal": spend_decimal,
        "source_numeric_text": selected["spend"],
        "source_metric": selected["spend_metric"],
        "source_unit": spend_units[capability.platform],
    }
    if row.get("spend") != expected_spend:
        raise NormalizedBatchError("adapter spend is not physical")
    raw_outcome = str(selected["outcome"])
    missing_state: str | None = None
    if capability.platform == "linkedin_ads":
        missing_state = {
            "<5": "suppressed",
            "NO_ACCESS": "absent",
            "": "null",
        }.get(raw_outcome)
    elif capability.platform == "tiktok_ads":
        missing_state = {"<5": "suppressed", "": "null"}.get(raw_outcome)
    outcome_decimal = (
        None if missing_state is not None else Decimal(raw_outcome)
    )
    quality = (
        "observed"
        if capability.platform == "tiktok_ads"
        else str(metadata["conversion_value_state"])
    )
    omitted = (
        "not_applicable"
        if capability.platform == "meta_ads"
        else metadata["omitted_zero_behavior"]
    )
    expected_outcome = {
        "metric_id": registration["metric_id"],
        "source_metric": selected["outcome_metric"],
        "value": (
            None
            if outcome_decimal is None
            else _decimal_json_number(outcome_decimal)
        ),
        "decimal": None if outcome_decimal is None else raw_outcome,
        "source_numeric_text": None if outcome_decimal is None else raw_outcome,
        "value_state": (
            missing_state
            if outcome_decimal is None
            else _outcome_value_state(outcome_decimal, quality)
        ),
        "omitted_zero_behavior": omitted,
    }
    if row.get("outcome") != expected_outcome:
        raise NormalizedBatchError("adapter outcome is not physical")
    _verify_context_semantics(
        row=row,
        capability=capability,
        registration=registration,
        metadata=metadata,
        start_date=str(selected["dates"][0]),
        end_date=str(selected["dates"][1]),
    )


def _verify_context_semantics(
    *,
    row: Mapping[str, object],
    capability: AdapterCapability,
    registration: Mapping[str, object],
    metadata: Mapping[str, object],
    start_date: str,
    end_date: str,
) -> None:
    platform = capability.platform
    reporting = row["reporting"]
    attribution = row["attribution"]
    currency = row["currency"]
    semantics = row["platform_semantics"]
    assert isinstance(reporting, Mapping)
    assert isinstance(attribution, Mapping)
    assert isinstance(currency, Mapping)
    assert isinstance(semantics, Mapping)
    if platform == "meta_ads":
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": metadata["account_timezone"],
            "basis": "account_reporting_day",
            "request_level": "ad",
            "time_increment": "1",
            "segment_grain": list(capability.identity_fields),
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": registration["action_report_time"],
            "windows": registration["attribution_windows"],
        }
        currency_code = metadata["account_currency"]
        expected_semantics = _platform_semantics()
    elif platform == "google_ads":
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": metadata["customer_time_zone"],
            "basis": "interaction_date",
            "request_level": None,
            "time_increment": None,
            "segment_grain": list(capability.identity_fields),
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {"report_time": "interaction_date", "windows": []}
        currency_code = metadata["customer_currency"]
        expected_semantics = _platform_semantics()
    elif platform == "linkedin_ads":
        granularity = metadata["time_granularity"]
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "UTC",
            "basis": (
                "utc_reporting_day" if granularity == "DAILY"
                else "utc_reporting_period"
            ),
            "request_level": "creative",
            "time_increment": "1" if granularity == "DAILY" else "period",
            "segment_grain": list(capability.identity_fields),
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": "platform_attribution",
            "windows": metadata["attribution_windows"],
        }
        currency_code = metadata["account_currency"]
        expected_semantics = _platform_semantics(
            billed_currency=str(metadata["billed_currency"]),
            currency_relationship=str(metadata["currency_state"]),
            privacy_review_state=str(metadata["privacy_state"]),
            demographic_truncation_state=str(
                metadata["demographic_truncation"]
            ),
        )
    elif platform == "tiktok_ads":
        registered_metric = str(registration["registered_source_metric"])
        reporting_basis = (
            "advertiser_reporting_day"
            if metadata["account_scope"] == "single_advertiser"
            else "multi_advertiser_utc_day"
        )
        ad_id_field = str(metadata["ad_id_field"])
        source_time_basis = (
            "conversion_time"
            if registered_metric == "real_time_conversion"
            else "interaction_time"
        )
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": metadata["reporting_timezone"],
            "basis": reporting_basis,
            "request_level": "ad",
            "time_increment": "1",
            "segment_grain": [
                *capability.identity_fields[:-2],
                ad_id_field,
                capability.identity_fields[-1],
            ],
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": source_time_basis,
            "windows": metadata["attribution_windows"],
        }
        currency_code = metadata["advertiser_currency"]
        delivery_state = str(metadata["delivery_state"])
        expected_semantics = _platform_semantics(
            click_semantic=(
                "all_clicks"
                if registration["registered_click_metric"] == "clicks"
                else "destination_clicks"
            ),
            optimization_event=str(metadata["optimization_event"]),
            delivery_state=(
                "delayed"
                if delivery_state in {"delayed", "skan_delayed"}
                else "standard"
            ),
            skan_state=(
                "skan_delayed"
                if delivery_state == "skan_delayed"
                else "non_skan"
            ),
            search_term_id=None,
            search_term_state="not_reported",
        )
    elif platform == "dv360":
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": metadata["reporting_timezone"],
            "basis": (
                "utc_reporting_day"
                if metadata["timezone_basis"] == "utc"
                else "advertiser_reporting_day"
            ),
            "request_level": "creative",
            "time_increment": "1",
            "segment_grain": list(capability.identity_fields),
            "latency_state": metadata["latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": "platform_attribution",
            "windows": metadata["attribution_windows"],
        }
        currency_code = metadata["currency_code"]
        expected_semantics = _platform_semantics(
            click_semantic="all_clicks"
        )
    elif platform == "the_trade_desk":
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "UTC",
            "basis": "utc_reporting_day",
            "request_level": "creative",
            "time_increment": "1",
            "segment_grain": list(capability.identity_fields),
            "latency_state": (
                "mature"
                if metadata["late_offline_conversion_state"] == "mature"
                else "immature"
            ),
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": "platform_attribution",
            "windows": metadata["attribution_windows"],
        }
        currency_code = metadata["advertiser_currency"]
        mature = metadata["late_offline_conversion_state"] == "mature"
        expected_semantics = _platform_semantics(
            click_semantic="all_clicks",
            delivery_state="standard" if mature else "delayed",
            skan_state="non_skan",
        )
    elif platform == "xandr":
        metric = registration["registered_source_metric"]
        click = metric in {
            "post_click_convs", "Post Click Convs", "Post-click Conversions"
        }
        expected_reporting = {
            "start_date": start_date,
            "end_date": end_date,
            "timezone": metadata["reporting_timezone"],
            "basis": (
                "historical_utc_day"
                if metadata["report_mode"] == "historical"
                else "member_reporting_day"
            ),
            "request_level": "creative",
            "time_increment": "1",
            "segment_grain": list(capability.identity_fields),
            "latency_state": metadata["conversion_latency_state"],
            "observed_at": metadata["observed_at"],
        }
        expected_attribution = {
            "report_time": "post_click" if click else "post_view",
            "windows": [
                metadata["click_window"] if click else metadata["view_window"]
            ],
        }
        currency_code = metadata["advertiser_currency"]
        expected_semantics = _platform_semantics(
            click_semantic="all_clicks",
            delivery_state=(
                "standard"
                if metadata["conversion_latency_state"] == "mature"
                else "delayed"
            ),
            skan_state="non_skan",
        )
    else:
        raise NormalizedBatchError("adapter context verification is unsupported")
    if dict(reporting) != expected_reporting:
        raise NormalizedBatchError("adapter reporting fact is not admitted")
    if dict(attribution) != expected_attribution:
        raise NormalizedBatchError("adapter attribution fact is not admitted")
    if currency != {"code": currency_code, "basis": capability.currency_basis}:
        raise NormalizedBatchError("adapter currency fact is not admitted")
    if dict(semantics) != expected_semantics:
        raise NormalizedBatchError("adapter platform fact is not admitted")


def _assignment_projection(
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
    effective_status_authority: EffectiveEvidenceStatusAuthority,
) -> dict[str, object]:
    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    effective_evidence_status = verify_effective_evidence_status(
        effective_status_authority,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    registration = study.registration
    surface = registration["synthetic_surface"]
    power = registration["study_design_power"]
    partition = registration["holdout_partition"]
    prior = registration["prior_outcome_access"]
    chronology = study.registration_receipt["chronology"]
    assert isinstance(surface, Mapping)
    assert isinstance(power, Mapping)
    assert isinstance(partition, Mapping)
    assert isinstance(prior, list)
    assert isinstance(chronology, Mapping)
    surface_hashes = {
        str(value)
        for key, value in surface.items()
        if key.endswith("sha256") and isinstance(value, str)
    }
    prior_hashes = {
        str(item["access_sha256"])
        for item in prior
        if isinstance(item, Mapping) and isinstance(item.get("access_sha256"), str)
    }
    events = chronology.get("events")
    if not isinstance(events, list):
        raise NormalizedBatchError("authenticated chronology is incomplete")
    accessed_surface_hashes = {
        str(event["evidence_source_sha256"])
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_type") in {
            "outcome_access_started", "reported_outcome_access", "source_accessed"
        }
        and isinstance(event.get("evidence_source_sha256"), str)
    }
    producer_binding_complete = {
        "result_sha256",
        "result_bytes_sha256",
        "manifest_sha256",
        "lineage_bundle_sha256",
        "producer_evidence_sha256",
        "producer_semantics_sha256",
    }.issubset(surface)
    randomized = (
        producer_binding_complete
        and str(power.get("method", "")).startswith("preregistered-randomized-")
        and effective_evidence_status == "preregistered_holdout"
    )
    return {
        "design": "randomized" if randomized else "observational",
        "unit": partition["partition_unit"],
        "leakage_detected": bool(
            surface_hashes.intersection(prior_hashes | accessed_surface_hashes)
        ),
    }


def _projection(
    *,
    row: Mapping[str, object],
    values: Mapping[str, str] | None,
    mapping: Mapping[str, str] | None,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
    effective_status_authority: EffectiveEvidenceStatusAuthority,
    governance: Mapping[str, object],
) -> dict[str, object]:
    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    effective_evidence_status = verify_effective_evidence_status(
        effective_status_authority,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    reverse = (
        {target: source for source, target in mapping.items()}
        if mapping is not None
        else {}
    )
    outcome = row["outcome"]
    exposure = row["exposure"]
    assert isinstance(outcome, Mapping)
    assert isinstance(exposure, Mapping)
    impressions = exposure["impressions"]
    assert isinstance(impressions, Mapping)
    eligible = _integer(impressions["value"], "impressions")
    outcome_value = outcome["value"]
    if outcome_value is None:
        raise NormalizedBatchError(
            "missing aggregate outcomes cannot enter validation handoff"
        )
    if values is not None and (
        "sample_count" in reverse or "standard_deviation" in reverse
    ):
        if not {"sample_count", "standard_deviation"}.issubset(reverse):
            raise NormalizedBatchError(
                "continuous mean requires sample count and standard deviation"
            )
        sample = _integer(values[reverse["sample_count"]], "sample_count")
        if sample > eligible:
            raise NormalizedBatchError(
                "sample_count cannot exceed admitted impressions"
            )
        family = "continuous_mean"
        aggregate = {
            "sample_count": sample,
            "mean": float(outcome_value),
            "standard_deviation": _number(
                values[reverse["standard_deviation"]],
                "standard_deviation",
            ),
        }
        missing = eligible - sample
        effective = float(sample)
    elif values is not None and "exposure_time" in reverse:
        family = "event_rate"
        aggregate = {
            "event_count": _integer(outcome_value, "event_count"),
            "exposure_time": _number(
                values[reverse["exposure_time"]],
                "exposure_time",
                positive=True,
            ),
        }
        missing = 0
        effective = float(eligible)
    else:
        success = _integer(outcome_value, "success_count")
        if success > eligible:
            raise NormalizedBatchError(
                "success_count cannot exceed admitted impressions"
            )
        family = "binary_proportion"
        aggregate = {
            "success_count": success,
            "eligible_exposure_count": eligible,
        }
        missing = 0
        effective = float(eligible)
    registration = study.registration
    metric = registration["primary_metric"]
    analysis = registration["analysis_rules"]
    partition = registration["holdout_partition"]
    power = registration["study_design_power"]
    assert isinstance(metric, Mapping)
    assert isinstance(analysis, Mapping)
    assert isinstance(partition, Mapping)
    assert isinstance(power, Mapping)
    confidence_levels = analysis["confidence_levels"]
    assert isinstance(confidence_levels, list) and confidence_levels
    return {
        "status": "available",
        "evidence_status": effective_evidence_status,
        "metric_family": family,
        "measurement_window": metric["measurement_window"],
        "attribution_window": metric["attribution_window"],
        "aggregate": aggregate,
        "eligible_exposure_count": eligible,
        "missing_outcome_count": missing,
        "effective_sample_size": effective,
        "assignment": _assignment_projection(
            authenticated_study=authenticated_study,
            study_authority=study_authority,
            effective_status_authority=effective_status_authority,
        ),
        "confidence_level": confidence_levels[0],
        "permission_confirmed": bool(governance["permission_reference"]),
        "outcome_accessed_at": governance["export_timestamp"],
        "limitations": ["schema_tested_adapter"],
    }


def _add_validation_projection(
    normalized_observation: Mapping[str, object],
    *,
    projection: Mapping[str, object],
) -> dict[str, object]:
    """Close one mechanically derived projection over an unavailable row."""

    validated = validate_normalized_observation(normalized_observation)
    current = validated["validation_projection"]
    assert isinstance(current, Mapping)
    if current["status"] != "unavailable":
        raise ContractError(
            "an available validation projection cannot be overwritten"
        )
    enriched = deepcopy(validated)
    enriched["validation_projection"] = deepcopy(dict(projection))
    enriched["normalized_observation_sha256"] = None
    return validate_normalized_observation(enriched)


def _derive_rows(inputs: _BatchInputs) -> tuple[dict[str, object], ...]:
    study = verify_study_authority(
        inputs.authenticated_study,
        authority=inputs.study_authority,
    )
    verify_effective_evidence_status(
        inputs.effective_status_authority,
        authenticated_study=inputs.authenticated_study,
        study_authority=inputs.study_authority,
    )
    governance = validate_source_governance_input(inputs.governance_input)
    manifest, source_id = _source_manifest_binding(
        inputs.source_manifest, inputs.admitted_source
    )
    event = authenticate_import_event(
        inputs.import_event_envelope,
        authority=inputs.study_authority,
    )
    if (
        event["source_manifest_sha256"] != manifest["source_manifest_sha256"]
        or event["study_id"] != study.delivery_map["study_id"]
        or _timestamp(event["imported_at"], "imported_at")
        < _timestamp(governance["export_timestamp"], "export_timestamp")
    ):
        raise NormalizedBatchError(
            "authenticated import event does not bind source chronology"
        )
    implementation, capability, adapter = _resolve_implementation(
        inputs.admission_validation
    )
    if capability.platform == "generic_dsp":
        if type(inputs.profile) is not GenericAdmissionProfile:
            raise NormalizedBatchError(
                "generic capability requires an exact admitted profile"
            )
        registration, payload = _generic_documents(
            study=study,
            profile=inputs.profile,
            inventory=inputs.source_inventory,
            admitted=inputs.admitted_source,
            governance=governance,
            source_id=source_id,
            import_id=str(event["import_id"]),
        )
        physical = {
            reference: values
            for reference, values in _physical_rows(
                inputs.source_inventory, inputs.profile.headers
            )
        }
        mapping: Mapping[str, str] | None = dict(
            inputs.profile.source_to_canonical
        )
        metadata = payload["reporting_metadata"]
        authority_snapshot = {
            "capability": capability,
            "registration": registration,
            "payload": payload,
            "physical": physical,
            "mapping": mapping,
            "metadata": metadata,
            "governance": governance,
            "profile": inputs.profile,
        }
        authority_digest = _input_digest(authority_snapshot)
        derived_profile = _guarded_adapter_call(
            implementation.approved_profile,
            adapter,
            payload,
            registration=registration,
            capability=capability,
        )
        if derived_profile != inputs.profile:
            raise NormalizedBatchError(
                "authenticated profile does not match admitted source"
            )
        adapter_inventory = _guarded_adapter_call(
            implementation.inventory,
            adapter,
            inputs.source_inventory,
            capability,
            profile=inputs.profile,
        )
        derived_validation = _guarded_adapter_call(
            implementation.validate,
            adapter,
            adapter_inventory,
            registration=registration,
            governance=governance,
            capability=capability,
        )
        admission_call = implementation.admission_validation
        derived_admission = _guarded_adapter_call(
            admission_call,
            adapter,
            inputs.source_inventory,
            source_sha256=inputs.admitted_source.source_sha256,
            validation=derived_validation,
            registration=registration,
            governance=governance,
            profile=inputs.profile,
        )
    else:
        if inputs.profile is not None:
            raise NormalizedBatchError(
                "exact capability cannot accept a generic profile"
            )
        registration, payload, physical = _exact_documents(
            inputs=inputs,
            capability=capability,
            source_id=source_id,
            import_id=str(event["import_id"]),
        )
        mapping = None
        metadata = payload["reporting_metadata"]
        authority_snapshot = {
            "capability": capability,
            "registration": registration,
            "payload": payload,
            "physical": physical,
            "mapping": mapping,
            "metadata": metadata,
            "governance": governance,
            "adapter_context": inputs.adapter_context,
        }
        authority_digest = _input_digest(authority_snapshot)
        adapter_inventory = _guarded_adapter_call(
            implementation.inventory,
            adapter,
            inputs.source_inventory,
            capability,
        )
        derived_validation = _guarded_adapter_call(
            implementation.validate,
            adapter,
            adapter_inventory,
            registration=registration,
            governance=governance,
            capability=capability,
        )
        admission_call = implementation.admission_validation
        derived_admission = _guarded_adapter_call(
            admission_call,
            adapter,
            inputs.source_inventory,
            source_sha256=inputs.admitted_source.source_sha256,
            validation=derived_validation,
            registration=registration,
            governance=governance,
            normalization_context=inputs.adapter_context,
        )
    if derived_admission != inputs.admission_validation:
        raise NormalizedBatchError(
            "adapter admission does not bind normalization context"
        )
    authenticate_admitted_source(
        inputs.admitted_source,
        inputs.source_inventory,
        pre_scan_obvious_privacy(
            inputs.source_inventory,
            source_name=inputs.admitted_source.source_name,
        ),
        derived_admission,
    )
    if capability.platform == "generic_dsp":
        result = _guarded_adapter_call(
            implementation.normalize,
            adapter,
            payload,
            registration=registration,
            capability=capability,
            source_inventory=inputs.source_inventory,
            admission_validation=derived_admission,
            admitted_source=inputs.admitted_source,
            governance=governance,
            profile=inputs.profile,
        )
    else:
        result = _guarded_adapter_call(
            implementation.normalize,
            adapter,
            payload,
            registration=registration,
            capability=capability,
        )
    if _input_digest(authority_snapshot) != authority_digest:
        raise NormalizedBatchError("trusted normalization authority changed")
    if type(result) is not AdapterResult:
        raise NormalizedBatchError("adapter did not return an exact result")
    observation_ids = sorted(
        str(row["observation_id"]) for row in result.normalized_rows
    )
    if event["observation_ids"] != observation_ids:
        raise NormalizedBatchError(
            "authenticated import event does not bind normalized observations"
        )
    if not isinstance(metadata, Mapping):
        raise NormalizedBatchError("reporting metadata is invalid")
    if len(result.normalized_rows) != len(physical):
        raise NormalizedBatchError(
            "adapter row count does not match admitted physical rows"
        )
    for row in result.normalized_rows:
        reference = str(row.get("source_row_reference"))
        values = physical.get(reference)
        if values is None:
            raise NormalizedBatchError(
                "adapter result does not identify an admitted physical row"
            )
        if capability.platform == "generic_dsp":
            assert mapping is not None
            _verify_generic_row(
                row=row,
                values=values,
                reference=reference,
                mapping=mapping,
                capability=capability,
                registration=registration,
                metadata=metadata,
                admitted=inputs.admitted_source,
                source_id=source_id,
                import_id=str(event["import_id"]),
            )
        else:
            _verify_physical_row(
                row=row,
                values=values,
                reference=reference,
                capability=capability,
                registration=registration,
                metadata=metadata,
                admitted=inputs.admitted_source,
                source_id=source_id,
                import_id=str(event["import_id"]),
            )
    enriched = tuple(
        _add_validation_projection(
            row,
            projection=_projection(
                row=row,
                values=(
                    physical[str(row["source_row_reference"])]
                    if capability.platform == "generic_dsp"
                    else None
                ),
                mapping=mapping,
                authenticated_study=inputs.authenticated_study,
                study_authority=inputs.study_authority,
                effective_status_authority=inputs.effective_status_authority,
                governance=governance,
            ),
        )
        for row in result.normalized_rows
    )
    physical_ids = [
        (
            row["source_sha256"],
            row["source_row_reference"],
            row["adapter"]["adapter_id"],
            row["adapter"]["adapter_version"],
            row["outcome"]["metric_id"],
        )
        for row in enriched
    ]
    if len(physical_ids) != len(set(physical_ids)):
        raise NormalizedBatchError("duplicate physical observation identity")
    return tuple(deepcopy(row) for row in enriched)


def authenticate_normalized_batch(
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
    source_inventory: ContainerInventory,
    admission_validation: AdapterAdmissionValidation,
    admitted_source: AdmittedSource,
    governance_input: Mapping[str, object],
    adapter_context: Mapping[str, object] | None = None,
    profile: GenericAdmissionProfile | None = None,
    source_manifest: Mapping[str, object],
    import_event_envelope: Mapping[str, object],
    effective_status_authority: EffectiveEvidenceStatusAuthority | None = None,
) -> AuthenticatedNormalizedBatch:
    """Reinspect admitted bytes, rerun the adapter, and mint one batch."""

    if effective_status_authority is None:
        effective_status_authority = authenticate_effective_evidence_status(
            authenticated_study=authenticated_study,
            study_authority=study_authority,
        )
    else:
        verify_effective_evidence_status(
            effective_status_authority,
            authenticated_study=authenticated_study,
            study_authority=study_authority,
        )
    inputs = _BatchInputs(
        authenticated_study=authenticated_study,
        study_authority=study_authority,
        effective_status_authority=effective_status_authority,
        source_inventory=source_inventory,
        admission_validation=admission_validation,
        admitted_source=admitted_source,
        governance_input=deepcopy(dict(governance_input)),
        adapter_context=(
            None
            if adapter_context is None
            else deepcopy(dict(adapter_context))
        ),
        profile=profile,
        source_manifest=deepcopy(dict(source_manifest)),
        import_event_envelope=deepcopy(dict(import_event_envelope)),
    )
    rows = _derive_rows(inputs)
    digest = sha256_json(list(rows))
    batch = object.__new__(AuthenticatedNormalizedBatch)
    _BATCH_STATES[batch] = _BatchState(inputs=inputs, rows=rows, batch_sha256=digest)
    return batch


def verify_authenticated_normalized_batch(
    batch: object,
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> tuple[dict[str, object], ...]:
    """Reauthenticate the operation-local batch and return defensive rows."""

    if not isinstance(batch, AuthenticatedNormalizedBatch):
        raise NormalizedBatchError("authenticated normalized batch is required")
    state = _BATCH_STATES.get(batch)
    if state is None:
        raise NormalizedBatchError("authenticated normalized batch is inactive")
    if (
        state.inputs.authenticated_study is not authenticated_study
        or state.inputs.study_authority is not study_authority
    ):
        raise NormalizedBatchError(
            "normalized batch does not bind the supplied study authority"
        )
    rows = _derive_rows(state.inputs)
    if rows != state.rows or sha256_json(list(rows)) != state.batch_sha256:
        raise NormalizedBatchError("authenticated normalized batch changed")
    return tuple(deepcopy(row) for row in rows)


def authenticated_normalized_batch_effective_status_authority(
    batch: object,
    *,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> EffectiveEvidenceStatusAuthority:
    """Return the verified opaque status authority carried by one live batch."""

    if not isinstance(batch, AuthenticatedNormalizedBatch):
        raise NormalizedBatchError("authenticated normalized batch is required")
    state = _BATCH_STATES.get(batch)
    if state is None or (
        state.inputs.authenticated_study is not authenticated_study
        or state.inputs.study_authority is not study_authority
    ):
        raise NormalizedBatchError(
            "normalized batch does not bind the supplied study authority"
        )
    verify_effective_evidence_status(
        state.inputs.effective_status_authority,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    return state.inputs.effective_status_authority


__all__ = [
    "AuthenticatedNormalizedBatch",
    "EffectiveEvidenceStatusAuthority",
    "NormalizedBatchError",
    "authenticate_effective_evidence_status",
    "authenticate_normalized_batch",
    "authenticated_normalized_batch_effective_status_authority",
    "effective_evidence_status_snapshot",
    "verify_effective_evidence_status",
    "verify_authenticated_normalized_batch",
]
