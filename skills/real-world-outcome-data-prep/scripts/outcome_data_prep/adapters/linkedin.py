"""Semantic normalizer for the exact LinkedIn Ads Reporting API variant."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

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


_ADAPTER_ID = "linkedin-ads-reporting-api-json-v1"
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "account_currency",
    "billed_currency",
    "reporting_timezone",
    "pivot",
    "time_granularity",
    "attribution_windows",
    "privacy_state",
    "demographic_truncation",
    "currency_state",
    "latency_state",
    "conversion_value_state",
    "observed_at",
    "omitted_zero_behavior",
}
_ROW_KEYS = {
    "source_row_reference",
    "pivotValues",
    "dateRange",
    "impressions",
    "clicks",
    "costInLocalCurrency",
    "externalWebsiteConversions",
    "oneClickLeads",
}
_DATE_RANGE_KEYS = {"start", "end"}
_EXACT_IDENTITIES = (
    "account",
    "campaign",
    "creative",
    "dateRange.start",
    "dateRange.end",
)
_EXACT_REQUIRED_FIELDS = (
    "impressions",
    "clicks",
    "costInLocalCurrency",
)
_EXACT_METRIC_FIELDS = (
    "externalWebsiteConversions",
    "oneClickLeads",
)
_EXACT_PIVOT = ("ACCOUNT", "CAMPAIGN", "CREATIVE")
_PIVOT_URN_PREFIXES = (
    "urn:li:sponsoredAccount:",
    "urn:li:sponsoredCampaign:",
    "urn:li:sponsoredCreative:",
)
_TIME_GRANULARITIES = frozenset({"DAILY", "PERIOD"})
_PRIVACY_STATES = frozenset(
    {
        "aggregate_privacy_reviewed",
        "privacy_suppressed",
        "no_access",
    }
)
_DEMOGRAPHIC_TRUNCATION_STATES = frozenset(
    {"not_applicable", "top_100_categories"}
)
_OMITTED_ZERO_BEHAVIOR = "omitted_metrics_are_unknown_not_zero"


def _require_exact_capability(capability: AdapterCapability) -> None:
    expected = (
        capability.adapter_id == _ADAPTER_ID
        and capability.platform == "linkedin_ads"
        and capability.report_type == "reporting_api_creative_daily"
        and capability.container == "json"
        and capability.locale == "invariant"
        and capability.row_grain == _EXACT_IDENTITIES
        and capability.identity_fields == _EXACT_IDENTITIES
        and capability.required_fields == _EXACT_REQUIRED_FIELDS
        and capability.metric_fields == _EXACT_METRIC_FIELDS
        and capability.time_basis == "account_reporting_day"
        and capability.currency_basis == "account_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    )
    if not expected:
        raise AdapterError(
            "LinkedInAdsAdapter requires its schema-tested exact adapter variant"
        )


def linkedin_identity(pivot_values: object) -> tuple[str, ...]:
    values = require_string_list(pivot_values, "pivotValues")
    if not values or any(not value.startswith("urn:li:") for value in values):
        raise AdapterError("LinkedIn pivotValues must preserve full URNs")
    return tuple(values)


def linkedin_conversion(
    row: Mapping[str, object], conversion_source_field: str
) -> Decimal:
    if not conversion_source_field:
        raise AdapterError("conversion_source_field is required")
    if conversion_source_field not in row:
        raise AdapterError("configured LinkedIn conversion field is absent")
    try:
        result = Decimal(str(row[conversion_source_field]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdapterError(
            f"{conversion_source_field} must be numeric"
        ) from exc
    if not result.is_finite() or result < 0:
        raise AdapterError(
            f"{conversion_source_field} must be non-negative and finite"
        )
    return result


def _linkedin_metric_value(
    value: object,
    path: str,
) -> tuple[str, Decimal | None, str | None]:
    if value == "<5":
        return "suppressed", None, None
    if value == "NO_ACCESS":
        return "absent", None, None
    if value in (None, ""):
        return "null", None, None
    source_text, parsed = require_nonnegative_decimal(
        value,
        path,
        strings_only=False,
    )
    return "observed", parsed, source_text


def _currency_state(
    account_currency: str,
    billed_currency: str,
    state: str,
) -> None:
    expected = (
        "local_currency_equals_billed_currency"
        if account_currency == billed_currency
        else "local_currency_distinct_from_billed_currency"
    )
    if state != expected:
        raise AdapterError(
            "LinkedIn currency state must preserve local-versus-billed currency"
        )


class LinkedInAdsAdapter(ExactVariantAdapter):
    """Normalize exact LinkedIn Ads Reporting API rows after admission."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("LinkedIn adapter capability is invalid")
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
            "LinkedIn export",
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "LinkedIn reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError("LinkedIn rows must be a non-empty list")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"),
            "registration_id",
        )
        metric_id = require_string(
            registration.get("metric_id"),
            "metric_id",
        )
        conversion_source = registration.get("conversion_source_field")
        if not isinstance(conversion_source, str) or not conversion_source:
            raise AdapterError("conversion_source_field is required")
        if conversion_source not in _EXACT_METRIC_FIELDS:
            raise AdapterError(
                "conversion_source_field is unsupported for the exact variant"
            )
        registered_pivot = tuple(
            require_string_list(registration.get("pivot"), "pivot")
        )
        metadata_pivot = tuple(
            require_string_list(metadata["pivot"], "pivot")
        )
        registered_granularity = require_string(
            registration.get("time_granularity"),
            "time_granularity",
        )
        metadata_granularity = require_string(
            metadata["time_granularity"],
            "time_granularity",
        )
        registered_windows = require_string_list(
            registration.get("attribution_windows"),
            "attribution_windows",
        )
        metadata_windows = require_string_list(
            metadata["attribution_windows"],
            "attribution_windows",
        )
        reporting_timezone = require_string(
            metadata["reporting_timezone"],
            "reporting_timezone",
        )
        if (
            registered_pivot != _EXACT_PIVOT
            or metadata_pivot != _EXACT_PIVOT
            or registered_granularity not in _TIME_GRANULARITIES
            or metadata_granularity != registered_granularity
            or reporting_timezone != "UTC"
        ):
            raise AdapterError(
                "LinkedIn exact adapter variant requires full creative pivot, "
                "UTC reporting, and an admitted grain"
            )
        if metadata_windows != registered_windows:
            raise AdapterError(
                "LinkedIn attribution_windows do not match registration"
            )

        account_currency = require_string(
            metadata["account_currency"],
            "account_currency",
        )
        billed_currency = require_string(
            metadata["billed_currency"],
            "billed_currency",
        )
        currency_state = require_string(
            metadata["currency_state"],
            "currency_state",
        )
        _currency_state(account_currency, billed_currency, currency_state)
        privacy_state = require_string(
            metadata["privacy_state"],
            "privacy_state",
        )
        if privacy_state not in _PRIVACY_STATES:
            raise AdapterError("LinkedIn privacy_state is unsupported")
        demographic_truncation = require_string(
            metadata["demographic_truncation"],
            "demographic_truncation",
        )
        if demographic_truncation not in _DEMOGRAPHIC_TRUNCATION_STATES:
            raise AdapterError(
                "LinkedIn demographic_truncation is unsupported"
            )
        latency_state = require_string(
            metadata["latency_state"],
            "latency_state",
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
            metadata["observed_at"],
            "observed_at",
        )
        omitted_zero_behavior = require_string(
            metadata["omitted_zero_behavior"],
            "omitted_zero_behavior",
        )
        if omitted_zero_behavior != _OMITTED_ZERO_BEHAVIOR:
            raise AdapterError(
                "LinkedIn exact adapter variant requires its "
                "omitted_zero_behavior"
            )
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])

        normalized: list[dict[str, object]] = []
        seen_references: set[str] = set()
        for index, raw_row in enumerate(rows):
            path = f"LinkedIn rows[{index}]"
            row = require_closed_object(
                raw_row,
                _ROW_KEYS,
                path,
                required={
                    "source_row_reference",
                    "pivotValues",
                    "dateRange",
                    *_EXACT_REQUIRED_FIELDS,
                },
            )
            source_row_reference = require_string(
                row["source_row_reference"],
                f"{path}.source_row_reference",
            )
            if source_row_reference in seen_references:
                raise AdapterError(
                    "LinkedIn source_row_reference values must be unique"
                )
            seen_references.add(source_row_reference)
            identities = linkedin_identity(row["pivotValues"])
            if (
                len(identities) != len(_PIVOT_URN_PREFIXES)
                or any(
                    not identity.startswith(prefix)
                    for identity, prefix in zip(
                        identities,
                        _PIVOT_URN_PREFIXES,
                        strict=True,
                    )
                )
            ):
                raise AdapterError(
                    "LinkedIn exact adapter variant requires account, campaign, "
                    "and creative full URNs in pivot order"
                )
            date_range = require_closed_object(
                row["dateRange"],
                _DATE_RANGE_KEYS,
                f"{path}.dateRange",
            )
            start = require_date(
                date_range["start"],
                f"{path}.dateRange.start",
            )
            end = require_date(
                date_range["end"],
                f"{path}.dateRange.end",
            )
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise AdapterError(
                    "LinkedIn dateRange.start must not follow dateRange.end"
                )
            if (
                metadata_granularity == "DAILY"
                and date.fromisoformat(start) != date.fromisoformat(end)
            ):
                raise AdapterError(
                    "LinkedIn exact daily variant requires one UTC reporting date"
                )
            impressions_text, impressions = require_nonnegative_count(
                row["impressions"],
                f"{path}.impressions",
                strings_only=False,
            )
            clicks_text, clicks = require_nonnegative_count(
                row["clicks"],
                f"{path}.clicks",
                strings_only=False,
            )
            spend_text, spend = require_nonnegative_decimal(
                row["costInLocalCurrency"],
                f"{path}.costInLocalCurrency",
                strings_only=True,
            )

            checked_metrics: dict[
                str, tuple[str, Decimal | None, str | None]
            ] = {}
            for field in _EXACT_METRIC_FIELDS:
                if field in row:
                    checked_metrics[field] = _linkedin_metric_value(
                        row[field],
                        f"{path}.{field}",
                    )
            if conversion_source not in checked_metrics:
                linkedin_conversion(row, conversion_source)
                raise AssertionError("unreachable")
            outcome_state, outcome, outcome_text = checked_metrics[
                conversion_source
            ]
            explicit_state = (
                outcome_state if outcome_state != "observed" else None
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
                    account_id=identities[0],
                    campaign_id=identities[1],
                    ad_group_id="not_applicable",
                    creative_id=identities[2],
                    reporting={
                        "start_date": start,
                        "end_date": end,
                        "timezone": "UTC",
                        "basis": (
                            "utc_reporting_day"
                            if metadata_granularity == "DAILY"
                            else "utc_reporting_period"
                        ),
                        "request_level": "creative",
                        "time_increment": (
                            "1"
                            if metadata_granularity == "DAILY"
                            else "period"
                        ),
                        "segment_grain": list(_EXACT_IDENTITIES),
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": "platform_attribution",
                        "windows": metadata_windows,
                    },
                    platform_semantics=build_platform_semantics(
                        billed_currency=billed_currency,
                        currency_relationship=currency_state,
                        privacy_review_state=privacy_state,
                        demographic_truncation_state=demographic_truncation,
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
                    spend_source_metric="costInLocalCurrency",
                    spend_source_unit="local_currency_units",
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=outcome_text,
                    outcome_source_metric=conversion_source,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior=omitted_zero_behavior,
                    outcome_value_state=explicit_state,
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
                    "privacy_state": privacy_state,
                    "demographic_truncation": demographic_truncation,
                    "account_currency": account_currency,
                    "billed_currency": billed_currency,
                    "currency_state": currency_state,
                    "pivot": list(_EXACT_PIVOT),
                    "time_granularity": metadata_granularity,
                },
                "warnings": ["schema_tested_not_export_verified"],
            },
        )
