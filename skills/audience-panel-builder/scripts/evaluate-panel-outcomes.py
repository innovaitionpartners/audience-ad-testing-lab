#!/usr/bin/env python3
"""Evaluate held-out ordering JSON and issue a claim only when supported."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from audience_panel_builder.common import ContractError, canonical_json_bytes, write_new_bytes
from audience_panel_builder.population.validation.contracts import (
    approve_preregistration_design,
    load_trusted_authority_registry,
    read_protected_authority_secret,
)
from audience_panel_builder.population.validation.evaluation import (
    evaluate_held_out_ordering,
    issue_tier4_claim,
)


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--comparison", required=True, action="append", type=Path)
    parser.add_argument("--claim-family", required=True, type=Path)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--evaluation-output", required=True, type=Path)
    parser.add_argument("--claim-output", type=Path)
    parser.add_argument("--claim-expires-at")
    parser.add_argument("--authority-root", required=True, type=Path)
    parser.add_argument("--authority-index", required=True, type=Path)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    args = parser.parse_args(argv)
    inputs = [args.registration, *args.comparison, args.claim_family]
    outputs = [args.evaluation_output] + ([args.claim_output] if args.claim_output is not None else [])
    if (
        len({path.resolve() for path in outputs}) != len(outputs)
        or any(output.resolve() == source.resolve() for output in outputs for source in inputs)
        or any(path.exists() for path in outputs)
    ):
        print("output collision", file=sys.stderr)
        return 3
    if len({path.resolve() for path in args.comparison}) != len(args.comparison):
        print("duplicate comparison input", file=sys.stderr)
        return 7
    if (args.claim_output is None) != (args.claim_expires_at is None):
        parser.error("--claim-output and --claim-expires-at must be supplied together")
    try:
        registration = _load(args.registration)
        if not isinstance(registration, dict):
            raise ContractError("registration must be a JSON object")
        approval = registration.get("approval")
        if not isinstance(approval, dict):
            raise ContractError("registration approval receipt is missing")
        root_sha = _file_sha256(args.authority_root)
        index_sha = _file_sha256(args.authority_index)
        if (
            approval.get("authority_root_sha256") != root_sha
            or approval.get("authority_index_sha256") != index_sha
        ):
            raise ContractError(
                "trusted authority root/index do not match the packaged approval receipt"
            )
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        approved_registration, capability = approve_preregistration_design(
            registration,
            authority_registry=registry,
            authority_id=str(registration["registered_by"]),
        )
        if approved_registration != registration:
            raise ContractError(
                "packaged registration is not the exact immutable approved design"
            )
        evaluation = evaluate_held_out_ordering(
            registration=registration,
            comparisons=[_load(path) for path in args.comparison],
            claim_family=_load(args.claim_family),
            evaluated_at=args.evaluated_at,
            design_approval=capability,
            authority_registry=registry,
        )
        status = evaluation["decision"]["status"]
        exits = {"tier4_supported": 0, "tier4_not_supported": 5, "evaluated_with_limitations": 6, "invalid": 7}
        if status == "tier4_supported" and args.claim_output is not None:
            if args.claim_output.exists():
                print("output collision", file=sys.stderr)
                return 3
            claim = issue_tier4_claim(
                evaluation=evaluation, issued_at=args.evaluated_at,
                expires_at=args.claim_expires_at,
                design_approval=capability,
                authority_registry=registry,
            )
        write_new_bytes(args.evaluation_output, canonical_json_bytes(evaluation), "held-out evaluation")
        if status == "tier4_supported" and args.claim_output is not None:
            write_new_bytes(args.claim_output, canonical_json_bytes(claim), "Tier 4 claim")
        return exits[status]
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 3 if "already exists" in str(exc) else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
