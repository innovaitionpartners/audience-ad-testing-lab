#!/usr/bin/env python3
"""Materialize one complete synthetic-only sandbox persona candidate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.experimental_calibration.candidate import (  # noqa: E402
    CandidateNotMaterializable,
    UnsafeCandidateOutput,
    materialize_sandbox_candidate,
    publish_sandbox_candidate_bundle,
)


def _read_json(path: Path, label: str) -> object:
    flags = os.O_RDONLY | (
        os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError(f"{label} cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must contain UTF-8 JSON") from exc
    return payload


def _read_object(path: Path, label: str) -> dict[str, object]:
    payload = _read_json(path, label)
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must contain one JSON object")
    return payload


def _read_object_list(path: Path, label: str) -> list[dict[str, object]]:
    payload = _read_json(path, label)
    if not isinstance(payload, list) or any(
        not isinstance(row, dict) for row in payload
    ):
        raise ContractError(f"{label} must contain one JSON object array")
    return payload


def _reject_aliases(
    *,
    output_dir: Path,
    input_paths: tuple[Path, ...],
) -> None:
    output = output_dir.resolve(strict=False)
    for input_path in input_paths:
        source = input_path.resolve(strict=False)
        if output == source or output in source.parents or source in output.parents:
            raise UnsafeCandidateOutput(
                "candidate output must not alias or contain an input path"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-panel", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--study-manifest", required=True, type=Path)
    parser.add_argument("--scenario-manifests", required=True, type=Path)
    parser.add_argument("--experiment-designs", required=True, type=Path)
    parser.add_argument("--diagnosis", required=True, type=Path)
    parser.add_argument("--attribute-registry", required=True, type=Path)
    parser.add_argument(
        "--evidence-library-snapshot",
        required=True,
        type=Path,
    )
    parser.add_argument("--evidence-head-receipt", required=True, type=Path)
    parser.add_argument("--alternative-causes", required=True, type=Path)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        _reject_aliases(
            output_dir=args.output_dir,
            input_paths=(
                args.base_panel,
                args.proposal,
                args.study_manifest,
                args.scenario_manifests,
                args.experiment_designs,
                args.diagnosis,
                args.attribute_registry,
                args.evidence_library_snapshot,
                args.evidence_head_receipt,
                args.alternative_causes,
            ),
        )
        candidate_inputs = {
            "base_panel": _read_object(args.base_panel, "base panel"),
            "proposal": _read_object(args.proposal, "proposal"),
            "study_manifest": _read_object(
                args.study_manifest,
                "study manifest",
            ),
            "scenario_manifests": _read_object_list(
                args.scenario_manifests,
                "scenario manifests",
            ),
            "experiment_designs": _read_object_list(
                args.experiment_designs,
                "experiment designs",
            ),
            "diagnosis": _read_object(args.diagnosis, "diagnosis"),
            "attribute_registry": _read_object(
                args.attribute_registry,
                "attribute registry",
            ),
            "evidence_library_snapshot": _read_object(
                args.evidence_library_snapshot,
                "evidence library snapshot",
            ),
            "evidence_head_receipt": _read_object(
                args.evidence_head_receipt,
                "evidence head receipt",
            ),
            "alternative_causes": _read_object(
                args.alternative_causes,
                "alternative causes",
            ),
            "candidate_id": args.candidate_id,
            "candidate_version": args.candidate_version,
            "created_at": args.created_at,
        }
        materialized = materialize_sandbox_candidate(**candidate_inputs)
        publish_sandbox_candidate_bundle(
            materialized=materialized,
            study_manifest=candidate_inputs["study_manifest"],
            scenario_manifests=candidate_inputs["scenario_manifests"],
            experiment_designs=candidate_inputs["experiment_designs"],
            diagnosis=candidate_inputs["diagnosis"],
            attribute_registry=candidate_inputs["attribute_registry"],
            evidence_library_snapshot=(
                candidate_inputs["evidence_library_snapshot"]
            ),
            evidence_head_receipt=candidate_inputs["evidence_head_receipt"],
            alternative_causes=candidate_inputs["alternative_causes"],
            output_dir=args.output_dir,
        )
        return 0
    except CandidateNotMaterializable as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except UnsafeCandidateOutput as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ContractError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
