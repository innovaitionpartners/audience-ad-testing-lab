#!/usr/bin/env python3
"""Build a deterministic Ad Testing Lab audience research package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audience_lab.audience_package import (
    DEFAULT_GENERATOR_VERSION, PackageSafetyError, PackageValidationError,
    build_audience_package,
)
from audience_lab.audience_research import AudienceResearchValidationError


class ArgumentParseError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParseError(message)


def main() -> int:
    parser = JsonArgumentParser(
        description="Build a validated, immutable Ad Testing Lab audience research package."
    )
    parser.add_argument(
        "--brief", required=True, type=Path,
        help="Approved persona-research-brief JSON file.",
    )
    parser.add_argument(
        "--panel", required=True, type=Path,
        help="Saved audience panel JSON file that matches the approved brief.",
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path,
        help="New directory for the report, sources CSV, manifest, and reusable ZIP.",
    )
    parser.add_argument(
        "--generator-version", default=DEFAULT_GENERATOR_VERSION,
        help="Supported package generator version (default: %(default)s).",
    )
    try:
        args = parser.parse_args()
        brief = json.loads(args.brief.read_text(encoding="utf-8"))
        panel = json.loads(args.panel.read_text(encoding="utf-8"))
        result = build_audience_package(brief, panel, args.output_dir, generator_version=args.generator_version)
        payload = result.to_dict()
        code = 0
    except PackageSafetyError as exc:
        payload, code = {"status": "error", "error": "package_safety", "message": str(exc)}, 6
    except ArgumentParseError as exc:
        payload, code = {"status": "error", "error": "arguments", "message": str(exc)}, 2
    except (AudienceResearchValidationError, PackageValidationError, json.JSONDecodeError, UnicodeError, OSError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
