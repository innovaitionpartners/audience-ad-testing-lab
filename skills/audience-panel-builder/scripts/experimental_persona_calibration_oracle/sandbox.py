"""Closed private staging for engine-visible calibration entrypoints."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

from audience_panel_builder.population.experimental_calibration import (
    contracts as _engine_contracts,
)
from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    create_new_directory,
    write_new_bytes,
)
from audience_panel_builder.population.validation.producer_replay import (
    ProducerRuntimeUnavailable,
    _bound_interpreter,
    _trusted_provider,
)


_SCRIPTS_ROOT = Path(__file__).resolve(strict=True).parents[1]
_AD_TESTING_SCRIPTS_ROOT = (
    _SCRIPTS_ROOT.parents[1] / "audience-ad-testing-lab" / "scripts"
)
_ORACLE_TOKEN = "experimental_persona_calibration_oracle"
_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_CANDIDATE_BUNDLE_FILES = frozenset(
    {
        "README.txt",
        "base-persona-authoring-projection.json",
        "base-persona-snapshot.json",
        "bundle-manifest.json",
        "candidate-audience-panel.json",
        "candidate-persona-authoring-projection.json",
        "candidate-persona-snapshot.json",
        "experimental-candidate-binding.json",
        "experimental-proposal.json",
        "persona-behavior-diff.json",
        "standalone-panel-validation.json",
    }
)
_PROTECTED_SYSTEM_ALIASES = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}
_BOOTSTRAP = """\
import json
import pathlib
import runpy
import sys

if len(sys.argv) < 5:
    raise SystemExit(2)
source_root, entry_relative, external_roots_json, separator, *arguments = sys.argv[1:]
if separator != "--":
    raise SystemExit(2)
root = pathlib.Path(source_root).resolve(strict=True)
entry = (root / entry_relative).resolve(strict=True)
if root not in entry.parents or entry.suffix != ".py":
    raise SystemExit(2)
external_roots = json.loads(external_roots_json)
if (
    not isinstance(external_roots, list)
    or any(not isinstance(value, str) for value in external_roots)
):
    raise SystemExit(2)
sys.path[:] = [str(root), *external_roots, *[
    value for value in sys.path
    if value and "site-packages" not in value and "dist-packages" not in value
]]
sys.argv = [str(entry), *arguments]
runpy.run_path(str(entry), run_name="__main__")
"""
_SMOKE_BOOTSTRAP = """\
import json
import pathlib
import runpy
import sys

source_root, entry_relative, external_roots_json, output = sys.argv[1:]
root = pathlib.Path(source_root).resolve(strict=True)
entry = (root / entry_relative).resolve(strict=True)
if root not in entry.parents or entry.suffix != ".py":
    raise SystemExit(2)
external_roots = json.loads(external_roots_json)
if (
    not isinstance(external_roots, list)
    or any(not isinstance(value, str) for value in external_roots)
):
    raise SystemExit(2)
sys.path[:] = [str(root), *external_roots, *[
    value for value in sys.path
    if value and "site-packages" not in value and "dist-packages" not in value
]]
runpy.run_path(str(entry), run_name="calibration_stage_smoke")
pathlib.Path(output).write_text(
    json.dumps(
        {"entry_relative": entry_relative, "status": "imports-ok"},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\\n",
    encoding="utf-8",
)
"""


class EntrypointUnavailable(ContractError):
    """A registered future engine entrypoint is not implemented yet."""


@dataclass(frozen=True)
class _Argument:
    name: str
    flag: str
    kind: str
    optional: bool = False
    validator: str | None = None


@dataclass(frozen=True)
class _Entrypoint:
    cli: str
    required_module: str
    arguments: tuple[_Argument, ...]
    source_manifest: str
    external_runtime_modules: tuple[str, ...] = ()
    output_flag: str = "--output"
    output_kind: str = "json_file"
    namespace_packages: tuple[str, ...] = ()


@dataclass
class _AuthenticatedInput:
    source_path: Path
    kind: str
    descriptors: list[int]
    identities: list[tuple[int, int]]
    files: list[tuple[str, int, os.stat_result]]
    denied_identities: frozenset[tuple[int, int]]

    def close(self) -> None:
        while self.descriptors:
            descriptor = self.descriptors.pop()
            try:
                os.close(descriptor)
            except OSError:
                pass


_ENTRYPOINTS = {
    "diagnose": _Entrypoint(
        cli="diagnose-experimental-persona-behavior.py",
        required_module=(
            "audience_panel_builder/population/experimental_calibration/diagnosis.py"
        ),
        arguments=(
            _Argument(
                "base_panel_binding",
                "--base-panel-binding",
                "file",
                validator="validate_base_panel_binding_input",
            ),
            _Argument(
                "study_manifest",
                "--study-manifest",
                "file",
                validator="validate_study_manifest",
            ),
            _Argument(
                "scenario_manifests",
                "--scenario-manifests",
                "file",
                validator="validate_scenario_manifests_input",
            ),
            _Argument(
                "experiment_designs",
                "--experiment-designs",
                "file",
                validator="validate_experiment_designs_input",
            ),
            _Argument(
                "evidence_library_snapshot",
                "--evidence-library-snapshot",
                "file",
                validator="validate_evidence_library_snapshot_input",
            ),
            _Argument(
                "evidence_head_receipt",
                "--evidence-head-receipt",
                "file",
                validator="validate_evidence_head_receipt_input",
            ),
            _Argument(
                "creative_attribute_registry",
                "--creative-attribute-registry",
                "file",
                validator="validate_creative_attribute_registry",
            ),
            _Argument(
                "alternative_causes",
                "--alternative-causes",
                "file",
                validator="validate_alternative_causes_input",
            ),
            _Argument("diagnosis_id", "--diagnosis-id", "literal"),
            _Argument("diagnosed_at", "--diagnosed-at", "literal"),
        ),
        source_manifest=(
            "audience_panel_builder/population/experimental_calibration/"
            "private_stage_manifests/diagnose.json"
        ),
        output_flag="--private-stage-output",
        namespace_packages=(
            "audience_panel_builder",
            "audience_panel_builder/population",
            "audience_panel_builder/population/experimental_calibration",
        ),
    ),
    "propose": _Entrypoint(
        cli="propose-experimental-persona-behavior-update.py",
        required_module=(
            "audience_panel_builder/population/experimental_calibration/proposal.py"
        ),
        arguments=(
            _Argument(
                "base_panel_binding",
                "--base-panel-binding",
                "file",
                validator="validate_base_panel_binding_input",
            ),
            _Argument(
                "study_manifest",
                "--study-manifest",
                "file",
                validator="validate_study_manifest",
            ),
            _Argument(
                "scenario_manifests",
                "--scenario-manifests",
                "file",
                validator="validate_scenario_manifests_input",
            ),
            _Argument(
                "experiment_designs",
                "--experiment-designs",
                "file",
                validator="validate_experiment_designs_input",
            ),
            _Argument(
                "diagnosis",
                "--diagnosis",
                "file",
                validator="validate_diagnosis",
            ),
            _Argument(
                "creative_attribute_registry",
                "--creative-attribute-registry",
                "file",
                validator="validate_creative_attribute_registry",
            ),
            _Argument(
                "evidence_library_snapshot",
                "--evidence-library-snapshot",
                "file",
                validator="validate_evidence_library_snapshot_input",
            ),
            _Argument(
                "evidence_head_receipt",
                "--evidence-head-receipt",
                "file",
                validator="validate_evidence_head_receipt_input",
            ),
            _Argument(
                "alternative_causes",
                "--alternative-causes",
                "file",
                validator="validate_alternative_causes_input",
            ),
            _Argument("proposal_id", "--proposal-id", "literal"),
            _Argument("proposed_at", "--proposed-at", "literal"),
        ),
        source_manifest=(
            "audience_panel_builder/population/experimental_calibration/"
            "private_stage_manifests/propose.json"
        ),
        output_flag="--private-stage-output",
        namespace_packages=(
            "audience_panel_builder",
            "audience_panel_builder/population",
            "audience_panel_builder/population/experimental_calibration",
        ),
    ),
    "materialize": _Entrypoint(
        cli="materialize-experimental-persona-candidate.py",
        required_module=(
            "audience_panel_builder/population/experimental_calibration/candidate.py"
        ),
        arguments=(
            _Argument(
                "base_panel",
                "--base-panel",
                "file",
                validator="validate_saved_panel_v3_input",
            ),
            _Argument(
                "proposal",
                "--proposal",
                "file",
                validator="validate_experimental_proposal",
            ),
            _Argument(
                "study_manifest",
                "--study-manifest",
                "file",
                validator="validate_study_manifest",
            ),
            _Argument(
                "scenario_manifests",
                "--scenario-manifests",
                "file",
                validator="validate_scenario_manifests_input",
            ),
            _Argument(
                "experiment_designs",
                "--experiment-designs",
                "file",
                validator="validate_experiment_designs_input",
            ),
            _Argument(
                "diagnosis",
                "--diagnosis",
                "file",
                validator="validate_diagnosis",
            ),
            _Argument(
                "attribute_registry",
                "--attribute-registry",
                "file",
                validator="validate_creative_attribute_registry",
            ),
            _Argument(
                "evidence_library_snapshot",
                "--evidence-library-snapshot",
                "file",
                validator="validate_evidence_library_snapshot_input",
            ),
            _Argument(
                "evidence_head_receipt",
                "--evidence-head-receipt",
                "file",
                validator="validate_evidence_head_receipt_input",
            ),
            _Argument(
                "alternative_causes",
                "--alternative-causes",
                "file",
                validator="validate_alternative_causes_input",
            ),
            _Argument("candidate_id", "--candidate-id", "literal"),
            _Argument("candidate_version", "--candidate-version", "literal"),
            _Argument("created_at", "--created-at", "literal"),
        ),
        source_manifest=(
            "audience_panel_builder/population/experimental_calibration/"
            "private_stage_manifests/materialize.json"
        ),
        output_flag="--output-dir",
        output_kind="directory",
        namespace_packages=(
            "audience_lab",
            "audience_panel_builder",
            "audience_panel_builder/population",
            "audience_panel_builder/population/experimental_calibration",
        ),
    ),
    "exercise": _Entrypoint(
        cli="run-synthetic-persona-behavior-exercise.py",
        required_module=(
            "audience_panel_builder/population/experimental_calibration/exercise.py"
        ),
        arguments=(
            _Argument(
                "study_manifest",
                "--study-manifest",
                "file",
                validator="validate_study_manifest",
            ),
            _Argument(
                "public_scenarios_root",
                "--public-scenarios-root",
                "directory",
                validator="validate_public_scenario_tree_input",
            ),
            _Argument(
                "creative_attribute_registry",
                "--creative-attribute-registry",
                "file",
                validator="validate_creative_attribute_registry",
            ),
            _Argument(
                "base_panel",
                "--base-panel",
                "file",
                validator="validate_saved_panel_v3_input",
            ),
            _Argument(
                "candidate_bindings_and_panels",
                "--candidate-bindings-and-panels",
                "file",
                validator="validate_candidate_bindings_and_panels_input",
            ),
            _Argument("exercise_id", "--exercise-id", "literal"),
            _Argument("exercised_at", "--exercised-at", "literal"),
        ),
        source_manifest=(
            "audience_panel_builder/population/experimental_calibration/"
            "private_stage_manifests/exercise.json"
        ),
        external_runtime_modules=("numpy", "scipy"),
        output_flag="--private-stage-output",
        namespace_packages=(
            "audience_lab",
            "audience_panel_builder",
            "audience_panel_builder/population",
            "audience_panel_builder/population/experimental_calibration",
        ),
    ),
}


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _literal(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ContractError(f"{path} must be one non-empty literal string")
    return value


def _real_path(value: object, path: str, *, directory: bool) -> Path:
    text = _literal(os.fspath(value) if isinstance(value, os.PathLike) else value, path)
    candidate = Path(text)
    if not candidate.is_absolute():
        raise ContractError(f"{path} must be an absolute path")
    candidate = _canonical_absolute(candidate)
    _assert_no_symlink_ancestors(candidate, path)
    try:
        entry = os.lstat(candidate)
    except OSError as exc:
        raise ContractError(f"{path} is unavailable") from exc
    if stat.S_ISLNK(entry.st_mode):
        raise ContractError(f"{path} must not be a symlink")
    expected = stat.S_ISDIR(entry.st_mode) if directory else stat.S_ISREG(entry.st_mode)
    if not expected:
        raise ContractError(
            f"{path} must be a real {'directory' if directory else 'file'}"
        )
    return candidate


def _relative_source(path: Path) -> Path:
    for root in (_SCRIPTS_ROOT, _AD_TESTING_SCRIPTS_ROOT):
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    raise ContractError("first-party source escapes the registered scripts roots")


def _source_root(path: Path) -> Path:
    for root in (_SCRIPTS_ROOT, _AD_TESTING_SCRIPTS_ROOT):
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    raise ContractError("first-party source escapes the registered scripts roots")


def _source_path(relative: Path) -> Path:
    root = (
        _AD_TESTING_SCRIPTS_ROOT
        if relative.parts and relative.parts[0] == "audience_lab"
        else _SCRIPTS_ROOT
    )
    return root / relative


def _read_source(path: Path) -> bytes:
    try:
        value = os.lstat(path)
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise EntrypointUnavailable(f"registered source is unavailable: {path}") from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise ContractError(f"registered source must be a real file: {path}")
    if first != second or b"\r" in first or not first.endswith(b"\n"):
        raise ContractError(f"registered source is not stable canonical Python: {path}")
    return first


def _stable_regular_bytes(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        before = os.lstat(path)
        first = path.read_bytes()
        second = path.read_bytes()
        after = os.lstat(path)
    except OSError as exc:
        raise EntrypointUnavailable(
            f"registered file is unavailable: {path}"
        ) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or first != second
        or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
    ):
        raise ContractError(f"registered file is not stable: {path}")
    return first, before


def _resolve_first_party_import(
    *,
    current: Path,
    module: str | None,
    level: int,
) -> list[Path]:
    current_relative = _relative_source(current)
    if level:
        root = _source_root(current)
        package_parts = list(current_relative.parent.parts)
        if current.name == "__init__.py":
            package_parts = list(current_relative.parent.parts)
        for _ in range(level - 1):
            if not package_parts:
                raise ContractError("relative import escapes the staged source package")
            package_parts.pop()
        parts = package_parts + ([] if module is None else module.split("."))
    else:
        if module is None:
            return []
        parts = module.split(".")
        root = (
            _AD_TESTING_SCRIPTS_ROOT
            if parts and parts[0] == "audience_lab"
            else _SCRIPTS_ROOT
        )
    if not parts or parts[0] not in {"audience_panel_builder", "audience_lab"}:
        return []
    base = root.joinpath(*parts)
    candidates = []
    if base.with_suffix(".py").is_file():
        candidates.append(base.with_suffix(".py"))
    if (base / "__init__.py").is_file():
        candidates.append(base / "__init__.py")
    if not candidates:
        raise EntrypointUnavailable(f"declared first-party dependency is unavailable: {module}")
    return candidates


def _package_initializers(
    path: Path,
    omitted: frozenset[str],
) -> list[Path]:
    relative = _relative_source(path)
    root = _source_root(path)
    initializers: list[Path] = []
    parent = relative.parent
    while parent != Path("."):
        if parent.as_posix() in omitted:
            parent = parent.parent
            continue
        initializer = root / parent / "__init__.py"
        if initializer.is_file():
            initializers.append(initializer)
        parent = parent.parent
    return initializers


_DYNAMIC_MODULES = {
    "builtins",
    "importlib",
    "pkgutil",
    "runpy",
    "zipimport",
}
_DYNAMIC_NAMES = {
    "__import__",
    "__builtins__",
    "__dict__",
    "compile",
    "eval",
    "exec",
    "exec_module",
    "find_loader",
    "getattr",
    "get_loader",
    "globals",
    "import_module",
    "load_module",
    "locals",
    "module_from_spec",
    "run_module",
    "run_path",
    "setattr",
    "spec_from_file_location",
    "vars",
}
_ALLOWED_LITERAL_GETATTR = frozenset(
    {
        ("os", "O_CLOEXEC"),
        ("os", "O_NOFOLLOW"),
    }
)
_ALLOWED_LITERAL_VALUE_GETATTR = frozenset({("nit", 0)})


def _exact_module_import_lines(
    tree: ast.AST,
) -> dict[str, int]:
    if not isinstance(tree, ast.Module):
        return {}
    direct_imports: dict[str, list[ast.Import]] = {"os": [], "re": []}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Import)
            and len(statement.names) == 1
            and statement.names[0].name in direct_imports
            and statement.names[0].asname is None
        ):
            direct_imports[statement.names[0].name].append(statement)

    invalid = {name: False for name in direct_imports}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if bound not in invalid:
                    continue
                if not (
                    node in direct_imports[bound]
                    and len(node.names) == 1
                    and alias.name == bound
                    and alias.asname is None
                ):
                    invalid[bound] = True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) in invalid:
                    invalid[alias.asname or alias.name] = True
        elif (
            isinstance(node, ast.Name)
            and node.id in invalid
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            invalid[node.id] = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in invalid:
                invalid[node.name] = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                if node.args.vararg is not None:
                    arguments.append(node.args.vararg)
                if node.args.kwarg is not None:
                    arguments.append(node.args.kwarg)
                for argument in arguments:
                    if argument.arg in invalid:
                        invalid[argument.arg] = True
        elif isinstance(node, ast.Lambda):
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                if argument.arg in invalid:
                    invalid[argument.arg] = True
        elif isinstance(node, ast.ExceptHandler):
            if isinstance(node.name, str) and node.name in invalid:
                invalid[node.name] = True
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if isinstance(node.name, str) and node.name in invalid:
                invalid[node.name] = True
        elif isinstance(node, ast.MatchMapping):
            if isinstance(node.rest, str) and node.rest in invalid:
                invalid[node.rest] = True
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in invalid
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            invalid[node.value.id] = True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in invalid
        ):
            invalid[node.args[0].id] = True

    return {
        name: imports[0].lineno
        for name, imports in direct_imports.items()
        if len(imports) == 1 and not invalid[name]
    }


def _is_allowed_literal_getattr(
    node: ast.AST,
    exact_module_imports: dict[str, int],
) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and not node.keywords
        and len(node.args) == 3
        and isinstance(node.args[0], ast.Name)
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and (node.args[0].id, node.args[1].value)
        in _ALLOWED_LITERAL_GETATTR
        and node.args[0].id in exact_module_imports
        and exact_module_imports[node.args[0].id] < node.lineno
        and isinstance(node.args[2], ast.Constant)
        and type(node.args[2].value) is int
        and node.args[2].value == 0
    )


def _is_allowed_literal_value_getattr(node: ast.AST) -> bool:
    """Allow closed reads of optimizer result metadata, never import authority."""

    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and not node.keywords
        and len(node.args) == 3
        and isinstance(node.args[0], ast.Name)
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and isinstance(node.args[2], ast.Constant)
        and (node.args[1].value, node.args[2].value)
        in _ALLOWED_LITERAL_VALUE_GETATTR
    )


def _is_allowed_re_compile(
    node: ast.AST,
    parent: ast.AST | None,
    exact_module_imports: dict[str, int],
) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "compile"
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
        and "re" in exact_module_imports
        and exact_module_imports["re"] < node.lineno
        and isinstance(parent, ast.Call)
        and parent.func is node
    )


def _assert_no_dynamic_authority(tree: ast.AST, relative: str) -> None:
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    exact_module_imports = _exact_module_import_lines(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".", 1)[0] in _DYNAMIC_MODULES for alias in node.names):
                raise ContractError(
                    f"dynamic source/import authority is forbidden: {relative}"
                )
        elif isinstance(node, ast.ImportFrom):
            if (
                node.module is not None
                and node.module.split(".", 1)[0] in _DYNAMIC_MODULES
            ) or any(alias.name in _DYNAMIC_NAMES for alias in node.names):
                raise ContractError(
                    f"dynamic source/import authority is forbidden: {relative}"
                )
        elif (
            isinstance(node, ast.Name)
            and node.id in _DYNAMIC_NAMES
            and not (
                node.id == "getattr"
                and _is_allowed_literal_getattr(
                    parents.get(id(node)),
                    exact_module_imports,
                )
                or node.id == "getattr"
                and _is_allowed_literal_value_getattr(
                    parents.get(id(node)),
                )
            )
        ):
            raise ContractError(
                f"dynamic source/import authority is forbidden: {relative}"
            )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _DYNAMIC_NAMES
            and not _is_allowed_re_compile(
                node,
                parents.get(id(node)),
                exact_module_imports,
            )
        ):
            raise ContractError(
                f"dynamic source/import authority is forbidden: {relative}"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and not _is_allowed_literal_getattr(
                node,
                exact_module_imports,
            )
            and not _is_allowed_literal_value_getattr(node)
        ):
            raise ContractError(
                f"dynamic source/import authority is forbidden: {relative}"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
        ):
            raise ContractError(
                f"dynamic source/import authority is forbidden: {relative}"
            )


def _discover_closure(
    entry: Path,
    *,
    omitted_initializers: Sequence[str] = (),
) -> list[dict[str, object]]:
    omitted = frozenset(omitted_initializers)
    pending = [entry]
    discovered: dict[str, bytes] = {}
    while pending:
        source = pending.pop()
        relative = _relative_source(source).as_posix()
        if relative in discovered:
            continue
        raw = _read_source(source)
        if _ORACLE_TOKEN.encode("utf-8") in raw or "oracle" in source.name.casefold():
            raise ContractError("engine source closure references the oracle package")
        try:
            tree = ast.parse(raw, filename=str(source))
        except SyntaxError as exc:
            raise ContractError(f"registered source is invalid Python: {relative}") from exc
        _assert_no_dynamic_authority(tree, relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pending.extend(
                        _resolve_first_party_import(
                            current=source,
                            module=alias.name,
                            level=0,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                pending.extend(
                    _resolve_first_party_import(
                        current=source,
                        module=node.module,
                        level=node.level,
                    )
                )
        discovered[relative] = raw
        pending.extend(_package_initializers(source, omitted))
    rows = [
        {
            "path": path,
            "byte_count": len(discovered[path]),
            "raw_bytes_sha256": _digest(discovered[path]),
        }
        for path in sorted(discovered)
    ]
    if any(
        _ORACLE_TOKEN in row["path"]
        or "oracle" in Path(str(row["path"])).name.casefold()
        for row in rows
    ):
        raise ContractError("staged source manifest contains oracle source")
    return rows


def _assert_declared_source_closure(
    declared: Sequence[Mapping[str, object]],
    discovered: Sequence[Mapping[str, object]],
) -> None:
    def normalize(
        rows: Sequence[Mapping[str, object]],
        label: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping) or set(raw) != {
                "path",
                "byte_count",
                "raw_bytes_sha256",
            }:
                raise ContractError(
                    f"{label}[{index}] must match the closed source binding"
                )
            path = raw["path"]
            count = raw["byte_count"]
            digest = raw["raw_bytes_sha256"]
            if (
                not isinstance(path, str)
                or not path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
                or _ORACLE_TOKEN in path
                or "oracle" in Path(path).name.casefold()
            ):
                raise ContractError(f"{label}[{index}].path is unsafe")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ContractError(f"{label}[{index}].byte_count is invalid")
            if (
                not isinstance(digest, str)
                or len(digest) != 71
                or not digest.startswith("sha256:")
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ContractError(
                    f"{label}[{index}].raw_bytes_sha256 is invalid"
                )
            result.append(
                {
                    "path": path,
                    "byte_count": count,
                    "raw_bytes_sha256": digest,
                }
            )
        if [row["path"] for row in result] != sorted(
            row["path"] for row in result
        ):
            raise ContractError(f"{label} must be canonically sorted")
        if len(result) != len({row["path"] for row in result}):
            raise ContractError(f"{label} must contain unique paths")
        return result

    expected = normalize(declared, "declared source files")
    actual = normalize(discovered, "discovered source files")
    if expected != actual:
        raise ContractError(
            "discovered first-party source closure differs from its declaration"
        )


def _load_declared_source_manifest(
    engine_entrypoint: str,
    spec: _Entrypoint,
) -> dict[str, object]:
    path = _SCRIPTS_ROOT / spec.source_manifest
    if not path.is_file():
        raise EntrypointUnavailable(
            f"registered source manifest is unavailable: {engine_entrypoint}"
        )
    raw, _manifest_stat = _stable_regular_bytes(path)
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("registered source manifest is not JSON") from exc
    if canonical_json_bytes(document) != raw:
        raise ContractError("registered source manifest is not canonical JSON")
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "engine_entrypoint",
        "files",
        "source_manifest_sha256",
    }:
        raise ContractError("registered source manifest has an invalid schema")
    if (
        document["schema_version"]
        != "experimental-calibration-source-allowlist-v1"
        or document["engine_entrypoint"] != engine_entrypoint
        or not isinstance(document["files"], list)
    ):
        raise ContractError("registered source manifest binding is invalid")
    supplied = document["source_manifest_sha256"]
    candidate = dict(document)
    candidate["source_manifest_sha256"] = None
    if supplied != _digest(canonical_json_bytes(candidate)):
        raise ContractError("registered source manifest self-hash is stale")
    _assert_declared_source_closure(document["files"], document["files"])
    return document


def _stage_closure(
    rows: Sequence[Mapping[str, object]],
    destination: Path,
) -> None:
    create_new_directory(destination, "private staged source")
    os.chmod(destination, 0o700)
    expected: set[str] = set()
    for row in rows:
        relative = Path(str(row["path"]))
        expected.add(relative.as_posix())
        source = _source_path(relative)
        raw = _read_source(source)
        if (
            len(raw) != row["byte_count"]
            or _digest(raw) != row["raw_bytes_sha256"]
        ):
            raise ContractError(f"source changed before staging: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new_bytes(target, raw, f"staged source {relative}")
        os.chmod(target, 0o400)
    actual = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise ContractError("staged source closure contains missing or extra files")
    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        os.chmod(directory, 0o500)
    os.chmod(destination, 0o500)


def _canonical_absolute(path: Path) -> Path:
    absolute = path.absolute()
    for alias, target in _PROTECTED_SYSTEM_ALIASES.items():
        if alias.is_symlink() and (absolute == alias or alias in absolute.parents):
            return target.joinpath(*absolute.relative_to(alias).parts)
    return absolute


def _assert_no_symlink_ancestors(path: Path, label: str) -> None:
    absolute = _canonical_absolute(path)
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            value = os.lstat(cursor)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ContractError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(value.st_mode):
            raise ContractError(f"{label} must not contain a symlink ancestor")
        if cursor != absolute and not stat.S_ISDIR(value.st_mode):
            raise ContractError(f"{label} has a non-directory ancestor")


_INPUT_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_INPUT_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_absolute_no_follow(path: Path, *, directory: bool) -> int:
    absolute = _canonical_absolute(path)
    if not absolute.is_absolute() or ".." in absolute.parts:
        raise ContractError("input provenance path is unsafe")
    descriptor = os.open(absolute.anchor, _INPUT_DIRECTORY_FLAGS)
    try:
        parts = absolute.parts[1:]
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            flags = (
                _INPUT_DIRECTORY_FLAGS
                if not final or directory
                else _INPUT_FILE_FLAGS
            )
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ContractError(
            "input provenance changed or contains a symlink"
        ) from exc


def _open_input_tree(
    root: Path,
) -> tuple[
    str,
    list[int],
    list[tuple[int, int]],
    list[tuple[str, int, os.stat_result]],
]:
    lexical = os.lstat(root)
    if stat.S_ISLNK(lexical.st_mode):
        raise ContractError("input provenance contains a symlink")
    root_is_directory = stat.S_ISDIR(lexical.st_mode)
    if not root_is_directory and not stat.S_ISREG(lexical.st_mode):
        raise ContractError("input provenance contains an unsupported path")
    root_fd = _open_absolute_no_follow(root, directory=root_is_directory)
    root_value = os.fstat(root_fd)
    if not _same_inode(lexical, root_value):
        os.close(root_fd)
        raise ContractError("input provenance changed during authentication")
    descriptors = [root_fd]
    identities = [(root_value.st_dev, root_value.st_ino)]
    files: list[tuple[str, int, os.stat_result]] = []
    if not root_is_directory:
        files.append(("", root_fd, root_value))
        return "file", descriptors, identities, files

    def walk(directory_fd: int, prefix: str) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise ContractError(
                "input provenance directory is unavailable"
            ) from exc
        for name in names:
            if not name or "/" in name or name in {".", ".."}:
                raise ContractError("input provenance member name is unsafe")
            relative = name if not prefix else f"{prefix}/{name}"
            try:
                lexical_member = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ContractError(
                    "input provenance changed during authentication"
                ) from exc
            if stat.S_ISLNK(lexical_member.st_mode):
                raise ContractError("input provenance contains a symlink")
            if stat.S_ISDIR(lexical_member.st_mode):
                flags = _INPUT_DIRECTORY_FLAGS
            elif stat.S_ISREG(lexical_member.st_mode):
                flags = _INPUT_FILE_FLAGS
            else:
                raise ContractError(
                    "input provenance contains an unsupported path"
                )
            try:
                member_fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise ContractError(
                    "input provenance changed during authentication"
                ) from exc
            descriptors.append(member_fd)
            checked = os.fstat(member_fd)
            if not _same_inode(lexical_member, checked):
                raise ContractError(
                    "input provenance changed during authentication"
                )
            identities.append((checked.st_dev, checked.st_ino))
            if stat.S_ISDIR(checked.st_mode):
                walk(member_fd, relative)
            else:
                files.append((relative, member_fd, checked))

    try:
        walk(root_fd, "")
    except BaseException:
        while descriptors:
            os.close(descriptors.pop())
        raise
    return "directory", descriptors, identities, files


def _closed_tree_identities(root: Path) -> set[tuple[int, int]]:
    _kind, descriptors, identities, _files = _open_input_tree(root)
    try:
        return set(identities)
    finally:
        while descriptors:
            os.close(descriptors.pop())


def _assert_original_input_admissible(
    source: Path,
    denied_roots: Sequence[Path],
) -> _AuthenticatedInput:
    candidate = Path(source)
    if not candidate.is_absolute():
        raise ContractError("role input source must be an absolute path")
    candidate = _canonical_absolute(candidate)
    _assert_no_symlink_ancestors(candidate, "role input source")
    normalized_denied: list[Path] = []
    for raw_denied in denied_roots:
        denied = Path(raw_denied)
        if not denied.is_absolute():
            raise ContractError("oracle-denied root must be absolute")
        blocked = _canonical_absolute(denied)
        normalized_denied.append(blocked)
        if (
            candidate == blocked
            or candidate in blocked.parents
            or blocked in candidate.parents
        ):
            raise ContractError(
                "original role input overlaps an oracle-denied root"
            )
    denied_identities: set[tuple[int, int]] = set()
    for blocked in normalized_denied:
        if blocked.exists() or blocked.is_symlink():
            denied_identities.update(_closed_tree_identities(blocked))
    kind, descriptors, identities, files = _open_input_tree(candidate)
    if set(identities) & denied_identities:
        while descriptors:
            os.close(descriptors.pop())
        raise ContractError(
            "original role input aliases oracle-denied content"
        )
    return _AuthenticatedInput(
        source_path=candidate,
        kind=kind,
        descriptors=descriptors,
        identities=identities,
        files=files,
        denied_identities=frozenset(denied_identities),
    )


def _stable_descriptor_bytes(
    descriptor: int,
    authenticated: os.stat_result,
    denied_identities: frozenset[tuple[int, int]],
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or not _same_inode(
        authenticated,
        before,
    ):
        raise ContractError("role input file changed during authentication")
    if (before.st_dev, before.st_ino) in denied_identities:
        raise ContractError("role input file aliases oracle-denied content")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ContractError("role input file changed during authentication")
    return b"".join(chunks), before


def _snapshot_admitted_path(
    source: _AuthenticatedInput,
) -> dict[str, object]:
    files: list[tuple[str, bytes]] = []
    for relative, descriptor, authenticated in source.files:
        raw, _checked = _stable_descriptor_bytes(
            descriptor,
            authenticated,
            source.denied_identities,
        )
        files.append((relative, raw))
    if source.kind == "file":
        checked = source.files[0][2]
        identity_preimage: dict[str, object] = {
            "device": checked.st_dev,
            "inode": checked.st_ino,
            "kind": "file",
        }
    else:
        identity_preimage = {
            "identities": [
                {"device": device, "inode": inode}
                for device, inode in sorted(source.identities)
            ],
            "kind": "directory",
        }
    return {
        "kind": source.kind,
        "identity_sha256": _digest(
            canonical_json_bytes(identity_preimage)
        ),
        "files": files,
    }


def _role_validator(name: str | None):
    if not name:
        raise EntrypointUnavailable(
            "registered role has no complete validator"
        )
    if name == "validate_saved_panel_v3_input":
        if str(_AD_TESTING_SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(_AD_TESTING_SCRIPTS_ROOT))
        from audience_lab.audience_research_v3 import validate_saved_panel_v3

        def validate_saved_panel(value: object) -> dict[str, object]:
            try:
                result = validate_saved_panel_v3(value)
            except ValueError as exc:
                raise ContractError(str(exc)) from exc
            if not isinstance(result, dict):
                raise ContractError(
                    "saved-panel-v3 validator returned an invalid result"
                )
            return result

        return validate_saved_panel
    if name == "validate_public_scenario_tree_input":
        # Directory bytes are validated by _validate_role_snapshot, where the
        # authenticated raw file rows (not just their tree envelope) exist.
        return lambda value: value
    if name in {
        "validate_scenario_manifests_input",
        "validate_experiment_designs_input",
    }:
        hash_field = (
            "manifest_sha256"
            if name == "validate_scenario_manifests_input"
            else "design_sha256"
        )

        def validate_self_hashed_rows(value: object) -> list[dict[str, object]]:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(row, Mapping) for row in value)
            ):
                raise ContractError(
                    "registered role input must be a nonempty object array"
                )
            result = [deepcopy(dict(row)) for row in value]
            for index, row in enumerate(result):
                supplied = row.get(hash_field)
                unhashed = deepcopy(row)
                unhashed[hash_field] = None
                if supplied != _digest(canonical_json_bytes(unhashed)):
                    raise ContractError(
                        f"registered role input row {index} has a stale "
                        f"{hash_field}"
                    )
            return result

        return validate_self_hashed_rows
    if name == "validate_evidence_library_snapshot_input":
        return _engine_contracts.validate_evidence_library
    if name == "validate_evidence_head_receipt_input":
        return _engine_contracts.validate_evidence_receipt
    if name == "validate_alternative_causes_input":
        from audience_panel_builder.population.experimental_calibration.diagnosis import (
            _alternative_causes,
        )

        return _alternative_causes
    if name == "validate_candidate_bindings_and_panels_input":
        from audience_panel_builder.population.experimental_calibration.candidate import (
            _authenticate_materialized,
        )

        def validate_candidate_envelopes(
            value: object,
        ) -> list[dict[str, object]]:
            if (
                not isinstance(value, list)
                or len(value) < 2
                or any(not isinstance(row, dict) for row in value)
            ):
                raise ContractError(
                    "candidate bindings and panels must be a nonempty plural object array"
                )
            result = deepcopy(value)
            candidate_ids: set[str] = set()
            panel_versions: set[tuple[str, str]] = set()
            validated: list[dict[str, object]] = []
            for index, row in enumerate(result):
                envelope = _engine_contracts.validate_candidate_seal_envelope(
                    row
                )
                materialized = envelope["materialized_candidate"]
                binding, panel, _, _ = _authenticate_materialized(
                    materialized
                )
                candidate_id = str(binding["candidate_id"])
                panel_version = (str(panel["panel_id"]), str(panel["version"]))
                if candidate_id in candidate_ids or panel_version in panel_versions:
                    raise ContractError(
                        f"candidate envelope {index} duplicates authority identity"
                    )
                candidate_ids.add(candidate_id)
                panel_versions.add(panel_version)
                validated.append(envelope)
            return validated

        return validate_candidate_envelopes
    validator = getattr(_engine_contracts, name, None)
    if validator is None or not callable(validator):
        raise EntrypointUnavailable(
            f"registered role validator is unavailable: {name}"
        )
    return validator


def _validate_role_snapshot(
    argument: _Argument,
    snapshot: Mapping[str, object],
) -> None:
    files = snapshot["files"]
    assert isinstance(files, list)
    if argument.validator == "validate_public_scenario_tree_input":
        _validate_public_scenario_tree_snapshot(files)
        return
    validator = _role_validator(argument.validator)
    if argument.kind == "file":
        if len(files) != 1 or files[0][0] != "":
            raise ContractError("file role snapshot is invalid")
        raw = files[0][1]
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                f"validated_arguments.{argument.name} is not JSON"
            ) from exc
        if canonical_json_bytes(document) != raw:
            raise ContractError(
                f"validated_arguments.{argument.name} is not canonical JSON"
            )
        checked = validator(document)
        if canonical_json_bytes(checked) != raw:
            raise ContractError(
                f"validated_arguments.{argument.name} changed during validation"
            )
        return
    tree_envelope = {
        "schema_version": "experimental-calibration-input-tree-v1",
        "files": [
            {
                "path": relative,
                "byte_count": len(raw),
                "raw_bytes_sha256": _digest(raw),
            }
            for relative, raw in files
        ],
    }
    validator(tree_envelope)


def _validate_public_scenario_tree_snapshot(
    files: Sequence[tuple[str, bytes]],
) -> None:
    """Authenticate exact public scenario bytes before copying the tree."""

    by_path = {relative: raw for relative, raw in files}
    if (
        len(by_path) != len(files)
        or not by_path
        or any(
            "oracle" in relative.casefold()
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            for relative in by_path
        )
    ):
        raise ContractError(
            "public scenario tree contains duplicate, unsafe, or oracle-named files"
        )
    groups: dict[tuple[str, str], dict[str, bytes]] = {}
    for relative, raw in by_path.items():
        parts = Path(relative).parts
        if len(parts) < 3 or parts[0] not in {"open", "sealed"}:
            raise ContractError(
                "public scenario tree must contain only open/sealed scenario files"
            )
        groups.setdefault((parts[0], parts[1]), {})[
            Path(*parts[2:]).as_posix()
        ] = raw
    scenario_ids: set[str] = set()
    for (partition, directory_name), values in groups.items():
        manifest_raw = values.get("scenario-manifest.json")
        if manifest_raw is None:
            raise ContractError("public scenario tree is missing a scenario manifest")
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("public scenario manifest is not JSON") from exc
        if (
            not isinstance(manifest, dict)
            or canonical_json_bytes(manifest) != manifest_raw
            or manifest.get("partition") != partition
        ):
            raise ContractError("public scenario manifest is not canonical or partition-bound")
        supplied = manifest.get("manifest_sha256")
        unhashed = deepcopy(manifest)
        unhashed["manifest_sha256"] = None
        if supplied != _digest(canonical_json_bytes(unhashed)):
            raise ContractError("public scenario manifest self-hash is stale")
        scenario = manifest.get("scenario_binding")
        scenario_id = (
            str(scenario.get("scenario_id"))
            if isinstance(scenario, Mapping)
            else ""
        )
        if (
            scenario_id != directory_name
            or scenario_id in scenario_ids
            or supplied
            != _engine_contracts.SYNTHETIC_SCENARIO_MANIFEST_SHA256.get(
                scenario_id
            )
        ):
            raise ContractError("public scenario manifest is not the frozen scenario")
        scenario_ids.add(scenario_id)
        bindings = manifest.get("public_file_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ContractError("public scenario manifest has no file bindings")
        expected = {"scenario-manifest.json"}
        for binding in bindings:
            if not isinstance(binding, Mapping) or set(binding) != {
                "path",
                "byte_count",
                "raw_bytes_sha256",
            }:
                raise ContractError("public scenario file binding is not closed")
            relative = binding["path"]
            raw = values.get(str(relative))
            if (
                raw is None
                or "oracle" in str(relative).casefold()
                or binding["byte_count"] != len(raw)
                or binding["raw_bytes_sha256"] != _digest(raw)
            ):
                raise ContractError("public scenario file binding is stale")
            expected.add(str(relative))
        if set(values) != expected:
            raise ContractError(
                "public scenario tree contains files outside its exact manifest"
            )
        design_raw = values.get("experiment-design.json")
        try:
            design = json.loads(design_raw) if design_raw is not None else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("public experiment design is not JSON") from exc
        if (
            not isinstance(design, dict)
            or canonical_json_bytes(design) != design_raw
        ):
            raise ContractError("public experiment design is not canonical")
        claimed_design = design.get("design_sha256")
        unhashed_design = deepcopy(design)
        unhashed_design["design_sha256"] = None
        if claimed_design != _digest(canonical_json_bytes(unhashed_design)):
            raise ContractError("public experiment design self-hash is stale")
    if scenario_ids != set(_engine_contracts.SYNTHETIC_SCENARIO_MANIFEST_SHA256):
        raise ContractError("public scenario tree must contain all frozen scenarios")


def _copy_admitted_snapshot(
    snapshot: Mapping[str, object],
    destination: Path,
) -> None:
    files = snapshot["files"]
    assert isinstance(files, list)
    if snapshot["kind"] == "file":
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new_bytes(
            destination,
            files[0][1],
            f"staged input {destination.name}",
        )
        os.chmod(destination, 0o400)
        return
    create_new_directory(destination, f"staged input {destination.name}")
    for relative, raw in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new_bytes(target, raw, f"staged input {relative}")
        os.chmod(target, 0o400)
    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        os.chmod(directory, 0o500)
    os.chmod(destination, 0o500)


def _role_input_binding(
    argument: _Argument,
    snapshot: Mapping[str, object],
    copied_relative: str,
) -> dict[str, object]:
    files = snapshot["files"]
    assert isinstance(files, list)
    binding: dict[str, object] = {
        "role": argument.name,
        "kind": snapshot["kind"],
        "validator": argument.validator,
        "source_identity_sha256": snapshot["identity_sha256"],
        "copied_path": copied_relative,
        "copied_files": [
            {
                "path": (
                    copied_relative
                    if relative == ""
                    else f"{copied_relative}/{relative}"
                ),
                "byte_count": len(raw),
                "raw_bytes_sha256": _digest(raw),
            }
            for relative, raw in files
        ],
        "role_input_sha256": None,
    }
    binding["role_input_sha256"] = _digest(canonical_json_bytes(binding))
    return binding


def _profile_literal(path: Path) -> str:
    text = str(path)
    if "\x00" in text or "\r" in text or "\n" in text:
        raise ContractError("sandbox path is not one literal path")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _system_read_roots(interpreter: Path) -> list[Path]:
    candidates = {
        Path("/System"),
        Path("/Library"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc/ld.so.cache"),
        Path("/etc/ld.so.conf"),
        Path("/etc/ld.so.conf.d"),
        Path("/etc/localtime"),
        Path(sys.base_prefix).resolve(strict=True),
        interpreter.resolve(strict=True).parent,
    }
    return sorted(
        (path for path in candidates if path.exists()),
        key=lambda path: str(path),
    )


def _assert_protected_system_path(path: Path) -> Path:
    absolute = path.absolute()
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise ProducerRuntimeUnavailable(
            f"macOS system runtime path is unavailable: {absolute}"
        ) from exc
    cursor = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        cursor /= part
        try:
            value = os.lstat(cursor)
        except OSError as exc:
            raise ProducerRuntimeUnavailable(
                f"macOS system runtime path is unavailable: {cursor}"
            ) from exc
        if (
            stat.S_ISLNK(value.st_mode)
            or value.st_uid != 0
            or value.st_mode & 0o022
        ):
            raise ProducerRuntimeUnavailable(
                f"macOS system runtime path is not protected: {cursor}"
            )
    return resolved


def _assert_bound_runtime_path(path: Path) -> Path:
    absolute = path.absolute()
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise ProducerRuntimeUnavailable(
            f"bound Python runtime path is unavailable: {absolute}"
        ) from exc
    try:
        value = os.lstat(resolved)
    except OSError as exc:
        raise ProducerRuntimeUnavailable(
            f"bound Python runtime path is unavailable: {resolved}"
        ) from exc
    if stat.S_ISLNK(value.st_mode) or value.st_mode & 0o022:
        raise ProducerRuntimeUnavailable(
            f"bound Python runtime path is group/world writable: {resolved}"
        )
    return resolved


def _runtime_probe_document(interpreter: Path) -> dict[str, object]:
    probe = (
        "import json,sys,sysconfig\n"
        "print(json.dumps({"
        "'base_prefix':sys.base_prefix,"
        "'executable':sys.executable,"
        "'platstdlib':sysconfig.get_path('platstdlib'),"
        "'stdlib':sysconfig.get_path('stdlib'),"
        "'version':list(sys.version_info[:3])"
        "},sort_keys=True,separators=(',',':')))\n"
    )
    try:
        completed = subprocess.run(
            [str(interpreter), "-I", "-B", "-c", probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            cwd="/",
            close_fds=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProducerRuntimeUnavailable(
            "bound Python runtime could not be authenticated"
        ) from exc
    if completed.returncode != 0:
        raise ProducerRuntimeUnavailable(
            "bound Python runtime probe failed"
        )
    try:
        document = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProducerRuntimeUnavailable(
            "bound Python runtime probe was invalid"
        ) from exc
    if not isinstance(document, dict) or set(document) != {
        "base_prefix",
        "executable",
        "platstdlib",
        "stdlib",
        "version",
    }:
        raise ProducerRuntimeUnavailable(
            "bound Python runtime probe was incomplete"
        )
    version = document["version"]
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(type(value) is not int or value < 0 for value in version)
    ):
        raise ProducerRuntimeUnavailable(
            "bound Python runtime version is invalid"
        )
    if tuple(version[:2]) < (3, 11):
        raise ProducerRuntimeUnavailable(
            "bound Python runtime must be Python 3.11 or newer"
        )
    for key in ("base_prefix", "executable", "platstdlib", "stdlib"):
        raw = document[key]
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ProducerRuntimeUnavailable(
                f"bound Python runtime {key} is invalid"
            )
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise ProducerRuntimeUnavailable(
                f"bound Python runtime {key} is not absolute"
            )
    return document


def _private_stage_runtime_binding(system: str) -> dict[str, object]:
    if system not in {"Darwin", "Linux"}:
        raise ProducerRuntimeUnavailable(
            f"unsupported sandbox platform: {system!r}"
        )
    interpreter = _bound_interpreter().absolute()
    resolved_interpreter = _assert_bound_runtime_path(interpreter)
    document = _runtime_probe_document(interpreter)
    version = document.get("version")
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(type(value) is not int or value < 0 for value in version)
        or tuple(version[:2]) < (3, 11)
    ):
        raise ProducerRuntimeUnavailable(
            "bound Python runtime must be Python 3.11 or newer"
        )
    reported_executable = _assert_bound_runtime_path(
        Path(str(document["executable"]))
    )
    if reported_executable != resolved_interpreter:
        raise ProducerRuntimeUnavailable(
            "bound Python runtime reported a different executable"
        )
    runtime_roots = []
    seen_runtime_roots: set[tuple[str, str]] = set()
    for key in ("base_prefix", "platstdlib", "stdlib"):
        lexical = Path(str(document[key])).absolute()
        resolved = _assert_bound_runtime_path(lexical)
        identity = (str(lexical), str(resolved))
        if identity in seen_runtime_roots:
            continue
        seen_runtime_roots.add(identity)
        value = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISDIR(value.st_mode):
            raise ProducerRuntimeUnavailable(
                f"bound Python runtime root is not a directory: {resolved}"
            )
        runtime_roots.append(
            {
                "path": str(lexical),
                "resolved_path": str(resolved),
                "device": value.st_dev,
                "inode": value.st_ino,
            }
        )
    runtime_roots.sort(key=lambda row: (row["path"], row["resolved_path"]))
    try:
        executable_raw = resolved_interpreter.read_bytes()
    except OSError as exc:
        raise ProducerRuntimeUnavailable(
            "bound Python executable could not be authenticated"
        ) from exc
    binding: dict[str, object] = {
        "schema_version": "experimental-calibration-python-runtime-v1",
        "platform": system,
        "interpreter_path": str(interpreter),
        "resolved_interpreter_path": str(resolved_interpreter),
        "python_version": list(document["version"]),
        "executable_sha256": _digest(executable_raw),
        "runtime_roots": runtime_roots,
        "external_dependency_files": [],
        "runtime_binding_sha256": None,
    }
    binding["runtime_binding_sha256"] = _digest(
        canonical_json_bytes(binding)
    )
    return binding


def _bind_external_runtime(
    base: Mapping[str, object],
    external_files: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    binding = json.loads(canonical_json_bytes(dict(base)))
    if binding.get("external_dependency_files") != []:
        raise ContractError(
            "base runtime binding already contains external dependencies"
        )
    binding["external_dependency_files"] = [
        dict(row) for row in external_files
    ]
    binding["runtime_binding_sha256"] = None
    binding["runtime_binding_sha256"] = _digest(
        canonical_json_bytes(binding)
    )
    return binding


def _published_runtime_binding(
    private_binding: Mapping[str, object],
) -> dict[str, object]:
    """Project provider-private runtime authority to a path-free public record."""

    required = {
        "platform",
        "python_version",
        "executable_sha256",
        "external_dependency_files",
    }
    if not required.issubset(private_binding):
        raise ProducerRuntimeUnavailable(
            "private-stage runtime binding is incomplete"
        )
    external = private_binding["external_dependency_files"]
    if not isinstance(external, list) or any(
        not isinstance(row, Mapping) for row in external
    ):
        raise ProducerRuntimeUnavailable(
            "private-stage external dependency binding is invalid"
        )
    external_rows = [dict(row) for row in external]
    for row in external_rows:
        path = row.get("path")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ProducerRuntimeUnavailable(
                "published external dependency path is unsafe"
            )
        if set(row) != {
            "path",
            "distribution",
            "distribution_version",
            "byte_count",
            "raw_bytes_sha256",
        }:
            raise ProducerRuntimeUnavailable(
                "published external dependency binding is incomplete"
            )
    external_rows.sort(
        key=lambda row: (
            str(row["distribution"]),
            str(row["path"]),
        )
    )
    binding: dict[str, object] = {
        "schema_version": "experimental-calibration-published-runtime-v1",
        "provider": {
            "name": "protected-private-stage",
            "version": "1",
        },
        "platform": private_binding["platform"],
        "interpreter": {
            "implementation": "CPython",
            "python_version": private_binding["python_version"],
            "executable_sha256": private_binding["executable_sha256"],
        },
        "external_dependency_files": external_rows,
        "external_dependency_files_sha256": _digest(
            canonical_json_bytes(external_rows)
        ),
        "runtime_binding_sha256": None,
    }
    binding["runtime_binding_sha256"] = _digest(
        canonical_json_bytes(binding)
    )
    return binding


def _phase_execution_receipt(
    *,
    engine_entrypoint: str,
    output_kind: str,
    output_sha256: str,
    source_manifest: Mapping[str, object],
    input_manifest: Mapping[str, object],
    runtime_binding: Mapping[str, object],
) -> dict[str, object]:
    """Build the path-free, persistent receipt for one released phase."""

    output_name = "result" if output_kind == "directory" else "result.json"
    receipt: dict[str, object] = {
        "schema_version": (
            "experimental-calibration-phase-execution-receipt-v1"
        ),
        "engine_entrypoint": engine_entrypoint,
        "arguments_sha256": input_manifest["arguments_sha256"],
        "admitted_input_tree_sha256": input_manifest["roles_sha256"],
        "input_manifest_sha256": input_manifest["input_manifest_sha256"],
        "first_party_source_closure_sha256": source_manifest[
            "first_party_files_sha256"
        ],
        "external_dependency_closure_sha256": source_manifest[
            "external_runtime_files_sha256"
        ],
        "source_manifest_sha256": source_manifest[
            "source_manifest_sha256"
        ],
        "runtime_binding_sha256": runtime_binding[
            "runtime_binding_sha256"
        ],
        "output": {
            "kind": output_kind,
            "name": output_name,
            "output_sha256": output_sha256,
        },
        "phase_execution_receipt_sha256": None,
    }
    receipt["phase_execution_receipt_sha256"] = _digest(
        canonical_json_bytes(receipt)
    )
    return receipt


def _validate_private_stage_runtime_binding(
    value: Mapping[str, object],
    system: str,
) -> dict[str, object]:
    binding = json.loads(canonical_json_bytes(dict(value)))
    expected = _private_stage_runtime_binding(system)
    external = binding.get("external_dependency_files")
    if not isinstance(external, list) or any(
        not isinstance(row, dict) for row in external
    ):
        raise ProducerRuntimeUnavailable(
            "private-stage external dependency binding is invalid"
        )
    for key, expected_value in expected.items():
        if key in {"external_dependency_files", "runtime_binding_sha256"}:
            continue
        if binding.get(key) != expected_value:
            raise ProducerRuntimeUnavailable(
                f"private-stage runtime binding changed at {key}"
            )
    claimed = binding.get("runtime_binding_sha256")
    binding["runtime_binding_sha256"] = None
    if claimed != _digest(canonical_json_bytes(binding)):
        raise ProducerRuntimeUnavailable(
            "private-stage runtime binding self-hash is stale"
        )
    binding["runtime_binding_sha256"] = claimed
    return binding


def _macos_runtime_read_paths(
    interpreter: Path,
    runtime_binding: Mapping[str, object] | None = None,
) -> list[Path]:
    protected_candidates = (
        Path("/System/Library"),
        Path("/usr/lib"),
        Path("/usr/share/zoneinfo"),
        Path("/private/etc"),
        Path("/private/var/db/dyld"),
    )
    protected = [
        _assert_protected_system_path(path)
        for path in protected_candidates
        if path.exists()
    ]
    random_device = Path("/dev/urandom")
    try:
        random_value = os.lstat(random_device)
    except OSError as exc:
        raise ProducerRuntimeUnavailable(
            "protected macOS entropy device is unavailable"
        ) from exc
    if not stat.S_ISCHR(random_value.st_mode) or random_value.st_uid != 0:
        raise ProducerRuntimeUnavailable(
            "protected macOS entropy device is invalid"
        )
    protected.append(random_device)
    binding = dict(
        runtime_binding
        if runtime_binding is not None
        else _private_stage_runtime_binding("Darwin")
    )
    if binding.get("resolved_interpreter_path") != str(
        interpreter.resolve(strict=True)
    ):
        raise ProducerRuntimeUnavailable(
            "macOS profile interpreter does not match its runtime binding"
        )
    resolved_interpreter = _assert_bound_runtime_path(interpreter)
    if binding.get("resolved_interpreter_path") != str(resolved_interpreter):
        raise ProducerRuntimeUnavailable(
            "macOS profile resolved interpreter binding is stale"
        )
    runtime_roots: list[Path] = []
    for row in binding.get("runtime_roots", []):
        if not isinstance(row, Mapping):
            continue
        lexical = Path(str(row.get("path", "")))
        resolved = _assert_bound_runtime_path(lexical)
        value = os.stat(resolved, follow_symlinks=False)
        if (
            str(resolved) != row.get("resolved_path")
            or value.st_dev != row.get("device")
            or value.st_ino != row.get("inode")
        ):
            raise ProducerRuntimeUnavailable(
                "macOS profile runtime-root binding is stale"
            )
        runtime_roots.extend((lexical, resolved))
    if len(runtime_roots) != 2 * len(binding.get("runtime_roots", [])):
        raise ProducerRuntimeUnavailable(
            "macOS profile runtime-root binding is invalid"
        )
    return sorted(
        {
            Path(str(binding["interpreter_path"])),
            interpreter.absolute(),
            resolved_interpreter,
            *runtime_roots,
            *protected,
        },
        key=str,
    )


def _private_stage_interpreter(system: str) -> Path:
    binding = _private_stage_runtime_binding(system)
    return Path(str(binding["resolved_interpreter_path"]))


def _external_runtime_closure(
    spec: _Entrypoint,
    private_root: Path,
) -> tuple[list[dict[str, object]], list[Path]]:
    """Stage the exact internally resolved pinned distribution file closure."""

    if not spec.external_runtime_modules:
        return [], []
    requirements = _AD_TESTING_SCRIPTS_ROOT.parent / "requirements-screening.txt"
    try:
        requirement_lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EntrypointUnavailable(
            "pinned screening requirements are unavailable"
        ) from exc
    pinned: dict[str, str] = {}
    for line in requirement_lines:
        if not line or line.startswith("#") or line.count("==") != 1:
            raise EntrypointUnavailable(
                "pinned screening requirements must contain exact name==version rows"
            )
        name, version = line.split("==", 1)
        pinned[name] = version
    if set(spec.external_runtime_modules) != set(pinned):
        raise EntrypointUnavailable(
            "registered external modules do not match pinned requirements"
        )
    destination_root = private_root / "external-runtime"
    destination_root.mkdir(mode=0o700)
    rows_by_path: dict[str, dict[str, object]] = {}
    try:
        for module in spec.external_runtime_modules:
            distribution = importlib.metadata.distribution(module)
            if distribution.version != pinned[module]:
                raise EntrypointUnavailable(
                    f"external runtime requires {module}=={pinned[module]}"
                )
            distribution_root = Path(
                distribution.locate_file("")
            ).resolve(strict=True)
            files = distribution.files
            if not files:
                raise EntrypointUnavailable(
                    f"external runtime distribution has no file inventory: {module}"
                )
            for package_path in files:
                relative = Path(str(package_path))
                if relative.is_absolute() or not relative.parts:
                    raise EntrypointUnavailable(
                        f"external runtime has unsafe file inventory: {module}"
                    )
                # Wheel metadata may list console scripts in the virtual
                # environment's bin directory (for example NumPy's f2py).
                # They are not import-time runtime dependencies and are never
                # admitted to the private stage.
                if ".." in relative.parts:
                    continue
                source = Path(
                    distribution.locate_file(package_path)
                ).resolve(strict=True)
                if (
                    source != distribution_root
                    and distribution_root not in source.parents
                ):
                    raise EntrypointUnavailable(
                        f"external runtime file escapes distribution root: {module}"
                    )
                source_stat = source.stat(follow_symlinks=False)
                if not stat.S_ISREG(source_stat.st_mode):
                    continue
                raw = source.read_bytes()
                target = destination_root / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists():
                    if target.read_bytes() != raw:
                        raise EntrypointUnavailable(
                            "external distributions disagree on staged file bytes"
                        )
                else:
                    write_new_bytes(
                        target,
                        raw,
                        f"external runtime file {relative.as_posix()}",
                    )
                    os.chmod(target, 0o400)
                row = {
                    "path": relative.as_posix(),
                    "distribution": module,
                    "distribution_version": distribution.version,
                    "byte_count": len(raw),
                    "raw_bytes_sha256": _digest(raw),
                }
                existing = rows_by_path.get(relative.as_posix())
                if existing is not None and (
                    existing["raw_bytes_sha256"] != row["raw_bytes_sha256"]
                    or existing["byte_count"] != row["byte_count"]
                ):
                    raise EntrypointUnavailable(
                        "external runtime staged path collision is inconsistent"
                    )
                rows_by_path[relative.as_posix()] = row
    except importlib.metadata.PackageNotFoundError as exc:
        raise EntrypointUnavailable(
            "exact pinned NumPy/SciPy runtime is unavailable"
        ) from exc
    for directory in sorted(
        (path for path in destination_root.rglob("*") if path.is_dir()),
        reverse=True,
    ):
        os.chmod(directory, 0o500)
    os.chmod(destination_root, 0o500)
    rows = [rows_by_path[path] for path in sorted(rows_by_path)]
    if not rows:
        raise EntrypointUnavailable("external runtime closure is empty")
    return rows, [destination_root]


def _macos_profile(
    *,
    interpreter: Path,
    runtime_binding: Mapping[str, object],
    admitted: Sequence[Path],
    denied: Sequence[Path],
    output_stage: Path,
    output_is_directory: bool = False,
) -> str:
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow signal)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        # Python resolves the fixed cwd "/" during startup. This grants only
        # the root directory inode, never descendant file content.
        '(allow file-read-data (literal "/"))',
    ]
    allowed_paths = [
        *_macos_runtime_read_paths(interpreter, runtime_binding),
        *admitted,
        output_stage.parent,
    ]
    metadata_paths: set[Path] = set()
    for path in allowed_paths:
        metadata_paths.add(path)
        metadata_paths.update(path.parents)
    for path in sorted(metadata_paths, key=str):
        rules.append(
            f'(allow file-read-metadata (literal "{_profile_literal(path)}"))'
        )
    for path in allowed_paths:
        operator = "subpath" if path.is_dir() else "literal"
        rules.append(
            f'(allow file-read* ({operator} "{_profile_literal(path)}"))'
        )
    if output_is_directory:
        writable_root = output_stage.parent
        rules.append(
            f'(allow file-write* (literal "{_profile_literal(writable_root)}"))'
        )
        rules.append(
            f'(allow file-write* (subpath "{_profile_literal(writable_root)}"))'
        )
    else:
        rules.append(
            f'(allow file-write* (literal "{_profile_literal(output_stage)}"))'
        )
    for path in denied:
        operator = "subpath" if path.is_dir() else "literal"
        rules.append(
            f'(deny file-read* ({operator} "{_profile_literal(path)}"))'
        )
    return "\n".join(rules) + "\n"


def _linux_vector(
    *,
    provider: Path,
    child: Sequence[str],
    interpreter: Path,
    admitted: Sequence[Path],
    output_stage: Path,
    output_is_directory: bool = False,
) -> list[str]:
    vector = [
        str(provider),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    roots = _system_read_roots(interpreter)
    for path in roots:
        vector.extend(["--ro-bind", str(path), str(path)])
    for path in admitted:
        vector.extend(["--ro-bind", str(path), str(path)])
    vector.extend(
        [
            "--bind",
            str(output_stage.parent if output_is_directory else output_stage),
            str(output_stage.parent if output_is_directory else output_stage),
            "--chdir",
            "/",
            "--",
            *child,
        ]
    )
    return vector


def _assert_disjoint(
    admitted: Sequence[Path],
    denied: Sequence[Path],
) -> None:
    for allowed in admitted:
        for blocked in denied:
            if (
                allowed == blocked
                or allowed in blocked.parents
                or blocked in allowed.parents
            ):
                raise ContractError("admitted and oracle-denied roots must be disjoint")


def _authenticate_candidate_bundle_tree(
    root: Path,
) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise ContractError(
            "private-stage candidate output must be one real directory"
        )
    members = list(root.iterdir())
    if (
        {path.name for path in members} != _CANDIDATE_BUNDLE_FILES
        or any(not path.is_file() or path.is_symlink() for path in members)
    ):
        raise ContractError(
            "private-stage candidate bundle has missing, extra, or unsafe files"
        )
    payloads = {path.name: path.read_bytes() for path in members}
    if any(not raw or len(raw) > _MAX_OUTPUT_BYTES for raw in payloads.values()):
        raise ContractError(
            "private-stage candidate bundle file is empty or too large"
        )
    try:
        manifest = json.loads(payloads["bundle-manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "private-stage candidate bundle manifest is not JSON"
        ) from exc
    if canonical_json_bytes(manifest) != payloads["bundle-manifest.json"]:
        raise ContractError(
            "private-stage candidate bundle manifest is not canonical JSON"
        )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema_version",
            "candidate_id",
            "registration_permitted",
            "production_package_manifest_present",
            "production_package_graph_present",
            "files",
            "bundle_manifest_sha256",
        }
        or manifest["schema_version"]
        != "experimental-persona-candidate-bundle-manifest-v1"
        or type(manifest["registration_permitted"]) is not bool
        or manifest["registration_permitted"] is not False
        or type(manifest["production_package_manifest_present"]) is not bool
        or manifest["production_package_manifest_present"] is not False
        or type(manifest["production_package_graph_present"]) is not bool
        or manifest["production_package_graph_present"] is not False
        or not isinstance(manifest["candidate_id"], str)
        or not manifest["candidate_id"]
        or not isinstance(manifest["files"], list)
    ):
        raise ContractError(
            "private-stage candidate bundle manifest has an invalid schema"
        )
    supplied_manifest_hash = manifest["bundle_manifest_sha256"]
    unhashed_manifest = dict(manifest)
    unhashed_manifest["bundle_manifest_sha256"] = None
    if supplied_manifest_hash != _digest(
        canonical_json_bytes(unhashed_manifest)
    ):
        raise ContractError(
            "private-stage candidate bundle manifest hash is stale"
        )
    expected_payload_names = sorted(
        _CANDIDATE_BUNDLE_FILES - {"bundle-manifest.json"}
    )
    rows = manifest["files"]
    if (
        len(rows) != len(expected_payload_names)
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"path", "sha256", "byte_count"}
            for row in rows
        )
        or [row["path"] for row in rows] != expected_payload_names
    ):
        raise ContractError(
            "private-stage candidate bundle file manifest is not closed"
        )
    for row in rows:
        path = str(row["path"])
        raw = payloads[path]
        if (
            row["byte_count"] != len(raw)
            or row["sha256"] != _digest(raw)
        ):
            raise ContractError(
                f"private-stage candidate bundle file binding is stale: {path}"
            )
    receipt: dict[str, object] = {
        "schema_version":
            "experimental-calibration-directory-output-receipt-v1",
        "candidate_id": manifest["candidate_id"],
        "files": [
            {
                "path": name,
                "byte_count": len(payloads[name]),
                "raw_bytes_sha256": _digest(payloads[name]),
            }
            for name in sorted(payloads)
        ],
        "tree_sha256": None,
    }
    receipt["tree_sha256"] = _digest(canonical_json_bytes(receipt))
    return receipt


def _run_provider_command(
    *,
    source: str,
    arguments: Sequence[str],
    admitted_read_paths: Sequence[Path],
    denied_roots: Sequence[Path],
    output_path: Path,
    environment: Mapping[str, str] | None = None,
    runtime_binding: Mapping[str, object] | None = None,
    output_kind: str = "json_file",
) -> dict[str, object]:
    """Run one fixed child vector through the exact protected OS provider."""

    if not isinstance(source, str) or not source:
        raise ContractError("private provider source must be a non-empty string")
    if output_kind not in {"json_file", "directory"}:
        raise ContractError("private provider output kind is invalid")
    args = [_literal(value, f"arguments[{index}]") for index, value in enumerate(arguments)]
    admitted = [
        _real_path(path, f"admitted_read_paths[{index}]", directory=Path(path).is_dir())
        for index, path in enumerate(admitted_read_paths)
    ]
    denied = []
    for index, path in enumerate(denied_roots):
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ContractError(f"denied_roots[{index}] must be absolute")
        denied.append(candidate.resolve(strict=False))
    _assert_disjoint(admitted, denied)
    requested_output = Path(output_path).absolute()
    if requested_output.exists() or requested_output.is_symlink():
        raise ContractError("private provider output already exists")
    if requested_output.parent.is_symlink():
        raise ContractError("private provider output parent must not be a symlink")

    provider = _trusted_provider()
    system = platform.system()
    binding = _validate_private_stage_runtime_binding(
        dict(
            runtime_binding
            if runtime_binding is not None
            else _private_stage_runtime_binding(system)
        ),
        system,
    )
    interpreter = Path(str(binding.get("resolved_interpreter_path", "")))
    if not interpreter.is_absolute():
        raise ProducerRuntimeUnavailable(
            "private-stage runtime binding has no absolute interpreter"
        )
    with tempfile.TemporaryDirectory(prefix="calibration-private-output-") as raw:
        stage_root = Path(raw).resolve(strict=True)
        os.chmod(stage_root, 0o700)
        if output_kind == "directory":
            publication_root = stage_root / "publication"
            publication_root.mkdir(mode=0o700)
            staged_output = publication_root / "result"
        else:
            staged_output = stage_root / "result.json"
            write_new_bytes(staged_output, b"", "private provider output inode")
        replaced_args = [
            str(staged_output) if value == str(requested_output) else value
            for value in args
        ]
        child = [
            str(interpreter),
            "-I",
            "-B",
            "-c",
            source,
            *replaced_args,
        ]
        fixed_environment = {
            "HOME": str(stage_root),
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(stage_root),
            "TMP": str(stage_root),
            "TMPDIR": str(stage_root),
        }
        for key, value in dict(environment or {}).items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\x00" in key
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ContractError("private provider environment is not literal")
            fixed_environment[key] = value
        if system == "Darwin":
            vector = [
                str(provider.path),
                "-p",
                _macos_profile(
                    interpreter=interpreter,
                    runtime_binding=binding,
                    admitted=admitted,
                    denied=denied,
                    output_stage=staged_output,
                    output_is_directory=output_kind == "directory",
                ),
                *child,
            ]
        elif system == "Linux":
            vector = _linux_vector(
                provider=provider.path,
                child=child,
                interpreter=interpreter,
                admitted=admitted,
                output_stage=staged_output,
                output_is_directory=output_kind == "directory",
            )
        else:
            raise ProducerRuntimeUnavailable(
                f"unsupported sandbox platform: {system!r}"
            )
        try:
            completed = subprocess.run(
                vector,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=fixed_environment,
                cwd="/",
                close_fds=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProducerRuntimeUnavailable(
                "protected provider could not execute private stage"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise ContractError(
                f"private staged engine failed with exit {completed.returncode}: {detail}"
            )
        if output_kind == "directory":
            members = list(publication_root.iterdir())
            if (
                members != [staged_output]
                or not staged_output.is_dir()
                or staged_output.is_symlink()
            ):
                raise ContractError(
                    "private stage did not create exactly one output directory"
                )
            receipt = _authenticate_candidate_bundle_tree(staged_output)
            shutil.copytree(staged_output, requested_output)
            copied_receipt = _authenticate_candidate_bundle_tree(
                requested_output
            )
            if copied_receipt != receipt:
                raise ContractError(
                    "released private-stage directory changed during copy"
                )
            return receipt
        members = [
            path
            for path in stage_root.iterdir()
            if path.name != ".DS_Store"
        ]
        if members != [staged_output] or not staged_output.is_file() or staged_output.is_symlink():
            raise ContractError("private stage did not create exactly one regular output")
        raw_output = staged_output.read_bytes()
        if not raw_output or len(raw_output) > _MAX_OUTPUT_BYTES:
            raise ContractError("private stage output is empty or too large")
        try:
            document = json.loads(raw_output)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("private stage output is not JSON") from exc
        if not isinstance(document, dict):
            raise ContractError("private stage output must be one JSON object")
        if canonical_json_bytes(document) != raw_output:
            raise ContractError("private stage output is not canonical JSON")
        write_new_bytes(
            requested_output,
            raw_output,
            "authenticated private-stage output",
        )
        return document


def _prepare_arguments(
    engine_entrypoint: str,
    spec: _Entrypoint,
    values: Mapping[str, object],
    input_root: Path,
    output_path: Path,
    denied_roots: Sequence[Path],
) -> tuple[list[str], list[Path], dict[str, object]]:
    if not isinstance(values, Mapping):
        raise ContractError("validated_arguments must be an object")
    expected = {argument.name for argument in spec.arguments}
    required = {argument.name for argument in spec.arguments if not argument.optional}
    unknown = set(values) - expected
    missing = required - set(values)
    if unknown or missing:
        raise ContractError(
            f"validated_arguments has unknown={sorted(unknown)} missing={sorted(missing)}"
        )
    arguments: list[str] = []
    admitted: list[Path] = []
    role_bindings: list[dict[str, object]] = []
    argument_bindings: list[dict[str, object]] = []
    for index, argument in enumerate(spec.arguments):
        if argument.name not in values:
            continue
        value = values[argument.name]
        arguments.append(argument.flag)
        if argument.kind == "literal":
            literal = _literal(
                value,
                f"validated_arguments.{argument.name}",
            )
            arguments.append(literal)
            argument_binding: dict[str, object] = {
                "name": argument.name,
                "flag": argument.flag,
                "kind": "literal",
                "value": literal,
                "value_sha256": _digest(literal.encode("utf-8")),
                "argument_sha256": None,
            }
            argument_binding["argument_sha256"] = _digest(
                canonical_json_bytes(argument_binding)
            )
            argument_bindings.append(argument_binding)
            continue
        source = _real_path(
            value,
            f"validated_arguments.{argument.name}",
            directory=argument.kind == "directory",
        )
        authenticated = _assert_original_input_admissible(
            source,
            denied_roots,
        )
        destination = input_root / f"{index:02d}-{argument.name}"
        try:
            snapshot = _snapshot_admitted_path(authenticated)
            _validate_role_snapshot(argument, snapshot)
            _copy_admitted_snapshot(snapshot, destination)
        finally:
            authenticated.close()
        arguments.append(str(destination))
        admitted.append(destination)
        role_binding = _role_input_binding(
            argument,
            snapshot,
            destination.relative_to(input_root).as_posix(),
        )
        role_bindings.append(role_binding)
        argument_binding = {
            "name": argument.name,
            "flag": argument.flag,
            "kind": argument.kind,
            "role_input_sha256": role_binding["role_input_sha256"],
            "argument_sha256": None,
        }
        argument_binding["argument_sha256"] = _digest(
            canonical_json_bytes(argument_binding)
        )
        argument_bindings.append(argument_binding)
    arguments.extend([spec.output_flag, str(output_path)])
    input_manifest: dict[str, object] = {
        "schema_version": "experimental-calibration-role-inputs-v1",
        "engine_entrypoint": engine_entrypoint,
        "arguments": argument_bindings,
        "arguments_sha256": _digest(
            canonical_json_bytes(argument_bindings)
        ),
        "roles": role_bindings,
        "roles_sha256": _digest(canonical_json_bytes(role_bindings)),
        "input_manifest_sha256": None,
    }
    input_manifest["input_manifest_sha256"] = _digest(
        canonical_json_bytes(input_manifest)
    )
    return arguments, admitted, input_manifest


def run_engine_in_private_stage(
    *,
    engine_entrypoint: str,
    validated_arguments: Mapping[str, object],
    oracle_denied_roots: Sequence[Path],
    output_dir: Path,
) -> dict[str, object]:
    """Stage and execute one closed engine CLI without oracle authority."""

    if engine_entrypoint not in _ENTRYPOINTS:
        raise ContractError(
            "engine_entrypoint must be one of: diagnose, exercise, materialize, propose"
        )
    spec = _ENTRYPOINTS[engine_entrypoint]
    cli = _SCRIPTS_ROOT / spec.cli
    required_module = _SCRIPTS_ROOT / spec.required_module
    if not cli.is_file() or not required_module.is_file():
        raise EntrypointUnavailable(
            f"registered engine entrypoint is not implemented: {engine_entrypoint}"
        )
    declared_source_manifest = _load_declared_source_manifest(
        engine_entrypoint,
        spec,
    )
    for argument in spec.arguments:
        if argument.kind != "literal":
            _role_validator(argument.validator)
    output_root = Path(output_dir).absolute()
    if output_root.exists() or output_root.is_symlink():
        raise ContractError("private-stage output directory already exists")
    denied = [
        Path(path).resolve(strict=False)
        for path in oracle_denied_roots
    ]
    if not denied:
        raise ContractError("oracle_denied_roots must not be empty")

    with tempfile.TemporaryDirectory(prefix="calibration-private-stage-") as raw:
        private_root = Path(raw).resolve(strict=True)
        os.chmod(private_root, 0o700)
        source_root = private_root / "source"
        input_root = private_root / "inputs"
        input_root.mkdir(mode=0o700)
        rows = _discover_closure(
            cli,
            omitted_initializers=spec.namespace_packages,
        )
        _assert_declared_source_closure(
            declared_source_manifest["files"],
            rows,
        )
        if spec.required_module not in {str(row["path"]) for row in rows}:
            raise ContractError("staged source closure omits the registered engine module")
        _stage_closure(rows, source_root)
        external_rows, external_roots = _external_runtime_closure(
            spec,
            private_root,
        )
        private_runtime_binding = _bind_external_runtime(
            _private_stage_runtime_binding(platform.system()),
            external_rows,
        )
        runtime_binding = _published_runtime_binding(
            private_runtime_binding
        )
        source_manifest = {
            "schema_version": "experimental-calibration-source-closure-v1",
            "engine_entrypoint": engine_entrypoint,
            "declared_source_manifest_sha256": (
                declared_source_manifest["source_manifest_sha256"]
            ),
            "files": rows,
            "first_party_files_sha256": _digest(
                canonical_json_bytes(rows)
            ),
            "external_runtime_files": external_rows,
            "external_runtime_files_sha256": _digest(
                canonical_json_bytes(external_rows)
            ),
            "runtime_binding_sha256": runtime_binding[
                "runtime_binding_sha256"
            ],
            "oracle_source_present": False,
            "source_manifest_sha256": None,
        }
        source_manifest["source_manifest_sha256"] = _digest(
            canonical_json_bytes(source_manifest)
        )
        smoke_output = private_root / "import-smoke.json"
        smoke_result = _run_provider_command(
            source=_SMOKE_BOOTSTRAP,
            arguments=[
                str(source_root),
                spec.cli,
                json.dumps(
                    [str(path) for path in external_roots],
                    separators=(",", ":"),
                ),
                str(smoke_output),
            ],
            admitted_read_paths=[source_root, *external_roots],
            denied_roots=denied,
            output_path=smoke_output,
            runtime_binding=private_runtime_binding,
        )
        if smoke_result != {
            "entry_relative": spec.cli,
            "status": "imports-ok",
        }:
            raise ContractError("staged import smoke result is invalid")
        staged_result = private_root / "authenticated-result.json"
        cli_arguments, admitted_inputs, input_manifest = _prepare_arguments(
            engine_entrypoint,
            spec,
            validated_arguments,
            input_root,
            staged_result,
            denied,
        )
        bootstrap_arguments = [
            str(source_root),
            spec.cli,
            json.dumps(
                [str(path) for path in external_roots],
                separators=(",", ":"),
            ),
            "--",
            *cli_arguments,
        ]
        result = _run_provider_command(
            source=_BOOTSTRAP,
            arguments=bootstrap_arguments,
            admitted_read_paths=[
                source_root,
                *external_roots,
                *admitted_inputs,
            ],
            denied_roots=denied,
            output_path=staged_result,
            runtime_binding=private_runtime_binding,
            output_kind=spec.output_kind,
        )
        create_new_directory(output_root, "private-stage released output")
        if spec.output_kind == "directory":
            released_result = output_root / "result"
            shutil.copytree(staged_result, released_result)
            if _authenticate_candidate_bundle_tree(released_result) != result:
                raise ContractError(
                    "released candidate directory changed during publication"
                )
            write_new_bytes(
                output_root / "result-receipt.json",
                canonical_json_bytes(result),
                "private-stage directory result receipt",
            )
            output_sha256 = str(result["tree_sha256"])
        else:
            released_result = output_root / "result.json"
            write_new_bytes(
                released_result,
                canonical_json_bytes(result),
                "private-stage result",
            )
            output_sha256 = _digest(canonical_json_bytes(result))
        write_new_bytes(
            output_root / "source-manifest.json",
            canonical_json_bytes(source_manifest),
            "private-stage source manifest",
        )
        write_new_bytes(
            output_root / "input-manifest.json",
            canonical_json_bytes(input_manifest),
            "private-stage role input manifest",
        )
        write_new_bytes(
            output_root / "runtime-binding.json",
            canonical_json_bytes(runtime_binding),
            "published private-stage runtime binding",
        )
        phase_receipt = _phase_execution_receipt(
            engine_entrypoint=engine_entrypoint,
            output_kind=spec.output_kind,
            output_sha256=output_sha256,
            source_manifest=source_manifest,
            input_manifest=input_manifest,
            runtime_binding=runtime_binding,
        )
        phase_receipt_path = output_root / "phase-execution-receipt.json"
        write_new_bytes(
            phase_receipt_path,
            canonical_json_bytes(phase_receipt),
            "private-stage phase execution receipt",
        )
        return {
            "engine_entrypoint": engine_entrypoint,
            "output_path": str(released_result.resolve(strict=True)),
            "output_sha256": output_sha256,
            "source_manifest_sha256": source_manifest["source_manifest_sha256"],
            "input_manifest_sha256": input_manifest["input_manifest_sha256"],
            "runtime_binding_sha256": runtime_binding[
                "runtime_binding_sha256"
            ],
            "phase_execution_receipt_path": str(
                phase_receipt_path.resolve(strict=True)
            ),
            "phase_execution_receipt_sha256": phase_receipt[
                "phase_execution_receipt_sha256"
            ],
        }


__all__ = [
    "EntrypointUnavailable",
    "run_engine_in_private_stage",
]
