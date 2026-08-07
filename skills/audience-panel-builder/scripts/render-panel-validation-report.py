#!/usr/bin/env python3
"""Render a self-contained marketer-first Tier 4 validation report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from audience_panel_builder.population.validation.reporting import (  # noqa: E402
    build_validation_report_payload,
    render_validation_report,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)


def _document(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--claim", type=Path)
    parser.add_argument("--library-root", type=Path)
    parser.add_argument("--validation-package", type=Path)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        payload = build_validation_report_payload(
            registration=_document(args.registration),
            evaluation=_document(args.evaluation),
            claim=_document(args.claim) if args.claim else None,
            library_root=args.library_root,
            validation_package_path=args.validation_package,
            as_of=args.as_of,
            authority_registry=registry,
        )
        print(render_validation_report(payload=payload, template_path=args.template, output_path=args.output))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
