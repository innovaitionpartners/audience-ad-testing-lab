#!/usr/bin/env python3
"""Validate an approved Audience Data Lab performance handoff."""

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
    args = parser.parse_args()
    try:
        payload = json.loads(args.handoff.read_text(encoding="utf-8"))
        handoff = validate_handoff(payload)
        if handoff["schema_version"] != "audience-performance-evidence-v1":
            raise ContractError(
                "calibration requires audience-performance-evidence-v1"
            )
        if handoff["status"] != "approved":
            raise ContractError("performance evidence must be approved")
        if "ad_test_calibration" not in handoff["allowed_uses"]:
            raise ContractError(
                "performance evidence does not authorize ad_test_calibration"
            )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
