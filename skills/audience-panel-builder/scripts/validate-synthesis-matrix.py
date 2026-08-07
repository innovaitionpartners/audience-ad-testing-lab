#!/usr/bin/env python3
"""Validate an evidence-linked audience research synthesis matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_panel_builder.common import ContractError
from audience_panel_builder.synthesis import validate_synthesis_matrix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("finding_support", type=Path)
    parser.add_argument("synthesis_matrix", type=Path)
    args = parser.parse_args()
    try:
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        support = json.loads(
            args.finding_support.read_text(encoding="utf-8")
        )
        matrix = json.loads(
            args.synthesis_matrix.read_text(encoding="utf-8")
        )
        validate_synthesis_matrix(matrix, ledger, support)
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
