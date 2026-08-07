#!/usr/bin/env python3
"""Apply a user-supplied approval record to a frozen draft handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_data_lab.common import ContractError, canonical_json_bytes, write_new_bytes
from audience_data_lab.pipeline import approve_handoff


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("draft")
    parser.add_argument("approval")
    parser.add_argument("output")
    args = parser.parse_args()
    try:
        draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
        approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        approved = approve_handoff(draft, approval)
        write_new_bytes(
            args.output,
            canonical_json_bytes(approved),
            "approved evidence handoff",
        )
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("approved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
