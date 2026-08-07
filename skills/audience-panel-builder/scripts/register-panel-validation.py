#!/usr/bin/env python3
"""Seal one preregistration without replacing any existing output."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    approve_preregistration_design,
    load_trusted_authority_registry,
    read_protected_authority_secret,
    seal_preregistration,
)


def _file_sha256(path: Path) -> str:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"{path} is not readable approval evidence") from exc
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path} is not readable canonical JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-root", required=True, type=Path)
    parser.add_argument("--authority-index", required=True, type=Path)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        draft = _load(args.input)
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        approved, capability = approve_preregistration_design(
            draft,
            authority_registry=registry,
            authority_id=str(draft["registered_by"]),
        )
        approval = approved["approval"]
        if (
            approval["authority_root_sha256"]
            != _file_sha256(args.authority_root)
            or approval["authority_index_sha256"]
            != _file_sha256(args.authority_index)
        ):
            raise ContractError(
                "trusted authority root/index bytes do not match registry entry"
            )
        registration = seal_preregistration(
            approved, design_approval=capability,
        )
        write_new_bytes(args.output, canonical_json_bytes(registration), "panel validation preregistration")
        payload, code = {
            "status": "registered",
            "output": str(args.output),
            "registration_sha256": registration["registration_sha256"],
        }, 0
    except ContractError as exc:
        collision = "already exists" in str(exc)
        payload, code = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": str(exc),
        }, 3 if collision else 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
