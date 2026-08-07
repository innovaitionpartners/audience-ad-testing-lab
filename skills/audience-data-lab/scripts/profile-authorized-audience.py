#!/usr/bin/env python3
"""Create a no-clobber structural profile of authorized audience sources."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from audience_data_lab.authorized_source import profile_authorized_bundle
from audience_data_lab.common import ContractError, canonical_json_bytes, write_new_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-version", required=True)
    parser.add_argument("--profiled-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        profile = profile_authorized_bundle(
            [Path(item) for item in args.inputs],
            profile_id=args.profile_id,
            profile_version=args.profile_version,
            profiled_at=args.profiled_at,
        )
        output = write_new_bytes(args.output, canonical_json_bytes(profile), "source profile output")
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    status = profile["decision"]["status"]
    print(f"{output} {status}")
    if status == "requires_private_aggregation":
        return 4
    if status == "rejected":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
