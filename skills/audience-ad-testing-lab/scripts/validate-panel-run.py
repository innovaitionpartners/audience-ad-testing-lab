#!/usr/bin/env python3
"""Validate enriched synthetic-replicate jobs and discriminated responses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from audience_lab.responses import (
    validate_job,
    validate_response_job_bindings,
)
from audience_lab.contracts import validate_v3_jobs_envelope
from audience_lab.legacy_v2_origin import (
    validate_legacy_v2_producer_record,
)


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_response_records(path: Path) -> list[dict[str, Any]]:
    """Load response JSONL, or the new response-list JSON shape for compatibility."""

    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            records.append(record)
        return records

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and "responses" in payload:
        records = payload["responses"]
    elif isinstance(payload, dict) and "record_type" in payload:
        records = [payload]
    elif isinstance(payload, dict) and "reviews" in payload:
        raise ValueError(
            "the obsolete reviews/panelist_reviews JSON contract is not supported; "
            "supply discriminated response records"
        )
    else:
        raise ValueError(f"{path} must contain responses or one discriminated response")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError(f"{path} responses must be an array of objects")
    return records


def validate_job_payload(
    payload: Mapping[str, Any], expected_count: int | None
) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
    errors: list[str] = []
    jobs = payload.get("synthetic_replicate_jobs")
    if not isinstance(jobs, list) or not jobs:
        if "panelist_jobs" in payload:
            return [
                "panelist_jobs is obsolete; expected synthetic_replicate_jobs enriched "
                "for progressive collection"
            ], {}
        return ["synthetic_replicate_jobs must be a non-empty array"], {}
    if expected_count is not None and len(jobs) != expected_count:
        errors.append(
            f"expected {expected_count} synthetic replicate jobs, found {len(jobs)}"
        )

    by_replicate: dict[str, Mapping[str, Any]] = {}
    response_ids: set[str] = set()
    dispatch_ids: set[str] = set()
    for index, job in enumerate(jobs):
        prefix = f"synthetic_replicate_jobs[{index}]"
        if not isinstance(job, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(f"{prefix}: {error}" for error in validate_job(job))
        replicate_id = job.get("synthetic_replicate_id")
        response_id = job.get("response_id")
        dispatch_id = job.get("dispatch_id")
        if isinstance(replicate_id, str) and replicate_id:
            if replicate_id in by_replicate:
                errors.append(f"duplicate synthetic_replicate_id: {replicate_id}")
            else:
                by_replicate[replicate_id] = job
        if isinstance(response_id, str) and response_id:
            if response_id in response_ids:
                errors.append(f"duplicate response_id in jobs: {response_id}")
            response_ids.add(response_id)
        if isinstance(dispatch_id, str) and dispatch_id:
            if dispatch_id in dispatch_ids:
                errors.append(f"duplicate dispatch_id: {dispatch_id}")
            dispatch_ids.add(dispatch_id)
    return errors, by_replicate


def _is_v3_jobs_payload(payload: Mapping[str, Any]) -> bool:
    if any(
        field in payload
        for field in (
            "audience_allocation_subset",
            "audience_run_claim",
            "audience_dispatch",
        )
    ):
        return True
    jobs = payload.get("synthetic_replicate_jobs")
    return isinstance(jobs, list) and any(
        isinstance(job, Mapping)
        and any(
            field in job
            for field in (
                "audience_slot_id",
                "profile_snapshot_sha256",
            )
        )
        for job in jobs
    )


def authenticate_v3_jobs_payload(
    payload: Mapping[str, Any],
    *,
    manifest_path: Path | None,
    audience_resolution: Path | None,
    dispatch_authority_path: Path | None,
) -> dict[str, Any]:
    supplied = (
        manifest_path is not None,
        audience_resolution is not None,
        dispatch_authority_path is not None,
    )
    if not all(supplied):
        raise ValueError(
            "v3 jobs require --manifest, --audience-resolution, and "
            "--dispatch-authority together"
        )
    assert manifest_path is not None
    assert audience_resolution is not None
    assert dispatch_authority_path is not None
    manifest = load_object(manifest_path)
    dispatch_authority = load_object(dispatch_authority_path)
    record_type = payload.get("record_type")
    stage_key = {
        "screening_response": "screening",
        "boundary_response": "boundary_reserve",
        "finalist_response": "finalist_reserve",
    }.get(record_type)
    rosters = manifest.get("audience_profile_rosters")
    if stage_key is None or not isinstance(rosters, Mapping):
        raise ValueError(
            "v3 jobs cannot resolve one frozen allocation stage from the manifest"
        )
    allocation_plan = rosters.get(stage_key)
    if not isinstance(allocation_plan, Mapping):
        raise ValueError(
            f"v3 manifest is missing audience_profile_rosters.{stage_key}"
        )
    return validate_v3_jobs_envelope(
        payload,
        allocation_plan=allocation_plan,
        authority=manifest,
        audience_resolution=audience_resolution,
        dispatch_authority=dispatch_authority,
    )


def authenticate_legacy_v2_jobs_payload(
    payload: Mapping[str, Any],
    authority_path: Path | None,
) -> None:
    if authority_path is None:
        raise ValueError(
            "non-v3 jobs require --legacy-v2-origin-authority from the trusted producer"
        )
    validate_legacy_v2_producer_record(
        load_object(authority_path),
        payload,
        record_path=authority_path,
    )


def validate_response_records(
    responses: list[dict[str, Any]], expected: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    return validate_response_job_bindings(list(expected.values()), responses)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "jobs", type=Path, help="JSON file containing synthetic_replicate_jobs"
    )
    parser.add_argument(
        "--reviews",
        "--responses",
        dest="responses",
        type=Path,
        help="Optional JSONL or JSON file containing discriminated response records",
    )
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--audience-resolution", type=Path)
    parser.add_argument("--dispatch-authority", type=Path)
    parser.add_argument("--legacy-v2-origin-authority", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    expected: dict[str, Mapping[str, Any]] = {}
    responses: list[dict[str, Any]] | None = None
    try:
        jobs_payload = load_object(args.jobs)
        if _is_v3_jobs_payload(jobs_payload):
            if args.legacy_v2_origin_authority is not None:
                raise ValueError(
                    "legacy v2 origin authority cannot downgrade a v3 jobs envelope"
                )
            jobs_payload = authenticate_v3_jobs_payload(
                jobs_payload,
                manifest_path=args.manifest,
                audience_resolution=args.audience_resolution,
                dispatch_authority_path=args.dispatch_authority,
            )
        elif any(
            value is not None
            for value in (
                args.manifest,
                args.audience_resolution,
                args.dispatch_authority,
            )
        ):
            raise ValueError(
                "v3 dispatch authority options are valid only for a v3 jobs envelope"
            )
        else:
            authenticate_legacy_v2_jobs_payload(
                jobs_payload,
                args.legacy_v2_origin_authority,
            )
        job_errors, expected = validate_job_payload(jobs_payload, args.expected_count)
        errors.extend(job_errors)
        if args.responses:
            responses = load_response_records(args.responses)
            errors.extend(validate_response_records(responses, expected))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))

    if errors:
        print("Panel run validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Panel run validation passed: {len(expected)} "
        "context-isolated synthetic replicate jobs"
    )
    if responses is not None:
        print(
            f"Response validation passed: {len(responses)} "
            "discriminated synthetic responses"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
