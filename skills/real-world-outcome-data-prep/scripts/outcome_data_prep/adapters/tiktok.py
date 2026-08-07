"""Semantic normalizer for the exact TikTok Reporting API variant."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ..capabilities import AdapterCapability
from ..common import ContractError, require_numeric_string
from .base import AdapterError, AdapterResult, ExactVariantAdapter
from .semantic_common import (
    LATENCY_STATES,
    build_platform_semantics,
    build_rich_observation,
    require_closed_object,
    require_date,
    require_nonnegative_count,
    require_nonnegative_decimal,
    require_source_sha256,
    require_string,
    require_string_list,
    require_timestamp,
)


_ADAPTER_ID = "tiktok-reporting-api-json-v1"
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "advertiser_currency",
    "account_timezone",
    "account_scope",
    "reporting_timezone",
    "ad_id_field",
    "optimization_event",
    "attribution_windows",
    "omitted_zero_behavior",
    "latency_state",
    "delivery_state",
    "spend_value_state",
    "observed_at",
}
_ROW_KEYS = {
    "source_row_reference",
    "advertiser_id",
    "campaign_id",
    "adgroup_id",
    "ad_id",
    "ad_id_v2",
    "stat_time_day",
    "impressions",
    "clicks",
    "destination_clicks",
    "spend",
    "conversion",
    "real_time_conversion",
    "search_term_id",
}
_EXACT_IDENTITIES = (
    "advertiser_id",
    "campaign_id",
    "adgroup_id",
    "ad_id",
    "stat_time_day",
)
_EXACT_REQUIRED_FIELDS = ("impressions", "clicks", "spend")
_EXACT_METRIC_FIELDS = ("conversion", "real_time_conversion")
_CONVERSION_TIME_BASES = {
    "conversion": "interaction_time",
    "real_time_conversion": "conversion_time",
}
_ID_FIELDS = frozenset({"ad_id", "ad_id_v2"})
_CLICK_FIELDS = frozenset({"clicks", "destination_clicks"})
_ACCOUNT_SCOPES = frozenset({"single_advertiser", "multi_advertiser"})
_DELIVERY_STATES = frozenset({"standard", "delayed", "skan_delayed"})
_SPEND_VALUE_STATES = frozenset({"observed", "estimated"})
_OMITTED_ZERO_BEHAVIOR = "omitted_metrics_are_unknown_not_zero"


def _require_exact_capability(capability: AdapterCapability) -> None:
    expected = (
        capability.adapter_id == _ADAPTER_ID
        and capability.platform == "tiktok_ads"
        and capability.report_type == "reporting_api_ad_daily"
        and capability.container == "json"
        and capability.locale == "invariant"
        and capability.row_grain == _EXACT_IDENTITIES
        and capability.identity_fields == _EXACT_IDENTITIES
        and capability.required_fields == _EXACT_REQUIRED_FIELDS
        and capability.metric_fields == _EXACT_METRIC_FIELDS
        and capability.time_basis == "advertiser_reporting_day"
        and capability.currency_basis == "advertiser_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    )
    if not expected:
        raise AdapterError(
            "TikTokAdsAdapter requires its schema-tested exact adapter variant"
        )


def tiktok_metric_value(value: object) -> tuple[str, Decimal | None]:
    if value == "<5":
        return "suppressed", None
    if value in (None, ""):
        return "missing", None
    try:
        source_text = require_numeric_string(value, "TikTok metric")
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc
    parsed = Decimal(source_text)
    if parsed < 0:
        raise AdapterError("TikTok metric must be non-negative")
    return "observed", parsed


def require_tiktok_time_basis(
    registered_metric: str,
    row: Mapping[str, object],
) -> str:
    if registered_metric not in _CONVERSION_TIME_BASES:
        raise AdapterError("registered TikTok source metric is unsupported")
    required = _CONVERSION_TIME_BASES[registered_metric]
    if registered_metric not in row:
        raise AdapterError(
            "registered TikTok metric is absent for required time basis"
        )
    return required


def _tiktok_metric(
    value: object,
    path: str,
) -> tuple[str, Decimal | None, str | None]:
    try:
        state, parsed = tiktok_metric_value(value)
    except AdapterError as exc:
        raise AdapterError(f"{path}: {exc}") from exc
    if state == "missing":
        return "null", None, None
    if state == "suppressed":
        return state, None, None
    assert parsed is not None
    return state, parsed, require_numeric_string(value, path)


def _reporting_basis(
    account_scope: str,
    account_timezone: str,
    reporting_timezone: str,
) -> str:
    if account_scope == "single_advertiser":
        if reporting_timezone != account_timezone:
            raise AdapterError(
                "TikTok single-advertiser reporting timezone must match "
                "the account timezone"
            )
        return "advertiser_reporting_day"
    if account_scope == "multi_advertiser":
        if reporting_timezone != "UTC":
            raise AdapterError(
                "TikTok multi-advertiser reporting timezone must be UTC"
            )
        return "multi_advertiser_utc_day"
    raise AdapterError("TikTok account_scope is unsupported")


class TikTokAdsAdapter(ExactVariantAdapter):
    """Normalize exact TikTok Reporting API rows after admission."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("TikTok adapter capability is invalid")
        _require_exact_capability(capability)
        super().__init__(capability)

    def normalize(
        self,
        inventory: object,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterResult:
        if (
            type(capability) is not AdapterCapability
            or capability != self.capability
        ):
            raise AdapterError("adapter capability does not match adapter")
        _require_exact_capability(capability)
        payload = require_closed_object(
            inventory,
            _ROOT_KEYS,
            "TikTok export",
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "TikTok reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("TikTok rows must be a non-empty list")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"),
            "registration_id",
        )
        metric_id = require_string(
            registration.get("metric_id"),
            "metric_id",
        )
        registered_metric = require_string(
            registration.get("registered_source_metric"),
            "registered_source_metric",
        )
        if registered_metric not in _CONVERSION_TIME_BASES:
            raise AdapterError("registered TikTok source metric is unsupported")
        registered_click_metric = require_string(
            registration.get("registered_click_metric"),
            "registered_click_metric",
        )
        if registered_click_metric not in _CLICK_FIELDS:
            raise AdapterError("registered TikTok click metric is unsupported")
        optimization_event = require_string(
            registration.get("optimization_event"),
            "optimization_event",
        )
        metadata_optimization_event = require_string(
            metadata["optimization_event"],
            "optimization_event",
        )
        if metadata_optimization_event != optimization_event:
            raise AdapterError(
                "TikTok optimization event does not match registration"
            )
        registered_windows = require_string_list(
            registration.get("attribution_windows"),
            "attribution_windows",
        )
        metadata_windows = require_string_list(
            metadata["attribution_windows"],
            "attribution_windows",
        )
        if metadata_windows != registered_windows:
            raise AdapterError(
                "TikTok attribution_windows do not match registration"
            )

        advertiser_currency = require_string(
            metadata["advertiser_currency"],
            "advertiser_currency",
        )
        account_timezone = require_string(
            metadata["account_timezone"],
            "account_timezone",
        )
        account_scope = require_string(
            metadata["account_scope"],
            "account_scope",
        )
        if account_scope not in _ACCOUNT_SCOPES:
            raise AdapterError("TikTok account_scope is unsupported")
        reporting_timezone = require_string(
            metadata["reporting_timezone"],
            "reporting_timezone",
        )
        reporting_basis = _reporting_basis(
            account_scope,
            account_timezone,
            reporting_timezone,
        )
        ad_id_field = require_string(
            metadata["ad_id_field"],
            "ad_id_field",
        )
        if ad_id_field not in _ID_FIELDS:
            raise AdapterError("TikTok ad_id_field is unsupported")
        latency_state = require_string(
            metadata["latency_state"],
            "latency_state",
        )
        if latency_state not in LATENCY_STATES:
            raise AdapterError("latency_state is unsupported")
        delivery_state = require_string(
            metadata["delivery_state"],
            "delivery_state",
        )
        if delivery_state not in _DELIVERY_STATES:
            raise AdapterError("TikTok delivery_state is unsupported")
        if delivery_state == "skan_delayed" and latency_state != "immature":
            raise AdapterError(
                "TikTok SKAN-delayed outcomes must remain immature"
            )
        spend_value_state = require_string(
            metadata["spend_value_state"],
            "spend_value_state",
        )
        if spend_value_state not in _SPEND_VALUE_STATES:
            raise AdapterError("TikTok spend_value_state is unsupported")
        omitted_zero_behavior = require_string(
            metadata["omitted_zero_behavior"],
            "omitted_zero_behavior",
        )
        if omitted_zero_behavior != _OMITTED_ZERO_BEHAVIOR:
            raise AdapterError(
                "TikTok exact adapter variant requires its "
                "omitted_zero_behavior"
            )
        observed_at = require_timestamp(
            metadata["observed_at"],
            "observed_at",
        )
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])

        normalized: list[dict[str, object]] = []
        seen_references: set[str] = set()
        search_term_values: list[str] = []
        for index, raw_row in enumerate(rows):
            path = f"TikTok rows[{index}]"
            row = require_closed_object(
                raw_row,
                _ROW_KEYS,
                path,
                required={
                    "source_row_reference",
                    "advertiser_id",
                    "campaign_id",
                    "adgroup_id",
                    "stat_time_day",
                    *_EXACT_REQUIRED_FIELDS,
                },
            )
            source_row_reference = require_string(
                row["source_row_reference"],
                f"{path}.source_row_reference",
            )
            if source_row_reference in seen_references:
                raise AdapterError(
                    "TikTok source_row_reference values must be unique"
                )
            seen_references.add(source_row_reference)
            present_id_fields = _ID_FIELDS.intersection(row)
            if present_id_fields != {ad_id_field}:
                raise AdapterError(
                    "TikTok row must preserve exactly its declared ad_id lane"
                )
            advertiser_id = require_string(
                row["advertiser_id"],
                f"{path}.advertiser_id",
            )
            campaign_id = require_string(
                row["campaign_id"],
                f"{path}.campaign_id",
            )
            ad_group_id = require_string(
                row["adgroup_id"],
                f"{path}.adgroup_id",
            )
            creative_id = require_string(
                row[ad_id_field],
                f"{path}.{ad_id_field}",
            )
            reporting_date = require_date(
                row["stat_time_day"],
                f"{path}.stat_time_day",
            )
            impressions_text, impressions = require_nonnegative_count(
                row["impressions"],
                f"{path}.impressions",
                strings_only=True,
            )
            click_values: dict[str, tuple[str, Decimal]] = {}
            for field in _CLICK_FIELDS:
                if field not in row:
                    continue
                click_values[field] = require_nonnegative_count(
                    row[field],
                    f"{path}.{field}",
                    strings_only=True,
                )
            if registered_click_metric not in click_values:
                raise AdapterError(
                    "registered TikTok click metric is absent"
                )
            clicks_text, clicks = click_values[registered_click_metric]
            spend_text, spend = require_nonnegative_decimal(
                row["spend"],
                f"{path}.spend",
                strings_only=True,
            )

            checked_metrics: dict[
                str, tuple[str, Decimal | None, str | None]
            ] = {}
            for field in _EXACT_METRIC_FIELDS:
                if field in row:
                    checked_metrics[field] = _tiktok_metric(
                        row[field],
                        f"{path}.{field}",
                    )
            time_basis = require_tiktok_time_basis(registered_metric, row)
            outcome_state, outcome, outcome_text = checked_metrics[
                registered_metric
            ]
            explicit_state = (
                outcome_state if outcome_state != "observed" else None
            )

            search_term_id: str | None = None
            search_term_state = "not_reported"
            if "search_term_id" in row:
                search_term_id = require_string(
                    row["search_term_id"],
                    f"{path}.search_term_id",
                )
                search_term_values.append(search_term_id)
                search_term_state = (
                    "unknown" if search_term_id == "-1" else "observed"
                )
            normalized_delivery_state = (
                "delayed"
                if delivery_state in {"delayed", "skan_delayed"}
                else "standard"
            )
            skan_state = (
                "skan_delayed"
                if delivery_state == "skan_delayed"
                else "non_skan"
            )
            normalized.append(
                build_rich_observation(
                    capability=capability,
                    study_id=study_id,
                    registration_id=registration_id,
                    import_id=import_id,
                    source_id=source_id,
                    source_sha256=source_sha256,
                    source_row_reference=source_row_reference,
                    metric_id=metric_id,
                    account_id=advertiser_id,
                    campaign_id=campaign_id,
                    ad_group_id=ad_group_id,
                    creative_id=creative_id,
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": reporting_timezone,
                        "basis": reporting_basis,
                        "request_level": "ad",
                        "time_increment": "1",
                        "segment_grain": [
                            *capability.identity_fields[:-2],
                            ad_id_field,
                            capability.identity_fields[-1],
                        ],
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": time_basis,
                        "windows": metadata_windows,
                    },
                    platform_semantics=build_platform_semantics(
                        billed_currency=None,
                        currency_relationship="not_applicable",
                        privacy_review_state="not_applicable",
                        demographic_truncation_state="not_applicable",
                        click_semantic=(
                            "all_clicks"
                            if registered_click_metric == "clicks"
                            else "destination_clicks"
                        ),
                        optimization_event=optimization_event,
                        delivery_state=normalized_delivery_state,
                        skan_state=skan_state,
                        search_term_id=search_term_id,
                        search_term_state=search_term_state,
                    ),
                    currency_code=advertiser_currency,
                    spend=spend,
                    spend_decimal_text=spend_text,
                    spend_source_text=spend_text,
                    spend_source_metric="spend",
                    spend_source_unit=(
                        "estimated_advertiser_currency"
                        if spend_value_state == "estimated"
                        else "advertiser_currency"
                    ),
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=outcome_text,
                    outcome_source_metric=registered_metric,
                    conversion_quality="observed",
                    omitted_zero_behavior=omitted_zero_behavior,
                    outcome_value_state=explicit_state,
                )
            )

        if not search_term_values:
            search_term_state = "not_reported"
        elif all(value == "-1" for value in search_term_values):
            search_term_state = "unknown"
        elif any(value == "-1" for value in search_term_values):
            search_term_state = "mixed_known_and_unknown"
        else:
            search_term_state = "observed"
        return AdapterResult(
            adapter_id=capability.adapter_id,
            adapter_version=capability.adapter_version,
            maturity=capability.maturity,
            source_sha256=source_sha256,
            source_rows=len(rows),
            normalized_rows=tuple(normalized),
            quarantined_rows=(),
            mapping_report={
                "adapter_id": capability.adapter_id,
                "adapter_version": capability.adapter_version,
                "maturity": capability.maturity,
                "operational_status": "incomplete",
                "contract_ready": False,
                "normalized_row_count": len(normalized),
                "quarantined_row_count": 0,
                "source_semantics": {
                    "ad_id_field": ad_id_field,
                    "click_metric": registered_click_metric,
                    "optimization_event": optimization_event,
                    "account_scope": account_scope,
                    "reporting_timezone": reporting_timezone,
                    "delivery_state": delivery_state,
                    "spend_value_state": spend_value_state,
                    "search_term_state": search_term_state,
                    "search_term_values": search_term_values,
                },
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
