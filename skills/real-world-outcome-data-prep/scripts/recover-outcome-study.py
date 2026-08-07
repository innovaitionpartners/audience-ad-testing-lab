#!/usr/bin/env python3
"""Recover one unambiguous authenticated outcome-preparation transaction."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

from outcome_data_prep.common import ContractError, canonical_json_bytes
from outcome_data_prep.publication import ImportConflict
from outcome_data_prep.workflow import recover_study_from_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--authority-registry", type=Path, required=True)
    parser.add_argument("--authority-secret-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        state = recover_study_from_paths(
            args.study_root,
            authority_registry=args.authority_registry,
            authority_secret_file=args.authority_secret_file,
        )
        sys.stdout.buffer.write(canonical_json_bytes(asdict(state)))
        return 0
    except ImportConflict as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ContractError, OSError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
