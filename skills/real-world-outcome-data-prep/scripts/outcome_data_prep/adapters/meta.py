"""Semantic normalizer for the exact Meta Insights API JSON variant."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from ..capabilities import AdapterCapability
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


_ADAPTER_ID = "meta-insights-api-json-v1"
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "account_currency",
    "account_timezone",
    "request_level",
    "action_report_time",
    "attribution_windows",
    "time_increment",
    "reporting_basis",
    "latency_state",
    "conversion_value_state",
    "observed_at",
}
_ROW_KEYS = {
    "source_row_reference",
    "account_id",
    "campaign_id",
    "adset_id",
    "ad_id",
    "date_start",
    "date_stop",
    "impressions",
    "clicks",
    "spend",
    "actions",
}
_ACTION_KEYS = {"action_type", "value"}
_EXACT_IDENTITIES = (
    "account_id",
    "campaign_id",
    "adset_id",
    "ad_id",
    "date_start",
    "date_stop",
)
_EXACT_REQUIRED_FIELDS = ("impressions", "clicks", "spend")
_EXACT_METRIC_FIELDS = ("actions",)
_EXACT_REQUEST_LEVEL = "ad"
_EXACT_TIME_INCREMENT = "1"
_EXACT_REPORTING_BASIS = "account_reporting_day"


def _require_exact_capability(capability: AdapterCapability) -> None:
    expected = (
        capability.adapter_id == _ADAPTER_ID
        and capability.platform == "meta_ads"
        and capability.report_type == "insights_api_ad_daily"
        and capability.container == "json"
        and capability.locale == "invariant"
        and capability.row_grain == _EXACT_IDENTITIES
        and capability.identity_fields == _EXACT_IDENTITIES
        and capability.required_fields == _EXACT_REQUIRED_FIELDS
        and capability.metric_fields == _EXACT_METRIC_FIELDS
        and capability.time_basis == "configured_action_report_time"
        and capability.currency_basis == "account_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    )
    if not expected:
        raise AdapterError(
            "MetaInsightsAdapter requires its schema-tested exact adapter variant"
        )


def selected_meta_action(
    actions: Sequence[Mapping[str, object]],
    conversion_event_key: str,
) -> Decimal:
    """Select one configured action after all admitted actions validate."""

    if (
        isinstance(actions, (str, bytes))
        or not isinstance(actions, Sequence)
    ):
        raise AdapterError("actions must be a sequence")
    matches = [
        item
        for item in actions
        if isinstance(item, Mapping)
        and item.get("action_type") == conversion_event_key
    ]
    if len(matches) != 1:
        raise AdapterError(
            "conversion_event_key must select exactly one Meta action"
        )
    _, selected = require_nonnegative_decimal(
        matches[0].get("value"),
        "actions.value",
        strings_only=True,
    )
    return selected


class MetaInsightsAdapter(ExactVariantAdapter):
    """Normalize exact Meta Insights API rows after Task 5 admission."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("Meta adapter capability is invalid")
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
        payload = require_closed_object(inventory, _ROOT_KEYS, "Meta export")
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "Meta reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("Meta rows must be a non-empty list")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"), "registration_id"
        )
        metric_id = require_string(
            registration.get("metric_id"), "metric_id"
        )
        conversion_event_key = require_string(
            registration.get("conversion_event_key"),
            "conversion_event_key",
        )
        request_level = require_string(
            registration.get("request_level"), "request_level"
        )
        action_report_time = require_string(
            registration.get("action_report_time"), "action_report_time"
        )
        attribution_windows = require_string_list(
            registration.get("attribution_windows"),
            "attribution_windows",
        )
        time_increment = require_string(
            registration.get("time_increment"), "time_increment"
        )

        if (
            request_level != _EXACT_REQUEST_LEVEL
            or metadata["request_level"] != _EXACT_REQUEST_LEVEL
            or time_increment != _EXACT_TIME_INCREMENT
            or metadata["time_increment"] != _EXACT_TIME_INCREMENT
            or metadata["reporting_basis"] != _EXACT_REPORTING_BASIS
        ):
            raise AdapterError(
                "Meta exact ad-daily variant requires request_level, "
                "time_increment, and reporting basis"
            )
        for field, expected in (
            ("action_report_time", action_report_time),
            ("attribution_windows", attribution_windows),
        ):
            if metadata[field] != expected:
                raise AdapterError(
                    f"Meta {field} does not match registered configuration"
                )

        account_currency = require_string(
            metadata["account_currency"], "account_currency"
        )
        account_timezone = require_string(
            metadata["account_timezone"], "account_timezone"
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
            path = f"Meta rows[{index}]"
            row = require_closed_object(raw_row, _ROW_KEYS, path)
            source_row_reference = require_string(
                row["source_row_reference"],
                f"{path}.source_row_reference",
            )
            if source_row_reference in seen_row_references:
                raise AdapterError(
                    "Meta source_row_reference values must be unique"
                )
            seen_row_references.add(source_row_reference)
            start = require_date(row["date_start"], f"{path}.date_start")
            stop = require_date(row["date_stop"], f"{path}.date_stop")
            if date.fromisoformat(start) != date.fromisoformat(stop):
                raise AdapterError(
                    "Meta exact ad-daily variant requires one row date"
                )
            impressions_text, impressions = require_nonnegative_count(
                row["impressions"],
                f"{path}.impressions",
                strings_only=True,
            )
            clicks_text, clicks = require_nonnegative_count(
                row["clicks"],
                f"{path}.clicks",
                strings_only=True,
            )
            spend_text, spend = require_nonnegative_decimal(
                row["spend"], f"{path}.spend", strings_only=True
            )
            actions = row["actions"]
            if not isinstance(actions, list):
                raise AdapterError(f"{path}.actions must be a list")
            checked_actions: list[dict[str, object]] = []
            action_text_by_type: dict[str, str] = {}
            for action_index, raw_action in enumerate(actions):
                action_path = f"{path}.actions[{action_index}]"
                action = require_closed_object(
                    raw_action, _ACTION_KEYS, action_path
                )
                action_type = require_string(
                    action["action_type"], f"{action_path}.action_type"
                )
                action_text, _ = require_nonnegative_decimal(
                    action["value"],
                    f"{action_path}.value",
                    strings_only=True,
                )
                checked_actions.append(action)
                if action_type == conversion_event_key:
                    action_text_by_type[action_type] = action_text
            outcome = selected_meta_action(
                checked_actions, conversion_event_key
            )
            outcome_text = action_text_by_type[conversion_event_key]
            identities = {
                field: require_string(row[field], f"{path}.{field}")
                for field in ("account_id", "campaign_id", "adset_id", "ad_id")
            }
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
                    account_id=identities["account_id"],
                    campaign_id=identities["campaign_id"],
                    ad_group_id=identities["adset_id"],
                    creative_id=identities["ad_id"],
                    reporting={
                        "start_date": start,
                        "end_date": stop,
                        "timezone": account_timezone,
                        "basis": _EXACT_REPORTING_BASIS,
                        "request_level": _EXACT_REQUEST_LEVEL,
                        "time_increment": _EXACT_TIME_INCREMENT,
                        "segment_grain": list(capability.identity_fields),
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": action_report_time,
                        "windows": list(attribution_windows),
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
                    currency_code=account_currency,
                    spend=spend,
                    spend_decimal_text=spend_text,
                    spend_source_text=spend_text,
                    spend_source_metric="spend",
                    spend_source_unit="account_currency_units",
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=outcome_text,
                    outcome_source_metric=conversion_event_key,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior="not_applicable",
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
