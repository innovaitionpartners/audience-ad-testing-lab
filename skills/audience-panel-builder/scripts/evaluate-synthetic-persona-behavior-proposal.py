#!/usr/bin/env python3
"""Evaluate sealed synthetic persona-behavior results against hidden truth."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from audience_panel_builder.common import ContractError, canonical_json_bytes
from experimental_persona_calibration_oracle.evaluator import (
    OracleIsolationFailure,
    SealedHoldoutFailure,
    evaluate_synthetic_study,
)


class UnsafeEvaluationPath(ValueError):
    """An evaluation input/output path is unsafe or aliases another role."""


def _read_canonical_json(path: Path, label: str) -> object:
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise UnsafeEvaluationPath(f"{label} must be a real regular file")
        raw = path.read_bytes()
        value = json.loads(raw)
    except UnsafeEvaluationPath:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not readable canonical JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise ContractError(f"{label} must use canonical JSON bytes")
    return value


def _object(path: Path, label: str) -> dict[str, object]:
    value = _read_canonical_json(path, label)
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain one JSON object")
    return value


def _objects(path: Path, label: str) -> list[dict[str, object]]:
    value = _read_canonical_json(path, label)
    if not isinstance(value, list) or any(
        not isinstance(row, dict) for row in value
    ):
        raise ContractError(f"{label} must contain a JSON object array")
    return value


def _validate_paths(output: Path, inputs: tuple[Path, ...]) -> None:
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise UnsafeEvaluationPath("evaluation output already exists")
    parent = output.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise UnsafeEvaluationPath(
            "evaluation output parent must already exist"
        ) from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise UnsafeEvaluationPath(
            "evaluation output parent must be a real directory"
        )
    parent_real = parent.resolve(strict=True)
    seen: set[Path] = set()
    for input_path in inputs:
        try:
            source = input_path.resolve(strict=True)
        except OSError as exc:
            raise UnsafeEvaluationPath("evaluation input is unavailable") from exc
        if source in seen:
            raise UnsafeEvaluationPath("evaluation input roles must be distinct")
        seen.add(source)
        if (
            source == output
            or output in source.parents
            or parent_real == source
            or parent_real in source.parents
        ):
            raise UnsafeEvaluationPath(
                "evaluation output directory must not contain an input"
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-manifest", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--exercise", required=True, type=Path)
    parser.add_argument("--oracles", required=True, type=Path)
    parser.add_argument("--diagnoses", required=True, type=Path)
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--phase-receipts", required=True, type=Path)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_paths = (
        args.study_manifest,
        args.observations,
        args.exercise,
        args.oracles,
        args.diagnoses,
        args.proposals,
        args.candidates,
        args.phase_receipts,
    )
    try:
        _validate_paths(args.output, input_paths)
        result = evaluate_synthetic_study(
            study_manifest=_object(args.study_manifest, "study manifest"),
            observations=_objects(args.observations, "observations"),
            exercise=_object(args.exercise, "exercise"),
            oracle_documents=_objects(args.oracles, "oracles"),
            diagnoses=_objects(args.diagnoses, "diagnoses"),
            proposals=_objects(args.proposals, "proposals"),
            candidates=_objects(args.candidates, "candidates"),
            phase_receipts=_objects(args.phase_receipts, "phase receipts"),
            evaluated_at=args.evaluated_at,
        )
        with args.output.open("xb") as handle:
            handle.write(canonical_json_bytes(result))
    except (OracleIsolationFailure, SealedHoldoutFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except UnsafeEvaluationPath as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
