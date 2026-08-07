"""Semantic normalizer for the exact Google Ads API v23 JSON variant."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ..capabilities import AdapterCapability
from ..common import ContractError, require_integer_string_or_int
from .base import AdapterError, AdapterResult, ExactVariantAdapter
from .semantic_common import (
    CONVERSION_QUALITY_STATES,
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


_ADAPTER_ID = "google-ads-api-v23-ad-daily-json"
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "customer_currency",
    "customer_time_zone",
    "time_basis",
    "segment_grain",
    "omitted_zero_behavior",
    "latency_state",
    "conversion_value_state",
    "observed_at",
}
_ROW_KEYS = {
    "source_row_reference",
    "customer",
    "campaign",
    "ad_group",
    "ad_group_ad",
    "segments",
    "metrics",
}
_METRIC_KEYS = {
    "impressions",
    "clicks",
    "cost_micros",
    "conversions",
    "all_conversions",
}
_CONVERSION_METRICS = {"conversions", "all_conversions"}
_EXACT_OMITTED_ZERO_BEHAVIOR = "rows_omitted_when_all_metrics_zero"
_EXACT_IDENTITIES = (
    "customer.id",
    "campaign.id",
    "ad_group.id",
    "ad_group_ad.ad.id",
    "segments.date",
)
_EXACT_ROW_GRAIN = (
    "customer_id",
    "campaign_id",
    "ad_group_id",
    "ad_id",
    "date",
)
_EXACT_REQUIRED_FIELDS = (
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
)
_EXACT_METRIC_FIELDS = (
    "metrics.conversions",
    "metrics.all_conversions",
)
_EXACT_TIME_BASIS = "interaction_date"


def _require_exact_capability(capability: AdapterCapability) -> None:
    expected = (
        capability.adapter_id == _ADAPTER_ID
        and capability.platform == "google_ads"
        and capability.report_type == "api_v23_ad_daily"
        and capability.container == "json"
        and capability.locale == "invariant"
        and capability.row_grain == _EXACT_ROW_GRAIN
        and capability.identity_fields == _EXACT_IDENTITIES
        and capability.required_fields == _EXACT_REQUIRED_FIELDS
        and capability.metric_fields == _EXACT_METRIC_FIELDS
        and capability.time_basis == _EXACT_TIME_BASIS
        and capability.currency_basis == "customer_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    )
    if not expected:
        raise AdapterError(
            "GoogleAdsAdapter requires its schema-tested exact adapter variant"
        )


def google_cost_decimal(cost_micros: object) -> str:
    try:
        micros = require_integer_string_or_int(
            cost_micros, "metrics.cost_micros"
        )
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc
    if micros < 0:
        raise AdapterError("metrics.cost_micros must be non-negative")
    return format(Decimal(micros) / Decimal(1_000_000), "f")


def selected_google_conversion(
    metrics: Mapping[str, object],
    registered_source_metric: str,
) -> Decimal:
    if registered_source_metric not in _CONVERSION_METRICS:
        raise AdapterError("registered source metric is unsupported")
    if registered_source_metric not in metrics:
        raise AdapterError("registered source metric is absent")
    _, selected = require_nonnegative_decimal(
        metrics[registered_source_metric],
        f"metrics.{registered_source_metric}",
        strings_only=False,
    )
    return selected


def _identity(value: object, key: str, path: str) -> str:
    item = require_closed_object(value, {key}, path)
    return require_string(item[key], f"{path}.{key}")


class GoogleAdsAdapter(ExactVariantAdapter):
    """Normalize exact Google Ads API rows after Task 5 admission."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("Google Ads adapter capability is invalid")
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
            inventory, _ROOT_KEYS, "Google Ads export"
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "Google Ads reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("Google Ads rows must be a non-empty list")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"), "registration_id"
        )
        metric_id = require_string(
            registration.get("metric_id"), "metric_id"
        )
        registered_source_metric = require_string(
            registration.get("registered_source_metric"),
            "registered_source_metric",
        )
        if registered_source_metric not in _CONVERSION_METRICS:
            raise AdapterError("registered source metric is unsupported")
        registered_time_basis = require_string(
            registration.get("time_basis"), "time_basis"
        )
        time_basis = require_string(metadata["time_basis"], "time basis")
        registered_segment_grain = require_string_list(
            registration.get("segment_grain"), "segment grain"
        )
        segment_grain = require_string_list(
            metadata["segment_grain"], "segment grain"
        )
        if (
            registered_time_basis != _EXACT_TIME_BASIS
            or time_basis != _EXACT_TIME_BASIS
            or registered_segment_grain != list(_EXACT_IDENTITIES)
            or segment_grain != list(_EXACT_IDENTITIES)
        ):
            raise AdapterError(
                "Google Ads exact ad-daily variant requires time basis "
                "and full segment grain"
            )
        customer_currency = require_string(
            metadata["customer_currency"], "customer_currency"
        )
        customer_time_zone = require_string(
            metadata["customer_time_zone"], "customer_time_zone"
        )
        omitted_zero_behavior = require_string(
            metadata["omitted_zero_behavior"], "omitted_zero_behavior"
        )
        if omitted_zero_behavior != _EXACT_OMITTED_ZERO_BEHAVIOR:
            raise AdapterError(
                "Google Ads exact ad-daily variant requires its "
                "omitted_zero_behavior"
            )
        latency_state = require_string(
            metadata["latency_state"], "latency_state"
        )
        if latency_state not in LATENCY_STATES:
            raise AdapterError("latency_state is unsupported")
        conversion_quality = require_string(
            metadata["conversion_value_state"],
            "conversion_value_state",
        )
        if conversion_quality not in CONVERSION_QUALITY_STATES:
            raise AdapterError("conversion_value_state is unsupported")
        observed_at = require_timestamp(
            metadata["observed_at"], "observed_at"
        )
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])

        normalized: list[dict[str, object]] = []
        seen_row_references: set[str] = set()
        for index, raw_row in enumerate(rows):
            path = f"Google Ads rows[{index}]"
            row = require_closed_object(raw_row, _ROW_KEYS, path)
            source_row_reference = require_string(
                row["source_row_reference"],
                f"{path}.source_row_reference",
            )
            if source_row_reference in seen_row_references:
                raise AdapterError(
                    "Google Ads source_row_reference values must be unique"
                )
            seen_row_references.add(source_row_reference)
            customer_id = _identity(
                row["customer"], "id", f"{path}.customer"
            )
            campaign_id = _identity(
                row["campaign"], "id", f"{path}.campaign"
            )
            ad_group_id = _identity(
                row["ad_group"], "id", f"{path}.ad_group"
            )
            ad_group_ad = require_closed_object(
                row["ad_group_ad"], {"ad"}, f"{path}.ad_group_ad"
            )
            ad_id = _identity(
                ad_group_ad["ad"], "id", f"{path}.ad_group_ad.ad"
            )
            segments = require_closed_object(
                row["segments"], {"date"}, f"{path}.segments"
            )
            reporting_date = require_date(
                segments["date"], f"{path}.segments.date"
            )
            metrics = require_closed_object(
                row["metrics"],
                _METRIC_KEYS,
                f"{path}.metrics",
                required={"impressions", "clicks", "cost_micros"},
            )
            impressions_text, impressions = require_nonnegative_count(
                metrics["impressions"],
                f"{path}.metrics.impressions",
                strings_only=False,
            )
            clicks_text, clicks = require_nonnegative_count(
                metrics["clicks"],
                f"{path}.metrics.clicks",
                strings_only=False,
            )
            source_cost_micros = str(metrics["cost_micros"])
            spend_text = google_cost_decimal(metrics["cost_micros"])
            spend = Decimal(spend_text)
            conversion_text: dict[str, str] = {}
            for source_metric in sorted(_CONVERSION_METRICS):
                if source_metric not in metrics:
                    continue
                source_text, _ = require_nonnegative_decimal(
                    metrics[source_metric],
                    f"{path}.metrics.{source_metric}",
                    strings_only=False,
                )
                conversion_text[source_metric] = source_text
            outcome = selected_google_conversion(
                metrics, registered_source_metric
            )
            outcome_text = conversion_text[registered_source_metric]
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
                    account_id=customer_id,
                    campaign_id=campaign_id,
                    ad_group_id=ad_group_id,
                    creative_id=ad_id,
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": customer_time_zone,
                        "basis": _EXACT_TIME_BASIS,
                        "request_level": None,
                        "time_increment": None,
                        "segment_grain": list(_EXACT_IDENTITIES),
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": _EXACT_TIME_BASIS,
                        "windows": [],
                    },
                    platform_semantics=build_platform_semantics(
                        billed_currency=None,
                        currency_relationship="not_applicable",
                        privacy_review_state="not_applicable",
                        demographic_truncation_state="not_applicable",
                        click_semantic="not_applicable",
                        optimization_event=None,
                        delivery_state="not_applicable",
                        skan_state="not_applicable",
                        search_term_id=None,
                        search_term_state="not_applicable",
                    ),
                    currency_code=customer_currency,
                    spend=spend,
                    spend_decimal_text=spend_text,
                    spend_source_text=source_cost_micros,
                    spend_source_metric="cost_micros",
                    spend_source_unit="micros",
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=outcome_text,
                    outcome_source_metric=registered_source_metric,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior=omitted_zero_behavior,
                )
            )

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
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
