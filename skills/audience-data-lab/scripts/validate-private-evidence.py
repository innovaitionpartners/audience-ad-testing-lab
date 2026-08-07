#!/usr/bin/env python3
"""Validate an Audience Data Lab aggregate handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_data_lab.common import ContractError
from audience_data_lab.pipeline import validate_handoff


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("handoff")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.handoff).read_text(encoding="utf-8"))
        validate_handoff(payload)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
