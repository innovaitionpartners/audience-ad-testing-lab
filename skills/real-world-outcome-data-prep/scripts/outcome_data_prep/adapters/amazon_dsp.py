"""Fail-closed boundary for registered but unavailable Amazon DSP variants."""

from __future__ import annotations

from collections.abc import Mapping

from ..capabilities import AdapterCapability
from .base import AdapterError, AdapterResult, ExactVariantAdapter
from .programmatic_common import require_programmatic_capability


_VARIANTS = {
    "amazon-unified-reporting-ui-csv-v1": (
        "csv",
        "unified_reporting_ui_ad_daily",
        (
            "Advertiser ID",
            "Order ID",
            "Line item ID",
            "Creative ID",
            "Date",
        ),
        ("Impressions delivered", "Clicks delivered", "Total cost"),
        ("Purchases",),
    ),
    "amazon-unified-reporting-ui-xlsx-v1": (
        "xlsx",
        "unified_reporting_ui_ad_daily",
        ("Advertiser", "Order", "Line item", "Creative", "Report date"),
        ("Impressions", "Clicks", "Cost"),
        ("Purchases",),
    ),
    "amazon-unified-reporting-api-json-v1": (
        "json",
        "unified_reporting_api_ad_daily",
        ("advertiserId", "orderId", "lineItemId", "creativeId", "date"),
        ("impressions", "clicks", "totalCost"),
        ("purchases",),
    ),
}
_CAPABILITY_SHA256 = {
    "amazon-unified-reporting-ui-csv-v1": (
        "sha256:e532e97e02ea0f1bdffa9d1a761a4a95e17f1292d9389287c131a61e10f17c75"
    ),
    "amazon-unified-reporting-ui-xlsx-v1": (
        "sha256:f80affde57018d9bce1ac3edf5f9b90e663b847deb59f4504a5b7e577fc1bd2e"
    ),
    "amazon-unified-reporting-api-json-v1": (
        "sha256:833dd5816b2e4a915d28edb0a8072b9abf9aa3813e4c0d8c28da40056037c7a9"
    ),
}


def amazon_variant_available(capability: AdapterCapability) -> None:
    if capability.maturity != "export_verified":
        raise AdapterError(
            "Amazon DSP physical schema is blocked pending a sanitized export"
        )


def _require_blocked_capability(capability: AdapterCapability) -> None:
    require_programmatic_capability(
        capability, _CAPABILITY_SHA256, "AmazonDSPAdapter"
    )
    variant = _VARIANTS.get(capability.adapter_id)
    if not (
        variant is not None
        and capability.platform == "amazon_dsp"
        and capability.report_type == variant[1]
        and capability.container == variant[0]
        and capability.locale == "invariant"
        and capability.row_grain == variant[2]
        and capability.identity_fields == variant[2]
        and capability.required_fields == variant[3]
        and capability.metric_fields == variant[4]
        and capability.time_basis == "advertiser_reporting_day"
        and capability.currency_basis == "advertiser_currency"
        and capability.maturity == "blocked"
        and capability.availability_reason
        == "blocked_pending_sanitized_sample"
        and not capability.contract_ready_permitted
        and capability.reviewer is None
        and capability.verified_at is None
    ):
        raise AdapterError(
            "AmazonDSPAdapter requires a registered blocked exact variant"
        )


class AmazonDSPAdapter(ExactVariantAdapter):
    """Reject normalization until genuine export verification exists."""

    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("Amazon DSP adapter capability is invalid")
        _require_blocked_capability(capability)
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
        _require_blocked_capability(capability)
        amazon_variant_available(capability)
        raise AssertionError("unreachable")
