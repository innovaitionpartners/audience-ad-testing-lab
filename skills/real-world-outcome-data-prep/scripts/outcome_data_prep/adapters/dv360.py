"""Semantic normalizer for registered DV360 Bid Manager v2 variants."""

from __future__ import annotations

from collections.abc import Mapping

from ..capabilities import AdapterCapability
from ..common import ContractError, require_enum, require_identifier
from .base import (
    AdapterError,
    AdapterResult,
    ExactVariantAdapter,
    require_no_prohibited_business_data,
)
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
    "dv360-bid-manager-v2-standard-csv-v1": (
        "csv",
        ("Impressions", "Clicks", "Media Cost (Advertiser Currency)"),
        ("Total Conversions",),
    ),
    "dv360-bid-manager-v2-standard-xlsx-v1": (
        "xlsx",
        ("Impressions", "Clicks", "Media Cost"),
        ("Total Conversions",),
    ),
}
_CAPABILITY_SHA256 = {
    "dv360-bid-manager-v2-standard-csv-v1": (
        "sha256:af5389d9d282a4a3c28602b7d7a508f6ffac3a83ad3340e36495eb0f9dbcdfd8"
    ),
    "dv360-bid-manager-v2-standard-xlsx-v1": (
        "sha256:90c47f8dd594c94daa15c1678d2696e38311d68d8274e8da0f25580b4d8a35a5"
    ),
}
_IDENTITIES = (
    "Advertiser ID",
    "Campaign ID",
    "Insertion Order ID",
    "Line Item ID",
    "Creative ID",
    "Date",
)
_REPORTING_KEYS = {
    "query_id",
    "report_type",
    "timezone_basis",
    "reporting_timezone",
    "currency_code",
    "cost_basis",
    "attribution_windows",
    "conversion_value_state",
    "latency_state",
    "observed_at",
    "omitted_zero_behavior",
    "mutability_days",
    "dimension_metric_compatible",
}
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_OMITTED_ZERO = "omitted_metrics_are_unknown_not_zero"
def _contract_call(function, *args):
    try:
        return function(*args)
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc


def require_dv360_report_context(
    metadata: Mapping[str, object],
    *,
    allowed_report_types: set[str],
) -> tuple[str, str, str]:
    return (
        _contract_call(
            require_identifier, metadata.get("query_id"), "query_id"
        ),
        _contract_call(
            require_enum,
            metadata.get("report_type"),
            allowed_report_types,
            "report_type",
        ),
        _contract_call(
            require_enum,
            metadata.get("timezone_basis"),
            {"advertiser", "utc"},
            "timezone_basis",
        ),
    )


def _require_capability(capability: AdapterCapability) -> None:
    require_programmatic_capability(
        capability, _CAPABILITY_SHA256, "DV360Adapter"
    )
    variant = _VARIANTS.get(capability.adapter_id)
    if not (
        variant is not None
        and capability.platform == "dv360"
        and capability.report_type == "bid_manager_v2_standard"
        and capability.container == variant[0]
        and capability.locale == "invariant"
        and capability.row_grain == _IDENTITIES
        and capability.identity_fields == _IDENTITIES
        and capability.required_fields == variant[1]
        and capability.metric_fields == variant[2]
        and capability.time_basis == "advertiser_reporting_day"
        and capability.currency_basis == "advertiser_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    ):
        raise AdapterError(
            "DV360Adapter requires a registered schema-tested exact variant"
        )


class DV360Adapter(ExactVariantAdapter):
    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("DV360 adapter capability is invalid")
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
        payload = require_closed_object(inventory, _ROOT_KEYS, "DV360 export")
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "DV360 reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("DV360 rows must be a non-empty list")
        require_no_prohibited_business_data(
            tuple(
                key
                for row in rows
                if isinstance(row, Mapping)
                for key in row
            ),
            context="DV360 export",
        )

        query_id, report_type, timezone_basis = require_dv360_report_context(
            metadata, allowed_report_types={"STANDARD"}
        )
        reporting_timezone = require_string(
            metadata["reporting_timezone"], "reporting_timezone"
        )
        if timezone_basis == "utc" and reporting_timezone != "UTC":
            raise AdapterError("DV360 UTC timezone basis requires UTC")
        if metadata["dimension_metric_compatible"] is not True:
            raise AdapterError(
                "DV360 query dimensions and metrics are not compatible"
            )
        if metadata["mutability_days"] != 31:
            raise AdapterError("DV360 conversion data requires 31-day mutability")
        cost_basis = require_string(metadata["cost_basis"], "cost_basis")
        if cost_basis != capability.currency_basis:
            raise AdapterError(
                "DV360 cost_basis does not match the exact "
                "advertiser-currency capability"
            )
        if registration.get("cost_basis") != cost_basis:
            raise AdapterError(
                "DV360 cost_basis does not match sealed registration metadata"
            )
        currency_code = require_string(
            metadata["currency_code"], "currency_code"
        )
        windows = require_string_list(
            metadata["attribution_windows"], "attribution_windows"
        )
        registered_windows = require_string_list(
            registration.get("attribution_windows"), "attribution_windows"
        )
        if windows != registered_windows:
            raise AdapterError(
                "DV360 attribution_windows do not match registration"
            )
        conversion_quality = require_string(
            metadata["conversion_value_state"],
            "conversion_value_state",
        )
        if conversion_quality not in CONVERSION_QUALITY_STATES:
            raise AdapterError("conversion_value_state is unsupported")
        latency_state = require_string(
            metadata["latency_state"], "latency_state"
        )
        if latency_state not in LATENCY_STATES:
            raise AdapterError("latency_state is unsupported")
        omitted_zero = require_string(
            metadata["omitted_zero_behavior"], "omitted_zero_behavior"
        )
        if omitted_zero != _OMITTED_ZERO:
            raise AdapterError(
                "DV360 exact variants require their omitted-zero behavior"
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
        require_no_prohibited_business_data(
            (source_metric,),
            context="DV360 registered source metric",
        )
        if source_metric not in capability.metric_fields:
            raise AdapterError("registered_source_metric is unsupported")
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])
        observed_at = require_timestamp(metadata["observed_at"], "observed_at")
        spend_metric = capability.required_fields[2]

        normalized: list[dict[str, object]] = []
        identity_context: list[dict[str, str]] = []
        seen: set[str] = set()
        allowed_row_keys = {
            "source_row_reference",
            *capability.identity_fields,
            *capability.required_fields,
            *capability.metric_fields,
        }
        required_row_keys = {
            "source_row_reference",
            *capability.identity_fields,
            *capability.required_fields,
            source_metric,
        }
        for index, raw_row in enumerate(rows):
            path = f"DV360 rows[{index}]"
            row = require_closed_object(
                raw_row,
                allowed_row_keys,
                path,
                required=required_row_keys,
            )
            reference = require_string(
                row["source_row_reference"], f"{path}.source_row_reference"
            )
            if reference in seen:
                raise AdapterError(
                    "DV360 source_row_reference values must be unique"
                )
            seen.add(reference)
            identities = {
                field: require_string(row[field], f"{path}.{field}")
                for field in _IDENTITIES[:-1]
            }
            reporting_date = require_date(row["Date"], f"{path}.Date")
            impressions_text, impressions = require_nonnegative_count(
                row["Impressions"], f"{path}.Impressions", strings_only=True
            )
            clicks_text, clicks = require_nonnegative_count(
                row["Clicks"], f"{path}.Clicks", strings_only=True
            )
            spend_text, spend = require_nonnegative_decimal(
                row[spend_metric], f"{path}.{spend_metric}", strings_only=True
            )
            metric_text: dict[str, str] = {}
            for field in capability.metric_fields:
                if field in row:
                    text, _ = require_nonnegative_decimal(
                        row[field], f"{path}.{field}", strings_only=True
                    )
                    metric_text[field] = text
            _, outcome = require_nonnegative_decimal(
                row[source_metric],
                f"{path}.{source_metric}",
                strings_only=True,
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
                    account_id=identities["Advertiser ID"],
                    campaign_id=identities["Campaign ID"],
                    ad_group_id=identities["Line Item ID"],
                    creative_id=identities["Creative ID"],
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": reporting_timezone,
                        "basis": (
                            "utc_reporting_day"
                            if timezone_basis == "utc"
                            else "advertiser_reporting_day"
                        ),
                        "request_level": "creative",
                        "time_increment": "1",
                        "segment_grain": list(_IDENTITIES),
                        "latency_state": latency_state,
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
                        delivery_state="not_applicable",
                        skan_state="not_applicable",
                        search_term_id=None,
                        search_term_state="not_applicable",
                    ),
                    currency_code=currency_code,
                    spend=spend,
                    spend_decimal_text=spend_text,
                    spend_source_text=spend_text,
                    spend_source_metric=spend_metric,
                    spend_source_unit=cost_basis,
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=metric_text[source_metric],
                    outcome_source_metric=source_metric,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior=omitted_zero,
                )
            )
            identity_context.append(
                {
                    "source_row_reference": reference,
                    "insertion_order_id": identities["Insertion Order ID"],
                    "line_item_id": identities["Line Item ID"],
                }
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
                    "query_id": query_id,
                    "report_type": report_type,
                    "timezone_basis": timezone_basis,
                    "cost_basis": cost_basis,
                    "mutability_days": 31,
                    "dimension_metric_compatible": True,
                    "row_identity_context": identity_context,
                },
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
