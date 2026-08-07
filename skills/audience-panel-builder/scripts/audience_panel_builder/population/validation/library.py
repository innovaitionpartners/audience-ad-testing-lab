"""Private immutable registry for authenticated Tier 4 claims.

Claim packages are copied once, never rewritten.  Lifecycle changes are
separate canonical JSONL records linked by their predecessor digest.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import time
from typing import Any, Mapping, Sequence

from ...common import ContractError, canonical_json_bytes, require_timestamp, sha256_json
from .package import validate_validation_package


VALIDATION_LIBRARY_VERSION = "audience-panel-validation-library-v1"
CLAIM_LIFECYCLE_EVENT_VERSION = "panel-tier4-claim-lifecycle-event-v1"
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 600.0
_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_INDEX_KEYS = {"schema_version", "updated_at", "claims"}
_ENTRY_KEYS = {
    "claim_id", "claim_sha256", "panel_id", "panel_version",
    "claim_scope_sha256", "issued_at", "expires_at", "registered_at",
    "package_sha256", "package_manifest_sha256", "relative_path",
    "event_count", "event_head_sha256",
}
_EVENT_KEYS = {
    "schema_version", "claim_id", "event_type", "effective_at", "actor_id",
    "reason", "evidence_sha256", "replacement_claim_id",
    "previous_event_sha256", "event_sha256",
}
_TRANSACTION_VERSION = "panel-tier4-claim-lifecycle-transaction-v1"
_TRANSACTION_KEYS = {
    "schema_version", "claim_id", "event", "old_event_count", "old_event_head_sha256",
    "old_event_log_byte_count", "old_event_log_sha256",
    "new_event_count", "new_event_head_sha256",
    "new_event_log_byte_count", "new_event_log_sha256",
    "transaction_sha256",
}


class LibraryError(ContractError):
    """A validation claim library cannot safely satisfy the request."""


class LibrarySafetyError(LibraryError):
    pass


class ImmutableVersionConflict(LibraryError):
    pass


class LibraryNotFoundError(LibraryError):
    pass


class LibraryLockError(LibraryError):
    pass


def _timestamp(value: object, label: str) -> datetime:
    return require_timestamp(value, label)


def _timestamp_text(value: object, label: str) -> str:
    parsed = _timestamp(value, label)
    return parsed.isoformat().replace("+00:00", "Z")


def _validate_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LibrarySafetyError(f"{label} must be a canonical lowercase hyphenated identifier")
    return value


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise LibrarySafetyError(f"{label} must be a prefixed SHA-256 digest")
    return value


def _reject_symlinks(path: Path, *, label: str) -> None:
    if ".." in path.parts:
        raise LibrarySafetyError(f"{label} must not contain parent-directory traversal")
    absolute = path.absolute(); current = Path(absolute.anchor)
    aliases = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() and (current not in aliases or current.resolve() != aliases[current]):
                raise LibrarySafetyError(f"{label} contains a symlink component")


def resolve_validation_library_root(library_root: Path | str) -> Path:
    root = Path(library_root).expanduser()
    # The CLI accepts an operator-friendly relative root, but resolve it before
    # creating anything so every subsequent containment check is absolute.
    if not root.is_absolute():
        root = root.absolute()
    _reject_symlinks(root, label="library root")
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise LibrarySafetyError("library root must be a real directory")
    return root.resolve(strict=False)


def _mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise LibrarySafetyError("library path must be a real directory")
    os.chmod(path, 0o700)


def _initialize(root: Path) -> None:
    _mkdir(root); _mkdir(root / "claims")


def _inside(root: Path, path: Path) -> None:
    try: path.relative_to(root)
    except ValueError as exc: raise LibrarySafetyError("library path escapes its root") from exc
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise LibrarySafetyError("library paths must not contain symlinks")


def _atomic(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".validation-index-", dir=path.parent)
    temp = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path); os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    except BaseException:
        temp.unlink(missing_ok=True); raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _transaction_path(root: Path) -> Path:
    path = root / "pending-lifecycle-transaction.json"; _inside(root, path); return path


def _bytes_sha256(value: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _transaction(
    event: Mapping[str, object],
    entry: Mapping[str, object],
    *,
    old_event_log_bytes: bytes = b"",
) -> dict[str, object]:
    event_bytes = canonical_json_bytes(event)
    new_event_log_bytes = old_event_log_bytes + event_bytes
    document: dict[str, object] = {
        "schema_version": _TRANSACTION_VERSION, "claim_id": entry["claim_id"], "event": dict(event),
        "old_event_count": entry["event_count"], "old_event_head_sha256": entry["event_head_sha256"],
        "old_event_log_byte_count": len(old_event_log_bytes),
        "old_event_log_sha256": _bytes_sha256(old_event_log_bytes),
        "new_event_count": entry["event_count"] + 1, "new_event_head_sha256": event["event_sha256"],
        "new_event_log_byte_count": len(new_event_log_bytes),
        "new_event_log_sha256": _bytes_sha256(new_event_log_bytes),
        "transaction_sha256": None,
    }
    document["transaction_sha256"] = sha256_json(document)
    return document


def _read_transaction(root: Path) -> dict[str, object] | None:
    path = _transaction_path(root)
    if not path.exists(): return None
    if path.is_symlink() or not path.is_file(): raise LibrarySafetyError("pending lifecycle transaction is unsafe")
    try: document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise LibrarySafetyError("pending lifecycle transaction is corrupt") from exc
    if not isinstance(document, Mapping) or set(document) != _TRANSACTION_KEYS or document.get("schema_version") != _TRANSACTION_VERSION:
        raise LibrarySafetyError("pending lifecycle transaction keys are invalid")
    checked = dict(document); supplied = checked["transaction_sha256"]; checked["transaction_sha256"] = None
    if not isinstance(supplied, str) or sha256_json(checked) != supplied: raise LibrarySafetyError("pending lifecycle transaction hash is invalid")
    _validate_id(checked["claim_id"], "transaction.claim_id")
    if checked["old_event_count"] < 0 or checked["new_event_count"] != checked["old_event_count"] + 1: raise LibrarySafetyError("pending lifecycle transaction counts are invalid")
    if checked["new_event_head_sha256"] != checked["event"].get("event_sha256"): raise LibrarySafetyError("pending lifecycle transaction head is invalid")
    for field in ("old_event_log_byte_count", "new_event_log_byte_count"):
        if (
            isinstance(checked[field], bool)
            or not isinstance(checked[field], int)
            or checked[field] < 0
        ):
            raise LibrarySafetyError(
                "pending lifecycle transaction byte counts are invalid"
            )
    event_bytes = canonical_json_bytes(checked["event"])
    if (
        checked["new_event_log_byte_count"]
        != checked["old_event_log_byte_count"] + len(event_bytes)
    ):
        raise LibrarySafetyError(
            "pending lifecycle transaction log sizes are inconsistent"
        )
    for field in ("old_event_log_sha256", "new_event_log_sha256"):
        _validate_digest(checked[field], f"transaction.{field}")
    return checked


def _append_event_bytes(path: Path, event: Mapping[str, object]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        remaining = memoryview(canonical_json_bytes(event))
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise LibrarySafetyError("claim event append made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
    finally: os.close(fd)
    _fsync_directory(path.parent)


def _write_lifecycle_index(root: Path, index: Mapping[str, object], *, claim_id: str, count: int, head: str, updated_at: str) -> None:
    claims = [{**row, "event_count": count, "event_head_sha256": head} if row["claim_id"] == claim_id else row for row in index["claims"]]
    _atomic(root / "index.json", canonical_json_bytes({"schema_version": VALIDATION_LIBRARY_VERSION, "updated_at": updated_at, "claims": claims}))


def _recover_pending_transaction(root: Path) -> None:
    transaction = _read_transaction(root)
    if transaction is None: return
    index = _read_index(root)
    entry = next((row for row in index["claims"] if row["claim_id"] == transaction["claim_id"]), None)
    if entry is None: raise LibrarySafetyError("pending lifecycle transaction references a missing claim")
    path = _events_path(root, transaction["claim_id"]); _mkdir(path.parent)
    old_match = entry["event_count"] == transaction["old_event_count"] and entry["event_head_sha256"] == transaction["old_event_head_sha256"]
    new_match = entry["event_count"] == transaction["new_event_count"] and entry["event_head_sha256"] == transaction["new_event_head_sha256"]
    try:
        raw = path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise LibrarySafetyError(
            "pending lifecycle transaction event log is unreadable"
        ) from exc
    old_size = transaction["old_event_log_byte_count"]
    new_size = transaction["new_event_log_byte_count"]
    assert isinstance(old_size, int) and isinstance(new_size, int)
    event_bytes = canonical_json_bytes(transaction["event"])
    if (
        len(raw) < old_size
        or len(raw) > new_size
        or _bytes_sha256(raw[:old_size])
        != transaction["old_event_log_sha256"]
        or raw[old_size:] != event_bytes[: len(raw) - old_size]
    ):
        raise LibrarySafetyError(
            "pending lifecycle transaction contains an unrecognized partial log"
        )
    if len(raw) < new_size:
        if not old_match:
            raise LibrarySafetyError(
                "pending lifecycle transaction state is inconsistent"
            )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.ftruncate(descriptor, old_size)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _append_event_bytes(path, transaction["event"])
        raw = path.read_bytes()
    if (
        len(raw) != new_size
        or _bytes_sha256(raw) != transaction["new_event_log_sha256"]
    ):
        raise LibrarySafetyError(
            "pending lifecycle transaction event log commitment is invalid"
        )
    events = _read_events(root, transaction["claim_id"])
    if len(events) != transaction["new_event_count"] or events[-1]["event_sha256"] != transaction["new_event_head_sha256"]:
        raise LibrarySafetyError("pending lifecycle transaction event state is inconsistent")
    if old_match:
        _write_lifecycle_index(root, index, claim_id=transaction["claim_id"], count=transaction["new_event_count"], head=transaction["new_event_head_sha256"], updated_at=transaction["event"]["effective_at"])
    elif not new_match:
        raise LibrarySafetyError("pending lifecycle transaction index state is inconsistent")
    path = _transaction_path(root); path.unlink(); _fsync_directory(root)


def _empty_index() -> dict[str, object]:
    return {"schema_version": VALIDATION_LIBRARY_VERSION, "updated_at": None, "claims": []}


def _relative(claim_id: str) -> str:
    return f"claims/{claim_id}/audience-panel-validation-package.zip"


def _validate_entry(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _ENTRY_KEYS:
        raise LibrarySafetyError("library claim entry keys do not match the allowlist")
    entry = dict(value)
    claim_id = _validate_id(entry["claim_id"], "claim_id")
    for field in ("claim_sha256", "claim_scope_sha256"):
        _validate_digest(entry[field], field)
    for field in ("panel_id",): _validate_id(entry[field], field)
    if not isinstance(entry["panel_version"], str) or not entry["panel_version"]:
        raise LibrarySafetyError("panel_version is invalid")
    for field in ("issued_at", "expires_at", "registered_at"):
        _timestamp(entry[field], field)
    if _timestamp(entry["expires_at"], "expires_at") <= _timestamp(entry["issued_at"], "issued_at"):
        raise LibrarySafetyError("claim expiry must follow issue time")
    for field in ("package_sha256", "package_manifest_sha256"):
        if not isinstance(entry[field], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[field]):
            raise LibrarySafetyError(f"{field} is invalid")
    if entry["relative_path"] != _relative(claim_id):
        raise LibrarySafetyError("claim relative path is invalid")
    if isinstance(entry["event_count"], bool) or not isinstance(entry["event_count"], int) or entry["event_count"] < 0:
        raise LibrarySafetyError("event_count is invalid")
    if entry["event_count"] == 0:
        if entry["event_head_sha256"] is not None: raise LibrarySafetyError("empty event log must have null event head")
    else:
        _validate_digest(entry["event_head_sha256"], "event_head_sha256")
    return entry


def _read_index(root: Path) -> dict[str, object]:
    path = root / "index.json"
    if not path.exists(): return _empty_index()
    _inside(root, path)
    if path.is_symlink() or not path.is_file(): raise LibrarySafetyError("library index is unsafe")
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise LibrarySafetyError("library index is corrupt") from exc
    if not isinstance(value, Mapping) or set(value) != _INDEX_KEYS or value.get("schema_version") != VALIDATION_LIBRARY_VERSION:
        raise LibrarySafetyError("library index keys do not match the allowlist")
    if value["updated_at"] is not None: _timestamp(value["updated_at"], "index.updated_at")
    if not isinstance(value["claims"], list): raise LibrarySafetyError("library claims must be an array")
    claims = [_validate_entry(item) for item in value["claims"]]
    if len({item["claim_id"] for item in claims}) != len(claims) or claims != sorted(claims, key=lambda item: item["claim_id"]):
        raise LibrarySafetyError("library claims are not canonical")
    return {"schema_version": VALIDATION_LIBRARY_VERSION, "updated_at": value["updated_at"], "claims": claims}


def _pid_running(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0: return True
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    except OSError as exc: return exc.errno != errno.ESRCH
    return True


class LibraryLock(AbstractContextManager["LibraryLock"]):
    def __init__(self, root: Path | str, *, timeout_seconds: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.root = resolve_validation_library_root(root); self.path = self.root / "library.lock"
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.descriptor: int | None = None

    def __enter__(self) -> "LibraryLock":
        _initialize(self.root)
        deadline = time.monotonic() + self.timeout_seconds
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise LibraryLockError("library lock is unsafe") from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise LibraryLockError("library lock is unsafe")
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(descriptor, 0)
                data = canonical_json_bytes({
                    "acquired_at": time.time(),
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                })
                os.write(descriptor, data)
                os.fsync(descriptor)
                self.descriptor = descriptor
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise LibraryLockError(
                        "library lock was not available within the bounded wait"
                    )
                time.sleep(0.05)

    def __exit__(self, *args: object) -> None:
        if self.descriptor is None:
            return
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _events_path(root: Path, claim_id: str) -> Path:
    path = root / "claims" / claim_id / "events.jsonl"; _inside(root, path); return path


def _read_events(root: Path, claim_id: str, *, expected_count: int | None = None, expected_head: str | None = None) -> list[dict[str, object]]:
    path = _events_path(root, claim_id)
    if not path.exists():
        if expected_count not in (None, 0) or expected_head is not None: raise LibrarySafetyError("claim event log is missing")
        return []
    if path.is_symlink() or not path.is_file(): raise LibrarySafetyError("claim event log is unsafe")
    try: lines = path.read_bytes().splitlines()
    except OSError as exc: raise LibrarySafetyError("claim event log cannot be read") from exc
    events: list[dict[str, object]] = []; predecessor: str | None = None; last: datetime | None = None
    for line in lines:
        try: event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise LibrarySafetyError("claim event log is corrupt") from exc
        if not isinstance(event, Mapping) or set(event) != _EVENT_KEYS: raise LibrarySafetyError("claim event keys do not match the allowlist")
        event = dict(event)
        if event["schema_version"] != CLAIM_LIFECYCLE_EVENT_VERSION or event["claim_id"] != claim_id or event["event_type"] not in {"expired", "superseded", "withdrawn", "invalidated"}:
            raise LibrarySafetyError("claim event identity is invalid")
        _timestamp(event["effective_at"], "event.effective_at"); _validate_id(event["actor_id"], "event.actor_id")
        if not isinstance(event["reason"], str) or not event["reason"].strip(): raise LibrarySafetyError("claim event reason is invalid")
        if not isinstance(event["evidence_sha256"], list) or not event["evidence_sha256"]: raise LibrarySafetyError("claim event evidence is invalid")
        if event["evidence_sha256"] != sorted(set(event["evidence_sha256"])): raise LibrarySafetyError("claim event evidence is not canonical")
        for digest in event["evidence_sha256"]: _validate_digest(digest, "event.evidence_sha256")
        if event["previous_event_sha256"] != predecessor: raise LibrarySafetyError("claim event chain is broken")
        unhashed = dict(event); actual = unhashed.pop("event_sha256"); unhashed["event_sha256"] = None
        if not isinstance(actual, str) or sha256_json(unhashed) != actual: raise LibrarySafetyError("claim event hash is invalid")
        current = _timestamp(event["effective_at"], "event.effective_at")
        if last is not None and current <= last: raise LibrarySafetyError("claim events are not in strict chronological order")
        last = current; predecessor = actual; events.append(event)
    if expected_count is not None and len(events) != expected_count:
        raise LibrarySafetyError("claim event log count does not match immutable index")
    actual_head = events[-1]["event_sha256"] if events else None
    if expected_count is not None and expected_head != actual_head:
        raise LibrarySafetyError("claim event log head does not match immutable index")
    return events


def _entry_from_validation(validated: Mapping[str, object], registered_at: str) -> dict[str, object]:
    claim = validated.get("claim")
    if validated.get("claim_kind") != "claim" or not isinstance(claim, Mapping) or claim.get("status") != "active":
        raise LibrarySafetyError("only a validation package with an initial active claim can be registered")
    panel = validated["panel_binding"]
    return {
        "claim_id": claim["claim_id"], "claim_sha256": claim["claim_sha256"],
        "panel_id": panel["panel_id"], "panel_version": panel["panel_version"],
        "claim_scope_sha256": validated["claim_scope_sha256"], "issued_at": claim["issued_at"],
        "expires_at": claim["expires_at"], "registered_at": registered_at,
        "package_sha256": validated["package_zip_sha256"], "package_manifest_sha256": validated["package_manifest_sha256"],
        "relative_path": _relative(claim["claim_id"]), "event_count": 0, "event_head_sha256": None,
    }


def _authenticate_entry(
    root: Path, entry: Mapping[str, object], *,
    authority_registry: object,
) -> None:
    """Re-open immutable package bytes before a registry read can authorize it."""
    path = root / str(entry["relative_path"]); _inside(root, path)
    if path.is_symlink() or not path.is_file():
        raise LibrarySafetyError("registered validation package is missing or unsafe")
    try:
        validated = validate_validation_package(
            path, authority_registry=authority_registry,
        )
    except (ContractError, OSError, ValueError) as exc:
        raise LibrarySafetyError("registered validation package failed authentication") from exc
    claim = validated.get("claim")
    if not isinstance(claim, Mapping): raise LibrarySafetyError("registered package does not contain an active claim")
    if any(validated[key] != entry[field] for key, field in (
        ("package_zip_sha256", "package_sha256"),
        ("package_manifest_sha256", "package_manifest_sha256"),
        ("claim_scope_sha256", "claim_scope_sha256"),
        ("claim_id", "claim_id"),
        ("claim_sha256", "claim_sha256"),
    )):
        raise LibrarySafetyError("registered validation package conflicts with immutable index")
    if any(claim[key] != entry[key] for key in ("claim_id", "claim_sha256", "issued_at", "expires_at")) or any(validated["panel_binding"][key] != entry[key] for key in ("panel_id", "panel_version")):
        raise LibrarySafetyError("registered claim identity conflicts with immutable index")


def _read_stable_source_bytes(source: Path) -> bytes:
    """Read one regular source inode exactly once and reject concurrent edits."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise LibrarySafetyError(
            "validation package source could not be opened safely"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LibrarySafetyError(
                "validation package source must be a regular file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns)
        ):
            raise LibrarySafetyError(
                "validation package source changed while it was being read"
            )
        value = b"".join(chunks)
        if len(value) != before.st_size:
            raise LibrarySafetyError(
                "validation package source length changed while it was being read"
            )
        return value
    finally:
        os.close(descriptor)


def register_validation_package(
    source: Path, *, library_root: Path, registered_at: str,
    authority_registry: object,
) -> dict[str, object]:
    registered = _timestamp_text(registered_at, "registered_at")
    source = Path(source); _reject_symlinks(source, label="package source")
    if source.is_symlink() or not source.is_file(): raise LibrarySafetyError("validation package source must be a real file")
    source_bytes = _read_stable_source_bytes(source)
    with tempfile.TemporaryDirectory(
        prefix="audience-tier4-registration-snapshot-",
    ) as snapshot_directory:
        snapshot = Path(snapshot_directory) / source.name
        descriptor = os.open(
            snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400,
        )
        try:
            remaining = memoryview(source_bytes)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise LibrarySafetyError(
                        "validation package snapshot write made no progress"
                    )
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        validated = validate_validation_package(
            snapshot, authority_registry=authority_registry,
        )
    entry = _entry_from_validation(validated, registered)
    if _timestamp(registered, "registered_at") < _timestamp(entry["issued_at"], "issued_at"):
        raise LibrarySafetyError("claim registration cannot precede claim issuance")
    if _timestamp(registered, "registered_at") >= _timestamp(entry["expires_at"], "expires_at"):
        raise LibrarySafetyError("initial claim must be active when registered")
    root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root)
        index = _read_index(root); existing = next((row for row in index["claims"] if row["claim_id"] == entry["claim_id"]), None)
        target = root / entry["relative_path"]; _inside(root, target)
        if existing is not None:
            if existing != entry:
                raise ImmutableVersionConflict("claim ID is already registered with different immutable bytes")
            if not target.exists() or target.read_bytes() != source_bytes:
                raise ImmutableVersionConflict("claim ID is already registered with different immutable bytes")
            return {"status": "already_registered", "claim": existing}
        orphan_matches = (
            target.is_file()
            and not target.is_symlink()
            and target.read_bytes() == source_bytes
        )
        if (target.exists() or target.is_symlink()) and not orphan_matches:
            raise ImmutableVersionConflict("claim package path already exists")
        _mkdir(target.parent)
        if not orphan_matches:
            stage = Path(tempfile.mkdtemp(prefix=".register-validation-", dir=target.parent)); os.chmod(stage, 0o700)
            try:
                staged = stage / target.name
                fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(source_bytes); handle.flush(); os.fsync(handle.fileno())
                try:
                    os.link(staged, target, follow_symlinks=False)
                except FileExistsError:
                    raise ImmutableVersionConflict(
                        "claim package path was concurrently created"
                    ) from None
                staged.unlink()
                os.chmod(target, 0o600)
                directory = os.open(target.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                try: stage.rmdir()
                except OSError: pass
        claims = sorted([*index["claims"], entry], key=lambda item: item["claim_id"])
        _atomic(root / "index.json", canonical_json_bytes({"schema_version": VALIDATION_LIBRARY_VERSION, "updated_at": registered, "claims": claims}))
    return {"status": "registered", "claim": entry}


def list_claims(
    *, library_root: Path, authority_registry: object,
) -> dict[str, object]:
    root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root)
        index = _read_index(root)
        for entry in index["claims"]:
            _authenticate_entry(
                root, entry, authority_registry=authority_registry,
            )
        return {"status": "ok", "claims": index["claims"]}


def show_claim(
    claim_id: str, *, library_root: Path, authority_registry: object,
) -> dict[str, object]:
    claim_id = _validate_id(claim_id, "claim_id"); root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root)
        entry = next((item for item in _read_index(root)["claims"] if item["claim_id"] == claim_id), None)
        if entry is None: raise LibraryNotFoundError("claim was not found")
        _authenticate_entry(
            root, entry, authority_registry=authority_registry,
        )
        events = _read_events(root, claim_id, expected_count=entry["event_count"], expected_head=entry["event_head_sha256"])
        return {"status": "ok", "claim": entry, "events": events}


def claim_lifecycle_status(
    claim_id: str,
    *,
    library_root: Path,
    as_of: str,
    authority_registry: object,
) -> dict[str, object]:
    """Resolve one authenticated claim's authoritative lifecycle as of a time."""

    claim_id = _validate_id(claim_id, "claim_id")
    current = _timestamp(as_of, "as_of")
    root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root)
        entry = next(
            (
                item for item in _read_index(root)["claims"]
                if item["claim_id"] == claim_id
            ),
            None,
        )
        if entry is None:
            raise LibraryNotFoundError("claim was not found")
        _authenticate_entry(
            root, entry, authority_registry=authority_registry,
        )
        events = _read_events(
            root,
            claim_id,
            expected_count=entry["event_count"],
            expected_head=entry["event_head_sha256"],
        )
        effective_events = [
            event for event in events
            if _timestamp(event["effective_at"], "event.effective_at") <= current
        ]
        if effective_events:
            lifecycle = str(effective_events[-1]["event_type"])
            lifecycle_event: dict[str, object] | None = effective_events[-1]
        elif (
            _timestamp(entry["issued_at"], "issued_at") > current
            or _timestamp(entry["registered_at"], "registered_at") > current
        ):
            lifecycle = "not_yet_active"
            lifecycle_event = None
        elif _timestamp(entry["expires_at"], "expires_at") <= current:
            lifecycle = "expired"
            lifecycle_event = None
        else:
            lifecycle = "active"
            lifecycle_event = None
        return {
            "status": "ok",
            "claim": entry,
            "lifecycle_status": lifecycle,
            "lifecycle_event": lifecycle_event,
            "as_of": _timestamp_text(as_of, "as_of"),
        }


def _active_at(entry: Mapping[str, object], events: Sequence[Mapping[str, object]], as_of: datetime) -> bool:
    if _timestamp(entry["issued_at"], "issued_at") > as_of or _timestamp(entry["registered_at"], "registered_at") > as_of or _timestamp(entry["expires_at"], "expires_at") <= as_of:
        return False
    return not any(_timestamp(event["effective_at"], "event.effective_at") <= as_of for event in events)


def append_claim_lifecycle_event(
    *, claim_id: str, event_type: str, effective_at: str, actor_id: str,
    reason: str, evidence_sha256: Sequence[str],
    replacement_claim_id: str | None, library_root: Path,
    authority_registry: object,
) -> dict[str, object]:
    claim_id = _validate_id(claim_id, "claim_id"); actor_id = _validate_id(actor_id, "actor_id")
    if event_type not in {"expired", "superseded", "withdrawn", "invalidated"}: raise LibrarySafetyError("event_type is invalid")
    effective = _timestamp_text(effective_at, "effective_at")
    if not isinstance(reason, str) or not reason.strip(): raise LibrarySafetyError("reason must be a non-empty string")
    evidence = sorted(set(evidence_sha256))
    if not evidence or len(evidence) != len(evidence_sha256): raise LibrarySafetyError("evidence_sha256 must be a unique non-empty sequence")
    for digest in evidence: _validate_digest(digest, "evidence_sha256")
    if event_type == "superseded":
        if replacement_claim_id is None or replacement_claim_id == claim_id: raise LibrarySafetyError("supersession requires a distinct replacement claim")
        _validate_id(replacement_claim_id, "replacement_claim_id")
    elif replacement_claim_id is not None: raise LibrarySafetyError("only supersession may name a replacement claim")
    root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root)
        index = _read_index(root); entry = next((row for row in index["claims"] if row["claim_id"] == claim_id), None)
        if entry is None: raise LibraryNotFoundError("claim was not found")
        _authenticate_entry(
            root, entry, authority_registry=authority_registry,
        )
        events = _read_events(root, claim_id, expected_count=entry["event_count"], expected_head=entry["event_head_sha256"])
        transition_at = _timestamp(effective, "effective_at")
        if events and transition_at <= _timestamp(
            events[-1]["effective_at"], "event.effective_at",
        ):
            raise LibrarySafetyError(
                "lifecycle events must be appended in strictly increasing "
                "effective-time order"
            )
        explicit_expiry = event_type == "expired" and transition_at == _timestamp(entry["expires_at"], "expires_at") and not events
        if not _active_at(entry, events, transition_at) and not explicit_expiry:
            raise LibrarySafetyError("lifecycle transition requires an active untransitioned claim")
        if _timestamp(effective, "effective_at") < _timestamp(entry["registered_at"], "registered_at"):
            raise LibrarySafetyError("lifecycle transition cannot precede registration")
        if event_type == "superseded":
            replacement = next((row for row in index["claims"] if row["claim_id"] == replacement_claim_id), None)
            if replacement is None or replacement["claim_scope_sha256"] != entry["claim_scope_sha256"]:
                raise LibrarySafetyError("supersession replacement must be a registered claim for the exact scope")
            replacement_events = _read_events(root, replacement_claim_id, expected_count=replacement["event_count"], expected_head=replacement["event_head_sha256"])
            _authenticate_entry(
                root, replacement, authority_registry=authority_registry,
            )
            if not _active_at(replacement, replacement_events, _timestamp(effective, "effective_at")):
                raise LibrarySafetyError("supersession replacement must be active")
            if replacement["claim_sha256"] not in evidence:
                raise LibrarySafetyError("supersession evidence must bind the replacement claim digest")
        previous = events[-1]["event_sha256"] if events else None
        event: dict[str, object] = {
            "schema_version": CLAIM_LIFECYCLE_EVENT_VERSION, "claim_id": claim_id,
            "event_type": event_type, "effective_at": effective, "actor_id": actor_id,
            "reason": reason, "evidence_sha256": evidence, "replacement_claim_id": replacement_claim_id,
            "previous_event_sha256": previous, "event_sha256": None,
        }
        event["event_sha256"] = sha256_json(event)
        path = _events_path(root, claim_id); _mkdir(path.parent)
        try:
            old_event_log_bytes = path.read_bytes() if path.exists() else b""
        except OSError as exc:
            raise LibrarySafetyError("claim event log cannot be read") from exc
        transaction = _transaction(
            event, entry, old_event_log_bytes=old_event_log_bytes,
        )
        _atomic(_transaction_path(root), canonical_json_bytes(transaction))
        _append_event_bytes(path, event)
        _write_lifecycle_index(root, index, claim_id=claim_id, count=transaction["new_event_count"], head=transaction["new_event_head_sha256"], updated_at=effective)
        _transaction_path(root).unlink(); _fsync_directory(root)
    return event


def current_claim(
    panel_id: str, panel_version: str, claim_scope_sha256: str, *,
    library_root: Path, as_of: str, authority_registry: object,
) -> dict[str, object]:
    panel_id = _validate_id(panel_id, "panel_id")
    if not isinstance(panel_version, str) or not panel_version: raise LibrarySafetyError("panel_version is invalid")
    _validate_digest(claim_scope_sha256, "claim_scope_sha256"); current = _timestamp(as_of, "as_of")
    root = resolve_validation_library_root(library_root)
    with LibraryLock(root):
        _recover_pending_transaction(root); index = _read_index(root)
        # Index fields are merely a committed cache.  Authenticate every immutable
        # package before using any cached identity to select an active claim.
        for entry in index["claims"]:
            _authenticate_entry(
                root, entry, authority_registry=authority_registry,
            )
        scope_entries = [
            item for item in index["claims"]
            if item["panel_id"] == panel_id and item["panel_version"] == panel_version and item["claim_scope_sha256"] == claim_scope_sha256
            and _timestamp(item["registered_at"], "registered_at") <= current
            and _timestamp(item["issued_at"], "issued_at") <= current
        ]
        if not scope_entries:
            raise LibraryNotFoundError(
                "no claim exists for the exact scope as of the requested time"
            )
        scope_entries.sort(
            key=lambda item: (
                _timestamp(item["registered_at"], "registered_at"),
                _timestamp(item["issued_at"], "issued_at"),
                item["claim_id"],
            ),
        )
        by_id = {str(item["claim_id"]): item for item in scope_entries}
        selected = scope_entries[0]
        visited: set[str] = set()
        while True:
            selected_id = str(selected["claim_id"])
            if selected_id in visited:
                raise LibrarySafetyError(
                    "claim supersession chain contains a cycle"
                )
            visited.add(selected_id)
            events = _read_events(
                root,
                selected_id,
                expected_count=selected["event_count"],
                expected_head=selected["event_head_sha256"],
            )
            effective_events = [
                event for event in events
                if _timestamp(event["effective_at"], "event.effective_at")
                <= current
            ]
            if effective_events:
                transition = effective_events[-1]
                if transition["event_type"] != "superseded":
                    raise LibraryNotFoundError(
                        "no active claim exists for the exact scope as of the "
                        "requested time"
                    )
                replacement_id = str(transition["replacement_claim_id"])
                replacement = by_id.get(replacement_id)
                if replacement is None:
                    raise LibrarySafetyError(
                        "claim supersession chain names an unavailable "
                        "replacement"
                    )
                selected = replacement
                continue
            if (
                _timestamp(selected["expires_at"], "expires_at") <= current
            ):
                raise LibraryNotFoundError(
                    "no active claim exists for the exact scope as of the "
                    "requested time"
                )
            return {"status": "ok", "claim": selected}


__all__ = [
    "CLAIM_LIFECYCLE_EVENT_VERSION", "VALIDATION_LIBRARY_VERSION", "ImmutableVersionConflict",
    "LibraryError", "LibraryLock", "LibraryLockError", "LibraryNotFoundError", "LibrarySafetyError",
    "append_claim_lifecycle_event", "claim_lifecycle_status", "current_claim",
    "list_claims", "register_validation_package",
    "resolve_validation_library_root", "show_claim",
]
