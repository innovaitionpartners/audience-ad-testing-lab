"""Seal one exact first-party producer closure and numerical runtime."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any

from ...common import canonical_json_bytes, sha256_json
from .evidence_errors import ProducerAuthenticationError, ProducerRuntimeUnavailable


ENTRY_POINT = "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
SCRIPTS_ROOT = Path("skills/audience-ad-testing-lab/scripts")
SURFACES = {
    "complete_exposure_ordering": (ENTRY_POINT, "screening"),
    "maxdiff_screening_ordering": (ENTRY_POINT, "screening"),
    "pairwise_boundary_ordering": (ENTRY_POINT, "boundary"),
}
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACCELERATE_RE = re.compile(
    r"^/System/Library/Frameworks/Accelerate\.framework/Versions/"
    r"[^/]+/Accelerate$"
)
_OTOOL_ROW_RE = re.compile(
    r"^\t(?P<install_name>[^()\r\n]+) "
    r"\(compatibility version (?P<compatibility>[^,()\r\n]+), "
    r"current version (?P<current>[^()\r\n]+)\)$"
)
_LINUX_VIRTUAL_RE = re.compile(
    r"^linux-vdso\.so\.(?P<version>[0-9]+)[ \t]+"
    r"\((?P<address>0x[0-9a-fA-F]+)\)$"
)
_LDD_RESOLVED_RE = re.compile(
    r"^(?P<path>/[^ \t\r\n]+)[ \t]+\((?P<address>0x[0-9a-fA-F]+)\)$"
)
_OPEN_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
_OPEN_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
)
_RUNTIME_PROBE_KEYS = frozenset({
    "python_implementation",
    "python_version",
    "numpy_version",
    "scipy_version",
    "platform_system",
    "platform_release",
    "machine",
    "extension_modules",
    "show_config",
})
_NUMPY_PROBE_KEYS = frozenset({"extension_modules", "show_config"})


class _FrozenList(list[str]):
    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("serialization constants are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


PRODUCER_RAW_SERIALIZATION = MappingProxyType({
    "encoding": "utf-8",
    "indent": 2,
    "sort_keys": True,
    "allow_nan": False,
    "ensure_ascii": True,
    "separators": None,
    "terminal_lf": True,
})
CANONICAL_DOCUMENT_SERIALIZATION = MappingProxyType({
    "encoding": "utf-8",
    "indent": None,
    "sort_keys": True,
    "allow_nan": False,
    "ensure_ascii": False,
    "separators": _FrozenList([",", ":"]),
    "terminal_lf": True,
})


REPLAY_BOOTSTRAP_SOURCE = """\
import builtins
import json
import os
import pathlib
import runpy
import sys

if len(sys.argv) < 5:
    raise SystemExit(2)
root, entry_point_relative, trace_fd_text, separator, *producer_args = sys.argv[1:]
if separator != "--":
    raise SystemExit(2)
root_path = pathlib.Path(root).resolve(strict=True)
if entry_point_relative != "aggregate-screening.py":
    raise SystemExit(2)
entry_path = (root_path / entry_point_relative).resolve(strict=True)
if entry_path.parent != root_path or entry_path.name != "aggregate-screening.py":
    raise SystemExit(2)
try:
    trace_fd = int(trace_fd_text)
except ValueError:
    raise SystemExit(2)
if trace_fd <= 2:
    raise SystemExit(2)
observed = {("__main__", entry_point_relative)}
last_module_count = -1

def record_imports(force=False):
    global last_module_count
    module_count = len(sys.modules)
    if not force and module_count == last_module_count:
        return
    last_module_count = module_count
    for name, module in tuple(sys.modules.items()):
        if name != "audience_lab" and not name.startswith("audience_lab."):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        try:
            relative = pathlib.Path(module_file).resolve(strict=True).relative_to(root_path)
        except (OSError, ValueError):
            continue
        if relative.suffix == ".py":
            observed.add((name, relative.as_posix()))

def import_originates_in_audience_lab(name, import_globals, level):
    if level == 0:
        return (
            isinstance(name, str)
            and (name == "audience_lab" or name.startswith("audience_lab."))
        )
    if level < 0 or not isinstance(import_globals, dict):
        return False
    package = import_globals.get("__package__")
    if not isinstance(package, str) or not package:
        spec = import_globals.get("__spec__")
        package = getattr(spec, "parent", None)
    if not isinstance(package, str) or not package:
        module_name = import_globals.get("__name__")
        if not isinstance(module_name, str):
            return False
        if "__path__" in import_globals:
            package = module_name
        else:
            package = module_name.rpartition(".")[0]
    return package == "audience_lab" or package.startswith("audience_lab.")

original_import = builtins.__import__
def traced_import(name, globals=None, locals=None, fromlist=(), level=0):
    force_capture = import_originates_in_audience_lab(name, globals, level)
    try:
        return original_import(name, globals, locals, fromlist, level)
    finally:
        record_imports(force=force_capture)

builtins.__import__ = traced_import
sys.path.insert(0, str(root_path))
sys.argv = [str(entry_path), *producer_args]
exit_code = 0
try:
    runpy.run_path(str(entry_path), run_name="__main__")
except SystemExit as exc:
    exit_code = exc.code if isinstance(exc.code, int) else 1
finally:
    builtins.__import__ = original_import
    record_imports(force=True)
    rows = [
        {"module": module, "path": path}
        for module, path in sorted(observed)
    ]
    trace = json.dumps(
        {"schema_version": "producer-import-trace-v1", "modules": rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\\n"
    os.write(trace_fd, trace)
raise SystemExit(exit_code)
"""


_RUNTIME_PROBE_SOURCE = """\
import importlib.machinery
import json
import os
from pathlib import Path
import platform
import runpy
import sys

scripts_root = Path(os.environ["AUDIENCE_C1_PROBE_SCRIPTS_ROOT"]).resolve(strict=True)
entry_point = Path(os.environ["AUDIENCE_C1_PROBE_ENTRY_POINT"]).resolve(strict=True)
entry_point.relative_to(scripts_root)
sys.path.insert(0, str(scripts_root))
runpy.run_path(str(entry_point), run_name="__audience_c1_probe__")
import numpy
import scipy

suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
extensions = []
for name, module in tuple(sys.modules.items()):
    if not (name == "numpy" or name.startswith("numpy.") or
            name == "scipy" or name.startswith("scipy.")):
        continue
    raw_path = getattr(module, "__file__", None)
    spec = getattr(module, "__spec__", None)
    loader = getattr(spec, "loader", None)
    if (not isinstance(loader, importlib.machinery.ExtensionFileLoader) or
            not isinstance(raw_path, str) or not raw_path.endswith(suffixes)):
        continue
    extensions.append({
        "distribution": name.split(".", 1)[0],
        "module": name,
        "path": str(Path(raw_path).resolve(strict=True)),
    })
payload = {
    "python_implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "numpy_version": numpy.__version__,
    "scipy_version": scipy.__version__,
    "platform_system": platform.system(),
    "platform_release": platform.release(),
    "machine": platform.machine(),
    "extension_modules": extensions,
    "show_config": numpy.show_config(mode="dicts"),
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                 allow_nan=False))
"""


@dataclass(frozen=True)
class ProducerSemanticsBundle:
    semantics: dict[str, object]
    staged_runtime_root: Path


def _authentication_failure(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ProducerAuthenticationError(message)
    raise ProducerAuthenticationError(message) from exc


def _runtime_failure(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ProducerRuntimeUnavailable(message)
    raise ProducerRuntimeUnavailable(message) from exc


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _validate_real_directory(path: Path, *, label: str) -> Path:
    absolute = path.absolute()
    try:
        path_stat = os.lstat(absolute)
    except OSError as exc:
        _authentication_failure(f"{label} is unavailable: {absolute}", exc)
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        _authentication_failure(f"{label} must be a real directory: {absolute}")
    resolved = absolute.resolve(strict=True)
    return resolved


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        _authentication_failure(f"source path must be a safe relative POSIX path: {value}")
    return path


def _read_stable_source(path: Path, root: Path) -> bytes:
    """Read source through descriptor-relative, no-follow component traversal."""
    root_path = Path(root).absolute()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(root_path)
    except ValueError:
        try:
            relative = candidate.resolve(strict=False).relative_to(
                root_path.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            _authentication_failure(
                f"source path escapes authenticated runtime root: {path}", exc
            )
    if any(part in {"", ".", "..", "__pycache__"} for part in relative.parts):
        _authentication_failure(f"source path is unsafe: {relative.as_posix()}")
    if path.suffix != ".py":
        _authentication_failure(f"first-party source must end in .py: {relative.as_posix()}")
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        root_fd = os.open(root_path, _OPEN_DIRECTORY_FLAGS)
        directory_fds.append(root_fd)
        root_before = os.fstat(root_fd)
        current_fd = root_fd
        for component in relative.parts[:-1]:
            next_fd = os.open(component, _OPEN_DIRECTORY_FLAGS, dir_fd=current_fd)
            component_stat = os.fstat(next_fd)
            if not stat.S_ISDIR(component_stat.st_mode):
                _authentication_failure(
                    f"source parent component is not a directory: {component}"
                )
            directory_fds.append(next_fd)
            current_fd = next_fd
        file_fd = os.open(relative.parts[-1], _OPEN_FILE_FLAGS, dir_fd=current_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            _authentication_failure(f"first-party source must be a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        try:
            verify_fd = os.open(
                relative.parts[-1], _OPEN_FILE_FLAGS, dir_fd=current_fd
            )
        except OSError as exc:
            _authentication_failure(f"first-party source changed during read: {path}", exc)
        try:
            path_after = os.fstat(verify_fd)
        finally:
            os.close(verify_fd)
        root_after = os.fstat(root_fd)
        root_path_after = os.stat(root_path, follow_symlinks=False)
        if (
            _stat_identity(before) != _stat_identity(after)
            or _stat_identity(before) != _stat_identity(path_after)
            or _stat_identity(root_before) != _stat_identity(root_after)
            or _stat_identity(root_before) != _stat_identity(root_path_after)
        ):
            _authentication_failure(f"first-party source changed during read: {path}")
        raw = b"".join(chunks)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            _authentication_failure(f"first-party source must be UTF-8: {path}", exc)
        return raw
    except OSError as exc:
        _authentication_failure(f"first-party source is unavailable or unsafe: {path}", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _module_name(relative_to_scripts: Path) -> tuple[str, bool]:
    if relative_to_scripts.name == "__init__.py":
        return ".".join(relative_to_scripts.parent.parts), True
    return ".".join(relative_to_scripts.with_suffix("").parts), False


def _module_source(scripts_root: Path, module_name: str) -> Path | None:
    if (
        not module_name
        or module_name.startswith(".")
        or any(not part or part in {".", ".."} for part in module_name.split("."))
    ):
        return None
    base = scripts_root.joinpath(*module_name.split("."))
    file_candidate = base.with_suffix(".py")
    package_candidate = base / "__init__.py"
    existing = [candidate for candidate in (file_candidate, package_candidate) if candidate.exists()]
    if len(existing) > 1:
        _authentication_failure(f"ambiguous first-party module: {module_name}")
    return existing[0] if existing else None


def _package_initializers(scripts_root: Path, source: Path) -> list[Path]:
    try:
        relative = source.relative_to(scripts_root)
    except ValueError as exc:
        _authentication_failure(f"first-party source escapes scripts root: {source}", exc)
    parents = relative.parent.parts
    result: list[Path] = []
    for index in range(1, len(parents) + 1):
        initializer = scripts_root.joinpath(*parents[:index], "__init__.py")
        if initializer.exists():
            result.append(initializer)
        elif index == 1 and parents[0] == "audience_lab":
            _authentication_failure(
                f"first-party package initializer is missing: "
                f"{initializer.relative_to(scripts_root)}"
            )
        elif result:
            _authentication_failure(
                f"nested first-party package initializer is missing: "
                f"{initializer.relative_to(scripts_root)}"
            )
    return result


def _resolve_imports(source: Path, scripts_root: Path, raw: bytes) -> set[Path]:
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=str(source))
    except SyntaxError as exc:
        _authentication_failure(f"first-party source has invalid Python syntax: {source}", exc)
    relative = source.relative_to(scripts_root)
    current_module, is_package = _module_name(relative)
    current_parts = current_module.split(".") if current_module else []
    package_parts = current_parts if is_package else current_parts[:-1]
    resolved: set[Path] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "__import__", "import_module", "exec", "eval", "compile"
        }:
            _authentication_failure(
                f"dynamic code/import reference is forbidden in sealed source: {relative}"
            )
        if isinstance(node, ast.Attribute) and node.attr in {
            "__import__", "import_module", "exec", "eval"
        }:
            _authentication_failure(
                f"dynamic code/import attribute is forbidden in sealed source: {relative}"
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in {"__import__", "import_module", "exec", "eval", "compile"}
        ):
            _authentication_failure(
                f"indirect dynamic code/import lookup is forbidden in sealed source: {relative}"
            )
        if isinstance(node, ast.Call):
            function = node.func
            if (
                isinstance(function, ast.Name)
                and function.id in {"__import__", "import_module"}
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr == "import_module"
            ):
                _authentication_failure(
                    f"dynamic imports are forbidden in sealed first-party source: {relative}"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib" or alias.name.startswith("importlib."):
                    _authentication_failure(
                        f"dynamic import library is forbidden in sealed source: {relative}"
                    )
                if alias.name == "audience_lab" or alias.name.startswith("audience_lab."):
                    imported = _module_source(scripts_root, alias.name)
                    if imported is None:
                        _authentication_failure(
                            f"unresolved first-party import {alias.name!r} in {relative}"
                        )
                    resolved.add(imported)
        elif isinstance(node, ast.ImportFrom):
            if any(
                alias.name in {
                    "__import__", "import_module", "exec", "eval", "compile"
                }
                for alias in node.names
            ):
                _authentication_failure(
                    f"dynamic code/import alias is forbidden in sealed source: {relative}"
                )
            if node.module == "importlib" or (
                isinstance(node.module, str) and node.module.startswith("importlib.")
            ):
                _authentication_failure(
                    f"dynamic import library is forbidden in sealed source: {relative}"
                )
            if node.level:
                remove_count = node.level - 1
                if remove_count > len(package_parts):
                    _authentication_failure(
                        f"relative import escapes first-party scripts root in {relative}"
                    )
                base_parts = package_parts[:len(package_parts) - remove_count]
                if node.module:
                    base_parts.extend(node.module.split("."))
                if not base_parts or base_parts[0] != "audience_lab":
                    _authentication_failure(
                        f"relative import escapes first-party package in {relative}"
                    )
                module = ".".join(base_parts)
                imported = _module_source(scripts_root, module)
                if imported is None:
                    _authentication_failure(
                        f"unresolved first-party import {module!r} in {relative}"
                    )
                resolved.add(imported)
                if imported.name == "__init__.py":
                    for alias in node.names:
                        candidate = _module_source(scripts_root, f"{module}.{alias.name}")
                        if candidate is not None:
                            resolved.add(candidate)
            elif node.module == "audience_lab" or (
                isinstance(node.module, str) and node.module.startswith("audience_lab.")
            ):
                imported = _module_source(scripts_root, node.module)
                if imported is None:
                    _authentication_failure(
                        f"unresolved first-party import {node.module!r} in {relative}"
                    )
                resolved.add(imported)
                if imported.name == "__init__.py":
                    for alias in node.names:
                        candidate = _module_source(
                            scripts_root, f"{node.module}.{alias.name}"
                        )
                        if candidate is not None:
                            resolved.add(candidate)
    return resolved


def _discover_dependency_closure(
    runtime_root: Path,
    entry_point: str = ENTRY_POINT,
) -> list[dict[str, object]]:
    """Return the exact path-sorted transitive first-party Python closure."""
    root = _validate_real_directory(Path(runtime_root), label="runtime_root")
    entry_relative = _safe_relative_path(entry_point)
    if entry_relative != Path(ENTRY_POINT):
        _authentication_failure(f"entry point is not allowlisted: {entry_point}")
    scripts_root = root / SCRIPTS_ROOT
    if not scripts_root.is_dir() or scripts_root.is_symlink():
        _authentication_failure(f"first-party scripts root is unavailable or unsafe: {scripts_root}")
    pending = [root / entry_relative]
    discovered: dict[Path, bytes] = {}
    while pending:
        source = pending.pop()
        try:
            relative = source.relative_to(root)
        except ValueError as exc:
            _authentication_failure(f"first-party source escapes runtime root: {source}", exc)
        if relative in discovered:
            continue
        raw = _read_stable_source(source, root)
        discovered[relative] = raw
        if source != root / entry_relative:
            for initializer in _package_initializers(scripts_root, source):
                initializer_relative = initializer.relative_to(root)
                if initializer_relative not in discovered:
                    pending.append(initializer)
        pending.extend(_resolve_imports(source, scripts_root, raw))
    return [
        {
            "path": relative.as_posix(),
            "byte_count": len(discovered[relative]),
            "raw_bytes_sha256": _sha256_bytes(discovered[relative]),
        }
        for relative in sorted(discovered, key=lambda item: item.as_posix())
    ]


def _prepare_stage(path: Path) -> Path:
    absolute = path.absolute()
    if absolute.exists() or absolute.is_symlink():
        try:
            path_stat = os.lstat(absolute)
        except OSError as exc:
            _authentication_failure(f"staged runtime root is unavailable: {absolute}", exc)
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
            _authentication_failure(f"staged runtime root must be a real directory: {absolute}")
        try:
            if any(absolute.iterdir()):
                _authentication_failure("staged runtime root must be empty")
        except OSError as exc:
            _authentication_failure(f"staged runtime root cannot be inspected: {absolute}", exc)
    else:
        try:
            absolute.mkdir(mode=0o700, parents=False)
        except OSError as exc:
            _authentication_failure(f"could not create staged runtime root: {absolute}", exc)
    try:
        os.chmod(absolute, 0o700)
    except OSError as exc:
        _authentication_failure(f"could not protect staged runtime root: {absolute}", exc)
    return absolute.resolve(strict=True)


def _stage_dependency_closure(
    runtime_root: Path,
    staged_runtime_root: Path,
    dependency_closure: Sequence[Mapping[str, object]],
) -> None:
    for row in dependency_closure:
        relative = _safe_relative_path(str(row["path"]))
        source = runtime_root / relative
        raw = _read_stable_source(source, runtime_root)
        if (
            len(raw) != row["byte_count"]
            or _sha256_bytes(raw) != row["raw_bytes_sha256"]
        ):
            _authentication_failure(f"first-party source mutated before staging: {relative}")
        destination = staged_runtime_root / relative
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                _authentication_failure(f"staged source path already exists: {relative}")
            file_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            try:
                offset = 0
                while offset < len(raw):
                    offset += os.write(file_fd, raw[offset:])
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            os.chmod(destination, 0o400)
        except OSError as exc:
            _authentication_failure(f"could not stage sealed source: {relative}", exc)
    for directory, directories, _files in os.walk(staged_runtime_root, topdown=False):
        for name in directories:
            os.chmod(Path(directory) / name, 0o500)
        if Path(directory) != staged_runtime_root:
            os.chmod(directory, 0o500)
    os.chmod(staged_runtime_root, 0o500)


def _validate_source_closure(
    runtime_root: Path,
    dependency_closure: Sequence[Mapping[str, object]],
) -> None:
    for row in dependency_closure:
        path = row.get("path")
        if not isinstance(path, str):
            _authentication_failure("dependency closure contains an invalid source path")
        raw = _read_stable_source(runtime_root / _safe_relative_path(path), runtime_root)
        if len(raw) != row.get("byte_count") or _sha256_bytes(raw) != row.get(
            "raw_bytes_sha256"
        ):
            _authentication_failure(f"producer source mutated after staging: {path}")


def _validate_staged_closure(
    staged_runtime_root: Path,
    dependency_closure: object,
) -> None:
    root = _validate_real_directory(Path(staged_runtime_root), label="staged_runtime_root")
    root_stat = os.stat(root, follow_symlinks=False)
    if root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o222:
        _authentication_failure("staged runtime root must be owner-bound and read-only")
    if not isinstance(dependency_closure, Sequence) or isinstance(
        dependency_closure, (str, bytes)
    ):
        _authentication_failure("dependency closure must be an array")
    expected: dict[str, Mapping[str, object]] = {}
    previous: str | None = None
    for index, raw_row in enumerate(dependency_closure):
        if not isinstance(raw_row, Mapping) or set(raw_row) != {
            "path", "byte_count", "raw_bytes_sha256"
        }:
            _authentication_failure(f"dependency closure row {index} is not closed")
        path = raw_row["path"]
        count = raw_row["byte_count"]
        digest = raw_row["raw_bytes_sha256"]
        if not isinstance(path, str):
            _authentication_failure(f"dependency closure row {index}.path is invalid")
        _safe_relative_path(path)
        if previous is not None and path <= previous:
            _authentication_failure("dependency closure paths must be unique and sorted")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _authentication_failure(f"dependency closure row {index}.byte_count is invalid")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            _authentication_failure(f"dependency closure row {index}.raw_bytes_sha256 is invalid")
        expected[path] = raw_row
        previous = path

    actual: set[str] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in [*directories, *files]:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            try:
                candidate_stat = os.lstat(candidate)
            except OSError as exc:
                _authentication_failure(f"staged closure path is unavailable: {relative}", exc)
            if stat.S_ISLNK(candidate_stat.st_mode):
                _authentication_failure(f"staged closure contains a symlink: {relative}")
            if candidate_stat.st_uid != os.geteuid() or candidate_stat.st_mode & 0o222:
                _authentication_failure(
                    f"staged closure path must be owner-bound and read-only: {relative}"
                )
            if name == "__pycache__" or name.endswith((".pyc", ".pyo")):
                _authentication_failure(f"staged closure contains bytecode: {relative}")
        for name in files:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.suffix != ".py":
                _authentication_failure(f"staged closure contains a non-source file: {relative}")
            actual.add(relative)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        _authentication_failure(
            f"staged closure does not equal sealed closure; missing={missing} extra={extra}"
        )
    for path, row in expected.items():
        raw = _read_stable_source(root / path, root)
        if len(raw) != row["byte_count"] or _sha256_bytes(raw) != row["raw_bytes_sha256"]:
            _authentication_failure(f"staged source does not match sealed bytes: {path}")


def _validate_import_trace(
    trace_record: bytes,
    dependency_closure: Sequence[Mapping[str, object]],
    *,
    staged_runtime_root: Path,
) -> tuple[str, ...]:
    """Authenticate a nonempty child-loaded subset of the sealed closure."""
    try:
        document = json.loads(trace_record)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _authentication_failure("child import trace is not complete UTF-8 JSON", exc)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "modules"}
        or document.get("schema_version") != "producer-import-trace-v1"
        or not isinstance(document["modules"], list)
    ):
        _authentication_failure("child import trace does not use the closed schema")
    if not document["modules"]:
        _authentication_failure("child import trace must contain observed modules")
    modules: list[dict[str, str]] = []
    for index, row in enumerate(document["modules"]):
        if (
            not isinstance(row, dict)
            or set(row) != {"module", "path"}
            or not isinstance(row["module"], str)
            or not row["module"]
            or not isinstance(row["path"], str)
            or not row["path"]
        ):
            _authentication_failure(f"child import trace module row {index} is invalid")
        modules.append(row)
    if modules != sorted(
        modules, key=lambda row: (row["module"], row["path"])
    ) or len({(row["module"], row["path"]) for row in modules}) != len(modules):
        _authentication_failure("child import trace module rows must be unique and sorted")
    if canonical_json_bytes(document) != trace_record:
        _authentication_failure("child import trace is not canonical JSON")

    module_to_path: dict[str, str] = {}
    path_to_module: dict[str, str] = {}
    for row in modules:
        module = row["module"]
        path = row["path"]
        _safe_relative_path(path)
        existing_path = module_to_path.setdefault(module, path)
        if existing_path != path:
            _authentication_failure(
                f"child import trace module aliases multiple paths: {module}"
            )
        existing_module = path_to_module.setdefault(path, module)
        if existing_module != module:
            _authentication_failure(
                f"child import trace path aliases multiple modules: {path}"
            )
    if module_to_path.get("__main__") != "aggregate-screening.py":
        _authentication_failure(
            "child import trace omits the direct aggregate-screening.py entry"
        )

    _validate_staged_closure(staged_runtime_root, dependency_closure)
    prefix = SCRIPTS_ROOT.as_posix() + "/"
    expected_by_path: dict[str, str] = {}
    expected_by_module: dict[str, str] = {}
    expected_paths: list[str] = []
    for row in dependency_closure:
        full_path = row.get("path")
        if not isinstance(full_path, str) or not full_path.startswith(prefix):
            _authentication_failure("dependency closure path is outside producer scripts")
        relative = full_path[len(prefix):]
        relative_path = Path(relative)
        if relative == "aggregate-screening.py":
            module = "__main__"
        elif relative_path.name == "__init__.py":
            module = ".".join(relative_path.parent.parts)
        else:
            module = ".".join(relative_path.with_suffix("").parts)
        if relative in expected_by_path or module in expected_by_module:
            _authentication_failure(
                "dependency closure does not have one-to-one module/path identities"
            )
        expected_by_path[relative] = module
        expected_by_module[module] = relative
        expected_paths.append(full_path)
    for row in modules:
        module = row["module"]
        path = row["path"]
        if (
            expected_by_path.get(path) != module
            or expected_by_module.get(module) != path
        ):
            _authentication_failure(
                f"child import trace identity is not sealed: {(module, path)!r}"
            )
    observed_paths = set(path_to_module)
    return tuple(
        full_path
        for full_path in expected_paths
        if full_path[len(prefix):] in observed_paths
    )


def _normalize_finite_json(value: object, path: str = "$") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _runtime_failure(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            _runtime_failure(f"{path} mapping keys must be strings")
        return {
            key: _normalize_finite_json(value[key], f"{path}.{key}")
            for key in sorted(value)
        }
    if isinstance(value, list):
        return [
            _normalize_finite_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    _runtime_failure(f"{path} contains a non-JSON runtime value")


def _read_stable_runtime_file(path: Path, *, label: str) -> tuple[Path, bytes]:
    try:
        absolute = path.absolute()
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        _runtime_failure(f"{label} is unavailable: {path}", exc)
    try:
        path_stat = os.stat(resolved, follow_symlinks=False)
    except OSError as exc:
        _runtime_failure(f"{label} is unavailable: {resolved}", exc)
    if not stat.S_ISREG(path_stat.st_mode):
        _runtime_failure(f"{label} must resolve to a regular file: {resolved}")
    file_fd: int | None = None
    try:
        file_fd = os.open(resolved, _OPEN_FILE_FLAGS)
        before = os.fstat(file_fd)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        verify = os.stat(resolved, follow_symlinks=False)
        if (
            _stat_identity(before) != _stat_identity(after)
            or _stat_identity(before) != _stat_identity(verify)
        ):
            _runtime_failure(f"{label} changed while it was fingerprinted: {resolved}")
        return resolved, b"".join(chunks)
    except OSError as exc:
        _runtime_failure(f"{label} could not be fingerprinted: {resolved}", exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _build_numpy_fingerprint(
    probe: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    if not isinstance(probe, Mapping) or set(probe) != _NUMPY_PROBE_KEYS:
        _runtime_failure("NumPy build probe must contain exactly extension_modules and show_config")
    raw_extensions = probe["extension_modules"]
    if not isinstance(raw_extensions, list) or not raw_extensions:
        _runtime_failure("NumPy/SciPy extension module list must be non-empty")
    rows: list[dict[str, object]] = []
    realpaths: set[Path] = set()
    for index, raw_row in enumerate(raw_extensions):
        if not isinstance(raw_row, Mapping) or set(raw_row) != {
            "distribution", "module", "path"
        }:
            _runtime_failure(f"extension module row {index} is not closed")
        distribution = raw_row["distribution"]
        module = raw_row["module"]
        path = raw_row["path"]
        if distribution not in {"numpy", "scipy"}:
            _runtime_failure(f"extension module row {index}.distribution is invalid")
        if (
            not isinstance(module, str)
            or not module.startswith(f"{distribution}.")
            or not isinstance(path, str)
            or not Path(path).is_absolute()
        ):
            _runtime_failure(f"extension module row {index} identity is invalid")
        resolved, raw = _read_stable_runtime_file(
            Path(path), label=f"extension module {module}"
        )
        if resolved in realpaths:
            _runtime_failure(f"duplicate extension module realpath: {resolved}")
        realpaths.add(resolved)
        rows.append({
            "distribution": distribution,
            "module": module,
            "path": str(resolved),
            "byte_count": len(raw),
            "raw_bytes_sha256": _sha256_bytes(raw),
        })
    rows.sort(key=lambda row: (
        str(row["distribution"]), str(row["module"]), str(row["path"])
    ))
    fingerprint = {
        "schema_version": "numpy-scipy-build-fingerprint-v1",
        "extension_modules": rows,
        "show_config": _normalize_finite_json(probe["show_config"], "$.show_config"),
    }
    return fingerprint, sha256_json(fingerprint)


def _parse_otool_output(extension_path: str, stdout: str) -> list[dict[str, str]]:
    forbidden_boundaries = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
    if (
        any(character in stdout for character in forbidden_boundaries)
        or not stdout.endswith("\n")
    ):
        _runtime_failure("otool output has invalid line boundaries")
    lines = stdout[:-1].split("\n")
    if any(not line or not line.strip(" \t") for line in lines):
        _runtime_failure("otool output contains a blank dependency row")
    if not lines or lines[0] != f"{extension_path}:":
        _runtime_failure("otool output header does not equal the extension path")
    if len(lines) < 2:
        _runtime_failure("otool output contains no dependencies")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for line in lines[1:]:
        match = _OTOOL_ROW_RE.fullmatch(line)
        if match is None:
            _runtime_failure(f"otool dependency line is malformed: {line!r}")
        row = {
            "extension_path": extension_path,
            "install_name": match.group("install_name"),
            "compatibility_version": match.group("compatibility"),
            "current_version": match.group("current"),
        }
        if any(
            not value or value.strip(" \t") != value
            for key, value in row.items()
            if key != "extension_path"
        ):
            _runtime_failure(f"otool dependency fields have invalid whitespace: {line!r}")
        identity = tuple(row.values())
        if identity in seen:
            _runtime_failure("otool output contains a duplicate dependency row")
        seen.add(identity)
        rows.append(row)
    return rows


def _bind_resolved_library(
    extension_path: str,
    soname: str,
    resolved_path: str,
) -> dict[str, object]:
    if not Path(resolved_path).is_absolute():
        _runtime_failure(f"ldd dependency path is not absolute: {resolved_path}")
    resolved, raw = _read_stable_runtime_file(
        Path(resolved_path), label=f"ldd dependency {soname}"
    )
    return {
        "extension_path": extension_path,
        "soname": soname,
        "resolved_path": str(resolved),
        "byte_count": len(raw),
        "raw_bytes_sha256": _sha256_bytes(raw),
    }


def _parse_ldd_output(extension_path: str, stdout: str) -> list[dict[str, object]]:
    forbidden_boundaries = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
    if (
        any(character in stdout for character in forbidden_boundaries)
        or not stdout.endswith("\n")
    ):
        _runtime_failure("ldd output contains non-LF line endings")
    lines = stdout[:-1].split("\n")
    if not lines or any(not line or not line.strip(" \t") for line in lines):
        _runtime_failure("ldd output is empty")
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_line in lines:
        line = raw_line.strip(" \t")
        if _LINUX_VIRTUAL_RE.fullmatch(line):
            continue
        soname: str
        resolved_text: str
        if "=>" in line:
            if line.count("=>") != 1:
                _runtime_failure(f"ldd dependency line is malformed: {raw_line!r}")
            raw_soname, raw_resolved = line.split("=>", 1)
            soname = raw_soname.strip(" \t")
            resolved_text = raw_resolved.strip(" \t")
            if not soname or any(character in soname for character in " \t"):
                _runtime_failure(f"ldd soname is malformed: {raw_line!r}")
        else:
            resolved_text = line
            direct_match = _LDD_RESOLVED_RE.fullmatch(resolved_text)
            if direct_match is None:
                _runtime_failure(f"ldd no-arrow line is malformed: {raw_line!r}")
            soname = direct_match.group("path")
        match = _LDD_RESOLVED_RE.fullmatch(resolved_text)
        if match is None:
            _runtime_failure(f"ldd dependency is unresolved or malformed: {raw_line!r}")
        path = match.group("path")
        row = _bind_resolved_library(extension_path, soname, path)
        identity = (
            str(row["extension_path"]),
            str(row["soname"]),
            str(row["resolved_path"]),
        )
        if identity in seen:
            _runtime_failure("ldd output contains a duplicate dependency row")
        seen.add(identity)
        rows.append(row)
    if not rows:
        _runtime_failure("ldd output contains no resolved file dependencies")
    return rows


def _run_root_owned_tool(tool: Path, extension_path: str) -> str:
    try:
        tool_stat = os.stat(tool, follow_symlinks=False)
    except OSError as exc:
        _runtime_failure(f"required numerical-link tool is unavailable: {tool}", exc)
    if (
        not stat.S_ISREG(tool_stat.st_mode)
        or tool_stat.st_uid != 0
        or tool_stat.st_mode & 0o022
        or not tool_stat.st_mode & stat.S_IXUSR
    ):
        _runtime_failure(f"required numerical-link tool is not root-owned and protected: {tool}")
    try:
        completed = subprocess.run(
            [str(tool), "-L", extension_path] if tool == Path("/usr/bin/otool")
            else [str(tool), extension_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            env={"LC_ALL": "C", "LANG": "C"},
            timeout=30,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        _runtime_failure(f"required numerical-link tool failed: {tool}", exc)
    if completed.returncode != 0 or completed.stderr:
        _runtime_failure(
            f"required numerical-link tool did not complete cleanly: "
            f"{tool} exit={completed.returncode} stderr={completed.stderr!r}"
        )
    return completed.stdout


def _build_link_fingerprint(
    *,
    platform_system: str,
    platform_release: str,
    machine: str,
    extension_modules: Sequence[Mapping[str, object]],
    numpy_build_sha256: str,
) -> tuple[dict[str, object], str]:
    paths = [row.get("path") for row in extension_modules]
    if (
        not paths
        or any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths)
        or len(paths) != len(set(paths))
    ):
        _runtime_failure("link fingerprint requires unique absolute extension paths")
    sorted_paths = sorted(str(path) for path in paths)
    if platform_system == "Darwin":
        dependencies: list[dict[str, str]] = []
        for extension_path in sorted_paths:
            stdout = _run_root_owned_tool(Path("/usr/bin/otool"), extension_path)
            dependencies.extend(_parse_otool_output(extension_path, stdout))
        dependencies.sort(key=lambda row: (
            row["extension_path"], row["install_name"],
            row["compatibility_version"], row["current_version"],
        ))
        identities = [tuple(row.values()) for row in dependencies]
        if len(identities) != len(set(identities)):
            _runtime_failure("macOS link fingerprint contains duplicate dependency rows")
        if not any(_ACCELERATE_RE.fullmatch(row["install_name"]) for row in dependencies):
            _runtime_failure("macOS numerical extensions are not bound to Accelerate")
        fingerprint: dict[str, object] = {
            "schema_version": "macos-accelerate-link-fingerprint-v1",
            "platform_release": platform_release,
            "machine": machine,
            "framework": "Accelerate",
            "dependencies": dependencies,
            "numpy_build_sha256": numpy_build_sha256,
        }
    elif platform_system == "Linux":
        libraries: list[dict[str, object]] = []
        for extension_path in sorted_paths:
            stdout = _run_root_owned_tool(Path("/usr/bin/ldd"), extension_path)
            libraries.extend(_parse_ldd_output(extension_path, stdout))
        libraries.sort(key=lambda row: (
            str(row["extension_path"]), str(row["soname"]), str(row["resolved_path"])
        ))
        identities = [
            (str(row["extension_path"]), str(row["soname"]), str(row["resolved_path"]))
            for row in libraries
        ]
        if len(identities) != len(set(identities)):
            _runtime_failure("Linux link fingerprint contains duplicate dependency rows")
        fingerprint = {
            "schema_version": "linux-blas-lapack-link-fingerprint-v1",
            "platform_release": platform_release,
            "machine": machine,
            "libraries": libraries,
            "numpy_build_sha256": numpy_build_sha256,
        }
    else:
        _runtime_failure(f"unsupported numerical-runtime platform: {platform_system!r}")
    return fingerprint, sha256_json(fingerprint)


def _probe_runtime(staged_runtime_root: Path) -> dict[str, object]:
    scripts_root = staged_runtime_root / SCRIPTS_ROOT
    entry_point = staged_runtime_root / ENTRY_POINT
    environment = {
        "AUDIENCE_C1_PROBE_SCRIPTS_ROOT": str(scripts_root),
        "AUDIENCE_C1_PROBE_ENTRY_POINT": str(entry_point),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _RUNTIME_PROBE_SOURCE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=60,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        _runtime_failure("scientific runtime probe could not execute", exc)
    if completed.returncode != 0 or completed.stderr:
        _runtime_failure(
            f"scientific runtime probe failed: exit={completed.returncode} "
            f"stderr={completed.stderr!r}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _runtime_failure("scientific runtime probe returned malformed JSON", exc)
    if not isinstance(payload, dict) or set(payload) != _RUNTIME_PROBE_KEYS:
        _runtime_failure("scientific runtime probe returned an unknown schema")
    return payload


def _build_runtime_fingerprint(staged_runtime_root: Path) -> dict[str, str]:
    probe = _probe_runtime(staged_runtime_root)
    for key in (
        "python_implementation", "python_version", "numpy_version", "scipy_version",
        "platform_system", "platform_release", "machine",
    ):
        if not isinstance(probe[key], str) or not str(probe[key]).strip():
            _runtime_failure(f"scientific runtime probe field {key} is empty")
    numpy_fingerprint, numpy_sha = _build_numpy_fingerprint({
        "extension_modules": probe["extension_modules"],
        "show_config": probe["show_config"],
    })
    _link_fingerprint, link_sha = _build_link_fingerprint(
        platform_system=str(probe["platform_system"]),
        platform_release=str(probe["platform_release"]),
        machine=str(probe["machine"]),
        extension_modules=numpy_fingerprint["extension_modules"],
        numpy_build_sha256=numpy_sha,
    )
    return {
        "python_implementation": str(probe["python_implementation"]),
        "python_version": str(probe["python_version"]),
        "numpy_version": str(probe["numpy_version"]),
        "scipy_version": str(probe["scipy_version"]),
        "platform_system": str(probe["platform_system"]),
        "platform_release": str(probe["platform_release"]),
        "machine": str(probe["machine"]),
        "numpy_build_sha256": numpy_sha,
        "blas_lapack_sha256": link_sha,
    }


def _canonical_configuration(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _authentication_failure(f"{label} must be an object")
    try:
        normalized = _normalize_finite_json(value, f"$.{label}")
    except ProducerRuntimeUnavailable as exc:
        _authentication_failure(f"{label} must be recursively finite", exc)
    assert isinstance(normalized, dict)
    return normalized


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        _authentication_failure(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))} "
            f"extra={sorted(set(value) - expected)}"
        )


def _json_integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        _authentication_failure(f"{label} must be a JSON integer")
    if minimum is not None and value < minimum:
        _authentication_failure(f"{label} must be at least {minimum}")
    return value


def _json_float(
    value: object,
    *,
    label: str,
    minimum: float | None = None,
    positive: bool = False,
) -> float:
    if type(value) is not float or not math.isfinite(value):
        _authentication_failure(f"{label} must be a finite JSON float")
    if positive and value <= 0:
        _authentication_failure(f"{label} must be positive")
    if minimum is not None and value < minimum:
        _authentication_failure(f"{label} must be at least {minimum}")
    return value


def _nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _authentication_failure(f"{label} must be a non-empty string")
    return value


def _validate_maxdiff_configuration(value: object) -> dict[str, object]:
    config = _canonical_configuration(value, label="maxdiff_configuration")
    _require_exact_keys(
        config,
        {
            "penalty_lambda", "optimizer_tolerance", "bootstrap_count",
            "successful_fit_floor", "clear_finalist_threshold",
            "clear_non_finalist_threshold", "seed",
        },
        label="maxdiff_configuration",
    )
    _json_float(config["penalty_lambda"], label="maxdiff.penalty_lambda", minimum=0.0)
    _json_float(
        config["optimizer_tolerance"],
        label="maxdiff.optimizer_tolerance",
        positive=True,
    )
    _json_integer(config["bootstrap_count"], label="maxdiff.bootstrap_count", minimum=1)
    successful = _json_float(
        config["successful_fit_floor"], label="maxdiff.successful_fit_floor"
    )
    upper = _json_float(
        config["clear_finalist_threshold"], label="maxdiff.clear_finalist_threshold"
    )
    lower = _json_float(
        config["clear_non_finalist_threshold"],
        label="maxdiff.clear_non_finalist_threshold",
    )
    _json_integer(config["seed"], label="maxdiff.seed")
    if not 0.0 <= successful <= 1.0:
        _authentication_failure("maxdiff.successful_fit_floor is outside [0,1]")
    if not 0.0 <= lower < upper <= 1.0:
        _authentication_failure("MaxDiff shortlist thresholds are invalid")
    return config


def _validate_pairwise_configuration(value: object) -> dict[str, object]:
    config = _canonical_configuration(value, label="pairwise_configuration")
    _require_exact_keys(
        config,
        {
            "tie_parameter", "penalty_lambda", "optimizer_tolerance",
            "bootstrap_count", "successful_fit_floor", "seed",
        },
        label="pairwise_configuration",
    )
    _json_float(
        config["tie_parameter"], label="pairwise.tie_parameter", minimum=0.0
    )
    _json_float(
        config["penalty_lambda"], label="pairwise.penalty_lambda", positive=True
    )
    _json_float(
        config["optimizer_tolerance"],
        label="pairwise.optimizer_tolerance",
        positive=True,
    )
    _json_integer(
        config["bootstrap_count"], label="pairwise.bootstrap_count", minimum=1
    )
    successful = _json_float(
        config["successful_fit_floor"], label="pairwise.successful_fit_floor"
    )
    seed = _json_integer(config["seed"], label="pairwise.seed")
    if not 0.0 <= successful <= 1.0:
        _authentication_failure("pairwise.successful_fit_floor is outside [0,1]")
    if seed < 0:
        _authentication_failure("pairwise.seed must be non-negative")
    return config


def _validate_recovery_configuration(value: object) -> dict[str, object]:
    recovery = _canonical_configuration(value, label="recovery_configuration")
    _require_exact_keys(
        recovery,
        {
            "version", "calibration_status", "library_size_bands",
            "shortlist_size_bands", "segment_count", "tie_inability_band",
            "utility_separation_band", "planned_participation_floor",
            "usable_participation_floor", "bootstrap_count",
            "successful_fit_floor", "shortlist_thresholds",
        },
        label="recovery_configuration",
    )
    _nonempty_string(recovery["version"], label="recovery.version")
    if recovery["calibration_status"] not in {"exploratory_only", "calibrated"}:
        _authentication_failure("recovery.calibration_status is invalid")
    calibrated = recovery["calibration_status"] == "calibrated"
    for field in ("library_size_bands", "shortlist_size_bands"):
        bands = recovery[field]
        if not isinstance(bands, list) or (calibrated and not bands):
            _authentication_failure(f"recovery.{field} must be a valid array")
        names: set[str] = set()
        for index, raw_band in enumerate(bands):
            if not isinstance(raw_band, Mapping):
                _authentication_failure(f"recovery.{field}[{index}] must be an object")
            band = dict(raw_band)
            _require_exact_keys(
                band, {"name", "minimum", "maximum"},
                label=f"recovery.{field}[{index}]",
            )
            name = _nonempty_string(
                band["name"], label=f"recovery.{field}[{index}].name"
            )
            if name in names:
                _authentication_failure(f"recovery.{field} band names must be unique")
            names.add(name)
            minimum = _json_integer(
                band["minimum"], label=f"recovery.{field}[{index}].minimum", minimum=1
            )
            maximum = _json_integer(
                band["maximum"], label=f"recovery.{field}[{index}].maximum", minimum=1
            )
            if maximum < minimum:
                _authentication_failure(f"recovery.{field}[{index}] range is invalid")
    segment = recovery["segment_count"]
    if not isinstance(segment, Mapping):
        _authentication_failure("recovery.segment_count must be an object")
    _require_exact_keys(dict(segment), {"minimum", "maximum"}, label="segment_count")
    segment_minimum = _json_integer(
        segment["minimum"], label="recovery.segment_count.minimum", minimum=1
    )
    segment_maximum = _json_integer(
        segment["maximum"], label="recovery.segment_count.maximum", minimum=1
    )
    if segment_maximum < segment_minimum:
        _authentication_failure("recovery.segment_count range is invalid")
    tie = recovery["tie_inability_band"]
    if not isinstance(tie, Mapping):
        _authentication_failure("recovery.tie_inability_band must be an object")
    _require_exact_keys(
        dict(tie), {"minimum_rate", "maximum_rate"}, label="tie_inability_band"
    )
    tie_minimum = _json_float(
        tie["minimum_rate"], label="recovery.tie_inability_band.minimum_rate"
    )
    tie_maximum = _json_float(
        tie["maximum_rate"], label="recovery.tie_inability_band.maximum_rate"
    )
    if not 0.0 <= tie_minimum <= tie_maximum <= 1.0:
        _authentication_failure("recovery.tie_inability_band range is invalid")
    utility = recovery["utility_separation_band"]
    if not isinstance(utility, Mapping):
        _authentication_failure("recovery.utility_separation_band must be an object")
    _require_exact_keys(
        dict(utility),
        {"minimum_log_utility_gap", "maximum_log_utility_gap"},
        label="utility_separation_band",
    )
    utility_minimum = _json_float(
        utility["minimum_log_utility_gap"],
        label="recovery.utility_separation_band.minimum_log_utility_gap",
    )
    utility_maximum = _json_float(
        utility["maximum_log_utility_gap"],
        label="recovery.utility_separation_band.maximum_log_utility_gap",
    )
    if not 0.0 <= utility_minimum <= utility_maximum:
        _authentication_failure("recovery.utility_separation_band range is invalid")
    for field in (
        "planned_participation_floor", "usable_participation_floor", "bootstrap_count"
    ):
        _json_integer(recovery[field], label=f"recovery.{field}", minimum=1)
    successful = _json_float(
        recovery["successful_fit_floor"], label="recovery.successful_fit_floor"
    )
    if not 0.0 <= successful <= 1.0:
        _authentication_failure("recovery.successful_fit_floor is outside [0,1]")
    thresholds = recovery["shortlist_thresholds"]
    if not isinstance(thresholds, Mapping):
        _authentication_failure("recovery.shortlist_thresholds must be an object")
    _require_exact_keys(
        dict(thresholds),
        {"clear_finalist", "clear_non_finalist"},
        label="shortlist_thresholds",
    )
    upper = _json_float(
        thresholds["clear_finalist"],
        label="recovery.shortlist_thresholds.clear_finalist",
    )
    lower = _json_float(
        thresholds["clear_non_finalist"],
        label="recovery.shortlist_thresholds.clear_non_finalist",
    )
    if not 0.0 <= lower < upper <= 1.0:
        _authentication_failure("recovery.shortlist thresholds are invalid")
    if calibrated and (
        recovery["bootstrap_count"] != 2000
        or successful != 0.95
        or upper != 0.90
        or lower != 0.10
    ):
        _authentication_failure(
            "calibrated recovery constants do not equal producer requirements"
        )
    return recovery


def _literal_constant(
    runtime_root: Path,
    relative: str,
    name: str,
    expected: object,
    dependency_rows: Mapping[str, Mapping[str, object]],
) -> object:
    if relative not in dependency_rows:
        _authentication_failure(
            f"producer constant source is not in dependency_closure: {relative}"
        )
    path = runtime_root / relative
    if not path.exists():
        _authentication_failure(f"producer constant source is missing: {relative}")
    raw = _read_stable_source(path, runtime_root)
    row = dependency_rows[relative]
    if (
        set(row) != {"path", "byte_count", "raw_bytes_sha256"}
        or row.get("path") != relative
        or row.get("byte_count") != len(raw)
        or row.get("raw_bytes_sha256") != _sha256_bytes(raw)
    ):
        _authentication_failure(
            f"producer constant source row is not hash-bound: {relative}"
        )
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
    except SyntaxError as exc:
        _authentication_failure(f"producer constant source is invalid: {relative}", exc)
    found: list[object] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                try:
                    found.append(ast.literal_eval(node.value))
                except (ValueError, TypeError) as exc:
                    if name != "_COMPLETE_POLICY":
                        _authentication_failure(
                            f"producer constant {name} is not a literal", exc
                        )
                    class _PolicyVersionResolver(ast.NodeTransformer):
                        def visit_Name(self, node: ast.Name) -> ast.AST:
                            if node.id != "CALIBRATION_POLICY_VERSION":
                                raise ValueError(
                                    f"unsupported name in _COMPLETE_POLICY: {node.id}"
                                )
                            return ast.copy_location(
                                ast.Constant(
                                    value="complete-exposure-calibration-v2"
                                ),
                                node,
                            )
                    try:
                        resolved = _PolicyVersionResolver().visit(deepcopy(node.value))
                        found.append(ast.literal_eval(ast.fix_missing_locations(resolved)))
                    except (ValueError, TypeError) as resolve_exc:
                        _authentication_failure(
                            f"producer constant {name} is not closed", resolve_exc
                        )
    if found != [expected]:
        _authentication_failure(
            f"producer constant {name} does not equal authenticated policy value"
        )
    return found[0]


def _require_number(
    mapping: Mapping[str, object],
    key: str,
    expected: float | int | None = None,
) -> float:
    value = mapping.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        _authentication_failure(f"configuration {key} must be finite")
    numeric = float(value)
    if expected is not None and numeric != float(expected):
        _authentication_failure(
            f"configuration {key} must equal authenticated producer constant {expected}"
        )
    return numeric


def _build_policy_bindings(
    surface: str,
    configuration: Mapping[str, object],
    upstream_semantics_sha256: str | None,
    runtime_root: Path,
    *,
    dependency_closure: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    if surface not in SURFACES:
        _authentication_failure(f"unknown synthetic producer surface: {surface}")
    if not isinstance(configuration, Mapping):
        _authentication_failure("producer configuration must be an object")
    complete_path = (
        "skills/audience-ad-testing-lab/scripts/audience_lab/complete_exposure.py"
    )
    maxdiff_path = "skills/audience-ad-testing-lab/scripts/audience_lab/maxdiff.py"
    pairwise_path = "skills/audience-ad-testing-lab/scripts/audience_lab/pairwise.py"
    tiebreak = "creative-id-serialization-only-v1"
    closure = (
        list(dependency_closure)
        if dependency_closure is not None
        else _discover_dependency_closure(runtime_root, ENTRY_POINT)
    )
    dependency_rows = {
        str(row.get("path")): row for row in closure if isinstance(row, Mapping)
    }

    if surface == "complete_exposure_ordering":
        if set(configuration) != {"recovery_configuration"}:
            _authentication_failure("complete-exposure configuration wrapper is not closed")
        if upstream_semantics_sha256 is not None:
            _authentication_failure("complete exposure must not bind upstream semantics")
        recovery = _canonical_configuration(
            configuration["recovery_configuration"], label="recovery_configuration"
        )
        expected_recovery = {
            "version": "complete-exposure-calibration-v2",
            "scope": "conditional_synthetic_run_only",
            "planned_jobs_per_segment": 9,
            "minimum_usable_records_per_segment": 8,
            "bootstrap_resamples": 2000,
            "finalist_inclusion_threshold": 0.90,
            "nonfinalist_inclusion_threshold": 0.10,
            "cutoff_tie_policy": "no_point_estimate_only_decision",
            "archetype_sensitivity": (
                "leave_one_persona_archetype_out_top_k_consistent"
            ),
            "minimum_archetype_diversity": 2,
            "minimum_evaluable_archetype_exclusions": 2,
            "calibration_basis": (
                "deterministic_task9_adversarial_recovery_fixtures"
            ),
            "human_market_calibration": False,
        }
        authenticated_recovery = _literal_constant(
            runtime_root, ENTRY_POINT, "_COMPLETE_POLICY",
            expected_recovery, dependency_rows,
        )
        if recovery != authenticated_recovery or any(
            type(recovery[key]) is not type(expected_recovery[key])
            for key in expected_recovery
        ):
            _authentication_failure(
                "complete recovery configuration does not equal authenticated policy"
            )
        policy_version = _literal_constant(
            runtime_root, complete_path, "CALIBRATION_POLICY_VERSION",
            "complete-exposure-calibration-v2", dependency_rows,
        )
        resamples = _literal_constant(
            runtime_root, complete_path, "PRODUCTION_RESAMPLES", 2000,
            dependency_rows,
        )
        cutoff = _literal_constant(
            runtime_root, complete_path, "_TIE_TOLERANCE", 1e-12,
            dependency_rows,
        )
        if recovery.get("version") != policy_version:
            _authentication_failure(
                "complete recovery version disagrees with authenticated producer constant"
            )
        return {
            "calibration_policy_version": policy_version,
            "production_resamples": resamples,
            "cutoff_tie_tolerance": cutoff,
            "ordering_tiebreak": tiebreak,
            "ordering_equivalence": "exact-utility-equality-v1",
            "recovery_configuration_sha256": sha256_json(recovery),
        }

    if surface == "maxdiff_screening_ordering":
        if set(configuration) != {
            "maxdiff_configuration", "recovery_configuration"
        }:
            _authentication_failure("MaxDiff configuration wrapper is not closed")
        if upstream_semantics_sha256 is not None:
            _authentication_failure("MaxDiff screening must not bind upstream semantics")
        model = _validate_maxdiff_configuration(configuration["maxdiff_configuration"])
        recovery = _validate_recovery_configuration(
            configuration["recovery_configuration"]
        )
        required = _literal_constant(
            runtime_root, maxdiff_path, "_REQUIRED_BOOTSTRAP_COUNT", 2000,
            dependency_rows,
        )
        successful = _literal_constant(
            runtime_root, maxdiff_path, "_MINIMUM_SUCCESSFUL_FIT_FLOOR", 0.95,
            dependency_rows,
        )
        upper = _literal_constant(
            runtime_root, maxdiff_path, "_CLEAR_FINALIST_THRESHOLD", 0.90,
            dependency_rows,
        )
        lower = _literal_constant(
            runtime_root, maxdiff_path, "_CLEAR_NON_FINALIST_THRESHOLD", 0.10,
            dependency_rows,
        )
        minimum = _literal_constant(
            runtime_root, maxdiff_path, "_MINIMUM_UTILITY_TIE_TOLERANCE", 1e-12,
            dependency_rows,
        )
        if (
            model["bootstrap_count"] != recovery["bootstrap_count"]
            or model["successful_fit_floor"] != recovery["successful_fit_floor"]
            or model["clear_finalist_threshold"]
            != recovery["shortlist_thresholds"]["clear_finalist"]
            or model["clear_non_finalist_threshold"]
            != recovery["shortlist_thresholds"]["clear_non_finalist"]
        ):
            _authentication_failure(
                "MaxDiff configuration does not equal the replay recovery configuration"
            )
        tolerance = _require_number(model, "optimizer_tolerance")
        if tolerance <= 0:
            _authentication_failure("optimizer_tolerance must be positive")
        _require_number(model, "bootstrap_count", int(required))
        _require_number(model, "successful_fit_floor", float(successful))
        _require_number(model, "clear_finalist_threshold", float(upper))
        _require_number(model, "clear_non_finalist_threshold", float(lower))
        return {
            "maxdiff_configuration_sha256": sha256_json(model),
            "required_bootstrap_count": required,
            "minimum_successful_fit_floor": successful,
            "clear_finalist_threshold": upper,
            "clear_non_finalist_threshold": lower,
            "minimum_utility_tie_tolerance": minimum,
            "ordering_tiebreak": tiebreak,
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "effective_ordering_tolerance": max(tolerance, float(minimum)),
            "rounding_rule": "python-half-even-v1",
            "recovery_configuration_sha256": sha256_json(recovery),
        }

    if set(configuration) != {"pairwise_configuration"}:
        _authentication_failure("pairwise configuration wrapper is not closed")
    if (
        not isinstance(upstream_semantics_sha256, str)
        or not _SHA256_RE.fullmatch(upstream_semantics_sha256)
    ):
        _authentication_failure(
            "pairwise boundary requires an authenticated upstream semantics SHA-256"
        )
    model = _validate_pairwise_configuration(configuration["pairwise_configuration"])
    upper = _literal_constant(
        runtime_root, pairwise_path, "_CLEAR_FINALIST_THRESHOLD", 0.90,
        dependency_rows,
    )
    lower = _literal_constant(
        runtime_root, pairwise_path, "_CLEAR_NON_FINALIST_THRESHOLD", 0.10,
        dependency_rows,
    )
    minimum = _literal_constant(
        runtime_root, pairwise_path, "_MINIMUM_UTILITY_TIE_TOLERANCE", 1e-12,
        dependency_rows,
    )
    tolerance = _require_number(model, "optimizer_tolerance")
    if tolerance <= 0:
        _authentication_failure("pairwise optimizer_tolerance must be positive")
    _require_number(model, "bootstrap_count", 2000)
    _require_number(model, "successful_fit_floor", 0.95)
    return {
        "pairwise_configuration_sha256": sha256_json(model),
        "clear_finalist_threshold": upper,
        "clear_non_finalist_threshold": lower,
        "minimum_utility_tie_tolerance": minimum,
        "ordering_tiebreak": tiebreak,
        "ordering_equivalence": "rounded-utility-bucket-v1",
        "effective_ordering_tolerance": max(tolerance, float(minimum)),
        "rounding_rule": "python-half-even-v1",
        "upstream_screening_producer_semantics_sha256": upstream_semantics_sha256,
    }


def _serialization_bindings() -> dict[str, object]:
    return {
        "producer_raw_serialization": deepcopy(dict(PRODUCER_RAW_SERIALIZATION)),
        "canonical_document_serialization": {
            **dict(CANONICAL_DOCUMENT_SERIALIZATION),
            "separators": list(CANONICAL_DOCUMENT_SERIALIZATION["separators"]),
        },
    }


def build_producer_semantics(
    *,
    surface: str,
    runtime_root: Path,
    staged_runtime_root: Path,
    configuration: Mapping[str, object],
    upstream_semantics_sha256: str | None,
) -> ProducerSemanticsBundle:
    """Seal, stage, and fingerprint one allowlisted unchanged producer runtime."""
    if surface not in SURFACES:
        _authentication_failure(f"unknown synthetic producer surface: {surface}")
    root = _validate_real_directory(Path(runtime_root), label="runtime_root")
    entry_point, subcommand = SURFACES[surface]
    dependency_closure = _discover_dependency_closure(root, entry_point)
    stage = _prepare_stage(Path(staged_runtime_root))
    _stage_dependency_closure(root, stage, dependency_closure)
    _validate_staged_closure(stage, dependency_closure)
    runtime_fingerprint = _build_runtime_fingerprint(stage)
    if set(runtime_fingerprint) != {
        "python_implementation", "python_version", "numpy_version", "scipy_version",
        "platform_system", "platform_release", "machine", "numpy_build_sha256",
        "blas_lapack_sha256",
    } or any(
        not isinstance(value, str) or not value
        for value in runtime_fingerprint.values()
    ):
        _runtime_failure("runtime fingerprint is not the exact closed non-empty schema")
    for digest_field in ("numpy_build_sha256", "blas_lapack_sha256"):
        if not _SHA256_RE.fullmatch(runtime_fingerprint[digest_field]):
            _runtime_failure(f"runtime fingerprint {digest_field} is invalid")
    policy_bindings = _build_policy_bindings(
        surface,
        configuration,
        upstream_semantics_sha256,
        stage,
        dependency_closure=dependency_closure,
    )
    _validate_source_closure(root, dependency_closure)
    semantics: dict[str, object] = {
        "entry_point": entry_point,
        "subcommand": subcommand,
        "bootstrap_sha256": _sha256_bytes(REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")),
        "dependency_closure": deepcopy(dependency_closure),
        "runtime_fingerprint": deepcopy(runtime_fingerprint),
        "policy_bindings": deepcopy(policy_bindings),
        "output_serialization": _serialization_bindings(),
        "producer_semantics_sha256": None,
    }
    semantics["producer_semantics_sha256"] = sha256_json(semantics)
    return ProducerSemanticsBundle(
        semantics=deepcopy(semantics),
        staged_runtime_root=stage,
    )


__all__ = [
    "CANONICAL_DOCUMENT_SERIALIZATION",
    "PRODUCER_RAW_SERIALIZATION",
    "REPLAY_BOOTSTRAP_SOURCE",
    "ProducerSemanticsBundle",
    "build_producer_semantics",
]
