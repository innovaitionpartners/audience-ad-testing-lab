#!/usr/bin/env python3
"""Build a complete, self-hashed Tier 4 claim family from JSON input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_panel_builder.common import ContractError, canonical_json_bytes, write_new_bytes
from audience_panel_builder.population.validation.contracts import (
    load_trusted_authority_registry,
    read_protected_authority_secret,
)
from audience_panel_builder.population.validation.evaluation import build_claim_family


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family-input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.family_input.resolve() or args.output.exists():
        print(f"output collision: {args.output}", file=sys.stderr)
        return 3
    try:
        payload = _load(args.family_input)
        if not isinstance(payload, dict) or set(payload) != {"registrations", "comparisons_by_registration", "built_at"}:
            raise ContractError("family input must contain registrations, comparisons_by_registration, and built_at")
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        value = build_claim_family(
            **payload, authority_registry=registry,
        )
        write_new_bytes(args.output, canonical_json_bytes(value), "panel validation claim family")
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 3 if "already exists" in str(exc) else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
