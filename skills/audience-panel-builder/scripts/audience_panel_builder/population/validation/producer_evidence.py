"""Authenticated, durable producer-evidence receipts for Tier 4 ordering."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterator, Mapping, Sequence

from audience_panel_builder.common import canonical_json_bytes, sha256_json

from .evidence_bindings import LINEAGE_ORDER, lineage_bundle_sha256
from .evidence_errors import (
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
    ProducerRuntimeUnavailable,
)
from .evidence_snapshot import (
    EvidenceSnapshot,
    _canonical_allowed_roots,
    _canonical_path as _canonical_snapshot_path,
    _close_joint,
    _open_absolute_directory,
    _open_joint,
    _recheck_chain,
    _reauthenticate_source,
    _resolve_runtime_for_replay,
    _validate_sources,
    create_evidence_snapshot,
    open_evidence_snapshot,
    recover_evidence_snapshot_publication,
)
from .producer_replay import replay_producer
from .producer_semantics import (
    CANONICAL_DOCUMENT_SERIALIZATION,
    PRODUCER_RAW_SERIALIZATION,
    REPLAY_BOOTSTRAP_SOURCE,
    ProducerSemanticsBundle,
    _build_runtime_fingerprint,
    _validate_staged_closure,
    build_producer_semantics,
)
from .replay_inputs import ProducerReplayInputs, assemble_replay_inputs


PRODUCER_EVIDENCE_VERSION = "panel-synthetic-producer-evidence-v1"
_REVOCATION_VERSION = "producer-evidence-publication-state-v1"
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RECEIPT_FIELDS = {
    "schema_version", "surface", "method", "stage", "run_id", "frozen_at",
    "sealed_at", "producer_semantics", "input_bindings", "result_binding",
    "snapshot_binding", "producer_evidence_sha256",
}
_SEMANTICS_FIELDS = {
    "entry_point", "subcommand", "bootstrap_sha256", "dependency_closure",
    "runtime_fingerprint", "policy_bindings", "output_serialization",
    "producer_semantics_sha256",
}
_GENERIC_BINDING_FIELDS = {
    "path", "raw_bytes_sha256", "canonical_document_sha256", "record_count",
}
_SNAPSHOT_BINDING_FIELDS = {
    "snapshot_id", "snapshot_sha256", "archive_sha256",
}
_UPSTREAM_BINDING_FIELDS = _GENERIC_BINDING_FIELDS | {
    "producer_evidence_sha256", "producer_semantics_sha256",
    "result_sha256", "result_bytes_sha256",
}
_RUNTIME_FINGERPRINT_FIELDS = {
    "python_implementation", "python_version", "numpy_version", "scipy_version",
    "platform_system", "platform_release", "machine", "numpy_build_sha256",
    "blas_lapack_sha256",
}
_OUTPUT_SERIALIZATION = {
    "producer_raw_serialization": dict(PRODUCER_RAW_SERIALIZATION),
    "canonical_document_serialization": dict(
        CANONICAL_DOCUMENT_SERIALIZATION
    ),
}
_SURFACES = {
    "complete_exposure_ordering": {
        "method": "complete_exposure",
        "stage": "screening",
        "subcommand": "screening",
        "result": "screening-model-results.json",
        "projection": "screening_response_projection",
    },
    "maxdiff_screening_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "screening",
        "subcommand": "screening",
        "result": "screening-model-results.json",
        "projection": "screening_response_projection",
    },
    "pairwise_boundary_ordering": {
        "method": "partial_exposure_maxdiff",
        "stage": "boundary",
        "subcommand": "boundary",
        "result": "boundary-results.json",
        "projection": "boundary_response_projection",
    },
}
_ENTRY_POINT = "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
_BOOTSTRAP_SHA256 = (
    "sha256:"
    + hashlib.sha256(REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")).hexdigest()
)
_BASE_INPUT_ROLES = {
    "study_manifest", "accepted_responses", "raw_provider_returns",
    "rejected_attempts", "dispatch_audit", "command_dispatch_audit_input",
}
_SCREENING_INPUT_ROLES = _BASE_INPUT_ROLES | {
    "screening_jobs", "screening_response_projection",
    "recovery_configuration",
}
_BOUNDARY_INPUT_ROLES = _BASE_INPUT_ROLES | {
    "boundary_response_projection", "screening_result",
    "screening_producer_evidence",
}
_ROLE_BINDING_SPEC = {
    "study_manifest": ("study-manifest.json", None),
    "accepted_responses": ("panelist-responses.jsonl", "jsonl"),
    "raw_provider_returns": ("raw-provider-returns.jsonl", "jsonl"),
    "rejected_attempts": ("rejected-attempts.jsonl", "jsonl"),
    "dispatch_audit": ("dispatch-audit.jsonl", "jsonl"),
    "command_dispatch_audit_input": (
        None, "jsonl"
    ),
    "screening_jobs": ("screening-jobs.json", None),
    "screening_response_projection": (
        "screening-response-projection.jsonl", "jsonl"
    ),
    "boundary_response_projection": (
        "boundary-response-projection.jsonl", "jsonl"
    ),
    "recovery_configuration": (None, None),
    "screening_result": ("screening-model-results.json", None),
}
_REPLAY_SCREENING_ROLES = {
    "study_manifest", "screening_jobs", "screening_response_projection",
    "recovery_configuration", "result",
}
_REPLAY_BOUNDARY_ROLES = {
    "study_manifest", "screening_result", "boundary_response_projection",
    "result",
}
_RECEIPT_BYTES = 16 * 1024 * 1024
_REVOCATION_BYTES = 4 * 1024
_CREATE_FLAGS = (
    os.O_RDWR | os.O_CREAT | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
)
_DIR_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True)
class _ResourceLimits:
    maximum_bytes: int
    maximum_depth: int = 8
    maximum_array_items: int = 16_384
    maximum_object_keys: int = 128
    maximum_string_bytes: int = 1024 * 1024
    maximum_scalars: int = 100_000


_RECEIPT_LIMITS = _ResourceLimits(_RECEIPT_BYTES)
_REVOCATION_LIMITS = _ResourceLimits(
    _REVOCATION_BYTES,
    maximum_depth=2,
    maximum_array_items=0,
    maximum_object_keys=4,
    maximum_string_bytes=1024,
    maximum_scalars=4,
)


@dataclass
class _PinnedRoot:
    path: Path
    fd: int
    identity: tuple[int, int]
    mode: int
    chain: tuple[tuple[Path, tuple[int, int]], ...]


@dataclass
class _PinnedFile:
    name: str
    fd: int
    identity: tuple[int, int]
    stat_key: tuple[int, ...]
    byte_count: int
    digest: str


def _auth(message: str, exc: BaseException | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _closed_public_failures(function):
    @wraps(function)
    def closed(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ProducerEvidenceError, KeyboardInterrupt, SystemExit):
            raise
        except (
            OSError, MemoryError, OverflowError, RecursionError,
            UnicodeError,
        ) as exc:
            raise ProducerEvidenceError(
                "producer-evidence operation failed within closed resources"
            ) from exc

    return closed


def _digest(value: bytes | bytearray) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid,
        value.st_gid, value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _canonical_path(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        _auth("trusted roots must be absolute Path values")
    return _canonical_snapshot_path(path)


def _open_root(path: Path, *, label: str) -> _PinnedRoot:
    missing = [
        name for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_EXCL")
        if not hasattr(os, name)
    ]
    if missing:
        raise ProducerEvidenceError(
            "producer-evidence publication requires " + ", ".join(missing)
        )
    canonical = _canonical_path(path)
    fd, chain = _open_absolute_directory(canonical, label=label)
    try:
        value = os.fstat(fd)
        mode = stat.S_IMODE(value.st_mode)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.geteuid()
            or mode & 0o022
        ):
            _auth(
                f"{label} must be an existing euid-owned real directory "
                "that is not group/world writable"
            )
        return _PinnedRoot(
            canonical, fd, _identity(value), mode, chain
        )
    except BaseException:
        os.close(fd)
        raise


def _recheck_root(root: _PinnedRoot, *, label: str) -> None:
    value = os.fstat(root.fd)
    if (
        _identity(value) != root.identity
        or not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or stat.S_IMODE(value.st_mode) != root.mode
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        _auth(f"{label} metadata or identity changed")
    _recheck_chain(root.chain, label=label)


def _receipt_id(surface: str, run_id: str, result_sha256: str) -> str:
    for label, value in (("surface", surface), ("run_id", run_id)):
        if (
            not isinstance(value, str)
            or "--" in value
            or not _IDENTIFIER_RE.fullmatch(value)
        ):
            _auth(f"{label} must be a safe non-empty producer identifier")
    if not isinstance(result_sha256, str) or not _SHA256_RE.fullmatch(
        result_sha256
    ):
        _auth("result_sha256 must be a lowercase prefixed SHA-256 digest")
    value = f"{surface}--{run_id}--{result_sha256[7:]}"
    if len((value + ".producer-evidence.json").encode("ascii")) > 240:
        _auth("derived producer-evidence basename exceeds component limit")
    return value


def _receipt_name(surface: str, run_id: str, result_sha256: str) -> str:
    return _receipt_id(surface, run_id, result_sha256) + ".producer-evidence.json"


def _revocation_name(surface: str, run_id: str, result_sha256: str) -> str:
    return _receipt_id(surface, run_id, result_sha256) + ".revoked.json"


def _write_all(fd: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("producer-evidence write made no progress")
        remaining = remaining[written:]


def _read_bounded(fd: int, limits: _ResourceLimits, *, label: str) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        _auth(f"{label} must be a regular file")
    if before.st_size > limits.maximum_bytes:
        _auth(f"{label} exceeds byte limit")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    count = 0
    while count <= limits.maximum_bytes:
        chunk = os.read(fd, min(1024 * 1024, limits.maximum_bytes + 1 - count))
        if not chunk:
            break
        chunks.append(chunk)
        count += len(chunk)
    if count > limits.maximum_bytes:
        _auth(f"{label} exceeds byte limit")
    after = os.fstat(fd)
    if _stat_key(before) != _stat_key(after) or count != before.st_size:
        _auth(f"{label} changed while read")
    return b"".join(chunks)


def _open_existing(
    root: _PinnedRoot, name: str, limits: _ResourceLimits, *, label: str
) -> tuple[_PinnedFile, bytes]:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=root.fd)
    except OSError as exc:
        _auth(f"{label} is missing, unsafe, or not regular", exc)
    try:
        value = os.fstat(fd)
        try:
            selected = os.stat(name, dir_fd=root.fd, follow_symlinks=False)
        except OSError as exc:
            _auth(f"{label} entry changed while opened", exc)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.geteuid()
            or value.st_nlink != 1
            or stat.S_IMODE(value.st_mode) != 0o400
            or _identity(value) != _identity(selected)
        ):
            _auth(f"{label} metadata, mode, link count, or identity is invalid")
        raw = _read_bounded(fd, limits, label=label)
        value = os.fstat(fd)
        return (
            _PinnedFile(
                name, fd, _identity(value), _stat_key(value), len(raw),
                _digest(raw),
            ),
            raw,
        )
    except BaseException:
        os.close(fd)
        raise


def _recheck_file(
    root: _PinnedRoot, item: _PinnedFile, limits: _ResourceLimits, *,
    root_label: str, label: str,
) -> bytes:
    _recheck_root(root, label=root_label)
    try:
        selected = os.stat(item.name, dir_fd=root.fd, follow_symlinks=False)
    except OSError as exc:
        _auth(f"{label} directory entry changed", exc)
    value = os.fstat(item.fd)
    if (
        _identity(selected) != item.identity
        or _identity(value) != item.identity
        or _stat_key(value) != item.stat_key
        or not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or stat.S_IMODE(value.st_mode) != 0o400
    ):
        _auth(f"{label} identity or metadata changed")
    raw = _read_bounded(item.fd, limits, label=label)
    if len(raw) != item.byte_count or _digest(raw) != item.digest:
        _auth(f"{label} length or digest changed")
    _recheck_root(root, label=root_label)
    return raw


def _parse_json(raw: bytes, limits: _ResourceLimits, *, label: str) -> object:
    def duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=duplicate_free,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError, json.JSONDecodeError, ValueError,
        RecursionError, MemoryError, OverflowError,
    ) as exc:
        _auth(f"{label} is not duplicate-free finite UTF-8 JSON", exc)
    scalars = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    try:
        while stack:
            item, depth = stack.pop()
            if depth > limits.maximum_depth:
                _auth(f"{label} exceeds JSON depth limit")
            if isinstance(item, dict):
                if len(item) > limits.maximum_object_keys:
                    _auth(f"{label} exceeds object-key limit")
                for key, child in item.items():
                    if len(key.encode("utf-8")) > limits.maximum_string_bytes:
                        _auth(f"{label} contains an over-limit object key")
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                if len(item) > limits.maximum_array_items:
                    _auth(f"{label} exceeds array-item limit")
                stack.extend((child, depth + 1) for child in item)
            else:
                scalars += 1
                if scalars > limits.maximum_scalars:
                    _auth(f"{label} exceeds scalar-value limit")
                if (
                    isinstance(item, str)
                    and len(item.encode("utf-8")) > limits.maximum_string_bytes
                ):
                    _auth(f"{label} contains an over-limit string")
                if isinstance(item, float) and not math.isfinite(item):
                    _auth(f"{label} contains a non-finite number")
    except (UnicodeError, RecursionError, MemoryError, OverflowError) as exc:
        _auth(f"{label} exceeded safe parsing resources", exc)
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _auth(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        _auth(f"{label} is invalid", exc)
    if parsed.tzinfo != timezone.utc:
        _auth(f"{label} must be UTC")
    canonical = (
        parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    if value != canonical:
        _auth(f"{label} must use canonical microsecond UTC form")
    return parsed


def _nonempty(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _auth(f"{label} must be a non-empty string")
    return value


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _auth(f"{label} must be a lowercase prefixed SHA-256")
    return value


def _generic_binding(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _GENERIC_BINDING_FIELDS:
        _auth(f"{label} must contain exactly the closed binding fields")
    result = deepcopy(dict(value))
    path = result["path"]
    if (
        not isinstance(path, str) or not path or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        _auth(f"{label}.path is not a safe relative path")
    _sha(result["raw_bytes_sha256"], label=f"{label}.raw_bytes_sha256")
    _sha(
        result["canonical_document_sha256"],
        label=f"{label}.canonical_document_sha256",
    )
    count = result["record_count"]
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count < 1
    ):
        _auth(f"{label}.record_count must be null or a positive integer")
    return result


def _role_binding(
    value: object, *, role: str, surface: str, run_id: str
) -> dict[str, object]:
    result = _generic_binding(value, label=role)
    if role == "screening_producer_evidence":
        if result["record_count"] is not None:
            _auth("screening_producer_evidence.record_count must be null")
        return result
    try:
        expected_path, kind = _ROLE_BINDING_SPEC[role]
    except KeyError as exc:
        _auth(f"binding role has no closed specification: {role}", exc)
    if expected_path is not None and result["path"] != expected_path:
        _auth(f"{role}.path must equal the canonical logical path")
    if expected_path is None and (
        "/" in str(result["path"])
        or "\\" in str(result["path"])
        or Path(str(result["path"])).name != result["path"]
    ):
        _auth(f"{role}.path must be one safe inert basename")
    if kind == "jsonl":
        if (
            isinstance(result["record_count"], bool)
            or not isinstance(result["record_count"], int)
            or result["record_count"] < 1
        ):
            _auth(f"{role}.record_count must be a positive JSONL count")
    elif result["record_count"] is not None:
        _auth(f"{role}.record_count must be null for one JSON document")
    if surface == "pairwise_boundary_ordering" and role == (
        "command_dispatch_audit_input"
    ):
        _auth("pairwise command_dispatch_audit_input must be null")
    return result


def _validate_semantics(
    value: object, *, surface: str
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _SEMANTICS_FIELDS:
        _auth("producer_semantics is not the exact closed object")
    result = deepcopy(dict(value))
    spec = _SURFACES[surface]
    if (
        result["entry_point"] != _ENTRY_POINT
        or result["subcommand"] != spec["subcommand"]
    ):
        _auth("producer semantics entry point or subcommand is wrong")
    if result["bootstrap_sha256"] != _BOOTSTRAP_SHA256:
        _auth("bootstrap_sha256 does not match the sealed replay bootstrap")
    closure = result["dependency_closure"]
    if not isinstance(closure, list) or not closure or len(closure) > 4096:
        _auth("dependency_closure must be a non-empty bounded array")
    previous = ""
    for index, row in enumerate(closure):
        if (
            not isinstance(row, Mapping)
            or set(row) != {"path", "byte_count", "raw_bytes_sha256"}
        ):
            _auth(f"dependency_closure[{index}] is not closed")
        path = row["path"]
        if (
            not isinstance(path, str) or not path.startswith(
                "skills/audience-ad-testing-lab/scripts/"
            )
            or path <= previous
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            _auth("dependency_closure paths are unsafe, duplicate, or unsorted")
        previous = path
        count = row["byte_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _auth("dependency_closure byte_count is invalid")
        _sha(row["raw_bytes_sha256"], label="dependency closure digest")
    if closure[0].get("path") != _ENTRY_POINT:
        _auth("dependency_closure omits the direct entry point")
    fingerprint = result["runtime_fingerprint"]
    if (
        not isinstance(fingerprint, Mapping)
        or set(fingerprint) != _RUNTIME_FINGERPRINT_FIELDS
        or any(not isinstance(item, str) or not item for item in fingerprint.values())
    ):
        _auth("runtime_fingerprint is not the closed non-empty schema")
    for name in ("numpy_build_sha256", "blas_lapack_sha256"):
        _sha(fingerprint[name], label=f"runtime_fingerprint.{name}")
    policy = result["policy_bindings"]
    _validate_policy(policy, surface=surface)
    if result["output_serialization"] != _OUTPUT_SERIALIZATION:
        _auth("producer output serialization does not match the closed contract")
    expected = sha256_json({**result, "producer_semantics_sha256": None})
    if result["producer_semantics_sha256"] != expected:
        _auth("producer semantics self-hash is invalid")
    return result


def _finite_number(value: object, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _auth(f"{label} must be a finite number")
    if not math.isfinite(float(value)):
        _auth(f"{label} must be finite")


def _validate_policy(value: object, *, surface: str) -> None:
    if not isinstance(value, Mapping):
        _auth("policy_bindings must be an object")
    if surface == "complete_exposure_ordering":
        keys = {
            "calibration_policy_version", "production_resamples",
            "cutoff_tie_tolerance", "ordering_tiebreak",
            "ordering_equivalence", "recovery_configuration_sha256",
        }
        fixed = {
            "calibration_policy_version": "complete-exposure-calibration-v2",
            "production_resamples": 2000,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "exact-utility-equality-v1",
        }
    elif surface == "maxdiff_screening_ordering":
        keys = {
            "maxdiff_configuration_sha256", "required_bootstrap_count",
            "minimum_successful_fit_floor", "clear_finalist_threshold",
            "clear_non_finalist_threshold", "minimum_utility_tie_tolerance",
            "ordering_tiebreak", "ordering_equivalence",
            "effective_ordering_tolerance", "rounding_rule",
            "recovery_configuration_sha256",
        }
        fixed = {
            "required_bootstrap_count": 2000,
            "minimum_successful_fit_floor": 0.95,
            "clear_finalist_threshold": 0.90,
            "clear_non_finalist_threshold": 0.10,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "rounding_rule": "python-half-even-v1",
        }
    else:
        keys = {
            "pairwise_configuration_sha256", "clear_finalist_threshold",
            "clear_non_finalist_threshold", "minimum_utility_tie_tolerance",
            "ordering_tiebreak", "ordering_equivalence",
            "effective_ordering_tolerance", "rounding_rule",
            "upstream_screening_producer_semantics_sha256",
        }
        fixed = {
            "clear_finalist_threshold": 0.90,
            "clear_non_finalist_threshold": 0.10,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "rounding_rule": "python-half-even-v1",
        }
    if set(value) != keys:
        _auth("policy_bindings fields are not the closed surface set")
    for name, expected in fixed.items():
        if value[name] != expected or type(value[name]) is not type(expected):
            _auth(f"policy_bindings.{name} is not the fixed value")
    for name in keys:
        if name.endswith("_sha256"):
            _sha(value[name], label=f"policy_bindings.{name}")
    for name in (
        "cutoff_tie_tolerance", "minimum_utility_tie_tolerance",
        "effective_ordering_tolerance",
    ):
        if name in value:
            _finite_number(value[name], label=f"policy_bindings.{name}")
    tolerance_name = (
        "cutoff_tie_tolerance"
        if surface == "complete_exposure_ordering"
        else "minimum_utility_tie_tolerance"
    )
    if value[tolerance_name] != 1e-12:
        _auth(f"policy_bindings.{tolerance_name} is not the fixed producer value")
    if (
        "effective_ordering_tolerance" in value
        and float(value["effective_ordering_tolerance"]) < 1e-12
    ):
        _auth("effective ordering tolerance is below the producer minimum")


def _validate_receipt_document(
    value: object, *, surface: str, run_id: str, result_sha256: str
) -> dict[str, object]:
    if surface not in _SURFACES:
        _auth("producer evidence surface is unsupported")
    if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
        _auth("producer evidence record is not the exact closed schema")
    result = deepcopy(dict(value))
    spec = _SURFACES[surface]
    if (
        result["schema_version"] != PRODUCER_EVIDENCE_VERSION
        or result["surface"] != surface
        or result["method"] != spec["method"]
        or result["stage"] != spec["stage"]
        or result["run_id"] != run_id
    ):
        _auth("producer evidence identity does not match the selected surface")
    frozen = _timestamp(result["frozen_at"], label="frozen_at")
    sealed = _timestamp(result["sealed_at"], label="sealed_at")
    if frozen > sealed:
        _auth("producer evidence was sealed before the snapshot freeze")
    semantics = _validate_semantics(result["producer_semantics"], surface=surface)
    expected_roles = (
        _BOUNDARY_INPUT_ROLES
        if surface == "pairwise_boundary_ordering"
        else _SCREENING_INPUT_ROLES
    )
    bindings = result["input_bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != expected_roles:
        _auth("input_bindings fields are not the closed surface set")
    checked: dict[str, object] = {}
    for role in sorted(expected_roles):
        raw = bindings[role]
        if role == "command_dispatch_audit_input" and raw is None:
            checked[role] = None
            continue
        if role == "screening_producer_evidence":
            if not isinstance(raw, Mapping) or set(raw) != _UPSTREAM_BINDING_FIELDS:
                _auth("screening_producer_evidence binding is not closed")
            ordinary = _role_binding(
                {
                    name: raw[name]
                    for name in _GENERIC_BINDING_FIELDS
                },
                role=role,
                surface=surface,
                run_id=run_id,
            )
            special = deepcopy(dict(raw))
            special.update(ordinary)
            for name in _UPSTREAM_BINDING_FIELDS - _GENERIC_BINDING_FIELDS:
                _sha(special[name], label=f"{role}.{name}")
            if special["path"] != _receipt_name(
                "maxdiff_screening_ordering",
                run_id,
                str(special["result_sha256"]),
            ):
                _auth(
                    "screening_producer_evidence.path is not the exact "
                    "deterministic upstream receipt name"
                )
            checked[role] = special
        else:
            checked[role] = _role_binding(
                raw, role=role, surface=surface, run_id=run_id
            )
    # Recalculate the Task 2 ordered-list lineage preimage rather than
    # trusting mapping insertion order or accepting an envelope digest.
    lineage_bundle_sha256({
        role: checked[role] for role in LINEAGE_ORDER
    })
    policy = semantics["policy_bindings"]
    if surface == "pairwise_boundary_ordering":
        if (
            policy["upstream_screening_producer_semantics_sha256"]
            != checked["screening_producer_evidence"][
                "producer_semantics_sha256"
            ]
        ):
            _auth("pairwise policy does not bind its recursive screening semantics")
        if (
            checked["screening_producer_evidence"]["result_sha256"]
            != checked["screening_result"]["canonical_document_sha256"]
            or checked["screening_producer_evidence"]["result_bytes_sha256"]
            != checked["screening_result"]["raw_bytes_sha256"]
        ):
            _auth(
                "recursive screening evidence does not bind the exact "
                "screening result input"
            )
    elif (
        policy["recovery_configuration_sha256"]
        != checked["recovery_configuration"]["canonical_document_sha256"]
    ):
        _auth("producer policy does not bind its exact recovery configuration")
    result_binding = _generic_binding(
        result["result_binding"], label="result_binding"
    )
    if (
        result_binding["path"] != spec["result"]
        or result_binding["canonical_document_sha256"] != result_sha256
        or result_binding["record_count"] is not None
    ):
        _auth("result_binding does not match the selected frozen result")
    snapshot = result["snapshot_binding"]
    if (
        not isinstance(snapshot, Mapping)
        or set(snapshot) != _SNAPSHOT_BINDING_FIELDS
        or snapshot["snapshot_id"] != _receipt_id(surface, run_id, result_sha256)
    ):
        _auth("snapshot_binding is not the exact derived binding")
    _sha(snapshot["snapshot_sha256"], label="snapshot_binding.snapshot_sha256")
    _sha(snapshot["archive_sha256"], label="snapshot_binding.archive_sha256")
    if result["frozen_at"] != snapshot.get("frozen_at", result["frozen_at"]):
        _auth("producer evidence frozen_at is inconsistent")
    expected = sha256_json({**result, "producer_evidence_sha256": None})
    if result["producer_evidence_sha256"] != expected:
        _auth("producer evidence self-hash is invalid")
    result["producer_semantics"] = semantics
    result["input_bindings"] = checked
    result["result_binding"] = result_binding
    return result


def _parse_receipt(
    raw: bytes, *, surface: str, run_id: str, result_sha256: str
) -> dict[str, object]:
    value = _parse_json(raw, _RECEIPT_LIMITS, label="producer evidence receipt")
    if canonical_json_bytes(value) != raw:
        _auth("producer evidence receipt is not canonical JSON")
    return _validate_receipt_document(
        value, surface=surface, run_id=run_id, result_sha256=result_sha256
    )


def _parse_revocation(
    raw: bytes, *, receipt_id: str, producer_evidence_sha256: str | None
) -> dict[str, object]:
    value = _parse_json(raw, _REVOCATION_LIMITS, label="producer revocation")
    if canonical_json_bytes(value) != raw:
        _auth("producer revocation is not canonical JSON")
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "receipt_id", "producer_evidence_sha256", "status"
        }
        or value["schema_version"] != _REVOCATION_VERSION
        or value["receipt_id"] != receipt_id
        or value["status"] != "revoked"
    ):
        _auth("producer revocation is not the exact closed marker")
    digest = _sha(
        value["producer_evidence_sha256"],
        label="revocation.producer_evidence_sha256",
    )
    if producer_evidence_sha256 is not None and digest != producer_evidence_sha256:
        _auth("producer revocation does not select the exact receipt")
    return deepcopy(value)


def _entry_exists(root: _PinnedRoot, name: str) -> bool:
    try:
        os.stat(name, dir_fd=root.fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        _auth("publication-state entry could not be inspected safely", exc)


def _preflight_existing_receipt(
    *, surface: str, run_id: str, result_sha256: str, evidence_root: Path
) -> None:
    """Refuse to infer durability or collide on an unresolved exact receipt."""
    root = _open_root(evidence_root, label="evidence_root")
    item: _PinnedFile | None = None
    try:
        if _entry_exists(
            root, _revocation_name(surface, run_id, result_sha256)
        ):
            _auth("the deterministic producer-evidence identity is revoked")
        name = _receipt_name(surface, run_id, result_sha256)
        if not _entry_exists(root, name):
            return
        item, raw = _open_existing(
            root, name, _RECEIPT_LIMITS, label="existing producer evidence"
        )
        _parse_receipt(
            raw, surface=surface, run_id=run_id, result_sha256=result_sha256
        )
        _recheck_file(
            root, item, _RECEIPT_LIMITS,
            root_label="evidence_root", label="existing producer evidence",
        )
        raise ProducerPublicationIndeterminate(
            "a complete deterministic producer-evidence receipt already "
            "exists; explicit receipt recovery is required"
        )
    finally:
        if item is not None:
            os.close(item.fd)
        os.close(root.fd)


def _recover_file(
    *, root: _PinnedRoot, name: str, limits: _ResourceLimits,
    root_label: str, label: str,
) -> tuple[_PinnedFile, bytes]:
    item, raw = _open_existing(root, name, limits, label=label)
    try:
        try:
            os.fsync(item.fd)
            os.fsync(root.fd)
        except OSError as exc:
            raise ProducerPublicationIndeterminate(
                f"{label} bytes are authentic but durability recovery failed"
            ) from exc
        raw = _recheck_file(
            root, item, limits, root_label=root_label, label=label
        )
        return item, raw
    except BaseException:
        os.close(item.fd)
        raise


def _snapshot_member_name(role: str, binding: Mapping[str, object]) -> str:
    fixed = {
        "study_manifest": "inputs/study-manifest.json",
        "accepted_responses": "inputs/panelist-responses.jsonl",
        "raw_provider_returns": "inputs/raw-provider-returns.jsonl",
        "rejected_attempts": "inputs/rejected-attempts.jsonl",
        "dispatch_audit": "inputs/dispatch-audit.jsonl",
        "command_dispatch_audit_input": "inputs/command-dispatch-audit-input.jsonl",
        "screening_jobs": "inputs/screening-jobs.json",
        "screening_response_projection": "inputs/screening-response-projection.jsonl",
        "boundary_response_projection": "inputs/boundary-response-projection.jsonl",
        "recovery_configuration": "inputs/recovery-configuration.json",
        "screening_result": "inputs/screening-model-results.json",
        "screening_producer_evidence": "inputs/screening-producer-evidence.json",
        "result": "results/" + str(binding["path"]),
    }
    return fixed[role]


def _snapshot_bindings(
    record: Mapping[str, object]
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    values = {
        **dict(record["input_bindings"]),
        "result": record["result_binding"],
    }
    for role, raw in sorted(values.items()):
        if raw is None:
            continue
        value = dict(raw)
        result[role] = {
            "member_path": _snapshot_member_name(role, value),
            "raw_bytes_sha256": value["raw_bytes_sha256"],
            "canonical_document_sha256": value["canonical_document_sha256"],
            "record_count": value["record_count"],
        }
    return result


def _validate_snapshot(
    record: Mapping[str, object], *, snapshot_root: Path,
) -> EvidenceSnapshot:
    surface = str(record["surface"])
    run_id = str(record["run_id"])
    result_sha256 = str(record["result_binding"]["canonical_document_sha256"])
    recovered = recover_evidence_snapshot_publication(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    )
    binding = record["snapshot_binding"]
    if (
        recovered.snapshot_id != binding["snapshot_id"]
        or recovered.snapshot_sha256 != binding["snapshot_sha256"]
        or recovered.archive_sha256 != binding["archive_sha256"]
        or recovered.frozen_at != record["frozen_at"]
    ):
        _auth("producer evidence snapshot binding is spliced or stale")
    expected_bindings = _snapshot_bindings(record)
    if dict(recovered.bindings) != expected_bindings:
        _auth("snapshot bindings do not equal the producer evidence bindings")

    # Require the member world itself to be exactly inputs/results plus the
    # sealed runtime envelope; bindings alone cannot prove absence of extras.
    joint = _open_joint(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    )
    try:
        expected_members = set(
            binding["member_path"] for binding in expected_bindings.values()
        ) | {
            "runtime/" + str(row["path"])
            for row in record["producer_semantics"]["dependency_closure"]
        }
        if {member.path for member in joint.members} != expected_members:
            _auth("snapshot member manifest is not the closed producer world")
    finally:
        _close_joint(joint)

    with open_evidence_snapshot(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    ) as snapshot:
        _require_live_snapshot_matches(recovered, snapshot)
        for role, expected in expected_bindings.items():
            actual = dict(snapshot.bindings)[role]
            if actual != expected:
                _auth(f"snapshot binding changed: {role}")
            snapshot.resolve_member(role)
        runtime = _resolve_runtime_for_replay(snapshot)
        closure = record["producer_semantics"]["dependency_closure"]
        _validate_staged_closure(runtime, closure)
        fingerprint = _build_runtime_fingerprint(runtime)
        if fingerprint != record["producer_semantics"]["runtime_fingerprint"]:
            _auth("snapshot scientific runtime no longer matches sealed semantics")
        manifest = _read_snapshot_json(snapshot, "study_manifest")
        recovery = (
            _read_snapshot_json(snapshot, "recovery_configuration")
            if surface != "pairwise_boundary_ordering"
            else None
        )
        configuration, upstream_semantics = _configuration_from_documents(
            surface=surface,
            manifest=manifest,
            recovery=recovery,
            input_bindings=record["input_bindings"],
        )
        policy = record["producer_semantics"]["policy_bindings"]
        if surface == "complete_exposure_ordering":
            if (
                policy["recovery_configuration_sha256"]
                != sha256_json(configuration["recovery_configuration"])
            ):
                _auth("snapshot complete policy configuration is spliced")
        elif surface == "maxdiff_screening_ordering":
            if (
                policy["maxdiff_configuration_sha256"]
                != sha256_json(configuration["maxdiff_configuration"])
                or policy["recovery_configuration_sha256"]
                != sha256_json(configuration["recovery_configuration"])
            ):
                _auth("snapshot MaxDiff producer configuration is spliced")
        elif (
            policy["pairwise_configuration_sha256"]
            != sha256_json(configuration["pairwise_configuration"])
            or policy["upstream_screening_producer_semantics_sha256"]
            != upstream_semantics
        ):
            _auth("snapshot pairwise producer configuration is spliced")
    return recovered


def _read_snapshot_json(
    snapshot: object, binding_name: str
) -> dict[str, object]:
    path = snapshot.resolve_member(binding_name)
    fd: int | None = None
    try:
        fd = os.open(path, _READ_FLAGS)
        value = os.fstat(fd)
        selected = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or stat.S_IMODE(value.st_mode) != 0o400
            or _identity(value) != _identity(selected)
        ):
            _auth(f"snapshot {binding_name} member is unsafe")
        raw = _read_bounded(
            fd, _RECEIPT_LIMITS, label=f"snapshot {binding_name}"
        )
    except OSError as exc:
        _auth(f"snapshot {binding_name} could not be read safely", exc)
    finally:
        if fd is not None:
            os.close(fd)
    value = _parse_json(
        raw, _RECEIPT_LIMITS, label=f"snapshot {binding_name}"
    )
    if not isinstance(value, dict):
        _auth(f"snapshot {binding_name} must be one JSON object")
    return value


def _validate_recursive_upstream(
    record: Mapping[str, object], *, evidence_root: Path, snapshot_root: Path,
    expected_snapshot: EvidenceSnapshot,
) -> None:
    if record["surface"] != "pairwise_boundary_ordering":
        return
    upstream = record["input_bindings"]["screening_producer_evidence"]
    run_id = str(record["run_id"])
    validated = validate_synthetic_producer_evidence(
        surface="maxdiff_screening_ordering",
        run_id=run_id,
        result_sha256=str(upstream["result_sha256"]),
        evidence_root=evidence_root,
        snapshot_root=snapshot_root,
    )
    if (
        validated["producer_evidence_sha256"]
        != upstream["producer_evidence_sha256"]
        or validated["producer_semantics"]["producer_semantics_sha256"]
        != upstream["producer_semantics_sha256"]
        or validated["result_binding"]["raw_bytes_sha256"]
        != upstream["result_bytes_sha256"]
    ):
        _auth("recursive screening producer evidence is spliced")
    result_sha256 = str(
        record["result_binding"]["canonical_document_sha256"]
    )
    with open_evidence_snapshot(
        surface=str(record["surface"]),
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    ) as snapshot:
        _require_live_snapshot_matches(expected_snapshot, snapshot)
        path = snapshot.resolve_member("screening_producer_evidence")
        fd: int | None = None
        try:
            fd = os.open(path, _READ_FLAGS)
            value = os.fstat(fd)
            selected = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_nlink != 1
                or stat.S_IMODE(value.st_mode) != 0o400
                or _identity(value) != _identity(selected)
            ):
                _auth("snapshot upstream evidence member is unsafe")
            raw = _read_bounded(
                fd, _RECEIPT_LIMITS, label="snapshot upstream evidence"
            )
        except OSError as exc:
            _auth("snapshot upstream evidence could not be read safely", exc)
        finally:
            if fd is not None:
                os.close(fd)
        copied = _parse_receipt(
            raw,
            surface="maxdiff_screening_ordering",
            run_id=run_id,
            result_sha256=str(upstream["result_sha256"]),
        )
    if copied != validated:
        _auth("snapshot upstream evidence is not the canonical recursive receipt")


def _recover_receipt(
    *, surface: str, run_id: str, result_sha256: str,
    evidence_root: Path, snapshot_root: Path,
    expected_root: _PinnedRoot | None = None,
    expected_item: _PinnedFile | None = None,
) -> dict[str, object]:
    if expected_item is not None and expected_root is None:
        _auth("expected receipt authority requires its original root")
    root = _open_root(evidence_root, label="evidence_root")
    item: _PinnedFile | None = None
    try:
        if expected_root is not None:
            _recheck_root(expected_root, label="original evidence_root")
            if (
                root.identity != expected_root.identity
                or root.mode != expected_root.mode
                or root.chain != expected_root.chain
            ):
                _auth("recovered evidence_root is not the original authority")
        revocation_name = _revocation_name(surface, run_id, result_sha256)
        if _entry_exists(root, revocation_name):
            _auth("producer evidence is revoked")
        item, raw = _recover_file(
            root=root,
            name=_receipt_name(surface, run_id, result_sha256),
            limits=_RECEIPT_LIMITS,
            root_label="evidence_root",
            label="producer evidence receipt",
        )
        if expected_item is not None:
            if item.identity != expected_item.identity:
                _auth(
                    "recovered producer evidence is not the "
                    "exclusive-created receipt"
                )
            _recheck_file(
                expected_root, expected_item, _RECEIPT_LIMITS,
                root_label="original evidence_root",
                label="original producer evidence receipt",
            )
        record = _parse_receipt(
            raw, surface=surface, run_id=run_id, result_sha256=result_sha256
        )
        if _entry_exists(root, revocation_name):
            _auth("producer evidence was revoked during validation")
        expected_snapshot = _validate_snapshot(
            record, snapshot_root=snapshot_root
        )
        _validate_recursive_upstream(
            record,
            evidence_root=evidence_root,
            snapshot_root=snapshot_root,
            expected_snapshot=expected_snapshot,
        )
        raw = _recheck_file(
            root, item, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        final = _parse_receipt(
            raw, surface=surface, run_id=run_id, result_sha256=result_sha256
        )
        if final != record or _entry_exists(root, revocation_name):
            _auth("producer evidence or publication state changed")
        if expected_item is not None:
            _recheck_file(
                expected_root, expected_item, _RECEIPT_LIMITS,
                root_label="original evidence_root",
                label="original producer evidence receipt",
            )
        return final
    finally:
        if item is not None:
            os.close(item.fd)
        os.close(root.fd)


@_closed_public_failures
def recover_synthetic_producer_evidence_publication(
    *, surface: str, run_id: str, result_sha256: str,
    evidence_root: Path, snapshot_root: Path,
) -> dict[str, object]:
    """Resolve receipt durability and fully reauthenticate its producer world."""
    root = _open_root(evidence_root, label="evidence_root")
    try:
        revoked = _entry_exists(
            root, _revocation_name(surface, run_id, result_sha256)
        )
    finally:
        os.close(root.fd)
    if revoked:
        recover_synthetic_producer_revocation_publication(
            surface=surface, run_id=run_id, result_sha256=result_sha256,
            evidence_root=evidence_root,
        )
        _auth("producer evidence is durably revoked")
    return _recover_receipt(
        surface=surface, run_id=run_id, result_sha256=result_sha256,
        evidence_root=evidence_root, snapshot_root=snapshot_root,
    )


def _recover_revocation(
    *, surface: str, run_id: str, result_sha256: str, evidence_root: Path,
    expected_root: _PinnedRoot | None = None,
    expected_item: _PinnedFile | None = None,
) -> dict[str, object]:
    if expected_item is not None and expected_root is None:
        _auth("expected revocation authority requires its original root")
    identifier = _receipt_id(surface, run_id, result_sha256)
    root = _open_root(evidence_root, label="evidence_root")
    item: _PinnedFile | None = None
    receipt: _PinnedFile | None = None
    try:
        if expected_root is not None:
            _recheck_root(expected_root, label="original evidence_root")
            if (
                root.identity != expected_root.identity
                or root.mode != expected_root.mode
                or root.chain != expected_root.chain
            ):
                _auth("recovered revocation root is not the original authority")
        receipt, receipt_raw = _open_existing(
            root,
            _receipt_name(surface, run_id, result_sha256),
            _RECEIPT_LIMITS,
            label="producer evidence receipt",
        )
        record = _parse_receipt(
            receipt_raw,
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
        )
        item, raw = _recover_file(
            root=root,
            name=_revocation_name(surface, run_id, result_sha256),
            limits=_REVOCATION_LIMITS,
            root_label="evidence_root",
            label="producer revocation",
        )
        if expected_item is not None:
            if item.identity != expected_item.identity:
                _auth(
                    "recovered revocation is not the exclusive-created marker"
                )
            _recheck_file(
                expected_root, expected_item, _REVOCATION_LIMITS,
                root_label="original evidence_root",
                label="original producer revocation",
            )
        marker = _parse_revocation(
            raw,
            receipt_id=identifier,
            producer_evidence_sha256=str(record["producer_evidence_sha256"]),
        )
        raw = _recheck_file(
            root, item, _REVOCATION_LIMITS,
            root_label="evidence_root", label="producer revocation",
        )
        if _parse_revocation(
            raw,
            receipt_id=identifier,
            producer_evidence_sha256=str(record["producer_evidence_sha256"]),
        ) != marker:
            _auth("producer revocation changed during durability recovery")
        _recheck_file(
            root, receipt, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        if expected_item is not None:
            _recheck_file(
                expected_root, expected_item, _REVOCATION_LIMITS,
                root_label="original evidence_root",
                label="original producer revocation",
            )
        return marker
    finally:
        if receipt is not None:
            os.close(receipt.fd)
        if item is not None:
            os.close(item.fd)
        os.close(root.fd)


@_closed_public_failures
def recover_synthetic_producer_revocation_publication(
    *, surface: str, run_id: str, result_sha256: str, evidence_root: Path,
) -> dict[str, object]:
    """Resolve durability of the exact flat revocation marker."""
    return _recover_revocation(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        evidence_root=evidence_root,
    )


@_closed_public_failures
def validate_synthetic_producer_evidence(
    *, surface: str, run_id: str, result_sha256: str,
    evidence_root: Path, snapshot_root: Path,
) -> dict[str, object]:
    """Claim-authorizing validation with mandatory durability recovery."""
    identifier = _receipt_id(surface, run_id, result_sha256)
    root = _open_root(evidence_root, label="evidence_root")
    try:
        revoked = _entry_exists(
            root, _revocation_name(surface, run_id, result_sha256)
        )
    finally:
        os.close(root.fd)
    if revoked:
        recover_synthetic_producer_revocation_publication(
            surface=surface, run_id=run_id, result_sha256=result_sha256,
            evidence_root=evidence_root,
        )
        _auth(f"producer evidence is revoked: {identifier}")
    return _recover_receipt(
        surface=surface, run_id=run_id, result_sha256=result_sha256,
        evidence_root=evidence_root, snapshot_root=snapshot_root,
    )


def _deterministic_seed(manifest: Mapping[str, object], *, boundary: bool) -> int:
    assignment = manifest.get("assignment")
    assignment_seed = (
        assignment.get("randomization_seed")
        if isinstance(assignment, Mapping)
        else ""
    )
    suffix = "boundary-bootstrap-v1" if boundary else "screening-bootstrap-v1"
    material = f"{manifest.get('study_id', '')}|{assignment_seed}|{suffix}"
    return int.from_bytes(
        hashlib.sha256(material.encode("utf-8")).digest()[:8], "big"
    )


def _load_configuration_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _auth("producer recovery configuration is unavailable", exc)
    value = _parse_json(
        raw,
        _ResourceLimits(256 * 1024 * 1024, maximum_depth=64,
                        maximum_array_items=1_000_000,
                        maximum_object_keys=1_000_000,
                        maximum_string_bytes=16 * 1024 * 1024,
                        maximum_scalars=1_000_000),
        label="producer recovery configuration",
    )
    if not isinstance(value, dict):
        _auth("producer recovery configuration must be an object")
    return value


def _configuration(
    surface: str, assembled: Mapping[str, object]
) -> tuple[dict[str, object], str | None]:
    manifest = assembled["manifest"]
    if not isinstance(manifest, Mapping):
        _auth("authenticated manifest is unavailable")
    sources = assembled["source_paths"]
    if not isinstance(sources, Mapping):
        _auth("authenticated source map is unavailable")
    model = manifest.get("model")
    if not isinstance(model, Mapping):
        _auth("authenticated manifest.model is unavailable")
    recovery: dict[str, object] | None = None
    if surface != "pairwise_boundary_ordering":
        recovery = _load_configuration_json(Path(sources["recovery_configuration"]))
    return _configuration_from_documents(
        surface=surface,
        manifest=manifest,
        recovery=recovery,
        input_bindings=assembled["input_bindings"],
    )


def _configuration_from_documents(
    *, surface: str, manifest: Mapping[str, object],
    recovery: Mapping[str, object] | None,
    input_bindings: Mapping[str, object],
) -> tuple[dict[str, object], str | None]:
    model = manifest.get("model")
    if not isinstance(model, Mapping):
        _auth("authenticated manifest.model is unavailable")
    if surface == "complete_exposure_ordering":
        if not isinstance(recovery, Mapping):
            _auth("complete recovery configuration is unavailable")
        return {"recovery_configuration": deepcopy(dict(recovery))}, None
    if surface == "maxdiff_screening_ordering":
        if not isinstance(recovery, Mapping):
            _auth("MaxDiff recovery configuration is unavailable")
        return {
            "maxdiff_configuration": {
                "penalty_lambda": model.get("penalty_lambda"),
                "optimizer_tolerance": model.get("optimizer_tolerance"),
                "bootstrap_count": model.get("bootstrap_count"),
                "successful_fit_floor": recovery.get("successful_fit_floor"),
                "clear_finalist_threshold": model.get("clear_finalist_threshold"),
                "clear_non_finalist_threshold": model.get(
                    "clear_non_finalist_threshold"
                ),
                "seed": _deterministic_seed(manifest, boundary=False),
            },
            "recovery_configuration": deepcopy(dict(recovery)),
        }, None
    upstream = input_bindings["screening_producer_evidence"]
    return {
        "pairwise_configuration": {
            "tie_parameter": model.get("pairwise_tie_parameter"),
            "penalty_lambda": model.get("pairwise_penalty_lambda"),
            "optimizer_tolerance": model.get("pairwise_optimizer_tolerance"),
            "bootstrap_count": model.get("bootstrap_count"),
            "successful_fit_floor": 0.95,
            "seed": _deterministic_seed(manifest, boundary=True),
        }
    }, str(upstream["producer_semantics_sha256"])


@contextmanager
def _private_stage() -> Iterator[tuple[Path, Path]]:
    try:
        parent = Path(
            tempfile.mkdtemp(prefix="producer-evidence-stage-")
        ).resolve()
        os.chmod(parent, 0o700)
    except OSError as exc:
        raise ProducerEvidenceError(
            "private producer-evidence stage could not be created"
        ) from exc
    runtime = parent / "runtime"
    projection = parent / "projection.jsonl"
    active_failure = False
    try:
        yield runtime, projection
    except BaseException:
        active_failure = True
        raise
    finally:
        # Both trees are verifier-created and still inside one private parent.
        cleanup_error: OSError | None = None

        def attempt(operation, *args, **kwargs) -> None:
            nonlocal cleanup_error
            try:
                operation(*args, **kwargs)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        try:
            present = parent.exists()
            rows = list(os.walk(parent, topdown=False)) if present else []
        except OSError as exc:
            present = True
            rows = []
            cleanup_error = exc
        if present:
            for directory, directories, files in rows:
                attempt(os.chmod, directory, 0o700)
                for name in files:
                    path = Path(directory) / name
                    attempt(os.chmod, path, 0o600)
                    attempt(path.unlink, missing_ok=True)
                for name in directories:
                    path = Path(directory) / name
                    attempt(os.chmod, path, 0o700)
                    attempt(path.rmdir)
            attempt(parent.rmdir)
        if cleanup_error is not None and not active_failure:
            raise ProducerEvidenceError(
                "private producer-evidence stage cleanup failed"
            ) from cleanup_error


def _archive_inputs(
    assembled: Mapping[str, object],
    bundle: ProducerSemanticsBundle,
    projection_path: Path,
) -> tuple[dict[str, Path], dict[str, dict[str, object]], dict[str, str]]:
    inputs = dict(assembled["input_bindings"])
    sources_by_role = dict(assembled["source_paths"])
    result_binding = dict(assembled["result_binding"])
    sources: dict[str, Path] = {}
    bindings: dict[str, dict[str, object]] = {}
    projection_role = str(_SURFACES[str(assembled["surface"])]["projection"])
    projection_path.write_bytes(bytes(assembled["response_projection_bytes"]))
    sources_by_role[projection_role] = projection_path
    values = {**inputs, "result": result_binding}
    for role, raw in sorted(values.items()):
        if raw is None:
            continue
        member = _snapshot_member_name(role, raw)
        source = sources_by_role.get(role)
        if source is None:
            _auth(f"authenticated source path is missing for role: {role}")
        sources[member] = Path(source)
        value = dict(raw)
        bindings[role] = {
            "member_path": member,
            "raw_bytes_sha256": value["raw_bytes_sha256"],
            "canonical_document_sha256": value["canonical_document_sha256"],
            "record_count": value["record_count"],
        }
    for row in bundle.semantics["dependency_closure"]:
        relative = str(row["path"])
        sources["runtime/" + relative] = bundle.staged_runtime_root / relative
    replay_roles = (
        set(_REPLAY_BOUNDARY_ROLES)
        if assembled["surface"] == "pairwise_boundary_ordering"
        else set(_REPLAY_SCREENING_ROLES)
    )
    if inputs.get("command_dispatch_audit_input") is not None:
        replay_roles.add("command_dispatch_audit_input")
    return sources, bindings, {role: role for role in sorted(replay_roles)}


def _snapshot_commit_exists(
    *, surface: str, run_id: str, result_sha256: str, snapshot_root: Path
) -> bool:
    root = _open_root(snapshot_root, label="snapshot_root")
    try:
        return _entry_exists(
            root,
            _receipt_id(surface, run_id, result_sha256) + ".snapshot.json",
        )
    finally:
        os.close(root.fd)


def _create_or_recover_exact_snapshot(
    *,
    surface: str,
    run_id: str,
    result_sha256: str,
    sources: Mapping[str, Path],
    bindings: Mapping[str, Mapping[str, object]],
    allowed_roots: Sequence[Path],
    snapshot_root: Path,
) -> EvidenceSnapshot:
    if not _snapshot_commit_exists(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    ):
        return create_evidence_snapshot(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            sources=sources,
            bindings=bindings,
            allowed_roots=allowed_roots,
            snapshot_root=snapshot_root,
        )

    # A deterministic commit is never inferred durable from its shape. The
    # real Task 3 recovery protocol must succeed first. Current authenticated
    # sources are independently pinned and compared to the recovered world so
    # an equal snapshot ID cannot authorize a stale or spliced source set.
    roots = _canonical_allowed_roots(allowed_roots)
    opened = _validate_sources(sources, roots)
    try:
        recovered = recover_evidence_snapshot_publication(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            snapshot_root=snapshot_root,
        )
        if dict(recovered.bindings) != {
            name: dict(value) for name, value in bindings.items()
        }:
            _auth(
                "recovered snapshot bindings do not equal the current "
                "authenticated producer inputs"
            )
        expected_members = {
            source.member_path: (
                source.byte_count,
                source.raw_digest,
            )
            for source in opened
        }
        joint = _open_joint(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            snapshot_root=snapshot_root,
        )
        try:
            actual_members = {
                member.path: (
                    member.byte_count,
                    member.raw_bytes_sha256,
                )
                for member in joint.members
            }
            if actual_members != expected_members:
                _auth(
                    "recovered snapshot member world does not equal the "
                    "current authenticated source world"
                )
        finally:
            _close_joint(joint)
        for source in opened:
            _reauthenticate_source(source)
        return recovered
    finally:
        for source in reversed(opened):
            os.close(source.fd)


def _normalized_snapshot_bindings(
    value: object, *, label: str
) -> tuple[tuple[str, tuple[tuple[str, tuple[str, object]], ...]], ...]:
    if not isinstance(value, tuple):
        _auth(f"{label} must be one immutable binding tuple")
    normalized: list[
        tuple[str, tuple[tuple[str, tuple[str, object]], ...]]
    ] = []
    names: set[str] = set()
    try:
        for row in value:
            if not isinstance(row, tuple) or len(row) != 2:
                _auth(f"{label} rows must be exact name/value pairs")
            name, raw_binding = row
            if type(name) is not str or not name or name in names:
                _auth(f"{label} names must be unique non-empty strings")
            if not isinstance(raw_binding, Mapping):
                _auth(f"{label}.{name} must be one mapping")
            names.add(name)
            items: list[tuple[str, tuple[str, object]]] = []
            for key, raw_value in raw_binding.items():
                if type(key) is not str or not key:
                    _auth(f"{label}.{name} keys must be non-empty strings")
                if raw_value is None:
                    typed = ("null", None)
                elif type(raw_value) is bool:
                    typed = ("bool", raw_value)
                elif type(raw_value) is int:
                    typed = ("int", raw_value)
                elif type(raw_value) is str:
                    typed = ("str", raw_value)
                else:
                    _auth(
                        f"{label}.{name}.{key} has an unsupported value type"
                    )
                items.append((key, typed))
            if len({key for key, _typed in items}) != len(items):
                _auth(f"{label}.{name} contains duplicate keys")
            normalized.append((name, tuple(sorted(items))))
    except ProducerEvidenceError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        _auth(f"{label} could not be normalized exactly", exc)
    return tuple(sorted(normalized))


def _require_live_snapshot_matches(
    expected: EvidenceSnapshot, live: object
) -> None:
    try:
        expected_identity = (
            expected.snapshot_id,
            expected.frozen_at,
            expected.snapshot_sha256,
            expected.archive_sha256,
        )
        live_identity = (
            live.snapshot_id,
            live.frozen_at,
            live.snapshot_sha256,
            live.archive_sha256,
        )
        if (
            any(type(value) is not str for value in expected_identity)
            or any(type(value) is not str for value in live_identity)
            or live_identity != expected_identity
            or _normalized_snapshot_bindings(
                live.bindings, label="live snapshot bindings"
            )
            != _normalized_snapshot_bindings(
                expected.bindings, label="expected snapshot bindings"
            )
        ):
            _auth(
                "live evidence snapshot does not equal the authenticated "
                "created or recovered snapshot"
            )
    except ProducerEvidenceError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        _auth("live evidence snapshot identity could not be authenticated", exc)


def _sealed_at() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _publish_receipt(
    record: Mapping[str, object], *, evidence_root: Path, snapshot_root: Path,
) -> dict[str, object]:
    surface = str(record["surface"])
    run_id = str(record["run_id"])
    result_sha256 = str(record["result_binding"]["canonical_document_sha256"])
    raw = canonical_json_bytes(record)
    if len(raw) > _RECEIPT_BYTES:
        _auth("canonical producer evidence exceeds receipt byte limit")
    root = _open_root(evidence_root, label="evidence_root")
    fd: int | None = None
    previous_umask = os.umask(0o077)
    uncertain = False
    try:
        name = _receipt_name(surface, run_id, result_sha256)
        try:
            fd = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=root.fd)
        except FileExistsError as exc:
            raise ProducerOutputCollision(
                f"immutable producer-evidence path already exists: {root.path / name}"
            ) from exc
        except OSError as exc:
            raise ProducerEvidenceError(
                "could not create producer-evidence receipt"
            ) from exc
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.geteuid()
            or initial.st_nlink != 1
        ):
            _auth("new producer-evidence receipt is not a private regular file")
        identity = _identity(initial)
        _write_all(fd, raw)
        parsed = _parse_receipt(
            _read_bounded(fd, _RECEIPT_LIMITS, label="new producer evidence receipt"),
            surface=surface, run_id=run_id, result_sha256=result_sha256,
        )
        if parsed != record:
            _auth("new producer-evidence receipt changed during write")
        os.fchmod(fd, 0o400)
        value = os.fstat(fd)
        item = _PinnedFile(
            name, fd, identity, _stat_key(value), len(raw), _digest(raw)
        )
        _recheck_file(
            root, item, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        try:
            os.fsync(fd)
        except OSError:
            uncertain = True
        _recheck_file(
            root, item, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        try:
            os.fsync(root.fd)
        except OSError:
            uncertain = True
        _recheck_file(
            root, item, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        if uncertain:
            raise ProducerPublicationIndeterminate(
                "producer-evidence receipt bytes are complete but "
                "durability is indeterminate"
            )
        return _recover_receipt(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            evidence_root=evidence_root,
            snapshot_root=snapshot_root,
            expected_root=root,
            expected_item=item,
        )
    except (ProducerEvidenceError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ProducerEvidenceError(
            f"producer-evidence publication failed: {exc}"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if root.fd >= 0:
            os.close(root.fd)
        os.umask(previous_umask)


def _publish_revocation(
    *, surface: str, run_id: str, result_sha256: str, evidence_root: Path,
) -> dict[str, object]:
    """Publish one maintainer-selected revocation without widening public API."""
    identifier = _receipt_id(surface, run_id, result_sha256)
    root = _open_root(evidence_root, label="evidence_root")
    receipt: _PinnedFile | None = None
    item: _PinnedFile | None = None
    fd: int | None = None
    previous_umask = os.umask(0o077)
    uncertain = False
    try:
        receipt, receipt_raw = _open_existing(
            root,
            _receipt_name(surface, run_id, result_sha256),
            _RECEIPT_LIMITS,
            label="producer evidence receipt",
        )
        record = _parse_receipt(
            receipt_raw,
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
        )
        marker = {
            "schema_version": _REVOCATION_VERSION,
            "receipt_id": identifier,
            "producer_evidence_sha256": record[
                "producer_evidence_sha256"
            ],
            "status": "revoked",
        }
        raw = canonical_json_bytes(marker)
        if len(raw) > _REVOCATION_BYTES:
            _auth("canonical producer revocation exceeds byte limit")
        name = _revocation_name(surface, run_id, result_sha256)
        try:
            fd = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=root.fd)
        except FileExistsError as exc:
            raise ProducerOutputCollision(
                f"immutable producer-revocation path already exists: "
                f"{root.path / name}"
            ) from exc
        except OSError as exc:
            raise ProducerEvidenceError(
                "could not create producer revocation"
            ) from exc
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.geteuid()
            or initial.st_nlink != 1
        ):
            _auth("new producer revocation is not a private regular file")
        _write_all(fd, raw)
        parsed = _parse_revocation(
            _read_bounded(
                fd, _REVOCATION_LIMITS, label="new producer revocation"
            ),
            receipt_id=identifier,
            producer_evidence_sha256=str(record["producer_evidence_sha256"]),
        )
        if parsed != marker:
            _auth("new producer revocation changed during write")
        os.fchmod(fd, 0o400)
        value = os.fstat(fd)
        item = _PinnedFile(
            name, fd, _identity(initial), _stat_key(value), len(raw),
            _digest(raw),
        )
        _recheck_file(
            root, item, _REVOCATION_LIMITS,
            root_label="evidence_root", label="producer revocation",
        )
        _recheck_file(
            root, receipt, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        try:
            os.fsync(fd)
        except OSError:
            uncertain = True
        _recheck_file(
            root, item, _REVOCATION_LIMITS,
            root_label="evidence_root", label="producer revocation",
        )
        try:
            os.fsync(root.fd)
        except OSError:
            uncertain = True
        _recheck_file(
            root, item, _REVOCATION_LIMITS,
            root_label="evidence_root", label="producer revocation",
        )
        _recheck_file(
            root, receipt, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        if uncertain:
            raise ProducerPublicationIndeterminate(
                "producer revocation bytes are complete but durability "
                "is indeterminate"
            )
        recovered = _recover_revocation(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            evidence_root=evidence_root,
            expected_root=root,
            expected_item=item,
        )
        _recheck_file(
            root, receipt, _RECEIPT_LIMITS,
            root_label="evidence_root", label="producer evidence receipt",
        )
        return recovered
    except (ProducerEvidenceError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ProducerEvidenceError(
            f"producer-revocation publication failed: {exc}"
        ) from exc
    finally:
        if receipt is not None:
            os.close(receipt.fd)
        if item is None and fd is not None:
            os.close(fd)
        elif item is not None:
            os.close(item.fd)
        os.close(root.fd)
        os.umask(previous_umask)


@_closed_public_failures
def verify_synthetic_producer(
    *, surface: str, inputs: ProducerReplayInputs,
    allowed_source_roots: Sequence[Path], runtime_root: Path,
    snapshot_root: Path, evidence_root: Path,
) -> dict[str, object]:
    """Replay one unchanged producer and publish its authenticated receipt."""
    if surface not in _SURFACES:
        _auth("producer evidence surface is unsupported")
    if not isinstance(inputs, ProducerReplayInputs):
        _auth("inputs must be one closed ProducerReplayInputs object")
    assembled = assemble_replay_inputs(surface=surface, paths=inputs)
    result_sha256 = str(
        assembled["result_binding"]["canonical_document_sha256"]
    )
    _preflight_existing_receipt(
        surface=surface,
        run_id=str(assembled["run_id"]),
        result_sha256=result_sha256,
        evidence_root=evidence_root,
    )
    with _private_stage() as (staged_runtime, projection_path):
        configuration, upstream_semantics = _configuration(surface, assembled)
        bundle = build_producer_semantics(
            surface=surface,
            runtime_root=runtime_root,
            staged_runtime_root=staged_runtime,
            configuration=configuration,
            upstream_semantics_sha256=upstream_semantics,
        )
        sources, bindings, replay_bindings = _archive_inputs(
            assembled, bundle, projection_path
        )
        roots = tuple(allowed_source_roots) + (
            projection_path.parent,
            staged_runtime,
        )
        snapshot = _create_or_recover_exact_snapshot(
            surface=surface,
            run_id=str(assembled["run_id"]),
            result_sha256=result_sha256,
            sources=sources,
            bindings=bindings,
            allowed_roots=roots,
            snapshot_root=snapshot_root,
        )
        with open_evidence_snapshot(
            surface=surface,
            run_id=str(assembled["run_id"]),
            result_sha256=result_sha256,
            snapshot_root=snapshot_root,
        ) as validated:
            _require_live_snapshot_matches(snapshot, validated)
            extracted_runtime = _resolve_runtime_for_replay(validated)
            _validate_staged_closure(
                extracted_runtime, bundle.semantics["dependency_closure"]
            )
            if (
                _build_runtime_fingerprint(extracted_runtime)
                != bundle.semantics["runtime_fingerprint"]
            ):
                _auth("extracted numerical runtime changed before replay")
            replayed = replay_producer(
                surface=surface,
                snapshot=validated,
                staged_input_bindings=replay_bindings,
                expected_result_binding=assembled["result_binding"],
                expected_import_trace=bundle.semantics["dependency_closure"],
            )
            if replayed != assembled["result_binding"]:
                _auth("producer replay did not return the exact frozen binding")
    record: dict[str, object] = {
        "schema_version": PRODUCER_EVIDENCE_VERSION,
        "surface": surface,
        "method": assembled["method"],
        "stage": assembled["stage"],
        "run_id": assembled["run_id"],
        "frozen_at": snapshot.frozen_at,
        "sealed_at": _sealed_at(),
        "producer_semantics": deepcopy(bundle.semantics),
        "input_bindings": deepcopy(assembled["input_bindings"]),
        "result_binding": deepcopy(assembled["result_binding"]),
        "snapshot_binding": {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "archive_sha256": snapshot.archive_sha256,
        },
        "producer_evidence_sha256": None,
    }
    record["producer_evidence_sha256"] = sha256_json(record)
    _validate_receipt_document(
        record,
        surface=surface,
        run_id=str(assembled["run_id"]),
        result_sha256=result_sha256,
    )
    return _publish_receipt(
        record, evidence_root=evidence_root, snapshot_root=snapshot_root
    )


__all__ = [
    "PRODUCER_EVIDENCE_VERSION",
    "verify_synthetic_producer",
    "validate_synthetic_producer_evidence",
    "recover_synthetic_producer_evidence_publication",
    "recover_synthetic_producer_revocation_publication",
]
