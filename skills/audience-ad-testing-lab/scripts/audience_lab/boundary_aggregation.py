"""Canonical authenticated boundary aggregation shared by CLI and dispatch."""

from __future__ import annotations

from collections import Counter
import hashlib
from typing import Any, Mapping, Sequence

from .contracts import (
    validate_boundary_profile_attachments,
    validate_manifest,
)
from .pairwise import PairwiseConfig, resolve_boundary
from .responses import validate_response


def _creative_roster(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = manifest.get("outputs")
    hashes = (
        outputs.get("creative_asset_hashes")
        if isinstance(outputs, Mapping)
        else None
    )
    if not isinstance(hashes, Mapping) or not hashes:
        raise ValueError(
            "manifest.outputs.creative_asset_hashes must be a non-empty object"
        )
    if not all(
        isinstance(creative_id, str) and creative_id.strip()
        for creative_id in hashes
    ):
        raise ValueError("manifest creative IDs must be non-empty strings")
    return tuple(sorted(hashes))


def _groups(
    screening: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    classifications = screening.get("classifications")
    if not isinstance(classifications, Mapping):
        raise ValueError("screening classifications must be an object")
    return (
        sorted(
            creative_id
            for creative_id, status in classifications.items()
            if status == "clear_finalist"
        ),
        sorted(
            creative_id
            for creative_id, status in classifications.items()
            if status == "boundary_candidate"
        ),
        sorted(
            creative_id
            for creative_id, status in classifications.items()
            if status == "clear_non_finalist"
        ),
    )


def _seed(manifest: Mapping[str, Any]) -> int:
    assignment = manifest.get("assignment")
    assignment_seed = (
        assignment.get("randomization_seed")
        if isinstance(assignment, Mapping)
        else ""
    )
    material = (
        f"{manifest.get('study_id', '')}|{assignment_seed}|"
        "boundary-bootstrap-v1"
    )
    return int.from_bytes(
        hashlib.sha256(material.encode("utf-8")).digest()[:8],
        "big",
    )


def _require_authenticated_inputs(
    manifest: Mapping[str, Any],
    screening: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
) -> None:
    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        raise ValueError(
            "canonical boundary manifest is invalid: "
            + "; ".join(manifest_errors)
        )
    roster = set(_creative_roster(manifest))
    if screening.get("study_id") != manifest.get("study_id"):
        raise ValueError(
            "canonical screening result study_id does not match the manifest"
        )
    if screening.get("method") != manifest.get("method"):
        raise ValueError(
            "canonical screening result method does not match the manifest"
        )
    if screening.get("validity_status") != "valid":
        raise ValueError(
            "canonical boundary aggregation requires a valid screening result"
        )
    if (
        screening.get("requested_top_k")
        != manifest.get("requested_shortlist_size")
    ):
        raise ValueError(
            "canonical screening result shortlist does not match the manifest"
        )
    classifications = screening.get("classifications")
    if (
        not isinstance(classifications, Mapping)
        or set(classifications) != roster
        or any(
            status
            not in {
                "clear_finalist",
                "boundary_candidate",
                "clear_non_finalist",
            }
            for status in classifications.values()
        )
    ):
        raise ValueError(
            "canonical screening classifications must exactly cover the "
            "manifest creative roster"
        )
    boundary_plan = screening.get("boundary_plan")
    if not isinstance(boundary_plan, Mapping):
        raise ValueError(
            "canonical screening result requires a frozen boundary plan"
        )
    if boundary_plan.get("plan_version") != "predeclared-boundary-v1":
        raise ValueError("canonical boundary plan version is unsupported")
    if boundary_plan.get("frozen_before_dispatch") is not True:
        raise ValueError("canonical boundary plan was not frozen before dispatch")
    assignments = boundary_plan.get("predeclared_pair_assignments")
    if not isinstance(assignments, list) or not assignments:
        raise ValueError(
            "canonical boundary plan requires predeclared pair assignments"
        )
    profile_rosters = manifest.get("audience_profile_rosters")
    boundary_roster = (
        profile_rosters.get("boundary_reserve")
        if isinstance(profile_rosters, Mapping)
        else None
    )
    if isinstance(boundary_roster, Mapping):
        validate_boundary_profile_attachments(
            boundary_plan,
            boundary_roster,
        )
    capacity = manifest.get("synthetic_replicate_capacity")
    if not isinstance(capacity, Mapping):
        raise ValueError("canonical boundary capacity must be an object")
    jobs_per_wave = capacity.get("boundary_jobs_per_wave")
    waves_max = capacity.get("boundary_waves_max")
    reserved = capacity.get("boundary_reserved")
    available = boundary_plan.get("available_boundary_reserve")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        for value in (jobs_per_wave, waves_max, reserved)
    ):
        raise ValueError("canonical boundary capacity is invalid")
    if reserved != jobs_per_wave * waves_max:
        raise ValueError("canonical boundary reserve does not bind its waves")
    if (
        isinstance(available, bool)
        or not isinstance(available, int)
        or available < 1
        or available > reserved
    ):
        raise ValueError(
            "canonical available boundary reserve is invalid"
        )
    segment_weights = manifest.get("audience_lock", {}).get(
        "segment_weights"
    )
    if not isinstance(segment_weights, Mapping) or not segment_weights:
        raise ValueError(
            "canonical boundary manifest requires locked segment weights"
        )
    candidates = set(_groups(screening)[1])
    seen_response_ids: list[str] = []
    seen_replicate_ids: list[str] = []
    for index, response in enumerate(responses):
        errors = validate_response(response)
        if errors:
            raise ValueError(
                f"canonical boundary response[{index}] is invalid: "
                + "; ".join(errors)
            )
        if response.get("record_type") != "boundary_response":
            raise ValueError(
                f"canonical boundary response[{index}] has the wrong type"
            )
        if response.get("study_id") != manifest.get("study_id"):
            raise ValueError(
                f"canonical boundary response[{index}] study_id is invalid"
            )
        if response.get("segment_id") not in segment_weights:
            raise ValueError(
                f"canonical boundary response[{index}] segment is not locked"
            )
        assigned = response.get("assigned_variation_ids")
        if (
            not isinstance(assigned, list)
            or len(assigned) != 2
            or not set(assigned) <= candidates
        ):
            raise ValueError(
                f"canonical boundary response[{index}] is outside the "
                "frozen candidate set"
            )
        seen_response_ids.append(str(response.get("response_id")))
        seen_replicate_ids.append(
            str(response.get("synthetic_replicate_id"))
        )
    for field, values in (
        ("response_id", seen_response_ids),
        ("synthetic_replicate_id", seen_replicate_ids),
    ):
        duplicates = [
            value
            for value, count in Counter(values).items()
            if count > 1
        ]
        if duplicates:
            raise ValueError(
                f"canonical boundary responses contain duplicate {field}"
            )


def canonical_boundary_result(
    manifest: Mapping[str, Any],
    screening: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the exact production boundary result for authenticated inputs."""

    if not isinstance(manifest, Mapping):
        raise ValueError("canonical boundary manifest must be an object")
    if not isinstance(screening, Mapping):
        raise ValueError("canonical screening result must be an object")
    if (
        not isinstance(responses, Sequence)
        or isinstance(responses, (str, bytes))
        or not responses
        or not all(isinstance(item, Mapping) for item in responses)
    ):
        raise ValueError(
            "canonical boundary responses must be a non-empty array of objects"
        )
    _require_authenticated_inputs(manifest, screening, responses)
    clear_finalists, candidates, clear_non_finalists = _groups(screening)
    model = manifest["model"]
    capacity = manifest["synthetic_replicate_capacity"]
    boundary_plan = screening["boundary_plan"]
    audience_lock = manifest["audience_lock"]
    if manifest.get("method") != "partial_exposure_maxdiff":
        raise ValueError(
            "canonical boundary aggregation requires partial_exposure_maxdiff"
        )
    if model.get("pairwise_model") != "davidson":
        raise ValueError("canonical boundary model must be davidson")
    if (
        model.get("clear_finalist_threshold") != 0.90
        or model.get("clear_non_finalist_threshold") != 0.10
        or model.get("bootstrap_count") != 2000
    ):
        raise ValueError("canonical boundary model policy is invalid")
    requested_top_k = manifest["requested_shortlist_size"]
    slots = requested_top_k - len(clear_finalists)
    if slots < 0:
        raise ValueError(
            "frozen clear finalists exceed the requested shortlist"
        )
    result = resolve_boundary(
        responses,
        slots,
        PairwiseConfig(
            tie_parameter=model.get("pairwise_tie_parameter"),
            penalty_lambda=model.get("pairwise_penalty_lambda"),
            optimizer_tolerance=model.get("pairwise_optimizer_tolerance"),
            bootstrap_count=model.get("bootstrap_count"),
            successful_fit_floor=0.95,
            seed=_seed(manifest),
        ),
        candidate_ids=candidates,
        segment_weights=audience_lock.get("segment_weights"),
        predeclared_pair_assignments=boundary_plan.get(
            "predeclared_pair_assignments"
        ),
        boundary_jobs_per_wave=capacity.get("boundary_jobs_per_wave"),
        boundary_waves_max=capacity.get("boundary_waves_max"),
        boundary_reserved=capacity.get("boundary_reserved"),
        available_boundary_reserve=boundary_plan.get(
            "available_boundary_reserve"
        ),
        finalist_reserved=capacity.get("finalist_reserved"),
        clear_finalist_ids=clear_finalists,
        clear_non_finalist_ids=clear_non_finalists,
    )
    payload = {"study_id": manifest["study_id"], **result.to_dict()}
    if payload["status"] == "invalid":
        raise ValueError(
            "canonical boundary aggregation is invalid: "
            + ", ".join(payload["status_reasons"])
        )
    audience_package = manifest.get("audience_package")
    if (
        isinstance(audience_package, Mapping)
        and isinstance(audience_lock, Mapping)
    ):
        payload["audience_package"] = dict(audience_package)
        payload["audience_lock"] = dict(audience_lock)
    return payload
