#!/usr/bin/env python3
"""Build one exact audience evidence ledger from normalized batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.evidence import build_evidence_ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("batches", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        batches = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in args.batches
        ]
        ledger = build_evidence_ledger(args.plan_id, batches)
        write_new_bytes(
            args.output,
            canonical_json_bytes(ledger),
            "audience evidence ledger",
        )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
