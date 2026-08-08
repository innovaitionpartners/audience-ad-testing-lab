#!/usr/bin/env python3
"""Generate the closed, deterministic outcome-prep runtime release identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

from outcome_data_prep.runtime_guard import (
    RUNTIME_IDENTITY_EXCLUDED_PATHS,
    closed_runtime_inventory,
    hash_closed_runtime_tree,
)


SCHEMA_VERSION = "outcome-prep-runtime-release-v2"
REPOSITORY = "innovaitionpartners/audience-ad-testing-lab"
RELEASE_VERSION = "0.3.1"


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_relative_path(value: PurePosixPath, *, label: str) -> PurePosixPath:
    if (
        value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
    ):
        raise ValueError(f"{label} must be one canonical relative POSIX path")
    return value


def runtime_file_hashes(
    plugin_root: Path,
    *,
    excluded: set[PurePosixPath] | frozenset[PurePosixPath] = frozenset(),
) -> dict[str, str]:
    """Hash every regular runtime file outside the closed exclusions."""

    return hash_closed_runtime_tree(plugin_root, excluded=excluded)


def build_release_manifest(
    *,
    plugin_root: Path,
    output_relative_path: PurePosixPath,
) -> dict[str, object]:
    output_relative = _validate_relative_path(
        output_relative_path, label="output path"
    )
    files = runtime_file_hashes(
        plugin_root,
        excluded=RUNTIME_IDENTITY_EXCLUDED_PATHS | {output_relative},
    )
    identity = {
        "schema_version": SCHEMA_VERSION,
        "repository": REPOSITORY,
        "release_version": RELEASE_VERSION,
        "files": files,
    }
    return {
        **identity,
        "release_tree_sha256": sha256_bytes(canonical_json_bytes(identity)),
    }


def write_release_manifest(
    *,
    plugin_root: Path,
    output: Path,
) -> dict[str, object]:
    root = Path(plugin_root).expanduser().resolve(strict=True)
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = output_path.resolve(strict=False)
    parent = output_path.parent.resolve(strict=True)
    output_path = parent / output_path.name
    try:
        parent.relative_to(root)
        output_relative = PurePosixPath(output_path.relative_to(root).as_posix())
    except ValueError as exc:
        raise ValueError("output path must be inside the plugin root") from exc
    _validate_relative_path(output_relative, label="output path")
    if output_path.is_symlink():
        raise ValueError("output path must not be a symlink")

    manifest = build_release_manifest(
        plugin_root=root,
        output_relative_path=output_relative,
    )
    payload = canonical_json_bytes(manifest)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    write_release_manifest(
        plugin_root=args.plugin_root,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
