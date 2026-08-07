"""Bounded explicit mapper for aggregate generic programmatic exports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
import re

from ..capabilities import AdapterCapability, Detection
from ..container_safety import ContainerInventory
from ..common import ContractError, sha256_json
from ..contracts import validate_delivery_map
from ..privacy import (
    AdmittedSource,
    AdapterAdmissionValidation,
    PrivacyAdmissionError,
    authenticate_admitted_source,
    container_inventory_sha256,
    normalize_header,
    pre_scan_obvious_privacy,
)
from .base import (
    AdapterError,
    AdapterInventory,
    AdapterResult,
    AdapterValidation,
    ExactVariantAdapter,
    _observed_minimum,
    prohibited_business_data_fields,
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
    require_object,
    require_source_sha256,
    require_string,
    require_string_list,
    require_timestamp,
)


GENERIC_ALLOWED_TARGETS = frozenset({
    "campaign_id",
    "line_item_id",
    "ad_group_id",
    "creative_id",
    "ad_id",
    "date",
    "impressions",
    "clicks",
    "spend",
    "currency",
    "conversion_value",
    "sample_count",
    "standard_deviation",
    "exposure_time",
})
_IDENTITY_TARGETS = {
    "campaign_id",
    "line_item_id",
    "ad_group_id",
    "creative_id",
    "ad_id",
}
_PROHIBITED_HEADER_TOKENS = {
    "email",
    "phone",
    "ip_address",
    "device_id",
    "cookie_id",
    "user_id",
    "person_id",
    "event_id",
    "request_id",
    "household_id",
}
_SENSITIVE_HEADER_PARTS = {
    "cookie",
    "device",
    "email",
    "event",
    "household",
    "ip",
    "person",
    "phone",
    "request",
    "user",
}
_IDENTIFIER_HEADER_PARTS = {"id", "identifier", "key", "uuid"}
_COMMON_NULL_TOKENS = {"", "-", "--", "NA", "N/A", "NULL", "None", "null"}
_ROOT_KEYS = {
    "source_id",
    "import_id",
    "source_sha256",
    "mapping",
    "reporting_metadata",
    "rows",
}
_REPORTING_KEYS = {
    "source_container",
    "source_platform",
    "headers",
    "header_fingerprint",
    "mapping_profile_id",
    "stable_id_targets",
    "timezone",
    "time_basis",
    "currency",
    "attribution_semantics",
    "attribution_windows",
    "conversion_metric",
    "admitted_null_tokens",
    "null_value_state",
    "aggregate_level",
    "currency_inferred",
    "currency_conversion",
    "cross_platform_reach_deduplication",
    "reconstructed_attribution",
    "platform_proof_basis",
    "mixed_time_bases",
    "automatic_adapter_promotion",
    "conversion_value_state",
    "latency_state",
    "observed_at",
    "omitted_zero_behavior",
}
_OMITTED_ZERO = "omitted_metrics_are_unknown_not_zero"
_REGISTERED_IDENTITIES = (
    "platform",
    "campaign_id",
    "line_item_id",
    "creative_id",
    "date",
)
_REGISTERED_REQUIRED = ("exposures", "clicks", "spend", "currency")
_REGISTERED_METRICS = ("outcome_value", "outcome_metric", "value_state")
_CAPABILITY_SHA256 = {
    "generic-dsp-mapping-v1": (
        "sha256:29be4ed0bd2b46c9b0960d2a9ffee9d6ca753f9f65938970e1ca838e2af96cb7"
    )
}
_SOURCE_MEDIA_TYPES = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class GenericAdmissionProfile:
    adapter_id: str
    adapter_version: str
    source_container: str
    source_platform: str
    headers: tuple[str, ...]
    header_fingerprint: str
    mapping_profile_id: str
    source_to_canonical: tuple[tuple[str, str], ...]
    stable_id_targets: tuple[str, ...]
    denominator_field: str
    delivery_map_sha256: str
    profile_sha256: str


def _profile_document(
    profile: GenericAdmissionProfile,
    *,
    include_hash: bool,
) -> dict[str, object]:
    document: dict[str, object] = {
        "adapter_id": profile.adapter_id,
        "adapter_version": profile.adapter_version,
        "source_container": profile.source_container,
        "source_platform": profile.source_platform,
        "headers": list(profile.headers),
        "header_fingerprint": profile.header_fingerprint,
        "mapping_profile_id": profile.mapping_profile_id,
        "source_to_canonical": [
            list(item) for item in profile.source_to_canonical
        ],
        "stable_id_targets": list(profile.stable_id_targets),
        "denominator_field": profile.denominator_field,
        "delivery_map_sha256": profile.delivery_map_sha256,
    }
    if include_hash:
        document["profile_sha256"] = profile.profile_sha256
    return document


def _require_profile(
    profile: GenericAdmissionProfile,
    capability: AdapterCapability,
) -> None:
    if type(profile) is not GenericAdmissionProfile:
        raise AdapterError("approved generic profile is invalid")
    if (
        profile.adapter_id != capability.adapter_id
        or profile.adapter_version != capability.adapter_version
        or profile.source_container not in _SOURCE_MEDIA_TYPES
        or profile.source_platform != capability.platform
        or not profile.headers
        or len(set(profile.headers)) != len(profile.headers)
        or profile.header_fingerprint
        != sha256_json(sorted(profile.headers))
        or profile.denominator_field
        != _target_source(
            dict(profile.source_to_canonical), "impressions"
        )
        or not _DIGEST.fullmatch(profile.delivery_map_sha256)
        or profile.profile_sha256
        != sha256_json(_profile_document(profile, include_hash=False))
    ):
        raise AdapterError("approved generic profile is invalid")
    require_no_prohibited_business_data(
        profile.headers,
        context="approved generic profile",
    )
    require_no_prohibited_business_data(
        tuple(target for _, target in profile.source_to_canonical),
        context="approved generic profile targets",
    )


def validate_generic_mapping(
    mapping: Mapping[str, str],
    *,
    sealed_delivery_map: Mapping[str, object],
    outcomes_accessed: bool,
) -> dict[str, str]:
    if not isinstance(mapping, Mapping) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        for key, value in mapping.items()
    ):
        raise AdapterError("generic mapping must be a string-to-string object")
    if not isinstance(sealed_delivery_map, Mapping):
        raise AdapterError("sealed delivery map is invalid")
    sealed = sealed_delivery_map.get("sealed_before_outcome_access")
    if type(sealed) is not bool:
        raise AdapterError("sealed delivery map is invalid")
    require_no_prohibited_business_data(
        tuple(mapping),
        context="generic mapping",
    )
    require_no_prohibited_business_data(
        tuple(mapping.values()),
        context="generic mapping targets",
    )
    unknown = sorted(set(mapping.values()) - GENERIC_ALLOWED_TARGETS)
    if unknown:
        raise AdapterError(
            f"generic mapping has unsupported targets: {unknown}"
        )
    if len(set(mapping.values())) != len(mapping):
        raise AdapterError("generic mapping targets must be one-to-one")
    if outcomes_accessed and not sealed:
        raise AdapterError(
            "generic identity mapping requires a sealed delivery map"
        )
    if "creative_id" not in mapping.values() and "ad_id" not in mapping.values():
        raise AdapterError(
            "generic mapping requires a stable creative or ad ID"
        )
    return dict(sorted(mapping.items()))


def _require_capability(capability: AdapterCapability) -> None:
    require_programmatic_capability(
        capability, _CAPABILITY_SHA256, "GenericProgrammaticAdapter"
    )
    if not (
        capability.adapter_id == "generic-dsp-mapping-v1"
        and capability.platform == "generic_dsp"
        and capability.report_type == "explicit_preregistered_mapping"
        and capability.container == "csv"
        and capability.locale == "invariant"
        and capability.row_grain == _REGISTERED_IDENTITIES
        and capability.identity_fields == _REGISTERED_IDENTITIES
        and capability.required_fields == _REGISTERED_REQUIRED
        and capability.metric_fields == _REGISTERED_METRICS
        and capability.time_basis == "explicit_registered_basis"
        and capability.currency_basis == "explicit_row_currency"
        and capability.maturity == "schema_tested"
        and not capability.contract_ready_permitted
    ):
        raise AdapterError(
            "GenericProgrammaticAdapter requires its exact mapping capability"
        )


def _header_token(header: str) -> str:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", header.strip())
    return "_".join(separated.lower().replace("-", " ").split())


def _prohibited_identifier_header(header: str) -> bool:
    token = _header_token(header)
    parts = set(token.split("_"))
    has_sensitive_part = bool(parts & _SENSITIVE_HEADER_PARTS) or any(
        sensitive in token for sensitive in _SENSITIVE_HEADER_PARTS
    )
    has_identifier_part = bool(parts & _IDENTIFIER_HEADER_PARTS)
    return (
        token in _PROHIBITED_HEADER_TOKENS
        or any(value in token for value in ("email", "phone"))
        or (has_sensitive_part and has_identifier_part)
    )


def _target_source(mapping: Mapping[str, str], target: str) -> str:
    matches = [source for source, mapped in mapping.items() if mapped == target]
    if len(matches) != 1:
        raise AdapterError(f"generic mapping requires exactly one {target}")
    return matches[0]


class GenericProgrammaticAdapter(ExactVariantAdapter):
    def __init__(self, capability: AdapterCapability):
        if type(capability) is not AdapterCapability:
            raise AdapterError("generic adapter capability is invalid")
        _require_capability(capability)
        super().__init__(capability)

    def approved_profile(
        self,
        inventory: object,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
    ) -> GenericAdmissionProfile:
        if capability != self.capability:
            raise AdapterError("adapter capability does not match adapter")
        _require_capability(capability)
        payload = require_closed_object(
            inventory, _ROOT_KEYS, "generic programmatic export"
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "generic reporting_metadata",
        )
        outcomes_accessed = registration.get("outcomes_accessed")
        if type(outcomes_accessed) is not bool:
            raise AdapterError("outcomes_accessed must be boolean")
        try:
            sealed_delivery_map = validate_delivery_map(
                registration.get("sealed_delivery_map")
            )
        except ContractError as exc:
            raise AdapterError(
                f"sealed delivery map is invalid: {exc}"
            ) from exc
        if sealed_delivery_map["sealed_before_outcome_access"] is not True:
            raise AdapterError(
                "generic identity mapping requires a sealed delivery map"
            )
        mapping = validate_generic_mapping(
            require_object(payload["mapping"], "generic mapping"),
            sealed_delivery_map=sealed_delivery_map,
            outcomes_accessed=outcomes_accessed,
        )
        source_container = require_string(
            metadata["source_container"], "source_container"
        )
        if source_container not in _SOURCE_MEDIA_TYPES:
            raise AdapterError(
                "generic mapper supports only CSV, TSV, or simple XLSX"
            )
        source_platform = require_string(
            metadata["source_platform"], "source_platform"
        )
        if source_platform != capability.platform:
            raise AdapterError(
                "generic source platform must be exactly generic_dsp"
            )
        if not any(
            item["platform"] == source_platform
            for item in sealed_delivery_map["mappings"]
        ):
            raise AdapterError(
                "generic source platform does not match sealed delivery map"
            )
        headers = require_string_list(metadata["headers"], "headers")
        if len(set(headers)) != len(headers):
            raise AdapterError("generic headers must be unique")
        if set(mapping) != set(headers):
            raise AdapterError(
                "generic mapping must be closed over the exact source headers"
            )
        prohibited_identifiers = sorted(
            header
            for header in headers
            if _prohibited_identifier_header(header)
        )
        if prohibited_identifiers:
            raise AdapterError(
                "generic mapping contains person, user, device, or event "
                f"identifiers: {prohibited_identifiers}"
            )
        expected_fingerprint = sha256_json(sorted(headers))
        if metadata["header_fingerprint"] != expected_fingerprint:
            raise AdapterError(
                "generic mapping profile does not match exact "
                "header fingerprint"
            )
        mapping_profile_id = require_string(
            metadata["mapping_profile_id"], "mapping_profile_id"
        )
        approved_mapping = validate_generic_mapping(
            require_object(
                registration.get("approved_mapping"), "approved_mapping"
            ),
            sealed_delivery_map=sealed_delivery_map,
            outcomes_accessed=outcomes_accessed,
        )
        if approved_mapping != mapping:
            raise AdapterError(
                "generic source-to-canonical mapping is not the "
                "approved mapping"
            )
        if registration.get("approved_mapping_profile_id") != mapping_profile_id:
            raise AdapterError(
                "generic mapping_profile_id is not the approved profile"
            )
        if (
            registration.get("approved_header_fingerprint")
            != expected_fingerprint
        ):
            raise AdapterError(
                "generic header fingerprint is not the approved fingerprint"
            )
        if registration.get("approved_source_container") != source_container:
            raise AdapterError(
                "generic source_container is not the approved container"
            )
        stable_targets = set(
            require_string_list(
                metadata["stable_id_targets"], "stable_id_targets"
            )
        )
        mapped_identity_targets = set(mapping.values()) & _IDENTITY_TARGETS
        if stable_targets != mapped_identity_targets:
            raise AdapterError(
                "generic mapping requires declared stable IDs for every "
                "identity target"
            )
        for source, target in mapping.items():
            if target not in _IDENTITY_TARGETS:
                continue
            token = _header_token(source)
            if token.endswith("_name") or not any(
                marker in token.split("_")
                for marker in ("id", "key", "uuid", "code", "identifier")
            ):
                raise AdapterError(
                    "generic identity mapping rejects name-only identity "
                    f"source: {source}"
                )
        required_targets = {
            "campaign_id",
            "date",
            "impressions",
            "clicks",
            "spend",
            "currency",
            "conversion_value",
        }
        missing_targets = sorted(required_targets - set(mapping.values()))
        if missing_targets:
            raise AdapterError(
                "generic mapping is missing required aggregate targets: "
                f"{missing_targets}"
            )
        if not ({"line_item_id", "ad_group_id"} & set(mapping.values())):
            raise AdapterError(
                "generic mapping requires a stable line-item or ad-group ID"
            )
        if metadata["aggregate_level"] != "already_aggregate":
            raise AdapterError("generic mapper rejects log-level data")
        for field, message in (
            ("currency_inferred", "generic mapper cannot infer currency"),
            (
                "currency_conversion",
                "generic mapper cannot perform currency conversion",
            ),
            (
                "cross_platform_reach_deduplication",
                "generic mapper cannot deduplicate cross-platform reach",
            ),
            (
                "reconstructed_attribution",
                "generic mapper cannot reconstruct attribution",
            ),
            ("mixed_time_bases", "generic mapper rejects mixed time bases"),
            (
                "automatic_adapter_promotion",
                "generic mapping cannot become a reusable adapter automatically",
            ),
        ):
            if metadata[field] is not False:
                raise AdapterError(message)
        if metadata["platform_proof_basis"] != "declared_not_filename":
            raise AdapterError(
                "filenames cannot establish generic platform proof"
            )
        denominator_field = _target_source(mapping, "impressions")
        draft = GenericAdmissionProfile(
            adapter_id=capability.adapter_id,
            adapter_version=capability.adapter_version,
            source_container=source_container,
            source_platform=source_platform,
            headers=tuple(headers),
            header_fingerprint=expected_fingerprint,
            mapping_profile_id=mapping_profile_id,
            source_to_canonical=tuple(sorted(mapping.items())),
            stable_id_targets=tuple(sorted(stable_targets)),
            denominator_field=denominator_field,
            delivery_map_sha256=str(
                sealed_delivery_map["delivery_map_sha256"]
            ),
            profile_sha256="sha256:" + "0" * 64,
        )
        profile = GenericAdmissionProfile(
            **{
                **_profile_document(draft, include_hash=False),
                "headers": draft.headers,
                "source_to_canonical": draft.source_to_canonical,
                "stable_id_targets": draft.stable_id_targets,
                "profile_sha256": sha256_json(
                    _profile_document(draft, include_hash=False)
                ),
            }
        )
        _require_profile(profile, capability)
        return profile

    def detect(
        self,
        inventory: ContainerInventory,
        *,
        profile: GenericAdmissionProfile | None = None,
    ) -> Detection:
        if not isinstance(inventory, ContainerInventory):
            raise AdapterError("container inventory is invalid")
        if profile is None:
            return Detection(
                adapter_id=None,
                status="explicit_mapping_required",
                confidence="none",
                reasons=("automatic_generic_detection_disabled",),
            )
        try:
            _require_profile(profile, self.capability)
        except AdapterError:
            return Detection(
                adapter_id=None,
                status="unsupported_explicit_mapping",
                confidence="none",
                reasons=("approved_profile_invalid",),
            )
        if not self._source_matches_profile(inventory, profile):
            return Detection(
                adapter_id=None,
                status="unsupported_explicit_mapping",
                confidence="none",
                reasons=("approved_profile_source_mismatch",),
            )
        return Detection(
            adapter_id=self.adapter_id,
            status="explicit_mapping_approved",
            confidence="explicit",
            reasons=("automatic_generic_detection_disabled",),
        )

    @staticmethod
    def _source_matches_profile(
        inventory: ContainerInventory,
        profile: GenericAdmissionProfile,
    ) -> bool:
        if (
            inventory.media_type
            != _SOURCE_MEDIA_TYPES.get(profile.source_container)
            or not inventory.tables
            or len(inventory.tables) != len(inventory.headers)
            or len(set(inventory.tables)) != len(inventory.tables)
        ):
            return False
        expected = set(profile.headers)
        return all(
            len(headers) == len(expected)
            and len(set(headers)) == len(headers)
            and set(headers) == expected
            for headers in inventory.headers
        )

    def inventory(
        self,
        inventory: ContainerInventory,
        capability: AdapterCapability,
        *,
        profile: GenericAdmissionProfile | None = None,
    ) -> AdapterInventory:
        if capability != self.capability:
            raise AdapterError("adapter capability does not match adapter")
        _require_capability(capability)
        if profile is None:
            raise AdapterError("approved generic profile is required")
        _require_profile(profile, capability)
        if not self._source_matches_profile(inventory, profile):
            raise AdapterError(
                "source does not match the approved generic profile"
            )
        exact_headers = tuple(profile.headers)
        observed, group_error = _observed_minimum(
            inventory,
            profile.denominator_field,
            exact_headers,
        )
        normalized_headers = {
            normalize_header(header) for header in profile.headers
        }
        prohibited = tuple(
            sorted(
                field
                for field in capability.prohibited_fields
                if normalize_header(field) in normalized_headers
            )
        )
        prohibited_business = prohibited_business_data_fields(
            profile.headers,
        )
        return AdapterInventory(
            capability=capability,
            tables=tuple(inventory.tables),
            headers=tuple(tuple(item) for item in inventory.headers),
            row_count=inventory.row_count,
            reporting_metadata={
                "media_type": inventory.media_type,
                "inventory_sha256": container_inventory_sha256(inventory),
                "exact_schema_match": True,
                "detected_adapter_id": capability.adapter_id,
                "prohibited_fields_present": prohibited,
                "prohibited_business_fields_present": prohibited_business,
                "observed_minimum_group_size": observed,
                "group_size_error": group_error,
                "generic_profile": profile,
            },
        )

    def _profile_matches_registration(
        self,
        profile: GenericAdmissionProfile,
        registration: Mapping[str, object],
    ) -> None:
        _require_profile(profile, self.capability)
        try:
            delivery_map = validate_delivery_map(
                registration.get("sealed_delivery_map")
            )
        except ContractError as exc:
            raise AdapterError(
                f"sealed delivery map is invalid: {exc}"
            ) from exc
        approved_mapping = require_object(
            registration.get("approved_mapping"), "approved_mapping"
        )
        if (
            tuple(sorted(approved_mapping.items()))
            != profile.source_to_canonical
            or registration.get("approved_mapping_profile_id")
            != profile.mapping_profile_id
            or registration.get("approved_header_fingerprint")
            != profile.header_fingerprint
            or registration.get("approved_source_container")
            != profile.source_container
            or delivery_map["delivery_map_sha256"]
            != profile.delivery_map_sha256
            or delivery_map["sealed_before_outcome_access"] is not True
            or not any(
                item["platform"] == profile.source_platform
                for item in delivery_map["mappings"]
            )
        ):
            raise AdapterError(
                "registration does not match approved generic profile"
            )

    def validate(
        self,
        inventory: AdapterInventory,
        *,
        registration: Mapping[str, object],
        governance: Mapping[str, object],
        capability: AdapterCapability,
    ) -> AdapterValidation:
        profile = (
            inventory.reporting_metadata.get("generic_profile")
            if type(inventory) is AdapterInventory
            else None
        )
        if type(profile) is not GenericAdmissionProfile:
            raise AdapterError("approved generic profile is invalid")
        self._profile_matches_registration(profile, registration)
        return ExactVariantAdapter.validate(
            self,
            inventory,
            registration=registration,
            governance=governance,
            capability=capability,
        )

    def admission_validation(
        self,
        inventory: ContainerInventory,
        *,
        source_sha256: str,
        validation: AdapterValidation,
        registration: Mapping[str, object],
        governance: Mapping[str, object],
        profile: GenericAdmissionProfile,
    ) -> AdapterAdmissionValidation:
        source_sha256 = require_source_sha256(source_sha256)
        if type(validation) is not AdapterValidation:
            raise AdapterError("adapter validation is invalid")
        derived_inventory = self.inventory(
            inventory, self.capability, profile=profile
        )
        derived_validation = self.validate(
            derived_inventory,
            registration=registration,
            governance=governance,
            capability=self.capability,
        )
        if (
            validation != derived_validation
            or not validation.accepted
            or validation.errors
            or validation.observed_minimum_group_size is None
        ):
            raise AdapterError(
                "adapter validation does not match accepted generic result"
            )
        inventory_sha256 = container_inventory_sha256(inventory)
        if validation.inventory_sha256 != inventory_sha256:
            raise AdapterError("adapter validation inventory binding mismatch")
        return AdapterAdmissionValidation(
            adapter_id=self.capability.adapter_id,
            adapter_version=self.capability.adapter_version,
            source_sha256=source_sha256,
            inventory_sha256=inventory_sha256,
            profile_sha256=profile.profile_sha256,
            adapter_validation_sha256=sha256_json(asdict(validation)),
            governance_sha256=sha256_json(dict(governance)),
            accepted=True,
            observed_minimum_group_size=(
                validation.observed_minimum_group_size
            ),
            errors=(),
        )

    def normalize(
        self,
        inventory: object,
        *,
        registration: Mapping[str, object],
        capability: AdapterCapability,
        source_inventory: ContainerInventory | None = None,
        admission_validation: AdapterAdmissionValidation | None = None,
        admitted_source: AdmittedSource | None = None,
        governance: Mapping[str, object] | None = None,
        profile: GenericAdmissionProfile | None = None,
    ) -> AdapterResult:
        if capability != self.capability:
            raise AdapterError("adapter capability does not match adapter")
        _require_capability(capability)
        if (
            not isinstance(source_inventory, ContainerInventory)
            or type(admission_validation) is not AdapterAdmissionValidation
            or type(admitted_source) is not AdmittedSource
            or not isinstance(governance, Mapping)
            or type(profile) is not GenericAdmissionProfile
        ):
            raise AdapterError(
                "generic normalization requires a durable admitted source"
            )
        derived_profile = self.approved_profile(
            inventory,
            registration=registration,
            capability=capability,
        )
        if profile != derived_profile:
            raise AdapterError(
                "approved generic profile does not match normalization inputs"
            )
        _require_profile(profile, capability)
        pre_scan = pre_scan_obvious_privacy(
            source_inventory, source_name=admitted_source.source_name
        )
        if (
            pre_scan.status != "pre_scan_clear"
            or pre_scan.blocked_categories
        ):
            raise AdapterError(
                "generic source did not pass the privacy pre-scan"
            )
        derived_inventory = self.inventory(
            source_inventory,
            capability,
            profile=profile,
        )
        derived_validation = self.validate(
            derived_inventory,
            registration=registration,
            governance=governance,
            capability=capability,
        )
        if (
            not derived_validation.accepted
            or derived_validation.errors
            or derived_validation.observed_minimum_group_size is None
        ):
            raise AdapterError(
                "generic source does not have accepted adapter validation"
            )
        derived_admission = self.admission_validation(
            source_inventory,
            source_sha256=admitted_source.source_sha256,
            validation=derived_validation,
            registration=registration,
            governance=governance,
            profile=profile,
        )
        if admission_validation != derived_admission:
            raise AdapterError(
                "generic admission validation does not match exact profile "
                "and governance"
            )
        try:
            authenticate_admitted_source(
                admitted_source,
                source_inventory,
                pre_scan,
                derived_admission,
            )
        except PrivacyAdmissionError as exc:
            raise AdapterError(
                "generic durable admission chain is invalid"
            ) from exc
        if (
            not admission_validation.accepted
            or admission_validation.errors
            or admission_validation.adapter_id != capability.adapter_id
            or admission_validation.adapter_version
            != capability.adapter_version
            or admission_validation.inventory_sha256
            != container_inventory_sha256(source_inventory)
        ):
            raise AdapterError(
                "generic admission validation does not match source"
            )
        payload = require_closed_object(
            inventory, _ROOT_KEYS, "generic programmatic export"
        )
        if not isinstance(registration, Mapping):
            raise AdapterError("registration must be an object")
        metadata = require_closed_object(
            payload["reporting_metadata"],
            _REPORTING_KEYS,
            "generic reporting_metadata",
        )
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise AdapterError(
                "generic programmatic rows must be a non-empty list"
            )
        if (
            admission_validation.source_sha256 != payload["source_sha256"]
            or not self._payload_matches_source_inventory(
                rows, source_inventory, profile.headers
            )
        ):
            raise AdapterError(
                "generic normalization payload does not match admitted source"
            )
        outcomes_accessed = registration.get("outcomes_accessed")
        if type(outcomes_accessed) is not bool:
            raise AdapterError("outcomes_accessed must be boolean")
        try:
            sealed_delivery_map = validate_delivery_map(
                registration.get("sealed_delivery_map")
            )
        except ContractError as exc:
            raise AdapterError(f"sealed delivery map is invalid: {exc}") from exc
        raw_mapping = require_object(payload["mapping"], "generic mapping")
        mapping = validate_generic_mapping(
            raw_mapping,
            sealed_delivery_map=sealed_delivery_map,
            outcomes_accessed=outcomes_accessed,
        )
        if sealed_delivery_map.get("sealed_before_outcome_access") is not True:
            raise AdapterError(
                "generic identity mapping requires a sealed delivery map"
            )

        source_container = require_string(
            metadata["source_container"], "source_container"
        )
        if source_container not in {"csv", "tsv", "xlsx"}:
            raise AdapterError(
                "generic mapper supports only CSV, TSV, or simple XLSX"
            )
        source_platform = require_string(
            metadata["source_platform"], "source_platform"
        )
        if source_platform != capability.platform:
            raise AdapterError(
                "generic source platform must be exactly generic_dsp"
            )
        headers = require_string_list(metadata["headers"], "headers")
        if len(set(headers)) != len(headers):
            raise AdapterError("generic headers must be unique")
        if set(mapping) != set(headers):
            raise AdapterError(
                "generic mapping must be closed over the exact source headers"
            )
        prohibited_identifiers = sorted(
            header
            for header in headers
            if _prohibited_identifier_header(header)
        )
        if prohibited_identifiers:
            raise AdapterError(
                "generic mapping contains person, user, device, or event "
                f"identifiers: {prohibited_identifiers}"
            )
        expected_fingerprint = sha256_json(sorted(headers))
        if metadata["header_fingerprint"] != expected_fingerprint:
            raise AdapterError(
                "generic mapping profile does not match exact header fingerprint"
            )
        mapping_profile_id = require_string(
            metadata["mapping_profile_id"], "mapping_profile_id"
        )
        approved_mapping = validate_generic_mapping(
            require_object(
                registration.get("approved_mapping"),
                "approved_mapping",
            ),
            sealed_delivery_map=sealed_delivery_map,
            outcomes_accessed=outcomes_accessed,
        )
        if approved_mapping != mapping:
            raise AdapterError(
                "generic source-to-canonical mapping is not the approved mapping"
            )
        if registration.get("approved_mapping_profile_id") != mapping_profile_id:
            raise AdapterError(
                "generic mapping_profile_id is not the approved profile"
            )
        if (
            registration.get("approved_header_fingerprint")
            != expected_fingerprint
        ):
            raise AdapterError(
                "generic header fingerprint is not the approved fingerprint"
            )
        if registration.get("approved_source_container") != source_container:
            raise AdapterError(
                "generic source_container is not the approved container"
            )
        stable_targets = set(
            require_string_list(
                metadata["stable_id_targets"], "stable_id_targets"
            )
        )
        mapped_identity_targets = set(mapping.values()) & _IDENTITY_TARGETS
        if stable_targets != mapped_identity_targets:
            raise AdapterError(
                "generic mapping requires declared stable IDs for every "
                "identity target"
            )
        for source, target in mapping.items():
            if target not in _IDENTITY_TARGETS:
                continue
            token = _header_token(source)
            if token.endswith("_name") or not any(
                marker in token.split("_")
                for marker in ("id", "key", "uuid", "code", "identifier")
            ):
                raise AdapterError(
                    "generic identity mapping rejects name-only identity "
                    f"source: {source}"
                )
        required_targets = {
            "campaign_id",
            "date",
            "impressions",
            "clicks",
            "spend",
            "currency",
            "conversion_value",
        }
        if not required_targets.issubset(mapping.values()):
            raise AdapterError(
                "generic mapping is missing required aggregate targets"
            )
        if not ({"line_item_id", "ad_group_id"} & set(mapping.values())):
            raise AdapterError(
                "generic mapping requires a stable line-item or ad-group ID"
            )
        if metadata["aggregate_level"] != "already_aggregate":
            raise AdapterError("generic mapper rejects log-level data")
        for field, message in (
            ("currency_inferred", "generic mapper cannot infer currency"),
            (
                "currency_conversion",
                "generic mapper cannot perform currency conversion",
            ),
            (
                "cross_platform_reach_deduplication",
                "generic mapper cannot deduplicate cross-platform reach",
            ),
            (
                "reconstructed_attribution",
                "generic mapper cannot reconstruct attribution",
            ),
            ("mixed_time_bases", "generic mapper rejects mixed time bases"),
            (
                "automatic_adapter_promotion",
                "generic mapping cannot become a reusable adapter automatically",
            ),
        ):
            if metadata[field] is not False:
                raise AdapterError(message)
        if metadata["platform_proof_basis"] != "declared_not_filename":
            raise AdapterError(
                "filenames cannot establish generic platform proof"
            )

        timezone = require_string(metadata["timezone"], "timezone")
        time_basis = require_string(metadata["time_basis"], "time_basis")
        if time_basis != require_string(
            registration.get("time_basis"), "time_basis"
        ):
            raise AdapterError("generic time_basis does not match registration")
        currency = require_string(metadata["currency"], "currency")
        if currency != require_string(
            registration.get("currency"), "currency"
        ):
            raise AdapterError("generic currency does not match registration")
        attribution_semantics = require_string(
            metadata["attribution_semantics"], "attribution_semantics"
        )
        if attribution_semantics != require_string(
            registration.get("attribution_semantics"),
            "attribution_semantics",
        ):
            raise AdapterError(
                "generic attribution semantics do not match registration"
            )
        windows = require_string_list(
            metadata["attribution_windows"], "attribution_windows"
        )
        if windows != require_string_list(
            registration.get("attribution_windows"), "attribution_windows"
        ):
            raise AdapterError(
                "generic attribution windows do not match registration"
            )
        conversion_metric = require_string(
            metadata["conversion_metric"], "conversion_metric"
        )
        if conversion_metric != require_string(
            registration.get("registered_source_metric"),
            "registered_source_metric",
        ):
            raise AdapterError(
                "generic conversion metric does not match registration"
            )
        if conversion_metric != _target_source(
            mapping, "conversion_value"
        ):
            raise AdapterError(
                "generic conversion metric must be the explicitly mapped "
                "conversion source column"
            )
        null_tokens = set(
            require_string_list(
                metadata["admitted_null_tokens"],
                "admitted_null_tokens",
                allow_empty=True,
            )
        )
        null_value_state = require_string(
            metadata["null_value_state"], "null_value_state"
        )
        if null_value_state not in {"null", "absent", "suppressed"}:
            raise AdapterError("generic null_value_state is unsupported")
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
                "generic mapper requires its admitted omitted-zero behavior"
            )
        observed_at = require_timestamp(metadata["observed_at"], "observed_at")

        study_id = require_string(registration.get("study_id"), "study_id")
        registration_id = require_string(
            registration.get("registration_id"), "registration_id"
        )
        if (
            sealed_delivery_map["study_id"] != study_id
            or sealed_delivery_map["registration_id"] != registration_id
        ):
            raise AdapterError(
                "sealed delivery map does not match study and registration"
            )
        metric_id = require_string(registration.get("metric_id"), "metric_id")
        source_id = require_string(payload["source_id"], "source_id")
        import_id = require_string(payload["import_id"], "import_id")
        source_sha256 = require_source_sha256(payload["source_sha256"])
        sources = {
            target: _target_source(mapping, target)
            for target in required_targets
        }
        middle_target = next(
            target
            for target in ("line_item_id", "ad_group_id")
            if target in mapping.values()
        )
        has_creative = "creative_id" in mapping.values()
        has_ad = "ad_id" in mapping.values()
        creative_target = "creative_id" if has_creative else "ad_id"
        sources[middle_target] = _target_source(mapping, middle_target)
        sources[creative_target] = _target_source(mapping, creative_target)
        if has_ad:
            sources["ad_id"] = _target_source(mapping, "ad_id")
        if has_creative:
            sources["creative_id"] = _target_source(mapping, "creative_id")

        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for index, raw_row in enumerate(rows):
            path = f"generic rows[{index}]"
            row = require_closed_object(
                raw_row, {"source_row_reference", "values"}, path
            )
            reference = require_string(
                row["source_row_reference"], f"{path}.source_row_reference"
            )
            if reference in seen:
                raise AdapterError(
                    "generic source_row_reference values must be unique"
                )
            seen.add(reference)
            values = require_closed_object(
                row["values"], set(headers), f"{path}.values"
            )
            for source, target in mapping.items():
                value = values[source]
                if not isinstance(value, str):
                    continue
                if value in _COMMON_NULL_TOKENS or value in null_tokens:
                    if target != "conversion_value":
                        raise AdapterError(
                            f"{path}.values.{source} has a null token in "
                            f"required {target}"
                        )
                    if value not in null_tokens:
                        raise AdapterError(
                            f"{path}.values.{source} has an unknown null token"
                        )
            campaign_id = require_string(
                values[sources["campaign_id"]],
                f"{path}.values.{sources['campaign_id']}",
            )
            middle_id = require_string(
                values[sources[middle_target]],
                f"{path}.values.{sources[middle_target]}",
            )
            creative_id = require_string(
                values[sources[creative_target]],
                f"{path}.values.{sources[creative_target]}",
            )
            ad_id = (
                require_string(
                    values[sources["ad_id"]],
                    f"{path}.values.{sources['ad_id']}",
                )
                if has_ad
                else creative_id
            )
            matching_delivery_rows = [
                delivery
                for delivery in sealed_delivery_map["mappings"]
                if delivery["platform"] == source_platform
                and delivery["platform_campaign_id"] == campaign_id
                and delivery["platform_ad_group_id"] == middle_id
                and delivery["platform_ad_id"] == ad_id
                and delivery["platform_creative_id"] == creative_id
            ]
            if len(matching_delivery_rows) != 1:
                raise AdapterError(
                    f"{path} identity does not match exactly one sealed "
                    "delivery map row"
                )
            reporting_date = require_date(
                values[sources["date"]],
                f"{path}.values.{sources['date']}",
            )
            impressions_text, impressions = require_nonnegative_count(
                values[sources["impressions"]],
                f"{path}.values.{sources['impressions']}",
                strings_only=True,
            )
            clicks_text, clicks = require_nonnegative_count(
                values[sources["clicks"]],
                f"{path}.values.{sources['clicks']}",
                strings_only=True,
            )
            spend_text, spend = require_nonnegative_decimal(
                values[sources["spend"]],
                f"{path}.values.{sources['spend']}",
                strings_only=True,
            )
            row_currency = require_string(
                values[sources["currency"]],
                f"{path}.values.{sources['currency']}",
            )
            if row_currency != currency:
                raise AdapterError(
                    f"{path} currency does not match registered currency"
                )
            raw_outcome = values[sources["conversion_value"]]
            if raw_outcome in null_tokens:
                outcome: Decimal | None = None
                outcome_text = None
                explicit_state = null_value_state
            else:
                outcome_text, outcome = require_nonnegative_decimal(
                    raw_outcome,
                    f"{path}.values.{sources['conversion_value']}",
                    strings_only=True,
                )
                explicit_state = None
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
                    account_id="not_applicable",
                    campaign_id=campaign_id,
                    ad_group_id=middle_id,
                    creative_id=creative_id,
                    ad_id=ad_id,
                    reporting={
                        "start_date": reporting_date,
                        "end_date": reporting_date,
                        "timezone": timezone,
                        "basis": time_basis,
                        "request_level": creative_target.removesuffix("_id"),
                        "time_increment": "1",
                        "segment_grain": list(mapping.values()),
                        "latency_state": latency_state,
                        "observed_at": observed_at,
                    },
                    attribution={
                        "report_time": attribution_semantics,
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
                    spend_source_metric=sources["spend"],
                    spend_source_unit="declared_currency",
                    impressions=impressions,
                    impressions_source_text=impressions_text,
                    clicks=clicks,
                    clicks_source_text=clicks_text,
                    outcome=outcome,
                    outcome_source_text=outcome_text,
                    outcome_source_metric=conversion_metric,
                    conversion_quality=conversion_quality,
                    omitted_zero_behavior=omitted_zero,
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
                "mapping_profile": {
                    "mapping_profile_id": mapping_profile_id,
                    "header_fingerprint": expected_fingerprint,
                    "source_to_canonical": mapping,
                    "stable_id_targets": sorted(stable_targets),
                    "source_container": source_container,
                    "source_platform": source_platform,
                    "reusable_adapter_promotion": False,
                    "export_verified": False,
                },
                "warnings": [
                    "schema_tested_not_export_verified",
                    "explicit_mapping_does_not_establish_export_verification",
                ],
            },
        )

    @staticmethod
    def _payload_matches_source_inventory(
        rows: list[object],
        source_inventory: ContainerInventory,
        headers: tuple[str, ...],
    ) -> bool:
        table_order = {
            table: index
            for index, table in enumerate(source_inventory.tables)
        }
        physical_rows: dict[tuple[str, int], dict[str, str]] = {}
        for cell in source_inventory.cells:
            key = (cell.table, cell.row_number)
            target = physical_rows.setdefault(key, {})
            if cell.column_name in target:
                return False
            target[cell.column_name] = cell.value
        ordered_physical = [
            values
            for _, values in sorted(
                physical_rows.items(),
                key=lambda item: (
                    table_order.get(item[0][0], len(table_order)),
                    item[0][1],
                ),
            )
        ]
        payload_values: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                return False
            values = row.get("values")
            if not isinstance(values, Mapping):
                return False
            payload_values.append(dict(values))
        return (
            source_inventory.row_count == len(rows)
            and all(set(item) == set(headers) for item in ordered_physical)
            and ordered_physical == payload_values
        )
