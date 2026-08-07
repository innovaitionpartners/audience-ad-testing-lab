"""Replay one unchanged producer inside an exact write-allowlisted sandbox.

The public input to this internal adapter is a live validated snapshot
capability.  Runtime and member paths are derived from that capability and
never accepted from a caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

from ...common import canonical_json_bytes
from .evidence_errors import (
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerRuntimeUnavailable,
)
from .evidence_snapshot import (
    ValidatedEvidenceSnapshot,
    _resolve_runtime_for_replay,
)
from .producer_semantics import (
    REPLAY_BOOTSTRAP_SOURCE,
    SCRIPTS_ROOT,
    _validate_import_trace,
    _validate_staged_closure,
)


BOOTSTRAP_SHA256 = (
    "sha256:e567c1fe9d73377dd2829cf649f73510c2476be09024ae898fd4d98340b111be"
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESULT_BINDING_KEYS = frozenset({
    "path",
    "raw_bytes_sha256",
    "canonical_document_sha256",
    "record_count",
})
_SNAPSHOT_BINDING_KEYS = frozenset({
    "member_path",
    "raw_bytes_sha256",
    "canonical_document_sha256",
    "record_count",
})
_SCREENING_ROLES = frozenset({
    "study_manifest",
    "screening_jobs",
    "screening_response_projection",
    "recovery_configuration",
    "result",
})
_SCREENING_OPTIONAL_ROLES = frozenset({"command_dispatch_audit_input"})
_BOUNDARY_ROLES = frozenset({
    "study_manifest",
    "screening_result",
    "boundary_response_projection",
    "result",
})
_SURFACES = {
    "complete_exposure_ordering": "screening",
    "maxdiff_screening_ordering": "screening",
    "pairwise_boundary_ordering": "boundary",
}
_MAX_RESULT_BYTES = 256 * 1024 * 1024
_MAX_TRACE_BYTES = 1024 * 1024
_MAX_TIMEOUT_SECONDS = 3600
_DIRECT_ENTRY = "aggregate-screening.py"
_OUTPUT_NAME = "replay-result.json"
_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True)
class _SandboxProvider:
    name: str
    path: Path


@dataclass(frozen=True)
class _Member:
    role: str
    binding_name: str
    path: Path
    identity: tuple[int, int]
    stat_key: tuple[int, ...]


@dataclass(frozen=True)
class _AuthorityHop:
    parent_fd: int
    name: str
    fd: int
    stat_key: tuple[int, ...]
    relaxed_directory_membership: bool


@dataclass(frozen=True)
class _PinnedAuthority:
    label: str
    path: Path
    root_fd: int
    root_stat_key: tuple[int, ...]
    hops: tuple[_AuthorityHop, ...]
    leaf_parent_fd: int
    leaf_name: str
    leaf_fd: int
    leaf_stat_key: tuple[int, ...]
    leaf_kind: str
    expected_digest: str | None
    expected_byte_count: int | None
    symlink_target: bytes | None
    mutable_regular: bool
    relaxed_directory_membership: bool


@dataclass
class _OwnedProcessGroup:
    process: subprocess.Popen[bytes]
    pgid: int
    released: bool = False


def _auth(message: str, exc: BaseException | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _unavailable(message: str, exc: BaseException | None = None) -> None:
    if exc is None:
        raise ProducerRuntimeUnavailable(message)
    raise ProducerRuntimeUnavailable(message) from exc


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _read_fd_bytes(fd: int, *, limit: int, label: str) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset <= limit:
        try:
            chunk = os.pread(fd, min(1024 * 1024, limit + 1 - offset), offset)
        except OSError as exc:
            _auth(f"{label} could not be read through its retained descriptor", exc)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    if offset > limit:
        _auth(f"{label} exceeds its authenticated byte count")
    return b"".join(chunks)


def _directory_replacement_is_possible(
    value: os.stat_result,
    parent_fd: int,
) -> bool:
    euid = os.geteuid()
    if euid == 0 or value.st_uid == euid:
        return True
    groups = {os.getegid(), *os.getgroups()}
    permissions = stat.S_IMODE(value.st_mode)
    if value.st_gid in groups:
        mode_allows = bool(
            permissions & stat.S_IWGRP and permissions & stat.S_IXGRP
        )
    else:
        mode_allows = bool(
            permissions & stat.S_IWOTH and permissions & stat.S_IXOTH
        )
    if mode_allows:
        return True
    try:
        return os.access(
            ".",
            os.W_OK | os.X_OK,
            dir_fd=parent_fd,
            effective_ids=True,
        )
    except (NotImplementedError, OSError, TypeError):
        return True


def _system_directory_membership_is_external(
    entry: os.stat_result,
    parent: os.stat_result,
    parent_fd: int,
) -> bool:
    """Whether this root-owned directory's membership is outside euid control."""
    if (
        os.geteuid() == 0
        or entry.st_uid != 0
        or not stat.S_ISDIR(entry.st_mode)
        or parent.st_uid == os.geteuid()
    ):
        return False
    if not _directory_replacement_is_possible(parent, parent_fd):
        return True
    if (
        parent.st_mode & stat.S_ISVTX
        and entry.st_uid != os.geteuid()
        and parent.st_uid != os.geteuid()
    ):
        return True
    return False


def _open_symlink_fd(name: str, parent_fd: int) -> int:
    flags = getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_SYMLINK"):
        flags |= os.O_RDONLY | getattr(os, "O_SYMLINK")
    elif hasattr(os, "O_PATH"):
        flags |= getattr(os, "O_PATH") | getattr(os, "O_NOFOLLOW", 0)
    else:
        _unavailable("this platform cannot retain a no-follow interpreter symlink")
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        _auth("bound interpreter symlink could not be retained", exc)
    raise AssertionError("unreachable")


def _pin_authority(
    path: Path,
    *,
    anchor: Path,
    label: str,
    expected_digest: str | None = None,
    expected_byte_count: int | None = None,
    mutable_regular: bool = False,
    retained_leaf_fd: int | None = None,
    relax_system_directories: bool = False,
) -> _PinnedAuthority:
    """Retain a no-follow descriptor chain from one trusted parent anchor."""
    try:
        canonical_anchor = anchor.resolve(strict=True)
        canonical_parent = path.parent.resolve(strict=True)
        canonical_path = canonical_parent / path.name
        relative = canonical_path.relative_to(canonical_anchor)
    except (OSError, ValueError) as exc:
        _auth(f"{label} is not below its authenticated authority anchor", exc)
    if not relative.parts:
        _auth(f"{label} must name one entry below its authority anchor")
    try:
        root_fd = os.open(canonical_anchor, _DIR_FLAGS)
        root_value = os.fstat(root_fd)
    except OSError as exc:
        _auth(f"{label} authority anchor could not be retained", exc)
    hops: list[_AuthorityHop] = []
    parent_fd = root_fd
    leaf_fd: int | None = None
    try:
        if not stat.S_ISDIR(root_value.st_mode):
            _auth(f"{label} authority anchor is not a real directory")
        for component in relative.parts[:-1]:
            try:
                fd = os.open(component, _DIR_FLAGS, dir_fd=parent_fd)
                entry = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                value = os.fstat(fd)
            except OSError as exc:
                _auth(f"{label} ancestor could not be retained", exc)
            if (
                not stat.S_ISDIR(value.st_mode)
                or _stat_key(entry) != _stat_key(value)
            ):
                os.close(fd)
                _auth(f"{label} ancestor is not one stable real directory")
            hops.append(
                _AuthorityHop(
                    parent_fd,
                    component,
                    fd,
                    _stat_key(value),
                    (
                        relax_system_directories
                        and _system_directory_membership_is_external(
                            value, os.fstat(parent_fd), parent_fd
                        )
                    ),
                )
            )
            parent_fd = fd
        leaf_name = relative.parts[-1]
        entry = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        if retained_leaf_fd is not None:
            leaf_fd = os.dup(retained_leaf_fd)
        elif stat.S_ISLNK(entry.st_mode):
            leaf_fd = _open_symlink_fd(leaf_name, parent_fd)
        elif stat.S_ISDIR(entry.st_mode):
            leaf_fd = os.open(leaf_name, _DIR_FLAGS, dir_fd=parent_fd)
        else:
            leaf_fd = os.open(leaf_name, _READ_FLAGS, dir_fd=parent_fd)
        leaf_value = os.fstat(leaf_fd)
        if _stat_key(entry) != _stat_key(leaf_value):
            _auth(f"{label} entry does not equal its retained descriptor")
        if stat.S_ISREG(leaf_value.st_mode):
            leaf_kind = "regular"
            if leaf_value.st_nlink != 1:
                _auth(f"{label} must be one unlinked-to regular inode")
        elif stat.S_ISDIR(leaf_value.st_mode):
            leaf_kind = "directory"
        elif stat.S_ISLNK(leaf_value.st_mode):
            leaf_kind = "symlink"
        else:
            _auth(f"{label} has an unsupported inode type")
        if mutable_regular and leaf_kind != "regular":
            _auth(f"{label} mutable output must be a regular file")
        relaxed_directory_membership = (
            relax_system_directories
            and leaf_kind == "directory"
            and _system_directory_membership_is_external(
                leaf_value, os.fstat(parent_fd), parent_fd
            )
        )
        symlink_target = (
            os.readlink(os.fsencode(leaf_name), dir_fd=parent_fd)
            if leaf_kind == "symlink"
            else None
        )
        if leaf_kind == "regular" and not mutable_regular:
            if expected_digest is not None or expected_byte_count is not None:
                read_limit = (
                    expected_byte_count
                    if expected_byte_count is not None
                    else _MAX_RESULT_BYTES
                )
            else:
                read_limit = _MAX_RESULT_BYTES
            raw = _read_fd_bytes(leaf_fd, limit=read_limit, label=label)
            if expected_digest is None:
                expected_digest = _digest(raw)
            if expected_byte_count is None:
                expected_byte_count = len(raw)
            if len(raw) != expected_byte_count or _digest(raw) != expected_digest:
                _auth(f"{label} bytes do not equal their authenticated binding")
        elif expected_digest is not None or expected_byte_count is not None:
            if leaf_kind != "regular" or mutable_regular:
                _auth(f"{label} byte binding requires one immutable regular file")
        return _PinnedAuthority(
            label=label,
            path=canonical_path,
            root_fd=root_fd,
            root_stat_key=_stat_key(root_value),
            hops=tuple(hops),
            leaf_parent_fd=parent_fd,
            leaf_name=leaf_name,
            leaf_fd=leaf_fd,
            leaf_stat_key=_stat_key(leaf_value),
            leaf_kind=leaf_kind,
            expected_digest=expected_digest,
            expected_byte_count=expected_byte_count,
            symlink_target=symlink_target,
            mutable_regular=mutable_regular,
            relaxed_directory_membership=relaxed_directory_membership,
        )
    except BaseException:
        if leaf_fd is not None:
            try:
                os.close(leaf_fd)
            except OSError:
                pass
        for hop in reversed(hops):
            try:
                os.close(hop.fd)
            except OSError:
                pass
        try:
            os.close(root_fd)
        except OSError:
            pass
        raise


def _stable_mutable_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
    )


def _stable_mutable_stat_key(value: tuple[int, ...]) -> tuple[int, ...]:
    return (value[0], value[1], value[2], value[3], value[4], value[8])


def _stable_anchor_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def _stable_anchor_stat_key(value: tuple[int, ...]) -> tuple[int, ...]:
    return (value[0], value[1], value[2], value[3], value[4])


def _recheck_authority(authority: _PinnedAuthority) -> None:
    try:
        if (
            _stable_anchor_key(os.fstat(authority.root_fd))
            != _stable_anchor_stat_key(authority.root_stat_key)
        ):
            _auth(f"{authority.label} authority anchor changed")
        for hop in authority.hops:
            entry = os.stat(
                hop.name, dir_fd=hop.parent_fd, follow_symlinks=False
            )
            value = os.fstat(hop.fd)
            if hop.relaxed_directory_membership:
                changed = (
                    _stable_anchor_key(entry)
                    != _stable_anchor_stat_key(hop.stat_key)
                    or _stable_anchor_key(value)
                    != _stable_anchor_stat_key(hop.stat_key)
                )
            else:
                changed = (
                    _stat_key(entry) != hop.stat_key
                    or _stat_key(value) != hop.stat_key
                )
            if changed or not stat.S_ISDIR(value.st_mode):
                _auth(f"{authority.label} ancestor changed")
        entry = os.stat(
            authority.leaf_name,
            dir_fd=authority.leaf_parent_fd,
            follow_symlinks=False,
        )
        value = os.fstat(authority.leaf_fd)
        if authority.mutable_regular:
            baseline = _stable_mutable_stat_key(authority.leaf_stat_key)
            if (
                _stable_mutable_key(entry) != baseline
                or _stable_mutable_key(value) != baseline
                or not stat.S_ISREG(value.st_mode)
            ):
                _auth(f"{authority.label} output identity changed")
        elif authority.relaxed_directory_membership:
            if (
                _stable_anchor_key(entry)
                != _stable_anchor_stat_key(authority.leaf_stat_key)
                or _stable_anchor_key(value)
                != _stable_anchor_stat_key(authority.leaf_stat_key)
                or not stat.S_ISDIR(value.st_mode)
            ):
                _auth(f"{authority.label} entry or descriptor changed")
        elif (
            _stat_key(entry) != authority.leaf_stat_key
            or _stat_key(value) != authority.leaf_stat_key
        ):
            _auth(f"{authority.label} entry or descriptor changed")
        if authority.leaf_kind == "symlink":
            if (
                not stat.S_ISLNK(entry.st_mode)
                or os.readlink(
                    os.fsencode(authority.leaf_name),
                    dir_fd=authority.leaf_parent_fd,
                )
                != authority.symlink_target
            ):
                _auth(f"{authority.label} symlink target changed")
        elif authority.leaf_kind == "regular":
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                _auth(f"{authority.label} is no longer one regular inode")
            if not authority.mutable_regular:
                raw = _read_fd_bytes(
                    authority.leaf_fd,
                    limit=(
                        authority.expected_byte_count
                        if authority.expected_byte_count is not None
                        else _MAX_RESULT_BYTES
                    ),
                    label=authority.label,
                )
                if (
                    authority.expected_byte_count is not None
                    and len(raw) != authority.expected_byte_count
                ) or (
                    authority.expected_digest is not None
                    and _digest(raw) != authority.expected_digest
                ):
                    _auth(f"{authority.label} bytes changed")
    except ProducerAuthenticationError:
        raise
    except OSError as exc:
        _auth(f"{authority.label} authority could not be rechecked", exc)


def _close_authorities(authorities: Sequence[_PinnedAuthority]) -> None:
    seen: set[int] = set()
    for authority in reversed(authorities):
        for fd in (
            authority.leaf_fd,
            *(hop.fd for hop in reversed(authority.hops)),
            authority.root_fd,
        ):
            if fd in seen:
                continue
            seen.add(fd)
            try:
                os.close(fd)
            except OSError:
                pass


def _pin_interpreter_authorities(path: Path) -> list[_PinnedAuthority]:
    """Resolve an executable from `/` while retaining every lexical entry."""
    absolute = Path(os.path.abspath(path))
    pending = list(absolute.parts[1:])
    current = Path("/")
    authorities: list[_PinnedAuthority] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    link_count = 0
    try:
        while pending:
            state = (str(current), tuple(pending))
            if state in seen:
                _auth("bound interpreter pathname contains a symlink cycle")
            seen.add(state)
            component = pending.pop(0)
            candidate = current / component
            authority = _pin_authority(
                candidate,
                anchor=Path("/"),
                label=f"bound interpreter entry {len(authorities)}",
                relax_system_directories=True,
            )
            authorities.append(authority)
            if authority.leaf_kind == "symlink":
                link_count += 1
                if link_count > 40:
                    _auth(
                        "bound interpreter pathname exceeds the symlink "
                        "traversal limit"
                    )
                target_text = authority.symlink_target
                if not isinstance(target_text, bytes):
                    _auth("bound interpreter symlink target is unavailable")
                try:
                    decoded_target = os.fsdecode(target_text)
                    if os.fsencode(decoded_target) != target_text:
                        _auth(
                            "bound interpreter symlink target is not "
                            "filesystem-byte stable"
                        )
                    target = Path(decoded_target)
                except (UnicodeError, ValueError, OSError) as exc:
                    _auth(
                        "bound interpreter symlink target cannot be decoded "
                        "exactly",
                        exc,
                    )
                replacement = target if target.is_absolute() else current / target
                normalized = Path(os.path.normpath(replacement))
                if not normalized.is_absolute():
                    _auth("bound interpreter symlink did not resolve absolutely")
                pending = list(normalized.parts[1:]) + pending
                current = Path("/")
            elif authority.leaf_kind == "directory":
                current = candidate
            elif authority.leaf_kind == "regular":
                if pending:
                    _auth(
                        "bound interpreter regular file has trailing path "
                        "components"
                    )
                value = os.fstat(authority.leaf_fd)
                if not value.st_mode & stat.S_IXUSR:
                    _auth("bound interpreter target is not owner-executable")
                return authorities
            else:
                _auth("bound interpreter pathname has an unsupported entry")
        _auth("bound interpreter pathname resolved to a directory")
    except BaseException:
        _close_authorities(authorities)
        raise
    raise AssertionError("unreachable")


def _validate_provider_file(path: Path, *, name: str) -> None:
    try:
        value = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        _unavailable(f"required sandbox provider is unavailable: {path}", exc)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != 0
        or value.st_mode & 0o022
        or not value.st_mode & stat.S_IXUSR
    ):
        _unavailable(
            f"required sandbox provider is not a protected root-owned executable: {path}"
        )
    if name == "macos-sandbox-exec-v1" and path != Path("/usr/bin/sandbox-exec"):
        _unavailable("macOS sandbox provider path is not exact")
    if name == "linux-bwrap-v1" and path != Path("/usr/bin/bwrap"):
        _unavailable("Linux sandbox provider path is not exact")


def _trusted_provider(
    *, platform_system: str | None = None
) -> _SandboxProvider:
    """Select one fixed provider without PATH lookup or caller input."""
    system = platform.system() if platform_system is None else platform_system
    if system == "Darwin":
        provider = _SandboxProvider(
            "macos-sandbox-exec-v1", Path("/usr/bin/sandbox-exec")
        )
        _validate_provider_file(provider.path, name=provider.name)
        return provider
    if system == "Linux":
        canonical = Path("/usr/bin/bwrap")
        try:
            _validate_provider_file(canonical, name="linux-bwrap-v1")
        except ProducerRuntimeUnavailable as canonical_error:
            alias = Path("/bin/bwrap")
            try:
                if alias.resolve(strict=True) != canonical:
                    raise OSError("/bin/bwrap does not resolve exactly to /usr/bin/bwrap")
                _validate_provider_file(canonical, name="linux-bwrap-v1")
            except (OSError, ProducerRuntimeUnavailable) as exc:
                _unavailable(
                    "root-owned exact /usr/bin/bwrap is unavailable",
                    canonical_error if isinstance(exc, OSError) else exc,
                )
        return _SandboxProvider("linux-bwrap-v1", canonical)
    _unavailable(f"unsupported sandbox platform: {system!r}")
    raise AssertionError("unreachable")


def _sandbox_profile(output: Path) -> str:
    """Return the literal macOS write allowlist profile."""
    text = os.fspath(output)
    if (
        not Path(text).is_absolute()
        or "\x00" in text
        or "\n" in text
        or "\r" in text
    ):
        _auth("sandbox output path is not one literal absolute path")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        f'(allow file-write* (literal "{escaped}"))'
    )


def _build_sandbox_vector(
    provider: _SandboxProvider,
    *,
    child: Sequence[str],
    output: Path,
) -> list[str]:
    if not isinstance(provider, _SandboxProvider):
        _auth("sandbox provider capability is invalid")
    child_vector = list(child)
    _assert_argument_bytes(child_vector)
    if provider.name == "macos-sandbox-exec-v1":
        if provider.path != Path("/usr/bin/sandbox-exec"):
            _auth("macOS sandbox vector uses a non-canonical provider")
        return [
            str(provider.path),
            "-p",
            _sandbox_profile(output),
            *child_vector,
        ]
    if provider.name == "linux-bwrap-v1":
        if provider.path != Path("/usr/bin/bwrap"):
            _auth("Linux sandbox vector uses a non-canonical provider")
        return [
            str(provider.path),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(output),
            str(output),
            "--",
            *child_vector,
        ]
    _auth("sandbox provider identifier is not allowlisted")
    raise AssertionError("unreachable")


def _assert_argument_bytes(vector: Sequence[str]) -> tuple[bytes, ...]:
    encoded: list[bytes] = []
    if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
        _auth("producer command vector must be an array")
    for index, argument in enumerate(vector):
        if not isinstance(argument, str) or "\x00" in argument:
            _auth(f"producer argument {index} is not one literal string")
        try:
            raw = os.fsencode(argument)
            if os.fsdecode(raw) != argument:
                _auth(f"producer argument {index} is not filesystem-byte stable")
            encoded.append(raw)
        except (UnicodeError, ValueError, OSError) as exc:
            _auth(f"producer argument {index} cannot be encoded exactly", exc)
    return tuple(encoded)


def _bound_interpreter() -> Path:
    try:
        path = Path(os.path.abspath(sys.executable))
        value = os.stat(path)
        resolved = path.resolve(strict=True)
        resolved_value = os.stat(resolved, follow_symlinks=False)
    except OSError as exc:
        _unavailable("bound Python interpreter is unavailable", exc)
    if (
        not path.is_absolute()
        or not stat.S_ISREG(value.st_mode)
        or not stat.S_ISREG(resolved_value.st_mode)
        or not value.st_mode & stat.S_IXUSR
    ):
        _unavailable("bound Python interpreter is not an executable regular file")
    return path


def _validate_result_binding(
    value: Mapping[str, object], *, surface: str
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _RESULT_BINDING_KEYS:
        _auth("expected_result_binding must contain exactly the closed fields")
    result = deepcopy(dict(value))
    expected_name = (
        "boundary-results.json"
        if surface == "pairwise_boundary_ordering"
        else "screening-model-results.json"
    )
    path = result["path"]
    if not isinstance(path, str) or path != expected_name:
        _auth("expected result path does not match the selected producer surface")
    for field in ("raw_bytes_sha256", "canonical_document_sha256"):
        if (
            not isinstance(result[field], str)
            or not _SHA256_RE.fullmatch(result[field])
        ):
            _auth(f"expected result {field} is invalid")
    if result["record_count"] is not None:
        _auth("producer result binding record_count must be exactly null")
    return result


def _validate_role_names(
    surface: str, value: Mapping[str, str]
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _auth("staged_input_bindings must be one closed mapping")
    names = dict(value)
    if surface not in _SURFACES:
        _auth("producer replay surface is unsupported")
    if _SURFACES[surface] == "screening":
        keys = set(names)
        if (
            keys != _SCREENING_ROLES
            and keys != _SCREENING_ROLES | _SCREENING_OPTIONAL_ROLES
        ):
            _auth("screening replay binding roles are not the closed surface set")
    elif set(names) != _BOUNDARY_ROLES:
        _auth("boundary replay binding roles are not the closed surface set")
    for role, binding_name in names.items():
        if (
            not isinstance(role, str)
            or not isinstance(binding_name, str)
            or binding_name != role
        ):
            _auth(
                f"producer replay role {role!r} must select its exact binding name"
            )
    return names


def _snapshot_binding_map(
    snapshot: ValidatedEvidenceSnapshot,
) -> dict[str, dict[str, object]]:
    try:
        raw = snapshot.bindings
    except ProducerEvidenceError:
        raise
    result: dict[str, dict[str, object]] = {}
    for name, value in raw:
        if name in result or not isinstance(name, str) or not isinstance(value, dict):
            _auth("snapshot binding capability returned a malformed mapping")
        result[name] = deepcopy(value)
    return result


def _pin_members(
    snapshot: ValidatedEvidenceSnapshot,
    role_names: Mapping[str, str],
    expected_result: Mapping[str, object],
) -> tuple[dict[str, _Member], dict[str, dict[str, object]], Path]:
    bindings = _snapshot_binding_map(snapshot)
    members: dict[str, _Member] = {}
    identities: set[tuple[int, int]] = set()
    common_root: Path | None = None
    for role, binding_name in role_names.items():
        if binding_name not in bindings:
            _auth(f"snapshot omits required producer binding: {binding_name}")
        binding = bindings[binding_name]
        if set(binding) != _SNAPSHOT_BINDING_KEYS:
            _auth(f"snapshot binding is not one closed document: {binding_name}")
        for field in ("raw_bytes_sha256", "canonical_document_sha256"):
            if (
                not isinstance(binding.get(field), str)
                or not _SHA256_RE.fullmatch(str(binding[field]))
            ):
                _auth(f"snapshot binding {field} is invalid: {binding_name}")
        record_count = binding.get("record_count")
        if (
            record_count is not None
            and (
                isinstance(record_count, bool)
                or not isinstance(record_count, int)
                or record_count < 0
            )
        ):
            _auth(f"snapshot binding record_count is invalid: {binding_name}")
        try:
            path = snapshot.resolve_member(binding_name)
            value = os.stat(path, follow_symlinks=False)
        except (OSError, ProducerEvidenceError) as exc:
            _auth(f"snapshot binding is unavailable or unsafe: {binding_name}", exc)
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o400
            or value.st_nlink != 1
        ):
            _auth(f"snapshot binding member is not immutable: {binding_name}")
        identity = _identity(value)
        if identity in identities:
            _auth("producer replay roles alias one extracted member")
        identities.add(identity)
        member_path = binding.get("member_path")
        if not isinstance(member_path, str) or not member_path:
            _auth(f"snapshot binding member_path is invalid: {binding_name}")
        candidate_root = path
        for _part in Path(member_path).parts:
            candidate_root = candidate_root.parent
        if common_root is None:
            common_root = candidate_root
        elif candidate_root != common_root:
            _auth("producer replay bindings are not inside one live extraction")
        members[role] = _Member(
            role,
            binding_name,
            path,
            identity,
            _stat_key(value),
        )
    result_binding = bindings[role_names["result"]]
    member_path = result_binding.get("member_path")
    authenticated_result = {
        "path": Path(member_path).name if isinstance(member_path, str) else None,
        "raw_bytes_sha256": result_binding.get("raw_bytes_sha256"),
        "canonical_document_sha256": result_binding.get(
            "canonical_document_sha256"
        ),
        "record_count": result_binding.get("record_count"),
    }
    if authenticated_result != dict(expected_result):
        _auth("expected producer result is not the authenticated snapshot result")
    if common_root is None:
        _auth("producer replay selected no live extraction")
    return members, bindings, common_root


def _recheck_members(
    snapshot: ValidatedEvidenceSnapshot,
    members: Mapping[str, _Member],
) -> None:
    snapshot.require_active()
    for role, member in members.items():
        try:
            selected = snapshot.resolve_member(member.binding_name)
            value = os.stat(selected, follow_symlinks=False)
        except (OSError, ProducerEvidenceError) as exc:
            _auth(f"producer replay input changed: {role}", exc)
        if (
            selected != member.path
            or _identity(value) != member.identity
            or _stat_key(value) != member.stat_key
            or not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o400
            or value.st_nlink != 1
        ):
            _auth(f"producer replay input changed: {role}")


def _command_arguments(
    *,
    surface: str,
    members: Mapping[str, _Member],
    output: Path,
) -> list[str]:
    if _SURFACES[surface] == "screening":
        arguments = [
            "screening",
            "--manifest",
            str(members["study_manifest"].path),
            "--jobs",
            str(members["screening_jobs"].path),
            "--responses",
            str(members["screening_response_projection"].path),
        ]
        if "command_dispatch_audit_input" in members:
            arguments.extend([
                "--dispatch-audit",
                str(members["command_dispatch_audit_input"].path),
            ])
        arguments.extend([
            "--recovery-config",
            str(members["recovery_configuration"].path),
            "--output",
            str(output),
        ])
        return arguments
    return [
        "boundary",
        "--manifest",
        str(members["study_manifest"].path),
        "--screening-results",
        str(members["screening_result"].path),
        "--responses",
        str(members["boundary_response_projection"].path),
        "--output",
        str(output),
    ]


def _fixed_environment(read_only_temp: Path) -> dict[str, str]:
    return {
        "HOME": str(read_only_temp),
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(read_only_temp),
        "TMP": str(read_only_temp),
        "TMPDIR": str(read_only_temp),
    }


def _read_trace_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    count = 0
    while count <= _MAX_TRACE_BYTES:
        try:
            chunk = os.read(fd, min(65536, _MAX_TRACE_BYTES + 1 - count))
        except OSError as exc:
            _auth("producer import trace could not be read", exc)
        if not chunk:
            break
        chunks.append(chunk)
        count += len(chunk)
    if count > _MAX_TRACE_BYTES:
        _auth("producer import trace exceeds the closed byte limit")
    return b"".join(chunks)


def _signal_owned_process_group(
    pgid: int, value: int, *, leader_is_terminal: bool
) -> bool:
    try:
        os.killpg(pgid, value)
        return True
    except ProcessLookupError:
        if leader_is_terminal:
            return False
        _auth("live producer replay process group disappeared before signaling")
    except PermissionError as exc:
        # Darwin reports EPERM when the retained group contains only the
        # unreaped zombie leader.  With that leader identity still owned, this
        # is the closed no-live-member result rather than numeric PGID reuse.
        if leader_is_terminal and platform.system() == "Darwin":
            return False
        _auth("producer replay process group could not be terminated", exc)
    except OSError as exc:
        _auth("producer replay process group could not be terminated", exc)
    raise AssertionError("unreachable")


def _waitid_returncode(result: object) -> int:
    code = getattr(result, "si_code", None)
    status_value = getattr(result, "si_status", None)
    if not isinstance(status_value, int):
        _unavailable("producer replay wait status is unavailable")
    if code == os.CLD_EXITED:
        return status_value
    if code in {os.CLD_KILLED, os.CLD_DUMPED}:
        return -status_value
    _unavailable("producer replay returned a non-terminal wait status")
    raise AssertionError("unreachable")


def _require_nonreaping_waitid() -> None:
    if platform.system() not in {"Darwin", "Linux"} or any(
        not hasattr(os, name)
        for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        _unavailable(
            "producer replay requires non-reaping waitid on macOS or Linux"
        )


def _observe_leader_exit(pid: int) -> int | None:
    _require_nonreaping_waitid()
    try:
        result = os.waitid(
            os.P_PID,
            pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except (ChildProcessError, OSError) as exc:
        _unavailable("producer replay leader could not be observed safely", exc)
    if result is None or getattr(result, "si_pid", 0) == 0:
        return None
    if getattr(result, "si_pid", None) != pid:
        _unavailable("producer replay waitid returned the wrong leader")
    return _waitid_returncode(result)


def _linux_group_has_live_nonleader(pgid: int) -> bool:
    try:
        entries = os.listdir("/proc")
    except OSError as exc:
        _unavailable("Linux process table is unavailable for group cleanup", exc)
    if len(entries) > 1_000_000:
        _unavailable("Linux process table exceeds the closed scan limit")
    for name in entries:
        if not name.isascii() or not name.isdigit():
            continue
        pid = int(name)
        if pid == pgid:
            continue
        try:
            fd = os.open(
                f"/proc/{name}/stat",
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                raw = os.read(fd, 4097)
            finally:
                os.close(fd)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            _unavailable("Linux process group could not be inspected", exc)
        if len(raw) > 4096:
            _unavailable("Linux process stat row exceeds the closed byte limit")
        closing = raw.rfind(b")")
        if closing < 1:
            _unavailable("Linux process stat row is malformed")
        fields = raw[closing + 1:].split()
        if len(fields) < 3:
            _unavailable("Linux process stat row is truncated")
        try:
            process_group = int(fields[2])
        except ValueError as exc:
            _unavailable("Linux process group field is malformed", exc)
        state = fields[0]
        if process_group == pgid and state != b"Z":
            return True
    return False


def _owned_group_has_live_nonleader(pgid: int) -> bool:
    system = platform.system()
    if system == "Linux":
        return _linux_group_has_live_nonleader(pgid)
    if system == "Darwin":
        try:
            os.killpg(pgid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError as exc:
            _unavailable("Darwin process group could not be inspected", exc)
    _unavailable("producer replay process-group inspection is unsupported")
    raise AssertionError("unreachable")


def _finish_owned_process_group(
    pgid: int, observed_returncode: int | None
) -> int:
    """Signal only while the unreaped leader retains this numeric PGID."""
    _signal_owned_process_group(
        pgid,
        signal.SIGTERM,
        leader_is_terminal=observed_returncode is not None,
    )
    term_deadline = time.monotonic() + 0.25
    while time.monotonic() < term_deadline:
        if observed_returncode is None:
            observed_returncode = _observe_leader_exit(pgid)
        time.sleep(0.01)
    _signal_owned_process_group(
        pgid,
        signal.SIGKILL,
        leader_is_terminal=observed_returncode is not None,
    )
    kill_deadline = time.monotonic() + 0.5
    while observed_returncode is None and time.monotonic() < kill_deadline:
        observed_returncode = _observe_leader_exit(pgid)
        if observed_returncode is None:
            time.sleep(0.01)
    if observed_returncode is None:
        _unavailable("producer replay leader did not terminate after SIGKILL")
    # Keep the zombie leader unreaped while SIGKILL becomes visible to every
    # live non-leader.  No group operation is permitted after this check and
    # the subsequent reap.
    disappearance_deadline = time.monotonic() + 0.5
    while (
        _owned_group_has_live_nonleader(pgid)
        and time.monotonic() < disappearance_deadline
    ):
        time.sleep(0.01)
    if _owned_group_has_live_nonleader(pgid):
        _unavailable("producer replay process group retained a live descendant")
    return observed_returncode


def _reap_leader_once(
    owner: _OwnedProcessGroup,
    observed_returncode: int,
) -> int:
    _require_nonreaping_waitid()
    try:
        result = os.waitid(os.P_PID, owner.pgid, os.WEXITED)
    except (ChildProcessError, OSError) as exc:
        _unavailable("producer replay leader could not be reaped exactly once", exc)
    # waitid without WNOWAIT has released the numeric identity.  Mark that
    # state before any validation that might raise so cleanup can never signal
    # a reused PGID.
    owner.released = True
    owner.process.returncode = observed_returncode
    if result is None or getattr(result, "si_pid", None) != owner.pgid:
        _unavailable("producer replay reap returned the wrong leader")
    returncode = _waitid_returncode(result)
    owner.process.returncode = returncode
    return returncode


def _execute_sandbox(
    vector: Sequence[str],
    *,
    environment: Mapping[str, str],
    read_fd: int,
    write_fd: int,
    timeout_seconds: int,
) -> tuple[int, bytes]:
    _require_nonreaping_waitid()
    trace_state: dict[str, object] = {}

    def consume_trace() -> None:
        try:
            trace_state["value"] = _read_trace_fd(read_fd)
        except BaseException as exc:
            trace_state["error"] = exc

    trace_thread = threading.Thread(
        target=consume_trace,
        name="producer-import-trace-reader",
        daemon=True,
    )
    trace_thread.start()
    owner: _OwnedProcessGroup | None = None
    observed_returncode: int | None = None
    group_finished = False
    write_open = True
    read_open = True
    try:
        process = subprocess.Popen(
            list(vector),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(environment),
            close_fds=True,
            pass_fds=(write_fd,),
            start_new_session=True,
        )
        owner = _OwnedProcessGroup(process=process, pgid=process.pid)
        os.close(write_fd)
        write_open = False
        deadline = time.monotonic() + timeout_seconds
        while observed_returncode is None:
            observed_returncode = _observe_leader_exit(owner.pgid)
            if observed_returncode is not None:
                break
            error = trace_state.get("error")
            if isinstance(error, BaseException):
                raise error
            if time.monotonic() >= deadline:
                raise TimeoutError("producer replay exceeded its timeout")
            time.sleep(0.01)
        observed_returncode = _finish_owned_process_group(
            owner.pgid, observed_returncode
        )
        group_finished = True
        trace_thread.join(timeout=0.5)
        if trace_thread.is_alive():
            os.close(read_fd)
            read_open = False
            trace_thread.join(timeout=0.25)
        if trace_thread.is_alive():
            _auth("producer import trace reader did not terminate")
        error = trace_state.get("error")
        if isinstance(error, BaseException):
            raise error
        trace = trace_state.get("value")
        if not isinstance(trace, bytes):
            _auth("producer import trace reader returned no complete bytes")
        returncode = _reap_leader_once(owner, observed_returncode)
        if returncode != observed_returncode:
            _auth("producer replay exit status changed before exact reap")
        return returncode, trace
    except TimeoutError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        _unavailable("sandbox provider could not execute the producer replay", exc)
    finally:
        if (
            owner is not None
            and not owner.released
        ):
            if not group_finished:
                observed_returncode = _finish_owned_process_group(
                    owner.pgid, observed_returncode
                )
                group_finished = True
        if write_open:
            try:
                os.close(write_fd)
            except OSError:
                pass
        if read_open:
            try:
                os.close(read_fd)
            except OSError:
                pass
        trace_thread.join(timeout=0.25)
        if (
            owner is not None
            and not owner.released
        ):
            if observed_returncode is None:
                _unavailable(
                    "producer replay cleanup lacks a terminal leader status"
                )
            reaped_returncode = _reap_leader_once(
                owner, observed_returncode
            )
            if (
                observed_returncode is not None
                and reaped_returncode != observed_returncode
            ):
                _auth("producer replay exit status changed before cleanup reap")


def _open_output(directory_fd: int) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(_OUTPUT_NAME, _CREATE_FLAGS, 0o600, dir_fd=directory_fd)
        value = os.fstat(fd)
    except FileExistsError as exc:
        _auth("private replay output path unexpectedly exists", exc)
    except OSError as exc:
        _auth("private replay output could not be precreated", exc)
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
    ):
        os.close(fd)
        _auth("private replay output is not one mode-0600 regular inode")
    return fd, _identity(value)


def _read_result_output(
    *,
    directory_fd: int,
    output_fd: int,
    output_identity: tuple[int, int],
) -> bytes:
    try:
        entry = os.stat(
            _OUTPUT_NAME, dir_fd=directory_fd, follow_symlinks=False
        )
        value = os.fstat(output_fd)
        if (
            not stat.S_ISREG(entry.st_mode)
            or _identity(entry) != output_identity
            or _identity(value) != output_identity
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_uid != os.geteuid()
            or value.st_nlink != 1
            or value.st_size > _MAX_RESULT_BYTES
        ):
            _auth("producer replay output inode was replaced or became unsafe")
        os.lseek(output_fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        count = 0
        while count <= _MAX_RESULT_BYTES:
            chunk = os.read(
                output_fd, min(1024 * 1024, _MAX_RESULT_BYTES + 1 - count)
            )
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        if count > _MAX_RESULT_BYTES:
            _auth("producer replay output exceeds its closed byte limit")
        after = os.fstat(output_fd)
        if (
            _identity(after) != output_identity
            or after.st_size != count
            or after.st_nlink != 1
        ):
            _auth("producer replay output changed while read")
        if set(os.listdir(directory_fd)) != {_OUTPUT_NAME}:
            _auth("producer replay created a write outside its one output inode")
        return b"".join(chunks)
    except ProducerAuthenticationError:
        raise
    except (OSError, MemoryError, OverflowError) as exc:
        _auth("producer replay output could not be authenticated", exc)


def _duplicate_free_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _auth(f"producer replay output contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    _auth(f"producer replay output contains non-finite constant: {value}")


def _validate_result_bytes(
    raw: bytes, expected: Mapping[str, object]
) -> None:
    if _digest(raw) != expected["raw_bytes_sha256"]:
        _auth(
            "producer replay raw result bytes do not equal the frozen result bytes"
        )
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_free_object,
            parse_constant=_reject_nonfinite,
        )
        if not isinstance(document, dict):
            _auth("producer replay output must be one JSON object")
        canonical = canonical_json_bytes(document)
    except ProducerAuthenticationError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
        OverflowError,
        TypeError,
        ValueError,
    ) as exc:
        _auth("producer replay output is not bounded finite UTF-8 JSON", exc)
    if _digest(canonical) != expected["canonical_document_sha256"]:
        _auth(
            "producer replay canonical result does not equal the frozen document"
        )


def _cleanup_private_replay(
    path: Path,
    directory_fd: int,
    output_fd: int | None,
    *,
    output_created: bool,
) -> None:
    failure: BaseException | None = None
    if output_fd is not None:
        try:
            os.close(output_fd)
        except OSError as exc:
            failure = exc
    try:
        os.fchmod(directory_fd, 0o700)
        names = set(os.listdir(directory_fd))
        if output_created and _OUTPUT_NAME in names:
            os.unlink(_OUTPUT_NAME, dir_fd=directory_fd)
            names.remove(_OUTPUT_NAME)
        if names:
            _auth("private replay directory contains an unexpected second write")
    except BaseException as exc:
        failure = failure or exc
    finally:
        try:
            os.close(directory_fd)
        except OSError as exc:
            failure = failure or exc
    if failure is None:
        try:
            os.rmdir(path)
        except OSError as exc:
            failure = exc
    if failure is not None:
        if isinstance(failure, ProducerEvidenceError):
            raise failure
        raise ProducerEvidenceError("private replay cleanup failed") from failure


def _pin_replay_authorities(
    *,
    extraction_root: Path,
    members: Mapping[str, _Member],
    bindings: Mapping[str, Mapping[str, object]],
    runtime_root: Path,
    closure: Sequence[Mapping[str, object]],
    interpreter: Path,
    provider: _SandboxProvider,
    replay_root: Path,
    output_fd: int,
) -> list[_PinnedAuthority]:
    authorities: list[_PinnedAuthority] = []

    def add(
        path: Path,
        *,
        anchor: Path,
        label: str,
        expected_digest: str | None = None,
        expected_byte_count: int | None = None,
        mutable_regular: bool = False,
        retained_leaf_fd: int | None = None,
    ) -> None:
        authorities.append(
            _pin_authority(
                path,
                anchor=anchor,
                label=label,
                expected_digest=expected_digest,
                expected_byte_count=expected_byte_count,
                mutable_regular=mutable_regular,
                retained_leaf_fd=retained_leaf_fd,
            )
        )

    try:
        add(
            extraction_root,
            anchor=extraction_root.parent,
            label="live extraction root",
        )
        for role, member in members.items():
            binding = bindings[member.binding_name]
            add(
                member.path,
                anchor=extraction_root.parent,
                label=f"live extraction member {role}",
                expected_digest=str(binding["raw_bytes_sha256"]),
                expected_byte_count=member.stat_key[5],
            )
        add(
            runtime_root,
            anchor=runtime_root.parent,
            label="staged runtime root",
        )
        for row in closure:
            relative = row["path"]
            byte_count = row["byte_count"]
            raw_digest = row["raw_bytes_sha256"]
            if (
                not isinstance(relative, str)
                or isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or not isinstance(raw_digest, str)
            ):
                _auth("sealed runtime closure row is invalid while pinning")
            add(
                runtime_root / relative,
                anchor=runtime_root.parent,
                label=f"staged runtime member {relative}",
                expected_digest=raw_digest,
                expected_byte_count=byte_count,
            )
        interpreter_entry = Path(os.path.abspath(interpreter))
        authorities.extend(_pin_interpreter_authorities(interpreter_entry))
        provider_anchor = provider.path.parent.parent
        if provider_anchor == provider.path.parent:
            provider_anchor = provider.path.parent
        add(
            provider.path,
            anchor=provider_anchor,
            label="sandbox provider executable",
        )
        add(
            replay_root / _OUTPUT_NAME,
            anchor=replay_root.parent,
            label="private replay output inode",
            mutable_regular=True,
            retained_leaf_fd=output_fd,
        )
        add(
            replay_root,
            anchor=replay_root.parent,
            label="private replay output parent",
        )
        return authorities
    except BaseException:
        _close_authorities(authorities)
        raise


def _recheck_authorities(
    authorities: Sequence[_PinnedAuthority],
) -> None:
    for authority in authorities:
        _recheck_authority(authority)


def replay_producer(
    *,
    surface: str,
    snapshot: ValidatedEvidenceSnapshot,
    staged_input_bindings: Mapping[str, str],
    expected_result_binding: Mapping[str, object],
    expected_import_trace: Sequence[Mapping[str, object]],
    timeout_seconds: int = 300,
) -> dict[str, object]:
    """Reproduce one authenticated producer result without changing its bytes."""
    if not isinstance(snapshot, ValidatedEvidenceSnapshot):
        raise ProducerEvidenceError("validated snapshot capability is invalid")
    snapshot.require_active()
    if surface not in _SURFACES:
        _auth("producer replay surface is unsupported")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= _MAX_TIMEOUT_SECONDS
    ):
        _auth("producer replay timeout must be an integer from 1 through 3600")
    expected = _validate_result_binding(
        expected_result_binding, surface=surface
    )
    role_names = _validate_role_names(surface, staged_input_bindings)
    members, bindings, extraction_root = _pin_members(
        snapshot, role_names, expected
    )
    runtime_root = _resolve_runtime_for_replay(snapshot)
    scripts_root = runtime_root / SCRIPTS_ROOT
    if not isinstance(expected_import_trace, Sequence) or isinstance(
        expected_import_trace, (str, bytes)
    ):
        _auth("expected_import_trace must be the sealed dependency envelope")
    if any(not isinstance(row, Mapping) for row in expected_import_trace):
        _auth("expected_import_trace rows must be closed mappings")
    try:
        closure = [deepcopy(dict(row)) for row in expected_import_trace]
    except (TypeError, ValueError, RecursionError, MemoryError) as exc:
        _auth("expected_import_trace could not be copied safely", exc)
    try:
        _validate_staged_closure(runtime_root, closure)
    except ProducerEvidenceError:
        raise
    except BaseException as exc:
        _auth("sealed producer source envelope is invalid", exc)
    actual_bootstrap_digest = _digest(REPLAY_BOOTSTRAP_SOURCE.encode("utf-8"))
    if actual_bootstrap_digest != BOOTSTRAP_SHA256:
        _auth("replay bootstrap bytes do not equal the sealed digest")
    provider = _trusted_provider()
    interpreter = _bound_interpreter()

    replay_root = Path(tempfile.mkdtemp(prefix="producer-replay-")).resolve(
        strict=True
    )
    os.chmod(replay_root, 0o700)
    directory_fd = os.open(replay_root, _DIR_FLAGS)
    output_fd: int | None = None
    output_created = False
    read_fd: int | None = None
    write_fd: int | None = None
    authorities: list[_PinnedAuthority] = []
    try:
        output_fd, output_identity = _open_output(directory_fd)
        output_created = True
        output = replay_root / _OUTPUT_NAME
        os.fchmod(directory_fd, 0o500)
        authorities = _pin_replay_authorities(
            extraction_root=extraction_root,
            members=members,
            bindings=bindings,
            runtime_root=runtime_root,
            closure=closure,
            interpreter=interpreter,
            provider=provider,
            replay_root=replay_root,
            output_fd=output_fd,
        )
        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(read_fd, False)
            os.set_inheritable(write_fd, True)
        except OSError as exc:
            _auth("dedicated import-trace pipe could not be protected", exc)
        producer_arguments = _command_arguments(
            surface=surface, members=members, output=output
        )
        child = [
            str(interpreter),
            "-I",
            "-B",
            "-c",
            REPLAY_BOOTSTRAP_SOURCE,
            str(scripts_root),
            _DIRECT_ENTRY,
            str(write_fd),
            "--",
            *producer_arguments,
        ]
        child_bytes = _assert_argument_bytes(child)
        vector = _build_sandbox_vector(
            provider, child=child, output=output
        )
        if _assert_argument_bytes(child) != child_bytes:
            _auth("producer argument bytes changed before sandbox launch")
        try:
            returncode, trace = _execute_sandbox(
                vector,
                environment=_fixed_environment(replay_root),
                read_fd=read_fd,
                write_fd=write_fd,
                timeout_seconds=timeout_seconds,
            )
            write_fd = None
        except TimeoutError as exc:
            _auth("producer replay timed out", exc)
        finally:
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
                read_fd = None
        _recheck_authorities(authorities)
        if returncode != 0:
            if returncode < 0:
                _auth(f"producer replay terminated by signal {-returncode}")
            _auth(f"producer replay returned nonzero exit status {returncode}")
        try:
            _validate_import_trace(
                trace, closure, staged_runtime_root=runtime_root
            )
        except ProducerEvidenceError:
            raise
        except BaseException as exc:
            _auth("producer replay import trace is invalid", exc)
        _recheck_members(snapshot, members)
        _validate_staged_closure(runtime_root, closure)
        raw = _read_result_output(
            directory_fd=directory_fd,
            output_fd=output_fd,
            output_identity=output_identity,
        )
        _validate_result_bytes(raw, expected)
        _recheck_authorities(authorities)
        _recheck_members(snapshot, members)
        snapshot.require_active()
        return deepcopy(expected)
    finally:
        if write_fd is not None:
            try:
                os.close(write_fd)
            except OSError:
                pass
        if read_fd is not None:
            try:
                os.close(read_fd)
            except OSError:
                pass
        _close_authorities(authorities)
        _cleanup_private_replay(
            replay_root,
            directory_fd,
            output_fd,
            output_created=output_created,
        )


__all__ = [
    "BOOTSTRAP_SHA256",
    "replay_producer",
]
