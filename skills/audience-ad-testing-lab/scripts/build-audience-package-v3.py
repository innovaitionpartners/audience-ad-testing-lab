#!/usr/bin/env python3
"""Build one deterministic Audience Panel Builder v3 package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_lab.audience_package import PackageSafetyError, PackageValidationError
from audience_lab.audience_package_v3 import build_audience_package_v3


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"status": "error", "error": "arguments", "message": message}, sort_keys=True))
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _JsonArgumentParser(add_help=False)
    for option in (
        "brief", "panel", "population-frame", "composition", "validity", "workflow-state",
        "report-inputs", "report", "report-manifest", "source-inventory", "verbatim-inventory", "audit",
    ):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--migration-provenance", type=Path)
    parser.add_argument("--source-v2-package", type=Path)
    parser.add_argument("--authorized-runtime-authority", type=Path)
    parser.add_argument("--panel-review-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--help", action="help")
    args = parser.parse_args(argv)
    try:
        inputs = {
                "brief": args.brief, "panel": args.panel,
                "population_frame": args.population_frame, "composition": args.composition,
                "validity": args.validity, "workflow_state": args.workflow_state,
                "report_inputs": args.report_inputs, "report": args.report,
                "report_manifest": args.report_manifest, "source_inventory": args.source_inventory,
                "verbatim_inventory": args.verbatim_inventory, "audit": args.audit,
            }
        if (args.migration_provenance is None) != (
            args.source_v2_package is None
        ):
            parser.error(
                "--migration-provenance and --source-v2-package must be supplied together"
            )
        if args.migration_provenance is not None:
            inputs.update(
                migration_provenance=args.migration_provenance,
                source_v2_package=args.source_v2_package,
            )
        if args.authorized_runtime_authority is not None:
            if args.migration_provenance is not None:
                parser.error(
                    "--authorized-runtime-authority cannot be combined with migration inputs"
                )
            inputs["authorized_runtime_authority"] = (
                args.authorized_runtime_authority
            )
        inputs["panel_review_manifest"] = args.panel_review_manifest
        result = build_audience_package_v3(
            inputs=inputs,
            output_dir=args.output_dir,
        )
    except (PackageSafetyError, PackageValidationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": "validation", "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
