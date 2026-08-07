#!/usr/bin/env python3
"""Return one canonical semantic verdict for Workflow legacy-v2 jobs."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import sys
import tempfile
from typing import Any, Mapping
import zipfile


PREFLIGHT_SCHEMA_VERSION = "legacy-v2-workflow-semantic-preflight-v1"
SEMANTIC_BUNDLE_SCHEMA_VERSION = "workflow-v2-semantic-bundle-v1"
SEMANTIC_BUNDLE_FILENAME = "workflow-v2-semantic-bundle.b85"
SEMANTIC_BUNDLE_SHA256 = (
    "83cc88620f415d6ff9c0974b25f18342cae21b423fdaaafc9bce90052b6302e5"
)
SEMANTIC_MANIFEST_FILENAME = "semantic-manifest.json"
SEMANTIC_ENTRY_MODULE = "audience_lab.legacy_v2_origin"
_MAX_SEMANTIC_ARCHIVE_BYTES = 2 * 1024 * 1024
_MAX_SEMANTIC_MEMBER_BYTES = 1024 * 1024
_MAX_SEMANTIC_TOTAL_BYTES = 4 * 1024 * 1024
_MAX_SEMANTIC_MEMBERS = 64
_CANDIDATE_KEYS = {
    "study_id",
    "method",
    "record_type",
    "synthetic_replicate_jobs",
}


def _canonical_json_bytes(value: Any) -> bytes:
    def language_neutral(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, Mapping):
            return {
                key: language_neutral(nested)
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [language_neutral(nested) for nested in item]
        return item

    return (
        json.dumps(
            language_neutral(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_canonical_object(
    path: Path,
    field: str,
) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be one real file")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must contain one JSON object")
    if raw != _canonical_json_bytes(payload):
        raise ValueError(f"{field} must use canonical JSON bytes")
    return payload, raw


def _require_isolated_python() -> None:
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.safe_path
    ):
        raise ValueError(
            "semantic preflight requires isolated Python flags -I -S"
        )


def _semantic_bundle_archive() -> bytes:
    helper = Path(__file__)
    if helper.is_symlink() or not helper.is_file():
        raise ValueError("semantic preflight helper must be one real file")
    bundle = helper.resolve().with_name(SEMANTIC_BUNDLE_FILENAME)
    if bundle.is_symlink() or not bundle.is_file():
        raise ValueError("semantic preflight bundle must be one real file")
    encoded = bundle.read_bytes()
    if _sha256(encoded) != SEMANTIC_BUNDLE_SHA256:
        raise ValueError("semantic preflight bundle hash does not match")
    try:
        archive_raw = base64.b85decode(b"".join(encoded.splitlines()))
    except ValueError as exc:
        raise ValueError("semantic preflight bundle encoding is invalid") from exc
    if (
        not archive_raw
        or len(archive_raw) > _MAX_SEMANTIC_ARCHIVE_BYTES
    ):
        raise ValueError("semantic preflight bundle size is invalid")
    return archive_raw


def _semantic_manifest(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if (
        not infos
        or len(infos) > _MAX_SEMANTIC_MEMBERS
        or len(names) != len(set(names))
        or SEMANTIC_MANIFEST_FILENAME not in names
    ):
        raise ValueError("semantic preflight bundle members are invalid")
    total_size = 0
    members: dict[str, bytes] = {}
    for info in infos:
        pure = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (
            info.flag_bits & 0x1
            or info.compress_type != zipfile.ZIP_DEFLATED
            or info.file_size < 1
            or info.file_size > _MAX_SEMANTIC_MEMBER_BYTES
            or info.compress_size > _MAX_SEMANTIC_MEMBER_BYTES
            or pure.is_absolute()
            or "\\" in info.filename
            or any(part in {"", ".", ".."} for part in pure.parts)
            or stat.S_IFMT(mode) != stat.S_IFREG
            or stat.S_IMODE(mode) != 0o444
        ):
            raise ValueError(
                "semantic preflight bundle member metadata is invalid"
            )
        total_size += info.file_size
        if total_size > _MAX_SEMANTIC_TOTAL_BYTES:
            raise ValueError(
                "semantic preflight bundle expands beyond its limit"
            )
        members[info.filename] = archive.read(info)

    manifest_raw = members.pop(SEMANTIC_MANIFEST_FILENAME)
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "semantic preflight bundle manifest is invalid"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "entry_module",
            "files",
            "schema_version",
            "semantic_source_sha256",
        }
        or manifest.get("schema_version")
        != SEMANTIC_BUNDLE_SCHEMA_VERSION
        or manifest.get("entry_module") != SEMANTIC_ENTRY_MODULE
        or manifest_raw != _canonical_json_bytes(manifest)
    ):
        raise ValueError(
            "semantic preflight bundle manifest is not canonical"
        )
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError(
            "semantic preflight bundle manifest files are invalid"
        )
    expected_names: list[str] = []
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"byte_count", "path", "sha256"}
        ):
            raise ValueError(
                "semantic preflight bundle file binding is invalid"
            )
        path = record.get("path")
        digest = record.get("sha256")
        byte_count = record.get("byte_count")
        if (
            not isinstance(path, str)
            or not path.startswith("audience_lab/")
            or not path.endswith(".py")
            or path not in members
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 1
            or digest != _sha256(members[path])
            or byte_count != len(members[path])
        ):
            raise ValueError(
                "semantic preflight bundle file binding does not match"
            )
        expected_names.append(path)
    if (
        expected_names != sorted(expected_names)
        or len(expected_names) != len(set(expected_names))
        or set(expected_names) != set(members)
        or manifest.get("semantic_source_sha256")
        != _sha256(_canonical_json_bytes(records))
    ):
        raise ValueError(
            "semantic preflight bundle dependency closure does not match"
        )
    return manifest, members


@contextmanager
def _isolated_semantic_validator():
    _require_isolated_python()
    archive_raw = _semantic_bundle_archive()
    try:
        with zipfile.ZipFile(io.BytesIO(archive_raw), "r") as archive:
            _semantic_manifest(archive)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ValueError("semantic preflight bundle archive is invalid") from exc

    prior_path = list(sys.path)
    if any(
        name == "audience_lab" or name.startswith("audience_lab.")
        for name in sys.modules
    ):
        raise ValueError(
            "semantic preflight package was loaded before isolation"
        )
    with tempfile.TemporaryDirectory(
        prefix="audience-v2-semantic-bundle-"
    ) as temporary:
        archive_path = Path(temporary) / "semantic-bundle.zip"
        descriptor = os.open(
            archive_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(archive_raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            archive_path.unlink(missing_ok=True)
            raise
        os.chmod(archive_path, 0o400)
        try:
            sys.path.insert(0, str(archive_path))
            from audience_lab.legacy_v2_origin import (
                validate_legacy_v2_producer_record,
            )

            yield validate_legacy_v2_producer_record
        finally:
            sys.path[:] = prior_path
            for name in tuple(sys.modules):
                if name == "audience_lab" or name.startswith("audience_lab."):
                    sys.modules.pop(name, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-jobs", required=True, type=Path)
    parser.add_argument("--producer-record", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        candidate, candidate_raw = _load_canonical_object(
            args.candidate_jobs,
            "candidate jobs",
        )
        if set(candidate) != _CANDIDATE_KEYS:
            raise ValueError("candidate jobs keys are invalid")
        record, record_raw = _load_canonical_object(
            args.producer_record,
            "producer record",
        )
        with _isolated_semantic_validator() as validate:
            validate(
                record,
                candidate,
                record_path=args.producer_record,
            )
        verdict = {
            "candidate_jobs_sha256": _sha256(candidate_raw),
            "producer_record_sha256": _sha256(record_raw),
            "schema_version": PREFLIGHT_SCHEMA_VERSION,
            "status": "valid",
        }
        sys.stdout.buffer.write(_canonical_json_bytes(verdict))
    except (
        ImportError,
        OSError,
        RuntimeError,
        UnicodeError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"legacy-v2 Workflow semantic preflight failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
