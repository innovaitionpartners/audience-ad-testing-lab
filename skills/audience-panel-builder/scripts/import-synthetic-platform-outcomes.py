#!/usr/bin/env python3
"""Normalize one exact fictional platform export into canonical observations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
)
from audience_panel_builder.population.experimental_calibration.adapters import (  # noqa: E402
    SUPPORTED_PLATFORMS,
    normalize_platform_export,
)


class UnsafeOutputPath(ContractError):
    """An output cannot be created without risking replacement or aliasing."""


_SYSTEM_PATH_ALIASES = {Path("/etc"), Path("/tmp"), Path("/var")}


def _json_file(path_value: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must be a readable UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _write_new_output(path_value: str, payload: bytes) -> None:
    path = Path(path_value).absolute()
    if path.exists() or path.is_symlink():
        raise UnsafeOutputPath(f"output already exists or is a symlink: {path}")
    for ancestor in path.parents:
        if (
            ancestor not in _SYSTEM_PATH_ALIASES
            and ancestor.exists()
            and ancestor.is_symlink()
        ):
            raise UnsafeOutputPath(
                f"output has a symlinked ancestor: {ancestor}"
            )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except FileExistsError as exc:
        raise UnsafeOutputPath(f"output already exists: {path}") from exc
    except OSError as exc:
        raise UnsafeOutputPath(f"unsafe output path '{path}': {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True, choices=sorted(SUPPORTED_PLATFORMS))
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--study-manifest", required=True)
    parser.add_argument("--attribute-registry", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw_bytes = Path(args.input).read_bytes()
        observations = normalize_platform_export(
            platform=args.platform,
            raw_export_bytes=raw_bytes,
            source_sha256=args.source_sha256,
            study_manifest=_json_file(args.study_manifest, "study manifest"),
            creative_attribute_registry=_json_file(
                args.attribute_registry,
                "creative attribute registry",
            ),
        )
        _write_new_output(args.output, canonical_json_bytes(observations))
        return 0
    except UnsafeOutputPath as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ContractError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
