"""Semantic normalizer for registered The Trade Desk report variants."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from ..capabilities import AdapterCapability
from ..common import ContractError, require_identifier
from .base import (
    AdapterError,
    AdapterResult,
    ExactVariantAdapter,
    require_no_prohibited_business_data,
)
from .programmatic_common import require_programmatic_capability
from .semantic_common import (
    CONVERSION_QUALITY_STATES,
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
    "trade-desk-report-template-csv-v1": (
        "csv",
        "report_template_ad_daily",
        "report_template_timezone_day",
    ),
    "trade-desk-report-template-tsv-v1": (
        "tsv",
        "report_template_ad_daily",
        "report_template_timezone_day",
    ),
    "trade-desk-report-type-xlsx-v1": (
        "xlsx",
        "report_type_ad_daily",
        "report_type_timezone_day",
    ),
}
_CAPABILITY_SHA256 = {
    "trade-desk-report-template-csv-v1": (
        "sha256:18bc63bfe69de088780c4510b76ef48b52d67f137f29887f7e0fd92218b24e22"
    ),
    "trade-desk-report-template-tsv-v1": (
        "sha256:e2d1d8a63cdb446808b0b903834454ba2bd960f3e38e99ff11f510602080f047"
    ),
    "trade-desk-report-type-xlsx-v1": (
        "sha256:200f63af85a71442fe2266ead44276c110b3926b6ade28a4a8552c24c8ff47c4"
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
    "template_id",
    "schedule_id",
    "file_format",
    "date_format",
    "numeric_format",
    "schedule_timezone",
    "reporting_timezone",
    "report_start_date",
    "report_end_exclusive",
    "completed_report_state",
    "late_offline_conversion_state",
    "release_note_version",
    "schema_version",
    "advertiser_currency",
    "attribution_windows",
    "conversion_value_state",
    "observed_at",
    "omitted_zero_behavior",
}
_OMITTED_ZERO = "omitted_metrics_are_unknown_not_zero"


def require_ttd_report_identity(
    metadata: Mapping[str, object],
) -> tuple[str, str]:
    try:
        template_id = require_identifier(
            metadata.get("template_id"), "template_id"
        )
        schedule_id = require_identifier(
            metadata.get("schedule_id"), "schedule_id"
        )
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc
    return template_id, schedule_id


def _variant_fields(
    capability: AdapterCapability,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if capability.container in {"csv", "tsv"}:
        return (
            ("AdvertiserId", "CampaignId", "AdGroupId", "CreativeId", "Date"),
            ("Impressions", "Clicks", "AdvertiserCost"),
            ("Conversions",),
        )
    return (
        (
            "Advertiser ID",
            "Campaign ID",
            "Ad Group ID",
            "Creative ID",
            "Report Date",
        ),
        ("Impression Count", "Click Count", "Advertiser Cost"),
        ("Conversion Count",),
    )


def _require_capability(capability: AdapterCapability) -> None:
    require_programmatic_capability(
        capability, _CAPABILITY_SHA256, "TradeDeskAdapter"
    )
    variant = _VARIANTS.get(capability.adapter_id)
    identities, required, metrics = _variant_fields(capability)
    if not (
        variant is not None
        and capability.platform == "the_trade_desk"
        and capability.report_type == variant[1]
        and capability.container == variant[0]
        and capability.locale == "invariant"
        and capability.row_grain == identities
        and capability.identity_fields == identities
        and capability.required_fields == required
        and capability.metric_fields == metrics
        and capability.time_basis == variant[2]
        and capability.currency_basis == "advertiser_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    ):
        raise AdapterError(
            "TradeDeskAdapter requires a registered schema-tested exact variant"
        )


class TradeDeskAdapter(ExactVariantAdapter):
    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("Trade Desk adapter capability is invalid")
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
        payload = require_closed_object(
            inventory, _ROOT_KEYS, "Trade Desk export"
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "Trade Desk reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("Trade Desk rows must be a non-empty list")
        require_no_prohibited_business_data(
            tuple(
                key
                for row in rows
                if isinstance(row, Mapping)
                for key in row
            ),
            context="Trade Desk export",
        )

        template_id, schedule_id = require_ttd_report_identity(metadata)
        file_format = require_string(metadata["file_format"], "file_format")
        if file_format != capability.container:
            raise AdapterError(
                "Trade Desk file_format does not match exact variant"
            )
        if metadata["date_format"] != "yyyy-MM-dd":
            raise AdapterError("Trade Desk date_format is unsupported")
        if metadata["numeric_format"] != "decimal_point_no_grouping":
            raise AdapterError("Trade Desk numeric_format is unsupported")
        schedule_timezone = require_string(
            metadata["schedule_timezone"], "schedule_timezone"
        )
        reporting_timezone = require_string(
            metadata["reporting_timezone"], "reporting_timezone"
        )
        if reporting_timezone != "UTC":
            raise AdapterError("Trade Desk reporting basis must be UTC")
        report_start = require_date(
            metadata["report_start_date"], "report_start_date"
        )
        report_end_exclusive = require_date(
            metadata["report_end_exclusive"], "report_end_exclusive"
        )
        if date.fromisoformat(report_end_exclusive) <= date.fromisoformat(
            report_start
        ):
            raise AdapterError(
                "Trade Desk report end must be exclusive and after start"
            )
        if metadata["completed_report_state"] != "immutable_completed":
            raise AdapterError(
                "Trade Desk completed reports must be immutable"
            )
        late_state = require_string(
            metadata["late_offline_conversion_state"],
            "late_offline_conversion_state",
        )
        if late_state not in {"mature", "mutable_late_offline"}:
            raise AdapterError(
                "late_offline_conversion_state is unsupported"
            )
        release_note_version = require_string(
            metadata["release_note_version"], "release_note_version"
        )
        schema_version = require_string(
            metadata["schema_version"], "schema_version"
        )
        currency = require_string(
            metadata["advertiser_currency"], "advertiser_currency"
        )
        windows = require_string_list(
            metadata["attribution_windows"], "attribution_windows"
        )
        if windows != require_string_list(
            registration.get("attribution_windows"), "attribution_windows"
        ):
            raise AdapterError(
                "Trade Desk attribution_windows do not match registration"
            )
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
                "Trade Desk exact variants require their omitted-zero behavior"
            )
        observed_at = require_timestamp(metadata["observed_at"], "observed_at")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"), "registration_id"
        )
        metric_id = require_string(registration.get("metric_id"), "metric_id")
        source_metric = require_string(
            registration.get("registered_source_metric"),
            "registered_source_metric",
        )
        require_no_prohibited_business_data(
            (source_metric,),
            context="Trade Desk registered source metric",
        )
        if source_metric not in capability.metric_fields:
            raise AdapterError("registered_source_metric is unsupported")
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])
        identities, required_fields, _ = _variant_fields(capability)
        (
            advertiser_field,
            campaign_field,
            ad_group_field,
            creative_field,
            date_field,
        ) = identities
        impressions_field, clicks_field, spend_field = required_fields
        allowed = {
            "source_row_reference",
            *identities,
            *required_fields,
            *capability.metric_fields,
        }
        required = {
            "source_row_reference",
            *identities,
            *required_fields,
            source_metric,
        }
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for index, raw_row in enumerate(rows):
            path = f"Trade Desk rows[{index}]"
            row = require_closed_object(
                raw_row, allowed, path, required=required
            )
            reference = require_string(
                row["source_row_reference"], f"{path}.source_row_reference"
            )
            if reference in seen:
                raise AdapterError(
                    "Trade Desk source_row_reference values must be unique"
                )
            seen.add(reference)
            reporting_date = require_date(
                row[date_field], f"{path}.{date_field}"
            )
            parsed_date = date.fromisoformat(reporting_date)
            if not (
                date.fromisoformat(report_start)
                <= parsed_date
                < date.fromisoformat(report_end_exclusive)
            ):
                raise AdapterError(
                    "Trade Desk row date is outside inclusive-start/"
                    "exclusive-end boundaries"
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
            metric_text: dict[str, str] = {}
            metric_values = {}
            for field in capability.metric_fields:
                if field in row:
                    text, value = require_nonnegative_decimal(
                        row[field], f"{path}.{field}", strings_only=True
                    )
                    metric_text[field] = text
                    metric_values[field] = value
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
                    ad_group_id=require_string(
                        row[ad_group_field], f"{path}.{ad_group_field}"
                    ),
                    creative_id=require_string(
                        row[creative_field], f"{path}.{creative_field}"
                    ),
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": "UTC",
                        "basis": "utc_reporting_day",
                        "request_level": "creative",
                        "time_increment": "1",
                        "segment_grain": list(identities),
                        "latency_state": (
                            "mature"
                            if late_state == "mature"
                            else "immature"
                        ),
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": "platform_attribution",
                        "windows": windows,
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
                            if late_state == "mature"
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
                    "template_id": template_id,
                    "schedule_id": schedule_id,
                    "schedule_timezone": schedule_timezone,
                    "reporting_basis": "utc_reporting_day",
                    "report_start_date": report_start,
                    "report_end_exclusive": report_end_exclusive,
                    "completed_report_state": "immutable_completed",
                    "late_offline_conversion_state": late_state,
                    "release_note_version": release_note_version,
                    "schema_version": schema_version,
                },
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
