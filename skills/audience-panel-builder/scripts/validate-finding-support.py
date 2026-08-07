#!/usr/bin/env python3
"""Validate exact finding-to-evidence-item support."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_panel_builder.common import ContractError
from audience_panel_builder.evidence import validate_finding_support


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("support", type=Path)
    args = parser.parse_args()
    try:
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        support = json.loads(args.support.read_text(encoding="utf-8"))
        validate_finding_support(support, ledger)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
