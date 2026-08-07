"""Deterministic assignment-core to enriched progressive-dispatch adapter."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audience_library import audience_package_binding, load_audience_resolution
from .audience_allocation import evaluate_allocation_subset
from .contracts import (
    canonical_v3_dispatch_cores,
    validate_boundary_profile_attachments,
    validate_v3_dispatch_authority,
    validate_v3_jobs_envelope,
)
from .finalists import validate_roster_approval
from .planning import load_reusable_v3_audience_resolution
from .responses import validate_job, validate_response_job_bindings


class AllocationDecisionRequired(ValueError):
    """Signal a canonical subset decision without authorizing worker jobs."""

    def __init__(self, decision: Mapping[str, Any]):
        super().__init__(
            "v3 audience allocation requires an explicit directional decision"
        )
        self.decision = dict(decision)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    return value


def _assignment_cores(plan: Mapping[str, Any]) -> Sequence[Any]:
    assignment = plan.get("assignment")
    if isinstance(assignment, Mapping):
        jobs = assignment.get("synthetic_replicate_jobs")
    else:
        jobs = plan.get("synthetic_replicate_jobs")
    jobs = _require_sequence(jobs, "synthetic_replicate_jobs")
    if not jobs:
        raise ValueError("synthetic_replicate_jobs must not be empty")
    return jobs


def _positive_slot_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _authorized_cores(
    authority: Mapping[str, Any],
    context: Mapping[str, Any],
    record_type: str,
    segment_ids: Sequence[str],
    manifest: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Materialize only jobs frozen by planner, boundary plan, or approval."""

    if record_type == "screening_response":
        cores = [dict(item) for item in _assignment_cores(authority)]
        capacity = _require_mapping(
            authority.get("synthetic_replicate_capacity"),
            "synthetic_replicate_capacity",
        )
        reserve = _positive_slot_count(
            capacity.get("screening_planned"), "screening reserve"
        )
        if len(cores) > reserve:
            raise ValueError("screening jobs exceed the frozen screening reserve")
        return cores

    if record_type == "boundary_response":
        boundary_plan = _require_mapping(
            authority.get("boundary_plan"), "boundary_plan"
        )
        raw_authorized = _require_sequence(
            boundary_plan.get("predeclared_pair_assignments"),
            "boundary_plan.predeclared_pair_assignments",
        )
        authorized: dict[tuple[str, int], Mapping[str, Any]] = {}
        for index, raw in enumerate(raw_authorized):
            item = _require_mapping(
                raw, f"boundary_plan.predeclared_pair_assignments[{index}]"
            )
            pair_id = item.get("pair_assignment_id")
            wave = item.get("wave", item.get("boundary_wave"))
            if not isinstance(pair_id, str) or not isinstance(wave, int):
                raise ValueError("authorized boundary pair IDs and waves are required")
            authorized[(pair_id, wave)] = item
        requested = context.get("requested_boundary_assignments")
        selected: list[Mapping[str, Any]]
        if requested is not None:
            selected = []
            for index, raw in enumerate(_require_sequence(requested, "requested_boundary_assignments")):
                item = _require_mapping(raw, f"requested_boundary_assignments[{index}]")
                pair_id = item.get("pair_assignment_id")
                wave = item.get("boundary_wave", item.get("wave"))
                frozen = authorized.get((str(pair_id), wave))
                if frozen is None:
                    raise ValueError("unauthorized boundary pair or unauthorized boundary wave")
                if list(item.get("variation_ids", ())) != list(frozen.get("variation_ids", ())):
                    raise ValueError("unauthorized boundary pair creative IDs")
                selected.append(frozen)
        else:
            waves = context.get("boundary_waves")
            allowed_waves = (
                set(_require_sequence(waves, "boundary_waves"))
                if waves is not None
                else {wave for _, wave in authorized}
            )
            unknown = allowed_waves - {wave for _, wave in authorized}
            if unknown:
                raise ValueError("unauthorized boundary wave")
            selected = [
                item
                for (pair_id, wave), item in sorted(authorized.items())
                if wave in allowed_waves
            ]
        reserve = boundary_plan.get("available_boundary_reserve")
        if isinstance(reserve, bool) or not isinstance(reserve, int) or len(selected) > reserve:
            raise ValueError("boundary jobs exceed the frozen boundary reserve")
        if manifest is None:
            raise ValueError("boundary dispatch requires the bound manifest")
        if authority.get("study_id") != manifest.get("study_id"):
            raise ValueError("boundary authority study_id must exactly match the manifest")
        if authority.get("method") != manifest.get("method"):
            raise ValueError("boundary authority method must exactly match the manifest")
        capacity = _require_mapping(
            manifest.get("synthetic_replicate_capacity"),
            "manifest.synthetic_replicate_capacity",
        )
        manifest_reserve = capacity.get("boundary_reserved")
        if (
            isinstance(manifest_reserve, bool)
            or not isinstance(manifest_reserve, int)
            or manifest_reserve < 0
            or reserve > manifest_reserve
            or len(selected) > manifest_reserve
        ):
            raise ValueError(
                "boundary authority reserve cannot exceed the manifest boundary reserve"
            )
        cores = []
        for index, item in enumerate(selected):
            variation_ids = list(item["variation_ids"])
            cores.append(
                {
                    "synthetic_replicate_id": item["pair_assignment_id"],
                    "segment_id": segment_ids[index % len(segment_ids)],
                    "variation_ids": variation_ids,
                    "shown_order": variation_ids if index % 2 == 0 else list(reversed(variation_ids)),
                    "assigned_variation_ids": variation_ids,
                    "pair_assignment_id": item["pair_assignment_id"],
                    "boundary_wave": item.get("wave", item.get("boundary_wave")),
                }
            )
        return cores

    if manifest is None:
        raise ValueError("finalist dispatch requires the bound manifest")
    approved_ids, _decision = validate_roster_approval(manifest, authority)
    capacity = _require_mapping(
        manifest.get("synthetic_replicate_capacity"),
        "manifest.synthetic_replicate_capacity",
    )
    reserve = _positive_slot_count(
        capacity.get("finalist_reserved"), "finalist reserve"
    )
    requested = _positive_slot_count(
        context.get("requested_job_slots", reserve), "requested_job_slots"
    )
    if requested > reserve:
        raise ValueError("finalist jobs exceed the frozen finalist reserve")
    return [
        {
            "synthetic_replicate_id": f"finalist-{index + 1:04d}",
            "segment_id": segment_ids[index % len(segment_ids)],
            "variation_ids": approved_ids,
            "shown_order": (
                approved_ids if index % 2 == 0 else list(reversed(approved_ids))
            ),
        }
        for index in range(requested)
    ]


def _shape_worker_jobs(
    cores: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    *,
    study_id: str,
    method: str,
    record_type: str,
) -> dict[str, Any]:
    """Build the version-neutral worker job and prompt shape exactly once."""

    if len(cores) != len(bindings):
        raise ValueError("worker job cores and profile bindings must align")
    creative_prompts = _require_mapping(
        context.get("creative_prompts"), "creative_prompts"
    )
    comparison_prompts = _require_mapping(
        context.get("comparison_prompts"), "comparison_prompts"
    )
    comparison_prompt = comparison_prompts.get(method)
    if not isinstance(comparison_prompt, str) or not comparison_prompt.strip():
        raise ValueError(f"comparison_prompts.{method} is required")
    reaction_protocol = context.get(
        "reaction_protocol", "progressive_reveal"
    )
    worker_context_isolation = context.get(
        "worker_context_isolation", "isolated"
    )
    enriched: list[dict[str, Any]] = []
    for index, (raw_core, raw_binding) in enumerate(
        zip(cores, bindings, strict=True)
    ):
        core = _require_mapping(
            raw_core, f"synthetic_replicate_jobs[{index}]"
        )
        binding = _require_mapping(
            raw_binding, f"profile_bindings[{index}]"
        )
        profile = _require_mapping(
            binding.get("profile"), f"profile_bindings[{index}].profile"
        )
        replicate_id = binding.get("replicate_id")
        if not isinstance(replicate_id, str) or not replicate_id.strip():
            raise ValueError(
                f"synthetic_replicate_jobs[{index}].synthetic_replicate_id is required"
            )
        segment_id = binding.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError(
                f"profile_bindings[{index}].segment_id is required"
            )
        context_stratum_id = binding.get("context_stratum_id")
        if context_stratum_id is not None and (
            not isinstance(context_stratum_id, str)
            or not context_stratum_id.strip()
        ):
            raise ValueError(
                f"profile_bindings[{index}].context_stratum_id is invalid"
            )
        variation_ids = list(
            _require_sequence(
                core.get("variation_ids"),
                f"synthetic_replicate_jobs[{index}].variation_ids",
            )
        )
        shown_order = list(
            _require_sequence(
                core.get("shown_order"),
                f"synthetic_replicate_jobs[{index}].shown_order",
            )
        )
        missing_prompts = [
            creative_id
            for creative_id in shown_order
            if not isinstance(creative_prompts.get(creative_id), str)
            or not creative_prompts.get(creative_id).strip()
        ]
        if missing_prompts:
            raise ValueError(
                "creative_prompts are missing assigned IDs: "
                + ",".join(missing_prompts)
            )
        blind_labels = {
            creative_id: chr(ord("A") + position)
            for position, creative_id in enumerate(shown_order)
        }
        job: dict[str, Any] = {
            "study_id": study_id,
            "response_id": f"{record_type}-{replicate_id}",
            "record_type": record_type,
            "method": method,
            "synthetic_replicate_id": replicate_id,
            "dispatch_id": f"dispatch-{record_type}-{replicate_id}",
            "persona_archetype_id": profile.get("persona_archetype_id"),
            "segment_id": segment_id,
            "profile_snapshot": profile.get("profile_snapshot"),
            "context_attribute_provenance": profile.get(
                "context_attribute_provenance"
            ),
            "worker_context_isolation": worker_context_isolation,
            "human_sample_independence": False,
            "variation_ids": variation_ids,
            "blind_labels": blind_labels,
            "shown_order": shown_order,
            "reaction_protocol": reaction_protocol,
            "reaction_prompts": [
                (
                    f"Blind creative {blind_labels[creative_id]} only. "
                    f"{creative_prompts[creative_id]}"
                )
                for creative_id in shown_order
            ],
            "comparison_prompt": comparison_prompt,
            "grounded_profile_id": profile.get("grounded_profile_id"),
        }
        if context_stratum_id is not None:
            job["context_stratum_id"] = context_stratum_id
        extra_fields = binding.get("extra_job_fields", {})
        if not isinstance(extra_fields, Mapping):
            raise ValueError(
                f"profile_bindings[{index}].extra_job_fields must be an object"
            )
        job.update(extra_fields)
        for passthrough in (
            "inclusion_probability",
            "context_stratum_id",
            "pair_assignment_id",
            "boundary_wave",
            "assigned_variation_ids",
        ):
            if passthrough in core:
                job[passthrough] = core[passthrough]
        errors = validate_job(job)
        if errors:
            raise ValueError(
                f"enriched synthetic_replicate_jobs[{index}] is invalid: "
                + "; ".join(errors)
            )
        enriched.append(job)
    return {
        "study_id": study_id,
        "method": method,
        "record_type": record_type,
        "synthetic_replicate_jobs": enriched,
    }


def _v3_dispatch_authority(
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    plan_package = plan.get("audience_package")
    manifest_package = (
        manifest.get("audience_package") if manifest is not None else None
    )
    plan_is_v3 = (
        isinstance(plan_package, Mapping)
        and plan_package.get("schema_version") == "audience-panel-package-v3"
    )
    manifest_is_v3 = (
        isinstance(manifest_package, Mapping)
        and manifest_package.get("schema_version")
        == "audience-panel-package-v3"
    )
    if not plan_is_v3 and not manifest_is_v3:
        return None
    if manifest is not None and plan_package is not None:
        if plan_is_v3 != manifest_is_v3:
            raise ValueError("mixed v2 and v3 dispatch bindings are forbidden")
        if plan_package != manifest_package:
            raise ValueError(
                "plan and manifest v3 audience_package bindings do not match"
            )
    authority = plan if plan_is_v3 else manifest
    if authority is None:
        raise ValueError("v3 dispatch authority is missing")
    validated = validate_v3_dispatch_authority(authority)
    if plan_is_v3 and manifest_is_v3:
        for field in (
            "study_id",
            "method",
            "synthetic_replicate_capacity",
            "assignment",
            "audience_lock",
            "grounded_context_profiles",
            "audience_profile_rosters",
        ):
            if plan.get(field) != manifest.get(field):
                raise ValueError(
                    f"plan and manifest v3 {field} bindings do not match"
                )
    return validated


def _v3_stage_roster(
    authority: Mapping[str, Any],
    record_type: str,
) -> Mapping[str, Any]:
    stage_key = {
        "screening_response": "screening",
        "boundary_response": "boundary_reserve",
        "finalist_response": "finalist_reserve",
    }[record_type]
    rosters = _require_mapping(
        authority.get("audience_profile_rosters"),
        "audience_profile_rosters",
    )
    roster = _require_mapping(
        rosters.get(stage_key),
        f"audience_profile_rosters.{stage_key}",
    )
    if (
        roster.get("schema_version")
        == "audience-profile-allocation-not-applicable-v1"
    ):
        raise ValueError(
            "complete_exposure boundary allocation is not applicable"
        )
    return roster


def _boundary_wave(slot_id: str) -> int:
    parts = slot_id.split("-")
    if (
        len(parts) != 5
        or parts[0] != "boundary"
        or parts[1] != "wave"
        or parts[3] != "job"
        or not parts[2].isdigit()
        or not parts[4].isdigit()
    ):
        raise ValueError("v3 boundary roster contains an invalid frozen slot ID")
    return int(parts[2])


def _validate_boundary_continuation_evidence(
    *,
    manifest: Mapping[str, Any],
    screening_result: Mapping[str, Any],
    prior_jobs: Sequence[Mapping[str, Any]],
    cumulative_jobs: Sequence[Mapping[str, Any]],
    prior_responses: Sequence[Mapping[str, Any]],
    boundary_result: Mapping[str, Any],
    expected_prior_slot_ids: list[str],
    newly_authorized_slot_ids: list[str],
    previous_wave: int,
) -> None:
    response_errors = validate_response_job_bindings(
        cumulative_jobs,
        prior_responses,
    )
    if response_errors:
        raise ValueError(
            "prior boundary responses do not authenticate the cumulative "
            "completed-wave jobs: "
            + "; ".join(response_errors)
        )
    if [
        response.get("synthetic_replicate_id")
        for response in prior_responses
    ] != [
        job.get("synthetic_replicate_id")
        for job in cumulative_jobs
    ]:
        raise ValueError(
            "prior boundary responses must follow the exact cumulative "
            "completed-wave job order"
        )
    if [job.get("audience_slot_id") for job in prior_jobs] != (
        expected_prior_slot_ids
    ):
        raise ValueError(
            "prior jobs must exactly cover the immediately preceding frozen wave"
        )
    cumulative_by_slot = {
        job.get("audience_slot_id"): job
        for job in cumulative_jobs
    }
    if list(prior_jobs) != [
        cumulative_by_slot.get(slot_id)
        for slot_id in expected_prior_slot_ids
    ]:
        raise ValueError(
            "prior jobs must equal the exact canonical immediately preceding "
            "frozen wave"
        )
    from .boundary_aggregation import (  # noqa: PLC0415
        canonical_boundary_result,
    )

    canonical_result = canonical_boundary_result(
        manifest,
        screening_result,
        prior_responses,
    )
    boundary_result_bytes = (
        json.dumps(
            boundary_result,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    canonical_result_bytes = (
        json.dumps(
            canonical_result,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if (
        dict(boundary_result) != canonical_result
        or boundary_result_bytes != canonical_result_bytes
    ):
        raise ValueError(
            "prior boundary result must equal the exact canonical aggregator "
            "result for the authenticated manifest, screening authority, "
            "cumulative jobs, and cumulative responses"
        )
    if canonical_result.get("status") != "unresolved":
        raise ValueError(
            "prior boundary result must be unresolved before continuation"
        )
    audit = canonical_result.get("decision_audit")
    if not isinstance(audit, Mapping):
        raise ValueError("prior boundary result decision_audit is required")
    waves = audit.get("waves")
    completed_waves = [
        wave
        for wave in waves
        if isinstance(wave, Mapping)
        and wave.get("completed") is True
    ] if isinstance(waves, list) else []
    completed = completed_waves[-1] if completed_waves else None
    if not isinstance(completed, Mapping) or (
        completed.get("wave") != previous_wave
    ):
        raise ValueError(
            "canonical boundary result does not contain the completed prior wave"
        )
    if audit.get("next_wave_job_ids") != newly_authorized_slot_ids:
        raise ValueError(
            "prior boundary result next_wave_job_ids must exactly authorize the new frozen wave"
        )


def _v3_dispatch_selection(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None,
    authority: Mapping[str, Any],
    allow_directional_allocation: bool,
    prior_jobs_envelope: Mapping[str, Any] | None,
    prior_responses: Sequence[Mapping[str, Any]] | None,
    prior_boundary_result: Mapping[str, Any] | None,
    audience_resolution: Path | str | None,
) -> tuple[
    Mapping[str, Any] | None,
    dict[str, Any] | None,
    list[str],
]:
    record_type = context.get("record_type", "screening_response")
    if record_type not in {
        "screening_response",
        "boundary_response",
        "finalist_response",
    }:
        raise ValueError("dispatch context record_type is unsupported")
    prior_evidence_supplied = any(
        value is not None
        for value in (
            prior_jobs_envelope,
            prior_responses,
            prior_boundary_result,
        )
    )
    if prior_evidence_supplied and record_type != "boundary_response":
        raise ValueError(
            "prior v3 boundary evidence is valid only for boundary continuation"
        )
    rosters = _require_mapping(
        authority.get("audience_profile_rosters"),
        "audience_profile_rosters",
    )
    stage_key = {
        "screening_response": "screening",
        "boundary_response": "boundary_reserve",
        "finalist_response": "finalist_reserve",
    }[str(record_type)]
    roster = _require_mapping(
        rosters.get(stage_key),
        f"audience_profile_rosters.{stage_key}",
    )
    if (
        roster.get("schema_version")
        == "audience-profile-allocation-not-applicable-v1"
    ):
        if record_type != "boundary_response":
            raise ValueError(
                "only a complete-exposure boundary may be not applicable"
            )
        if prior_evidence_supplied:
            raise ValueError(
                "complete-exposure boundary cannot carry prior allocation authority"
            )
        return None, None, []
    if record_type == "boundary_response":
        validate_boundary_profile_attachments(
            _require_mapping(plan.get("boundary_plan"), "boundary_plan"),
            roster,
        )

    profiles = _require_sequence(
        authority.get("grounded_context_profiles"),
        "grounded_context_profiles",
    )
    cores = _authorized_cores(
        plan,
        context,
        str(record_type),
        sorted(
            {
                str(profile["reported_segment_id"])
                for profile in profiles
                if isinstance(profile, Mapping)
            }
        ),
        manifest,
    )
    newly_authorized = [
        str(core.get("synthetic_replicate_id"))
        for core in cores
        if isinstance(core, Mapping)
    ]
    if len(newly_authorized) != len(cores) or len(newly_authorized) != len(
        set(newly_authorized)
    ):
        raise ValueError("v3 dispatch slot selection must be unique")
    frozen_ids = [
        str(assignment.get("slot_id"))
        for assignment in _require_sequence(
            roster.get("assignments"), "v3 roster assignments"
        )
        if isinstance(assignment, Mapping)
    ]
    if not newly_authorized or any(
        slot_id not in frozen_ids for slot_id in newly_authorized
    ):
        raise ValueError(
            "v3 dispatch selected a slot absent from the frozen roster"
        )

    if record_type == "screening_response":
        if newly_authorized != frozen_ids:
            raise ValueError(
                "v3 screening dispatch must use the complete frozen roster"
            )
        claim_slot_ids = frozen_ids
    elif record_type == "finalist_response":
        if newly_authorized != frozen_ids[: len(newly_authorized)]:
            raise ValueError(
                "v3 finalist dispatch must use a deterministic frozen prefix"
            )
        requested_finalist_ids = context.get(
            "requested_finalist_slot_ids"
        )
        if requested_finalist_ids is not None:
            requested_finalist_ids = list(
                _require_sequence(
                    requested_finalist_ids,
                    "requested_finalist_slot_ids",
                )
            )
            if requested_finalist_ids != newly_authorized:
                raise ValueError(
                    "v3 finalist slot selection must equal the deterministic frozen prefix"
                )
        claim_slot_ids = newly_authorized
    else:
        frozen_by_wave: dict[int, list[str]] = defaultdict(list)
        for slot_id in frozen_ids:
            frozen_by_wave[_boundary_wave(slot_id)].append(slot_id)
        selected_waves = sorted(
            {_boundary_wave(slot_id) for slot_id in newly_authorized}
        )
        expected_new = [
            slot_id
            for wave in selected_waves
            for slot_id in frozen_by_wave.get(wave, [])
        ]
        if newly_authorized != expected_new:
            raise ValueError(
                "v3 boundary dispatch must authorize complete frozen waves in order"
            )
        if len(selected_waves) != 1:
            raise ValueError(
                "v3 boundary dispatch must authorize exactly one new frozen wave"
            )
        current_wave = selected_waves[0]
        claim_slot_ids = [
            slot_id
            for slot_id in frozen_ids
            if _boundary_wave(slot_id) <= current_wave
        ]
        current_only_later_wave = current_wave > 1
        if current_only_later_wave:
            if (
                prior_jobs_envelope is None
                or prior_responses is None
                or prior_boundary_result is None
            ):
                raise ValueError(
                    "a current-only later boundary wave requires prior jobs, responses, and boundary result"
                )
            if not isinstance(audience_resolution, (str, Path)):
                raise ValueError(
                    "prior boundary authority requires the canonical v3 audience resolution"
                )
            validated_prior = validate_v3_jobs_envelope(
                prior_jobs_envelope,
                allocation_plan=roster,
                authority=authority,
                audience_resolution=audience_resolution,
                dispatch_authority=plan,
            )
            expected_prior_ids = [
                slot_id
                for slot_id in frozen_ids
                if _boundary_wave(slot_id) < current_wave
            ]
            prior_selected_ids = validated_prior[
                "audience_allocation_subset"
            ]["selected_slot_ids"]
            if prior_selected_ids != expected_prior_ids:
                raise ValueError(
                    "prior v3 jobs envelope must authorize the complete cumulative prefix through the preceding wave"
                )
            if claim_slot_ids != expected_prior_ids + newly_authorized:
                raise ValueError(
                    "current boundary wave must exactly extend the prior cumulative prefix"
                )
            immediately_prior_ids = [
                slot_id
                for slot_id in frozen_ids
                if _boundary_wave(slot_id) == current_wave - 1
            ]
            prior_jobs = validated_prior["synthetic_replicate_jobs"]
            cumulative_prior_payload = _enrich_v3_assignment_jobs(
                plan,
                context,
                manifest=manifest,
                audience_resolution=audience_resolution,
                authority=authority,
                selected_slot_ids=expected_prior_ids,
            )
            cumulative_jobs = cumulative_prior_payload[
                "synthetic_replicate_jobs"
            ]
            if not isinstance(prior_responses, Sequence) or isinstance(
                prior_responses, (str, bytes)
            ) or not all(
                isinstance(response, Mapping)
                for response in prior_responses
            ):
                raise ValueError(
                    "prior boundary responses must be an array of objects"
                )
            _validate_boundary_continuation_evidence(
                manifest=_require_mapping(
                    manifest,
                    "manifest",
                ),
                screening_result=plan,
                prior_jobs=prior_jobs,
                cumulative_jobs=cumulative_jobs,
                prior_responses=prior_responses,
                boundary_result=prior_boundary_result,
                expected_prior_slot_ids=immediately_prior_ids,
                newly_authorized_slot_ids=newly_authorized,
                previous_wave=current_wave - 1,
            )
        elif prior_evidence_supplied:
            raise ValueError(
                "prior v3 boundary evidence is accepted only for a current-only later wave"
            )

    subset = evaluate_allocation_subset(
        roster,
        selected_slot_ids=claim_slot_ids,
        allow_directional_allocation=allow_directional_allocation,
    )
    return roster, subset, newly_authorized


def _enrich_v3_assignment_jobs(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None,
    audience_resolution: Path | str | None,
    authority: Mapping[str, Any],
    selected_slot_ids: list[str],
) -> dict[str, Any]:
    if audience_resolution is None or not isinstance(
        audience_resolution, (str, Path)
    ):
        raise ValueError(
            "v3 dispatch requires the canonical audience/resolution.json path"
        )
    if context.get("profiles") is not None:
        raise ValueError(
            "v3 dispatch forbids free-form profile selection"
        )
    envelope, envelope_bytes = load_reusable_v3_audience_resolution(
        audience_resolution
    )
    rosters = _require_mapping(
        authority.get("audience_profile_rosters"),
        "audience_profile_rosters",
    )
    expected_envelope_hash = "sha256:" + hashlib.sha256(
        envelope_bytes
    ).hexdigest()
    if rosters.get("envelope_sha256") != expected_envelope_hash:
        raise ValueError(
            "v3 profile rosters do not bind the immutable audience envelope"
        )
    for field in (
        "audience_package",
        "audience_lock",
        "grounded_context_profiles",
    ):
        if authority.get(field) != envelope.get(field):
            raise ValueError(
                f"v3 dispatch authority {field} does not match the resolved audience"
            )

    study_id = authority.get("study_id")
    method = authority.get("method")
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("v3 dispatch authority study_id is required")
    if method not in {"complete_exposure", "partial_exposure_maxdiff"}:
        raise ValueError("v3 dispatch authority method is unsupported")
    if context.get("study_id") not in {None, study_id}:
        raise ValueError("dispatch context study_id must match the v3 plan")
    record_type = context.get("record_type", "screening_response")
    if record_type not in {
        "screening_response",
        "boundary_response",
        "finalist_response",
    }:
        raise ValueError("dispatch context record_type is unsupported")
    roster = _v3_stage_roster(authority, str(record_type))
    assignments = _require_sequence(
        roster.get("assignments"),
        "v3 stage roster assignments",
    )
    assignment_by_slot: dict[str, Mapping[str, Any]] = {}
    for index, raw_assignment in enumerate(assignments):
        assignment = _require_mapping(
            raw_assignment, f"v3 roster assignments[{index}]"
        )
        slot_id = assignment.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id.strip():
            raise ValueError("v3 roster slot IDs are required")
        if slot_id in assignment_by_slot:
            raise ValueError("v3 roster slot IDs must be unique")
        assignment_by_slot[slot_id] = assignment

    canonical_profiles = _require_sequence(
        envelope.get("grounded_context_profiles"),
        "v3 grounded_context_profiles",
    )
    profile_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_profile in enumerate(canonical_profiles):
        profile = _require_mapping(
            raw_profile, f"v3 grounded_context_profiles[{index}]"
        )
        profile_id = profile.get("grounded_profile_id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("v3 grounded profile IDs are required")
        if profile_id in profile_by_id:
            raise ValueError("v3 grounded profile IDs must be unique")
        profile_by_id[profile_id] = profile

    cores = canonical_v3_dispatch_cores(
        authority=authority,
        dispatch_authority=plan,
        allocation_plan=roster,
        selected_slot_ids=selected_slot_ids,
    )
    bindings: list[dict[str, Any]] = []
    for index, raw_core in enumerate(cores):
        core = _require_mapping(
            raw_core, f"synthetic_replicate_jobs[{index}]"
        )
        slot_id = core.get("synthetic_replicate_id")
        assignment = assignment_by_slot.get(str(slot_id))
        if assignment is None:
            raise ValueError(
                f"synthetic_replicate_jobs[{index}] references an unknown frozen audience slot"
            )
        for field, core_field in (
            ("slot_id", "audience_slot_id"),
            ("grounded_profile_id", "grounded_profile_id"),
            ("profile_snapshot_sha256", "profile_snapshot_sha256"),
        ):
            if (
                core_field in core
                and core.get(core_field) != assignment.get(field)
            ):
                raise ValueError(
                    f"synthetic_replicate_jobs[{index}] changed frozen {field}"
                )
        profile = profile_by_id.get(str(assignment.get("grounded_profile_id")))
        if profile is None:
            raise ValueError("v3 roster references an unknown grounded profile")
        if (
            profile.get("reported_segment_id")
            != assignment.get("reported_segment_id")
            or profile.get("segment_id")
            != assignment.get("reported_segment_id")
        ):
            raise ValueError(
                "v3 frozen profile and roster reported segment do not match"
            )
        if (
            profile.get("structural_group_id")
            != assignment.get("structural_group_id")
        ):
            raise ValueError(
                "v3 frozen profile and roster structural group do not match"
            )
        if (
            profile.get("profile_snapshot_sha256")
            != assignment.get("profile_snapshot_sha256")
        ):
            raise ValueError(
                "v3 frozen profile and roster snapshot hash do not match"
            )
        if (
            record_type == "screening_response"
            and core.get("segment_id")
            != assignment.get("reported_segment_id")
        ):
            raise ValueError(
                f"synthetic_replicate_jobs[{index}] changed frozen reported segment"
            )
        context_stratum_id = profile.get("context_stratum_id")
        if (
            "context_stratum_id" in core
            and core.get("context_stratum_id") != context_stratum_id
        ):
            raise ValueError(
                f"synthetic_replicate_jobs[{index}] changed frozen context stratum"
            )
        bindings.append(
            {
                "profile": profile,
                "replicate_id": slot_id,
                "segment_id": assignment["reported_segment_id"],
                "context_stratum_id": context_stratum_id,
                "extra_job_fields": {
                    "audience_slot_id": assignment["slot_id"],
                    "grounded_profile_id": assignment[
                        "grounded_profile_id"
                    ],
                    "profile_snapshot_sha256": assignment[
                        "profile_snapshot_sha256"
                    ],
                },
            }
        )
    return _shape_worker_jobs(
        cores,
        bindings,
        context,
        study_id=study_id,
        method=method,
        record_type=str(record_type),
    )


def enrich_assignment_jobs(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None = None,
    audience_resolution: Path | str | None = None,
    allow_directional_allocation: bool = False,
    prior_jobs_envelope: Mapping[str, Any] | None = None,
    prior_responses: Sequence[Mapping[str, Any]] | None = None,
    prior_boundary_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich deterministic assignment cores and validate the Task 4 boundary."""

    v3_authority = _v3_dispatch_authority(plan, manifest)
    if v3_authority is not None:
        if context.get("profiles") is not None:
            raise ValueError(
                "v3 dispatch forbids free-form profile selection"
            )
        allocation_plan, subset, newly_authorized = _v3_dispatch_selection(
            plan,
            context,
            manifest=manifest,
            authority=v3_authority,
            allow_directional_allocation=allow_directional_allocation,
            prior_jobs_envelope=prior_jobs_envelope,
            prior_responses=prior_responses,
            prior_boundary_result=prior_boundary_result,
            audience_resolution=audience_resolution,
        )
        if allocation_plan is None:
            return {
                "study_id": v3_authority["study_id"],
                "method": v3_authority["method"],
                "record_type": context.get(
                    "record_type", "screening_response"
                ),
                "synthetic_replicate_jobs": [],
            }
        if subset is None:
            raise AssertionError("applicable v3 allocation omitted its subset")
        if subset["claim_effect"] == "requires_user_decision":
            raise AllocationDecisionRequired(subset)
        payload = _enrich_v3_assignment_jobs(
            plan,
            context,
            manifest=manifest,
            audience_resolution=audience_resolution,
            authority=v3_authority,
            selected_slot_ids=newly_authorized,
        )
        payload.update(
            {
                "audience_allocation_subset": subset,
                "audience_run_claim": subset["claim_effect"],
                "audience_dispatch": {
                    "stage": allocation_plan["stage"],
                    "newly_authorized_slot_ids": newly_authorized,
                },
            }
        )
        return validate_v3_jobs_envelope(
            payload,
            allocation_plan=allocation_plan,
            authority=v3_authority,
            audience_resolution=audience_resolution,
            dispatch_authority=plan,
        )
    if any(
        value is not None
        for value in (
            prior_jobs_envelope,
            prior_responses,
            prior_boundary_result,
        )
    ):
        raise ValueError(
            "a prior jobs envelope is available only for v3 boundary dispatch"
        )
    if allow_directional_allocation:
        raise ValueError(
            "--allow-directional-allocation is available only for v3 dispatch"
        )

    plan_binding = plan.get("audience_package")
    plan_lock = plan.get("audience_lock")
    manifest_binding = manifest.get("audience_package") if manifest is not None else None
    manifest_lock = manifest.get("audience_lock") if manifest is not None else None
    if manifest is not None and plan_binding is not None:
        if manifest_binding is None or manifest_lock is None:
            raise ValueError(
                "a manifest supplied with a v2 plan must contain audience_package and audience_lock"
            )
        if manifest_binding != plan_binding or manifest_lock != plan_lock:
            raise ValueError(
                "manifest v2 audience_package and audience_lock must exactly match the plan"
            )
        if manifest.get("study_id") != plan.get("study_id"):
            raise ValueError("manifest study_id must exactly match the v2 plan study_id")
        if manifest.get("method") != plan.get("method"):
            raise ValueError("manifest method must exactly match the v2 plan method")
    elif manifest_binding is not None:
        raise ValueError(
            "a v2 manifest requires a plan with the same audience_package and audience_lock"
        )

    study_id = plan.get("study_id")
    method = plan.get("method")
    if manifest is not None:
        study_id = manifest.get("study_id")
        method = manifest.get("method")
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("plan study_id must be a non-empty string")
    if method not in {"complete_exposure", "partial_exposure_maxdiff"}:
        raise ValueError("plan method is unsupported")
    if context.get("study_id") not in {None, study_id}:
        raise ValueError("dispatch context study_id must match the plan")
    record_type = context.get("record_type", "screening_response")
    if record_type not in {
        "screening_response",
        "boundary_response",
        "finalist_response",
    }:
        raise ValueError("dispatch context record_type is unsupported")
    if plan_binding is not None and manifest_binding is not None and plan_binding != manifest_binding:
        raise ValueError("plan and manifest audience_package bindings do not match")
    v2_binding = plan_binding if plan_binding is not None else manifest_binding
    raw_assignment = plan.get("assignment")
    raw_jobs = (
        raw_assignment.get("synthetic_replicate_jobs", [])
        if isinstance(raw_assignment, Mapping)
        else []
    )
    v2_only_without_binding = (
        "grounded_context_profiles" in plan
        or (manifest is not None and "grounded_context_profiles" in manifest)
        or any(
            isinstance(job, Mapping) and "grounded_profile_id" in job
            for job in raw_jobs
        )
    )
    if v2_binding is None and v2_only_without_binding:
        raise ValueError("v2 audience fields require audience_package")
    if v2_binding is not None and (
        plan_lock is not None and manifest_lock is not None and plan_lock != manifest_lock
    ):
        raise ValueError("mixed v2 and legacy audience locks are forbidden")
    if v2_binding is None:
        if audience_resolution is not None:
            raise ValueError(
                "legacy v1 dispatch cannot attach a v2 audience resolution without a manifest binding"
            )
        legacy_profiles = context.get("profiles", [])
        legacy_segment_ids = sorted({
            profile.get("segment_id")
            for profile in legacy_profiles
            if isinstance(profile, Mapping)
            and isinstance(profile.get("segment_id"), str)
            and profile.get("segment_id").strip()
        }) if isinstance(legacy_profiles, Sequence) and not isinstance(
            legacy_profiles, (str, bytes)
        ) else []
        _authorized_cores(
            plan, context, str(record_type), legacy_segment_ids, manifest
        )
        raise ValueError(
            "legacy v1 or unbound audience inputs are read-only and cannot dispatch new jobs"
        )
    if v2_binding is not None:
        if audience_resolution is None:
            raise ValueError(
                "v2 dispatch requires the ready run-local audience resolution"
            )
        if not isinstance(audience_resolution, (str, Path)):
            raise ValueError(
                "v2 dispatch requires the canonical audience/resolution.json path"
            )
        resolution = load_audience_resolution(audience_resolution)
        bound_audience_lock = (
            plan_lock
            if plan_binding is not None
            else manifest_lock
        )
        if bound_audience_lock != resolution["audience_lock"]:
            raise ValueError("manifest audience_lock does not match the resolved audience")
        resolution_path = Path(audience_resolution)
        expected_binding = audience_package_binding(
            resolution_path.parent.parent, resolution
        )
        if not isinstance(v2_binding, Mapping) or dict(v2_binding) != expected_binding:
            raise ValueError("manifest audience_package does not match the resolved audience")
        canonical_profiles = resolution["grounded_context_profiles"]
        supplied_profiles = context.get("profiles")
        if supplied_profiles is None:
            raw_profiles = canonical_profiles
        else:
            supplied_profiles = _require_sequence(supplied_profiles, "profiles")
            canonical_by_bytes = {
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")): item
                for item in canonical_profiles
            }
            selected: list[Mapping[str, Any]] = []
            selected_ids: set[str] = set()
            for index, raw in enumerate(supplied_profiles):
                profile = _require_mapping(raw, f"profiles[{index}]")
                encoded = json.dumps(
                    profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                canonical = canonical_by_bytes.get(encoded)
                if canonical is None:
                    raise ValueError(
                        f"profiles[{index}] is absent from the resolved grounded-profile records"
                    )
                profile_id = canonical["grounded_profile_id"]
                if profile_id in selected_ids:
                    raise ValueError("resolved grounded profiles must not be duplicated")
                selected_ids.add(profile_id)
                selected.append(canonical)
            raw_profiles = selected
    profiles_by_segment: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    profiles_by_context: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for index, raw_profile in enumerate(raw_profiles):
        profile = _require_mapping(raw_profile, f"profiles[{index}]")
        segment_id = profile.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError(f"profiles[{index}].segment_id is required")
        profiles_by_segment[segment_id].append(profile)
        context_stratum_id = profile.get("context_stratum_id")
        if context_stratum_id is not None:
            if not isinstance(context_stratum_id, str) or not context_stratum_id.strip():
                raise ValueError(
                    f"profiles[{index}].context_stratum_id must be a non-empty string"
                )
            profiles_by_context[(segment_id, context_stratum_id)].append(profile)
    cores = _authorized_cores(
        plan,
        context,
        str(record_type),
        sorted(profiles_by_segment),
        manifest,
    )
    profile_offsets: dict[tuple[str, str | None], int] = defaultdict(int)
    bindings: list[dict[str, Any]] = []
    for index, raw_core in enumerate(cores):
        core = _require_mapping(raw_core, f"synthetic_replicate_jobs[{index}]")
        segment_id = core.get("segment_id")
        if not isinstance(segment_id, str) or segment_id not in profiles_by_segment:
            raise ValueError(
                f"synthetic_replicate_jobs[{index}].segment_id has no approved profile"
            )
        context_stratum_id = core.get("context_stratum_id")
        if context_stratum_id is not None:
            if not isinstance(context_stratum_id, str) or not context_stratum_id.strip():
                raise ValueError(
                    f"synthetic_replicate_jobs[{index}].context_stratum_id is invalid"
                )
            profiles = profiles_by_context.get((segment_id, context_stratum_id), [])
            if not profiles:
                raise ValueError(
                    f"synthetic_replicate_jobs[{index}] has no approved profile for "
                    f"segment {segment_id!r} and context stratum "
                    f"{context_stratum_id!r}"
                )
        else:
            profiles = profiles_by_segment[segment_id]
        offset_key = (segment_id, context_stratum_id)
        profile = profiles[profile_offsets[offset_key] % len(profiles)]
        profile_offsets[offset_key] += 1
        replicate_id = core.get("synthetic_replicate_id")
        if not isinstance(replicate_id, str) or not replicate_id.strip():
            raise ValueError(
                f"synthetic_replicate_jobs[{index}].synthetic_replicate_id is required"
            )
        bindings.append(
            {
                "profile": profile,
                "replicate_id": replicate_id,
                "segment_id": segment_id,
                "context_stratum_id": context_stratum_id,
            }
        )
    return _shape_worker_jobs(
        cores,
        bindings,
        context,
        study_id=study_id,
        method=method,
        record_type=str(record_type),
    )


__all__ = ["AllocationDecisionRequired", "enrich_assignment_jobs"]
