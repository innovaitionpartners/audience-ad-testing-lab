#!/usr/bin/env python3
"""Seal one synthetic-only persona-behavior proposal or abstain."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.experimental_calibration.proposal import (  # noqa: E402
    ProposalNotPermitted,
    build_experimental_proposal,
)


class UnsafeOutputPath(ContractError):
    """A new output cannot be created without aliasing or replacement."""


_INPUT_KEYS = {
    "base_panel_binding",
    "study_manifest",
    "scenario_manifests",
    "experiment_designs",
    "diagnosis",
    "attribute_registry",
    "evidence_library_snapshot",
    "evidence_head_receipt",
    "alternative_causes",
    "proposal_id",
    "proposed_at",
}
_SYSTEM_PATH_ALIASES = {Path("/etc"), Path("/tmp"), Path("/var")}


def _read_input(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError("input cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("input must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("input must contain UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != _INPUT_KEYS:
        raise ContractError("input must be the closed proposal request")
    return value


def _read_value(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError("input cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("input must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("input must contain UTF-8 JSON") from exc


def _arguments(args: argparse.Namespace) -> dict[str, object]:
    staged: dict[str, object] = {
        "base_panel_binding": args.base_panel_binding,
        "study_manifest": args.study_manifest,
        "scenario_manifests": args.scenario_manifests,
        "experiment_designs": args.experiment_designs,
        "diagnosis": args.diagnosis,
        "attribute_registry": args.attribute_registry,
        "evidence_library_snapshot": args.evidence_library_snapshot,
        "evidence_head_receipt": args.evidence_head_receipt,
        "alternative_causes": args.alternative_causes,
        "proposal_id": args.proposal_id,
        "proposed_at": args.proposed_at,
    }
    if args.input is not None:
        if any(value is not None for value in staged.values()):
            raise ContractError("--input cannot be combined with staged arguments")
        return _read_input(args.input)
    missing = sorted(key for key, value in staged.items() if value is None)
    if missing:
        raise ContractError(
            "staged proposal arguments are incomplete: " + ", ".join(missing)
        )
    return {
        key: (
            _read_value(value)
            if key not in {"proposal_id", "proposed_at"}
            else value
        )
        for key, value in staged.items()
    }


def _write_new(path: Path, payload: bytes) -> None:
    absolute = path.absolute()
    if absolute.exists() or absolute.is_symlink():
        raise UnsafeOutputPath("output already exists or is a symlink")
    for ancestor in absolute.parents:
        if (
            ancestor not in _SYSTEM_PATH_ALIASES
            and ancestor.exists()
            and ancestor.is_symlink()
        ):
            raise UnsafeOutputPath("output has a symlinked ancestor")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags, 0o600)
    except OSError as exc:
        raise UnsafeOutputPath("output cannot be created safely") from exc
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise UnsafeOutputPath("output write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_stage(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.absolute(), flags)
    except OSError as exc:
        raise UnsafeOutputPath(
            "private-stage output inode is unavailable"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafeOutputPath("private-stage output must be a regular file")
        os.ftruncate(descriptor, 0)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise UnsafeOutputPath("output write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--base-panel-binding", type=Path)
    parser.add_argument("--study-manifest", type=Path)
    parser.add_argument("--scenario-manifests", type=Path)
    parser.add_argument("--experiment-designs", type=Path)
    parser.add_argument("--diagnosis", type=Path)
    parser.add_argument("--evidence-library-snapshot", type=Path)
    parser.add_argument("--evidence-head-receipt", type=Path)
    parser.add_argument(
        "--creative-attribute-registry",
        dest="attribute_registry",
        type=Path,
    )
    parser.add_argument("--alternative-causes", type=Path)
    parser.add_argument("--proposal-id")
    parser.add_argument("--proposed-at")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--private-stage-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_experimental_proposal(**_arguments(args))
        payload = canonical_json_bytes(result)
        if args.private_stage_output is not None:
            _write_private_stage(args.private_stage_output, payload)
        else:
            _write_new(args.output, payload)
        return 0
    except ProposalNotPermitted as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except UnsafeOutputPath as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ContractError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
