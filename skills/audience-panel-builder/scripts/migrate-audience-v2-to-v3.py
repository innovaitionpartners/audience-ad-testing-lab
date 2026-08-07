#!/usr/bin/env python3
"""Migrate one validated v2 panel package to five honest Tier 1 v3 documents."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
)
from audience_panel_builder.population.migration import (  # noqa: E402
    migrate_v2_to_v3,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-package", required=True, type=Path)
    parser.add_argument("--new-panel-version", required=True)
    parser.add_argument("--migrated-at", required=True)
    parser.add_argument("--migrated-by", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = migrate_v2_to_v3(
            v2_package_path=args.v2_package,
            new_panel_version=args.new_panel_version,
            migrated_at=args.migrated_at,
            migrated_by=args.migrated_by,
            output_dir=args.output_dir,
        )
        code = 0
    except ContractError as exc:
        collision = "already exists" in str(exc)
        payload, code = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": str(exc),
        }, 3 if collision else 2
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        payload, code = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
