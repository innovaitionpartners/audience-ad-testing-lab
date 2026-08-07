"""Mechanical projection into unchanged Tier 4 validation observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
import re
import sys

from .common import ContractError, closed_object, sha256_json
from .contracts import (
    validate_normalized_observation,
    validate_observation_binding,
)
from .matching import match_normalized_rows
from .normalization import AuthenticatedNormalizedBatch
from .study_authority import AuthenticatedStudy, StudyAuthority


PANEL_BUILDER_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
)
if str(PANEL_BUILDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))

from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    project_shared_outcome_evidence,
    validate_preregistration,
    validate_shared_outcome_evidence,
    validate_validation_observation,
)


HANDOFF_VERSION = "outcome-validation-handoff-v1"
_MATCHED_KEYS = {"normalized_observation", "delivery_binding"}
_HANDOFF_KEYS = {
    "schema_version", "registration_binding", "normalized_observations",
    "observation_bindings", "validation_observations", "handoff_sha256",
}
_REGISTRATION_BINDING_KEYS = {"registration_id", "registration_sha256"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _registered_block(
    registration: Mapping[str, object],
    binding: Mapping[str, object],
) -> Mapping[str, object]:
    blocks = registration["validation_blocks"]
    assert isinstance(blocks, list)
    selected = [
        block
        for block in blocks
        if isinstance(block, Mapping)
        and block["block_id"] == binding["block_id"]
    ]
    if len(selected) != 1:
        raise ContractError("delivery binding block is not registered")
    block = selected[0]
    if block["study_id"] != binding["study_id"]:
        raise ContractError("delivery binding study is mismatched")
    if binding["arm_id"] not in block["planned_arm_ids"]:
        raise ContractError("delivery binding arm is mismatched")
    memberships = [
        item["segment_ids"]
        for item in block["planned_segment_membership"]
        if item["arm_id"] == binding["arm_id"]
    ]
    if len(memberships) != 1 or memberships[0] != binding["segment_ids"]:
        raise ContractError("delivery binding segments are mismatched")
    return block


def _require_binding(
    *,
    row: Mapping[str, object],
    registration: Mapping[str, object],
    binding: Mapping[str, object],
) -> dict[str, object]:
    checked = validate_observation_binding(binding)
    panel = registration["panel_binding"]
    surface = registration["synthetic_surface"]
    metric = registration["primary_metric"]
    projection = row["validation_projection"]
    assert isinstance(panel, Mapping)
    assert isinstance(surface, Mapping)
    assert isinstance(metric, Mapping)
    assert isinstance(projection, Mapping)
    expected = {
        "observation_id": row["observation_id"],
        "registration_id": registration["registration_id"],
        "registration_sha256": registration["registration_sha256"],
        "normalized_observation_sha256": row[
            "normalized_observation_sha256"
        ],
        "platform": row["platform"],
        "platform_campaign_id": row["campaign"]["platform_id"],  # type: ignore[index]
        "platform_ad_group_id": row["ad_group"]["platform_id"],  # type: ignore[index]
        "platform_ad_id": row["ad"]["platform_id"],  # type: ignore[index]
        "platform_creative_id": row["creative"]["platform_id"],  # type: ignore[index]
        "study_id": row["study_id"],
        "panel_sha256": panel["panel_sha256"],
        "package_sha256": panel["package_sha256"],
        "run_id": surface["run_id"],
        "result_sha256": surface["result_sha256"],
        "metric_id": metric["name"],
        "measurement_window": metric["measurement_window"],
        "attribution_window": metric["attribution_window"],
        "source_sha256": row["source_sha256"],
        "source_row_reference": row["source_row_reference"],
    }
    for field, value in expected.items():
        if checked[field] != value:
            raise ContractError(
                f"delivery binding {field} does not match sealed identity"
            )
    _registered_block(registration, checked)
    creatives = surface["eligible_creatives"]
    assert isinstance(creatives, list)
    selected = [
        item
        for item in creatives
        if isinstance(item, Mapping)
        and item["creative_id"] == checked["creative_id"]
    ]
    if (
        len(selected) != 1
        or selected[0]["creative_sha256"] != checked["asset_sha256"]
    ):
        raise ContractError(
            "delivery binding creative or asset is mismatched"
        )
    if projection["measurement_window"] != checked["measurement_window"]:
        raise ContractError("row measurement window is mismatched")
    if projection["attribution_window"] != checked["attribution_window"]:
        raise ContractError("row attribution window is mismatched")
    if projection["evidence_status"] != checked["evidence_status"]:
        raise ContractError("row evidence status is mismatched")
    return checked


def _registration_binding(
    registration: Mapping[str, object],
) -> dict[str, object]:
    access = registration["prior_outcome_access"]
    assert isinstance(access, list)
    access_hashes = [entry["access_sha256"] for entry in access]
    return {
        "registration_id": registration["registration_id"],
        "registration_sha256": registration["registration_sha256"],
        "registered_at": registration["registered_at"],
        "status": registration["status"],
        "prior_outcome_access_sha256": sha256_json(access_hashes),
        "prior_outcome_access_hashes": access_hashes,
        "holdout_partition": deepcopy(registration["holdout_partition"]),
        "claim_scope": deepcopy(registration["claim_scope"]),
        "multiplicity_rules": deepcopy(registration["multiplicity_rules"]),
        "preregistration": deepcopy(dict(registration)),
    }


def _holdout_status(document: Mapping[str, object]) -> str:
    registration_binding = document["registration_binding"]
    assignment = document["assignment"]
    source = document["source"]
    assert isinstance(registration_binding, Mapping)
    assert isinstance(assignment, Mapping)
    assert isinstance(source, Mapping)
    registration = registration_binding["preregistration"]
    partition = registration_binding["holdout_partition"]
    assert isinstance(registration, Mapping)
    assert isinstance(partition, Mapping)
    if registration_binding["status"] != "registered":
        return "descriptive_only"
    blocks = registration["validation_blocks"]
    assert isinstance(blocks, list)
    block = next(
        item for item in blocks if item["block_id"] == document["block_id"]
    )
    partition_identity = (
        document["block_id"]
        if partition["partition_unit"] == "block"
        else block["study_id"]
    )
    if partition_identity not in partition["held_out_ids"]:
        return "in_sample"
    if document["claim_scope"] != registration_binding["claim_scope"]:
        return "mismatched"
    if assignment["leakage_detected"] is True:
        return "leaked"
    if source["source_sha256"] in registration_binding[
        "prior_outcome_access_hashes"
    ]:
        return "in_sample"
    if assignment["design"] != "randomized":
        return "mismatched"
    return "eligible_held_out"


def _build_validation_observation(
    *,
    normalized_observation: Mapping[str, object],
    registration: Mapping[str, object],
    delivery_binding: Mapping[str, object],
) -> dict[str, object]:
    """Build one Tier 4 observation without making an eligibility decision."""

    row = validate_normalized_observation(normalized_observation)
    try:
        registered = validate_preregistration(registration)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("registration is not a valid preregistration") from exc
    projection = row["validation_projection"]
    assert isinstance(projection, Mapping)
    if projection["status"] != "available":
        raise ContractError("validation projection is unavailable")
    binding = _require_binding(
        row=row,
        registration=registered,
        binding=delivery_binding,
    )
    if binding["evidence_status"] == "descriptive_only":
        raise ContractError(
            "descriptive evidence must stop before Tier 4 materialization"
        )
    if binding["evidence_status"] != "preregistered_holdout":
        raise ContractError(
            "blocked evidence cannot become a Tier 4 observation"
        )
    metric = registered["primary_metric"]
    claim_scope = registered["claim_scope"]
    panel = registered["panel_binding"]
    surface = registered["synthetic_surface"]
    assert isinstance(metric, Mapping)
    assert isinstance(claim_scope, Mapping)
    assert isinstance(panel, Mapping)
    assert isinstance(surface, Mapping)

    eligible = projection["eligible_exposure_count"]
    missing = projection["missing_outcome_count"]
    assert isinstance(eligible, int) and not isinstance(eligible, bool)
    assert isinstance(missing, int) and not isinstance(missing, bool)
    shared_id = "shared-" + sha256_json({
        "normalized_observation_sha256": row[
            "normalized_observation_sha256"
        ],
        "observation_binding_sha256": binding[
            "observation_binding_sha256"
        ],
    }).removeprefix("sha256:")
    shared = {
        "schema_version": "panel-shared-outcome-evidence-v1",
        "shared_evidence_id": shared_id,
        "study_id": binding["study_id"],
        "block_id": binding["block_id"],
        "arm_id": binding["arm_id"],
        "creative_binding": {
            "creative_id": binding["creative_id"],
            "creative_sha256": binding["asset_sha256"],
        },
        "outcome_scope": deepcopy(claim_scope["outcome_scope"]),
        "metric": deepcopy(metric),
        "metric_family": projection["metric_family"],
        "units": {
            "exposure": metric["exposure_unit"],
            "outcome": metric["outcome_unit"],
        },
        "assignment": deepcopy(projection["assignment"]),
        "windows": {
            "measurement": projection["measurement_window"],
            "attribution": projection["attribution_window"],
        },
        "aggregate": deepcopy(projection["aggregate"]),
        "precision": {
            "confidence_level": projection["confidence_level"],
        },
        "sample": {
            "eligible_exposure_count": eligible,
            "effective_sample_size": projection["effective_sample_size"],
        },
        "missingness": {
            "status": "none" if missing == 0 else "present",
            "eligible_exposure_count": eligible,
            "missing_outcome_count": missing,
            "rate": missing / eligible if eligible else 0.0,
        },
        "segment_ids": deepcopy(binding["segment_ids"]),
        "exclusions": [],
        "source": {
            "source_id": row["source_id"],
            "source_sha256": row["source_sha256"],
            "permission_confirmed": projection["permission_confirmed"],
        },
        "outcome_accessed_at": projection["outcome_accessed_at"],
        "limitations": deepcopy(projection["limitations"]),
        "shared_evidence_sha256": None,
    }
    shared["shared_evidence_sha256"] = sha256_json(shared)
    shared = validate_shared_outcome_evidence(shared)

    synthetic_binding = {
        "surface": surface["surface"],
        "run_id": surface["run_id"],
        "result_sha256": surface["result_sha256"],
    }
    observation = {
        "schema_version": "panel-validation-observation-v1",
        "observation_id": "validation-" + sha256_json({
            "shared_evidence_sha256": shared["shared_evidence_sha256"],
            "registration_sha256": registered["registration_sha256"],
        }).removeprefix("sha256:"),
        "registration_binding": _registration_binding(registered),
        "shared_outcome_evidence_binding": {
            "shared_evidence_id": shared["shared_evidence_id"],
            "study_id": shared["study_id"],
            "shared_evidence_sha256": shared["shared_evidence_sha256"],
        },
        "block_id": shared["block_id"],
        "arm_id": shared["arm_id"],
        "creative_binding": deepcopy(shared["creative_binding"]),
        "synthetic_binding": synthetic_binding,
        "panel_binding": deepcopy(panel),
        "claim_scope": deepcopy(claim_scope),
        "outcome_scope": deepcopy(shared["outcome_scope"]),
        "metric": deepcopy(shared["metric"]),
        "metric_family": shared["metric_family"],
        "units": deepcopy(shared["units"]),
        "assignment": deepcopy(shared["assignment"]),
        "windows": deepcopy(shared["windows"]),
        "aggregate": deepcopy(shared["aggregate"]),
        "precision": deepcopy(shared["precision"]),
        "sample": deepcopy(shared["sample"]),
        "missingness": deepcopy(shared["missingness"]),
        "segment_ids": deepcopy(shared["segment_ids"]),
        "exclusions": deepcopy(shared["exclusions"]),
        "source": deepcopy(shared["source"]),
        "outcome_accessed_at": shared["outcome_accessed_at"],
        "holdout_status": None,
        "limitations": deepcopy(shared["limitations"]),
        "observation_sha256": None,
    }
    observation["holdout_status"] = _holdout_status(observation)
    observation["observation_sha256"] = sha256_json(observation)
    checked = validate_validation_observation(observation)
    projected = project_shared_outcome_evidence(checked)
    validate_shared_outcome_evidence(projected)
    return checked


def build_validation_observation(
    *,
    observation_id: str,
    authenticated_batch: AuthenticatedNormalizedBatch,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
) -> dict[str, object]:
    """Build one observation only from the live authenticated batch."""

    if not isinstance(observation_id, str) or not observation_id:
        raise ContractError("observation_id must be a non-empty string")
    matched = match_normalized_rows(
        authenticated_batch=authenticated_batch,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    ).matched
    selected = [
        item
        for item in matched
        if item["normalized_observation"]["observation_id"] == observation_id
    ]
    if len(selected) != 1:
        raise ContractError(
            "observation_id does not identify one authenticated matched row"
        )
    item = selected[0]
    return _build_validation_observation(
        normalized_observation=item["normalized_observation"],
        registration=authenticated_study.registration,
        delivery_binding=item["delivery_binding"],
    )


def validate_validation_handoff(
    *,
    authenticated_batch: AuthenticatedNormalizedBatch,
    authenticated_study: AuthenticatedStudy,
    study_authority: StudyAuthority,
    validation_observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Validate and deterministically package matched prep records."""

    registration = authenticated_study.registration
    try:
        registered = validate_preregistration(registration)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("registration is not a valid preregistration") from exc
    matched_result = match_normalized_rows(
        authenticated_batch=authenticated_batch,
        authenticated_study=authenticated_study,
        study_authority=study_authority,
    )
    normalized_observations = matched_result.matched
    if not normalized_observations or not validation_observations:
        raise ContractError("validation handoff observations must not be empty")
    matched: list[dict[str, object]] = []
    for index, raw in enumerate(normalized_observations):
        item = closed_object(
            raw, _MATCHED_KEYS, f"normalized_observations[{index}]"
        )
        row = validate_normalized_observation(
            item["normalized_observation"]
        )
        binding = validate_observation_binding(item["delivery_binding"])
        matched.append({
            "normalized_observation": row,
            "delivery_binding": binding,
        })
    checked_observations = [
        validate_validation_observation(item)
        for item in validation_observations
    ]
    expected_by_id: dict[str, dict[str, object]] = {}
    for item in matched:
        expected = _build_validation_observation(
            normalized_observation=item["normalized_observation"],
            registration=registered,
            delivery_binding=item["delivery_binding"],
        )
        observation_id = str(expected["observation_id"])
        if observation_id in expected_by_id:
            raise ContractError("duplicate matched observation identity")
        expected_by_id[observation_id] = expected
    supplied_by_id = {
        str(item["observation_id"]): item for item in checked_observations
    }
    if len(supplied_by_id) != len(checked_observations):
        raise ContractError("duplicate validation observation identity")
    if supplied_by_id != expected_by_id:
        raise ContractError(
            "validation observations do not equal the matched projection"
        )
    sorted_matched = sorted(
        matched,
        key=lambda item: str(
            item["normalized_observation"]["observation_id"]  # type: ignore[index]
        ),
    )
    sorted_observations = [
        supplied_by_id[key] for key in sorted(supplied_by_id)
    ]
    document = {
        "schema_version": HANDOFF_VERSION,
        "registration_binding": {
            "registration_id": registered["registration_id"],
            "registration_sha256": registered["registration_sha256"],
        },
        "normalized_observations": [
            item["normalized_observation"] for item in sorted_matched
        ],
        "observation_bindings": [
            item["delivery_binding"] for item in sorted_matched
        ],
        "validation_observations": sorted_observations,
        "handoff_sha256": None,
    }
    document["handoff_sha256"] = sha256_json(document)
    return validate_validation_handoff_document(document)


def validate_validation_handoff_document(payload: object) -> dict[str, object]:
    """Validate exact persisted Task 9 output without recreating its batch.

    Live batch/study reauthentication remains mandatory when the handoff is
    created. Publication uses this strict persisted form, then authority-binds
    its exact digest into the immutable import ledger envelope.
    """

    document = closed_object(payload, _HANDOFF_KEYS, "validation_handoff")
    if document["schema_version"] != HANDOFF_VERSION:
        raise ContractError("validation_handoff schema version is invalid")
    registration = closed_object(
        document["registration_binding"],
        _REGISTRATION_BINDING_KEYS,
        "validation_handoff.registration_binding",
    )
    if not isinstance(registration["registration_id"], str) or not registration[
        "registration_id"
    ]:
        raise ContractError("validation_handoff registration identity is invalid")
    if (
        not isinstance(registration["registration_sha256"], str)
        or not _DIGEST.fullmatch(registration["registration_sha256"])
    ):
        raise ContractError("validation_handoff registration digest is invalid")
    rows_raw = document["normalized_observations"]
    bindings_raw = document["observation_bindings"]
    observations_raw = document["validation_observations"]
    if not all(isinstance(value, list) for value in (
        rows_raw, bindings_raw, observations_raw
    )):
        raise ContractError("validation_handoff collections must be arrays")
    rows = [validate_normalized_observation(item) for item in rows_raw]
    bindings = [validate_observation_binding(item) for item in bindings_raw]
    observations = [
        validate_validation_observation(item) for item in observations_raw
    ]
    if not rows or len(rows) != len(bindings) or len(rows) != len(observations):
        raise ContractError(
            "validation_handoff collections must be nonempty and aligned"
        )
    for index, (row, binding) in enumerate(zip(rows, bindings)):
        if (
            binding["observation_id"] != row["observation_id"]
            or binding["normalized_observation_sha256"]
            != row["normalized_observation_sha256"]
            or binding["registration_id"] != registration["registration_id"]
            or binding["registration_sha256"]
            != registration["registration_sha256"]
        ):
            raise ContractError(
                f"validation_handoff row {index} binding is inconsistent"
            )
    if len({str(row["observation_id"]) for row in rows}) != len(rows):
        raise ContractError(
            "validation_handoff observation identities are duplicated"
        )
    if rows != sorted(rows, key=lambda item: str(item["observation_id"])):
        raise ContractError("validation_handoff normalized rows are not canonical")
    if len({str(item["observation_id"]) for item in observations}) != len(
        observations
    ):
        raise ContractError(
            "validation_handoff validation observations are duplicated"
        )
    if observations != sorted(
        observations, key=lambda item: str(item["observation_id"])
    ):
        raise ContractError(
            "validation_handoff validation observations are not canonical"
        )
    embedded_binding = observations[0]["registration_binding"]
    if not isinstance(embedded_binding, Mapping):
        raise ContractError(
            "validation_handoff observation registration is invalid"
        )
    embedded_registration = embedded_binding.get("preregistration")
    if not isinstance(embedded_registration, Mapping):
        raise ContractError(
            "validation_handoff observation preregistration is invalid"
        )
    if (
        embedded_registration.get("registration_id")
        != registration["registration_id"]
        or embedded_registration.get("registration_sha256")
        != registration["registration_sha256"]
    ):
        raise ContractError(
            "validation_handoff observation registration is inconsistent"
        )
    expected_observations = [
        _build_validation_observation(
            normalized_observation=row,
            registration=embedded_registration,
            delivery_binding=binding,
        )
        for row, binding in zip(rows, bindings)
    ]
    expected_by_id = {
        str(item["observation_id"]): item for item in expected_observations
    }
    supplied_by_id = {
        str(item["observation_id"]): item for item in observations
    }
    if supplied_by_id != expected_by_id:
        raise ContractError(
            "validation_handoff observations do not equal the matched projection"
        )
    supplied = document["handoff_sha256"]
    unhashed = {
        "schema_version": HANDOFF_VERSION,
        "registration_binding": registration,
        "normalized_observations": rows,
        "observation_bindings": bindings,
        "validation_observations": observations,
        "handoff_sha256": None,
    }
    if not isinstance(supplied, str) or sha256_json(unhashed) != supplied:
        raise ContractError("validation_handoff self-hash is invalid")
    return {**unhashed, "handoff_sha256": supplied}


__all__ = [
    "HANDOFF_VERSION",
    "build_validation_observation",
    "validate_validation_handoff",
    "validate_validation_handoff_document",
]
