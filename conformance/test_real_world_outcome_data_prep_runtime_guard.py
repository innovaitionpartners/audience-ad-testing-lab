from __future__ import annotations

from dataclasses import fields
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from outcome_data_prep.common import canonical_json_bytes, sha256_bytes  # noqa: E402
from outcome_data_prep import runtime_guard as runtime_guard_module  # noqa: E402
from outcome_data_prep.runtime_guard import (  # noqa: E402
    RUNTIME_IDENTITY_NOTICE,
    RuntimeGuardError,
    RuntimeIdentity,
    closed_runtime_inventory,
    hash_closed_runtime_tree,
    load_release_manifest,
    verify_runtime_identity,
)
from outcome_data_prep.source_snapshot import (  # noqa: E402
    SourceSnapshot,
    SourceSnapshotError,
    snapshot_source,
)


OPERATIONS = (
    "prepare_study",
    "import_results",
    "validate_study",
    "recover_study",
)
MANIFEST_GENERATOR = (
    ROOT
    / "skills"
    / "real-world-outcome-data-prep"
    / "scripts"
    / "generate-runtime-release-manifest.py"
)
RELEASE_MANIFEST = (
    ROOT
    / "skills"
    / "real-world-outcome-data-prep"
    / "references"
    / "runtime-release-manifest.json"
)
RELEASE_MANIFEST_RELATIVE = PurePosixPath(
    "skills/real-world-outcome-data-prep/references/"
    "runtime-release-manifest.json"
)


def _load_manifest_generator():
    spec = importlib.util.spec_from_file_location(
        "task14_runtime_release_manifest", MANIFEST_GENERATOR
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.runtime = self.base / "portable-runtime"
        skill = self.runtime / "skills" / "real-world-outcome-data-prep"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("approved release\n", encoding="utf-8")
        scripts = skill / "scripts"
        scripts.mkdir()
        (scripts / "operation.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.valid_manifest = self.build_manifest(self.runtime)

    @staticmethod
    def build_manifest(root: Path) -> dict[str, object]:
        files = {}
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = {
            "schema_version": "outcome-prep-runtime-release-v2",
            "repository": "innovaitionpartners/audience-ad-testing-lab",
            "release_version": "0.3.1",
            "files": files,
        }
        return {
            **identity,
            "release_tree_sha256": sha256_bytes(canonical_json_bytes(identity)),
        }

    def copy_runtime_tree(self) -> Path:
        copy = self.base / f"copy-{len(list(self.base.glob('copy-*')))}"
        shutil.copytree(self.runtime, copy)
        return copy

    @staticmethod
    def public_runtime_root_patch(root: Path):
        original_resolve = Path.resolve
        runtime_module = Path(runtime_guard_module.__file__)
        replacement = root / "one/two/three/four/runtime_guard.py"

        def resolve(path, *args, **kwargs):
            if path == runtime_module:
                return replacement
            return original_resolve(path, *args, **kwargs)

        return mock.patch.object(Path, "resolve", new=resolve)

    @staticmethod
    def assert_descriptors_were_closed(
        test: unittest.TestCase,
        descriptors: list[int],
    ) -> None:
        leaked: list[int] = []
        for descriptor in descriptors:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            leaked.append(descriptor)
            os.close(descriptor)
        test.assertEqual([], leaked, f"descriptor leak: {leaked}")

    def assert_fifo_swap_fails_promptly(
        self,
        *,
        root: Path,
        target: Path,
        operation: str,
    ) -> None:
        probe = r"""
import os
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, sys.argv[1])
from outcome_data_prep import runtime_guard

root = Path(sys.argv[2])
target = Path(sys.argv[3])
operation = sys.argv[4]
original_flags = runtime_guard._file_open_flags
attacked = False

def replace_regular_file_with_fifo():
    global attacked
    if not attacked:
        attacked = True
        target.unlink()
        os.mkfifo(target)
    return original_flags()

try:
    with mock.patch.object(
        runtime_guard,
        "_file_open_flags",
        side_effect=replace_regular_file_with_fifo,
    ):
        if operation == "manifest":
            runtime_guard.load_co_shipped_release_manifest(root)
        elif operation == "runtime-tree":
            runtime_guard.hash_closed_runtime_tree(root)
        else:
            raise AssertionError(f"unknown probe operation: {operation}")
except runtime_guard.RuntimeGuardError:
    if not attacked:
        raise AssertionError("FIFO timing probe did not reach the file open")
else:
    raise AssertionError("FIFO replacement was accepted")
"""
        command = [
            sys.executable,
            "-c",
            probe,
            str(SCRIPTS),
            str(root),
            str(target),
            operation,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1.5,
            )
        except subprocess.TimeoutExpired:
            self.fail(f"{operation} blocked after a regular file became a FIFO")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_modified_release_file_is_rejected(self):
        root = self.copy_runtime_tree()
        (root / "skills/real-world-outcome-data-prep/SKILL.md").write_text(
            "changed", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeGuardError, "release bytes"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_every_public_operation_rejects_modified_runtime(self):
        root = self.copy_runtime_tree()
        (root / "skills/real-world-outcome-data-prep/SKILL.md").write_text(
            "changed", encoding="utf-8"
        )
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeGuardError, "release bytes"):
                    verify_runtime_identity(
                        plugin_root=root,
                        release_manifest=self.valid_manifest,
                        operation=operation,
                    )

    def test_unlisted_regular_runtime_file_is_rejected(self):
        root = self.copy_runtime_tree()
        extra = root / "skills/real-world-outcome-data-prep/scripts/extra.py"
        extra.write_text("UNLISTED = True\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeGuardError, "closed runtime inventory"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_unlisted_runtime_symlink_is_rejected(self):
        root = self.copy_runtime_tree()
        scripts = root / "skills/real-world-outcome-data-prep/scripts"
        (scripts / "extra-link.py").symlink_to(scripts / "operation.py")

        with self.assertRaisesRegex(RuntimeGuardError, "closed runtime inventory"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_unlisted_nonregular_runtime_path_is_rejected(self):
        root = self.copy_runtime_tree()
        fifo = root / "skills/real-world-outcome-data-prep/scripts/extra.pipe"
        os.mkfifo(fifo)

        with self.assertRaisesRegex(RuntimeGuardError, "closed runtime inventory"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_live_and_manifest_closed_path_sets_must_match(self):
        manifest = json.loads(json.dumps(self.valid_manifest))
        manifest["files"].pop(
            "skills/real-world-outcome-data-prep/scripts/operation.py"
        )
        identity = {
            key: manifest[key]
            for key in (
                "schema_version",
                "repository",
                "release_version",
                "files",
            )
        }
        manifest["release_tree_sha256"] = sha256_bytes(
            canonical_json_bytes(identity)
        )

        with self.assertRaisesRegex(RuntimeGuardError, "closed runtime inventory"):
            verify_runtime_identity(
                plugin_root=self.runtime,
                release_manifest=manifest,
                operation="import_results",
            )

    def test_parent_directory_change_cannot_redirect_release_hashing(self):
        root = self.base / "parent-swap-runtime"
        first = root / "adir/control.py"
        payload = root / "zdir/payload.py"
        outside = self.base / "outside-runtime"
        first.parent.mkdir(parents=True)
        payload.parent.mkdir(parents=True)
        outside.mkdir()
        first.write_text("CONTROL = True\n", encoding="utf-8")
        payload.write_text("EXPECTED = True\n", encoding="utf-8")
        (outside / "payload.py").write_bytes(payload.read_bytes())
        manifest = self.build_manifest(root)
        original_hash = runtime_guard_module._hash_release_file
        attacked = False

        def swap_parent_before_first_hash(target, *args, **kwargs):
            nonlocal attacked
            if not attacked:
                attacked = True
                (root / "zdir").rename(root / "zdir-original")
                (root / "zdir").symlink_to(outside, target_is_directory=True)
            return original_hash(target, *args, **kwargs)

        with (
            mock.patch.object(
                runtime_guard_module,
                "_hash_release_file",
                side_effect=swap_parent_before_first_hash,
            ),
            self.assertRaisesRegex(RuntimeGuardError, "closed runtime inventory"),
        ):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=manifest,
                operation="import_results",
            )
        self.assertTrue(attacked)

    def test_public_runtime_rejects_a_co_shipped_manifest_symlink(self):
        root = self.copy_runtime_tree()
        manifest_parent = (
            root / "skills/real-world-outcome-data-prep/references"
        )
        manifest_parent.mkdir()
        outside = self.base / "outside-release-manifest.json"
        outside.write_bytes(canonical_json_bytes(self.valid_manifest))
        (manifest_parent / "runtime-release-manifest.json").symlink_to(outside)

        with (
            self.public_runtime_root_patch(root),
            self.assertRaisesRegex(RuntimeGuardError, "manifest"),
        ):
            runtime_guard_module.require_approved_runtime("import_results")

    def test_manifest_parent_replacement_during_load_is_rejected(self):
        root = self.copy_runtime_tree()
        skill = root / "skills/real-world-outcome-data-prep"
        manifest_parent = skill / "references"
        manifest_parent.mkdir()
        manifest_name = "runtime-release-manifest.json"
        payload = canonical_json_bytes(self.valid_manifest)
        (manifest_parent / manifest_name).write_bytes(payload)
        outside_parent = self.base / "outside-manifest-parent"
        outside_parent.mkdir()
        (outside_parent / manifest_name).write_bytes(payload)
        original_stat_child = runtime_guard_module._stat_child
        attacked = False

        def replace_parent(directory, name):
            nonlocal attacked
            if name == manifest_name and not attacked:
                attacked = True
                manifest_parent.rename(skill / "references-original")
                manifest_parent.symlink_to(
                    outside_parent,
                    target_is_directory=True,
                )
            return original_stat_child(directory, name)

        with (
            self.public_runtime_root_patch(root),
            mock.patch.object(
                runtime_guard_module,
                "_stat_child",
                side_effect=replace_parent,
            ),
            self.assertRaisesRegex(RuntimeGuardError, "manifest"),
        ):
            runtime_guard_module.require_approved_runtime("import_results")
        self.assertTrue(attacked)

    def test_co_shipped_manifest_is_byte_limited_before_json_parsing(self):
        root = self.copy_runtime_tree()
        manifest = (
            root
            / "skills/real-world-outcome-data-prep/references/"
            "runtime-release-manifest.json"
        )
        manifest.parent.mkdir()
        manifest.write_bytes(
            b"{" + b"x" * runtime_guard_module._MAX_RELEASE_MANIFEST_BYTES
        )

        with self.assertRaisesRegex(RuntimeGuardError, "byte limit"):
            runtime_guard_module.load_co_shipped_release_manifest(root)

    def test_co_shipped_manifest_regular_file_to_fifo_swap_fails_promptly(self):
        root = self.copy_runtime_tree()
        manifest = (
            root
            / "skills/real-world-outcome-data-prep/references/"
            "runtime-release-manifest.json"
        )
        manifest.parent.mkdir()
        manifest.write_bytes(canonical_json_bytes(self.valid_manifest))

        self.assert_fifo_swap_fails_promptly(
            root=root,
            target=manifest,
            operation="manifest",
        )

    def test_runtime_hash_regular_file_to_fifo_swap_fails_promptly(self):
        root = self.base / "fifo-swap-runtime"
        root.mkdir()
        candidate = root / "payload.py"
        candidate.write_text("VALUE = 1\n", encoding="utf-8")

        self.assert_fifo_swap_fails_promptly(
            root=root,
            target=candidate,
            operation="runtime-tree",
        )

    def test_root_directory_fstat_failure_closes_the_new_descriptor(self):
        opened: list[int] = []
        real_open = os.open

        def track_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        caught: BaseException | None = None
        with (
            mock.patch.object(runtime_guard_module.os, "open", side_effect=track_open),
            mock.patch.object(
                runtime_guard_module.os,
                "fstat",
                side_effect=OSError("injected root fstat failure"),
            ),
        ):
            try:
                runtime_guard_module._open_root_directory(self.runtime)
            except BaseException as exc:
                caught = exc

        self.assert_descriptors_were_closed(self, opened)
        self.assertIsInstance(caught, RuntimeGuardError)

    def test_child_directory_fstat_failure_closes_every_descriptor(self):
        opened: list[int] = []
        real_open = os.open
        real_fstat = os.fstat
        fstat_calls = 0

        def track_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        def fail_child_fstat(descriptor):
            nonlocal fstat_calls
            fstat_calls += 1
            if fstat_calls == 2:
                raise OSError("injected child fstat failure")
            return real_fstat(descriptor)

        caught: BaseException | None = None
        with (
            mock.patch.object(runtime_guard_module.os, "open", side_effect=track_open),
            mock.patch.object(
                runtime_guard_module.os,
                "fstat",
                side_effect=fail_child_fstat,
            ),
        ):
            try:
                hash_closed_runtime_tree(self.runtime)
            except BaseException as exc:
                caught = exc

        self.assert_descriptors_were_closed(self, opened)
        self.assertIsInstance(caught, RuntimeGuardError)

    def test_portable_installation_with_matching_release_bytes_is_accepted(self):
        root = self.copy_runtime_tree()
        identity = verify_runtime_identity(
            plugin_root=root,
            release_manifest=self.valid_manifest,
            operation="prepare_study",
        )
        self.assertEqual(
            RuntimeIdentity(
                plugin_root=root.resolve(),
                repository="innovaitionpartners/audience-ad-testing-lab",
                release_version="0.3.1",
                release_tree_sha256=self.valid_manifest["release_tree_sha256"],
            ),
            identity,
        )

    def test_operation_is_closed_to_the_four_public_operations(self):
        with self.assertRaisesRegex(RuntimeGuardError, "operation"):
            verify_runtime_identity(
                plugin_root=self.runtime,
                release_manifest=self.valid_manifest,
                operation="delete_study",
            )

    def test_release_manifest_rejects_unknown_fields(self):
        manifest = {**self.valid_manifest, "signature": "caller-asserted"}
        with self.assertRaisesRegex(RuntimeGuardError, "unknown fields"):
            verify_runtime_identity(
                plugin_root=self.runtime,
                release_manifest=manifest,
                operation="recover_study",
            )

    def test_release_manifest_tree_identity_is_verified(self):
        manifest = {**self.valid_manifest, "release_version": "9.9.9"}
        with self.assertRaisesRegex(RuntimeGuardError, "release tree"):
            verify_runtime_identity(
                plugin_root=self.runtime,
                release_manifest=manifest,
                operation="validate_study",
            )

    def test_release_manifest_loader_rejects_duplicate_json_keys(self):
        path = self.base / "manifest.json"
        path.write_text(
            '{"schema_version":"outcome-prep-runtime-release-v2",'
            '"schema_version":"outcome-prep-runtime-release-v2"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeGuardError, "duplicate field"):
            load_release_manifest(path)

    def test_release_manifest_rejects_noncanonical_file_paths(self):
        manifest = json.loads(json.dumps(self.valid_manifest))
        manifest["files"]["../outside"] = "b" * 64
        identity = {
            key: manifest[key]
            for key in (
                "schema_version",
                "repository",
                "release_version",
                "files",
            )
        }
        manifest["release_tree_sha256"] = sha256_bytes(canonical_json_bytes(identity))
        with self.assertRaisesRegex(RuntimeGuardError, "release file path"):
            verify_runtime_identity(
                plugin_root=self.runtime,
                release_manifest=manifest,
                operation="validate_study",
            )

    def test_wrong_git_origin_is_rejected_even_when_release_bytes_match(self):
        root = self.copy_runtime_tree()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", "https://example.com/wrong.git"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with self.assertRaisesRegex(RuntimeGuardError, "origin"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_configured_git_origin_is_accepted(self):
        root = self.copy_runtime_tree()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "remote",
                "add",
                "origin",
                "git@github.com:innovaitionpartners/audience-ad-testing-lab.git",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        identity = verify_runtime_identity(
            plugin_root=root,
            release_manifest=self.valid_manifest,
            operation="import_results",
        )
        self.assertEqual(root.resolve(), identity.plugin_root)

    def test_multiple_git_origin_values_reject_if_any_value_is_unapproved(self):
        root = self.copy_runtime_tree()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "remote",
                "add",
                "origin",
                "https://example.com/unapproved-first.git",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "config",
                "--local",
                "--add",
                "remote.origin.url",
                "https://github.com/innovaitionpartners/audience-ad-testing-lab.git",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        with self.assertRaisesRegex(RuntimeGuardError, "origin"):
            verify_runtime_identity(
                plugin_root=root,
                release_manifest=self.valid_manifest,
                operation="import_results",
            )

    def test_global_only_approved_origin_does_not_authorize_local_runtime(self):
        root = self.copy_runtime_tree()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        global_config = self.base / "global-git-config"
        subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(global_config),
                "remote.origin.url",
                "https://github.com/innovaitionpartners/audience-ad-testing-lab.git",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        with mock.patch.dict(
            os.environ,
            {"GIT_CONFIG_GLOBAL": str(global_config)},
        ):
            with self.assertRaisesRegex(RuntimeGuardError, "origin"):
                verify_runtime_identity(
                    plugin_root=root,
                    release_manifest=self.valid_manifest,
                    operation="import_results",
                )

    def test_runtime_identity_notice_does_not_claim_software_signing(self):
        self.assertIn("stale or modified operational bytes", RUNTIME_IDENTITY_NOTICE)
        self.assertIn("not a cryptographic software-signing authority", RUNTIME_IDENTITY_NOTICE)

    def test_superpowers_directory_exclusion(self):
        from outcome_data_prep.runtime_guard import _is_excluded_runtime_path
        
        # Test exact match
        self.assertTrue(_is_excluded_runtime_path(PurePosixPath("docs/superpowers/plans/plan.md")))
        self.assertTrue(_is_excluded_runtime_path(PurePosixPath("superpowers/plans/plan.md")))
        
        # Test wildcard match (su?erpowers, where ? is any single char, e.g. 'p', 'b', etc.)
        self.assertTrue(_is_excluded_runtime_path(PurePosixPath("docs/suberpowers/plans/plan.md")))
        self.assertTrue(_is_excluded_runtime_path(PurePosixPath("docs/suXerpowers/plans/plan.md")))
        
        # Test non-matches (should not be excluded if they don't match)
        self.assertFalse(_is_excluded_runtime_path(PurePosixPath("docs/superpower/plans/plan.md"))) # wrong length
        self.assertFalse(_is_excluded_runtime_path(PurePosixPath("docs/superpowerss/plans/plan.md"))) # wrong length
        self.assertFalse(_is_excluded_runtime_path(PurePosixPath("docs/uuperpowers/plans/plan.md"))) # wrong prefix
        self.assertFalse(_is_excluded_runtime_path(PurePosixPath("docs/superpowera/plans/plan.md"))) # wrong suffix





class RuntimeReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "plugin"
        self.root.mkdir()
        (self.root / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
        skill = self.root / "skills" / "sample"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("sample\n", encoding="utf-8")
        self.output = self.root / "release.json"

    def build(self):
        module = _load_manifest_generator()
        return module.build_release_manifest(
            plugin_root=self.root,
            output_relative_path=PurePosixPath("release.json"),
        )

    def test_generator_uses_the_runtime_verifiers_closed_inventory_policy(self):
        module = _load_manifest_generator()
        self.assertIs(closed_runtime_inventory, module.closed_runtime_inventory)
        self.assertIs(hash_closed_runtime_tree, module.hash_closed_runtime_tree)

    def test_generator_hashes_every_regular_runtime_file_except_closed_exclusions(self):
        excluded_files = (
            self.root / ".git",
            self.root / "__pycache__" / "runtime.cpython-313.pyc",
            self.root / ".pytest_cache" / "state",
            self.root / ".mypy_cache" / "state",
            self.root / ".ruff_cache" / "state",
            self.root / ".venv" / "bin" / "python",
            self.root / "venv" / "bin" / "python",
            self.root / "env" / "bin" / "python",
            self.root / "tmp" / "scratch.txt",
            self.root / "tests" / "output" / "result.json",
            self.root / "tests" / "runs" / "result.json",
            self.root / ".DS_Store",
        )
        for path in excluded_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local only\n")
        included_cache_like = self.root / ".unknown-cache" / "must-be-hashed.txt"
        included_cache_like.parent.mkdir()
        included_cache_like.write_bytes(b"runtime byte\n")
        self.output.write_bytes(b"prior self-excluded manifest\n")

        manifest = self.build()

        expected = {
            ".unknown-cache/must-be-hashed.txt": hashlib.sha256(
                included_cache_like.read_bytes()
            ).hexdigest(),
            "runtime.py": hashlib.sha256(
                (self.root / "runtime.py").read_bytes()
            ).hexdigest(),
            "skills/sample/SKILL.md": hashlib.sha256(
                (self.root / "skills/sample/SKILL.md").read_bytes()
            ).hexdigest(),
        }
        self.assertEqual(expected, manifest["files"])
        self.assertNotIn("release.json", manifest["files"])

    def test_generator_is_reproducible_and_binds_the_closed_identity(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(
            {
                "schema_version",
                "repository",
                "release_version",
                "files",
                "release_tree_sha256",
            },
            set(first),
        )
        self.assertEqual(
            "innovaitionpartners/audience-ad-testing-lab",
            first["repository"],
        )
        self.assertEqual("0.3.1", first["release_version"])
        identity = {
            key: first[key]
            for key in (
                "schema_version",
                "repository",
                "release_version",
                "files",
            )
        }
        self.assertEqual(
            sha256_bytes(canonical_json_bytes(identity)),
            first["release_tree_sha256"],
        )

    def test_generator_rejects_noncanonical_output(self):
        module = _load_manifest_generator()
        for output in (
            PurePosixPath("/release.json"),
            PurePosixPath("../release.json"),
            PurePosixPath("."),
        ):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "output"):
                    module.build_release_manifest(
                        plugin_root=self.root,
                        output_relative_path=output,
                    )

    def test_atomic_publication_is_mode_0644_and_preserves_prior_bytes_on_failure(self):
        module = _load_manifest_generator()
        module.write_release_manifest(
            plugin_root=self.root,
            output=self.output,
        )
        first = self.output.read_bytes()
        self.assertEqual(0o644, stat.S_IMODE(self.output.stat().st_mode))
        self.assertEqual(canonical_json_bytes(json.loads(first)), first)

        with (
            mock.patch.object(
                module.os, "replace", side_effect=OSError("injected replace failure")
            ),
            self.assertRaisesRegex(OSError, "injected replace failure"),
        ):
            module.write_release_manifest(
                plugin_root=self.root,
                output=self.output,
            )
        self.assertEqual(first, self.output.read_bytes())
        self.assertEqual([], list(self.root.glob(".release.json.*.tmp")))

    @unittest.skipUnless(RELEASE_MANIFEST.is_file(), "release manifest not generated yet")
    def test_co_shipped_manifest_is_complete_self_excluded_and_hash_bound(self):
        module = _load_manifest_generator()
        manifest = load_release_manifest(RELEASE_MANIFEST)
        self.assertEqual(
            module.runtime_file_hashes(
                ROOT,
                excluded={RELEASE_MANIFEST_RELATIVE},
            ),
            manifest["files"],
        )
        self.assertNotIn(RELEASE_MANIFEST_RELATIVE.as_posix(), manifest["files"])

    @unittest.skipUnless(RELEASE_MANIFEST.is_file(), "release manifest not generated yet")
    def test_real_release_accepts_exact_tree_and_rejects_adapter_or_release_change(self):
        manifest = load_release_manifest(RELEASE_MANIFEST)
        identity = verify_runtime_identity(
            plugin_root=ROOT,
            release_manifest=manifest,
            operation="import_results",
        )
        self.assertEqual("0.3.1", identity.release_version)

        portable = Path(self.temporary_directory.name) / "portable"
        for relative in manifest["files"]:
            source = ROOT.joinpath(*PurePosixPath(relative).parts)
            destination = portable.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        mutations = (
            "skills/real-world-outcome-data-prep/references/platform-capabilities.json",
            "skills/real-world-outcome-data-prep/scripts/outcome_data_prep/runtime_guard.py",
        )
        for relative in mutations:
            path = portable.joinpath(*PurePosixPath(relative).parts)
            original = path.read_bytes()
            path.write_bytes(original + b"\nchanged\n")
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(RuntimeGuardError, "release bytes"):
                    verify_runtime_identity(
                        plugin_root=portable,
                        release_manifest=manifest,
                        operation="import_results",
                    )
            path.write_bytes(original)

    @unittest.skipUnless(
        os.environ.get("RUN_REAL_WORLD_OUTCOME_PREP_PROVIDER_TESTS") == "1",
        "set RUN_REAL_WORLD_OUTCOME_PREP_PROVIDER_TESTS=1 for real filesystem checks",
    )
    def test_real_filesystem_provider_verifies_release_and_stable_source(self):
        if not RELEASE_MANIFEST.is_file():
            self.fail("provider check requires the co-shipped release manifest")
        manifest = load_release_manifest(RELEASE_MANIFEST)
        verify_runtime_identity(
            plugin_root=ROOT,
            release_manifest=manifest,
            operation="import_results",
        )
        source = self.root / "provider-source.csv"
        source.write_bytes(b"campaign_id,impressions\nacme,100\n")
        snapshot = snapshot_source(source, staging_root=self.root / "provider-stage")
        self.assertEqual(source.read_bytes(), snapshot.staged_path.read_bytes())


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.source = self.base / "source.csv"
        self.source.write_bytes(b"campaign,clicks\nA,12\n")
        self.stage = self.base / "stage"

    def test_source_change_during_read_fails_closed(self):
        with self.assertRaisesRegex(SourceSnapshotError, "changed while read"):
            snapshot_source(
                self.source,
                staging_root=self.stage,
                after_open_hook=lambda: self.source.write_bytes(b"changed"),
            )
        self.assertEqual([], list(self.stage.glob("source-*.bin")))

    def test_timestamp_change_during_read_fails_closed(self):
        original = self.source.stat()

        def change_timestamp():
            os.utime(
                self.source,
                ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000),
            )

        with self.assertRaisesRegex(SourceSnapshotError, "changed while read"):
            snapshot_source(
                self.source,
                staging_root=self.stage,
                after_open_hook=change_timestamp,
            )
        self.assertEqual([], list(self.stage.glob("source-*.bin")))

    def test_cleanup_uses_deletion_when_secure_truncation_fails(self):
        with mock.patch(
            "outcome_data_prep.source_snapshot.os.ftruncate",
            side_effect=OSError("injected truncation failure"),
        ):
            with self.assertRaisesRegex(SourceSnapshotError, "changed while read"):
                snapshot_source(
                    self.source,
                    staging_root=self.stage,
                    after_open_hook=lambda: self.source.write_bytes(b"changed"),
                )
        self.assertEqual([], list(self.stage.glob("source-*.bin")))

    def test_cleanup_accepts_verified_truncation_when_unlink_fails(self):
        with mock.patch.object(
            Path,
            "unlink",
            side_effect=OSError("injected unlink failure"),
        ):
            with self.assertRaisesRegex(SourceSnapshotError, "changed while read"):
                snapshot_source(
                    self.source,
                    staging_root=self.stage,
                    after_open_hook=lambda: self.source.write_bytes(b"changed"),
                )

        remaining = list(self.stage.glob("source-*.bin"))
        self.assertEqual(1, len(remaining))
        self.assertEqual(0, remaining[0].stat().st_size)
        remaining[0].unlink()

    def test_cleanup_failure_is_surfaced_when_truncation_and_unlink_fail(self):
        with (
            mock.patch(
                "outcome_data_prep.source_snapshot.os.ftruncate",
                side_effect=OSError("injected truncation failure"),
            ),
            mock.patch.object(
                Path,
                "unlink",
                side_effect=OSError("injected unlink failure"),
            ),
        ):
            with self.assertRaisesRegex(SourceSnapshotError, "cleanup failed"):
                snapshot_source(
                    self.source,
                    staging_root=self.stage,
                    after_open_hook=lambda: self.source.write_bytes(b"changed"),
                )

        remaining = list(self.stage.glob("source-*.bin"))
        self.assertEqual(1, len(remaining))
        self.assertGreater(remaining[0].stat().st_size, 0)
        remaining[0].unlink()

    def test_snapshot_is_a_stable_protected_copy(self):
        expected = self.source.read_bytes()
        snapshot = snapshot_source(self.source, staging_root=self.stage)
        self.source.write_bytes(b"later replacement")

        self.assertEqual(expected, snapshot.staged_path.read_bytes())
        self.assertEqual(len(expected), snapshot.byte_length)
        self.assertEqual("sha256:" + hashlib.sha256(expected).hexdigest(), snapshot.source_sha256)
        self.assertEqual("application/octet-stream", snapshot.media_type)
        self.assertEqual(self.source, snapshot.original_path)
        self.assertEqual(0o700, stat.S_IMODE(self.stage.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(snapshot.staged_path.stat().st_mode))

    def test_source_snapshot_exposes_only_the_approved_stable_fields(self):
        self.assertEqual(
            [
                "original_path",
                "staged_path",
                "byte_length",
                "source_sha256",
                "media_type",
                "stat_identity",
            ],
            [field.name for field in fields(SourceSnapshot)],
        )
        self.assertTrue(SourceSnapshot.__dataclass_params__.frozen)

    def test_symlink_and_nonregular_sources_are_rejected(self):
        symlink = self.base / "source-link.csv"
        symlink.symlink_to(self.source)
        directory = self.base / "source-directory"
        directory.mkdir()
        for candidate in (symlink, directory):
            with self.subTest(candidate=candidate.name):
                with self.assertRaisesRegex(
                    SourceSnapshotError, "non-symlink regular file"
                ):
                    snapshot_source(candidate, staging_root=self.stage)

    def test_source_larger_than_limit_is_rejected_without_staged_bytes(self):
        with self.assertRaisesRegex(SourceSnapshotError, "byte limit"):
            snapshot_source(
                self.source,
                staging_root=self.stage,
                byte_limit=len(self.source.read_bytes()) - 1,
            )
        self.assertFalse(self.stage.exists())


if __name__ == "__main__":
    unittest.main()
