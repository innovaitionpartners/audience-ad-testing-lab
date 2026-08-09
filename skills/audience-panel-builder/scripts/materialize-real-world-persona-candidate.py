#!/usr/bin/env python3
"""Diagnose C1 misses and materialize one experimental C2 panel candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path[:0] = [str(CURRENT_SCRIPTS), str(SIBLING_SCRIPTS)]

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.experimental_calibration.real_world import (  # noqa: E402
    authenticate_c1_validation_packages,
    build_real_world_persona_behavior_proposal,
    diagnose_real_world_persona_behavior,
    materialize_real_world_candidate,
    publish_real_world_candidate_bundle,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)
from audience_panel_builder.population.validation.package import (  # noqa: E402
    read_authenticated_panel_snapshot,
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
    parser.add_argument("--base-panel-package", required=True, type=Path)
    parser.add_argument(
        "--diagnostic-validation-package",
        required=True,
        action="append",
        type=Path,
    )
    parser.add_argument("--attribute-registry", required=True, type=Path)
    parser.add_argument("--alternative-causes", required=True, type=Path)
    parser.add_argument("--target-persona-id", required=True)
    parser.add_argument("--target-segment-id", required=True)
    parser.add_argument("--diagnosis-id", required=True)
    parser.add_argument("--diagnosed-at", required=True)
    parser.add_argument("--proposal-id", required=True)
    parser.add_argument("--proposed-at", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        registry_capability = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file
            ),
        )
        base_binding, _validation, base_panel = read_authenticated_panel_snapshot(
            args.base_panel_package
        )
        packages = authenticate_c1_validation_packages(
            args.diagnostic_validation_package,
            authority_registry=registry_capability,
        )
        attribute_registry = _load(
            args.attribute_registry, "creative attribute registry"
        )
        alternative_causes = _load(
            args.alternative_causes, "alternative-cause review"
        )
        diagnosis = diagnose_real_world_persona_behavior(
            base_panel=base_panel,
            base_panel_binding=base_binding,
            validated_packages=packages,
            attribute_registry=attribute_registry,
            alternative_causes=alternative_causes,
            target_persona_id=args.target_persona_id,
            target_segment_id=args.target_segment_id,
            diagnosis_id=args.diagnosis_id,
            diagnosed_at=args.diagnosed_at,
        )
        proposal = build_real_world_persona_behavior_proposal(
            base_panel=base_panel,
            diagnosis=diagnosis,
            proposal_id=args.proposal_id,
            proposed_at=args.proposed_at,
        )
        materialized = materialize_real_world_candidate(
            base_panel=base_panel,
            proposal=proposal,
            candidate_id=args.candidate_id,
            candidate_version=args.candidate_version,
            created_at=args.created_at,
        )
        output = publish_real_world_candidate_bundle(
            materialized=materialized,
            diagnosis=diagnosis,
            attribute_registry=attribute_registry,
            alternative_causes=alternative_causes,
            output_dir=args.output_dir,
        )
        payload = {
            "status": "experimental_candidate_created",
            "output_dir": str(output),
            "candidate_id": args.candidate_id,
            "candidate_version": args.candidate_version,
            "candidate_binding_sha256": materialized["candidate_binding"][
                "candidate_binding_sha256"
            ],
            "registration_permitted": False,
        }
        code = 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        payload = {"status": "error", "error": "validation", "message": str(exc)}
        code = 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
