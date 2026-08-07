#!/usr/bin/env python3
"""Build or verify the isolated Workflow legacy-v2 semantic bundle."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
from typing import Any, Mapping
import zipfile


BUNDLE_SCHEMA_VERSION = "workflow-v2-semantic-bundle-v1"
BUNDLE_FILENAME = "workflow-v2-semantic-bundle.b85"
MANIFEST_FILENAME = "semantic-manifest.json"
ENTRY_MODULE = "audience_lab.legacy_v2_origin"
PACKAGE_DIRECTORY = Path(__file__).resolve().parent / "audience_lab"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / BUNDLE_FILENAME
PACKAGE_INITIALIZER = (
    b'"""Isolated Workflow legacy-v2 semantic implementation."""\n'
)
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = 0o100444 << 16
_ENCODED_LINE_LENGTH = 100


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative_module_dependencies(
    source: bytes,
    *,
    module_name: str,
) -> set[str]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=module_name)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"semantic module {module_name} is not valid UTF-8 Python"
        ) from exc
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        candidates = (
            [node.module.split(".", 1)[0]]
            if node.module
            else [alias.name.split(".", 1)[0] for alias in node.names]
        )
        for candidate in candidates:
            if (
                candidate
                and (PACKAGE_DIRECTORY / f"{candidate}.py").is_file()
            ):
                dependencies.add(candidate)
    return dependencies


def _source_files() -> dict[str, bytes]:
    pending = ["legacy_v2_origin"]
    discovered: set[str] = set()
    files = {"audience_lab/__init__.py": PACKAGE_INITIALIZER}
    while pending:
        module = pending.pop()
        if module in discovered:
            continue
        path = PACKAGE_DIRECTORY / f"{module}.py"
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"semantic dependency audience_lab.{module} is unavailable"
            )
        raw = path.read_bytes()
        discovered.add(module)
        files[f"audience_lab/{module}.py"] = raw
        pending.extend(
            sorted(
                _relative_module_dependencies(
                    raw,
                    module_name=f"audience_lab.{module}",
                )
                - discovered,
                reverse=True,
            )
        )
    return dict(sorted(files.items()))


def _manifest(files: Mapping[str, bytes]) -> dict[str, Any]:
    records = [
        {
            "byte_count": len(raw),
            "path": path,
            "sha256": _sha256(raw),
        }
        for path, raw in sorted(files.items())
    ]
    return {
        "entry_module": ENTRY_MODULE,
        "files": records,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "semantic_source_sha256": _sha256(
            _canonical_json_bytes(records)
        ),
    }


def _archive_member(path: str, raw: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(path, date_time=_ARCHIVE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = _ARCHIVE_MODE
    return info, raw


def _build_encoded_bundle(files: Mapping[str, bytes]) -> bytes:
    manifest_raw = _canonical_json_bytes(_manifest(files))
    stream = io.BytesIO()
    with zipfile.ZipFile(
        stream,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for path, raw in sorted(files.items()):
            archive.writestr(*_archive_member(path, raw))
        archive.writestr(
            *_archive_member(MANIFEST_FILENAME, manifest_raw)
        )
    encoded = base64.b85encode(stream.getvalue())
    return (
        b"\n".join(
            encoded[index : index + _ENCODED_LINE_LENGTH]
            for index in range(0, len(encoded), _ENCODED_LINE_LENGTH)
        )
        + b"\n"
    )


def _load_existing_bundle(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("semantic bundle must be one real file")
    raw = path.read_bytes()
    try:
        archive_raw = base64.b85decode(b"".join(raw.splitlines()))
    except ValueError as exc:
        raise ValueError("semantic bundle encoding is invalid") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(archive_raw), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError(
                    "semantic bundle contains duplicate archive members"
                )
            members = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("semantic bundle archive is invalid") from exc
    manifest_raw = members.pop(MANIFEST_FILENAME, None)
    if manifest_raw is None:
        raise ValueError("semantic bundle manifest is missing")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("semantic bundle manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest_raw != _canonical_json_bytes(manifest)
    ):
        raise ValueError("semantic bundle manifest is not canonical")
    return manifest, members


def _verify_existing_bundle(path: Path) -> None:
    expected_files = _source_files()
    expected_manifest = _manifest(expected_files)
    actual_manifest, actual_files = _load_existing_bundle(path)
    if actual_manifest != expected_manifest:
        raise ValueError(
            "semantic bundle manifest does not match the current dependency "
            "closure"
        )
    if actual_files != expected_files:
        raise ValueError(
            "semantic bundle modules do not match the current dependency "
            "closure"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        output = args.output.expanduser().absolute()
        if args.write:
            raw = _build_encoded_bundle(_source_files())
            if output.exists() or output.is_symlink():
                output.unlink()
            output.write_bytes(raw)
            print(
                f"semantic_bundle_sha256={_sha256(raw)} "
                f"semantic_bundle_bytes={len(raw)}"
            )
        else:
            _verify_existing_bundle(output)
            raw = output.read_bytes()
            print(
                f"semantic_bundle_integrity=passed "
                f"semantic_bundle_sha256={_sha256(raw)}"
            )
    except (OSError, TypeError, ValueError, zipfile.LargeZipFile) as exc:
        print(f"semantic bundle operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
