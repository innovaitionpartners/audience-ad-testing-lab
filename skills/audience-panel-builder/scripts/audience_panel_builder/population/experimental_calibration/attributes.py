"""Pre-outcome creative-attribute registration for the synthetic sandbox."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy

from ...common import ContractError, require_timestamp, sha256_json
from .contracts import ATTRIBUTE_REGISTRY_VERSION, validate_creative_attribute_registry


def build_creative_attribute_registry(
    *,
    registry_id: str,
    registered_at: str,
    creative_bindings: Sequence[Mapping[str, object]],
    attribute_definitions: Sequence[Mapping[str, object]],
    creative_attributes: Sequence[Mapping[str, object]],
    annotation_methods: Sequence[Mapping[str, object]],
    reviewed_by: str,
    reviewed_at: str,
    earliest_outcome_accessed_at: str,
) -> dict[str, object]:
    """Build and fully validate one canonical, pre-outcome registry."""

    if require_timestamp(registered_at, "registered_at") >= require_timestamp(
        earliest_outcome_accessed_at,
        "earliest_outcome_accessed_at",
    ):
        raise ContractError(
            "creative attributes must be registered before outcome access"
        )
    document: dict[str, object] = {
        "schema_version": ATTRIBUTE_REGISTRY_VERSION,
        "registry_id": registry_id,
        "registered_at": registered_at,
        "creative_bindings": sorted(
            deepcopy(list(creative_bindings)),
            key=lambda item: str(item.get("creative_id", "")),
        ),
        "attribute_definitions": sorted(
            deepcopy(list(attribute_definitions)),
            key=lambda item: str(item.get("attribute_id", "")),
        ),
        "creative_attributes": sorted(
            deepcopy(list(creative_attributes)),
            key=lambda item: (
                str(item.get("creative_id", "")),
                str(item.get("attribute_id", "")),
            ),
        ),
        "annotation_methods": sorted(
            deepcopy(list(annotation_methods)),
            key=lambda item: str(item.get("method_id", "")),
        ),
        "review": {
            "status": "approved",
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
        },
        "outcome_access_boundary": {
            "status": "pre_outcome",
            "earliest_outcome_accessed_at": earliest_outcome_accessed_at,
        },
        "registry_sha256": None,
    }
    document["registry_sha256"] = sha256_json(document)
    return validate_creative_attribute_registry(document)
