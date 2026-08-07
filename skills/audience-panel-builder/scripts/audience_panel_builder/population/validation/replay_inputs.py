"""Authenticate and assemble exact cross-directory producer replay inputs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

from ...common import canonical_json_bytes
from .evidence_bindings import (
    LINEAGE_ORDER,
    lineage_bundle_sha256,
)
from .evidence_errors import ProducerAuthenticationError
from .producer_semantics import REPLAY_BOOTSTRAP_SOURCE


_AD_TESTING_SCRIPTS = (
    Path(__file__).resolve().parents[5] / "audience-ad-testing-lab" / "scripts"
)
if str(_AD_TESTING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_AD_TESTING_SCRIPTS))

from audience_lab.contracts import validate_manifest  # noqa: E402
from audience_lab.lineage import (  # noqa: E402
    CANONICAL_LINEAGE_FILES,
    validate_bound_lineage,
)
from audience_lab.responses import validate_response  # noqa: E402


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SURFACES = {
    "complete_exposure_ordering": {
        "method": "complete_exposure",
        "stage": "screening",
        "result_name": "screening-model-results.json",
        "projection_role": "screening_response_projection",
        "record_type": "screening_response",
    },
    "maxdiff_screening_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "screening",
        "result_name": "screening-model-results.json",
        "projection_role": "screening_response_projection",
        "record_type": "screening_response",
    },
    "pairwise_boundary_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "boundary",
        "result_name": "boundary-results.json",
        "projection_role": "boundary_response_projection",
        "record_type": "boundary_response",
    },
}
_EVIDENCE_KEYS = frozenset({
    "schema_version",
    "surface",
    "method",
    "stage",
    "run_id",
    "frozen_at",
    "sealed_at",
    "producer_semantics",
    "input_bindings",
    "result_binding",
    "snapshot_binding",
    "producer_evidence_sha256",
})
_SEMANTICS_KEYS = frozenset({
    "entry_point",
    "subcommand",
    "bootstrap_sha256",
    "dependency_closure",
    "runtime_fingerprint",
    "policy_bindings",
    "output_serialization",
    "producer_semantics_sha256",
})
_SNAPSHOT_KEYS = frozenset({
    "snapshot_id",
    "snapshot_sha256",
    "archive_sha256",
})
_BINDING_KEYS = frozenset({
    "path",
    "raw_bytes_sha256",
    "canonical_document_sha256",
    "record_count",
})
_UPSTREAM_SCREENING_BINDING_KEYS = frozenset({
    "study_manifest",
    "accepted_responses",
    "raw_provider_returns",
    "rejected_attempts",
    "dispatch_audit",
    "command_dispatch_audit_input",
    "screening_jobs",
    "screening_response_projection",
    "recovery_configuration",
})
_DEPENDENCY_ROW_KEYS = frozenset({
    "path", "byte_count", "raw_bytes_sha256",
})
_RUNTIME_FINGERPRINT_KEYS = frozenset({
    "python_implementation",
    "python_version",
    "numpy_version",
    "scipy_version",
    "platform_system",
    "platform_release",
    "machine",
    "numpy_build_sha256",
    "blas_lapack_sha256",
})
_MAXDIFF_POLICY_KEYS = frozenset({
    "maxdiff_configuration_sha256",
    "required_bootstrap_count",
    "minimum_successful_fit_floor",
    "clear_finalist_threshold",
    "clear_non_finalist_threshold",
    "minimum_utility_tie_tolerance",
    "ordering_tiebreak",
    "ordering_equivalence",
    "effective_ordering_tolerance",
    "rounding_rule",
    "recovery_configuration_sha256",
})
_OUTPUT_SERIALIZATION_KEYS = frozenset({
    "producer_raw_serialization",
    "canonical_document_serialization",
})
_SERIALIZATION_KEYS = frozenset({
    "encoding",
    "indent",
    "sort_keys",
    "allow_nan",
    "ensure_ascii",
    "separators",
    "terminal_lf",
})
_OPEN_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_FILE_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
)


@dataclass(frozen=True)
class _ResourceLimits:
    maximum_file_bytes: int = 256 * 1024 * 1024
    maximum_aggregate_bytes: int = 1024 * 1024 * 1024
    maximum_jsonl_records: int = 100_000
    maximum_json_depth: int = 64
    maximum_container_items: int = 1_000_000
    maximum_string_bytes: int = 16 * 1024 * 1024
    maximum_scalars: int = 1_000_000


_RESOURCE_LIMITS = _ResourceLimits()


@dataclass
class _PinnedFile:
    role: str
    path: Path
    root_fd: int
    root_stat: tuple[int, ...]
    chain: tuple[tuple[int, str, int, tuple[int, ...]], ...]
    parent_fd: int
    name: str
    fd: int
    file_stat: tuple[int, ...]
    identity: tuple[int, int]
    byte_count: int
    raw_digest: str = ""
    raw: bytes = b""


@dataclass(frozen=True)
class ProducerReplayInputs:
    study_manifest: Path
    accepted_responses: Path
    raw_provider_returns: Path
    rejected_attempts: Path
    cumulative_dispatch_audit: Path
    result: Path
    screening_jobs: Path | None
    recovery_configuration: Path | None
    command_dispatch_audit_input: Path | None
    screening_result: Path | None
    screening_producer_evidence: Path | None


def _fail(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _normalize_resource_failures(message: str):
    """Map only exhaustion failures while preserving closed/security failures."""

    def decorator(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except ProducerAuthenticationError:
                raise
            except (MemoryError, RecursionError, OverflowError) as exc:
                _fail(message, exc)

        return guarded

    return decorator


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _directory_stat_key(value: os.stat_result) -> tuple[int, ...]:
    # Shared ancestors may gain unrelated children while held. Identity,
    # ownership, and type/mode are the stable authority metadata.
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid


def _read_fd_bytes(fd: int, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while total <= maximum:
            chunk = os.read(fd, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except (OSError, MemoryError, OverflowError) as exc:
        _fail(f"{label} could not be read safely", exc)
    if total > maximum:
        _fail(f"{label} exceeds its authenticated byte limit")
    try:
        return b"".join(chunks)
    except (MemoryError, OverflowError) as exc:
        _fail(f"{label} could not be assembled within resource limits", exc)


class _PinnedInputReader:
    """Hold exact path authority for every role through parse and verification."""

    @_normalize_resource_failures(
        "producer input reader construction exceeded safe resources"
    )
    def __init__(
        self,
        paths: Mapping[str, Path | None],
        limits: _ResourceLimits,
    ):
        if not isinstance(limits, _ResourceLimits) or any(
            isinstance(getattr(limits, field.name), bool)
            or not isinstance(getattr(limits, field.name), int)
            or getattr(limits, field.name) < 1
            for field in fields(_ResourceLimits)
        ):
            _fail("producer input resource limits are invalid")
        self._limits = limits
        self._files: dict[str, _PinnedFile] = {}
        try:
            for role, path in paths.items():
                if path is not None:
                    pinned = self._open(role, path)
                    try:
                        self._files[role] = pinned
                    except BaseException:
                        self._close_pinned(pinned)
                        raise
            identities = [item.identity for item in self._files.values()]
            if len(identities) != len(set(identities)):
                _fail("producer replay roles must not alias one exact file identity")
            total = sum(item.byte_count for item in self._files.values())
            if total > limits.maximum_aggregate_bytes:
                _fail("producer replay inputs exceed the aggregate byte limit")
            for item in self._files.values():
                item.raw = self._read_one(item)
                item.raw_digest = _sha256_bytes(item.raw)
            self.verify_all()
        except BaseException:
            self.close()
            raise

    def _open(self, role: str, raw_path: Path) -> _PinnedFile:
        absolute = Path(os.path.abspath(raw_path))
        if sys.platform == "darwin" and absolute.parts[:2] in {
            (os.path.sep, "var"),
            (os.path.sep, "tmp"),
        }:
            absolute = Path(os.path.sep, "private", *absolute.parts[1:])
        components = absolute.parts
        if (
            not absolute.is_absolute()
            or not components
            or components[0] != os.path.sep
            or any(component in {"", ".", ".."} for component in components[1:])
        ):
            _fail(f"{role} must be one normalized absolute path")
        opened: list[int] = []
        file_fd: int | None = None
        try:
            root_fd = os.open(os.path.sep, _OPEN_DIRECTORY_FLAGS)
            opened.append(root_fd)
            root_value = os.fstat(root_fd)
            chain: list[tuple[int, str, int, tuple[int, ...]]] = []
            parent_fd = root_fd
            for component in components[1:-1]:
                child_fd = os.open(
                    component, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd
                )
                opened.append(child_fd)
                child_value = os.fstat(child_fd)
                if not stat.S_ISDIR(child_value.st_mode):
                    _fail(f"{role} ancestor is not a real directory: {component}")
                chain.append(
                    (
                        parent_fd,
                        component,
                        child_fd,
                        _directory_stat_key(child_value),
                    )
                )
                parent_fd = child_fd
            if len(components) < 2:
                _fail(f"{role} must select a file beneath the filesystem root")
            name = components[-1]
            file_fd = os.open(name, _OPEN_FILE_FLAGS, dir_fd=parent_fd)
            value = os.fstat(file_fd)
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                _fail(f"{role} must be one unlinked-alias-free regular file")
            if value.st_size < 0 or value.st_size > self._limits.maximum_file_bytes:
                _fail(f"{role} exceeds the per-file byte limit")
            return _PinnedFile(
                role=role,
                path=absolute,
                root_fd=root_fd,
                root_stat=_directory_stat_key(root_value),
                chain=tuple(chain),
                parent_fd=parent_fd,
                name=name,
                fd=file_fd,
                file_stat=_stat_key(value),
                identity=_identity(value),
                byte_count=value.st_size,
            )
        except ProducerAuthenticationError:
            if file_fd is not None:
                os.close(file_fd)
            for fd in reversed(opened):
                os.close(fd)
            raise
        except (OSError, MemoryError, OverflowError, ValueError) as exc:
            if file_fd is not None:
                os.close(file_fd)
            for fd in reversed(opened):
                os.close(fd)
            _fail(f"{role} path is unavailable or unsafe: {absolute}", exc)

    def _read_one(self, item: _PinnedFile) -> bytes:
        raw = _read_fd_bytes(
            item.fd, self._limits.maximum_file_bytes, item.role
        )
        if len(raw) != item.byte_count:
            _fail(f"{item.role} byte length changed during read")
        return raw

    def _verify_item(self, item: _PinnedFile) -> None:
        try:
            root_fd_stat = os.fstat(item.root_fd)
            root_path_stat = os.stat(os.path.sep, follow_symlinks=False)
            if (
                _directory_stat_key(root_fd_stat) != item.root_stat
                or _directory_stat_key(root_path_stat) != item.root_stat
            ):
                _fail(f"{item.role} filesystem root authority changed")
            for parent_fd, name, child_fd, expected in item.chain:
                child_stat = os.fstat(child_fd)
                entry_stat = os.stat(
                    name, dir_fd=parent_fd, follow_symlinks=False
                )
                if (
                    _directory_stat_key(child_stat) != expected
                    or _directory_stat_key(entry_stat) != expected
                ):
                    _fail(f"{item.role} ancestor authority changed: {name}")
            file_stat = os.fstat(item.fd)
            entry_stat = os.stat(
                item.name, dir_fd=item.parent_fd, follow_symlinks=False
            )
            if (
                _stat_key(file_stat) != item.file_stat
                or _stat_key(entry_stat) != item.file_stat
                or not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_nlink != 1
            ):
                _fail(f"{item.role} file authority changed")
            raw = _read_fd_bytes(
                item.fd, self._limits.maximum_file_bytes, item.role
            )
            if (
                len(raw) != item.byte_count
                or _sha256_bytes(raw) != item.raw_digest
                or raw != item.raw
                or _stat_key(os.fstat(item.fd)) != item.file_stat
            ):
                _fail(f"{item.role} bytes changed after authentication")
        except ProducerAuthenticationError:
            raise
        except (OSError, MemoryError, OverflowError, ValueError) as exc:
            _fail(f"{item.role} could not be reauthenticated", exc)

    def verify_all(self) -> None:
        identities: list[tuple[int, int]] = []
        for item in self._files.values():
            self._verify_item(item)
            identities.append(_identity(os.fstat(item.fd)))
        if len(identities) != len(set(identities)):
            _fail("producer replay roles alias after exact-FD authentication")

    def raw(self, role: str) -> bytes:
        try:
            return self._files[role].raw
        except KeyError as exc:
            _fail(f"producer replay role was not pinned: {role}", exc)

    def path(self, role: str) -> Path:
        try:
            return self._files[role].path
        except KeyError as exc:
            _fail(f"producer replay role was not pinned: {role}", exc)

    def close(self) -> None:
        while self._files:
            _role, item = self._files.popitem()
            self._close_pinned(item)

    @staticmethod
    def _close_pinned(item: _PinnedFile) -> None:
        try:
            os.close(item.fd)
        except OSError:
            pass
        for row in reversed(item.chain):
            try:
                os.close(row[2])
            except OSError:
                pass
        try:
            os.close(item.root_fd)
        except OSError:
            pass


def _exact_mapping(
    value: object, keys: frozenset[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{label} must contain exactly the closed fields")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(f"{label} must be a prefixed SHA-256")
    return value


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")
    return value


def _timestamp(value: object, label: str) -> str:
    text = _nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        _fail(f"{label} must be an ISO 8601 timestamp", exc)
    if parsed.tzinfo is None:
        _fail(f"{label} must include a timezone")
    return text


def _parsed_timestamp(value: object, label: str) -> datetime:
    text = _timestamp(value, label)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _required_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        _fail(f"{label} must be an explicit filesystem path")
    path = Path(value)
    if (
        not str(path)
        or any(component in {"", ".", ".."} for component in path.parts)
    ):
        _fail(f"{label} must be an explicit filesystem path")
    return path


def _validate_matrix(
    surface: str, paths: ProducerReplayInputs
) -> tuple[dict[str, str], dict[str, Path | None]]:
    if surface not in _SURFACES:
        _fail("producer replay surface is unsupported")
    if not isinstance(paths, ProducerReplayInputs):
        _fail("paths must be one closed ProducerReplayInputs object")
    if set(paths.__dict__) != {field.name for field in fields(ProducerReplayInputs)}:
        _fail("ProducerReplayInputs contains missing or extra fields")
    values: dict[str, Path | None] = {}
    required = {
        "study_manifest",
        "accepted_responses",
        "raw_provider_returns",
        "rejected_attempts",
        "cumulative_dispatch_audit",
        "result",
    }
    if _SURFACES[surface]["stage"] == "screening":
        required |= {"screening_jobs", "recovery_configuration"}
        forbidden = {"screening_result", "screening_producer_evidence"}
    else:
        required |= {"screening_result", "screening_producer_evidence"}
        forbidden = {
            "screening_jobs",
            "recovery_configuration",
            "command_dispatch_audit_input",
        }
    for field in fields(ProducerReplayInputs):
        raw = getattr(paths, field.name)
        if field.name in required:
            values[field.name] = _required_path(raw, field.name)
        elif field.name in forbidden:
            if raw is not None:
                _fail(f"{field.name} must be exactly null for surface {surface}")
            values[field.name] = None
        else:
            values[field.name] = (
                None if raw is None else _required_path(raw, field.name)
            )
    return _SURFACES[surface], values


def _duplicate_free_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON object contains a duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    _fail(f"JSON contains a non-finite constant: {value}")


def _validate_json_resources(
    value: object,
    limits: _ResourceLimits,
    *,
    label: str,
) -> tuple[int, int]:
    container_items = 0
    scalars = 0
    stack: list[tuple[object, int, str]] = [(value, 1, label)]
    try:
        while stack:
            current, depth, path = stack.pop()
            if isinstance(current, Mapping):
                if depth > limits.maximum_json_depth:
                    _fail(f"{label} exceeds the JSON nesting-depth limit")
                container_items += len(current)
                if container_items > limits.maximum_container_items:
                    _fail(f"{label} exceeds the container-item limit")
                for key, item in current.items():
                    if not isinstance(key, str):
                        _fail(f"{path} has a non-string object key")
                    scalars += 1
                    if len(key.encode("utf-8")) > limits.maximum_string_bytes:
                        _fail(f"{path} object key exceeds the UTF-8 string limit")
                    stack.append((item, depth + 1, f"{path}.{key}"))
            elif isinstance(current, list):
                if depth > limits.maximum_json_depth:
                    _fail(f"{label} exceeds the JSON nesting-depth limit")
                container_items += len(current)
                if container_items > limits.maximum_container_items:
                    _fail(f"{label} exceeds the container-item limit")
                for index, item in enumerate(current):
                    stack.append((item, depth + 1, f"{path}[{index}]"))
            elif current is None or isinstance(current, (str, bool, int, float)):
                scalars += 1
                if isinstance(current, str) and (
                    len(current.encode("utf-8")) > limits.maximum_string_bytes
                ):
                    _fail(f"{path} exceeds the UTF-8 string limit")
                if isinstance(current, float) and not math.isfinite(current):
                    _fail(f"{path} must be finite")
            else:
                _fail(f"{path} is not a JSON-compatible value")
            if scalars > limits.maximum_scalars:
                _fail(f"{label} exceeds the scalar-value limit")
    except ProducerAuthenticationError:
        raise
    except (
        UnicodeError,
        RecursionError,
        MemoryError,
        OverflowError,
        ValueError,
    ) as exc:
        _fail(f"{label} exceeds safe JSON resource handling", exc)
    return container_items, scalars


@_normalize_resource_failures("JSON document allocation exceeded safe resources")
def _load_json_bytes(
    raw: bytes,
    *,
    label: str,
    limits: _ResourceLimits,
) -> dict[str, object]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            parse_constant=_reject_nonfinite_constant,
            object_pairs_hook=_duplicate_free_object,
        )
        _validate_json_resources(value, limits, label=label)
    except ProducerAuthenticationError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
        OverflowError,
        ValueError,
    ) as exc:
        _fail(f"{label} must contain one bounded finite UTF-8 JSON document", exc)
    if not isinstance(value, dict):
        _fail(f"{label} must contain one JSON object")
    return deepcopy(value)


def _canonical_bytes(value: object, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (
        UnicodeError,
        RecursionError,
        MemoryError,
        OverflowError,
        TypeError,
        ValueError,
    ) as exc:
        _fail(f"{label} could not be canonicalized safely", exc)


@_normalize_resource_failures("JSON input assembly exceeded safe resources")
def _read_json(
    role: str,
    *,
    reader: _PinnedInputReader,
    logical_path: str | None = None,
) -> tuple[dict[str, object], dict[str, object], bytes]:
    raw = reader.raw(role)
    path = logical_path or reader.path(role).name
    value = _load_json_bytes(
        raw, label=role, limits=reader._limits
    )
    canonical = _canonical_bytes(value, role)
    return (
        deepcopy(value),
        {
            "path": path,
            "raw_bytes_sha256": _sha256_bytes(raw),
            "canonical_document_sha256": _sha256_bytes(canonical),
            "record_count": None,
        },
        raw,
    )


def _append_jsonl_record(
    records: list[dict[str, object]],
    record: dict[str, object],
) -> None:
    records.append(record)


@_normalize_resource_failures("JSONL input assembly exceeded safe resources")
def _read_jsonl(
    role: str,
    *,
    reader: _PinnedInputReader,
    logical_path: str | None = None,
) -> tuple[list[dict[str, object]], dict[str, object], bytes]:
    raw = reader.raw(role)
    path = logical_path or reader.path(role).name
    if not raw.endswith(b"\n"):
        _fail(f"{role} must end every JSONL record with LF")
    record_count = raw.count(b"\n")
    if record_count > reader._limits.maximum_jsonl_records:
        _fail(f"{role} exceeds the JSONL record-count limit")
    try:
        lines = raw[:-1].split(b"\n")
    except (MemoryError, OverflowError) as exc:
        _fail(f"{role} could not be split within resource limits", exc)
    if not lines or any(not line.strip() for line in lines):
        _fail(f"{role} must contain complete non-empty JSONL records")
    records: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        _append_jsonl_record(
            records,
            _load_json_bytes(
                line, label=f"{role}[{index}]", limits=reader._limits
            ),
        )
    total_container_items = 0
    total_scalars = 0
    for index, record in enumerate(records):
        container_items, scalars = _validate_json_resources(
            record,
            reader._limits,
            label=f"{role}[{index}]",
        )
        total_container_items += container_items
        total_scalars += scalars
        if total_container_items > reader._limits.maximum_container_items:
            _fail(f"{role} exceeds the aggregate JSONL container-item limit")
        if total_scalars > reader._limits.maximum_scalars:
            _fail(f"{role} exceeds the aggregate JSONL scalar-value limit")
    try:
        canonical = b"".join(
            _canonical_bytes(record, f"{role}[{index}]")
            for index, record in enumerate(records)
        )
    except (MemoryError, OverflowError) as exc:
        _fail(f"{role} canonical JSONL exceeds safe resources", exc)
    return (
        records,
        {
            "path": path,
            "raw_bytes_sha256": _sha256_bytes(raw),
            "canonical_document_sha256": _sha256_bytes(canonical),
            "record_count": len(records),
        },
        raw,
    )


def _validate_file_names(
    spec: Mapping[str, str], values: Mapping[str, Path | None]
) -> None:
    fixed = {
        "study_manifest": "study-manifest.json",
        "accepted_responses": CANONICAL_LINEAGE_FILES["accepted_responses"],
        "raw_provider_returns": CANONICAL_LINEAGE_FILES["raw_provider_returns"],
        "rejected_attempts": CANONICAL_LINEAGE_FILES["rejected_attempts"],
        "cumulative_dispatch_audit": CANONICAL_LINEAGE_FILES["dispatch_audit"],
        "result": spec["result_name"],
    }
    if spec["stage"] == "screening":
        fixed["screening_jobs"] = "screening-jobs.json"
    else:
        fixed["screening_result"] = "screening-model-results.json"
    for role, filename in fixed.items():
        path = values[role]
        if path is None or path.name != filename:
            _fail(f"{role} must select the exact {filename} path")


def _validate_generic_binding(value: object, label: str) -> dict[str, object]:
    item = dict(_exact_mapping(value, _BINDING_KEYS, label))
    path = item["path"]
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or any(component in {"", ".", ".."} for component in path.split("/"))
    ):
        _fail(f"{label}.path must be a safe relative path")
    _digest(item["raw_bytes_sha256"], f"{label}.raw_bytes_sha256")
    _digest(
        item["canonical_document_sha256"],
        f"{label}.canonical_document_sha256",
    )
    count = item["record_count"]
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count < 1
    ):
        _fail(f"{label}.record_count must be null or a positive integer")
    return deepcopy(item)


def _safe_dependency_path(value: object, label: str) -> str:
    path = _nonempty(value, label)
    candidate = Path(path)
    prefix = "skills/audience-ad-testing-lab/scripts/"
    if (
        candidate.is_absolute()
        or "\\" in path
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != path
        or not path.startswith(prefix)
        or not path.endswith(".py")
    ):
        _fail(f"{label} must be a safe producer-source POSIX path")
    return path


def _finite_number(
    value: object,
    label: str,
    *,
    expected: int | float | None = None,
    positive: bool = False,
) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        _fail(f"{label} must be a finite number")
    if expected is not None and (
        value != expected or type(value) is not type(expected)
    ):
        _fail(f"{label} must equal the authenticated fixed value {expected!r}")
    if positive and value <= 0:
        _fail(f"{label} must be positive")
    return value


def _validate_dependency_closure(value: object) -> None:
    if not isinstance(value, list) or not value:
        _fail("upstream dependency_closure must be a non-empty array")
    previous: str | None = None
    paths: list[str] = []
    for index, raw_row in enumerate(value):
        row = _exact_mapping(
            raw_row,
            _DEPENDENCY_ROW_KEYS,
            f"upstream dependency_closure[{index}]",
        )
        path = _safe_dependency_path(
            row["path"], f"upstream dependency_closure[{index}].path"
        )
        count = row["byte_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _fail(
                f"upstream dependency_closure[{index}].byte_count is invalid"
            )
        _digest(
            row["raw_bytes_sha256"],
            f"upstream dependency_closure[{index}].raw_bytes_sha256",
        )
        if previous is not None and path <= previous:
            _fail("upstream dependency_closure paths must be unique and sorted")
        previous = path
        paths.append(path)
    entry = "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
    if entry not in paths:
        _fail("upstream dependency_closure omits the exact entry point")


def _validate_runtime_fingerprint(value: object) -> None:
    runtime = _exact_mapping(
        value,
        _RUNTIME_FINGERPRINT_KEYS,
        "upstream runtime_fingerprint",
    )
    for field in _RUNTIME_FINGERPRINT_KEYS:
        text = _nonempty(runtime[field], f"upstream runtime_fingerprint.{field}")
        if field.endswith("_sha256"):
            _digest(text, f"upstream runtime_fingerprint.{field}")


def _validate_maxdiff_policy(value: object) -> str:
    policy = _exact_mapping(
        value, _MAXDIFF_POLICY_KEYS, "upstream MaxDiff policy_bindings"
    )
    for field in (
        "maxdiff_configuration_sha256",
        "recovery_configuration_sha256",
    ):
        _digest(policy[field], f"upstream MaxDiff policy_bindings.{field}")
    _finite_number(
        policy["required_bootstrap_count"],
        "upstream MaxDiff policy_bindings.required_bootstrap_count",
        expected=2000,
    )
    for field, expected in (
        ("minimum_successful_fit_floor", 0.95),
        ("clear_finalist_threshold", 0.90),
        ("clear_non_finalist_threshold", 0.10),
        ("minimum_utility_tie_tolerance", 1e-12),
    ):
        _finite_number(
            policy[field],
            f"upstream MaxDiff policy_bindings.{field}",
            expected=expected,
        )
    tolerance = _finite_number(
        policy["effective_ordering_tolerance"],
        "upstream MaxDiff policy_bindings.effective_ordering_tolerance",
        positive=True,
    )
    if type(tolerance) is not float:
        _fail("upstream effective ordering tolerance must be a finite float")
    if tolerance < policy["minimum_utility_tie_tolerance"]:
        _fail("upstream effective ordering tolerance is below its minimum")
    fixed = {
        "ordering_tiebreak": "creative-id-serialization-only-v1",
        "ordering_equivalence": "rounded-utility-bucket-v1",
        "rounding_rule": "python-half-even-v1",
    }
    for field, expected in fixed.items():
        if policy[field] != expected:
            _fail(f"upstream MaxDiff policy_bindings.{field} is invalid")
    return str(policy["recovery_configuration_sha256"])


def _exact_typed_equal(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (
            set(value) == set(expected)  # type: ignore[arg-type]
            and all(
                _exact_typed_equal(value[key], expected[key])  # type: ignore[index]
                for key in expected
            )
        )
    if isinstance(expected, list):
        return (
            len(value) == len(expected)  # type: ignore[arg-type]
            and all(
                _exact_typed_equal(actual, target)
                for actual, target in zip(value, expected)  # type: ignore[arg-type]
            )
        )
    return value == expected


def _validate_output_serialization(value: object) -> None:
    output = _exact_mapping(
        value,
        _OUTPUT_SERIALIZATION_KEYS,
        "upstream output_serialization",
    )
    raw = _exact_mapping(
        output["producer_raw_serialization"],
        _SERIALIZATION_KEYS,
        "upstream producer_raw_serialization",
    )
    canonical = _exact_mapping(
        output["canonical_document_serialization"],
        _SERIALIZATION_KEYS,
        "upstream canonical_document_serialization",
    )
    expected_raw = {
        "encoding": "utf-8",
        "indent": 2,
        "sort_keys": True,
        "allow_nan": False,
        "ensure_ascii": True,
        "separators": None,
        "terminal_lf": True,
    }
    expected_canonical = {
        "encoding": "utf-8",
        "indent": None,
        "sort_keys": True,
        "allow_nan": False,
        "ensure_ascii": False,
        "separators": [",", ":"],
        "terminal_lf": True,
    }
    if (
        not _exact_typed_equal(dict(raw), expected_raw)
        or not _exact_typed_equal(dict(canonical), expected_canonical)
    ):
        _fail("upstream output serialization does not equal the closed contract")


def _validate_upstream_semantics(value: object) -> tuple[str, str]:
    semantics = _exact_mapping(
        value,
        _SEMANTICS_KEYS,
        "screening producer evidence.producer_semantics",
    )
    if (
        semantics["entry_point"]
        != "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
        or semantics["subcommand"] != "screening"
    ):
        _fail("upstream producer semantics entry point is invalid")
    bootstrap = _digest(
        semantics["bootstrap_sha256"], "upstream semantics.bootstrap_sha256"
    )
    if bootstrap != _sha256_bytes(REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")):
        _fail("upstream replay bootstrap does not equal the sealed implementation")
    _validate_dependency_closure(semantics["dependency_closure"])
    _validate_runtime_fingerprint(semantics["runtime_fingerprint"])
    recovery_configuration_sha256 = _validate_maxdiff_policy(
        semantics["policy_bindings"]
    )
    _validate_output_serialization(semantics["output_serialization"])
    semantics_digest = _digest(
        semantics["producer_semantics_sha256"],
        "upstream semantics.producer_semantics_sha256",
    )
    semantics_preimage = deepcopy(dict(semantics))
    semantics_preimage["producer_semantics_sha256"] = None
    if _sha256_bytes(
        _canonical_bytes(semantics_preimage, "upstream producer semantics")
    ) != semantics_digest:
        _fail("upstream producer semantics self-hash is invalid")
    return semantics_digest, recovery_configuration_sha256


def _validate_upstream_evidence(
    evidence: Mapping[str, object],
    *,
    run_id: str,
    screening_result_binding: Mapping[str, object],
    evidence_file_binding: Mapping[str, object],
) -> dict[str, object]:
    item = _exact_mapping(evidence, _EVIDENCE_KEYS, "screening producer evidence")
    if (
        item["schema_version"] != "panel-synthetic-producer-evidence-v1"
        or item["surface"] != "maxdiff_screening_ordering"
        or item["method"] != "partial_exposure_maxdiff"
        or item["stage"] != "screening"
        or item["run_id"] != run_id
    ):
        _fail("screening producer evidence surface identity is invalid")
    frozen_at = _parsed_timestamp(
        item["frozen_at"], "screening producer evidence.frozen_at"
    )
    sealed_at = _parsed_timestamp(
        item["sealed_at"], "screening producer evidence.sealed_at"
    )
    if frozen_at > sealed_at:
        _fail("screening producer evidence must not seal before its snapshot freeze")

    semantics_digest, recovery_configuration_sha256 = _validate_upstream_semantics(
        item["producer_semantics"]
    )

    input_bindings = _exact_mapping(
        item["input_bindings"],
        _UPSTREAM_SCREENING_BINDING_KEYS,
        "screening producer evidence.input_bindings",
    )
    jsonl_names = {
        "accepted_responses",
        "raw_provider_returns",
        "rejected_attempts",
        "dispatch_audit",
        "command_dispatch_audit_input",
        "screening_response_projection",
    }
    validated_input_bindings: dict[str, dict[str, object] | None] = {}
    for name in _UPSTREAM_SCREENING_BINDING_KEYS:
        value = input_bindings[name]
        if name == "command_dispatch_audit_input" and value is None:
            validated_input_bindings[name] = None
            continue
        binding = _validate_generic_binding(
            value, f"screening producer evidence.input_bindings.{name}"
        )
        validated_input_bindings[name] = binding
        if (name in jsonl_names) != (binding["record_count"] is not None):
            _fail(
                "screening producer evidence input record_count does not match "
                f"the JSON/JSONL role: {name}"
            )
    recovery_binding = validated_input_bindings["recovery_configuration"]
    if (
        recovery_binding is None
        or recovery_configuration_sha256
        != recovery_binding["canonical_document_sha256"]
    ):
        _fail(
            "upstream MaxDiff recovery policy does not bind the exact "
            "recovery configuration input"
        )

    result = _validate_generic_binding(
        item["result_binding"], "screening producer evidence.result_binding"
    )
    if (
        result["record_count"] is not None
        or Path(str(result["path"])).name != "screening-model-results.json"
    ):
        _fail("screening producer result binding record_count must be null")
    if (
        result["canonical_document_sha256"]
        != screening_result_binding["canonical_document_sha256"]
        or result["raw_bytes_sha256"]
        != screening_result_binding["raw_bytes_sha256"]
    ):
        _fail("upstream screening result bytes do not match its producer evidence")

    snapshot = _exact_mapping(
        item["snapshot_binding"],
        _SNAPSHOT_KEYS,
        "screening producer evidence.snapshot_binding",
    )
    snapshot_id = _nonempty(
        snapshot["snapshot_id"], "upstream snapshot.snapshot_id"
    )
    _digest(snapshot["snapshot_sha256"], "upstream snapshot.snapshot_sha256")
    _digest(snapshot["archive_sha256"], "upstream snapshot.archive_sha256")
    expected_snapshot_id = (
        f"maxdiff_screening_ordering--{run_id}--"
        f"{str(result['canonical_document_sha256'])[7:]}"
    )
    if snapshot_id != expected_snapshot_id:
        _fail("upstream snapshot ID does not bind its surface, run, and result")

    evidence_digest = _digest(
        item["producer_evidence_sha256"],
        "screening producer evidence.producer_evidence_sha256",
    )
    evidence_preimage = deepcopy(dict(item))
    evidence_preimage["producer_evidence_sha256"] = None
    if _sha256_bytes(
        _canonical_bytes(evidence_preimage, "screening producer evidence")
    ) != evidence_digest:
        _fail("screening producer evidence self-hash is invalid")

    return {
        **deepcopy(dict(evidence_file_binding)),
        "producer_evidence_sha256": evidence_digest,
        "producer_semantics_sha256": semantics_digest,
        "result_sha256": result["canonical_document_sha256"],
        "result_bytes_sha256": result["raw_bytes_sha256"],
    }


@_normalize_resource_failures(
    "producer replay input assembly exceeded safe resources"
)
def assemble_replay_inputs(
    *,
    surface: str,
    paths: ProducerReplayInputs,
) -> dict[str, object]:
    """Validate one exact surface matrix and derive replay-only stage inputs."""
    spec, values = _validate_matrix(surface, paths)
    _validate_file_names(spec, values)
    reader = _PinnedInputReader(values, _RESOURCE_LIMITS)
    try:
        result = _assemble_replay_inputs(
            surface=surface,
            spec=spec,
            values=values,
            reader=reader,
        )
        reader.verify_all()
        return result
    finally:
        reader.close()


@_normalize_resource_failures(
    "producer replay input validation exceeded safe resources"
)
def _assemble_replay_inputs(
    *,
    surface: str,
    spec: Mapping[str, str],
    values: Mapping[str, Path | None],
    reader: _PinnedInputReader,
) -> dict[str, object]:
    manifest, manifest_binding, _manifest_raw = _read_json(
        "study_manifest",
        reader=reader,
        logical_path="study-manifest.json",
    )
    try:
        manifest_errors = validate_manifest(manifest)
    except (
        TypeError,
        ValueError,
        RecursionError,
        MemoryError,
        OverflowError,
    ) as exc:
        _fail("study manifest validation exceeded safe resources", exc)
    if manifest_errors:
        _fail("study manifest is invalid: " + "; ".join(manifest_errors))
    run_id = manifest.get("study_id")
    if not isinstance(run_id, str) or not run_id:
        _fail("study manifest must contain one non-empty study_id")
    if manifest.get("method") != spec["method"]:
        _fail("study manifest method does not match the selected surface")

    lineage_records: dict[str, list[dict[str, object]]] = {}
    lineage_content: dict[str, bytes] = {}
    lineage_bindings: dict[str, dict[str, object]] = {}
    for role in LINEAGE_ORDER:
        records, binding, raw = _read_jsonl(
            (
                "cumulative_dispatch_audit"
                if role == "dispatch_audit"
                else role
            ),
            reader=reader,
            logical_path=CANONICAL_LINEAGE_FILES[role],
        )
        filename = CANONICAL_LINEAGE_FILES[role]
        lineage_records[filename] = records
        lineage_content[filename] = raw
        lineage_bindings[role] = binding
    try:
        validate_bound_lineage(manifest, lineage_records, lineage_content)
    except (
        TypeError,
        ValueError,
        RecursionError,
        MemoryError,
        OverflowError,
    ) as exc:
        _fail("final study manifest does not authenticate its lineage: " + str(exc), exc)

    accepted_records = lineage_records[
        CANONICAL_LINEAGE_FILES["accepted_responses"]
    ]
    projected: list[dict[str, object]] = []
    for index, record in enumerate(accepted_records):
        if record.get("record_type") != spec["record_type"]:
            continue
        try:
            errors = validate_response(record)
        except (
            TypeError,
            ValueError,
            RecursionError,
            MemoryError,
            OverflowError,
        ) as exc:
            _fail(
                f"accepted response projection record {index} validation failed",
                exc,
            )
        if errors:
            _fail(
                f"accepted response projection record {index} is invalid: "
                + "; ".join(errors)
            )
        if record.get("study_id") != run_id:
            _fail(f"accepted response projection record {index} has wrong study_id")
        if record.get("method") != spec["method"]:
            _fail(f"accepted response projection record {index} has wrong method")
        projected.append(deepcopy(record))
    if not projected:
        _fail(f"{spec['record_type']} projection must not be empty")
    projection_bytes = b"".join(
        _canonical_bytes(record, f"{spec['record_type']} projection")
        for record in projected
    )
    projection_binding = {
        "path": f"{spec['record_type'].replace('_response', '')}-response-projection.jsonl",
        "raw_bytes_sha256": _sha256_bytes(projection_bytes),
        "canonical_document_sha256": _sha256_bytes(projection_bytes),
        "record_count": len(projected),
    }

    result_document, result_binding, _result_raw = _read_json(
        "result", reader=reader, logical_path=spec["result_name"]
    )
    if result_document.get("study_id") != run_id:
        _fail("chosen result study_id does not match the final manifest")

    input_bindings: dict[str, object] = {
        "study_manifest": manifest_binding,
        "accepted_responses": lineage_bindings["accepted_responses"],
        "raw_provider_returns": lineage_bindings["raw_provider_returns"],
        "rejected_attempts": lineage_bindings["rejected_attempts"],
        "dispatch_audit": lineage_bindings["dispatch_audit"],
        "command_dispatch_audit_input": None,
    }
    source_paths: dict[str, Path] = {
        "study_manifest": reader.path("study_manifest"),
        "accepted_responses": reader.path("accepted_responses"),
        "raw_provider_returns": reader.path("raw_provider_returns"),
        "rejected_attempts": reader.path("rejected_attempts"),
        "dispatch_audit": reader.path("cumulative_dispatch_audit"),
        "result": reader.path("result"),
    }
    command_audit = values["command_dispatch_audit_input"]
    if command_audit is not None:
        _records, command_binding, _raw = _read_jsonl(
            "command_dispatch_audit_input", reader=reader
        )
        input_bindings["command_dispatch_audit_input"] = command_binding
        source_paths["command_dispatch_audit_input"] = reader.path(
            "command_dispatch_audit_input"
        )

    if spec["stage"] == "screening":
        jobs, jobs_binding, _jobs_raw = _read_json(
            "screening_jobs",
            reader=reader,
            logical_path="screening-jobs.json",
        )
        if (
            jobs.get("study_id") != run_id
            or jobs.get("method") != spec["method"]
            or jobs.get("record_type") != "screening_response"
            or not isinstance(jobs.get("synthetic_replicate_jobs"), list)
            or not jobs["synthetic_replicate_jobs"]
        ):
            _fail("screening jobs identity does not match the selected surface")
        _recovery, recovery_binding, _recovery_raw = _read_json(
            "recovery_configuration", reader=reader
        )
        input_bindings.update({
            "screening_jobs": jobs_binding,
            spec["projection_role"]: projection_binding,
            "recovery_configuration": recovery_binding,
        })
        source_paths["screening_jobs"] = reader.path("screening_jobs")
        source_paths["recovery_configuration"] = reader.path(
            "recovery_configuration"
        )
    else:
        screening_result_document, screening_result_binding, _screening_raw = (
            _read_json(
                "screening_result",
                reader=reader,
                logical_path="screening-model-results.json",
            )
        )
        if screening_result_document.get("study_id") != run_id:
            _fail("upstream screening result study_id does not match final manifest")
        evidence, evidence_binding, _evidence_raw = _read_json(
            "screening_producer_evidence", reader=reader
        )
        upstream_binding = _validate_upstream_evidence(
            evidence,
            run_id=run_id,
            screening_result_binding=screening_result_binding,
            evidence_file_binding=evidence_binding,
        )
        input_bindings.update({
            spec["projection_role"]: projection_binding,
            "screening_result": screening_result_binding,
            "screening_producer_evidence": upstream_binding,
        })
        source_paths["screening_result"] = reader.path("screening_result")
        source_paths["screening_producer_evidence"] = reader.path(
            "screening_producer_evidence"
        )

    try:
        lineage_digest = lineage_bundle_sha256(lineage_bindings)
    except (
        TypeError,
        ValueError,
        RecursionError,
        MemoryError,
        OverflowError,
    ) as exc:
        _fail("lineage binding digest could not be calculated safely", exc)
    return _build_replay_output(
        surface=surface,
        method=spec["method"],
        stage=spec["stage"],
        run_id=run_id,
        manifest=manifest,
        input_bindings=input_bindings,
        result_binding=result_binding,
        lineage_digest=lineage_digest,
        projection_bytes=projection_bytes,
        source_paths=source_paths,
    )


def _build_replay_output(
    *,
    surface: str,
    method: str,
    stage: str,
    run_id: str,
    manifest: Mapping[str, object],
    input_bindings: Mapping[str, object],
    result_binding: Mapping[str, object],
    lineage_digest: str,
    projection_bytes: bytes,
    source_paths: Mapping[str, Path],
) -> dict[str, object]:
    return {
        "surface": surface,
        "method": method,
        "stage": stage,
        "run_id": run_id,
        "manifest": deepcopy(manifest),
        "input_bindings": deepcopy(input_bindings),
        "result_binding": deepcopy(result_binding),
        "lineage_bundle_sha256": lineage_digest,
        "response_projection_bytes": bytes(projection_bytes),
        "source_paths": dict(source_paths),
    }
