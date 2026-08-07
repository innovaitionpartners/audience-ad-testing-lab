"""Descriptor-pinned, canonical bindings for producer-written evidence."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from ...common import canonical_json_bytes, sha256_json
from .evidence_errors import ProducerAuthenticationError


LINEAGE_ORDER = (
    "accepted_responses",
    "raw_provider_returns",
    "rejected_attempts",
    "dispatch_audit",
)

_BINDING_KEYS = frozenset({
    "path", "raw_bytes_sha256", "canonical_document_sha256", "record_count",
})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPEN_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
_OPEN_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _fail(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _finite_json(value: object, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(f"{path} must contain only finite numbers")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{path} object keys must be strings")
            _finite_json(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_json(item, f"{path}[{index}]")
        return
    _fail(f"{path} must contain JSON-compatible values")


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON object has duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail(f"JSON contains non-finite constant: {value}")


def _load_finite_json(value: bytes, *, label: str) -> object:
    try:
        text = value.decode("utf-8")
        document = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ProducerAuthenticationError) as exc:
        _fail(f"{label} must contain one complete finite UTF-8 JSON document", exc)
    _finite_json(document, label)
    return deepcopy(document)


def _stat_key(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _relative_components(path: Path, root: Path) -> tuple[Path, tuple[str, ...]]:
    if any(component in {"", ".", ".."} for component in path.parts):
        _fail(f"evidence path is hostile: {path}")
    root_path = root.absolute()
    candidate = (root_path / path) if not path.is_absolute() else path.absolute()
    try:
        relative = candidate.relative_to(root_path)
    except ValueError as exc:
        _fail(f"evidence path is outside declared root: {path}", exc)
    components = relative.parts
    if not components or any(component in {"", ".", ".."} for component in components):
        _fail(f"evidence path is hostile or does not name a file beneath root: {path}")
    return root_path, components


def _read_pinned_bytes(path: Path, *, root: Path) -> tuple[str, bytes]:
    """Read a regular non-symlink file, rejecting a path swap or in-place mutation."""
    root_path, components = _relative_components(path, root)
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        try:
            current_fd = os.open(root_path, _OPEN_DIRECTORY_FLAGS)
        except OSError as exc:
            _fail(f"declared evidence root is unavailable or unsafe: {root_path}", exc)
        directory_fds.append(current_fd)
        for component in components[:-1]:
            try:
                next_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError as exc:
                _fail(f"evidence parent path is unavailable or unsafe: {component}", exc)
            directory_fds.append(next_fd)
            current_fd = next_fd
        try:
            file_fd = os.open(components[-1], _OPEN_FILE_FLAGS, dir_fd=current_fd)
        except OSError as exc:
            _fail(f"evidence file is unavailable or unsafe: {'/'.join(components)}", exc)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"evidence file must be a regular file: {'/'.join(components)}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        if _stat_key(before) != _stat_key(after):
            _fail(f"evidence file changed while it was read: {'/'.join(components)}")
        try:
            verify_fd = os.open(components[-1], _OPEN_FILE_FLAGS, dir_fd=current_fd)
        except OSError as exc:
            _fail(f"evidence file changed while it was read: {'/'.join(components)}", exc)
        try:
            verified = os.fstat(verify_fd)
        finally:
            os.close(verify_fd)
        if _stat_key(before) != _stat_key(verified):
            _fail(f"evidence file path changed while it was read: {'/'.join(components)}")
        return "/".join(components), b"".join(chunks)
    except OSError as exc:
        _fail(f"could not read producer evidence safely: {path}", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _binding(path: str, raw: bytes, canonical: bytes, record_count: int | None) -> dict[str, object]:
    return {
        "path": path,
        "raw_bytes_sha256": _sha256_bytes(raw),
        "canonical_document_sha256": _sha256_bytes(canonical),
        "record_count": record_count,
    }


def bind_json(path: Path, *, root: Path) -> dict[str, object]:
    """Bind one finite JSON document using original bytes and canonical semantics."""
    relative_path, raw = _read_pinned_bytes(Path(path), root=Path(root))
    document = _load_finite_json(raw, label=relative_path)
    return _binding(relative_path, raw, canonical_json_bytes(document), None)


def bind_jsonl(path: Path, *, root: Path) -> dict[str, object]:
    """Bind ordered finite JSONL records with exactly one canonical LF per record."""
    relative_path, raw = _read_pinned_bytes(Path(path), root=Path(root))
    if not raw.endswith(b"\n"):
        _fail(f"{relative_path} must end every JSONL record with LF")
    lines = raw[:-1].split(b"\n")
    if not lines or any(not line.strip() for line in lines):
        _fail(f"{relative_path} must contain complete non-empty JSONL records")
    records = [
        _load_finite_json(line, label=f"{relative_path}[{index}]")
        for index, line in enumerate(lines)
    ]
    canonical = b"".join(canonical_json_bytes(record) for record in records)
    return _binding(relative_path, raw, canonical, len(records))


def _validate_binding(name: str, value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"lineage binding {name} must be an object")
    if set(value) != _BINDING_KEYS:
        _fail(f"lineage binding {name} must contain exactly the closed binding fields")
    binding = dict(value)
    path = binding["path"]
    if not isinstance(path, str) or not path or path.startswith("/") or any(
        component in {"", ".", ".."} for component in path.split("/")
    ):
        _fail(f"lineage binding {name}.path must be a safe relative path")
    for field in ("raw_bytes_sha256", "canonical_document_sha256"):
        digest = binding[field]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            _fail(f"lineage binding {name}.{field} must be a prefixed SHA-256")
    count = binding["record_count"]
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        _fail(f"lineage binding {name}.record_count must be null or a positive integer")
    return deepcopy(binding)


def lineage_bundle_sha256(input_bindings: Mapping[str, dict[str, object]]) -> str:
    """Digest the four closed lineage bindings in their required physical-file order."""
    if not isinstance(input_bindings, Mapping) or tuple(input_bindings) != LINEAGE_ORDER:
        _fail("lineage bindings must contain the fixed lineage files in required order")
    bindings = [_validate_binding(name, input_bindings[name]) for name in LINEAGE_ORDER]
    paths = [binding["path"] for binding in bindings]
    if len(paths) != len(set(paths)):
        _fail("lineage bindings must not reuse a file path")
    return sha256_json(bindings)
