#!/usr/bin/env python3
"""Enrich a deterministic assignment core for progressive response collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_lab.contracts import load_json
from audience_lab.dispatch import (
    AllocationDecisionRequired,
    enrich_assignment_jobs,
)
from audience_lab.legacy_v2_origin import (
    write_legacy_v2_producer_record,
)


def load_response_records(path: Path) -> list[dict[str, object]]:
    if path.suffix.lower() == ".jsonl":
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = (
            payload.get("responses")
            if isinstance(payload, dict)
            else payload
        )
    if (
        not isinstance(records, list)
        or not records
        or not all(isinstance(record, dict) for record in records)
    ):
        raise ValueError("prior responses must be a non-empty array of objects")
    return records


def load_canonical_boundary_result(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            "prior boundary result must contain one canonical JSON object"
        )
    expected = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if raw != expected:
        raise ValueError(
            "prior boundary result bytes must exactly match the canonical "
            "aggregator JSON encoding"
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignment_core", type=Path)
    parser.add_argument("dispatch_context", type=Path)
    parser.add_argument("output_jobs", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--audience-resolution", type=Path)
    parser.add_argument("--allow-directional-allocation", action="store_true")
    parser.add_argument("--prior-jobs-envelope", type=Path)
    parser.add_argument("--prior-responses", type=Path)
    parser.add_argument("--prior-boundary-result", type=Path)
    parser.add_argument("--legacy-v2-origin-authority-output", type=Path)
    args = parser.parse_args(argv)
    try:
        assignment_core = load_json(args.assignment_core)
        manifest = load_json(args.manifest) if args.manifest else None
        audience_resolution = args.audience_resolution
        if audience_resolution is None and (
            "audience_package" in assignment_core
            or (
                isinstance(manifest, dict)
                and "audience_package" in manifest
            )
        ):
            audience_resolution = (
                args.assignment_core.resolve().parent / "audience" / "resolution.json"
            )
        dispatch_context = load_json(args.dispatch_context)
        payload = enrich_assignment_jobs(
            assignment_core,
            dispatch_context,
            manifest=manifest,
            audience_resolution=audience_resolution,
            allow_directional_allocation=args.allow_directional_allocation,
            prior_jobs_envelope=(
                load_json(args.prior_jobs_envelope)
                if args.prior_jobs_envelope
                else None
            ),
            prior_responses=(
                load_response_records(args.prior_responses)
                if args.prior_responses
                else None
            ),
            prior_boundary_result=(
                load_canonical_boundary_result(args.prior_boundary_result)
                if args.prior_boundary_result
                else None
            ),
        )
        is_v3 = "audience_dispatch" in payload
        if is_v3 and args.legacy_v2_origin_authority_output is not None:
            raise ValueError(
                "legacy v2 origin authority cannot be emitted for v3 jobs"
            )
        if (
            not is_v3
            and args.legacy_v2_origin_authority_output is not None
        ):
            if audience_resolution is None:
                raise ValueError(
                    "legacy v2 producer evidence requires the canonical "
                    "audience resolution"
                )
            write_legacy_v2_producer_record(
                record_path=args.legacy_v2_origin_authority_output,
                assignment_core=assignment_core,
                dispatch_context=dispatch_context,
                manifest=manifest,
                jobs_payload=payload,
                audience_resolution=audience_resolution,
            )
        if (
            isinstance(manifest, dict)
            and manifest.get("method") == "complete_exposure"
            and dispatch_context.get("record_type") == "boundary_response"
            and payload.get("synthetic_replicate_jobs") == []
        ):
            try:
                args.output_jobs.unlink()
            except FileNotFoundError:
                pass
            print(
                "dispatch_status=not_applicable "
                "stage=boundary reason=method_complete_exposure"
            )
            return 0
        args.output_jobs.parent.mkdir(parents=True, exist_ok=True)
        args.output_jobs.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except AllocationDecisionRequired as exc:
        args.output_jobs.parent.mkdir(parents=True, exist_ok=True)
        args.output_jobs.write_text(
            json.dumps(exc.decision, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "allocation_decision=required "
            f"output={args.output_jobs}"
        )
        return 6
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"enriched_jobs={len(payload['synthetic_replicate_jobs'])} "
        f"output={args.output_jobs}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
