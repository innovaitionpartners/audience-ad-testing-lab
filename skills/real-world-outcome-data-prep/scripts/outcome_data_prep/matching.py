"""Exact sealed-identity matching for normalized advertising outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys

from .common import ContractError, sha256_json
from .contracts import (
    OBSERVATION_BINDING_VERSION,
    validate_delivery_map,
    validate_normalized_observation,
    validate_observation_binding,
)
from .normalization import (
    AuthenticatedNormalizedBatch,
    EffectiveEvidenceStatusAuthority,
    authenticated_normalized_batch_effective_status_authority,
    verify_effective_evidence_status,
    verify_authenticated_normalized_batch,
)
from .study_authority import (
    AuthenticatedStudy,
    StudyAuthority,
    verify_study_authority,
)


PANEL_BUILDER_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
)
if str(PANEL_BUILDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))

from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    validate_preregistration,
)


@dataclass(frozen=True)
class MatchResult:
    matched: tuple[dict[str, object], ...]
    quarantined: tuple[dict[str, object], ...]


def _identity_key(document: Mapping[str, object]) -> tuple[str, ...]:
    return (
        str(document["platform"]),
        str(document["platform_campaign_id"]),
        str(document["platform_ad_group_id"]),
        str(document["platform_ad_id"]),
        str(document["platform_creative_id"]),
    )


def _row_key(row: Mapping[str, object]) -> tuple[str, ...]:
    return (
        str(row["platform"]),
        str(row["campaign"]["platform_id"]),  # type: ignore[index]
        str(row["ad_group"]["platform_id"]),  # type: ignore[index]
        str(row["ad"]["platform_id"]),  # type: ignore[index]
        str(row["creative"]["platform_id"]),  # type: ignore[index]
    )


def _registered_block(
    registration: Mapping[str, object], mapping: Mapping[str, object]
) -> Mapping[str, object]:
    blocks = registration["validation_blocks"]
    assert isinstance(blocks, list)
    selected = [
        block
        for block in blocks
        if isinstance(block, Mapping)
        and block["block_id"] == mapping["block_id"]
    ]
    if len(selected) != 1:
        raise ContractError("delivery identity block is not uniquely registered")
    block = selected[0]
    if block["study_id"] != mapping["study_id"]:
        raise ContractError("delivery identity study does not match registered block")
    if mapping["arm_id"] not in block["planned_arm_ids"]:
        raise ContractError("delivery identity arm is not registered")
    membership = [
        item["segment_ids"]
        for item in block["planned_segment_membership"]
        if item["arm_id"] == mapping["arm_id"]
    ]
    if len(membership) != 1 or membership[0] != mapping["segment_ids"]:
        raise ContractError(
            "delivery identity segment membership is not registered"
        )
    return block


def _require_mapping_matches_registration(
    mapping: Mapping[str, object], registration: Mapping[str, object]
) -> None:
    _registered_block(registration, mapping)
    surface = registration["synthetic_surface"]
    assert isinstance(surface, Mapping)
    creatives = surface["eligible_creatives"]
    assert isinstance(creatives, list)
    selected = [
        item
        for item in creatives
        if isinstance(item, Mapping)
        and item["creative_id"] == mapping["creative_id"]
    ]
    if len(selected) != 1:
        raise ContractError("delivery identity creative is not registered")
    if selected[0]["creative_sha256"] != mapping["asset_sha256"]:
        raise ContractError(
            "delivery identity asset does not match registered creative"
        )


def _binding(
    *,
    row: Mapping[str, object],
    mapping: Mapping[str, object],
    delivery_map_sha256: str,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
    effective_status_authority: EffectiveEvidenceStatusAuthority,
) -> dict[str, object]:
    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    evidence_status = verify_effective_evidence_status(
        effective_status_authority,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    row = validate_normalized_observation(row)
    try:
        registration = validate_preregistration(study.registration)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            "authenticated registration is not a valid preregistration"
        ) from exc
    validated_map = validate_delivery_map(study.delivery_map)
    if delivery_map_sha256 != validated_map["delivery_map_sha256"]:
        raise ContractError(
            "delivery map digest does not match authenticated study"
        )
    sealed_mappings = validated_map["mappings"]
    assert isinstance(sealed_mappings, list)
    if len([item for item in sealed_mappings if item == mapping]) != 1:
        raise ContractError("delivery identity is not in the authenticated map")
    _require_mapping_matches_registration(mapping, registration)
    projection = row["validation_projection"]
    assert isinstance(projection, Mapping)
    if projection["status"] != "available":
        raise ContractError(
            "normalized observation validation projection is unavailable"
        )
    if projection["evidence_status"] != evidence_status:
        raise ContractError(
            "row evidence status does not match sealed delivery chronology"
        )
    metric = registration["primary_metric"]
    panel = registration["panel_binding"]
    surface = registration["synthetic_surface"]
    assert isinstance(metric, Mapping)
    assert isinstance(panel, Mapping)
    assert isinstance(surface, Mapping)
    if row["outcome"]["metric_id"] != metric["name"]:  # type: ignore[index]
        raise ContractError("row metric does not match sealed registration")
    if projection["measurement_window"] != metric["measurement_window"]:
        raise ContractError(
            "row measurement window does not match sealed registration"
        )
    if projection["attribution_window"] != metric["attribution_window"]:
        raise ContractError(
            "row attribution window does not match sealed registration"
        )
    if row["study_id"] != mapping["study_id"]:
        raise ContractError("row study identity is mismatched")
    if row["registration_id"] != registration["registration_id"]:
        raise ContractError("row registration identity is mismatched")
    if row["source_sha256"] is None:
        raise ContractError("row source identity is missing")
    document = {
        "schema_version": OBSERVATION_BINDING_VERSION,
        "observation_id": row["observation_id"],
        "registration_id": registration["registration_id"],
        "registration_sha256": registration["registration_sha256"],
        "normalized_observation_sha256": row[
            "normalized_observation_sha256"
        ],
        "delivery_map_sha256": delivery_map_sha256,
        "delivery_mapping_id": mapping["mapping_id"],
        "delivery_mapping_sha256": sha256_json(mapping),
        "campaign_plan_sha256": mapping["campaign_plan_sha256"],
        "platform": mapping["platform"],
        "platform_campaign_id": mapping["platform_campaign_id"],
        "platform_ad_group_id": mapping["platform_ad_group_id"],
        "platform_ad_id": mapping["platform_ad_id"],
        "platform_creative_id": mapping["platform_creative_id"],
        "block_id": mapping["block_id"],
        "study_id": mapping["study_id"],
        "arm_id": mapping["arm_id"],
        "batch_id": mapping["batch_id"],
        "segment_ids": sorted(mapping["segment_ids"]),
        "creative_id": mapping["creative_id"],
        "variant_id": mapping["variant_id"],
        "asset_sha256": mapping["asset_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "package_sha256": panel["package_sha256"],
        "run_id": surface["run_id"],
        "result_sha256": surface["result_sha256"],
        "metric_id": metric["name"],
        "measurement_window": metric["measurement_window"],
        "attribution_window": metric["attribution_window"],
        "source_sha256": row["source_sha256"],
        "source_row_reference": row["source_row_reference"],
        "evidence_status": evidence_status,
        "observation_binding_sha256": None,
    }
    return validate_observation_binding(document)


def match_normalized_rows(
    *,
    authenticated_batch: AuthenticatedNormalizedBatch,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> MatchResult:
    """Match rows through one exact authenticated delivery-map identity."""

    study = verify_study_authority(
        authenticated_study, authority=study_authority
    )
    rows = verify_authenticated_normalized_batch(
        authenticated_batch,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    effective_status_authority = (
        authenticated_normalized_batch_effective_status_authority(
            authenticated_batch,
            authenticated_study=authenticated_study,
            study_authority=study_authority,
        )
    )
    try:
        registered = validate_preregistration(study.registration)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            "authenticated registration is not a valid preregistration"
        ) from exc
    validated_map = validate_delivery_map(study.delivery_map)
    if validated_map["registration_id"] != registered["registration_id"]:
        raise ContractError(
            "delivery map registration does not match sealed registration"
        )
    mappings = validated_map["mappings"]
    assert isinstance(mappings, list)
    index: dict[tuple[str, ...], Mapping[str, object]] = {}
    for mapping in mappings:
        assert isinstance(mapping, Mapping)
        _require_mapping_matches_registration(mapping, registered)
        key = _identity_key(mapping)
        if key in index:
            raise ContractError("duplicate sealed identity in delivery map")
        index[key] = mapping

    checked_rows = [validate_normalized_observation(row) for row in rows]
    observation_ids = [str(row["observation_id"]) for row in checked_rows]
    if len(observation_ids) != len(set(observation_ids)):
        raise ContractError("duplicate normalized observation identity")

    matched: list[dict[str, object]] = []
    quarantined: list[dict[str, object]] = []
    for row in sorted(
        checked_rows,
        key=lambda item: (
            str(item["source_sha256"]),
            str(item["source_row_reference"]),
            str(item["observation_id"]),
        ),
    ):
        if row["registration_id"] != registered["registration_id"]:
            raise ContractError("row registration identity is mismatched")
        mapping = index.get(_row_key(row))
        if mapping is None:
            quarantined.append({
                "observation_id": row["observation_id"],
                "source_id": row["source_id"],
                "source_sha256": row["source_sha256"],
                "source_row_reference": row["source_row_reference"],
                "reason": "identity_not_sealed",
            })
            continue
        delivery_binding = _binding(
            row=row,
            mapping=mapping,
            delivery_map_sha256=str(validated_map["delivery_map_sha256"]),
            authenticated_study=authenticated_study,
            study_authority=study_authority,
            effective_status_authority=effective_status_authority,
        )
        matched.append({
            "normalized_observation": deepcopy(row),
            "delivery_binding": delivery_binding,
        })
    return MatchResult(tuple(matched), tuple(quarantined))


__all__ = ["MatchResult", "match_normalized_rows"]
