"""Exact-FD canonical archives for authenticated producer evidence.

The deterministic commit is the only locator that can authorize an otherwise
inert, randomly named archive.  Persistent snapshot files are never renamed,
unlinked, quarantined, or overwritten by this module.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tempfile
from typing import Any
import uuid
import weakref

from ...common import canonical_json_bytes
from .evidence_errors import (
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
)


MAX_MEMBER_COUNT = 4_096
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_COMPONENT_BYTES = 240
MAX_MEMBER_PATH_BYTES = 1_024
MAX_COMMIT_BYTES = 16 * 1024 * 1024
MAX_BINDING_COUNT = 1_024
MAX_BINDING_NAME_BYTES = 128
MAX_COMMIT_DEPTH = 6

_SCHEMA = "panel-evidence-snapshot-commit-v1"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ARCHIVE_RE = re.compile(
    r"^\.snapshot-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\.zip$"
)
_FROZEN_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?Z$"
)
_COMMIT_KEYS = frozenset({
    "schema_version", "status", "snapshot_id", "surface", "run_id",
    "result_sha256", "archive_name", "archive_sha256",
    "archive_byte_count", "frozen_at", "bindings", "members",
    "snapshot_sha256",
})
_BINDING_KEYS = frozenset({
    "member_path", "raw_bytes_sha256", "canonical_document_sha256",
    "record_count",
})
_MEMBER_KEYS = frozenset({"path", "byte_count", "raw_bytes_sha256"})
_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FLAGS = (
    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_DOS_DATE = 33
_DOS_TIME = 0
_LOCAL = struct.Struct("<IHHHHHIIIHH")
_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_EOCD = struct.Struct("<IHHHHIIH")
_LOCAL_SIGNATURE = 0x04034B50
_CENTRAL_SIGNATURE = 0x02014B50
_EOCD_SIGNATURE = 0x06054B50
_EXTERNAL_ATTR = (stat.S_IFREG | 0o400) << 16
_DARWIN_ROOT_ALIASES = (
    (Path("/var"), Path("/private/var")),
    (Path("/tmp"), Path("/private/tmp")),
)


@dataclass(frozen=True)
class EvidenceSnapshot:
    snapshot_id: str
    commit_path: Path
    frozen_at: str
    snapshot_sha256: str
    archive_sha256: str
    bindings: tuple[tuple[str, dict[str, object]], ...]


@dataclass
class _PinnedRoot:
    path: Path
    fd: int
    identity: tuple[int, int]
    chain: tuple[tuple[Path, tuple[int, int]], ...]


@dataclass
class _Source:
    member_path: str
    path: Path
    fd: int
    stat_key: tuple[int, int, int, int, int, int, int]
    parent_path: Path
    parent_chain: tuple[tuple[Path, tuple[int, int]], ...]
    byte_count: int
    raw_digest: str
    crc32: int


@dataclass
class _PublishedFile:
    name: str
    fd: int
    identity: tuple[int, int]
    stat_key: tuple[int, int, int, int, int, int, int]
    byte_count: int
    digest: str


@dataclass(frozen=True)
class _ArchiveMember:
    path: str
    byte_count: int
    raw_bytes_sha256: str
    crc32: int
    data_offset: int
    local_offset: int


@dataclass
class _Joint:
    root: _PinnedRoot
    commit: _PublishedFile
    archive: _PublishedFile
    record: dict[str, object]
    members: tuple[_ArchiveMember, ...]


def _auth(message: str, exc: BaseException | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _stat_key(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns, value.st_nlink,
    )


def _canonical_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if sys.platform != "darwin":
        return absolute
    for alias, target in _DARWIN_ROOT_ALIASES:
        if absolute != alias and alias not in absolute.parents:
            continue
        try:
            alias_lstat = os.lstat(alias)
            alias_stat = os.stat(alias)
            target_stat = os.stat(target)
        except OSError:
            continue
        if (
            stat.S_ISLNK(alias_lstat.st_mode)
            and Path(os.path.realpath(alias)) == target
            and os.path.samestat(alias_stat, target_stat)
        ):
            return target / absolute.relative_to(alias)
    return absolute


def _require_platform() -> None:
    missing = [
        name for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_EXCL")
        if not hasattr(os, name)
    ]
    if missing:
        raise ProducerEvidenceError(
            "canonical evidence archives require " + ", ".join(missing)
        )


def _open_absolute_directory(
    path: Path, *, label: str,
) -> tuple[int, tuple[tuple[Path, tuple[int, int]], ...]]:
    canonical = _canonical_path(path)
    fds: list[int] = []
    chain: list[tuple[Path, tuple[int, int]]] = []
    current_path = Path("/")
    try:
        current_fd = os.open("/", _DIR_FLAGS)
        fds.append(current_fd)
        chain.append((current_path, _identity(os.fstat(current_fd))))
        for component in canonical.parts[1:]:
            current_path /= component
            current_fd = os.open(component, _DIR_FLAGS, dir_fd=current_fd)
            fds.append(current_fd)
            value = os.fstat(current_fd)
            if not stat.S_ISDIR(value.st_mode):
                _auth(f"{label} is not a directory: {current_path}")
            chain.append((current_path, _identity(value)))
        result = fds.pop()
        return result, tuple(chain)
    except ProducerEvidenceError:
        raise
    except OSError as exc:
        _auth(f"{label} contains an unavailable or symlink component", exc)
    finally:
        for fd in reversed(fds):
            os.close(fd)
    raise AssertionError("unreachable")


def _recheck_chain(
    chain: tuple[tuple[Path, tuple[int, int]], ...], *, label: str,
) -> None:
    if not chain:
        _auth(f"{label} has no pinned chain")
    fds: list[int] = []
    try:
        current_fd = os.open("/", _DIR_FLAGS)
        fds.append(current_fd)
        if _identity(os.fstat(current_fd)) != chain[0][1]:
            _auth(f"{label} filesystem root changed")
        for path, expected in chain[1:]:
            current_fd = os.open(path.name, _DIR_FLAGS, dir_fd=current_fd)
            fds.append(current_fd)
            if _identity(os.fstat(current_fd)) != expected:
                _auth(f"{label} component changed: {path}")
    except ProducerEvidenceError:
        raise
    except OSError as exc:
        _auth(f"{label} component changed or became unsafe", exc)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _open_trusted_root(path: Path) -> _PinnedRoot:
    public = Path(os.path.abspath(os.fspath(path)))
    canonical = _canonical_path(Path(path))
    fd, chain = _open_absolute_directory(canonical, label="snapshot_root")
    try:
        value = os.fstat(fd)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) & 0o022
        ):
            _auth(
                "snapshot_root must be an existing euid-owned real directory "
                "that is not group/world writable"
            )
        return _PinnedRoot(public, fd, _identity(value), chain)
    except BaseException:
        os.close(fd)
        raise


def _recheck_root(root: _PinnedRoot) -> None:
    value = os.fstat(root.fd)
    if (
        _identity(value) != root.identity
        or not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        _auth("snapshot_root metadata or identity changed")
    _recheck_chain(root.chain, label="snapshot_root")


def _validate_identifiers(surface: str, run_id: str, result_sha256: str) -> str:
    for label, value in (("surface", surface), ("run_id", run_id)):
        if (
            not isinstance(value, str) or "--" in value
            or not _IDENTIFIER_RE.fullmatch(value)
        ):
            _auth(f"{label} must be a safe non-empty snapshot identifier")
    if not isinstance(result_sha256, str) or not _SHA256_RE.fullmatch(result_sha256):
        _auth("result_sha256 must be a lowercase prefixed SHA-256 digest")
    snapshot_id = f"{surface}--{run_id}--{result_sha256[7:]}"
    if len((snapshot_id + ".snapshot.json").encode("ascii")) > 240:
        _auth("derived snapshot commit basename exceeds the component limit")
    return snapshot_id


def _validate_member_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        _auth("archive member paths must be non-empty strings")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        _auth(f"archive member path must be ASCII: {value!r}", exc)
    if (
        len(encoded) > MAX_MEMBER_PATH_BYTES or value.startswith("/")
        or value.endswith("/") or "\\" in value or "\x00" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        _auth(f"archive member path is unsafe or over limit: {value!r}")
    components = value.split("/")
    if any(
        not component or component in {".", ".."}
        or len(component.encode("ascii")) > MAX_COMPONENT_BYTES
        for component in components
    ):
        _auth(f"archive member path is non-canonical: {value!r}")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.parts != tuple(components):
        _auth(f"archive member path is non-canonical: {value!r}")
    return value


def _validate_authenticated_resource_counters(
    *,
    member_count: int,
    largest_member_bytes: int,
    total_uncompressed_bytes: int,
    archive_bytes: int,
) -> None:
    counters = (
        member_count, largest_member_bytes, total_uncompressed_bytes,
        archive_bytes,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
        _auth("authenticated archive resource counters are invalid")
    if member_count > MAX_MEMBER_COUNT:
        _auth("archive exceeds member-count limit")
    if largest_member_bytes > MAX_MEMBER_BYTES:
        _auth("archive exceeds per-member byte limit")
    if total_uncompressed_bytes > MAX_TOTAL_BYTES:
        _auth("archive exceeds total uncompressed-byte limit")
    if archive_bytes > MAX_ARCHIVE_BYTES:
        _auth("archive exceeds archive-byte limit")


def _canonical_allowed_roots(values: Sequence[Path]) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes, os.PathLike)) or not isinstance(values, Sequence):
        _auth("allowed_roots must be a non-empty sequence")
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, (str, os.PathLike)):
            _auth("allowed_roots entries must be filesystem paths")
        path = _canonical_path(Path(value))
        if path in roots:
            _auth(f"allowed_roots contains a duplicate: {path}")
        roots.append(path)
    if not roots:
        _auth("allowed_roots must not be empty")
    return tuple(sorted(roots, key=lambda item: (-len(item.parts), os.fspath(item))))


def _source_parent(
    source_path: Path, roots: tuple[Path, ...],
) -> tuple[Path, tuple[str, ...]]:
    canonical = _canonical_path(source_path)
    for root in roots:
        try:
            relative = canonical.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            return root, relative.parts
    _auth(f"snapshot source is outside every allowed root: {source_path}")
    raise AssertionError("unreachable")


def _open_source(
    member_path: str, source_path: Path, roots: tuple[Path, ...],
) -> _Source:
    root_path, components = _source_parent(source_path, roots)
    canonical_source = _canonical_path(source_path)
    expected_directories: dict[Path, tuple[int, int]] = {}
    current = Path("/")
    try:
        for component in canonical_source.parts[1:-1]:
            current /= component
            value = os.lstat(current)
            if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
                _auth(f"source path contains a non-directory or symlink: {current}")
            expected_directories[current] = _identity(value)
        expected_file = os.lstat(canonical_source)
        if not stat.S_ISREG(expected_file.st_mode):
            _auth(f"snapshot source must be regular: {source_path}")
    except ProducerEvidenceError:
        raise
    except OSError as exc:
        _auth(f"snapshot source cannot be preflighted safely: {source_path}", exc)
    root_fd, root_chain = _open_absolute_directory(root_path, label="allowed source root")
    directory_fds = [root_fd]
    chain = list(root_chain)
    current_fd = root_fd
    current_path = root_path
    file_fd: int | None = None
    try:
        for component in components[:-1]:
            current_path /= component
            current_fd = os.open(component, _DIR_FLAGS, dir_fd=current_fd)
            directory_fds.append(current_fd)
            value = os.fstat(current_fd)
            if not stat.S_ISDIR(value.st_mode):
                _auth(f"source parent is not a directory: {current_path}")
            chain.append((current_path, _identity(value)))
        file_fd = os.open(components[-1], _READ_FLAGS, dir_fd=current_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or _stat_key(before) != _stat_key(expected_file)
            or any(
                expected_directories.get(path) != identity
                for path, identity in chain
                if path in expected_directories
            )
        ):
            _auth(f"snapshot source path changed before descriptor pin: {source_path}")
        if before.st_size > MAX_MEMBER_BYTES:
            _auth(f"snapshot source exceeds member-byte limit: {source_path}")
        digest = hashlib.sha256()
        crc = 0
        count = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, MAX_MEMBER_BYTES + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_MEMBER_BYTES:
                _auth(f"snapshot source exceeds member-byte limit: {source_path}")
            digest.update(chunk)
            crc = binascii.crc32(chunk, crc)
        after = os.fstat(file_fd)
        if _stat_key(before) != _stat_key(after) or count != before.st_size:
            _auth(f"snapshot source changed while read: {source_path}")
        result = _Source(
            member_path=member_path,
            path=canonical_source,
            fd=file_fd,
            stat_key=_stat_key(before),
            parent_path=current_path,
            parent_chain=tuple(chain),
            byte_count=count,
            raw_digest="sha256:" + digest.hexdigest(),
            crc32=crc & 0xFFFFFFFF,
        )
        file_fd = None
        return result
    except ProducerEvidenceError:
        raise
    except OSError as exc:
        _auth(f"snapshot source is unavailable or unsafe: {source_path}", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for fd in reversed(directory_fds):
            os.close(fd)
    raise AssertionError("unreachable")


def _reauthenticate_source(source: _Source) -> None:
    _recheck_chain(source.parent_chain, label=f"source {source.member_path}")
    parent_fd, chain = _open_absolute_directory(
        source.parent_path, label=f"source parent {source.member_path}"
    )
    verify_fd: int | None = None
    try:
        if chain != source.parent_chain:
            _auth(f"source parent identity changed: {source.path}")
        verify_fd = os.open(source.path.name, _READ_FLAGS, dir_fd=parent_fd)
        before = os.fstat(verify_fd)
        if not stat.S_ISREG(before.st_mode) or _stat_key(before) != source.stat_key:
            _auth(f"source entry changed: {source.path}")
        digest = hashlib.sha256()
        count = 0
        while True:
            chunk = os.read(verify_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
        if (
            _stat_key(os.fstat(verify_fd)) != source.stat_key
            or _stat_key(os.fstat(source.fd)) != source.stat_key
            or count != source.byte_count
            or "sha256:" + digest.hexdigest() != source.raw_digest
        ):
            _auth(f"source bytes changed: {source.path}")
        _recheck_chain(source.parent_chain, label=f"source {source.member_path}")
    except ProducerEvidenceError:
        raise
    except OSError as exc:
        _auth(f"source could not be reauthenticated: {source.path}", exc)
    finally:
        if verify_fd is not None:
            os.close(verify_fd)
        os.close(parent_fd)


def _validate_sources(
    sources: Mapping[str, Path], roots: tuple[Path, ...],
) -> list[_Source]:
    if not isinstance(sources, Mapping) or not sources:
        _auth("sources must be a non-empty mapping")
    if len(sources) > MAX_MEMBER_COUNT:
        _auth("sources exceed archive member-count limit")
    specs: list[tuple[str, Path]] = []
    names: set[str] = set()
    for raw_member, raw_source in sources.items():
        member = _validate_member_path(raw_member)
        if member in names:
            _auth(f"duplicate archive member: {member}")
        if not isinstance(raw_source, (str, os.PathLike)):
            _auth(f"source for {member} must be a filesystem path")
        names.add(member)
        specs.append((member, Path(raw_source)))
    for name in names:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            if "/".join(parts[:depth]) in names:
                _auth("archive members contain a file/directory collision")
    opened: list[_Source] = []
    total = 0
    try:
        for member, path in sorted(specs):
            source = _open_source(member, path, roots)
            opened.append(source)
            total += source.byte_count
            if total > MAX_TOTAL_BYTES:
                _auth("sources exceed total uncompressed-byte limit")
        return opened
    except BaseException:
        for source in reversed(opened):
            os.close(source.fd)
        raise


def _validate_bindings(
    bindings: Mapping[str, Mapping[str, object]],
    members: Mapping[str, tuple[int, str]],
) -> dict[str, dict[str, object]]:
    if not isinstance(bindings, Mapping):
        _auth("bindings must be a mapping")
    if len(bindings) > MAX_BINDING_COUNT:
        _auth("bindings exceed closed count limit")
    result: dict[str, dict[str, object]] = {}
    for raw_name, raw_value in sorted(bindings.items()):
        if not isinstance(raw_name, str) or not raw_name:
            _auth("binding names must be non-empty strings")
        try:
            encoded_name = raw_name.encode("ascii")
        except UnicodeEncodeError as exc:
            _auth("binding names must be ASCII", exc)
        if (
            len(encoded_name) > MAX_BINDING_NAME_BYTES
            or any(byte < 0x20 or byte == 0x7F for byte in encoded_name)
        ):
            _auth(f"binding name is unsafe or over limit: {raw_name!r}")
        if not isinstance(raw_value, Mapping) or set(raw_value) != _BINDING_KEYS:
            _auth(f"binding {raw_name} must contain exactly the closed fields")
        value = dict(raw_value)
        member_path = _validate_member_path(value["member_path"])
        raw_digest = value["raw_bytes_sha256"]
        if not isinstance(raw_digest, str) or not _SHA256_RE.fullmatch(raw_digest):
            _auth(f"binding {raw_name}.raw_bytes_sha256 is invalid")
        if member_path not in members or members[member_path][1] != raw_digest:
            _auth(f"binding {raw_name} does not select its exact member bytes")
        canonical = value["canonical_document_sha256"]
        count = value["record_count"]
        if canonical is None:
            if count is not None:
                _auth(f"binding {raw_name} raw non-document fields must both be null")
        elif not isinstance(canonical, str) or not _SHA256_RE.fullmatch(canonical):
            _auth(f"binding {raw_name}.canonical_document_sha256 is invalid")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 1
        ):
            _auth(f"binding {raw_name}.record_count is invalid")
        result[raw_name] = {
            "member_path": member_path,
            "raw_bytes_sha256": raw_digest,
            "canonical_document_sha256": canonical,
            "record_count": count,
        }
    return result


def _write_all(fd: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("snapshot write made no progress")
        remaining = remaining[written:]


def _create_file(root: _PinnedRoot, name: str) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=root.fd)
    except FileExistsError as exc:
        raise ProducerOutputCollision(
            f"immutable snapshot publication path already exists: {root.path / name}"
        ) from exc
    except OSError as exc:
        raise ProducerEvidenceError(f"could not create snapshot file: {name}") from exc
    value = os.fstat(fd)
    if (
        not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
        or value.st_nlink != 1
    ):
        os.close(fd)
        _auth(f"new snapshot path is not a private regular file: {name}")
    return fd, _identity(value)


def _entry_matches(root: _PinnedRoot, published: _PublishedFile) -> bool:
    try:
        value = os.stat(published.name, dir_fd=root.fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(value.st_mode)
        and _identity(value) == published.identity
        and _identity(os.fstat(published.fd)) == published.identity
    )


def _read_bounded_fd(fd: int, maximum: int, *, label: str) -> bytearray:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        _auth(f"{label} must be a regular file")
    if before.st_size > maximum:
        _auth(f"{label} exceeds byte limit")
    os.lseek(fd, 0, os.SEEK_SET)
    result = bytearray(before.st_size)
    count = 0
    while count <= maximum:
        chunk = os.read(fd, min(1024 * 1024, maximum + 1 - count))
        if not chunk:
            break
        result[count:count + len(chunk)] = chunk
        count += len(chunk)
    if count > maximum:
        _auth(f"{label} exceeds byte limit")
    after = os.fstat(fd)
    if _stat_key(before) != _stat_key(after) or count != before.st_size:
        _auth(f"{label} changed while read")
    return result


def _make_published(
    name: str, fd: int, identity: tuple[int, int], *, expected_mode: int,
    maximum: int, label: str,
) -> tuple[_PublishedFile, bytearray]:
    data = _read_bounded_fd(fd, maximum, label=label)
    value = os.fstat(fd)
    if (
        _identity(value) != identity or not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid() or value.st_nlink != 1
        or stat.S_IMODE(value.st_mode) != expected_mode
    ):
        _auth(f"{label} metadata, mode, or identity is invalid")
    return (
        _PublishedFile(
            name, fd, identity, _stat_key(value), len(data), _digest_bytes(data)
        ),
        data,
    )


def _recheck_published(
    root: _PinnedRoot, published: _PublishedFile, *, maximum: int, label: str,
) -> bytearray:
    _recheck_published_identity(root, published, label=label)
    data = _read_bounded_fd(published.fd, maximum, label=label)
    value = os.fstat(published.fd)
    if (
        _stat_key(value) != published.stat_key
        or len(data) != published.byte_count
        or _digest_bytes(data) != published.digest
        or stat.S_IMODE(value.st_mode) != 0o400
    ):
        _auth(f"{label} metadata, length, or digest changed")
    _recheck_published_identity(root, published, label=label)
    return data


def _recheck_published_identity(
    root: _PinnedRoot, published: _PublishedFile, *, label: str,
) -> None:
    _recheck_root(root)
    if not _entry_matches(root, published):
        _auth(f"{label} directory entry no longer selects its pinned FD")
    value = os.fstat(published.fd)
    if (
        _stat_key(value) != published.stat_key
        or not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or stat.S_IMODE(value.st_mode) != 0o400
        or value.st_size != published.byte_count
    ):
        _auth(f"{label} final FD metadata, length, or mode changed")
    _recheck_root(root)


def _write_archive(fd: int, sources: Sequence[_Source]) -> None:
    central: list[bytes] = []
    offset = 0
    for source in sources:
        name = source.member_path.encode("ascii")
        header = _LOCAL.pack(
            _LOCAL_SIGNATURE, 20, 0, 0, _DOS_TIME, _DOS_DATE,
            source.crc32, source.byte_count, source.byte_count, len(name), 0,
        )
        _write_all(fd, header)
        _write_all(fd, name)
        os.lseek(source.fd, 0, os.SEEK_SET)
        copied = 0
        digest = hashlib.sha256()
        crc = 0
        while True:
            chunk = os.read(source.fd, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            digest.update(chunk)
            crc = binascii.crc32(chunk, crc)
            _write_all(fd, chunk)
        if (
            copied != source.byte_count
            or "sha256:" + digest.hexdigest() != source.raw_digest
            or (crc & 0xFFFFFFFF) != source.crc32
            or _stat_key(os.fstat(source.fd)) != source.stat_key
        ):
            _auth(f"source changed during archive copy: {source.path}")
        central.append(
            _CENTRAL.pack(
                _CENTRAL_SIGNATURE, (3 << 8) | 20, 20, 0, 0,
                _DOS_TIME, _DOS_DATE, source.crc32, source.byte_count,
                source.byte_count, len(name), 0, 0, 0, 0,
                _EXTERNAL_ATTR, offset,
            ) + name
        )
        offset += len(header) + len(name) + source.byte_count
        if offset > MAX_ARCHIVE_BYTES:
            _auth("canonical archive exceeds byte limit")
    central_offset = offset
    for record in central:
        _write_all(fd, record)
        offset += len(record)
    central_size = offset - central_offset
    _write_all(
        fd,
        _EOCD.pack(
            _EOCD_SIGNATURE, 0, 0, len(sources), len(sources),
            central_size, central_offset, 0,
        ),
    )
    if offset + _EOCD.size > MAX_ARCHIVE_BYTES:
        _auth("canonical archive exceeds byte limit")


def _parse_archive_bytes(value: bytes | bytearray) -> tuple[_ArchiveMember, ...]:
    _validate_authenticated_resource_counters(
        member_count=0,
        largest_member_bytes=0,
        total_uncompressed_bytes=0,
        archive_bytes=len(value),
    )
    if len(value) < _EOCD.size or value[:4] != b"PK\x03\x04":
        _auth("archive lacks an exact local-header start")
    eocd_offset = len(value) - _EOCD.size
    try:
        eocd = _EOCD.unpack_from(value, eocd_offset)
    except struct.error as exc:
        _auth("archive has no complete EOCD", exc)
    if (
        eocd[0] != _EOCD_SIGNATURE or eocd[1] != 0 or eocd[2] != 0
        or eocd[3] != eocd[4] or eocd[3] > MAX_MEMBER_COUNT
        or eocd[7] != 0 or eocd[5] + eocd[6] != eocd_offset
    ):
        _auth("archive EOCD is non-canonical")
    count, central_size, central_offset = eocd[3], eocd[5], eocd[6]
    if count < 1 or central_offset > eocd_offset:
        _auth("archive EOCD count or offset is invalid")

    central_rows: list[tuple[str, tuple[int, ...]]] = []
    cursor = central_offset
    for _index in range(count):
        if cursor + _CENTRAL.size > eocd_offset:
            _auth("archive central directory is truncated")
        row = _CENTRAL.unpack_from(value, cursor)
        if (
            row[0] != _CENTRAL_SIGNATURE or row[1] != ((3 << 8) | 20)
            or row[2] != 20 or row[3] != 0 or row[4] != 0
            or row[5] != _DOS_TIME or row[6] != _DOS_DATE
            or row[10] < 1 or row[11] != 0 or row[12] != 0
            or row[13] != 0 or row[14] != 0
            or row[15] != _EXTERNAL_ATTR or row[8] == 0xFFFFFFFF
            or row[9] == 0xFFFFFFFF or row[16] == 0xFFFFFFFF
            or row[8] != row[9]
        ):
            _auth("archive central entry metadata is non-canonical")
        end = cursor + _CENTRAL.size + row[10]
        if end > eocd_offset:
            _auth("archive central filename is truncated")
        try:
            name = value[cursor + _CENTRAL.size:end].decode("ascii")
        except UnicodeDecodeError as exc:
            _auth("archive central filename is not ASCII", exc)
        _validate_member_path(name)
        central_rows.append((name, row))
        cursor = end
    if cursor != eocd_offset or cursor - central_offset != central_size:
        _auth("archive central directory has padding, gaps, or wrong size")
    names = [name for name, _row in central_rows]
    if names != sorted(names) or len(set(names)) != len(names):
        _auth("archive members are duplicated or out of order")

    members: list[_ArchiveMember] = []
    local_cursor = 0
    total = 0
    largest = 0
    for name, central in central_rows:
        if central[16] != local_cursor or local_cursor + _LOCAL.size > central_offset:
            _auth("archive local entries have a gap, overlap, or wrong offset")
        local = _LOCAL.unpack_from(value, local_cursor)
        if (
            local[0] != _LOCAL_SIGNATURE or local[1] != 20 or local[2] != 0
            or local[3] != 0 or local[4] != _DOS_TIME or local[5] != _DOS_DATE
            or local[6] != central[7] or local[7] != central[8]
            or local[8] != central[9] or local[9] != central[10]
            or local[10] != 0 or local[7] != local[8]
        ):
            _auth("archive local and central headers do not agree")
        name_start = local_cursor + _LOCAL.size
        name_end = name_start + local[9]
        data_end = name_end + local[7]
        if data_end > central_offset or value[name_start:name_end] != name.encode("ascii"):
            _auth("archive local filename or member extent is invalid")
        if local[7] > MAX_MEMBER_BYTES:
            _auth("archive member exceeds byte limit")
        data = memoryview(value)[name_end:data_end]
        if len(data) != local[7] or (binascii.crc32(data) & 0xFFFFFFFF) != local[6]:
            _auth("archive member CRC or size does not match bytes")
        total += len(data)
        largest = max(largest, len(data))
        if total > MAX_TOTAL_BYTES:
            _auth("archive total uncompressed bytes exceed limit")
        members.append(
            _ArchiveMember(
                name, len(data), _digest_bytes(data), local[6],
                name_end, local_cursor,
            )
        )
        local_cursor = data_end
    if local_cursor != central_offset:
        _auth("archive has prefix, padding, overlap, or undeclared local data")
    _validate_authenticated_resource_counters(
        member_count=len(members),
        largest_member_bytes=largest,
        total_uncompressed_bytes=total,
        archive_bytes=len(value),
    )
    return tuple(members)


def _validate_archive_fd(
    root: _PinnedRoot, published: _PublishedFile,
) -> tuple[_ArchiveMember, ...]:
    value = _recheck_published(
        root, published, maximum=MAX_ARCHIVE_BYTES, label="snapshot archive"
    )
    return _parse_archive_bytes(value)


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _auth(f"commit JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _json_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_COMMIT_DEPTH:
            return maximum
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return maximum


def _parse_commit_bytes(
    value: bytes | bytearray, *, surface: str, run_id: str, result_sha256: str,
    snapshot_id: str,
) -> dict[str, object]:
    if len(value) > MAX_COMMIT_BYTES:
        _auth("snapshot commit exceeds byte limit")
    try:
        record = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=lambda token: _auth(f"non-finite JSON constant: {token}"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        _auth("snapshot commit is not one finite UTF-8 JSON document", exc)
    if not isinstance(record, dict) or set(record) != _COMMIT_KEYS:
        _auth("snapshot commit does not contain exactly the closed fields")
    if _json_depth(record) > MAX_COMMIT_DEPTH:
        _auth("snapshot commit exceeds nesting-depth limit")
    if canonical_json_bytes(record) != value:
        _auth("snapshot commit bytes are not canonical compact JSON plus LF")
    if (
        record["schema_version"] != _SCHEMA or record["status"] != "committed"
        or record["snapshot_id"] != snapshot_id or record["surface"] != surface
        or record["run_id"] != run_id or record["result_sha256"] != result_sha256
    ):
        _auth("snapshot commit identity or status is invalid")
    archive_name = record["archive_name"]
    if not isinstance(archive_name, str) or not _ARCHIVE_RE.fullmatch(archive_name):
        _auth("snapshot commit archive_name is not an inert canonical basename")
    archive_digest = record["archive_sha256"]
    snapshot_digest = record["snapshot_sha256"]
    if (
        not isinstance(archive_digest, str) or not _SHA256_RE.fullmatch(archive_digest)
        or not isinstance(snapshot_digest, str) or not _SHA256_RE.fullmatch(snapshot_digest)
    ):
        _auth("snapshot commit digests are invalid")
    archive_count = record["archive_byte_count"]
    if (
        isinstance(archive_count, bool) or not isinstance(archive_count, int)
        or archive_count < 1 or archive_count > MAX_ARCHIVE_BYTES
    ):
        _auth("snapshot commit archive_byte_count is invalid")
    frozen_at = record["frozen_at"]
    if not isinstance(frozen_at, str) or not _FROZEN_RE.fullmatch(frozen_at):
        _auth("snapshot commit frozen_at is not canonical UTC Z")
    try:
        parsed_time = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    except ValueError as exc:
        _auth("snapshot commit frozen_at is invalid", exc)
    if parsed_time.utcoffset() != timezone.utc.utcoffset(parsed_time):
        _auth("snapshot commit frozen_at is not UTC")
    members_value = record["members"]
    if not isinstance(members_value, list) or not (1 <= len(members_value) <= MAX_MEMBER_COUNT):
        _auth("snapshot commit members has invalid count")
    member_map: dict[str, tuple[int, str]] = {}
    member_names: list[str] = []
    member_total = 0
    for row in members_value:
        if not isinstance(row, dict) or set(row) != _MEMBER_KEYS:
            _auth("snapshot commit member row is not closed")
        path = _validate_member_path(row["path"])
        byte_count = row["byte_count"]
        raw_digest = row["raw_bytes_sha256"]
        if (
            isinstance(byte_count, bool) or not isinstance(byte_count, int)
            or byte_count < 0 or byte_count > MAX_MEMBER_BYTES
            or not isinstance(raw_digest, str) or not _SHA256_RE.fullmatch(raw_digest)
        ):
            _auth("snapshot commit member row values are invalid")
        if path in member_map:
            _auth("snapshot commit has duplicate member rows")
        member_map[path] = (byte_count, raw_digest)
        member_names.append(path)
        member_total += byte_count
        if member_total > MAX_TOTAL_BYTES:
            _auth("snapshot commit members exceed total-byte limit")
    if member_names != sorted(member_names):
        _auth("snapshot commit member rows are not path sorted")
    bindings_value = record["bindings"]
    if not isinstance(bindings_value, dict):
        _auth("snapshot commit bindings must be an object")
    if list(bindings_value) != sorted(bindings_value):
        _auth("snapshot commit bindings must be name sorted")
    _validate_bindings(bindings_value, member_map)
    unhashed = dict(record)
    unhashed["snapshot_sha256"] = None
    if _digest_bytes(canonical_json_bytes(unhashed)) != snapshot_digest:
        _auth("snapshot commit self-hash is invalid")
    return record


def _snapshot_from_record(root: _PinnedRoot, record: Mapping[str, object]) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=str(record["snapshot_id"]),
        commit_path=root.path / (str(record["snapshot_id"]) + ".snapshot.json"),
        frozen_at=str(record["frozen_at"]),
        snapshot_sha256=str(record["snapshot_sha256"]),
        archive_sha256=str(record["archive_sha256"]),
        bindings=tuple(
            (name, dict(value))
            for name, value in sorted(dict(record["bindings"]).items())
        ),
    )


def _open_existing_file(root: _PinnedRoot, name: str, *, label: str) -> _PublishedFile:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=root.fd)
    except OSError as exc:
        _auth(f"{label} is missing, unsafe, or not regular", exc)
    try:
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
            or value.st_nlink != 1 or stat.S_IMODE(value.st_mode) != 0o400
        ):
            _auth(f"{label} metadata or mode is invalid")
        if not _identity(value) == _identity(
            os.stat(name, dir_fd=root.fd, follow_symlinks=False)
        ):
            _auth(f"{label} entry changed while opened")
        data = _read_bounded_fd(
            fd, MAX_COMMIT_BYTES if label == "snapshot commit" else MAX_ARCHIVE_BYTES,
            label=label,
        )
        return _PublishedFile(
            name, fd, _identity(value), _stat_key(os.fstat(fd)),
            len(data), _digest_bytes(data),
        )
    except BaseException:
        os.close(fd)
        raise


def _open_joint(
    *, surface: str, run_id: str, result_sha256: str, snapshot_root: Path,
) -> _Joint:
    snapshot_id = _validate_identifiers(surface, run_id, result_sha256)
    root = _open_trusted_root(snapshot_root)
    commit: _PublishedFile | None = None
    archive: _PublishedFile | None = None
    try:
        commit = _open_existing_file(
            root, snapshot_id + ".snapshot.json", label="snapshot commit"
        )
        commit_bytes = _recheck_published(
            root, commit, maximum=MAX_COMMIT_BYTES, label="snapshot commit"
        )
        record = _parse_commit_bytes(
            commit_bytes, surface=surface, run_id=run_id,
            result_sha256=result_sha256, snapshot_id=snapshot_id,
        )
        archive = _open_existing_file(
            root, str(record["archive_name"]), label="snapshot archive"
        )
        archive_bytes = _recheck_published(
            root, archive, maximum=MAX_ARCHIVE_BYTES, label="snapshot archive"
        )
        if (
            archive.byte_count != record["archive_byte_count"]
            or archive.digest != record["archive_sha256"]
        ):
            _auth("snapshot archive length or digest does not match commit")
        members = _parse_archive_bytes(archive_bytes)
        exact_rows = [
            {
                "path": member.path, "byte_count": member.byte_count,
                "raw_bytes_sha256": member.raw_bytes_sha256,
            }
            for member in members
        ]
        if exact_rows != record["members"]:
            _auth("snapshot archive members do not match the closed commit manifest")
        _validate_bindings(
            record["bindings"],
            {member.path: (member.byte_count, member.raw_bytes_sha256) for member in members},
        )
        _recheck_published(
            root, commit, maximum=MAX_COMMIT_BYTES, label="snapshot commit"
        )
        _recheck_published(
            root, archive, maximum=MAX_ARCHIVE_BYTES, label="snapshot archive"
        )
        joint = _Joint(root, commit, archive, record, members)
        _final_joint_identity(joint)
        return joint
    except BaseException:
        if archive is not None:
            os.close(archive.fd)
        if commit is not None:
            os.close(commit.fd)
        os.close(root.fd)
        raise


def _close_joint(joint: _Joint) -> None:
    os.close(joint.archive.fd)
    os.close(joint.commit.fd)
    os.close(joint.root.fd)


def _final_joint_identity(joint: _Joint) -> None:
    """Finish with one root-chain and both-entry identity/metadata barrier."""
    _recheck_root(joint.root)
    for published, label in (
        (joint.commit, "snapshot commit"),
        (joint.archive, "snapshot archive"),
    ):
        if not _entry_matches(joint.root, published):
            _auth(f"{label} failed the final joint entry-to-FD check")
        value = os.fstat(published.fd)
        if (
            _stat_key(value) != published.stat_key
            or not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.geteuid()
            or value.st_nlink != 1
            or stat.S_IMODE(value.st_mode) != 0o400
            or value.st_size != published.byte_count
        ):
            _auth(f"{label} failed the final joint metadata/length check")
    _recheck_root(joint.root)


def _canonical_frozen_at() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _recover_joint(
    *, surface: str, run_id: str, result_sha256: str, snapshot_root: Path,
) -> _Joint:
    """Return the same durably recovered joint FD set that was authenticated."""
    joint = _open_joint(
        surface=surface, run_id=run_id, result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    )
    try:
        try:
            os.fsync(joint.archive.fd)
            os.fsync(joint.commit.fd)
            os.fsync(joint.root.fd)
        except OSError as exc:
            raise ProducerPublicationIndeterminate(
                "snapshot bytes are authentic but durability recovery failed"
            ) from exc
        # Recovery is not complete until every structural and identity check is
        # repeated after the file-file-root durability barrier.
        commit_bytes = _recheck_published(
            joint.root, joint.commit, maximum=MAX_COMMIT_BYTES,
            label="snapshot commit",
        )
        record = _parse_commit_bytes(
            commit_bytes, surface=surface, run_id=run_id,
            result_sha256=result_sha256,
            snapshot_id=_validate_identifiers(surface, run_id, result_sha256),
        )
        archive_bytes = _recheck_published(
            joint.root, joint.archive, maximum=MAX_ARCHIVE_BYTES,
            label="snapshot archive",
        )
        if (
            _digest_bytes(archive_bytes) != record["archive_sha256"]
            or len(archive_bytes) != record["archive_byte_count"]
        ):
            _auth("snapshot archive changed across durability recovery")
        members = _parse_archive_bytes(archive_bytes)
        if [
            {
                "path": member.path, "byte_count": member.byte_count,
                "raw_bytes_sha256": member.raw_bytes_sha256,
            }
            for member in members
        ] != record["members"]:
            _auth("snapshot manifest changed across durability recovery")
        joint.record = record
        joint.members = members
        _final_joint_identity(joint)
        return joint
    except BaseException:
        _close_joint(joint)
        raise


def recover_evidence_snapshot_publication(
    *, surface: str, run_id: str, result_sha256: str, snapshot_root: Path,
) -> EvidenceSnapshot:
    """Durably recover and jointly authenticate a derived commit and archive."""
    joint = _recover_joint(
        surface=surface, run_id=run_id, result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    )
    try:
        return _snapshot_from_record(joint.root, joint.record)
    finally:
        _close_joint(joint)


def create_evidence_snapshot(
    *, surface: str, run_id: str, result_sha256: str,
    sources: Mapping[str, Path],
    bindings: Mapping[str, Mapping[str, object]],
    allowed_roots: Sequence[Path],
    snapshot_root: Path,
) -> EvidenceSnapshot:
    """Create a no-overwrite canonical ZIP and deterministic commit record."""
    _require_platform()
    snapshot_id = _validate_identifiers(surface, run_id, result_sha256)
    roots = _canonical_allowed_roots(allowed_roots)
    opened_sources: list[_Source] = []
    root: _PinnedRoot | None = None
    archive_fd: int | None = None
    commit_fd: int | None = None
    previous_umask = os.umask(0o077)
    try:
        opened_sources = _validate_sources(sources, roots)
        source_members = {
            source.member_path: (source.byte_count, source.raw_digest)
            for source in opened_sources
        }
        checked_bindings = _validate_bindings(bindings, source_members)
        root = _open_trusted_root(snapshot_root)
        archive_name = f".snapshot-{uuid.uuid4()}.zip"
        archive_fd, archive_identity = _create_file(root, archive_name)
        _write_archive(archive_fd, opened_sources)
        for source in opened_sources:
            _reauthenticate_source(source)
        # Validate canonical bytes from the exact O_EXCL FD before changing mode.
        archive_bytes = _read_bounded_fd(
            archive_fd, MAX_ARCHIVE_BYTES, label="new snapshot archive"
        )
        archive_members = _parse_archive_bytes(archive_bytes)
        expected_rows = [
            {
                "path": source.member_path, "byte_count": source.byte_count,
                "raw_bytes_sha256": source.raw_digest,
            }
            for source in opened_sources
        ]
        actual_rows = [
            {
                "path": member.path, "byte_count": member.byte_count,
                "raw_bytes_sha256": member.raw_bytes_sha256,
            }
            for member in archive_members
        ]
        if actual_rows != expected_rows:
            _auth("new archive does not match its authenticated sources")
        os.fchmod(archive_fd, 0o400)
        archive, _archive_data = _make_published(
            archive_name, archive_fd, archive_identity, expected_mode=0o400,
            maximum=MAX_ARCHIVE_BYTES, label="snapshot archive",
        )
        durability_uncertain = False
        try:
            os.fsync(archive_fd)
        except OSError:
            durability_uncertain = True
        _validate_archive_fd(root, archive)
        try:
            os.fsync(root.fd)
        except OSError:
            durability_uncertain = True
        _validate_archive_fd(root, archive)
        # The archive is not committed merely because its own bytes survived
        # the durability checks.  Reauthenticate every producer source again
        # at this final pre-commit boundary, then recheck the archive once more.
        for source in opened_sources:
            _reauthenticate_source(source)
        _validate_archive_fd(root, archive)

        frozen_at = _canonical_frozen_at()
        members_record = actual_rows
        commit_record: dict[str, object] = {
            "schema_version": _SCHEMA,
            "status": "committed",
            "snapshot_id": snapshot_id,
            "surface": surface,
            "run_id": run_id,
            "result_sha256": result_sha256,
            "archive_name": archive_name,
            "archive_sha256": archive.digest,
            "archive_byte_count": archive.byte_count,
            "frozen_at": frozen_at,
            "bindings": checked_bindings,
            "members": members_record,
            "snapshot_sha256": None,
        }
        commit_record["snapshot_sha256"] = _digest_bytes(
            canonical_json_bytes(commit_record)
        )
        commit_bytes = canonical_json_bytes(commit_record)
        if len(commit_bytes) > MAX_COMMIT_BYTES:
            _auth("canonical snapshot commit exceeds byte limit")
        commit_name = snapshot_id + ".snapshot.json"
        commit_fd, commit_identity = _create_file(root, commit_name)
        _write_all(commit_fd, commit_bytes)
        parsed = _parse_commit_bytes(
            _read_bounded_fd(commit_fd, MAX_COMMIT_BYTES, label="new snapshot commit"),
            surface=surface, run_id=run_id, result_sha256=result_sha256,
            snapshot_id=snapshot_id,
        )
        os.fchmod(commit_fd, 0o400)
        commit, _commit_data = _make_published(
            commit_name, commit_fd, commit_identity, expected_mode=0o400,
            maximum=MAX_COMMIT_BYTES, label="snapshot commit",
        )
        try:
            os.fsync(commit_fd)
        except OSError:
            durability_uncertain = True
        _recheck_published(
            root, commit, maximum=MAX_COMMIT_BYTES, label="snapshot commit"
        )
        try:
            os.fsync(root.fd)
        except OSError:
            durability_uncertain = True
        _recheck_published(
            root, commit, maximum=MAX_COMMIT_BYTES, label="snapshot commit"
        )
        _validate_archive_fd(root, archive)

        # Always use the public derived-path recovery protocol.  It closes the
        # pre-commit durability uncertainty and catches archive replacement at
        # every point during commit publication.
        os.close(commit_fd)
        commit_fd = None
        os.close(archive_fd)
        archive_fd = None
        os.close(root.fd)
        root = None
        try:
            return recover_evidence_snapshot_publication(
                surface=surface, run_id=run_id, result_sha256=result_sha256,
                snapshot_root=snapshot_root,
            )
        except ProducerPublicationIndeterminate:
            raise
        except ProducerEvidenceError:
            raise
        finally:
            _ = durability_uncertain
            _ = parsed
    except (ProducerEvidenceError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ProducerEvidenceError(
            f"evidence snapshot publication failed: {exc}"
        ) from exc
    finally:
        if commit_fd is not None:
            os.close(commit_fd)
        if archive_fd is not None:
            os.close(archive_fd)
        if root is not None:
            os.close(root.fd)
        for source in reversed(opened_sources):
            os.close(source.fd)
        os.umask(previous_umask)


def _safe_extract(joint: _Joint) -> tuple[Path, int, tuple[int, int]]:
    temp_root = Path(tempfile.gettempdir()).resolve()
    extraction = Path(tempfile.mkdtemp(prefix="validated-evidence-", dir=temp_root))
    if extraction == temp_root or extraction.parent != temp_root:
        raise ProducerEvidenceError("private extraction escaped the temporary root")
    os.chmod(extraction, 0o700)
    root_fd = os.open(extraction, _DIR_FLAGS)
    root_identity = _identity(os.fstat(root_fd))
    directory_fds: dict[tuple[str, ...], int] = {(): root_fd}
    directory_identities: dict[tuple[str, ...], tuple[int, int]] = {
        (): root_identity
    }
    created_files: dict[str, tuple[tuple[int, int], int, str]] = {}
    try:
        for member in joint.members:
            components = tuple(member.path.split("/"))
            for depth in range(1, len(components)):
                key = components[:depth]
                if key in directory_fds:
                    continue
                parent = directory_fds[key[:-1]]
                os.mkdir(key[-1], 0o700, dir_fd=parent)
                directory_fds[key] = os.open(key[-1], _DIR_FLAGS, dir_fd=parent)
                directory_identities[key] = _identity(os.fstat(directory_fds[key]))
            parent = directory_fds[components[:-1]]
            fd = os.open(components[-1], _CREATE_FLAGS, 0o600, dir_fd=parent)
            try:
                digest = hashlib.sha256()
                remaining = member.byte_count
                offset = member.data_offset
                while remaining:
                    chunk = os.pread(joint.archive.fd, min(1024 * 1024, remaining), offset)
                    if not chunk:
                        _auth(f"archive member became truncated: {member.path}")
                    _write_all(fd, chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                    offset += len(chunk)
                if "sha256:" + digest.hexdigest() != member.raw_bytes_sha256:
                    _auth(f"archive member changed during extraction: {member.path}")
                os.fchmod(fd, 0o400)
                os.fsync(fd)
                created_files[member.path] = (
                    _identity(os.fstat(fd)),
                    member.byte_count,
                    member.raw_bytes_sha256,
                )
            finally:
                os.close(fd)
        for key in sorted(
            (key for key in directory_fds if key), key=lambda item: (-len(item), item)
        ):
            os.fchmod(directory_fds[key], 0o500)
            os.fsync(directory_fds[key])
        os.fsync(root_fd)
        # Closed-world and identity revalidation through pinned directories.
        expected_children: dict[tuple[str, ...], set[str]] = {}
        for member in joint.members:
            parts = tuple(member.path.split("/"))
            for depth, component in enumerate(parts):
                expected_children.setdefault(parts[:depth], set()).add(component)
        for key, fd in directory_fds.items():
            if set(os.listdir(fd)) != expected_children.get(key, set()):
                _auth("private extraction is not a closed world")
            if key:
                parent = directory_fds[key[:-1]]
                entry = os.stat(key[-1], dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(entry.st_mode)
                    or _identity(entry) != directory_identities[key]
                    or _identity(os.fstat(fd)) != directory_identities[key]
                    or stat.S_IMODE(entry.st_mode) != 0o500
                ):
                    _auth("private extraction directory identity changed")
        for member in joint.members:
            parts = tuple(member.path.split("/"))
            parent = directory_fds[parts[:-1]]
            value = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            expected_identity, expected_count, expected_digest = created_files[
                member.path
            ]
            if (
                not stat.S_ISREG(value.st_mode)
                or _identity(value) != expected_identity
                or stat.S_IMODE(value.st_mode) != 0o400
            ):
                _auth(f"extracted member identity changed: {member.path}")
            verify_fd = os.open(parts[-1], _READ_FLAGS, dir_fd=parent)
            try:
                before = os.fstat(verify_fd)
                digest = hashlib.sha256()
                count = 0
                while True:
                    chunk = os.read(verify_fd, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    count += len(chunk)
                after = os.fstat(verify_fd)
                if (
                    _identity(before) != expected_identity
                    or _stat_key(before) != _stat_key(after)
                    or count != expected_count
                    or "sha256:" + digest.hexdigest() != expected_digest
                ):
                    _auth(f"extracted member bytes changed: {member.path}")
            finally:
                os.close(verify_fd)
        return extraction, root_fd, root_identity
    except BaseException:
        for fd in set(directory_fds.values()):
            if fd != root_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass
        try:
            _cleanup_extraction(extraction, root_fd, root_identity)
        except BaseException:
            pass
        raise
    finally:
        for key, fd in list(directory_fds.items()):
            if key:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _cleanup_directory_fd(fd: int) -> None:
    os.fchmod(fd, 0o700)
    for name in os.listdir(fd):
        value = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(value.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            try:
                if _identity(os.fstat(child)) != _identity(value):
                    raise ProducerEvidenceError("cleanup directory identity changed")
                _cleanup_directory_fd(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


def _cleanup_extraction(
    path: Path, root_fd: int, root_identity: tuple[int, int],
) -> None:
    failure: BaseException | None = None
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        if path == temp_root or path.parent != temp_root or not path.name.startswith(
            "validated-evidence-"
        ):
            raise ProducerEvidenceError("cleanup path is outside the private allowlist")
        _cleanup_directory_fd(root_fd)
        try:
            pathname = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise ProducerEvidenceError("private extraction path disappeared") from exc
        if (
            _identity(pathname) != root_identity
            or _identity(os.fstat(root_fd)) != root_identity
        ):
            raise ProducerEvidenceError(
                "private extraction root identity changed before final cleanup"
            )
    except ProducerEvidenceError as exc:
        failure = exc
    except OSError as exc:
        failure = ProducerEvidenceError("private extraction cleanup traversal failed")
        failure.__cause__ = exc
    finally:
        try:
            os.close(root_fd)
        except OSError as exc:
            if failure is None:
                failure = ProducerEvidenceError(
                    "private extraction descriptor close failed"
                )
                failure.__cause__ = exc
    if failure is not None:
        raise failure
    try:
        os.rmdir(path)
    except OSError as exc:
        raise ProducerEvidenceError("private extraction cleanup failed") from exc


def _build_capability_api():
    """Build the only three objects allowed to escape the live-state closure."""

    @dataclass(frozen=True)
    class State:
        root: Path
        root_fd: int
        root_identity: tuple[int, int]
        snapshot_id: str
        frozen_at: str
        snapshot_sha256: str
        archive_sha256: str
        bindings: tuple[tuple[str, tuple[tuple[str, object], ...]], ...]
        members: tuple[tuple[str, str], ...]

    states: weakref.WeakKeyDictionary[object, State] = weakref.WeakKeyDictionary()

    def require(capability: object) -> State:
        if not isinstance(capability, Capability):
            raise ProducerEvidenceError("validated snapshot capability is invalid")
        state = states.get(capability)
        if state is None:
            raise ProducerEvidenceError("validated snapshot capability is inactive")
        try:
            value = os.fstat(state.root_fd)
            pathname = os.stat(state.root, follow_symlinks=False)
        except OSError as exc:
            raise ProducerEvidenceError(
                "validated snapshot extraction is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(value.st_mode)
            or _identity(value) != state.root_identity
            or _identity(pathname) != state.root_identity
            or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) != 0o700
        ):
            raise ProducerEvidenceError(
                "validated snapshot extraction identity was reused or changed"
            )
        return state

    class Capability:
        """Immutable handle whose authority exists only in this closure."""

        __slots__ = ("__weakref__",)

        def __new__(cls, *args: object, **kwargs: object):
            del cls, args, kwargs
            raise ProducerEvidenceError(
                "ValidatedEvidenceSnapshot can only be created by "
                "open_evidence_snapshot"
            )

        def __setattr__(self, name: str, value: object) -> None:
            del self, name, value
            raise ProducerEvidenceError(
                "validated snapshot capabilities are immutable"
            )

        def __copy__(self):
            del self
            raise ProducerEvidenceError(
                "validated snapshot capabilities cannot be copied"
            )

        def __deepcopy__(self, memo):
            del self, memo
            raise ProducerEvidenceError(
                "validated snapshot capabilities cannot be copied"
            )

        def require_active(self) -> None:
            require(self)

        @property
        def snapshot_id(self) -> str:
            return require(self).snapshot_id

        @property
        def frozen_at(self) -> str:
            return require(self).frozen_at

        @property
        def snapshot_sha256(self) -> str:
            return require(self).snapshot_sha256

        @property
        def archive_sha256(self) -> str:
            return require(self).archive_sha256

        @property
        def bindings(self) -> tuple[tuple[str, dict[str, object]], ...]:
            state = require(self)
            return tuple((name, dict(values)) for name, values in state.bindings)

        def resolve_member(self, binding_name: str) -> Path:
            state = require(self)
            members = dict(state.members)
            if not isinstance(binding_name, str) or binding_name not in members:
                raise ProducerEvidenceError(
                    f"unknown snapshot binding: {binding_name!r}"
                )
            path = state.root / members[binding_name]
            try:
                value = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ProducerEvidenceError(
                    f"snapshot member is unavailable: {binding_name}"
                ) from exc
            if (
                not stat.S_ISREG(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o400
            ):
                raise ProducerEvidenceError(
                    f"snapshot member changed or became unsafe: {binding_name}"
                )
            return path

    Capability.__name__ = "ValidatedEvidenceSnapshot"
    Capability.__qualname__ = "ValidatedEvidenceSnapshot"

    @contextmanager
    def open_snapshot(
        *, surface: str, run_id: str, result_sha256: str, snapshot_root: Path,
    ) -> Iterator[Capability]:
        joint = _recover_joint(
            surface=surface, run_id=run_id, result_sha256=result_sha256,
            snapshot_root=snapshot_root,
        )
        extraction: Path | None = None
        extraction_fd: int | None = None
        capability: Capability | None = None
        try:
            extraction, extraction_fd, extraction_identity = _safe_extract(joint)
            commit_bytes = _recheck_published(
                joint.root, joint.commit, maximum=MAX_COMMIT_BYTES,
                label="snapshot commit",
            )
            _parse_commit_bytes(
                commit_bytes, surface=surface, run_id=run_id,
                result_sha256=result_sha256,
                snapshot_id=_validate_identifiers(surface, run_id, result_sha256),
            )
            archive_bytes = _recheck_published(
                joint.root, joint.archive, maximum=MAX_ARCHIVE_BYTES,
                label="snapshot archive",
            )
            if _parse_archive_bytes(archive_bytes) != joint.members:
                _auth("snapshot archive changed during private extraction")
            _final_joint_identity(joint)
            bindings = tuple(
                (name, tuple(sorted(dict(value).items())))
                for name, value in sorted(dict(joint.record["bindings"]).items())
            )
            capability = object.__new__(Capability)
            states[capability] = State(
                root=extraction,
                root_fd=extraction_fd,
                root_identity=extraction_identity,
                snapshot_id=str(joint.record["snapshot_id"]),
                frozen_at=str(joint.record["frozen_at"]),
                snapshot_sha256=str(joint.record["snapshot_sha256"]),
                archive_sha256=str(joint.record["archive_sha256"]),
                bindings=bindings,
                members=tuple(
                    (name, str(dict(values)["member_path"]))
                    for name, values in bindings
                ),
            )
            yield capability
        finally:
            if capability is not None:
                states.pop(capability, None)
            _close_joint(joint)
            if extraction is not None and extraction_fd is not None:
                _cleanup_extraction(extraction, extraction_fd, extraction_identity)

    def resolve_runtime(snapshot: Capability) -> Path:
        """Read-only internal replay adapter for the fixed live runtime child."""
        state = require(snapshot)
        runtime_fd: int | None = None
        try:
            runtime_fd = os.open("runtime", _DIR_FLAGS, dir_fd=state.root_fd)
            value = os.fstat(runtime_fd)
            entry = os.stat(
                "runtime", dir_fd=state.root_fd, follow_symlinks=False
            )
            if (
                not stat.S_ISDIR(value.st_mode)
                or _identity(value) != _identity(entry)
                or stat.S_IMODE(value.st_mode) != 0o500
            ):
                raise ProducerEvidenceError("snapshot runtime child is unsafe")
            return state.root / "runtime"
        except OSError as exc:
            raise ProducerEvidenceError(
                "snapshot runtime child is unavailable"
            ) from exc
        finally:
            if runtime_fd is not None:
                os.close(runtime_fd)

    open_snapshot.__name__ = "open_evidence_snapshot"
    resolve_runtime.__name__ = "_resolve_runtime_for_replay"
    return Capability, open_snapshot, resolve_runtime


(
    ValidatedEvidenceSnapshot,
    open_evidence_snapshot,
    _resolve_runtime_for_replay,
) = _build_capability_api()
del _build_capability_api


__all__ = [
    "EvidenceSnapshot",
    "ValidatedEvidenceSnapshot",
    "create_evidence_snapshot",
    "recover_evidence_snapshot_publication",
    "open_evidence_snapshot",
]
