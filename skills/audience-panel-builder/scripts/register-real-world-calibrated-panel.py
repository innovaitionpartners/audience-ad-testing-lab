#!/usr/bin/env python3
"""Register an experimental C2 panel only after fresh evidence and approval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path[:0] = [str(CURRENT_SCRIPTS), str(SIBLING_SCRIPTS)]

from audience_lab.audience_library import (  # noqa: E402
    ImmutableVersionConflict,
    LibraryLockError,
    LibrarySafetyError,
)
from audience_lab.audience_package import (  # noqa: E402
    PackageSafetyError,
    PackageValidationError,
)
from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.experimental_calibration.real_world import (  # noqa: E402
    build_registration_proposal,
    register_real_world_calibrated_package,
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


def _load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must be readable JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain an object")
    return value


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
    parser.add_argument("--registration-proposal", required=True, type=Path)
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--registered-at", required=True)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    parser.add_argument("--library-root", required=True, type=Path)
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
        replayed_proposal = build_registration_proposal(
            candidate=candidate,
            candidate_package_binding=candidate_binding,
            fresh_validation=fresh,
            registered_at=args.registered_at,
        )
        supplied_proposal = _load(
            args.registration_proposal, "registration proposal"
        )
        if supplied_proposal != replayed_proposal:
            raise ContractError(
                "registration proposal does not byte-match fresh evidence replay"
            )
        result = register_real_world_calibrated_package(
            args.candidate_panel_package,
            library_root=args.library_root,
            registration_proposal=replayed_proposal,
            workflow_state=_load(args.workflow_state, "workflow state"),
        )
        payload = {
            "status": result["status"],
            "panel": result["panel"],
            "experimental_claim_boundary": replayed_proposal["claim_boundary"],
        }
        code = 0
    except ImmutableVersionConflict as exc:
        payload = {
            "status": "error",
            "error": "immutable_version_conflict",
            "message": str(exc),
        }
        code = 3
    except (LibrarySafetyError, PackageSafetyError) as exc:
        payload = {
            "status": "error",
            "error": "package_safety",
            "message": str(exc),
        }
        code = 6
    except LibraryLockError as exc:
        payload = {
            "status": "error",
            "error": "library_lock",
            "message": str(exc),
        }
        code = 7
    except (
        ContractError,
        PackageValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        payload = {"status": "error", "error": "validation", "message": str(exc)}
        code = 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
