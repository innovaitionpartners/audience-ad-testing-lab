#!/usr/bin/env python3
"""Create a deterministic large-library study and capacity plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from audience_lab.assignments import (
    build_assignments,
    build_boundary_reserve_slots,
    build_finalist_reserve_slots,
    plan_context_stratum_schedule,
)
from audience_lab.audience_allocation import (
    ALLOCATION_REQUEST_VERSION,
    allocate_stage_profiles,
)
from audience_lab.audience_library import (
    audience_package_binding,
    load_reusable_audience_resolution,
    materialize_provisional_audience,
    verify_file_package_binding,
)
from audience_lab.contracts import load_json
from audience_lab.dynamic_complete_exposure_capacity import (
    plan_dynamic_complete_exposure_capacity,
)
from audience_lab.planning import (
    ContextStratum,
    StudyRequest,
    choose_method,
    load_reusable_v3_audience_resolution,
    minimum_screening_jobs,
    reserve_capacity,
    resolve_reported_segment_ids,
    v3_allocation_profiles,
)


def _absolute_without_resolving_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_request", type=Path)
    parser.add_argument("output_plan", type=Path)
    parser.add_argument(
        "--burden-pilot",
        choices=("passed", "failed", "not_run"),
        required=True,
    )
    parser.add_argument("--reported-segments", type=positive_int, required=True)
    parser.add_argument("--boundary-jobs-per-wave", type=non_negative_int, required=True)
    parser.add_argument("--boundary-waves-max", type=non_negative_int, required=True)
    parser.add_argument("--finalist-reserved", type=non_negative_int, required=True)
    parser.add_argument("--assignment-seed", type=int, default=17)
    parser.add_argument("--audience-resolution", type=Path)
    parser.add_argument("--allow-directional-allocation", action="store_true")
    return parser


def _canonical_sha256(value: object) -> str:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _atomic_json_write(path: Path, payload: object, *, indent: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if indent is None
        else json.dumps(payload, indent=indent)
    )
    encoded = (serialized + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _remove_competing_capacity_alias(output_plan: Path) -> None:
    """Remove the retired sibling alias before publishing one plan state."""

    alias = output_plan.parent / "capacity-decision.json"
    if alias == output_plan:
        return
    if not alias.exists() and not alias.is_symlink():
        return
    if alias.is_symlink() or not alias.is_file():
        raise ValueError(
            "capacity-decision.json must be a regular file before retirement"
        )
    alias.unlink()


def _complete_assignment_payload(
    creative_ids: tuple[str, ...],
    segment_allocations: dict[str, int],
    seed: int,
    context_strata: tuple[ContextStratum, ...],
) -> dict[str, object]:
    """Build a dynamic full-set schedule with deterministic counterbalancing."""

    jobs: list[dict[str, object]] = []
    exposure_counts = {creative_id: 0 for creative_id in creative_ids}
    position_counts = {
        creative_id: [0 for _ in creative_ids] for creative_id in creative_ids
    }
    context_allocations: list[dict[str, object]] = []
    normalized_strata: list[ContextStratum] = []
    for segment_id, planned_jobs in sorted(segment_allocations.items()):
        segment_strata = tuple(
            stratum for stratum in context_strata if stratum.segment_id == segment_id
        )
        scheduled_strata: tuple[str, ...] = ()
        if segment_strata:
            normalized, allocations, scheduled_strata = plan_context_stratum_schedule(
                segment_strata,
                segment_id=segment_id,
                job_count=planned_jobs,
                seed=seed,
            )
            normalized_strata.extend(normalized)
            context_allocations.extend(
                {
                    "segment_id": segment_id,
                    "context_stratum_id": context_stratum_id,
                    "planned_jobs": planned_jobs,
                }
                for context_stratum_id, planned_jobs in allocations
            )
        rng = random.Random(f"{seed}:{segment_id}:complete-exposure-v1")
        offset = rng.randrange(len(creative_ids))
        for job_index in range(1, planned_jobs + 1):
            rotation = (offset + job_index - 1) % len(creative_ids)
            ordered = list(creative_ids[rotation:] + creative_ids[:rotation])
            if job_index % 2 == 0:
                ordered.reverse()
            for position, creative_id in enumerate(ordered):
                exposure_counts[creative_id] += 1
                position_counts[creative_id][position] += 1
            job: dict[str, object] = {
                    "synthetic_replicate_id": (
                        f"{segment_id}-complete-replicate-{job_index:04d}"
                    ),
                    "segment_id": segment_id,
                    "variation_ids": list(creative_ids),
                    "shown_order": ordered,
                    "inclusion_probability": 1.0,
                }
            if scheduled_strata:
                job["context_stratum_id"] = scheduled_strata[job_index - 1]
            jobs.append(job)
    payload: dict[str, object] = {
        "block_size": len(creative_ids),
        "assignment_version": "complete-exposure-counterbalanced-v1",
        "seed": seed,
        "segment_allocations": dict(sorted(segment_allocations.items())),
        "exposure_counts": exposure_counts,
        "position_counts": position_counts,
        "synthetic_replicate_jobs": jobs,
    }
    if normalized_strata:
        context_balance: list[dict[str, object]] = []
        for allocation in context_allocations:
            segment_id = str(allocation["segment_id"])
            context_stratum_id = str(allocation["context_stratum_id"])
            scoped_jobs = [
                job
                for job in jobs
                if job["segment_id"] == segment_id
                and job.get("context_stratum_id") == context_stratum_id
            ]
            scoped_exposure = {creative_id: 0 for creative_id in creative_ids}
            scoped_positions = {
                creative_id: [0 for _ in creative_ids]
                for creative_id in creative_ids
            }
            for job in scoped_jobs:
                for creative_id in job["variation_ids"]:
                    scoped_exposure[str(creative_id)] += 1
                for position, creative_id in enumerate(job["shown_order"]):
                    scoped_positions[str(creative_id)][position] += 1
            position_values = [
                value for values in scoped_positions.values() for value in values
            ]
            context_balance.append(
                {
                    "diagnostic_scope": "planned_assignment_balance",
                    **allocation,
                    "assigned_jobs": len(scoped_jobs),
                    "exposure_counts": scoped_exposure,
                    "position_counts": scoped_positions,
                    "exposure_range": (
                        max(scoped_exposure.values()) - min(scoped_exposure.values())
                    ),
                    "position_range": max(position_values) - min(position_values),
                }
            )
        payload.update(
            {
                "context_strata": [
                    stratum.as_dict() for stratum in normalized_strata
                ],
                "context_stratum_allocations": context_allocations,
                "context_stratum_balance": context_balance,
            }
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        request = StudyRequest.from_mapping(load_json(args.study_request))
        resolution = None
        audience_binding = None
        v3_envelope = None
        v3_envelope_sha256 = None
        if request.audience_route == "provisional_audience":
            output_run_dir = _absolute_without_resolving_symlinks(args.output_plan).parent
            canonical_resolution = output_run_dir / "audience" / "resolution.json"
            if args.audience_resolution and (
                _absolute_without_resolving_symlinks(args.audience_resolution)
                != canonical_resolution
            ):
                raise ValueError(
                    "a provisional audience resolution is run-local and cannot be reused"
                )
            resolution = materialize_provisional_audience(
                request.audience_intake["value"],
                run_dir=output_run_dir,
            )
            audience_binding = audience_package_binding(
                output_run_dir, resolution
            )
            request = replace(
                request,
                context_strata=tuple(
                    ContextStratum.from_mapping(item)
                    for item in resolution["context_strata"]
                ),
            )
        elif args.audience_resolution:
            output_run_dir = _absolute_without_resolving_symlinks(args.output_plan).parent
            canonical_resolution = output_run_dir / "audience" / "resolution.json"
            supplied_resolution = _absolute_without_resolving_symlinks(
                args.audience_resolution
            )
            if supplied_resolution != canonical_resolution:
                raise ValueError(
                    "audience resolution must equal the canonical run-relative "
                    "output_plan.parent/audience/resolution.json path"
                )
            if request.audience_route != "audience_panel":
                raise ValueError(
                    "an audience resolution requires the exact audience_panel intake route"
                )
            selected = request.audience_intake["value"]
            raw_resolution = supplied_resolution.read_bytes()
            candidate_resolution = load_json(supplied_resolution)
            if candidate_resolution.get("schema_version") == "audience-run-envelope-v3":
                resolution, raw_resolution = (
                    load_reusable_v3_audience_resolution(
                        supplied_resolution
                    )
                )
                v3_envelope = resolution
                v3_envelope_sha256 = (
                    "sha256:" + hashlib.sha256(raw_resolution).hexdigest()
                )
                audience_binding = resolution["audience_package"]
                if selected["source"] == "library":
                    if (
                        selected["panel_id"] != audience_binding["panel_id"]
                        or selected["version"] != audience_binding["panel_version"]
                    ):
                        raise ValueError(
                            "v3 audience resolution identity does not match "
                            "the selected library panel"
                        )
                else:
                    package_path = Path(selected["package_path"])
                    package_hash = hashlib.sha256(package_path.read_bytes()).hexdigest()
                    if package_hash != audience_binding["package_zip_sha256"]:
                        raise ValueError(
                            "v3 audience resolution does not bind the selected package"
                        )
            else:
                if args.allow_directional_allocation:
                    raise ValueError(
                        "--allow-directional-allocation is available only for v3 planning"
                    )
                resolution = load_reusable_audience_resolution(
                    supplied_resolution
                )
                if selected["source"] == "library" and (
                    selected["panel_id"] != resolution["panel_id"]
                    or selected["version"] != resolution["panel_version"]
                ):
                    raise ValueError(
                        "audience resolution identity does not match the selected library panel"
                    )
                audience_binding = audience_package_binding(
                    output_run_dir, resolution
                )
                if selected["source"] == "file":
                    verify_file_package_binding(
                        selected["package_path"], audience_binding
                    )
            request = replace(
                request,
                context_strata=tuple(
                    ContextStratum.from_mapping(item)
                    for item in resolution["context_strata"]
                ),
            )
        elif request.audience_route == "target_audience":
            raise ValueError(
                "v2 audience intake must be packaged and resolved before planning"
            )
        else:
            raise ValueError(
                "legacy v1 audience inputs are read-only; new plans require a bound v2 audience route"
            )
        segment_ids = resolve_reported_segment_ids(
            request.context_strata, args.reported_segments
        )
        method = choose_method(request.creative_count, args.burden_pilot == "passed")
        dynamic_complete_exposure_capacity = None
        complete_segment_allocations = None
        dynamic_profiles = None
        if method == "complete_exposure" and v3_envelope is not None:
            dynamic_profiles = v3_allocation_profiles(v3_envelope)
            dynamic_complete_exposure_capacity = (
                plan_dynamic_complete_exposure_capacity(
                    profiles=dynamic_profiles,
                    segment_weights=v3_envelope["audience_lock"][
                        "segment_weights"
                    ],
                    creative_count=request.creative_count,
                    maximum_total_executions=(
                        request.maximum_synthetic_panelists
                    ),
                    finalist_reserved=args.finalist_reserved,
                )
            )
            screening_planned = int(
                dynamic_complete_exposure_capacity[
                    "core_planned_executions"
                ]
            )
            complete_segment_allocations = {
                str(segment_id): int(planned)
                for segment_id, planned in dynamic_complete_exposure_capacity[
                    "core_allocation_by_segment"
                ].items()
            }
        elif method == "complete_exposure":
            # Compatibility-only path for byte-bound v2 plans. New v3 plans
            # always use the profile-aware versioned policy above.
            screening_planned = 9 * args.reported_segments
            complete_segment_allocations = {
                segment_id: 9 for segment_id in segment_ids
            }
        else:
            screening_planned = minimum_screening_jobs(
                request.creative_count,
                reported_segments=args.reported_segments,
            )
        boundary_jobs_per_wave = (
            0 if method == "complete_exposure" else args.boundary_jobs_per_wave
        )
        boundary_waves_max = (
            0 if method == "complete_exposure" else args.boundary_waves_max
        )
        capacity = reserve_capacity(
            ceiling=request.maximum_synthetic_panelists,
            screening_planned=screening_planned,
            boundary_jobs_per_wave=boundary_jobs_per_wave,
            boundary_waves_max=boundary_waves_max,
            finalist_reserved=args.finalist_reserved,
        )
        capacity_payload = asdict(capacity)
        capacity_payload.update(
            {
                "boundary_jobs_per_wave": boundary_jobs_per_wave,
                "boundary_waves_max": boundary_waves_max,
                "shortfall": capacity.shortfall,
            }
        )
        total_capacity_authorized = (
            capacity.ceiling_satisfied
            and (
                dynamic_complete_exposure_capacity is None
                or bool(
                    dynamic_complete_exposure_capacity[
                        "authorized_total_capacity_satisfied"
                    ]
                )
            )
        )
        if v3_envelope is not None:
            _remove_competing_capacity_alias(args.output_plan)
        if v3_envelope is not None and not total_capacity_authorized:
            decision = {
                "schema_version": "audience-capacity-decision-v1",
                "decision_status": "insufficient_capacity",
                "study_id": request.study_id,
                "envelope_sha256": v3_envelope_sha256,
                "audience_package_sha256": (
                    "sha256:" + audience_binding["package_zip_sha256"]
                ),
                "synthetic_replicate_capacity": capacity_payload,
            }
            if dynamic_complete_exposure_capacity is not None:
                decision["dynamic_complete_exposure_capacity"] = (
                    dynamic_complete_exposure_capacity
                )
            _atomic_json_write(args.output_plan, decision, indent=None)
            print(
                f"required_total={capacity.required_total} "
                f"shortfall={capacity.shortfall} "
                f"method={method}"
            )
            return 3
        assignment = None
        if method == "complete_exposure" and total_capacity_authorized:
            if complete_segment_allocations is None:
                raise ValueError(
                    "complete exposure requires planned segment allocations"
                )
            assignment = _complete_assignment_payload(
                request.creative_ids,
                complete_segment_allocations,
                args.assignment_seed,
                request.context_strata,
            )
        elif method == "partial_exposure_maxdiff" and total_capacity_authorized:
            jobs_per_segment = screening_planned // args.reported_segments
            segment_allocations = {
                segment_id: jobs_per_segment for segment_id in segment_ids
            }
            assignment = build_assignments(
                request.creative_ids,
                segment_allocations,
                seed=args.assignment_seed,
                capacity_plan=capacity,
                context_strata=request.context_strata,
            )
        audience_profile_rosters = None
        audience_allocation_fidelity = None
        audience_run_claim = None
        if v3_envelope is not None and assignment is not None:
            if (
                method == "partial_exposure_maxdiff"
                and (
                    boundary_jobs_per_wave < 1
                    or boundary_waves_max < 1
                )
            ):
                raise ValueError(
                    "v3 large-library planning requires a positive boundary reserve"
                )
            if method == "complete_exposure" and (
                capacity.boundary_reserved != 0
                or boundary_jobs_per_wave != 0
                or boundary_waves_max != 0
            ):
                raise ValueError(
                    "v3 complete exposure requires exact zero boundary capacity"
                )
            if capacity.finalist_reserved < 1:
                raise ValueError(
                    "v3 large-library planning requires a positive finalist reserve"
                )
            assignment_payload = (
                assignment.as_dict()
                if hasattr(assignment, "as_dict")
                else assignment
            )
            screening_slots = [
                {
                    "slot_id": job["synthetic_replicate_id"],
                    "reported_segment_id": job["segment_id"],
                }
                for job in assignment_payload["synthetic_replicate_jobs"]
            ]
            boundary_slots = (
                []
                if method == "complete_exposure"
                else build_boundary_reserve_slots(
                    segment_ids,
                    jobs_per_wave=boundary_jobs_per_wave,
                    waves_max=boundary_waves_max,
                )
            )
            finalist_slots = build_finalist_reserve_slots(
                capacity.finalist_reserved
            )
            profiles = (
                dynamic_profiles
                if dynamic_profiles is not None
                else v3_allocation_profiles(v3_envelope)
            )
            must_cover_group_ids = sorted(
                {
                    group_id
                    for profile in profiles
                    for group_id in profile["must_cover_group_ids"]
                }
            )
            analysis_weights = {
                str(segment_id): float(weight)
                for segment_id, weight in v3_envelope["audience_lock"][
                    "segment_weights"
                ].items()
            }
            stable_seed = (
                f"{request.study_id}:{args.assignment_seed}:"
                "audience-profile-allocation-v1"
            )

            def allocate(
                stage: str,
                roster_name: str,
                slots: list[dict[str, object]],
            ) -> dict[str, object]:
                return allocate_stage_profiles(
                    {
                        "schema_version": ALLOCATION_REQUEST_VERSION,
                        "stage": stage,
                        "stage_roster_id": (
                            f"{request.study_id}:{roster_name}"
                        ),
                        "stable_seed": stable_seed,
                        "allocation_basis": v3_envelope["allocation_basis"],
                        "slots": slots,
                        "profiles": profiles,
                        "analysis_weights": (
                            {} if stage == "finalist" else analysis_weights
                        ),
                        "must_cover_group_ids": must_cover_group_ids,
                        "maximum_absolute_deviation": 0.05,
                        "allow_directional_allocation": (
                            args.allow_directional_allocation
                        ),
                    }
                )

            screening_roster = allocate(
                "screening", "screening", screening_slots
            )
            if screening_roster["claim_effect"] == "requires_user_decision":
                _atomic_json_write(
                    args.output_plan,
                    screening_roster,
                    indent=2,
                )
                print(
                    "audience allocation requires a user decision",
                    file=sys.stderr,
                )
                return 6
            boundary_roster = (
                {
                    "schema_version": (
                        "audience-profile-allocation-not-applicable-v1"
                    ),
                    "stage": "boundary",
                    "stage_roster_id": (
                        f"{request.study_id}:boundary-reserve"
                    ),
                    "status": "not_applicable",
                    "reason": "method_complete_exposure",
                    "assignments": [],
                    "fidelity": {
                        "status": "not_applicable",
                        "allocation_basis": v3_envelope[
                            "allocation_basis"
                        ],
                    },
                }
                if method == "complete_exposure"
                else allocate(
                    "boundary", "boundary-reserve", boundary_slots
                )
            )
            finalist_roster = allocate(
                "finalist", "finalist-reserve", finalist_slots
            )
            roster_core = {
                "schema_version": "audience-profile-rosters-v1",
                "envelope_sha256": v3_envelope_sha256,
                "screening": screening_roster,
                "boundary_reserve": boundary_roster,
                "finalist_reserve": finalist_roster,
            }
            combined_input = {
                "schema_version": roster_core["schema_version"],
                "study_id": request.study_id,
                "method": method,
                "maximum_synthetic_panelists": (
                    request.maximum_synthetic_panelists
                ),
                "synthetic_replicate_capacity": capacity_payload,
                "assignment_sha256": _canonical_sha256(
                    assignment_payload
                ),
                "envelope_sha256": roster_core["envelope_sha256"],
                "screening": roster_core["screening"],
                "boundary_reserve": roster_core["boundary_reserve"],
                "finalist_reserve": roster_core["finalist_reserve"],
            }
            audience_profile_rosters = {
                **roster_core,
                "combined_sha256": _canonical_sha256(combined_input),
            }
            audience_allocation_fidelity = {
                stage: audience_profile_rosters[stage]["fidelity"]
                for stage in (
                    "screening",
                    "boundary_reserve",
                    "finalist_reserve",
                )
            }
            audience_run_claim = screening_roster["claim_effect"]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    payload = {
        "study_id": request.study_id,
        "creative_format": request.creative_format,
        "creative_count": request.creative_count,
        "requested_shortlist_size": request.requested_shortlist_size,
        "method": method,
        "burden_pilot_status": args.burden_pilot,
        "reported_segments": args.reported_segments,
        "reported_segment_ids": list(segment_ids),
        "synthetic_replicate_capacity": capacity_payload,
    }
    if assignment is not None:
        payload["assignment"] = (
            assignment.as_dict() if hasattr(assignment, "as_dict") else assignment
        )
    if resolution is not None:
        payload.update(
            {
                "audience_lock": resolution["audience_lock"],
                "audience_package": audience_binding,
                "grounded_context_profiles": resolution["grounded_context_profiles"],
            }
        )
    if audience_profile_rosters is not None:
        payload.update(
            {
                "maximum_synthetic_panelists": (
                    request.maximum_synthetic_panelists
                ),
                "audience_profile_rosters": audience_profile_rosters,
                "audience_allocation_fidelity": audience_allocation_fidelity,
                "audience_run_claim": audience_run_claim,
            }
        )
    if dynamic_complete_exposure_capacity is not None:
        payload["dynamic_complete_exposure_capacity"] = (
            dynamic_complete_exposure_capacity
        )

    try:
        _atomic_json_write(args.output_plan, payload, indent=2)
    except (OSError, UnicodeError) as exc:
        parser.error(str(exc))

    print(
        f"required_total={capacity.required_total} "
        f"shortfall={capacity.shortfall} "
        f"method={method}"
    )
    return 0 if total_capacity_authorized else 3


if __name__ == "__main__":
    sys.exit(main())
