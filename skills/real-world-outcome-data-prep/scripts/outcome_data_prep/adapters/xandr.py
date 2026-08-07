"""Semantic normalizer for registered Xandr advertiser analytics variants."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from ..capabilities import AdapterCapability
from .base import AdapterError, AdapterResult, ExactVariantAdapter
from .programmatic_common import require_programmatic_capability
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


_VARIANTS = {
    "xandr-advertiser-analytics-csv-v1": (
        "csv",
        "advertiser_analytics",
        (
            "advertiser_id",
            "campaign_id",
            "insertion_order_id",
            "line_item_id",
            "creative_id",
            "day",
        ),
        ("imps", "clicks", "media_cost"),
        ("post_click_convs", "post_view_convs"),
    ),
    "xandr-advertiser-analytics-excel-tsv-v1": (
        "tsv",
        "advertiser_analytics_excel",
        (
            "Advertiser ID",
            "Campaign ID",
            "Insertion Order ID",
            "Line Item ID",
            "Creative ID",
            "Day",
        ),
        ("Imps", "Clicks", "Media Cost"),
        ("Post Click Convs", "Post View Convs"),
    ),
    "xandr-advertiser-analytics-xlsx-v1": (
        "xlsx",
        "advertiser_analytics",
        (
            "Advertiser ID",
            "Campaign ID",
            "Insertion Order ID",
            "Line Item ID",
            "Creative ID",
            "Date",
        ),
        ("Impressions", "Clicks", "Media Cost"),
        ("Post-click Conversions", "Post-view Conversions"),
    ),
}
_CAPABILITY_SHA256 = {
    "xandr-advertiser-analytics-csv-v1": (
        "sha256:68380cdd81f3b0364d2b88e69053011f9955a60218e8adb97840f758c3815ab0"
    ),
    "xandr-advertiser-analytics-excel-tsv-v1": (
        "sha256:5698d89dda7f4b02ec09833cfe0af0d1f32a2cbcc6b2fe806d81b8f4e6d89887"
    ),
    "xandr-advertiser-analytics-xlsx-v1": (
        "sha256:50ae8fc2d0b7e98e3fe80978c6c304c6f6c8fbda9d05703ae67508b04cb8c085"
    ),
}
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "advertiser_currency",
    "decimal_mark",
    "report_start_date",
    "report_end_exclusive",
    "report_mode",
    "reporting_timezone",
    "click_window",
    "view_window",
    "conversion_latency_state",
    "conversion_value_state",
    "observed_at",
    "omitted_zero_behavior",
}
_OMITTED_ZERO = "omitted_metrics_are_unknown_not_zero"


def xandr_creative_state(value: object) -> tuple[str, str | None]:
    text = require_string(value, "creative_id")
    if text in {"0", "-1"}:
        return "external_tracker", None
    return "platform_creative", text


def _require_capability(capability: AdapterCapability) -> None:
    require_programmatic_capability(
        capability, _CAPABILITY_SHA256, "XandrAdapter"
    )
    variant = _VARIANTS.get(capability.adapter_id)
    if not (
        variant is not None
        and capability.platform == "xandr"
        and capability.report_type == variant[1]
        and capability.container == variant[0]
        and capability.locale == "invariant"
        and capability.row_grain == variant[2]
        and capability.identity_fields == variant[2]
        and capability.required_fields == variant[3]
        and capability.metric_fields == variant[4]
        and capability.time_basis == "member_reporting_day"
        and capability.currency_basis == "advertiser_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    ):
        raise AdapterError(
            "XandrAdapter requires a registered schema-tested exact variant"
        )


class XandrAdapter(ExactVariantAdapter):
    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("Xandr adapter capability is invalid")
        _require_capability(capability)
        super().__init__(capability)

    def normalize(
        self,
        inventory: object,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterResult:
        if capability != self.capability:
            raise AdapterError("adapter capability does not match adapter")
        _require_capability(capability)
        payload = require_closed_object(inventory, _ROOT_KEYS, "Xandr export")
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "Xandr reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("Xandr rows must be a non-empty list")
        if metadata["decimal_mark"] != ".":
            raise AdapterError("Xandr decimal_mark policy must be '.'")
        report_start = require_date(
            metadata["report_start_date"], "report_start_date"
        )
        report_end_exclusive = require_date(
            metadata["report_end_exclusive"], "report_end_exclusive"
        )
        start_value = date.fromisoformat(report_start)
        end_value = date.fromisoformat(report_end_exclusive)
        if end_value <= start_value:
            raise AdapterError(
                "Xandr report end must be exclusive and after start"
            )
        report_mode = require_string(metadata["report_mode"], "report_mode")
        if report_mode not in {"ordinary", "historical"}:
            raise AdapterError("Xandr report_mode is unsupported")
        timezone = require_string(
            metadata["reporting_timezone"], "reporting_timezone"
        )
        if report_mode == "historical" and timezone != "UTC":
            raise AdapterError("Xandr historical reports must use UTC")
        latency_state = require_string(
            metadata["conversion_latency_state"],
            "conversion_latency_state",
        )
        if latency_state not in LATENCY_STATES:
            raise AdapterError("conversion_latency_state is unsupported")
        conversion_quality = require_string(
            metadata["conversion_value_state"],
            "conversion_value_state",
        )
        if conversion_quality not in CONVERSION_QUALITY_STATES:
            raise AdapterError("conversion_value_state is unsupported")
        omitted_zero = require_string(
            metadata["omitted_zero_behavior"], "omitted_zero_behavior"
        )
        if omitted_zero != _OMITTED_ZERO:
            raise AdapterError(
                "Xandr exact variants require their omitted-zero behavior"
            )
        click_window = require_string(
            metadata["click_window"], "click_window"
        )
        view_window = require_string(
            metadata["view_window"], "view_window"
        )
        if [click_window, view_window] != require_string_list(
            registration.get("attribution_windows"), "attribution_windows"
        ):
            raise AdapterError(
                "Xandr attribution windows do not match registration"
            )

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"), "registration_id"
        )
        metric_id = require_string(registration.get("metric_id"), "metric_id")
        source_metric = require_string(
            registration.get("registered_source_metric"),
            "registered_source_metric",
        )
        if source_metric not in capability.metric_fields:
            raise AdapterError("registered_source_metric is unsupported")
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])
        currency = require_string(
            metadata["advertiser_currency"], "advertiser_currency"
        )
        observed_at = require_timestamp(metadata["observed_at"], "observed_at")

        (
            advertiser_field,
            campaign_field,
            insertion_order_field,
            line_item_field,
            creative_field,
            date_field,
        ) = capability.identity_fields
        impressions_field, clicks_field, spend_field = capability.required_fields
        allowed = {
            "source_row_reference",
            *capability.identity_fields,
            *capability.required_fields,
            *capability.metric_fields,
        }
        required = {
            "source_row_reference",
            *capability.identity_fields,
            *capability.required_fields,
            source_metric,
        }
        normalized: list[dict[str, object]] = []
        quarantined: list[dict[str, object]] = []
        identity_context: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw_row in enumerate(rows):
            path = f"Xandr rows[{index}]"
            row = require_closed_object(
                raw_row, allowed, path, required=required
            )
            reference = require_string(
                row["source_row_reference"], f"{path}.source_row_reference"
            )
            if reference in seen:
                raise AdapterError(
                    "Xandr source_row_reference values must be unique"
                )
            seen.add(reference)
            reporting_date = require_date(
                row[date_field], f"{path}.{date_field}"
            )
            if not (
                start_value <= date.fromisoformat(reporting_date) < end_value
            ):
                raise AdapterError(
                    "Xandr row date is outside the exclusive-end range"
                )
            impressions_text, impressions = require_nonnegative_count(
                row[impressions_field],
                f"{path}.{impressions_field}",
                strings_only=True,
            )
            clicks_text, clicks = require_nonnegative_count(
                row[clicks_field],
                f"{path}.{clicks_field}",
                strings_only=True,
            )
            spend_text, spend = require_nonnegative_decimal(
                row[spend_field],
                f"{path}.{spend_field}",
                strings_only=True,
            )
            metric_values = {}
            metric_text = {}
            for field in capability.metric_fields:
                if field in row:
                    text, value = require_nonnegative_decimal(
                        row[field], f"{path}.{field}", strings_only=True
                    )
                    metric_text[field] = text
                    metric_values[field] = value
            creative_state, creative_id = xandr_creative_state(
                row[creative_field]
            )
            insertion_order_id = require_string(
                row[insertion_order_field],
                f"{path}.{insertion_order_field}",
            )
            line_item_id = require_string(
                row[line_item_field], f"{path}.{line_item_field}"
            )
            identity_context.append(
                {
                    "source_row_reference": reference,
                    "insertion_order_id": insertion_order_id,
                    "line_item_id": line_item_id,
                }
            )
            if creative_state == "external_tracker":
                quarantined.append(
                    {
                        "source_row_reference": reference,
                        "creative_id_state": "external_tracker",
                        "source_creative_id": require_string(
                            row[creative_field], f"{path}.{creative_field}"
                        ),
                        "reason": (
                            "external tracker sentinel is not a platform "
                            "creative identity"
                        ),
                    }
                )
                continue
            assert creative_id is not None
            attribution_kind = (
                "click"
                if source_metric
                in {"post_click_convs", "Post Click Convs", "Post-click Conversions"}
                else "view"
            )
            selected_window = (
                click_window if attribution_kind == "click" else view_window
            )
            normalized.append(
                build_rich_observation(
                    capability=capability,
                    study_id=study_id,
                    registration_id=registration_id,
                    import_id=import_id,
                    source_id=source_id,
                    source_sha256=source_sha256,
                    source_row_reference=reference,
                    metric_id=metric_id,
                    account_id=require_string(
                        row[advertiser_field], f"{path}.{advertiser_field}"
                    ),
                    campaign_id=require_string(
                        row[campaign_field], f"{path}.{campaign_field}"
                    ),
                    ad_group_id=line_item_id,
                    creative_id=creative_id,
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": timezone,
                        "basis": (
                            "historical_utc_day"
                            if report_mode == "historical"
                            else "member_reporting_day"
                        ),
                        "request_level": "creative",
                        "time_increment": "1",
                        "segment_grain": list(capability.identity_fields),
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": f"post_{attribution_kind}",
                        "windows": [selected_window],
                    },
                    platform_semantics=build_platform_semantics(
                        billed_currency=None,
                        currency_relationship="not_applicable",
                        privacy_review_state="not_applicable",
                        demographic_truncation_state="not_applicable",
                        click_semantic="all_clicks",
                        optimization_event=None,
                        delivery_state=(
                            "standard"
                            if latency_state == "mature"
                            else "delayed"
                        ),
                        skan_state="non_skan",
                        search_term_id=None,
                        search_term_state="not_applicable",
                    ),
                    currency_code=currency,
                    spend=spend,
                    spend_decimal_text=spend_text,
                    spend_source_text=spend_text,
                    spend_source_metric=spend_field,
                    spend_source_unit="advertiser_currency",
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=metric_values[source_metric],
                    outcome_source_text=metric_text[source_metric],
                    outcome_source_metric=source_metric,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior=omitted_zero,
                )
            )

        return AdapterResult(
            adapter_id=capability.adapter_id,
            adapter_version=capability.adapter_version,
            maturity=capability.maturity,
            source_sha256=source_sha256,
            source_rows=len(rows),
            normalized_rows=tuple(normalized),
            quarantined_rows=tuple(quarantined),
            mapping_report={
                "adapter_id": capability.adapter_id,
                "adapter_version": capability.adapter_version,
                "maturity": capability.maturity,
                "operational_status": "incomplete",
                "contract_ready": False,
                "normalized_row_count": len(normalized),
                "quarantined_row_count": len(quarantined),
                "source_semantics": {
                    "decimal_mark": ".",
                    "report_start_date": report_start,
                    "report_end_exclusive": report_end_exclusive,
                    "report_mode": report_mode,
                    "reporting_timezone": timezone,
                    "conversion_latency_state": latency_state,
                    "attribution_kind": (
                        "click"
                        if source_metric
                        in {
                            "post_click_convs",
                            "Post Click Convs",
                            "Post-click Conversions",
                        }
                        else "view"
                    ),
                    "row_identity_context": identity_context,
                },
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
