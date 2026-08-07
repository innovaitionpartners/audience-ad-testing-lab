#!/usr/bin/env python3
"""Prepare a privacy-reviewed aggregate evidence handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_data_lab.common import ContractError
# Import the two processing engines at the public boundary so an incomplete
# standalone package fails before any private file is opened.
from audience_data_lab import modeling as _modeling_engine  # noqa: F401
from audience_data_lab import tabular as _tabular_loader  # noqa: F401
from audience_data_lab.pipeline import prepare_private_evidence, write_outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("intake")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    try:
        intake = json.loads(Path(args.intake).read_text(encoding="utf-8"))
        audit, handoff, report = prepare_private_evidence(args.input, intake)
        paths = write_outputs(args.output_dir, audit, handoff, report)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(paths, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
