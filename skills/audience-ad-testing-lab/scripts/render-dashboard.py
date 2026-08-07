#!/usr/bin/env python3
"""Compile one Audience Ad Testing Lab run into a self-contained HTML dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from audience_lab.dashboard import DashboardInputError, render_dashboard

PANEL_BUILDER_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "audience-panel-builder" / "scripts"
)
sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--template",
        type=Path,
        default=ROOT / "assets" / "dashboard-template.html",
        help="HTML template (defaults to the bundled dashboard template)",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-registry", type=Path)
    parser.add_argument("--authority-secret-file", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        registry = None
        if (
            args.authority_registry is not None
            or args.authority_secret_file is not None
        ):
            if (
                args.authority_registry is None
                or args.authority_secret_file is None
            ):
                raise DashboardInputError(
                    "both Tier 4 authority arguments are required together"
                )
            registry = load_trusted_authority_registry(
                args.authority_registry,
                authority_secret=read_protected_authority_secret(
                    args.authority_secret_file,
                ),
            )
        output = render_dashboard(
            run_dir=args.run_dir,
            template_path=args.template,
            output_path=args.output,
            authority_registry=registry,
        )
    except (OSError, ValueError) as exc:
        print(f"dashboard render failed: {exc}", file=sys.stderr)
        return 2
    print(f"dashboard={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
