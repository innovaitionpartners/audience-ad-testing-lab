#!/usr/bin/env python3
"""Manage the synthetic-only Outcome Evidence Library."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.experimental_calibration.evidence_library import (  # noqa: E402
    EvidenceHistoryError,
    EvidenceLibraryConflict,
    EvidenceLibrarySafetyError,
    append_evidence_correction,
    append_evidence_entry,
    initialize_evidence_library,
    load_evidence_library,
)


def _read_json(path: Path, label: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceLibrarySafetyError(f"{label} cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceLibrarySafetyError(f"{label} must be a regular file")
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
        raise ContractError(f"{label} must contain JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain a JSON object")
    return value


def _emit(value: object) -> None:
    print(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--library-root", required=True, type=Path)
    initialize.add_argument("--library-id", required=True)
    initialize.add_argument("--created-at", required=True)
    append = commands.add_parser("append")
    append.add_argument("--library-root", required=True, type=Path)
    append.add_argument("--observation", required=True, type=Path)
    append.add_argument("--attribute-registry", required=True, type=Path)
    append.add_argument("--ingested-at", required=True)
    correct = commands.add_parser("correct")
    correct.add_argument("--library-root", required=True, type=Path)
    correct.add_argument("--superseded-entry-id", required=True)
    correct.add_argument("--replacement-observation", required=True, type=Path)
    correct.add_argument("--attribute-registry", required=True, type=Path)
    correct.add_argument("--reason", required=True)
    correct.add_argument("--corrected-at", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--library-root", required=True, type=Path)
    listing.add_argument("--as-of", required=True)
    show = commands.add_parser("show")
    show.add_argument("--library-root", required=True, type=Path)
    show.add_argument("--entry-id", required=True)
    show.add_argument("--as-of", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--library-root", required=True, type=Path)
    verify.add_argument("--as-of", required=True)
    verify.add_argument("--expected-head-receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_evidence_library(
                library_root=args.library_root,
                library_id=args.library_id,
                created_at=args.created_at,
            )
        elif args.command == "append":
            result = append_evidence_entry(
                library_root=args.library_root,
                observation=_read_json(args.observation, "observation"),
                attribute_registry=_read_json(
                    args.attribute_registry, "attribute registry"
                ),
                ingested_at=args.ingested_at,
            )
        elif args.command == "correct":
            result = append_evidence_correction(
                library_root=args.library_root,
                superseded_entry_id=args.superseded_entry_id,
                replacement_observation=_read_json(
                    args.replacement_observation, "replacement observation"
                ),
                attribute_registry=_read_json(
                    args.attribute_registry, "attribute registry"
                ),
                correction_reason=args.reason,
                corrected_at=args.corrected_at,
            )
        else:
            expected = (
                _read_json(args.expected_head_receipt, "expected head receipt")
                if args.command == "verify"
                else None
            )
            snapshot = load_evidence_library(
                library_root=args.library_root,
                as_of=args.as_of,
                expected_head_receipt=expected,
            )
            if args.command in {"list", "verify"}:
                result = snapshot
            else:
                result = next(
                    (
                        entry for entry in snapshot["entries"]
                        if entry["entry_id"] == args.entry_id
                    ),
                    None,
                )
                if result is None:
                    raise ContractError(
                        "entry is not active in the requested historical view"
                    )
        _emit(result)
        return 0
    except EvidenceHistoryError as exc:
        _emit({"status": "error", "error": "history", "message": str(exc)})
        return 4
    except (EvidenceLibrarySafetyError, EvidenceLibraryConflict) as exc:
        _emit({"status": "error", "error": "unsafe_or_conflict", "message": str(exc)})
        return 3
    except (ContractError, OSError, ValueError) as exc:
        _emit({"status": "error", "error": "contract", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
