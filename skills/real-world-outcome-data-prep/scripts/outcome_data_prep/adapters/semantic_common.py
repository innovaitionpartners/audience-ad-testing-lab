"""Platform-neutral semantic helpers for exact advertising adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
import re

from ..capabilities import AdapterCapability
from ..common import (
    ContractError,
    require_numeric_string,
    require_numeric_string_or_number,
    sha256_json,
)
from ..contracts import (
    NORMALIZED_OBSERVATION_VERSION,
    validate_normalized_observation,
)
from .base import AdapterError


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONVERSION_QUALITY_STATES = frozenset({"observed", "modeled", "estimated"})
LATENCY_STATES = frozenset({"mature", "immature"})
MISSING_OUTCOME_STATES = frozenset(
    {"null", "absent", "suppressed", "omitted_zero"}
)


def require_object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AdapterError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise AdapterError(f"{path} keys must be strings")
    return dict(value)


def require_closed_object(
    value: object,
    keys: set[str],
    path: str,
    *,
    required: set[str] | None = None,
) -> dict[str, object]:
    document = require_object(value, path)
    required_keys = keys if required is None else required
    unknown = sorted(set(document) - keys)
    missing = sorted(required_keys - set(document))
    if unknown:
        raise AdapterError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise AdapterError(f"{path} is missing fields: {', '.join(missing)}")
    return document


def require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{path} must be a non-empty string")
    return value


def require_string_list(
    value: object,
    path: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else "non-empty "
        raise AdapterError(f"{path} must be a {qualifier}list")
    result = [require_string(item, f"{path}[]") for item in value]
    if len(set(result)) != len(result):
        raise AdapterError(f"{path} must not contain duplicates")
    return result


def require_date(value: object, path: str) -> str:
    result = require_string(value, path)
    try:
        date.fromisoformat(result)
    except ValueError as exc:
        raise AdapterError(f"{path} must be an ISO 8601 date") from exc
    return result


def require_timestamp(value: object, path: str) -> str:
    result = require_string(value, path)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError(
            f"{path} must be a timezone-aware ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(
            f"{path} must be a timezone-aware ISO 8601 timestamp"
        )
    return result


def require_source_sha256(value: object, path: str = "source_sha256") -> str:
    result = require_string(value, path)
    if not _DIGEST.fullmatch(result):
        raise AdapterError(f"{path} must be a prefixed SHA-256")
    return result


def require_numeric_text(
    value: object,
    path: str,
    *,
    strings_only: bool,
) -> str:
    try:
        if strings_only:
            return require_numeric_string(value, path)
        return require_numeric_string_or_number(value, path)
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc


def require_nonnegative_decimal(
    value: object,
    path: str,
    *,
    strings_only: bool,
) -> tuple[str, Decimal]:
    source_text = require_numeric_text(
        value, path, strings_only=strings_only
    )
    parsed = Decimal(source_text)
    if parsed < 0:
        raise AdapterError(f"{path} must be non-negative")
    return source_text, parsed


def require_nonnegative_count(
    value: object,
    path: str,
    *,
    strings_only: bool,
) -> tuple[str, Decimal]:
    source_text, parsed = require_nonnegative_decimal(
        value, path, strings_only=strings_only
    )
    if parsed != parsed.to_integral_value():
        raise AdapterError(f"{path} must be a non-negative integral count")
    return source_text, parsed


def decimal_json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def canonical_outcome_value_state(
    value: Decimal,
    conversion_quality: str,
) -> str:
    if conversion_quality not in CONVERSION_QUALITY_STATES:
        raise AdapterError("conversion_value_state is unsupported")
    if conversion_quality != "observed":
        return conversion_quality
    if value == 0:
        return "observed_zero"
    if value != value.to_integral_value():
        return "fractional"
    return "observed"


def build_platform_semantics(
    *,
    billed_currency: str | None,
    currency_relationship: str,
    privacy_review_state: str,
    demographic_truncation_state: str,
    click_semantic: str,
    optimization_event: str | None,
    delivery_state: str,
    skan_state: str,
    search_term_id: str | None,
    search_term_state: str,
) -> dict[str, object]:
    """Build the one closed cross-platform semantics block."""

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


def build_rich_observation(
    *,
    capability: AdapterCapability,
    study_id: str,
    registration_id: str,
    import_id: str,
    source_id: str,
    source_sha256: str,
    source_row_reference: str,
    metric_id: str,
    account_id: str,
    campaign_id: str,
    ad_group_id: str,
    creative_id: str,
    reporting: Mapping[str, object],
    attribution: Mapping[str, object],
    platform_semantics: Mapping[str, object],
    currency_code: str,
    spend: Decimal,
    spend_decimal_text: str,
    spend_source_text: str,
    spend_source_metric: str,
    spend_source_unit: str,
    impressions: Decimal,
    impressions_source_text: str,
    clicks: Decimal,
    clicks_source_text: str,
    outcome: Decimal | None,
    outcome_source_text: str | None,
    outcome_source_metric: str,
    conversion_quality: str,
    omitted_zero_behavior: str,
    ad_id: str | None = None,
    outcome_value_state: str | None = None,
) -> dict[str, object]:
    """Build one rich row, preserving distinct ad and creative identities.

    A platform with one physical identity lane passes no ``ad_id`` and the
    closed rule is ``ad == creative``. A platform with two lanes must pass the
    exact source ad identity; this helper never invents one.
    """

    if outcome is None:
        if outcome_value_state not in MISSING_OUTCOME_STATES:
            raise AdapterError(
                "missing outcome requires an explicit missing value state"
            )
        if outcome_source_text is not None:
            raise AdapterError("missing outcome source text must be null")
        outcome_value: int | float | None = None
        outcome_decimal: str | None = None
        effective_outcome_state = outcome_value_state
    else:
        if outcome_source_text is None:
            raise AdapterError("observed outcome source text is required")
        if outcome_value_state is not None:
            raise AdapterError(
                "numeric outcome value state must derive from conversion quality"
            )
        outcome_value = decimal_json_number(outcome)
        outcome_decimal = outcome_source_text
        effective_outcome_state = canonical_outcome_value_state(
            outcome,
            conversion_quality,
        )

    observation_id = "observation-" + sha256_json(
        {
            "adapter_id": capability.adapter_id,
            "source_sha256": source_sha256,
            "source_row_reference": source_row_reference,
            "metric_id": metric_id,
        }
    ).removeprefix("sha256:")
    return validate_normalized_observation(
        {
            "schema_version": NORMALIZED_OBSERVATION_VERSION,
            "observation_id": observation_id,
            "study_id": study_id,
            "registration_id": registration_id,
            "import_id": import_id,
            "source_id": source_id,
            "source_sha256": source_sha256,
            "source_row_reference": source_row_reference,
            "platform": capability.platform,
            "adapter": {
                "adapter_id": capability.adapter_id,
                "adapter_version": capability.adapter_version,
                "maturity": capability.maturity,
            },
            "account": {"platform_id": account_id},
            "campaign": {"platform_id": campaign_id},
            "ad_group": {"platform_id": ad_group_id},
            "ad": {"platform_id": ad_id or creative_id},
            "creative": {"platform_id": creative_id},
            "reporting": dict(reporting),
            "attribution": dict(attribution),
            "currency": {
                "code": currency_code,
                "basis": capability.currency_basis,
            },
            "spend": {
                "value": decimal_json_number(spend),
                "decimal": spend_decimal_text,
                "source_numeric_text": spend_source_text,
                "source_metric": spend_source_metric,
                "source_unit": spend_source_unit,
            },
            "exposure": {
                "impressions": {
                    "value": decimal_json_number(impressions),
                    "source_numeric_text": impressions_source_text,
                },
                "clicks": {
                    "value": decimal_json_number(clicks),
                    "source_numeric_text": clicks_source_text,
                },
            },
            "outcome": {
                "metric_id": metric_id,
                "source_metric": outcome_source_metric,
                "value": outcome_value,
                "decimal": outcome_decimal,
                "source_numeric_text": outcome_source_text,
                "value_state": effective_outcome_state,
                "omitted_zero_behavior": omitted_zero_behavior,
            },
            "platform_semantics": dict(platform_semantics),
            "validation_projection": {
                "status": "unavailable",
                "evidence_status": "blocked",
                "metric_family": None,
                "measurement_window": None,
                "attribution_window": None,
                "aggregate": None,
                "eligible_exposure_count": None,
                "missing_outcome_count": None,
                "effective_sample_size": None,
                "assignment": None,
                "confidence_level": None,
                "permission_confirmed": None,
                "outcome_accessed_at": None,
                "limitations": [],
            },
            "normalized_observation_sha256": None,
        }
    )
