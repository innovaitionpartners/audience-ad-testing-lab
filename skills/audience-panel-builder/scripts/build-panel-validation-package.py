#!/usr/bin/env python3
"""Build one no-clobber Tier 4 validation package."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from audience_panel_builder.population.validation.package import (  # noqa: E402
    ValidationPackageError,
    build_validation_package,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-dir", required=True, type=Path)
    parser.add_argument("--panel-package", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.inputs_dir.is_dir() or args.inputs_dir.is_symlink():
            raise ValidationPackageError("inputs directory must be a real directory")
        names = {child.name: child for child in args.inputs_dir.iterdir() if child.is_file() and not child.is_symlink()}
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        output = build_validation_package(
            inputs=names,
            panel_package_path=args.panel_package,
            output_dir=args.output_dir,
            authority_registry=registry,
        )
    except (ValidationPackageError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 3 if "already exists" in str(exc) or "output" in str(exc) and "empty" in str(exc) else 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
