#!/usr/bin/env python3
"""Build the exact C2 registration proposal after fresh held-out validation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path[:0] = [str(CURRENT_SCRIPTS), str(SIBLING_SCRIPTS)]

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.population.experimental_calibration.real_world import (  # noqa: E402
    build_registration_proposal,
    replay_real_world_candidate_bundle,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)
from audience_panel_builder.population.validation.package import (  # noqa: E402
    read_authenticated_panel_snapshot,
    validate_validation_package,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-bundle", required=True, type=Path)
    parser.add_argument("--base-panel-package", required=True, type=Path)
    parser.add_argument(
        "--diagnostic-validation-package",
        required=True,
        action="append",
        type=Path,
    )
    parser.add_argument("--candidate-panel-package", required=True, type=Path)
    parser.add_argument("--fresh-validation-package", required=True, type=Path)
    parser.add_argument("--registered-at", required=True)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        authority = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file
            ),
        )
        candidate, _diagnosis = replay_real_world_candidate_bundle(
            bundle_dir=args.candidate_bundle,
            base_panel_package=args.base_panel_package,
            diagnostic_validation_packages=args.diagnostic_validation_package,
            authority_registry=authority,
        )
        candidate_binding, _validation, packaged_panel = (
            read_authenticated_panel_snapshot(args.candidate_panel_package)
        )
        if packaged_panel != candidate["candidate_panel"]:
            raise ContractError(
                "candidate package panel does not match the materialized candidate"
            )
        fresh = validate_validation_package(
            args.fresh_validation_package,
            authority_registry=authority,
        )
        proposal = build_registration_proposal(
            candidate=candidate,
            candidate_package_binding=candidate_binding,
            fresh_validation=fresh,
            registered_at=args.registered_at,
        )
        write_new_bytes(
            args.output,
            canonical_json_bytes(proposal),
            "C2 registration proposal",
        )
        payload = {
            "status": "awaiting_explicit_human_approval",
            "registration_proposal_sha256": proposal[
                "registration_proposal_sha256"
            ],
            "approval_scope": "calibration",
            "output": str(args.output),
        }
        code = 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        payload = {"status": "error", "error": "validation", "message": str(exc)}
        code = 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
