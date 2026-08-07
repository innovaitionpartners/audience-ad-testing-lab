#!/usr/bin/env python3
"""Validate an approved aggregate handoff from Audience Data Lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SIBLING_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "audience-data-lab" / "scripts"
)
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_data_lab.common import ContractError  # noqa: E402
from audience_data_lab.pipeline import validate_handoff  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("handoff", type=Path)
    parser.add_argument(
        "--expected",
        choices=["first_party", "performance"],
        required=True,
    )
    args = parser.parse_args()
    expected_version = {
        "first_party": "audience-first-party-evidence-v1",
        "performance": "audience-performance-evidence-v1",
    }[args.expected]
    try:
        payload = json.loads(args.handoff.read_text(encoding="utf-8"))
        handoff = validate_handoff(payload)
        if handoff["schema_version"] != expected_version:
            raise ContractError(
                f"expected {expected_version}, got {handoff['schema_version']}"
            )
        if handoff["status"] != "approved":
            raise ContractError("Audience Data Lab handoff must be approved")
        if "audience_panel_research" not in handoff["allowed_uses"]:
            raise ContractError(
                "Audience Data Lab handoff does not authorize audience_panel_research"
            )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
