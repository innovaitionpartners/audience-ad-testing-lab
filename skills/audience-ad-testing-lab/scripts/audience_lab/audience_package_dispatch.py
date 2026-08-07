"""Exact schema/generator dispatch for supported audience package archives."""

from __future__ import annotations

import json
from pathlib import Path

from .audience_package import (
    PACKAGE_SCHEMA_VERSION,
    PackageValidationError,
    validate_package_archive,
)
from .audience_package_v3 import (
    GENERATOR_VERSION_V3,
    PACKAGE_SCHEMA_VERSION_V3,
    _validate_package_archive_v3_snapshot,
    read_v3_archive_manifest,
)


def validate_supported_audience_package(package_path: Path) -> dict[str, object]:
    """Dispatch after safely reading exactly the manifest, before all members."""

    archive_bytes, manifest_bytes = read_v3_archive_manifest(package_path)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError("package manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise PackageValidationError("package manifest must be an object")
    route = (manifest.get("schema_version"), manifest.get("generator_version"))
    if route == (PACKAGE_SCHEMA_VERSION, "1.0.0"):
        return validate_package_archive(archive_bytes)
    if route == (PACKAGE_SCHEMA_VERSION_V3, GENERATOR_VERSION_V3):
        return _validate_package_archive_v3_snapshot(archive_bytes)
    raise PackageValidationError("unsupported package schema or generator")


__all__ = ["validate_supported_audience_package"]
