#!/usr/bin/env python3
"""Create a deterministic audience research source plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audience_panel_builder.capabilities import validate_capability_inventory
from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.planning import build_source_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan Audience Panel Builder research sources.")
    parser.add_argument("intake", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--capabilities",
        type=Path,
        required=True,
        help="Validated connector-capability-inventory-v1 JSON.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "references" / "source-registry.json",
    )
    try:
        args = parser.parse_args()
        intake = json.loads(args.intake.read_text(encoding="utf-8"))
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        capabilities = validate_capability_inventory(
            json.loads(args.capabilities.read_text(encoding="utf-8"))
        )
        plan = build_source_plan(intake, registry, capabilities)
        write_new_bytes(
            args.output,
            canonical_json_bytes(plan),
            "audience source plan",
        )
        payload, code = {"status": "planned", "output": str(args.output), "plan_id": plan["plan_id"]}, 0
    except (ContractError, json.JSONDecodeError, UnicodeError, OSError, ValueError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
