#!/usr/bin/env python3
"""Render a static synthetic-only persona behavior sandbox report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from audience_panel_builder.common import ContractError, canonical_json_bytes
from experimental_persona_calibration_oracle.reporting import (
    UnsafeReportTemplate,
    render_experimental_report,
)


class UnsafeReportPath(ValueError):
    """A report input/output path is unsafe."""


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise UnsafeReportPath(f"{label} must be a real regular file")
        return path.read_bytes()
    except UnsafeReportPath:
        raise
    except OSError as exc:
        raise UnsafeReportPath(f"{label} is unavailable") from exc


def _read_json(path: Path, label: str) -> object:
    raw = _read_bytes(path, label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must contain UTF-8 JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise ContractError(f"{label} must use canonical JSON bytes")
    return value


def _object(path: Path, label: str) -> dict[str, object]:
    value = _read_json(path, label)
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain one JSON object")
    return value


def _objects(path: Path, label: str) -> list[dict[str, object]]:
    value = _read_json(path, label)
    if not isinstance(value, list) or any(
        not isinstance(row, dict) for row in value
    ):
        raise ContractError(f"{label} must contain a JSON object array")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise UnsafeReportPath("report output already exists")
        parent = args.output.absolute().parent
        info = os.lstat(parent)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeReportPath("report output parent must be a real directory")
        template = _read_bytes(args.template, "template").decode("utf-8")
        report = render_experimental_report(
            evaluation=_object(args.evaluation, "evaluation"),
            proposals=_objects(args.proposals, "proposals"),
            candidates=_objects(args.candidates, "candidates"),
            template=template,
        )
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
    except (UnsafeReportTemplate, ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except UnsafeReportPath as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (UnicodeDecodeError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
