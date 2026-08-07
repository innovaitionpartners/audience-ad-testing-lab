#!/usr/bin/env python3
"""Run the closed fictional base-versus-candidate exercise."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.population.experimental_calibration.exercise import (
    ExerciseDependencyUnavailable,
    ExerciseSourceIsolationFailure,
    build_synthetic_panel_exercise,
    load_public_scenario_inputs,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fictional synthetic persona behavior exercise."
    )
    parser.add_argument("--study-manifest", required=True, type=Path)
    parser.add_argument("--public-scenarios-root", required=True, type=Path)
    parser.add_argument("--creative-attribute-registry", required=True, type=Path)
    parser.add_argument("--base-panel", required=True, type=Path)
    parser.add_argument(
        "--candidate-bindings-and-panels", required=True, type=Path
    )
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--exercised-at", required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--private-stage-output", type=Path)
    return parser


def _read_json(path: Path, label: str) -> object:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not readable JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise ContractError(f"{label} must be canonical JSON")
    return value


def _write_private_stage(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.absolute(), flags)
    except OSError as exc:
        raise ContractError(
            "private-stage output inode is unavailable"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ContractError("private-stage output must be a regular file")
        os.ftruncate(descriptor, 0)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ContractError("private-stage output write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = (args.private_stage_output or args.output).absolute()
    if args.private_stage_output is None and (
        output.exists() or output.is_symlink()
    ):
        print("exercise output already exists or is a symlink", file=sys.stderr)
        return 3
    try:
        result = build_synthetic_panel_exercise(
            study_manifest=_read_json(
                args.study_manifest, "study manifest"
            ),
            public_scenario_inputs=load_public_scenario_inputs(
                args.public_scenarios_root
            ),
            creative_attribute_registry=_read_json(
                args.creative_attribute_registry,
                "creative attribute registry",
            ),
            base_panel=_read_json(args.base_panel, "base panel"),
            candidate_bindings_and_panels=_read_json(
                args.candidate_bindings_and_panels,
                "candidate bindings and panels",
            ),
            exercise_id=args.exercise_id,
            exercised_at=args.exercised_at,
        )
        payload = canonical_json_bytes(result)
        if args.private_stage_output is not None:
            _write_private_stage(output, payload)
        else:
            write_new_bytes(
                output,
                payload,
                "synthetic exercise output",
            )
    except (ExerciseDependencyUnavailable, ExerciseSourceIsolationFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
