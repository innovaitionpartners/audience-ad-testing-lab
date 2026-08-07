#!/usr/bin/env python3
"""Materialize canonical response and provider-attempt lineage for one run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_lab.contracts import load_json
from audience_lab.lineage import materialize_workflow_lineage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_output", type=Path)
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = materialize_workflow_lineage(
            load_json(args.workflow_output),
            load_json(args.source_manifest),
            args.run_dir,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"accepted_response_records={payload['usage']['accepted_response_records']} "
        f"total_model_calls={payload['usage']['total_model_calls']} "
        f"run_dir={args.run_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
