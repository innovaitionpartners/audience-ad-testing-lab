from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Iterator

from .common import canonical_json_bytes, sha256_bytes


_APPROVED_OPERATIONS = frozenset(
    {"prepare_study", "import_results", "validate_study", "recover_study"}
)
_APPROVED_REPOSITORY = "innovaitionpartners/audience-ad-testing-lab"
_RELEASE_MANIFEST_RELATIVE = PurePosixPath(
    "skills/real-world-outcome-data-prep/references/"
    "runtime-release-manifest.json"
)
_MAX_RELEASE_MANIFEST_BYTES = 1_048_576
_EXCLUDED_ROOTS = frozenset(
    {
        PurePosixPath(".git"),
        PurePosixPath("tmp"),
        PurePosixPath("tests/output"),
        PurePosixPath("tests/runs"),
    }
)
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".cache",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "env",
        "venv",
        ".superpowers",
        ".gemini_security",
    }
)
_EXCLUDED_FILENAMES = frozenset({".DS_Store", ".env", ".env.local"})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "release_version",
        "files",
        "release_tree_sha256",
    }
)
_IDENTITY_FIELDS = (
    "schema_version",
    "repository",
    "release_version",
    "files",
)
_APPROVED_ORIGINS = frozenset(
    {
        "https://github.com/innovaitionpartners/audience-ad-testing-lab",
        "https://github.com/innovaitionpartners/audience-ad-testing-lab.git",
        "git@github.com:innovaitionpartners/audience-ad-testing-lab",
        "git@github.com:innovaitionpartners/audience-ad-testing-lab.git",
        "ssh://git@github.com/innovaitionpartners/audience-ad-testing-lab",
        "ssh://git@github.com/innovaitionpartners/audience-ad-testing-lab.git",
    }
)

RUNTIME_IDENTITY_NOTICE = (
    "The co-shipped release manifest detects stale or modified operational bytes; "
    "it is not a cryptographic software-signing authority."
)


class RuntimeGuardError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    plugin_root: Path
    repository: str
    release_version: str
    release_tree_sha256: str


@dataclass
class _TrustedDirectory:
    relative: PurePosixPath | None
    name: str | None
    descriptor: int
    identity: tuple[int, ...]
    parent: _TrustedDirectory | None
    entries: dict[str, tuple[str, tuple[int, ...]]]


@dataclass(frozen=True)
class _TrustedFile:
    relative: PurePosixPath
    name: str
    identity: tuple[int, ...]
    parent: _TrustedDirectory


@dataclass
class _ClosedRuntimeInventory:
    root_device: int
    explicit_exclusions: frozenset[PurePosixPath]
    directories: list[_TrustedDirectory]
    files: dict[str, _TrustedFile]

    def close(self) -> None:
        for directory in reversed(self.directories):
            try:
                os.close(directory.descriptor)
            except OSError:
                pass
        self.directories.clear()


def _duplicate_rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeGuardError(f"release manifest has duplicate field: {key}")
        result[key] = value
    return result


def _parse_release_manifest_bytes(payload: bytes) -> dict[str, object]:
    try:
        raw = payload.decode("utf-8")
        value = json.loads(raw, object_pairs_hook=_duplicate_rejecting_object)
    except RuntimeGuardError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeGuardError("release manifest could not be loaded") from exc
    if not isinstance(value, dict):
        raise RuntimeGuardError("release manifest must be an object")
    return value


def load_release_manifest(path: Path) -> dict[str, object]:
    """Load an explicit caller-supplied manifest for bounded internal APIs."""

    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise RuntimeGuardError("release manifest could not be loaded") from exc
    return _parse_release_manifest_bytes(payload)


def _require_exact_fields(manifest: Mapping[str, object]) -> None:
    observed = set(manifest)
    if any(not isinstance(field, str) for field in observed):
        raise RuntimeGuardError("release manifest field names must be strings")
    unknown = sorted(observed - _MANIFEST_FIELDS)
    missing = sorted(_MANIFEST_FIELDS - observed)
    if unknown:
        raise RuntimeGuardError(
            "release manifest has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise RuntimeGuardError(
            "release manifest is missing fields: " + ", ".join(missing)
        )


def _require_manifest_string(
    manifest: Mapping[str, object], field: str
) -> str:
    value = manifest[field]
    if not isinstance(value, str) or not value:
        raise RuntimeGuardError(f"release manifest {field} must be a non-empty string")
    return value


def _validated_file_hashes(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise RuntimeGuardError("release manifest files must be a non-empty object")
    result: dict[str, str] = {}
    for raw_path, raw_digest in value.items():
        if not isinstance(raw_path, str):
            raise RuntimeGuardError("release file path must be a string")
        relative = PurePosixPath(raw_path)
        if (
            not raw_path
            or relative.is_absolute()
            or raw_path != relative.as_posix()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise RuntimeGuardError("release file path must be canonical and relative")
        if not isinstance(raw_digest, str) or re.fullmatch(
            r"[0-9a-f]{64}", raw_digest
        ) is None:
            raise RuntimeGuardError(
                f"release file hash must be lowercase SHA-256 hex: {raw_path}"
            )
        result[raw_path] = raw_digest
    return result


def _validate_manifest(manifest: object) -> tuple[dict[str, object], dict[str, str]]:
    if not isinstance(manifest, Mapping):
        raise RuntimeGuardError("release manifest must be an object")
    _require_exact_fields(manifest)
    schema_version = _require_manifest_string(manifest, "schema_version")
    if schema_version != "outcome-prep-runtime-release-v2":
        raise RuntimeGuardError("release manifest schema_version is unsupported")
    repository = _require_manifest_string(manifest, "repository")
    if repository != _APPROVED_REPOSITORY:
        raise RuntimeGuardError("release manifest repository is not approved")
    _require_manifest_string(manifest, "release_version")
    files = _validated_file_hashes(manifest["files"])
    release_tree_sha256 = _require_manifest_string(
        manifest, "release_tree_sha256"
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", release_tree_sha256) is None:
        raise RuntimeGuardError("release manifest release_tree_sha256 is invalid")

    identity = {field: manifest[field] for field in _IDENTITY_FIELDS}
    expected_tree = sha256_bytes(canonical_json_bytes(identity))
    if release_tree_sha256 != expected_tree:
        raise RuntimeGuardError("release tree identity does not match the manifest")
    return dict(manifest), files


def _validated_inventory_path(
    value: PurePosixPath, *, label: str
) -> PurePosixPath:
    if (
        value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
    ):
        raise RuntimeGuardError(f"{label} must be one canonical relative POSIX path")
    return value


def _is_beneath_or_equal(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def _is_superpowers_path(part: str) -> bool:
    return (
        part == "superpowers"
        or (len(part) == 11 and part.startswith("su") and part.endswith("erpowers"))
    )


def _is_excluded_runtime_path(path: PurePosixPath) -> bool:
    relative = _validated_inventory_path(path, label="runtime path")
    if any(_is_beneath_or_equal(relative, root) for root in _EXCLUDED_ROOTS):
        return True
    if any(
        part in _EXCLUDED_DIRECTORY_NAMES or _is_superpowers_path(part)
        for part in relative.parts[:-1]
    ):
        return True
    if relative.name in _EXCLUDED_DIRECTORY_NAMES or _is_superpowers_path(relative.name):
        return True
    if relative.name in _EXCLUDED_FILENAMES:
        return True
    return relative.suffix in _EXCLUDED_SUFFIXES



def _absolute_runtime_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _node_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeGuardError(
            "closed runtime inventory requires directory no-follow support"
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_open_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeGuardError(
            "closed runtime inventory requires file no-follow support"
        )
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _relative_child(
    directory: _TrustedDirectory, name: str
) -> PurePosixPath:
    if directory.relative is None:
        return PurePosixPath(name)
    return directory.relative / name


def _directory_names(directory: _TrustedDirectory) -> list[str]:
    try:
        names = os.listdir(directory.descriptor)
    except OSError as exc:
        raise RuntimeGuardError(
            "closed runtime inventory directory could not be enumerated"
        ) from exc
    if any(not isinstance(name, str) or not name or "/" in name for name in names):
        raise RuntimeGuardError(
            "closed runtime inventory returned a non-canonical child name"
        )
    if len(names) != len(set(names)):
        raise RuntimeGuardError("closed runtime inventory contains duplicate names")
    return sorted(names)


def _stat_child(directory: _TrustedDirectory, name: str) -> os.stat_result:
    try:
        return os.stat(
            name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise RuntimeGuardError(
            "closed runtime inventory child disappeared or is unreadable"
        ) from exc


def _open_root_directory(path: Path) -> tuple[int, tuple[int, ...]]:
    root = _absolute_runtime_path(path)
    try:
        before = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeGuardError("closed runtime inventory root is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise RuntimeGuardError(
            "closed runtime inventory root must be a non-symlink directory"
        )
    try:
        descriptor = os.open(root, _directory_open_flags())
    except OSError as exc:
        raise RuntimeGuardError(
            "closed runtime inventory root could not be opened"
        ) from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        try:
            os.close(descriptor)
        finally:
            raise RuntimeGuardError(
                "closed runtime inventory root could not be authenticated"
            ) from exc
    if _node_identity(before) != _node_identity(opened):
        os.close(descriptor)
        raise RuntimeGuardError("closed runtime inventory root changed while opening")
    return descriptor, _node_identity(opened)


def _open_child_directory(
    *,
    inventory: _ClosedRuntimeInventory,
    parent: _TrustedDirectory,
    name: str,
    relative: PurePosixPath,
    before: os.stat_result,
) -> _TrustedDirectory:
    try:
        descriptor = os.open(
            name,
            _directory_open_flags(),
            dir_fd=parent.descriptor,
        )
    except OSError as exc:
        raise RuntimeGuardError(
            f"closed runtime inventory directory changed while opening: {relative}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        try:
            os.close(descriptor)
        finally:
            raise RuntimeGuardError(
                f"closed runtime inventory directory could not be authenticated: "
                f"{relative}"
            ) from exc
    identity = _node_identity(opened)
    if (
        _node_identity(before) != identity
        or not stat.S_ISDIR(opened.st_mode)
        or opened.st_dev != inventory.root_device
    ):
        os.close(descriptor)
        raise RuntimeGuardError(
            f"closed runtime inventory directory identity changed: {relative}"
        )
    directory = _TrustedDirectory(
        relative=relative,
        name=name,
        descriptor=descriptor,
        identity=identity,
        parent=parent,
        entries={},
    )
    inventory.directories.append(directory)
    return directory


def _inventory_directory(
    inventory: _ClosedRuntimeInventory,
    directory: _TrustedDirectory,
) -> None:
    for name in _directory_names(directory):
        relative = _relative_child(directory, name)
        if (
            relative in inventory.explicit_exclusions
            or _is_excluded_runtime_path(relative)
        ):
            continue
        before = _stat_child(directory, name)
        identity = _node_identity(before)
        if stat.S_ISLNK(before.st_mode):
            raise RuntimeGuardError(
                f"closed runtime inventory contains a symlink: {relative}"
            )
        if stat.S_ISDIR(before.st_mode):
            child = _open_child_directory(
                inventory=inventory,
                parent=directory,
                name=name,
                relative=relative,
                before=before,
            )
            if name in directory.entries:
                raise RuntimeGuardError(
                    f"closed runtime inventory contains a duplicate: {relative}"
                )
            directory.entries[name] = ("directory", child.identity)
            _inventory_directory(inventory, child)
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_dev != inventory.root_device:
            raise RuntimeGuardError(
                f"closed runtime inventory contains a non-regular path: {relative}"
            )
        key = relative.as_posix()
        if key in inventory.files or name in directory.entries:
            raise RuntimeGuardError(
                f"closed runtime inventory contains a duplicate: {relative}"
            )
        trusted_file = _TrustedFile(
            relative=relative,
            name=name,
            identity=identity,
            parent=directory,
        )
        inventory.files[key] = trusted_file
        directory.entries[name] = ("file", identity)


def _build_closed_runtime_inventory(
    plugin_root: Path,
    *,
    excluded: set[PurePosixPath] | frozenset[PurePosixPath],
) -> _ClosedRuntimeInventory:
    explicit_exclusions = frozenset(
        _validated_inventory_path(path, label="excluded path") for path in excluded
    )
    root_descriptor, root_identity = _open_root_directory(plugin_root)
    root_directory = _TrustedDirectory(
        relative=None,
        name=None,
        descriptor=root_descriptor,
        identity=root_identity,
        parent=None,
        entries={},
    )
    inventory = _ClosedRuntimeInventory(
        root_device=root_identity[0],
        explicit_exclusions=explicit_exclusions,
        directories=[root_directory],
        files={},
    )
    try:
        _inventory_directory(inventory, root_directory)
        inventory.files = dict(sorted(inventory.files.items()))
        return inventory
    except BaseException:
        inventory.close()
        raise


@contextmanager
def closed_runtime_inventory(
    plugin_root: Path,
    *,
    excluded: set[PurePosixPath] | frozenset[PurePosixPath] = frozenset(),
) -> Iterator[_ClosedRuntimeInventory]:
    """Retain one descriptor-bound closed runtime inventory for hashing."""

    inventory = _build_closed_runtime_inventory(
        plugin_root,
        excluded=excluded,
    )
    try:
        yield inventory
    finally:
        inventory.close()


def _open_manifest_parent_inventory(
    plugin_root: Path,
) -> tuple[_ClosedRuntimeInventory, _TrustedDirectory]:
    root_descriptor, root_identity = _open_root_directory(plugin_root)
    root_directory = _TrustedDirectory(
        relative=None,
        name=None,
        descriptor=root_descriptor,
        identity=root_identity,
        parent=None,
        entries={},
    )
    inventory = _ClosedRuntimeInventory(
        root_device=root_identity[0],
        explicit_exclusions=frozenset({_RELEASE_MANIFEST_RELATIVE}),
        directories=[root_directory],
        files={},
    )
    current = root_directory
    try:
        for name in _RELEASE_MANIFEST_RELATIVE.parts[:-1]:
            relative = _relative_child(current, name)
            before = _stat_child(current, name)
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISDIR(before.st_mode)
                or before.st_dev != inventory.root_device
            ):
                raise RuntimeGuardError(
                    f"co-shipped release manifest parent is not trusted: {relative}"
                )
            current = _open_child_directory(
                inventory=inventory,
                parent=current,
                name=name,
                relative=relative,
                before=before,
            )
        return inventory, current
    except BaseException:
        inventory.close()
        raise


def _recheck_manifest_parent_inventory(
    inventory: _ClosedRuntimeInventory,
) -> None:
    for directory in inventory.directories:
        try:
            opened = os.fstat(directory.descriptor)
        except OSError as exc:
            raise RuntimeGuardError(
                "co-shipped release manifest parent descriptor became invalid"
            ) from exc
        if _node_identity(opened) != directory.identity:
            raise RuntimeGuardError(
                "co-shipped release manifest parent descriptor changed"
            )
        if directory.parent is None:
            continue
        linked = _stat_child(directory.parent, str(directory.name))
        if (
            stat.S_ISLNK(linked.st_mode)
            or not stat.S_ISDIR(linked.st_mode)
            or linked.st_dev != inventory.root_device
            or _node_identity(linked) != directory.identity
        ):
            raise RuntimeGuardError(
                "co-shipped release manifest parent linkage changed"
            )


def _read_co_shipped_release_manifest_bytes(plugin_root: Path) -> bytes:
    inventory, parent = _open_manifest_parent_inventory(plugin_root)
    descriptor = -1
    try:
        _recheck_manifest_parent_inventory(inventory)
        manifest_name = _RELEASE_MANIFEST_RELATIVE.name
        before = _stat_child(parent, manifest_name)
        before_identity = _node_identity(before)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_dev != inventory.root_device
        ):
            raise RuntimeGuardError(
                "co-shipped release manifest must be a same-device regular file"
            )
        if before.st_size > _MAX_RELEASE_MANIFEST_BYTES:
            raise RuntimeGuardError("co-shipped release manifest exceeds the byte limit")
        try:
            descriptor = os.open(
                manifest_name,
                _file_open_flags(),
                dir_fd=parent.descriptor,
            )
        except OSError as exc:
            raise RuntimeGuardError(
                "co-shipped release manifest changed while opening"
            ) from exc
        try:
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise RuntimeGuardError(
                "co-shipped release manifest could not be authenticated"
            ) from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != inventory.root_device
            or _node_identity(opened) != before_identity
        ):
            raise RuntimeGuardError(
                "co-shipped release manifest identity changed while opening"
            )

        chunks: list[bytes] = []
        observed_length = 0
        while True:
            remaining = _MAX_RELEASE_MANIFEST_BYTES - observed_length + 1
            chunk = os.read(descriptor, min(1_048_576, remaining))
            if not chunk:
                break
            observed_length += len(chunk)
            if observed_length > _MAX_RELEASE_MANIFEST_BYTES:
                raise RuntimeGuardError(
                    "co-shipped release manifest exceeds the byte limit"
                )
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            _node_identity(after) != before_identity
            or observed_length != after.st_size
        ):
            raise RuntimeGuardError(
                "co-shipped release manifest changed while reading"
            )
        linked = _stat_child(parent, manifest_name)
        if (
            stat.S_ISLNK(linked.st_mode)
            or not stat.S_ISREG(linked.st_mode)
            or _node_identity(linked) != before_identity
        ):
            raise RuntimeGuardError(
                "co-shipped release manifest linkage changed while reading"
            )
        _recheck_manifest_parent_inventory(inventory)
        return b"".join(chunks)
    except OSError as exc:
        raise RuntimeGuardError(
            "co-shipped release manifest could not be read"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        inventory.close()


def load_co_shipped_release_manifest(plugin_root: Path) -> dict[str, object]:
    """Load the self-excluded manifest through retained trusted descriptors."""

    return _parse_release_manifest_bytes(
        _read_co_shipped_release_manifest_bytes(plugin_root)
    )


def _configured_origins(plugin_root: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(plugin_root),
                "config",
                "--local",
                "--get-all",
                "remote.origin.url",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeGuardError("runtime git origin could not be verified") from exc
    origins = tuple(result.stdout.splitlines())
    if (
        result.returncode != 0
        or not origins
        or any(origin not in _APPROVED_ORIGINS for origin in origins)
    ):
        raise RuntimeGuardError(
            "runtime git origin is not innovaitionpartners/audience-ad-testing-lab"
        )
    return origins


def _observed_directory_entries(
    inventory: _ClosedRuntimeInventory,
    directory: _TrustedDirectory,
) -> dict[str, tuple[str, tuple[int, ...]]]:
    observed: dict[str, tuple[str, tuple[int, ...]]] = {}
    for name in _directory_names(directory):
        relative = _relative_child(directory, name)
        if (
            relative in inventory.explicit_exclusions
            or _is_excluded_runtime_path(relative)
        ):
            continue
        info = _stat_child(directory, name)
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeGuardError(
                f"closed runtime inventory changed to a symlink: {relative}"
            )
        if stat.S_ISDIR(info.st_mode):
            kind = "directory"
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
        else:
            raise RuntimeGuardError(
                f"closed runtime inventory changed to a non-regular path: {relative}"
            )
        if info.st_dev != inventory.root_device:
            raise RuntimeGuardError(
                f"closed runtime inventory crossed the root device: {relative}"
            )
        if name in observed:
            raise RuntimeGuardError(
                f"closed runtime inventory contains a duplicate: {relative}"
            )
        observed[name] = (kind, _node_identity(info))
    return observed


def _recheck_directory(
    inventory: _ClosedRuntimeInventory,
    directory: _TrustedDirectory,
) -> None:
    try:
        opened = os.fstat(directory.descriptor)
    except OSError as exc:
        raise RuntimeGuardError(
            "closed runtime inventory directory descriptor became invalid"
        ) from exc
    if _node_identity(opened) != directory.identity:
        raise RuntimeGuardError(
            "closed runtime inventory directory descriptor changed identity"
        )
    if directory.parent is not None:
        linked = _stat_child(directory.parent, str(directory.name))
        if (
            stat.S_ISLNK(linked.st_mode)
            or not stat.S_ISDIR(linked.st_mode)
            or _node_identity(linked) != directory.identity
        ):
            raise RuntimeGuardError(
                "closed runtime inventory parent directory linkage changed"
            )
    if _observed_directory_entries(inventory, directory) != directory.entries:
        raise RuntimeGuardError("closed runtime inventory directory entries changed")


def _recheck_directory_chain(
    inventory: _ClosedRuntimeInventory,
    directory: _TrustedDirectory,
) -> None:
    chain: list[_TrustedDirectory] = []
    current: _TrustedDirectory | None = directory
    while current is not None:
        chain.append(current)
        current = current.parent
    for current in reversed(chain):
        _recheck_directory(inventory, current)


def _hash_release_file(
    trusted_file: _TrustedFile,
    *,
    inventory: _ClosedRuntimeInventory,
) -> str:
    _recheck_directory_chain(inventory, trusted_file.parent)
    before = _stat_child(trusted_file.parent, trusted_file.name)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_dev != inventory.root_device
        or _node_identity(before) != trusted_file.identity
    ):
        raise RuntimeGuardError(
            f"closed runtime inventory file identity changed: {trusted_file.relative}"
        )
    try:
        descriptor = os.open(
            trusted_file.name,
            _file_open_flags(),
            dir_fd=trusted_file.parent.descriptor,
        )
    except OSError as exc:
        raise RuntimeGuardError(
            f"closed runtime inventory file changed while opening: "
            f"{trusted_file.relative}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != inventory.root_device
            or _node_identity(opened) != trusted_file.identity
        ):
            raise RuntimeGuardError(
                f"closed runtime inventory opened file identity changed: "
                f"{trusted_file.relative}"
            )
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            length += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _node_identity(after) != trusted_file.identity or length != after.st_size:
            raise RuntimeGuardError(
                f"closed runtime inventory file changed while hashing: "
                f"{trusted_file.relative}"
            )
    except OSError as exc:
        raise RuntimeGuardError(
            f"closed runtime inventory file could not be hashed: "
            f"{trusted_file.relative}"
        ) from exc
    finally:
        os.close(descriptor)
    _recheck_directory_chain(inventory, trusted_file.parent)
    return digest.hexdigest()


def hash_closed_runtime_tree(
    plugin_root: Path,
    *,
    excluded: set[PurePosixPath] | frozenset[PurePosixPath] = frozenset(),
    expected_paths: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Inventory and hash one runtime through retained trusted descriptors."""

    with closed_runtime_inventory(plugin_root, excluded=excluded) as inventory:
        live_paths = set(inventory.files)
        if expected_paths is not None and live_paths != set(expected_paths):
            manifest_paths = set(expected_paths)
            raise RuntimeGuardError(
                "closed runtime inventory does not match the release manifest: "
                f"{len(live_paths - manifest_paths)} unlisted path(s), "
                f"{len(manifest_paths - live_paths)} missing path(s)"
            )
        hashes = {
            relative: _hash_release_file(trusted_file, inventory=inventory)
            for relative, trusted_file in inventory.files.items()
        }
        for directory in inventory.directories:
            _recheck_directory(inventory, directory)
        return hashes


def verify_runtime_identity(
    *,
    plugin_root: Path,
    release_manifest: object,
    operation: str,
) -> RuntimeIdentity:
    if not isinstance(operation, str) or operation not in _APPROVED_OPERATIONS:
        raise RuntimeGuardError("runtime operation is not approved")

    reported_root = Path(plugin_root).expanduser().resolve(strict=False)
    root = _absolute_runtime_path(plugin_root)
    if not root.is_dir():
        raise RuntimeGuardError("plugin runtime root is unavailable")

    manifest, files = _validate_manifest(release_manifest)
    git_metadata = root / ".git"
    if git_metadata.exists() or git_metadata.is_symlink():
        _configured_origins(root)

    live_hashes = hash_closed_runtime_tree(
        root,
        excluded={_RELEASE_MANIFEST_RELATIVE},
        expected_paths=set(files),
    )
    for relative_path, expected_digest in files.items():
        if live_hashes[relative_path] != expected_digest:
            raise RuntimeGuardError(
                f"release bytes do not match the manifest: {relative_path}"
            )

    return RuntimeIdentity(
        plugin_root=reported_root,
        repository=str(manifest["repository"]),
        release_version=str(manifest["release_version"]),
        release_tree_sha256=str(manifest["release_tree_sha256"]),
    )


def require_approved_runtime(operation: str) -> RuntimeIdentity:
    plugin_root = Path(__file__).resolve().parents[4]
    manifest = load_co_shipped_release_manifest(plugin_root)
    return verify_runtime_identity(
        plugin_root=plugin_root,
        release_manifest=manifest,
        operation=operation,
    )
