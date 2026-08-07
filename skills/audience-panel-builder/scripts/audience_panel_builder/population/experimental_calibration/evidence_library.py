"""Crash-recoverable synthetic-only outcome evidence history.

This module is deliberately self-contained inside the experimental package.
It has no production package, registration, library, or activation seam.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Mapping

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_identifier,
    require_string,
    require_timestamp,
    sha256_json,
)
from .contracts import (
    EVIDENCE_ENTRY_VERSION,
    EVIDENCE_EVENT_VERSION,
    EVIDENCE_LIBRARY_INDEX_VERSION,
    EVIDENCE_LIBRARY_VERSION,
    EVIDENCE_RECEIPT_VERSION,
    evidence_correction_identity_sha256,
    validate_creative_attribute_registry,
    validate_evidence_entry,
    validate_evidence_event,
    validate_evidence_library,
    validate_evidence_library_index,
    validate_evidence_receipt,
    validate_outcome_observation,
)


LOCK_TIMEOUT_SECONDS = 10.0
TRANSACTION_VERSION = "persona-behavior-evidence-transaction-v1"
TRANSACTION_KEYS = {
    "schema_version", "event_id",
    "old_event_count", "old_event_head_sha256",
    "old_event_log_byte_count", "old_event_log_sha256",
    "new_event_count", "new_event_head_sha256",
    "new_event_log_byte_count", "new_event_log_sha256",
    "event_bytes", "event_bytes_sha256", "event_sha256",
    "old_index_bytes", "old_index_sha256",
    "new_index_bytes", "new_index_sha256",
    "entry_relative_path", "entry_bytes", "entry_file_sha256",
    "entry_sha256", "receipt_relative_path", "receipt_bytes",
    "receipt_file_sha256", "receipt_sha256", "transaction_sha256",
}


class EvidenceLibraryError(ContractError):
    """The experimental history cannot safely satisfy a request."""


class EvidenceLibrarySafetyError(EvidenceLibraryError):
    """A path or filesystem object is outside the experimental boundary."""


class EvidenceLibraryConflict(EvidenceLibraryError):
    """Immutable or serial history already conflicts with the request."""


class EvidenceHistoryError(EvidenceLibraryError):
    """The complete evidence chain is absent, partial, or inauthentic."""


class EvidenceLibraryLockError(EvidenceLibraryError):
    """The bounded evidence-library lock could not be acquired."""


def _bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _timestamp_text(value: object, path: str) -> str:
    parsed = require_timestamp(value, path)
    return parsed.isoformat().replace("+00:00", "Z")


def _reject_symlink_components(path: Path, *, label: str) -> None:
    if ".." in path.parts:
        raise EvidenceLibrarySafetyError(
            f"{label} must not contain parent-directory traversal"
        )
    absolute = path.expanduser()
    if not absolute.is_absolute():
        absolute = absolute.absolute()
    current = Path(absolute.anchor)
    macos_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
    }
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            if current not in macos_aliases:
                raise EvidenceLibrarySafetyError(
                    f"{label} contains a symlink component"
                )
            try:
                if current.resolve() != macos_aliases[current]:
                    raise EvidenceLibrarySafetyError(
                        f"{label} contains a symlink component"
                    )
            except OSError as exc:
                raise EvidenceLibrarySafetyError(
                    f"{label} contains an unreadable symlink component"
                ) from exc


def _root_path(library_root: Path | str) -> Path:
    root = Path(library_root).expanduser()
    if not root.is_absolute():
        root = root.absolute()
    _reject_symlink_components(root, label="evidence library root")
    return root.resolve(strict=False)


def _inside(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise EvidenceLibrarySafetyError(
            "evidence library path escapes its root"
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise EvidenceLibrarySafetyError(
                "evidence library paths must not contain symlinks"
            )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(path: Path, *, root: Path, label: str) -> bytes:
    _inside(root, path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceLibrarySafetyError(f"{label} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceLibrarySafetyError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise EvidenceLibrarySafetyError(f"{label} changed while being read")
        result = b"".join(chunks)
        if len(result) != before.st_size:
            raise EvidenceLibrarySafetyError(f"{label} length changed while read")
        return result
    finally:
        os.close(descriptor)


def _read_json(path: Path, *, root: Path, label: str) -> dict[str, object]:
    raw = _read_regular(path, root=root, label=label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceHistoryError(f"{label} is not canonical JSON") from exc
    if not isinstance(value, Mapping):
        raise EvidenceHistoryError(f"{label} must be a JSON object")
    document = dict(value)
    if canonical_json_bytes(document) != raw:
        raise EvidenceHistoryError(f"{label} bytes are not canonical")
    return document


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise EvidenceLibrarySafetyError("evidence write made no progress")
        remaining = remaining[written:]


def _atomic_replace(path: Path, data: bytes, *, root: Path) -> None:
    _inside(root, path)
    if path.exists() or path.is_symlink():
        _read_regular(path, root=root, label=path.name)
    temporary = path.parent / f".atomic-{path.name}-{secrets.token_hex(8)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _publish_immutable(path: Path, data: bytes, *, root: Path) -> None:
    """Publish exact bytes with link-based no-clobber semantics."""

    _inside(root, path)
    temporary = path.parent / f".immutable-{path.name}-{secrets.token_hex(8)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
            _fsync_directory(path.parent)
        except FileExistsError:
            existing = _read_regular(path, root=root, label=path.name)
            if existing != data:
                raise EvidenceLibraryConflict(
                    f"immutable evidence target conflicts: {path.name}"
                )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _append_event_bytes(path: Path, data: bytes, *, root: Path) -> None:
    _inside(root, path)
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceLibrarySafetyError(
            "evidence event log cannot be opened safely"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceLibrarySafetyError(
                "evidence event log must be a regular file"
            )
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _unlink_regular(path: Path, *, root: Path, label: str) -> None:
    _read_regular(path, root=root, label=label)
    os.unlink(path)
    _fsync_directory(path.parent)


def _empty_index(
    *, library_id: str, created_at: str,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": EVIDENCE_LIBRARY_INDEX_VERSION,
        "library_id": library_id,
        "created_at": created_at,
        "updated_at": None,
        "event_count": 0,
        "event_head_sha256": None,
        "entry_ids": [],
        "active_entry_ids": [],
        "source_sha256": [],
        "observation_sha256": [],
        "dependency_identity_sha256": [],
        "head_receipt_sha256": None,
        "library_sha256": None,
    }
    document["library_sha256"] = sha256_json(document)
    return validate_evidence_library_index(document)


def _empty_projection(
    *, library_id: str, created_at: str, as_of: str,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": EVIDENCE_LIBRARY_VERSION,
        "library_id": library_id,
        "as_of": as_of,
        "created_at": created_at,
        "entry_ids": [],
        "entries": [],
        "historical_entry_ids": [],
        "historical_entries": [],
        "events": [],
        "event_count": 0,
        "head_receipt": None,
        "library_sha256": None,
    }
    document["library_sha256"] = sha256_json(document)
    return validate_evidence_library(document)


def _prepare_existing_root(library_root: Path | str) -> Path:
    root = _root_path(library_root)
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise EvidenceLibrarySafetyError(
            "evidence library root does not exist; initialize it first"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EvidenceLibrarySafetyError(
            "evidence library root must be a real directory"
        )
    required_files = (".library.lock", "library.json", "events.jsonl")
    required_directories = ("entries", "receipts")
    for name in required_files:
        path = root / name
        try:
            child = path.lstat()
        except FileNotFoundError as exc:
            raise EvidenceLibrarySafetyError(
                "existing root is not an initialized experimental evidence library"
            ) from exc
        if stat.S_ISLNK(child.st_mode) or not stat.S_ISREG(child.st_mode):
            raise EvidenceLibrarySafetyError(
                "existing root contains an unsafe required file"
            )
    for name in required_directories:
        path = root / name
        try:
            child = path.lstat()
        except FileNotFoundError as exc:
            raise EvidenceLibrarySafetyError(
                "existing root is not an initialized experimental evidence library"
            ) from exc
        if stat.S_ISLNK(child.st_mode) or not stat.S_ISDIR(child.st_mode):
            raise EvidenceLibrarySafetyError(
                "existing root contains an unsafe required directory"
            )
    return root


class EvidenceLibraryLock(AbstractContextManager["EvidenceLibraryLock"]):
    def __init__(
        self, root: Path, *, timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.root = root
        self.path = root / ".library.lock"
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.descriptor: int | None = None

    def __enter__(self) -> "EvidenceLibraryLock":
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as exc:
            raise EvidenceLibraryLockError(
                "evidence library lock is unsafe"
            ) from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise EvidenceLibraryLockError("evidence library lock is unsafe")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.descriptor = descriptor
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise EvidenceLibraryLockError(
                        "evidence library lock exceeded its bounded wait"
                    )
                time.sleep(0.025)

    def __exit__(self, *args: object) -> None:
        if self.descriptor is None:
            return
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def initialize_evidence_library(
    *,
    library_root: Path,
    library_id: str,
    created_at: str,
) -> dict[str, object]:
    root = _root_path(library_root)
    identifier = require_identifier(library_id, "library_id")
    timestamp = _timestamp_text(created_at, "created_at")
    parent = root.parent
    _reject_symlink_components(parent, label="evidence library parent")
    if not parent.is_dir() or parent.is_symlink():
        raise EvidenceLibrarySafetyError(
            "evidence library parent must be an existing real directory"
        )
    try:
        os.mkdir(root, 0o700)
    except FileExistsError as exc:
        raise EvidenceLibraryConflict(
            "evidence library root already exists"
        ) from exc
    except OSError as exc:
        raise EvidenceLibrarySafetyError(
            "evidence library root could not be created"
        ) from exc
    try:
        os.mkdir(root / "entries", 0o700)
        os.mkdir(root / "receipts", 0o700)
        lock_descriptor = os.open(
            root / ".library.lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(lock_descriptor)
        event_descriptor = os.open(
            root / "events.jsonl",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fsync(event_descriptor)
        os.close(event_descriptor)
        index = _empty_index(library_id=identifier, created_at=timestamp)
        _publish_immutable(
            root / "library.json", canonical_json_bytes(index), root=root
        )
        _fsync_directory(root / "entries")
        _fsync_directory(root / "receipts")
        _fsync_directory(root)
        _fsync_directory(parent)
    except BaseException:
        # An incomplete newly owned root is retained for forensic inspection.
        # Re-initialization will fail closed rather than adopting its bytes.
        raise
    return _empty_projection(
        library_id=identifier, created_at=timestamp, as_of=timestamp
    )


def _read_index(root: Path) -> dict[str, object]:
    try:
        return validate_evidence_library_index(
            _read_json(
                root / "library.json", root=root, label="evidence library index"
            )
        )
    except ContractError as exc:
        raise EvidenceHistoryError("evidence library index is invalid") from exc


def _read_events(root: Path) -> tuple[list[dict[str, object]], bytes]:
    raw = _read_regular(
        root / "events.jsonl", root=root, label="evidence event log"
    )
    if raw and not raw.endswith(b"\n"):
        raise EvidenceHistoryError("evidence event log has a partial tail")
    events: list[dict[str, object]] = []
    previous: str | None = None
    previous_at: datetime | None = None
    for index, line in enumerate(raw.splitlines(keepends=True)):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceHistoryError("evidence event log is corrupt") from exc
        if not isinstance(value, Mapping) or canonical_json_bytes(value) != line:
            raise EvidenceHistoryError("evidence event bytes are not canonical")
        try:
            event = validate_evidence_event(value)
        except ContractError as exc:
            raise EvidenceHistoryError(
                f"evidence event {index} is invalid"
            ) from exc
        if event["previous_event_sha256"] != previous:
            raise EvidenceHistoryError("evidence event chain is broken")
        effective = require_timestamp(
            event["effective_at"], "evidence_event.effective_at"
        )
        if previous_at is not None and effective <= previous_at:
            raise EvidenceHistoryError(
                "evidence events are not in strict chronological order"
            )
        previous = event["event_sha256"]
        previous_at = effective
        events.append(event)
    return events, raw


def _entry_path(root: Path, entry_id: str) -> Path:
    return root / "entries" / f"{entry_id}.json"


def _receipt_path(root: Path, event_id: str) -> Path:
    return root / "receipts" / f"{event_id}.json"


def _read_entry(root: Path, entry_id: str) -> dict[str, object]:
    try:
        entry = validate_evidence_entry(
            _read_json(
                _entry_path(root, entry_id),
                root=root,
                label=f"evidence entry {entry_id}",
            )
        )
    except ContractError as exc:
        raise EvidenceHistoryError(f"evidence entry {entry_id} is invalid") from exc
    if entry["entry_id"] != entry_id:
        raise EvidenceHistoryError("evidence entry path and identity conflict")
    return entry


def _read_receipt(root: Path, event_id: str) -> dict[str, object]:
    try:
        receipt = validate_evidence_receipt(
            _read_json(
                _receipt_path(root, event_id),
                root=root,
                label=f"evidence receipt {event_id}",
            )
        )
    except ContractError as exc:
        raise EvidenceHistoryError(
            f"evidence receipt {event_id} is invalid"
        ) from exc
    if receipt["event_id"] != event_id:
        raise EvidenceHistoryError("evidence receipt path and identity conflict")
    return receipt


def _closed_json_member_ids(
    root: Path,
    directory_name: str,
) -> list[str]:
    """List every direct JSON member and reject unsafe objects explicitly."""

    directory = root / directory_name
    _inside(root, directory)
    members: list[str] = []
    try:
        candidates = list(directory.iterdir())
    except OSError as exc:
        raise EvidenceLibrarySafetyError(
            f"{directory_name} directory cannot be enumerated safely"
        ) from exc
    for path in candidates:
        if not path.name.endswith(".json"):
            continue
        try:
            info = path.lstat()
        except OSError as exc:
            raise EvidenceLibrarySafetyError(
                f"{directory_name} JSON member cannot be inspected safely"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise EvidenceLibrarySafetyError(
                f"{directory_name} JSON members must be regular and non-symlinked"
            )
        members.append(path.stem)
    return sorted(members)


def _active_ids(events: list[dict[str, object]]) -> list[str]:
    active: set[str] = set()
    for event in events:
        if event["operation"] == "correct":
            superseded = str(event["superseded_entry_id"])
            if superseded not in active:
                raise EvidenceHistoryError(
                    "correction supersedes an entry that is not active"
                )
            active.remove(superseded)
        if event["entry_id"] in active:
            raise EvidenceHistoryError("an evidence entry was activated twice")
        active.add(str(event["entry_id"]))
    return sorted(active)


def _projection_preimage(document: Mapping[str, object]) -> dict[str, object]:
    result = dict(document)
    result["library_sha256"] = None
    receipt = result.get("head_receipt")
    if isinstance(receipt, Mapping):
        receipt_copy = dict(receipt)
        receipt_copy["projection_sha256"] = None
        receipt_copy["receipt_sha256"] = None
        result["head_receipt"] = receipt_copy
    return result


def _projection_document(
    *,
    index: Mapping[str, object],
    events: list[dict[str, object]],
    entries: Mapping[str, dict[str, object]],
    as_of: str,
    receipt: dict[str, object] | None,
) -> dict[str, object]:
    active = _active_ids(events)
    historical = [str(event["entry_id"]) for event in events]
    document: dict[str, object] = {
        "schema_version": EVIDENCE_LIBRARY_VERSION,
        "library_id": index["library_id"],
        "as_of": as_of,
        "created_at": index["created_at"],
        "entry_ids": active,
        "entries": [entries[entry_id] for entry_id in active],
        "historical_entry_ids": historical,
        "historical_entries": [entries[entry_id] for entry_id in historical],
        "events": events,
        "event_count": len(events),
        "head_receipt": receipt,
        "library_sha256": None,
    }
    document["library_sha256"] = sha256_json(document)
    return validate_evidence_library(document)


def _build_receipt_and_projection(
    *,
    index: Mapping[str, object],
    events: list[dict[str, object]],
    entries: Mapping[str, dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]]:
    event = events[-1]
    skeleton: dict[str, object] = {
        "schema_version": EVIDENCE_RECEIPT_VERSION,
        "receipt_id": event["event_id"],
        "library_id": index["library_id"],
        "effective_at": event["effective_at"],
        "event_count": len(events),
        "event_id": event["event_id"],
        "event_sha256": event["event_sha256"],
        "projection_sha256": None,
        "receipt_sha256": None,
    }
    active = _active_ids(events)
    historical = [str(member["entry_id"]) for member in events]
    preprojection: dict[str, object] = {
        "schema_version": EVIDENCE_LIBRARY_VERSION,
        "library_id": index["library_id"],
        "as_of": event["effective_at"],
        "created_at": index["created_at"],
        "entry_ids": active,
        "entries": [entries[entry_id] for entry_id in active],
        "historical_entry_ids": historical,
        "historical_entries": [entries[entry_id] for entry_id in historical],
        "events": events,
        "event_count": len(events),
        "head_receipt": skeleton,
        "library_sha256": None,
    }
    skeleton["projection_sha256"] = sha256_json(
        _projection_preimage(preprojection)
    )
    skeleton["receipt_sha256"] = sha256_json(skeleton)
    receipt = validate_evidence_receipt(skeleton)
    projection = _projection_document(
        index=index,
        events=events,
        entries=entries,
        as_of=str(event["effective_at"]),
        receipt=receipt,
    )
    return receipt, projection


def _metric_identity(
    observation: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    measurement = observation["measurement_definition"]
    assert isinstance(measurement, Mapping)
    metric_id = measurement["primary_metric_id"]
    events = [
        row for row in observation["outcome_events"]
        if isinstance(row, Mapping) and row.get("metric_id") == metric_id
    ]
    denominators = [
        row for row in observation["denominators"]
        if isinstance(row, Mapping) and row.get("metric_id") == metric_id
    ]
    if len(events) != 1 or len(denominators) != 1:
        raise ContractError(
            "observation primary metric must have one event and denominator"
        )
    event = events[0]
    denominator = denominators[0]
    reporting = observation["reporting_context"]
    assert isinstance(reporting, Mapping)
    identity: dict[str, object] = {
        "metric_id": metric_id,
        "event_kind": event["event_kind"],
        "direction": "higher_is_better",
        "denominator_kind": denominator["denominator_kind"],
        "attribution_model": measurement["attribution_model"],
        "click_window": measurement["click_window"],
        "view_window": measurement["view_window"],
        "engaged_view_window": measurement["engaged_view_window"],
        "report_time_basis": event["report_time_basis"],
        "data_status": event["data_status"],
        "currency": reporting["currency"],
        "timezone": reporting["timezone"],
    }
    return identity, sha256_json(identity)


def _persona_ids(
    observation: Mapping[str, object],
    registry: Mapping[str, object],
) -> list[str]:
    binding = observation["creative_attribute_binding"]
    assert isinstance(binding, Mapping)
    hypothesis_ids = set(binding["hypothesis_ids"])
    personas: set[str] = set()
    for definition in registry["attribute_definitions"]:
        if not isinstance(definition, Mapping):
            continue
        hypothesis = definition.get("behavioral_hypothesis")
        if (
            isinstance(hypothesis, Mapping)
            and hypothesis.get("hypothesis_id") in hypothesis_ids
        ):
            personas.add(str(hypothesis["target_persona_id"]))
    if hypothesis_ids and not personas:
        raise ContractError(
            "observation hypotheses do not resolve to registered personas"
        )
    return sorted(personas)


def _entry_document(
    *,
    observation: dict[str, object],
    registry: dict[str, object],
    ingested_at: str,
    supersedes_entry_id: str | None,
) -> dict[str, object]:
    checked_observation = validate_outcome_observation(observation)
    checked_registry = validate_creative_attribute_registry(registry)
    attribute_binding = checked_observation["creative_attribute_binding"]
    assert isinstance(attribute_binding, Mapping)
    if (
        attribute_binding["registry_id"] != checked_registry["registry_id"]
        or attribute_binding["registry_sha256"]
        != checked_registry["registry_sha256"]
    ):
        raise ContractError(
            "observation creative attribute registry binding does not match"
        )
    metric, metric_hash = _metric_identity(checked_observation)
    experiment = checked_observation["experiment_binding"]
    audience = checked_observation["audience_scope"]
    source = checked_observation["source"]
    quality = checked_observation["design_quality"]
    reporting = checked_observation["reporting_context"]
    study = checked_observation["synthetic_study_binding"]
    measurement = checked_observation["measurement_definition"]
    assert all(
        isinstance(value, Mapping)
        for value in (
            experiment, audience, source, quality, reporting, study, measurement
        )
    )
    dependency = {
        "platform": source["platform"],
        "account_id": reporting["account_id"],
        "experiment_id": experiment["experiment_id"],
        "campaign_id": experiment["campaign_id"],
        "block_id": experiment["block_id"],
        "batch_id": experiment["batch_id"],
        "arm_id": experiment["arm_id"],
        "creative_id": checked_observation["creative_binding"]["creative_id"],
        "grouping_identity": quality["grouping_identity"],
    }
    document: dict[str, object] = {
        "schema_version": EVIDENCE_ENTRY_VERSION,
        "entry_id": checked_observation["observation_id"],
        "observation": checked_observation,
        "observation_sha256": checked_observation["observation_sha256"],
        "source_sha256": source["source_sha256"],
        "creative_attribute_registry_sha256": checked_registry["registry_sha256"],
        "study_manifest_sha256": study["study_manifest_sha256"],
        "platform": source["platform"],
        "persona_ids": _persona_ids(checked_observation, checked_registry),
        "segment_id": audience["segment_id"],
        "objective": audience["objective"],
        "placement": audience["placement"],
        "experiment_id": experiment["experiment_id"],
        "campaign_id": experiment["campaign_id"],
        "block_id": experiment["block_id"],
        "batch_id": experiment["batch_id"],
        "grouping_identity": quality["grouping_identity"],
        "dependency_identity_sha256": sha256_json(dependency),
        "correction_identity_sha256": evidence_correction_identity_sha256(
            checked_observation
        ),
        "design": quality["design"],
        "evidence_maturity": reporting["maturity"],
        "metric_identity": metric,
        "metric_identity_sha256": metric_hash,
        "denominator_kind": metric["denominator_kind"],
        "attribution": {
            "model": metric["attribution_model"],
            "click_window": metric["click_window"],
            "view_window": metric["view_window"],
            "engaged_view_window": metric["engaged_view_window"],
            "report_time_basis": metric["report_time_basis"],
        },
        "ingested_at": ingested_at,
        "provenance_state": "synthetic_fixture_only",
        "descriptive_claim_boundary": "associated_with_outcome",
        "supersedes_entry_id": supersedes_entry_id,
        "entry_sha256": None,
    }
    document["entry_sha256"] = sha256_json(document)
    return validate_evidence_entry(document)


def _event_document(
    *,
    index: Mapping[str, object],
    entry: Mapping[str, object],
    operation: str,
    effective_at: str,
    superseded: Mapping[str, object] | None,
    correction_reason: str | None,
) -> dict[str, object]:
    event_id = f"event-{int(index['event_count']) + 1:06d}-{entry['entry_id']}"
    document: dict[str, object] = {
        "schema_version": EVIDENCE_EVENT_VERSION,
        "event_id": event_id,
        "effective_at": effective_at,
        "operation": operation,
        "entry_id": entry["entry_id"],
        "entry_sha256": entry["entry_sha256"],
        "superseded_entry_id": (
            superseded["entry_id"] if superseded is not None else None
        ),
        "superseded_entry_sha256": (
            superseded["entry_sha256"] if superseded is not None else None
        ),
        "correction_reason": correction_reason,
        "previous_event_sha256": index["event_head_sha256"],
        "event_sha256": None,
    }
    document["event_sha256"] = sha256_json(document)
    return validate_evidence_event(document)


def _transaction_document(
    *,
    old_index: dict[str, object],
    new_index: dict[str, object],
    old_log: bytes,
    event: dict[str, object],
    entry: dict[str, object],
    receipt: dict[str, object],
) -> dict[str, object]:
    event_bytes = canonical_json_bytes(event)
    new_log = old_log + event_bytes
    old_index_bytes = canonical_json_bytes(old_index)
    new_index_bytes = canonical_json_bytes(new_index)
    entry_bytes = canonical_json_bytes(entry)
    receipt_bytes = canonical_json_bytes(receipt)
    document: dict[str, object] = {
        "schema_version": TRANSACTION_VERSION,
        "event_id": event["event_id"],
        "old_event_count": old_index["event_count"],
        "old_event_head_sha256": old_index["event_head_sha256"],
        "old_event_log_byte_count": len(old_log),
        "old_event_log_sha256": _bytes_sha256(old_log),
        "new_event_count": new_index["event_count"],
        "new_event_head_sha256": new_index["event_head_sha256"],
        "new_event_log_byte_count": len(new_log),
        "new_event_log_sha256": _bytes_sha256(new_log),
        "event_bytes": event_bytes.decode("utf-8"),
        "event_bytes_sha256": _bytes_sha256(event_bytes),
        "event_sha256": event["event_sha256"],
        "old_index_bytes": old_index_bytes.decode("utf-8"),
        "old_index_sha256": _bytes_sha256(old_index_bytes),
        "new_index_bytes": new_index_bytes.decode("utf-8"),
        "new_index_sha256": _bytes_sha256(new_index_bytes),
        "entry_relative_path": f"entries/{entry['entry_id']}.json",
        "entry_bytes": entry_bytes.decode("utf-8"),
        "entry_file_sha256": _bytes_sha256(entry_bytes),
        "entry_sha256": entry["entry_sha256"],
        "receipt_relative_path": f"receipts/{event['event_id']}.json",
        "receipt_bytes": receipt_bytes.decode("utf-8"),
        "receipt_file_sha256": _bytes_sha256(receipt_bytes),
        "receipt_sha256": receipt["receipt_sha256"],
        "transaction_sha256": None,
    }
    document["transaction_sha256"] = sha256_json(document)
    return document


def _read_transaction(root: Path) -> dict[str, object] | None:
    path = root / "pending-evidence-transaction.json"
    if not path.exists() and not path.is_symlink():
        return None
    document = _read_json(path, root=root, label="pending evidence transaction")
    if set(document) != TRANSACTION_KEYS:
        raise EvidenceHistoryError(
            "pending evidence transaction keys are invalid"
        )
    if document["schema_version"] != TRANSACTION_VERSION:
        raise EvidenceHistoryError(
            "pending evidence transaction version is invalid"
        )
    supplied = document["transaction_sha256"]
    unhashed = dict(document)
    unhashed["transaction_sha256"] = None
    if supplied != sha256_json(unhashed):
        raise EvidenceHistoryError(
            "pending evidence transaction hash is invalid"
        )
    byte_fields = (
        ("event_bytes", "event_bytes_sha256"),
        ("old_index_bytes", "old_index_sha256"),
        ("new_index_bytes", "new_index_sha256"),
        ("entry_bytes", "entry_file_sha256"),
        ("receipt_bytes", "receipt_file_sha256"),
    )
    for value_field, hash_field in byte_fields:
        value = document[value_field]
        if not isinstance(value, str) or _bytes_sha256(value.encode()) != document[hash_field]:
            raise EvidenceHistoryError(
                f"pending evidence transaction {value_field} is invalid"
            )
    try:
        old_index = validate_evidence_library_index(
            json.loads(str(document["old_index_bytes"]))
        )
        new_index = validate_evidence_library_index(
            json.loads(str(document["new_index_bytes"]))
        )
        event = validate_evidence_event(json.loads(str(document["event_bytes"])))
        entry = validate_evidence_entry(json.loads(str(document["entry_bytes"])))
        receipt = validate_evidence_receipt(
            json.loads(str(document["receipt_bytes"]))
        )
    except (json.JSONDecodeError, ContractError) as exc:
        raise EvidenceHistoryError(
            "pending evidence transaction documents are invalid"
        ) from exc
    if canonical_json_bytes(old_index).decode() != document["old_index_bytes"]:
        raise EvidenceHistoryError("pending old index bytes are not canonical")
    if canonical_json_bytes(new_index).decode() != document["new_index_bytes"]:
        raise EvidenceHistoryError("pending new index bytes are not canonical")
    if canonical_json_bytes(event).decode() != document["event_bytes"]:
        raise EvidenceHistoryError("pending event bytes are not canonical")
    if canonical_json_bytes(entry).decode() != document["entry_bytes"]:
        raise EvidenceHistoryError("pending entry bytes are not canonical")
    if canonical_json_bytes(receipt).decode() != document["receipt_bytes"]:
        raise EvidenceHistoryError("pending receipt bytes are not canonical")
    if (
        document["old_event_count"] != old_index["event_count"]
        or document["new_event_count"] != old_index["event_count"] + 1
        or document["new_event_count"] != new_index["event_count"]
        or document["old_event_head_sha256"] != old_index["event_head_sha256"]
        or document["new_event_head_sha256"] != event["event_sha256"]
        or document["new_event_head_sha256"] != new_index["event_head_sha256"]
        or document["event_sha256"] != event["event_sha256"]
        or document["entry_sha256"] != entry["entry_sha256"]
        or document["receipt_sha256"] != receipt["receipt_sha256"]
        or document["entry_relative_path"] != f"entries/{entry['entry_id']}.json"
        or document["receipt_relative_path"] != f"receipts/{event['event_id']}.json"
    ):
        raise EvidenceHistoryError(
            "pending evidence transaction bindings are inconsistent"
        )
    old_size = document["old_event_log_byte_count"]
    new_size = document["new_event_log_byte_count"]
    if (
        isinstance(old_size, bool)
        or not isinstance(old_size, int)
        or isinstance(new_size, bool)
        or not isinstance(new_size, int)
        or new_size != old_size + len(str(document["event_bytes"]).encode())
    ):
        raise EvidenceHistoryError(
            "pending evidence transaction log sizes are invalid"
        )
    return document


def _recover_pending_transaction(root: Path) -> None:
    transaction = _read_transaction(root)
    if transaction is None:
        return
    event_log_path = root / "events.jsonl"
    index_path = root / "library.json"
    log_bytes = _read_regular(
        event_log_path, root=root, label="evidence event log"
    )
    index_bytes = _read_regular(
        index_path, root=root, label="evidence library index"
    )
    old_log = (
        len(log_bytes) == transaction["old_event_log_byte_count"]
        and _bytes_sha256(log_bytes) == transaction["old_event_log_sha256"]
    )
    new_log = (
        len(log_bytes) == transaction["new_event_log_byte_count"]
        and _bytes_sha256(log_bytes) == transaction["new_event_log_sha256"]
    )
    old_index = (
        index_bytes.decode("utf-8") == transaction["old_index_bytes"]
        and _bytes_sha256(index_bytes) == transaction["old_index_sha256"]
    )
    new_index = (
        index_bytes.decode("utf-8") == transaction["new_index_bytes"]
        and _bytes_sha256(index_bytes) == transaction["new_index_sha256"]
    )
    state = (old_log, new_log, old_index, new_index)
    recognized = {
        (True, False, True, False): "old-log-old-index",
        (False, True, True, False): "new-log-old-index",
        (False, True, False, True): "new-log-new-index",
    }
    if state not in recognized:
        raise EvidenceHistoryError(
            "pending evidence transaction has an unrecognized partial state"
        )
    entry_path = root / str(transaction["entry_relative_path"])
    receipt_path = root / str(transaction["receipt_relative_path"])
    entry_bytes = str(transaction["entry_bytes"]).encode()
    receipt_bytes = str(transaction["receipt_bytes"]).encode()
    _publish_immutable(entry_path, entry_bytes, root=root)
    if recognized[state] == "old-log-old-index":
        _append_event_bytes(
            event_log_path, str(transaction["event_bytes"]).encode(), root=root
        )
        _atomic_replace(
            index_path, str(transaction["new_index_bytes"]).encode(), root=root
        )
    elif recognized[state] == "new-log-old-index":
        _atomic_replace(
            index_path, str(transaction["new_index_bytes"]).encode(), root=root
        )
    _publish_immutable(receipt_path, receipt_bytes, root=root)
    _unlink_regular(
        root / "pending-evidence-transaction.json",
        root=root,
        label="pending evidence transaction",
    )


def _authenticate_full_history(
    root: Path,
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    bytes,
]:
    index = _read_index(root)
    events, event_log = _read_events(root)
    if len(events) != index["event_count"]:
        raise EvidenceHistoryError(
            "evidence event count does not match the library index"
        )
    actual_head = events[-1]["event_sha256"] if events else None
    if actual_head != index["event_head_sha256"]:
        raise EvidenceHistoryError(
            "evidence event head does not match the library index"
        )
    if [event["entry_id"] for event in events] != index["entry_ids"]:
        raise EvidenceHistoryError(
            "evidence event entries do not match the library index"
        )
    entries = {
        entry_id: _read_entry(root, entry_id)
        for entry_id in index["entry_ids"]
    }
    for event in events:
        entry = entries[str(event["entry_id"])]
        if (
            event["entry_sha256"] != entry["entry_sha256"]
            or event["effective_at"] != entry["ingested_at"]
        ):
            raise EvidenceHistoryError(
                "evidence event and immutable entry bindings conflict"
            )
        if event["operation"] == "append":
            if entry["supersedes_entry_id"] is not None:
                raise EvidenceHistoryError(
                    "append entry unexpectedly carries a supersession"
                )
        else:
            superseded_id = str(event["superseded_entry_id"])
            superseded = entries.get(superseded_id)
            if (
                superseded is None
                or entry["supersedes_entry_id"] != superseded_id
                or event["superseded_entry_sha256"]
                != superseded["entry_sha256"]
            ):
                raise EvidenceHistoryError(
                    "correction event and superseded entry bindings conflict"
                )
    if sorted({entry["source_sha256"] for entry in entries.values()}) != index["source_sha256"]:
        raise EvidenceHistoryError("evidence source registry is inconsistent")
    if sorted(entry["observation_sha256"] for entry in entries.values()) != index["observation_sha256"]:
        raise EvidenceHistoryError(
            "evidence observation registry is inconsistent"
        )
    if sorted(
        {entry["dependency_identity_sha256"] for entry in entries.values()}
    ) != index["dependency_identity_sha256"]:
        raise EvidenceHistoryError(
            "evidence dependency registry is inconsistent"
        )
    if _active_ids(events) != index["active_entry_ids"]:
        raise EvidenceHistoryError("active evidence projection is inconsistent")
    entry_files = _closed_json_member_ids(root, "entries")
    if entry_files != sorted(index["entry_ids"]):
        raise EvidenceHistoryError(
            "evidence entry path set does not match the index"
        )
    receipts: dict[str, dict[str, object]] = {}
    for count, event in enumerate(events, start=1):
        receipt = _read_receipt(root, str(event["event_id"]))
        if (
            receipt["library_id"] != index["library_id"]
            or receipt["event_count"] != count
            or receipt["effective_at"] != event["effective_at"]
            or receipt["event_sha256"] != event["event_sha256"]
        ):
            raise EvidenceHistoryError(
                "historical evidence receipt binding is inconsistent"
            )
        prefix = events[:count]
        projection = _projection_document(
            index=index,
            events=prefix,
            entries=entries,
            as_of=str(event["effective_at"]),
            receipt=receipt,
        )
        if receipt["projection_sha256"] != sha256_json(
            _projection_preimage(projection)
        ):
            raise EvidenceHistoryError(
                "historical evidence receipt projection is invalid"
            )
        receipts[str(event["event_id"])] = receipt
    receipt_files = _closed_json_member_ids(root, "receipts")
    if receipt_files != sorted(receipts):
        raise EvidenceHistoryError(
            "evidence receipt path set does not match the event chain"
        )
    if events:
        head = receipts[str(events[-1]["event_id"])]
        if head["receipt_sha256"] != index["head_receipt_sha256"]:
            raise EvidenceHistoryError(
                "head evidence receipt does not match the index"
            )
    return index, events, entries, receipts, event_log


def _load_locked(
    root: Path,
    *,
    as_of: str,
    expected_head_receipt: dict[str, object] | None,
) -> dict[str, object]:
    index, events, entries, receipts, _ = _authenticate_full_history(root)
    cutoff = require_timestamp(as_of, "as_of")
    created = require_timestamp(index["created_at"], "library.created_at")
    if cutoff < created:
        raise ContractError("as_of must not precede library creation")
    eligible = [
        event for event in events
        if require_timestamp(event["effective_at"], "event.effective_at") <= cutoff
    ]
    receipt = (
        receipts[str(eligible[-1]["event_id"])] if eligible else None
    )
    if expected_head_receipt is not None:
        try:
            expected = validate_evidence_receipt(expected_head_receipt)
        except ContractError as exc:
            raise EvidenceHistoryError("expected head receipt is invalid") from exc
        if (
            receipt is None
            or expected["receipt_sha256"] != receipt["receipt_sha256"]
        ):
            raise EvidenceHistoryError(
                "expected head receipt does not match the historical projection"
            )
    return _projection_document(
        index=index,
        events=eligible,
        entries=entries,
        as_of=as_of,
        receipt=receipt,
    )


def _new_index(
    *,
    old: dict[str, object],
    event: dict[str, object],
    entry: dict[str, object],
    receipt: dict[str, object],
) -> dict[str, object]:
    entry_ids = [*old["entry_ids"], entry["entry_id"]]
    active = set(old["active_entry_ids"])
    if event["operation"] == "correct":
        active.remove(event["superseded_entry_id"])
    active.add(entry["entry_id"])
    document: dict[str, object] = {
        "schema_version": EVIDENCE_LIBRARY_INDEX_VERSION,
        "library_id": old["library_id"],
        "created_at": old["created_at"],
        "updated_at": event["effective_at"],
        "event_count": int(old["event_count"]) + 1,
        "event_head_sha256": event["event_sha256"],
        "entry_ids": entry_ids,
        "active_entry_ids": sorted(active),
        "source_sha256": sorted(
            {*old["source_sha256"], entry["source_sha256"]}
        ),
        "observation_sha256": sorted(
            [*old["observation_sha256"], entry["observation_sha256"]]
        ),
        "dependency_identity_sha256": sorted(
            {
                *old["dependency_identity_sha256"],
                entry["dependency_identity_sha256"],
            }
        ),
        "head_receipt_sha256": receipt["receipt_sha256"],
        "library_sha256": None,
    }
    document["library_sha256"] = sha256_json(document)
    return validate_evidence_library_index(document)


def _append(
    *,
    root: Path,
    observation: dict[str, object],
    attribute_registry: dict[str, object],
    effective_at: str,
    superseded_entry_id: str | None,
    correction_reason: str | None,
) -> dict[str, object]:
    index, events, entries, _, old_log = _authenticate_full_history(root)
    if index["updated_at"] is None:
        previous = require_timestamp(index["created_at"], "library.created_at")
    else:
        previous = require_timestamp(index["updated_at"], "library.updated_at")
    current = require_timestamp(effective_at, "effective_at")
    if current <= previous:
        raise ContractError(
            "evidence event timestamp must be strictly after the current head"
        )
    entry = _entry_document(
        observation=observation,
        registry=attribute_registry,
        ingested_at=effective_at,
        supersedes_entry_id=superseded_entry_id,
    )
    if entry["entry_id"] in index["entry_ids"]:
        raise EvidenceLibraryConflict("evidence entry ID is already registered")
    if entry["observation_sha256"] in index["observation_sha256"]:
        raise EvidenceLibraryConflict("observation is already registered")
    superseded = None
    operation = "append"
    if superseded_entry_id is not None:
        operation = "correct"
        if superseded_entry_id not in index["active_entry_ids"]:
            raise EvidenceLibraryConflict(
                "superseded evidence entry is not currently active"
            )
        superseded = entries[superseded_entry_id]
        if entry["source_sha256"] in index["source_sha256"]:
            raise EvidenceLibraryConflict(
                "replacement source is already registered"
            )
        if (
            entry["dependency_identity_sha256"]
            != superseded["dependency_identity_sha256"]
            or entry["correction_identity_sha256"]
            != superseded["correction_identity_sha256"]
        ):
            raise EvidenceLibraryConflict(
                "correction must preserve the named entry's exact analytical row"
            )
    else:
        for active_entry_id in index["active_entry_ids"]:
            active_entry = entries[active_entry_id]
            if (
                entry["dependency_identity_sha256"]
                == active_entry["dependency_identity_sha256"]
                or entry["correction_identity_sha256"]
                == active_entry["correction_identity_sha256"]
            ):
                raise EvidenceLibraryConflict(
                    "dependent evidence is already active"
                )
    event = _event_document(
        index=index,
        entry=entry,
        operation=operation,
        effective_at=effective_at,
        superseded=superseded,
        correction_reason=correction_reason,
    )
    staged_entries = dict(entries)
    staged_entries[str(entry["entry_id"])] = entry
    staged_events = [*events, event]
    receipt_skeleton, _ = _build_receipt_and_projection(
        index=index,
        events=staged_events,
        entries=staged_entries,
    )
    new_index = _new_index(
        old=index,
        event=event,
        entry=entry,
        receipt=receipt_skeleton,
    )
    # Rebuild after the head-receipt hash has entered the exact new index.
    receipt, _ = _build_receipt_and_projection(
        index=new_index,
        events=staged_events,
        entries=staged_entries,
    )
    if receipt["receipt_sha256"] != receipt_skeleton["receipt_sha256"]:
        new_index = _new_index(
            old=index, event=event, entry=entry, receipt=receipt
        )
        receipt, _ = _build_receipt_and_projection(
            index=new_index,
            events=staged_events,
            entries=staged_entries,
        )
        if new_index["head_receipt_sha256"] != receipt["receipt_sha256"]:
            raise EvidenceHistoryError(
                "evidence receipt/index commitment did not converge"
            )
    transaction = _transaction_document(
        old_index=index,
        new_index=new_index,
        old_log=old_log,
        event=event,
        entry=entry,
        receipt=receipt,
    )
    _atomic_replace(
        root / "pending-evidence-transaction.json",
        canonical_json_bytes(transaction),
        root=root,
    )
    _recover_pending_transaction(root)
    return _load_locked(
        root, as_of=effective_at, expected_head_receipt=receipt
    )


def append_evidence_entry(
    *,
    library_root: Path,
    observation: dict[str, object],
    attribute_registry: dict[str, object],
    ingested_at: str,
) -> dict[str, object]:
    timestamp = _timestamp_text(ingested_at, "ingested_at")
    # Validate before the first filesystem interaction.
    validate_outcome_observation(observation)
    validate_creative_attribute_registry(attribute_registry)
    root = _prepare_existing_root(library_root)
    with EvidenceLibraryLock(root):
        _recover_pending_transaction(root)
        return _append(
            root=root,
            observation=observation,
            attribute_registry=attribute_registry,
            effective_at=timestamp,
            superseded_entry_id=None,
            correction_reason=None,
        )


def append_evidence_correction(
    *,
    library_root: Path,
    superseded_entry_id: str,
    replacement_observation: dict[str, object],
    attribute_registry: dict[str, object],
    correction_reason: str,
    corrected_at: str,
) -> dict[str, object]:
    superseded_id = require_identifier(
        superseded_entry_id, "superseded_entry_id"
    )
    reason = require_string(correction_reason, "correction_reason")
    timestamp = _timestamp_text(corrected_at, "corrected_at")
    validate_outcome_observation(replacement_observation)
    validate_creative_attribute_registry(attribute_registry)
    root = _prepare_existing_root(library_root)
    with EvidenceLibraryLock(root):
        _recover_pending_transaction(root)
        return _append(
            root=root,
            observation=replacement_observation,
            attribute_registry=attribute_registry,
            effective_at=timestamp,
            superseded_entry_id=superseded_id,
            correction_reason=reason,
        )


def load_evidence_library(
    *,
    library_root: Path,
    as_of: str,
    expected_head_receipt: dict[str, object] | None,
) -> dict[str, object]:
    timestamp = _timestamp_text(as_of, "as_of")
    root = _prepare_existing_root(library_root)
    with EvidenceLibraryLock(root):
        _recover_pending_transaction(root)
        return _load_locked(
            root,
            as_of=timestamp,
            expected_head_receipt=expected_head_receipt,
        )


def list_compatible_evidence(
    *,
    library_root: Path,
    persona_id: str,
    segment_id: str,
    platform: str,
    metric_identity_sha256: str,
    as_of: str,
) -> list[dict[str, object]]:
    persona = require_identifier(persona_id, "persona_id")
    segment = require_identifier(segment_id, "segment_id")
    if platform not in {"meta", "google", "linkedin", "tiktok"}:
        raise ContractError("platform is unsupported")
    if (
        not isinstance(metric_identity_sha256, str)
        or not metric_identity_sha256.startswith("sha256:")
        or len(metric_identity_sha256) != 71
    ):
        raise ContractError("metric_identity_sha256 must be a prefixed digest")
    snapshot = load_evidence_library(
        library_root=library_root,
        as_of=as_of,
        expected_head_receipt=None,
    )
    return [
        entry for entry in snapshot["entries"]
        if persona in entry["persona_ids"]
        and entry["segment_id"] == segment
        and entry["platform"] == platform
        and entry["metric_identity_sha256"] == metric_identity_sha256
    ]
