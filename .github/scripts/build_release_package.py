#!/usr/bin/env python3
"""Build a deterministic, runtime-authenticated plugin archive."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PLUGIN_NAME = "audience-ad-testing-lab"
MANIFEST_PATH = Path(
    "skills/real-world-outcome-data-prep/references/runtime-release-manifest.json"
)
PLUGIN_MANIFESTS = (
    Path(".codex-plugin/plugin.json"),
    Path(".claude-plugin/plugin.json"),
)
EXTRA_PUBLIC_FILES = (Path("README.md"),)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MAX_CLAUDE_UPLOAD_BYTES = 50 * 1024 * 1024


class PackageError(ValueError):
    """Raised when release bytes are incomplete, modified, or unsafe."""


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"Cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"Expected a JSON object: {path}")
    return value


def _validate_relative_path(value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PackageError(f"Unsafe archive path: {value!r}")
    return Path(*pure.parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_release_tree(root: Path) -> tuple[str, tuple[Path, ...]]:
    root = root.resolve(strict=True)
    versions: set[str] = set()
    for relative in PLUGIN_MANIFESTS:
        payload = _load_json(root / relative)
        if payload.get("name") != PLUGIN_NAME:
            raise PackageError(f"Wrong plugin name in {relative.as_posix()}")
        version = payload.get("version")
        if not isinstance(version, str) or not version:
            raise PackageError(f"Missing plugin version in {relative.as_posix()}")
        versions.add(version)
    if len(versions) != 1:
        raise PackageError("Claude and Codex plugin versions do not match")

    release_manifest = _load_json(root / MANIFEST_PATH)
    raw_files = release_manifest.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise PackageError("Runtime release manifest has no file inventory")

    files: set[Path] = set(EXTRA_PUBLIC_FILES)
    for raw_name, raw_digest in raw_files.items():
        if not isinstance(raw_name, str) or not isinstance(raw_digest, str):
            raise PackageError("Runtime release inventory is malformed")
        relative = _validate_relative_path(raw_name)
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise PackageError(f"Missing or unsafe release file: {raw_name}")
        if _sha256(path) != raw_digest:
            raise PackageError(f"Release file hash mismatch: {raw_name}")
        files.add(relative)

    for relative in EXTRA_PUBLIC_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise PackageError(f"Missing public file: {relative.as_posix()}")

    return versions.pop(), tuple(sorted(files, key=lambda item: item.as_posix()))


def _write_archive(root: Path, destination: Path, files: tuple[Path, ...]) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative.as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / relative).read_bytes())


def _validate_archive(path: Path, files: tuple[Path, ...]) -> None:
    expected = [relative.as_posix() for relative in files]
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != expected:
            raise PackageError("Archive inventory does not match release inventory")
        for name in expected:
            if archive.getinfo(name).date_time != ZIP_TIMESTAMP:
                raise PackageError(f"Non-deterministic archive timestamp: {name}")
        corrupt = archive.testzip()
        if corrupt is not None:
            raise PackageError(f"Corrupt archive member: {corrupt}")
    if path.stat().st_size >= MAX_CLAUDE_UPLOAD_BYTES:
        raise PackageError("Archive exceeds Claude's 50 MB custom-plugin limit")


def build_archive(root: Path, output_dir: Path) -> Path:
    root = root.resolve(strict=True)
    output_dir = output_dir.resolve()
    version, files = validate_release_tree(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{PLUGIN_NAME}-plugin-{version}.zip"
    with tempfile.TemporaryDirectory(prefix=".ad-lab-build-", dir=output_dir) as tmp:
        temporary = Path(tmp) / filename
        _write_archive(root, temporary, files)
        _validate_archive(temporary, files)
        destination = output_dir / filename
        os.replace(temporary, destination)
    return destination


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    archive = build_archive(root, root / "dist")
    print(f"{_sha256(archive)}  {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
