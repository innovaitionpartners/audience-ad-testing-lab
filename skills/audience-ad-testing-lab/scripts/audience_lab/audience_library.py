"""Private, immutable local registry for reusable audience packages."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import tempfile
import time
from typing import Any, Mapping
import unicodedata

from .audience_package import (
    ARCHIVE_FILES,
    PackageSafetyError,
    PackageValidationError,
    _archive_bytes,
    _safe_extract_package_archive,
    _safe_read_package_archive,
    build_audience_package,
    validate_package_archive,
)
from .audience_package_dispatch import validate_supported_audience_package
from .audience_package_v3 import (
    PACKAGE_SCHEMA_VERSION_V3,
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
    read_v3_archive_members,
)
from .audience_research import compute_scope_fingerprint


LIBRARY_SCHEMA_VERSION = "audience-library-index-v1"
ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
VERSION_PATTERN = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)\Z")
INDEX_KEYS = {"schema_version", "updated_at", "panels"}
ENTRY_KEYS = {
    "panel_id", "panel_name", "version", "registered_at",
    "package_manifest_sha256", "package_manifest_byte_count",
    "package_zip_sha256", "package_zip_byte_count", "relative_path",
}
LOCK_STALE_SECONDS = 600.0
LOCK_TIMEOUT_SECONDS = 10.0
RESOLUTION_SCHEMA_VERSION = "audience-panel-resolution-v2"
SNAPSHOT_RELATIVE_PATH = "audience/snapshot"
RESOLUTION_RELATIVE_PATH = "audience/resolution.json"
SCOPE_INPUT_KEYS = {
    "audience", "market", "geography", "category", "buying_context", "exclusions",
}
TARGET_AUDIENCE_KEYS = SCOPE_INPUT_KEYS | {
    "research_mode", "research_depth", "supplied_research_paths",
}
PROVISIONAL_KEYS = {
    "scope", "user_defined_segments", "accepted_by", "accepted_at", "expires_at",
}
PROVISIONAL_SEGMENT_KEYS = {"segment_id", "name", "description"}
RESOLVER_KEYS = {
    "schema_version", "status", "reasons", "panel_id", "panel_version",
    "snapshot_dir", "audience_lock", "context_strata", "grounded_context_profiles",
    "hashes",
}
RESOLVER_HASH_KEYS = {
    "panel_sha256", "brief_sha256", "package_manifest_sha256", "package_zip_sha256",
}
AUDIENCE_LOCK_KEYS = {
    "persona_research_brief_id", "panel_id", "panel_version", "segment_weights",
    "segment_names", "archetype_names", "segment_weight_provenance",
    "unique_archetypes", "unique_grounded_context_profiles", "attribute_provenance",
}
AUDIENCE_PACKAGE_BINDING_KEYS = {
    "panel_id", "panel_version", "panel_sha256", "panel_byte_count", "brief_id",
    "brief_sha256", "brief_byte_count", "package_manifest_sha256",
    "package_manifest_byte_count", "package_zip_sha256", "package_zip_byte_count",
    "resolved_snapshot_path",
}


class LibraryError(ValueError):
    """Base class for library failures."""


class LibrarySafetyError(LibraryError):
    """A path, identifier, index, or registered package is unsafe."""


class ImmutableVersionConflict(LibraryError):
    """An immutable panel version already exists with different content."""


class LibraryNotFoundError(LibraryError):
    """The requested immutable panel version is unavailable."""


class LibraryLockError(LibraryError):
    """The bounded library-wide lock could not be acquired."""


class AudienceResolutionBlocked(LibraryError):
    """The selected panel cannot safely be reused for the requested study scope."""

    def __init__(self, result: Mapping[str, Any]):
        self.result = dict(result)
        super().__init__("audience panel resolution requires refresh or is incompatible")


def _require_exact_object(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if set(value) != keys:
        raise ValueError(f"{label} keys do not match the allowlist")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_string_array(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{label} must be an array of non-empty strings")
    return list(value)


def _validate_scope_input(value: Any, label: str) -> dict[str, Any]:
    scope = _require_exact_object(value, SCOPE_INPUT_KEYS, label)
    normalized = {
        key: _require_text(scope.get(key), f"{label}.{key}")
        for key in ("audience", "market", "geography", "category", "buying_context")
    }
    normalized["exclusions"] = _require_string_array(
        scope.get("exclusions"), f"{label}.exclusions"
    )
    return normalized


def validate_audience_intake(value: Any) -> dict[str, Any]:
    """Validate one exact research, saved-panel, file-package, or provisional route."""

    if not isinstance(value, Mapping):
        raise ValueError("audience intake must be an object")
    routes = [
        key for key in ("target_audience", "audience_panel", "provisional_audience")
        if key in value
    ]
    if len(routes) != 1 or set(value) != set(routes):
        raise ValueError("audience intake must choose exactly one audience route")
    route = routes[0]
    payload = value[route]
    if route == "target_audience":
        target = _require_exact_object(payload, TARGET_AUDIENCE_KEYS, route)
        _validate_scope_input(
            {key: target[key] for key in SCOPE_INPUT_KEYS}, route
        )
        _require_text(target.get("research_mode"), "target_audience.research_mode")
        _require_text(target.get("research_depth"), "target_audience.research_depth")
        _require_string_array(
            target.get("supplied_research_paths"),
            "target_audience.supplied_research_paths",
        )
    elif route == "audience_panel":
        panel = payload
        if not isinstance(panel, Mapping):
            raise ValueError("audience_panel must be an object")
        source = panel.get("source")
        if source == "library":
            panel = _require_exact_object(
                panel, {"source", "panel_id", "version"}, "audience_panel"
            )
            _validate_identity(panel.get("panel_id"), panel.get("version"))
        elif source == "file":
            panel = _require_exact_object(
                panel, {"source", "package_path"}, "audience_panel"
            )
            _require_text(panel.get("package_path"), "audience_panel.package_path")
        else:
            raise ValueError("audience_panel.source must be library or file")
    else:
        provisional = _require_exact_object(payload, PROVISIONAL_KEYS, route)
        _validate_scope_input(provisional.get("scope"), "provisional_audience.scope")
        segments = provisional.get("user_defined_segments")
        if not isinstance(segments, list) or not segments:
            raise ValueError("provisional_audience.user_defined_segments must be non-empty")
        seen: set[str] = set()
        for index, raw in enumerate(segments):
            segment = _require_exact_object(
                raw, PROVISIONAL_SEGMENT_KEYS,
                f"provisional_audience.user_defined_segments[{index}]",
            )
            segment_id = _require_text(
                segment.get("segment_id"),
                f"provisional_audience.user_defined_segments[{index}].segment_id",
            )
            if not ID_PATTERN.fullmatch(segment_id) or segment_id in seen:
                raise ValueError("provisional audience segment IDs must be unique canonical IDs")
            seen.add(segment_id)
            _require_text(segment.get("name"), "provisional audience segment name")
            _require_text(segment.get("description"), "provisional audience segment description")
        _require_text(provisional.get("accepted_by"), "provisional_audience.accepted_by")
        accepted = _parse_timestamp(provisional.get("accepted_at"), "provisional_audience.accepted_at")
        expires = _parse_timestamp(provisional.get("expires_at"), "provisional_audience.expires_at")
        if expires <= accepted or (expires - accepted).total_seconds() > 30 * 86400:
            raise ValueError("provisional audience expiry must be within 30 days after acceptance")
    return {"route": route, "value": dict(payload)}


def _reject_symlink_components(path: Path, *, label: str) -> None:
    """Reject unresolved symlink traversal, preserving only fixed macOS aliases."""

    if ".." in path.parts:
        raise LibrarySafetyError(f"{label} must not contain parent-directory traversal")
    absolute = path.absolute()
    current = Path(absolute.anchor)
    platform_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
    }
    for part in absolute.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            permitted_target = platform_aliases.get(current)
            if permitted_target is None or current.resolve() != permitted_target:
                raise LibrarySafetyError(f"{label} contains a symlink component: {current}")


def resolve_library_root(library_root: Path | str | None = None) -> Path:
    if library_root is None:
        configured = os.environ.get("AUDIENCE_LAB_LIBRARY_DIR")
        candidate = Path(configured).expanduser() if configured else Path.home() / ".audience-ad-testing-lab" / "library"
    else:
        candidate = Path(library_root).expanduser()
    if not candidate.is_absolute():
        raise LibrarySafetyError("library root must be an absolute path")
    _reject_symlink_components(candidate, label="library root")
    if candidate.exists() and candidate.is_symlink():
        raise LibrarySafetyError("library root must not be a symlink")
    if candidate.exists() and not candidate.is_dir():
        raise LibrarySafetyError("library root must be a directory")
    return candidate.resolve(strict=False)


def _validate_identity(panel_id: str, version: str) -> None:
    if not isinstance(panel_id, str) or not ID_PATTERN.fullmatch(panel_id):
        raise LibrarySafetyError("panel_id must use lowercase letters, digits, and single hyphens")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise LibrarySafetyError("version must be a canonical three-part semantic version")


def _semantic_version(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise LibrarySafetyError("index contains an invalid semantic version")
    return tuple(int(part) for part in match.groups())


def _private_mkdir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise LibrarySafetyError(f"could not create private library directory: {path}") from exc
    if path.is_symlink() or not path.is_dir():
        raise LibrarySafetyError(f"library path is not a real directory: {path}")
    os.chmod(path, 0o700)


def _initialize_root(root: Path) -> None:
    _private_mkdir(root)
    _private_mkdir(root / "panels")


def _inside(root: Path, path: Path) -> bool:
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _assert_real_path(root: Path, path: Path, *, require_file: bool = False) -> None:
    if not _inside(root, path):
        raise LibrarySafetyError("library path escapes the selected library root")
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LibrarySafetyError("library path escapes the selected library root") from exc
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LibrarySafetyError("library paths must not contain symlinks")
    if require_file and (not path.exists() or not path.is_file()):
        raise LibraryNotFoundError("registered package file is missing")


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".index-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _empty_index() -> dict[str, Any]:
    return {"schema_version": LIBRARY_SCHEMA_VERSION, "updated_at": None, "panels": []}


def _expected_relative_path(panel_id: str, version: str) -> str:
    return f"panels/{panel_id}/{version}/audience-panel-package.zip"


def _validate_index(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != INDEX_KEYS:
        raise LibrarySafetyError("library index keys do not match the allowlist")
    if value.get("schema_version") != LIBRARY_SCHEMA_VERSION:
        raise LibrarySafetyError("library index schema version is invalid")
    if not isinstance(value["updated_at"], str) or not value["updated_at"]:
        raise LibrarySafetyError("library index timestamp is invalid")
    panels = value.get("panels")
    if not isinstance(panels, list):
        raise LibrarySafetyError("library index panels must be an array")
    seen: set[tuple[str, str]] = set()
    for entry in panels:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise LibrarySafetyError("library index entry keys do not match the allowlist")
        _validate_identity(entry.get("panel_id"), entry.get("version"))
        identity = (entry["panel_id"], entry["version"])
        if identity in seen:
            raise LibrarySafetyError("library index contains duplicate panel versions")
        seen.add(identity)
        if entry.get("relative_path") != _expected_relative_path(*identity):
            raise LibrarySafetyError("library index contains an unsafe relative path")
        for key in ("panel_name", "registered_at", "package_manifest_sha256", "package_zip_sha256"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise LibrarySafetyError(f"library index {key} is invalid")
        for key in ("package_manifest_sha256", "package_zip_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", entry[key]):
                raise LibrarySafetyError(f"library index {key} is invalid")
        for key in ("package_manifest_byte_count", "package_zip_byte_count"):
            if isinstance(entry.get(key), bool) or not isinstance(entry[key], int) or entry[key] < 0:
                raise LibrarySafetyError(f"library index {key} is invalid")
    expected = sorted(panels, key=lambda item: (item["panel_id"], _semantic_version(item["version"]), item["version"]))
    if panels != expected:
        raise LibrarySafetyError("library index is not in canonical order")
    return value


def _read_index(root: Path) -> dict[str, Any]:
    path = root / "index.json"
    if not path.exists():
        return _empty_index()
    _assert_real_path(root, path, require_file=True)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibrarySafetyError("library index is corrupt") from exc
    return _validate_index(value)


def _pid_running(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


class LibraryLock(AbstractContextManager["LibraryLock"]):
    def __init__(self, root: Path | str, *, timeout_seconds: float = LOCK_TIMEOUT_SECONDS, poll_seconds: float = 0.05) -> None:
        self.root = resolve_library_root(root)
        self.path = self.root / "library.lock"
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.poll_seconds = max(0.001, poll_seconds)
        self._owned_inode: tuple[int, int] | None = None

    def _remove_stale_local_lock(self) -> bool:
        try:
            before = self.path.stat(follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                return False
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or set(payload) != {"acquired_at", "host", "pid"}:
            return False
        acquired = payload.get("acquired_at")
        if isinstance(acquired, bool) or not isinstance(acquired, (int, float)) or not math.isfinite(float(acquired)):
            return False
        if not isinstance(payload.get("host"), str) or not payload["host"]:
            return False
        if isinstance(payload.get("pid"), bool) or not isinstance(payload["pid"], int) or payload["pid"] <= 0:
            return False
        if time.time() - float(acquired) <= LOCK_STALE_SECONDS:
            return False
        if payload.get("host") != socket.gethostname() or _pid_running(payload.get("pid")):
            return False
        try:
            after = self.path.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                return False
            self.path.unlink()
            return True
        except OSError:
            return False

    def __enter__(self) -> "LibraryLock":
        _initialize_root(self.root)
        deadline = time.monotonic() + self.timeout_seconds
        payload = _canonical_json({
            "acquired_at": time.time(), "host": socket.gethostname(), "pid": os.getpid(),
        })
        while True:
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(descriptor, payload)
                    os.fsync(descriptor)
                    info = os.fstat(descriptor)
                    self._owned_inode = (info.st_dev, info.st_ino)
                finally:
                    os.close(descriptor)
                return self
            except FileExistsError:
                if self.path.is_symlink():
                    raise LibraryLockError("library lock path is a symlink")
                if self._remove_stale_local_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise LibraryLockError("library lock was not available within the bounded wait")
                time.sleep(min(self.poll_seconds, max(0.0, deadline - time.monotonic())))

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._owned_inode is None:
            return
        try:
            info = self.path.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino) == self._owned_inode:
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._owned_inode = None


def _entry_from_package(validation: Mapping[str, Any], panel: Mapping[str, Any], registered_at: str) -> dict[str, Any]:
    panel_id = validation["panel_id"]
    version = validation["panel_version"]
    return {
        "panel_id": panel_id,
        "panel_name": panel["panel_name"],
        "version": version,
        "registered_at": registered_at,
        "package_manifest_sha256": validation["package_manifest_sha256"],
        "package_manifest_byte_count": validation["package_manifest_byte_count"],
        "package_zip_sha256": validation["package_zip_sha256"],
        "package_zip_byte_count": validation["package_zip_byte_count"],
        "relative_path": _expected_relative_path(panel_id, version),
    }


def _supported_package_snapshot(
    raw: bytes,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    validation = dict(validate_supported_audience_package(raw))
    if validation["schema_version"] == PACKAGE_SCHEMA_VERSION_V3:
        _snapshot, manifest_bytes = read_v3_archive_manifest(raw)
        archive_files = archive_files_v3_for_manifest(
            json.loads(manifest_bytes.decode("utf-8"))
        )
    else:
        archive_files = ARCHIVE_FILES
    files = read_v3_archive_members(raw, allowed_files=archive_files)
    panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    validation["package_manifest_byte_count"] = len(
        files["package-manifest.json"]
    )
    validation["package_zip_byte_count"] = len(raw)
    return validation, files, panel


def _validate_registered_directory(
    root: Path, target: Path, expected_members: set[str]
) -> None:
    expected = expected_members | {"audience-panel-package.zip"}
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise LibrarySafetyError("registered version directory cannot be read") from exc
    if {child.name for child in children} != expected:
        raise LibrarySafetyError("registered version directory does not match the file allowlist")
    for child in children:
        _assert_real_path(root, child, require_file=True)


def register_package(source: Path | str, *, library_root: Path | str | None = None) -> dict[str, Any]:
    root = resolve_library_root(library_root)
    if isinstance(source, (str, os.PathLike)):
        _reject_symlink_components(Path(source), label="package source path")
    try:
        raw, _manifest_bytes = read_v3_archive_manifest(source)
        validation, files, panel = _supported_package_snapshot(raw)
    except PackageSafetyError as exc:
        raise LibrarySafetyError(str(exc)) from exc
    except (PackageValidationError, UnicodeError, json.JSONDecodeError):
        raise
    if panel["persona_research"]["source_state"] == "no_research_sources" or panel["persona_research"]["status"] != "approved":
        raise PackageValidationError("provisional packages cannot be registered for reuse")
    panel_id, version = validation["panel_id"], validation["panel_version"]
    _validate_identity(panel_id, version)

    with LibraryLock(root):
        index = _read_index(root)
        existing = next((entry for entry in index["panels"] if (entry["panel_id"], entry["version"]) == (panel_id, version)), None)
        target_parent = root / "panels" / panel_id
        _private_mkdir(target_parent)
        target = target_parent / version
        state = "registered"
        effective_validation = validation
        if target.exists() or target.is_symlink():
            _assert_real_path(root, target)
            if not target.is_dir():
                raise LibrarySafetyError("registered version path is not a real directory")
            stored_zip = target / "audience-panel-package.zip"
            stored_manifest = target / "package-manifest.json"
            _assert_real_path(root, stored_zip, require_file=True)
            _assert_real_path(root, stored_manifest, require_file=True)
            try:
                stored_raw, _manifest_bytes = read_v3_archive_manifest(
                    stored_zip
                )
                stored_validation, stored_files, _stored_panel = (
                    _supported_package_snapshot(stored_raw)
                )
            except PackageSafetyError as exc:
                raise LibrarySafetyError("registered package is corrupt or unsafe") from exc
            _validate_registered_directory(root, target, set(stored_files))
            if (
                stored_manifest.read_bytes() != files["package-manifest.json"]
                or stored_validation["package_manifest_sha256"] != validation["package_manifest_sha256"]
                or stored_validation["package_zip_sha256"] != validation["package_zip_sha256"]
                or stored_validation["package_zip_byte_count"] != validation["package_zip_byte_count"]
            ):
                raise ImmutableVersionConflict(f"{panel_id} {version} is already registered with different package bytes")
            effective_validation = stored_validation
            state = "already_registered"
        else:
            if existing is not None:
                raise LibrarySafetyError("library index points to a missing immutable package")
            stage_parent = Path(tempfile.mkdtemp(prefix=".register-", dir=target_parent))
            os.chmod(stage_parent, 0o700)
            payload = stage_parent / "payload"
            try:
                payload.mkdir(mode=0o700)
                for name, member in files.items():
                    _atomic_write(payload / name, member)
                zip_path = payload / "audience-panel-package.zip"
                descriptor = os.open(zip_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(zip_path, 0o600)
                os.replace(payload, target)
                os.chmod(target, 0o700)
            finally:
                shutil.rmtree(stage_parent, ignore_errors=True)

        if existing and any(
            existing[key] != effective_validation[key]
            for key in (
                "package_manifest_sha256", "package_manifest_byte_count",
                "package_zip_sha256", "package_zip_byte_count",
            )
        ):
            raise LibrarySafetyError("library index conflicts with the immutable package")
        registered_at = existing["registered_at"] if existing else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry = _entry_from_package(effective_validation, panel, registered_at)
        panels = [item for item in index["panels"] if (item["panel_id"], item["version"]) != (panel_id, version)]
        panels.append(entry)
        panels.sort(key=lambda item: (item["panel_id"], _semantic_version(item["version"]), item["version"]))
        updated = {
            "schema_version": LIBRARY_SCHEMA_VERSION,
            "updated_at": index["updated_at"] if state == "already_registered" and existing == entry else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "panels": panels,
        }
        _atomic_write(root / "index.json", _canonical_json(updated))
    return {"status": state, "panel": entry}


def list_panels(*, library_root: Path | str | None = None) -> dict[str, Any]:
    root = resolve_library_root(library_root)
    index = _read_index(root) if root.exists() else _empty_index()
    return {"status": "ok", "panels": index["panels"]}


def show_panel(panel_id: str, version: str, *, library_root: Path | str | None = None) -> dict[str, Any]:
    _validate_identity(panel_id, version)
    root = resolve_library_root(library_root)
    if not root.exists():
        raise LibraryNotFoundError(f"panel {panel_id} version {version} was not found")
    index = _read_index(root)
    entry = next((item for item in index["panels"] if (item["panel_id"], item["version"]) == (panel_id, version)), None)
    if entry is None:
        raise LibraryNotFoundError(f"panel {panel_id} version {version} was not found")
    return {"status": "ok", "panel": entry}


def find_package(panel_id: str, version: str, *, library_root: Path | str | None = None) -> Path:
    entry = show_panel(panel_id, version, library_root=library_root)["panel"]
    root = resolve_library_root(library_root)
    path = root / entry["relative_path"]
    _assert_real_path(root, path, require_file=True)
    try:
        raw, _manifest_bytes = read_v3_archive_manifest(path)
        validation, _files, _panel = _supported_package_snapshot(raw)
    except PackageSafetyError as exc:
        raise LibrarySafetyError("registered package is corrupt or unsafe") from exc
    except PackageValidationError as exc:
        raise LibrarySafetyError("registered package failed validation") from exc
    if any(
        validation[key] != entry[key]
        for key in (
            "package_manifest_sha256", "package_manifest_byte_count",
            "package_zip_sha256", "package_zip_byte_count",
        )
    ):
        raise LibrarySafetyError("registered package does not match the immutable index")
    return path


lookup_package = find_package


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _normalized_scope_text(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFC", str(value)).strip().split()
    ).casefold()


def _reason(code: str, field: str, expected: Any, actual: Any, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _audience_lock(panel: Mapping[str, Any]) -> dict[str, Any]:
    segments = panel["segments"]
    archetypes = panel["persona_archetypes"]
    strata = panel["context_strata"]
    attribute_provenance: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stratum in strata:
        for dimension in stratum["dimensions"]:
            encoded = json.dumps(dimension, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if encoded in seen:
                continue
            seen.add(encoded)
            attribute_provenance.append({
                "attribute": dimension["name"],
                "status": dimension["status"],
                "source_evidence": list(dimension["source_evidence"]),
                "weighting_rule": stratum["weighting_rule"],
            })
    attribute_provenance.sort(
        key=lambda item: (
            item["attribute"], item["status"],
            json.dumps(item["source_evidence"], ensure_ascii=False, sort_keys=True),
            item["weighting_rule"],
        )
    )
    lock = {
        "persona_research_brief_id": panel["persona_research"]["brief_id"],
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "segment_weights": {
            segment["segment_id"]: segment["study_weight"] for segment in segments
        },
        "segment_names": {
            segment["segment_id"]: segment["name"] for segment in segments
        },
        "archetype_names": {
            archetype["persona_archetype_id"]: archetype["display_name"]
            for archetype in archetypes
        },
        "segment_weight_provenance": [
            {
                "segment_id": segment["segment_id"],
                "source": (
                    "saved_audience_panel:"
                    f"{panel['panel_id']}@{panel['version']}"
                ),
                "weighting_rule": segment["weighting_rule"],
            }
            for segment in segments
        ],
        "unique_archetypes": len(archetypes),
        "unique_grounded_context_profiles": len(panel["grounded_context_profiles"]),
        "attribute_provenance": attribute_provenance,
    }
    if set(lock) != AUDIENCE_LOCK_KEYS:
        raise AssertionError("audience lock construction drifted from its allowlist")
    return lock


def _resolution_payload(
    panel: Mapping[str, Any], validation: Mapping[str, Any], status: str,
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "status": status,
        "reasons": reasons,
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "snapshot_dir": SNAPSHOT_RELATIVE_PATH,
        "audience_lock": _audience_lock(panel),
        "context_strata": copy_json(panel["context_strata"]),
        "grounded_context_profiles": copy_json(panel["grounded_context_profiles"]),
        "hashes": {
            key: validation[key]
            for key in RESOLVER_HASH_KEYS
        },
    }
    if set(payload) != RESOLVER_KEYS or set(payload["hashes"]) != RESOLVER_HASH_KEYS:
        raise AssertionError("resolver construction drifted from its allowlist")
    return payload


def copy_json(value: Any) -> Any:
    """Copy JSON-compatible state without retaining caller-owned containers."""

    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _provisional_documents(provisional: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validated = validate_audience_intake({"provisional_audience": provisional})["value"]
    accepted = _parse_timestamp(validated["accepted_at"], "provisional_audience.accepted_at")
    expires = _parse_timestamp(validated["expires_at"], "provisional_audience.expires_at")
    accepted_text = accepted.isoformat().replace("+00:00", "Z")
    expires_text = expires.isoformat().replace("+00:00", "Z")
    scope = copy_json(validated["scope"])
    identity_seed = _canonical_json({
        "scope": scope,
        "segments": validated["user_defined_segments"],
        "accepted_at": accepted_text,
    })
    suffix = hashlib.sha256(identity_seed).hexdigest()[:16]
    panel_id = f"provisional-{suffix}"
    brief_id = f"{panel_id}-brief"
    coverage = {
        key: "empty" for key in (
            "pain_points_challenges", "motivations_goals", "decision_criteria",
            "buying_triggers", "fears_objections", "proof_needs", "media_behaviors",
        )
    }
    gap = {
        "gap": "No audience research was supplied for this provisional run.",
        "impact_on_panel": "Only the accepted audience scope, segment names and descriptions, buying context, and planning weights are available. All other audience attributes are unknown.",
        "mitigation": "Refresh through the research gate before registration or reuse.",
    }
    hypotheses = [{
        "segment_id": item["segment_id"], "name": item["name"],
        "origin": "provisional_user_defined", "finding_ids": [], "evidence_ids": [],
        "confidence": "low",
        "why_it_matters_for_ad_testing": item["description"],
    } for item in validated["user_defined_segments"]]
    brief = {
        "schema_version": "audience-research-brief-v2", "brief_id": brief_id,
        "created_at": accepted_text, "updated_at": accepted_text,
        "status": "provisional_no_research", "target_audience": scope,
        "research_mode": "provisional_no_research", "research_depth": "quick_directional",
        "research_questions": [], "evidence_sources": [], "findings": [],
        "coverage": coverage, "segment_hypotheses": hypotheses,
        "evidence_gaps": [gap],
        "privacy_confirmation": {
            "confirmed": True, "confirmed_by": validated["accepted_by"],
            "confirmed_at": accepted_text,
            "note": "User accepted a provisional run without research or person-level records.",
        },
        "approval": {
            "approved_for_panel_creation": True,
            "approved_by": validated["accepted_by"], "approved_at": accepted_text,
            "approval_note": "Accepted for this initial provisional run only; not approved for reuse.",
        },
    }
    segment_count = len(validated["user_defined_segments"])
    weight = 1.0 / segment_count
    segments: list[dict[str, Any]] = []
    archetypes: list[dict[str, Any]] = []
    strata: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    for item in validated["user_defined_segments"]:
        segment_id = item["segment_id"]
        archetype_id = f"{segment_id}-provisional-mindset"
        stratum_id = f"{segment_id}-provisional-context"
        role_context = item["description"]
        decision_context = scope["buying_context"]
        motivations: list[str] = []
        anxieties: list[str] = []
        proof_needs: list[str] = []
        segments.append({
            "segment_id": segment_id, "name": item["name"],
            "origin": "provisional_user_defined", "study_weight": weight,
            "weighting_rule": "planning_allocation", "weight_source_evidence": [],
            "finding_ids": [], "evidence_ids": [], "description": item["description"],
            "primary_needs": motivations, "primary_objections": anxieties,
            "creative_implications": [],
        })
        archetypes.append({
            "persona_archetype_id": archetype_id, "segment_id": segment_id,
            "display_name": f"{item['name']} provisional profile — attributes unknown",
            "role_context": role_context, "decision_context": decision_context,
            "motivations": motivations, "anxieties": anxieties,
            "triggers": [], "objections": [],
            "proof_needs": proof_needs, "finding_ids": [], "evidence_ids": [],
            "evidence_strength": "low",
            "inference_boundary": "Only role context and buying context come from the accepted user input. Motivations, anxieties, triggers, objections, and proof needs are unknown because no research was supplied.",
        })
        dimension = {
            "name": "audience_attribute_support", "value": "unknown_no_research",
            "status": "experimental", "source_evidence": [], "finding_ids": [],
        }
        strata.append({
            "context_stratum_id": stratum_id, "segment_id": segment_id,
            "planned_weight": weight, "weighting_rule": "planning_allocation",
            "dimensions": [dimension],
        })
        profiles.append({
            "grounded_profile_id": f"{segment_id}-provisional-profile-v1",
            "segment_id": segment_id, "persona_archetype_id": archetype_id,
            "context_stratum_id": stratum_id,
            "profile_snapshot": {
                "role_context": role_context, "decision_context": decision_context,
                "motivations": motivations, "anxieties": anxieties,
                "proof_needs": proof_needs,
            },
            "context_attribute_provenance": [{
                "attribute": dimension["name"], "value": dimension["value"],
                "status": dimension["status"], "source_evidence": [], "finding_ids": [],
            }],
        })
    panel = {
        "schema_version": "saved-audience-panel-v2", "panel_id": panel_id,
        "panel_name": "Provisional audience — no research", "version": "0.0.0",
        "created_at": accepted_text, "updated_at": accepted_text,
        "audience_scope": {**scope, "scope_fingerprint": compute_scope_fingerprint(scope)},
        "persona_research": {
            "brief_id": brief_id, "mode": "provisional_no_research",
            "status": "provisional_no_research", "approved_at": accepted_text,
            "expires_at": expires_text, "source_types": [], "evidence_ids": [],
            "coverage": coverage, "evidence_gaps": [gap],
            "source_state": "no_research_sources",
        },
        "segments": segments, "persona_archetypes": archetypes,
        "context_strata": strata, "grounded_context_profiles": profiles,
        "replicate_strategy": {
            "worker_unit": "one_context_isolated_replicate_per_job",
            "shared_context_fallback_allowed": False,
            "fields_allowed_to_vary": [],
            "fields_never_to_invent": [
                "segment", "archetype", "context stratum", "evidence",
                "motivation", "anxiety", "trigger", "objection", "proof need",
                "creative implication",
            ],
        },
        "calibration_history": [],
        "refresh_conditions": {
            "review_after": expires_text,
            "max_age_days": max(1, int((expires - accepted).total_seconds() // 86400)),
            "triggers": ["Any attempted reuse", "New audience research"],
        },
        "governance": {
            "pii_policy": "No person-level records; accepted scope and segment planning inputs only; all other audience attributes are unknown",
            "allowed_uses": ["This initial synthetic ad testing run"],
            "excluded_uses": ["Registration", "Reuse", "Population inference", "Individual targeting"],
            "privacy_confirmation": copy_json(brief["privacy_confirmation"]),
        },
    }
    return brief, panel


def materialize_provisional_audience(
    provisional: Mapping[str, Any], *, run_dir: Path | str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a run-local, non-reusable package for one accepted provisional intake."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    accepted = _parse_timestamp(provisional.get("accepted_at"), "provisional_audience.accepted_at")
    expires = _parse_timestamp(provisional.get("expires_at"), "provisional_audience.expires_at")
    if current < accepted or current >= expires:
        raise ValueError("provisional audience acceptance must be active for the initial run")
    brief, panel = _provisional_documents(provisional)
    stage_root = Path(tempfile.mkdtemp(prefix="audience-provisional-"))
    try:
        built = build_audience_package(brief, panel, stage_root / "package")
        raw, validation, files = _package_for_source(
            {"source": "file", "package_path": str(built.package_zip_path)}, None
        )
        result = _resolution_payload(panel, validation, "ready", [])
        _materialize_snapshot(run_dir, raw, files, result)
        return result
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _package_for_source(
    source: Mapping[str, Any], library_root: Path | str | None
) -> tuple[bytes, dict[str, Any], dict[str, bytes]]:
    intake = validate_audience_intake({"audience_panel": source})["value"]
    if intake["source"] == "library":
        package_path = find_package(
            intake["panel_id"], intake["version"], library_root=library_root
        )
    else:
        package_path = Path(intake["package_path"]).expanduser()
        if not package_path.is_absolute():
            raise LibrarySafetyError("portable package path must be absolute")
        _reject_symlink_components(package_path, label="portable package path")
        if package_path.is_symlink() or not package_path.is_file():
            raise LibrarySafetyError("portable package must be a real ZIP file")
    try:
        raw = _archive_bytes(package_path)
        validation = validate_package_archive(raw)
        files = _safe_read_package_archive(raw)
    except PackageSafetyError as exc:
        raise LibrarySafetyError(str(exc)) from exc
    return raw, validation, files


def _compare_scope(
    panel: Mapping[str, Any], study_scope: Mapping[str, Any], *,
    now: datetime, explicit_refresh_triggers: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    saved = panel["audience_scope"]
    reasons: list[dict[str, Any]] = []
    status = "ready"
    for field in ("audience", "category"):
        if _normalized_scope_text(study_scope[field]) != _normalized_scope_text(saved[field]):
            status = "incompatible"
            reasons.append(_reason(
                f"{field}_mismatch", field, saved[field], study_scope[field],
                f"The saved panel {field.replace('_', ' ')} does not match this study.",
            ))
    for field in ("market", "geography", "buying_context"):
        if _normalized_scope_text(study_scope[field]) != _normalized_scope_text(saved[field]):
            if status != "incompatible":
                status = "needs_refresh"
            reasons.append(_reason(
                f"{field}_mismatch", field, saved[field], study_scope[field],
                f"The saved panel {field.replace('_', ' ')} needs to be refreshed for this study.",
            ))
    saved_exclusions = [
        _normalized_scope_text(item) for item in saved["exclusions"]
    ]
    study_exclusions = [
        _normalized_scope_text(item) for item in study_scope["exclusions"]
    ]
    if study_exclusions != saved_exclusions:
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(_reason(
            "exclusions_mismatch", "exclusions", saved["exclusions"],
            study_scope["exclusions"],
            "The saved panel exclusions changed and the audience needs to be refreshed.",
        ))
    refresh = panel["refresh_conditions"]
    review_after = _parse_timestamp(refresh["review_after"], "refresh_conditions.review_after")
    if now > review_after:
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(_reason(
            "review_after_elapsed", "refresh_conditions.review_after",
            refresh["review_after"], now.isoformat().replace("+00:00", "Z"),
            "The saved panel has passed its scheduled research review date.",
        ))
    panel_updated = _parse_timestamp(panel["updated_at"], "updated_at")
    max_age_days = refresh["max_age_days"]
    if now > panel_updated + timedelta(days=max_age_days):
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(_reason(
            "max_age_elapsed", "refresh_conditions.max_age_days", max_age_days,
            (now - panel_updated).total_seconds() / 86400,
            "The saved panel exceeded its maximum allowed age.",
        ))
    saved_triggers = {
        _normalized_scope_text(trigger): trigger for trigger in refresh["triggers"]
    }
    for trigger in explicit_refresh_triggers:
        normalized = _normalized_scope_text(trigger)
        if normalized in saved_triggers:
            if status != "incompatible":
                status = "needs_refresh"
            reasons.append(_reason(
                "refresh_trigger_present", "refresh_conditions.triggers",
                saved_triggers[normalized], trigger,
                "A saved research-refresh trigger is present in this study.",
            ))
    reasons.sort(key=lambda item: (item["field"], item["code"]))
    return status, reasons


def _snapshot_matches(snapshot: Path, files: Mapping[str, bytes], raw: bytes) -> bool:
    expected = dict(files)
    expected["audience-panel-package.zip"] = raw
    try:
        children = list(snapshot.iterdir())
    except OSError:
        return False
    if {child.name for child in children} != set(expected):
        return False
    return all(
        child.is_file() and not child.is_symlink() and child.read_bytes() == expected[child.name]
        for child in children
    )


def _materialize_snapshot(
    run_dir: Path | str, raw: bytes, files: Mapping[str, bytes], result: Mapping[str, Any]
) -> None:
    run = Path(run_dir).expanduser()
    if not run.is_absolute():
        raise LibrarySafetyError("run directory must be absolute")
    _reject_symlink_components(run, label="run directory")
    audience_dir = run / "audience"
    _reject_symlink_components(audience_dir, label="run audience directory")
    if audience_dir.is_symlink():
        raise LibrarySafetyError("run audience directory must not be a symlink")
    snapshot = audience_dir / "snapshot"
    if snapshot.exists() or snapshot.is_symlink():
        if snapshot.is_symlink() or not snapshot.is_dir() or not _snapshot_matches(snapshot, files, raw):
            raise ImmutableVersionConflict("run audience snapshot already exists with different bytes")
    else:
        _private_mkdir(run)
        _private_mkdir(audience_dir)
        stage = Path(tempfile.mkdtemp(prefix=".resolve-", dir=audience_dir))
        os.chmod(stage, 0o700)
        extracted = stage / "snapshot"
        try:
            _safe_extract_package_archive(raw, extracted, allowed_root=stage)
            _atomic_write(extracted / "audience-panel-package.zip", raw)
            os.replace(extracted, snapshot)
            os.chmod(snapshot, 0o700)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    resolution_path = audience_dir / "resolution.json"
    _reject_symlink_components(resolution_path, label="audience resolution path")
    if resolution_path.is_symlink():
        raise LibrarySafetyError("audience resolution path must not be a symlink")
    _atomic_write(resolution_path, _canonical_json(result))


def resolve_audience_panel(
    source: Mapping[str, Any], study_scope: Mapping[str, Any], *, run_dir: Path | str,
    library_root: Path | str | None = None,
    explicit_refresh_triggers: list[str] | tuple[str, ...] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve one immutable v2 panel and bind it to a run-local snapshot."""

    scope = _validate_scope_input(study_scope, "study_scope")
    triggers = _require_string_array(list(explicit_refresh_triggers), "explicit_refresh_triggers")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(timezone.utc)
    raw, validation, files = _package_for_source(source, library_root)
    panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    status, reasons = _compare_scope(
        panel, scope, now=current, explicit_refresh_triggers=triggers
    )
    if panel["persona_research"]["status"] != "approved" or panel["persona_research"]["source_state"] == "no_research_sources":
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(_reason(
            "provisional_requires_research_refresh", "persona_research.status",
            "approved", panel["persona_research"]["status"],
            "A provisional package cannot be reused until its research is refreshed and approved.",
        ))
        reasons.sort(key=lambda item: (item["field"], item["code"]))
    result = _resolution_payload(panel, validation, status, reasons)
    if status != "ready":
        raise AudienceResolutionBlocked(result)
    _materialize_snapshot(run_dir, raw, files, result)
    return result


def require_ready_audience_resolution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RESOLVER_KEYS:
        raise ValueError("audience resolution keys do not match the allowlist")
    if value.get("schema_version") != RESOLUTION_SCHEMA_VERSION or value.get("status") != "ready":
        raise ValueError("audience resolution is not ready")
    if value.get("snapshot_dir") != SNAPSHOT_RELATIVE_PATH:
        raise ValueError("audience resolution snapshot path is not canonical")
    if not isinstance(value.get("hashes"), dict) or set(value["hashes"]) != RESOLVER_HASH_KEYS:
        raise ValueError("audience resolution hashes do not match the allowlist")
    if not isinstance(value.get("audience_lock"), dict) or set(value["audience_lock"]) != AUDIENCE_LOCK_KEYS:
        raise ValueError("audience resolution lock does not match the allowlist")
    return copy_json(value)


def load_audience_resolution(path: Path | str) -> dict[str, Any]:
    resolution_path = Path(path).expanduser()
    if not resolution_path.is_absolute():
        raise LibrarySafetyError("audience resolution path must be absolute")
    _reject_symlink_components(resolution_path, label="audience resolution path")
    if resolution_path.is_symlink() or not resolution_path.is_file():
        raise LibrarySafetyError("audience resolution must be a real file")
    value = require_ready_audience_resolution(
        json.loads(resolution_path.read_text(encoding="utf-8"))
    )
    if resolution_path.name != "resolution.json" or resolution_path.parent.name != "audience":
        raise LibrarySafetyError(
            "audience resolution must use the canonical run-relative audience/resolution.json path"
        )
    run_dir = resolution_path.parent.parent
    audience_package_binding(run_dir, value)
    return value


def load_reusable_audience_resolution(path: Path | str) -> dict[str, Any]:
    """Load a research-backed resolution eligible for library or file reuse."""

    value = load_audience_resolution(path)
    resolution_path = Path(path)
    package_path = (
        resolution_path.parent / "snapshot" / "audience-panel-package.zip"
    )
    files = _safe_read_package_archive(_archive_bytes(package_path))
    panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    persona_research = panel.get("persona_research", {})
    if (
        persona_research.get("status") != "approved"
        or persona_research.get("source_state") != "documented_sources"
    ):
        raise LibrarySafetyError(
            "provisional audience snapshots are run-local and cannot be reused through audience_panel"
        )
    return value


def audience_package_binding(run_dir: Path | str, resolution: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(resolution, Mapping) or set(resolution) != RESOLVER_KEYS:
        raise ValueError("audience resolution keys do not match the allowlist")
    if resolution.get("status") != "ready" or resolution.get("snapshot_dir") != SNAPSHOT_RELATIVE_PATH:
        raise ValueError("only a ready canonical resolution can bind a manifest")
    run = Path(run_dir).expanduser()
    if not run.is_absolute():
        raise LibrarySafetyError("run directory must be absolute")
    _reject_symlink_components(run, label="run directory")
    snapshot = run / "audience" / "snapshot"
    _reject_symlink_components(snapshot, label="resolved audience snapshot")
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise LibrarySafetyError("resolved audience snapshot is missing or unsafe")
    package_path = snapshot / "audience-panel-package.zip"
    raw = _archive_bytes(package_path)
    validation = validate_package_archive(raw)
    files = _safe_read_package_archive(raw)
    if not _snapshot_matches(snapshot, files, raw):
        raise LibrarySafetyError(
            "resolved audience snapshot loose files do not match the validated ZIP"
        )
    if any(validation[key] != resolution["hashes"][key] for key in RESOLVER_HASH_KEYS):
        raise LibrarySafetyError("resolved audience snapshot hashes do not match the resolution")
    panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    expected_resolution = _resolution_payload(panel, validation, "ready", [])
    if dict(resolution) != expected_resolution:
        raise LibrarySafetyError(
            "audience resolution content does not match the immutable panel snapshot"
        )
    binding = {
        "panel_id": validation["panel_id"],
        "panel_version": validation["panel_version"],
        "panel_sha256": validation["panel_sha256"],
        "panel_byte_count": len(files["saved-audience-panel.json"]),
        "brief_id": validation["brief_id"],
        "brief_sha256": validation["brief_sha256"],
        "brief_byte_count": len(files["persona-research-brief.json"]),
        "package_manifest_sha256": validation["package_manifest_sha256"],
        "package_manifest_byte_count": validation["package_manifest_byte_count"],
        "package_zip_sha256": validation["package_zip_sha256"],
        "package_zip_byte_count": validation["package_zip_byte_count"],
        "resolved_snapshot_path": SNAPSHOT_RELATIVE_PATH,
    }
    if set(binding) != AUDIENCE_PACKAGE_BINDING_KEYS:
        raise AssertionError("audience package binding drifted from its allowlist")
    return binding


def verify_file_package_binding(
    package_path: Path | str, expected_binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify that the exact requested portable ZIP matches a resolved binding."""

    raw, validation, files = _package_for_source(
        {"source": "file", "package_path": str(package_path)}, None
    )
    actual = {
        "panel_id": validation["panel_id"],
        "panel_version": validation["panel_version"],
        "panel_sha256": validation["panel_sha256"],
        "panel_byte_count": len(files["saved-audience-panel.json"]),
        "brief_id": validation["brief_id"],
        "brief_sha256": validation["brief_sha256"],
        "brief_byte_count": len(files["persona-research-brief.json"]),
        "package_manifest_sha256": validation["package_manifest_sha256"],
        "package_manifest_byte_count": validation["package_manifest_byte_count"],
        "package_zip_sha256": validation["package_zip_sha256"],
        "package_zip_byte_count": len(raw),
        "resolved_snapshot_path": SNAPSHOT_RELATIVE_PATH,
    }
    if not isinstance(expected_binding, Mapping) or dict(expected_binding) != actual:
        raise LibrarySafetyError(
            "requested portable ZIP does not match the complete resolved audience binding"
        )
    return actual


__all__ = [
    "AudienceResolutionBlocked", "ImmutableVersionConflict", "LibraryError", "LibraryLock", "LibraryLockError",
    "LibraryNotFoundError", "LibrarySafetyError", "find_package", "lookup_package", "list_panels",
    "audience_package_binding", "load_audience_resolution", "load_reusable_audience_resolution",
    "materialize_provisional_audience",
    "register_package",
    "require_ready_audience_resolution",
    "resolve_audience_panel", "resolve_library_root", "show_panel", "validate_audience_intake",
    "verify_file_package_binding",
]
