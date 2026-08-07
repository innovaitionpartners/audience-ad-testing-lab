#!/usr/bin/env python3
"""Build one canonical reusable panel-composition plan."""

from __future__ import annotations

import argparse
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
from audience_panel_builder.population.composition import (  # noqa: E402
    build_composition_plan,
)


def _load(path: Path, *, array: bool) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path} is not readable JSON") from exc
    expected = list if array else dict
    if not isinstance(value, expected):
        kind = "array" if array else "object"
        raise ContractError(f"{path} must contain a JSON {kind}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-frame", required=True, type=Path)
    parser.add_argument("--structural-findings", required=True, type=Path)
    parser.add_argument("--overlay-findings", required=True, type=Path)
    parser.add_argument("--profile-specs", required=True, type=Path)
    parser.add_argument("--requested-tier", required=True)
    parser.add_argument("--evidence-basis", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--plan-version", required=True)
    parser.add_argument("--built-at", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = build_composition_plan(
            population_frame=_load(args.population_frame, array=False),
            structural_findings=_load(args.structural_findings, array=True),
            overlay_findings=_load(args.overlay_findings, array=True),
            supported_profile_specs=_load(args.profile_specs, array=True),
            requested_tier=args.requested_tier,
            evidence_basis=args.evidence_basis,
            plan_id=args.plan_id,
            plan_version=args.plan_version,
            built_at=args.built_at,
        )
        write_new_bytes(
            args.output,
            canonical_json_bytes(plan),
            "panel composition output",
        )
        payload, code = {
            "status": "built",
            "output": str(args.output),
        }, 0
    except ContractError as exc:
        collision = "already exists" in str(exc)
        payload, code = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": str(exc),
        }, 3 if collision else 2
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        payload, code = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
