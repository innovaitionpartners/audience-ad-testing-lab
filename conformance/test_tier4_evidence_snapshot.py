from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
)
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    EvidenceSnapshot,
    ValidatedEvidenceSnapshot,
    create_evidence_snapshot,
    open_evidence_snapshot,
    recover_evidence_snapshot_publication,
)


RESULT_DIGEST = "sha256:" + ("ab" * 32)
SNAPSHOT_ID = "screening--run-001--" + ("ab" * 32)
COMMIT_NAME = SNAPSHOT_ID + ".snapshot.json"
FROZEN_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?Z$"
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class Tier4EvidenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.allowed_root = self.base / "allowed"
        self.snapshot_root = self.base / "snapshots"
        self.allowed_root.mkdir()
        self.snapshot_root.mkdir()

    def write(self, relative: str, value: bytes = b'{"ok":true}\n') -> Path:
        path = self.allowed_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def binding(
        self,
        member_path: str,
        value: bytes,
        *,
        canonical: str | None = None,
        count: int | None = None,
    ) -> dict[str, object]:
        return {
            "member_path": member_path,
            "raw_bytes_sha256": digest(value),
            "canonical_document_sha256": canonical or digest(value),
            "record_count": count,
        }

    def create(
        self,
        sources: dict[str, Path] | None = None,
        bindings: dict[str, dict[str, object]] | None = None,
        *,
        surface: str = "screening",
        run_id: str = "run-001",
    ) -> EvidenceSnapshot:
        raw = b'{"ok":true}\n'
        selected_sources = sources or {"result.json": self.write("result.json", raw)}
        selected_bindings = bindings or {"result": self.binding("result.json", raw)}
        return create_evidence_snapshot(
            surface=surface,
            run_id=run_id,
            result_sha256=RESULT_DIGEST,
            sources=selected_sources,
            bindings=selected_bindings,
            allowed_roots=[self.allowed_root],
            snapshot_root=self.snapshot_root,
        )

    def commit(self) -> tuple[Path, dict[str, object]]:
        path = self.snapshot_root / COMMIT_NAME
        return path, json.loads(path.read_text())

    def test_commits_canonical_archive_and_closed_self_hashed_record(self):
        result = b'{"ok":true}\n'
        module = b"VALUE = 1\n"
        deep = b"DEEP = True\n"
        original_umask = os.umask(0o022)
        os.umask(original_umask)
        calls: list[int] = []
        real_umask = os.umask

        def observe(value: int) -> int:
            calls.append(value)
            return real_umask(value)

        with patch(
            "audience_panel_builder.population.validation.evidence_snapshot.os.umask",
            side_effect=observe,
        ):
            snapshot = self.create(
                {
                    "result.json": self.write("result.json", result),
                    "runtime/pkg/module.py": self.write("module.py", module),
                    "runtime/pkg/sub/deep/module.py": self.write("deep.py", deep),
                },
                {"result": self.binding("result.json", result)},
            )

        commit_path, record = self.commit()
        self.assertEqual(SNAPSHOT_ID, snapshot.snapshot_id)
        self.assertEqual(commit_path, snapshot.commit_path)
        self.assertRegex(snapshot.frozen_at, FROZEN_AT_RE)
        self.assertIn(0o077, calls)
        self.assertEqual(0o400, stat.S_IMODE(commit_path.stat().st_mode))
        archive = self.snapshot_root / record["archive_name"]
        self.assertRegex(archive.name, r"^\.snapshot-[0-9a-f-]{36}\.zip$")
        self.assertEqual(0o400, stat.S_IMODE(archive.stat().st_mode))
        self.assertEqual(
            {
                "schema_version", "status", "snapshot_id", "surface", "run_id",
                "result_sha256", "archive_name", "archive_sha256",
                "archive_byte_count", "frozen_at", "bindings", "members",
                "snapshot_sha256",
            },
            set(record),
        )
        self.assertEqual("panel-evidence-snapshot-commit-v1", record["schema_version"])
        self.assertEqual("committed", record["status"])
        self.assertEqual(list(sorted(record["bindings"])), list(record["bindings"]))
        self.assertEqual(sorted(row["path"] for row in record["members"]), [
            row["path"] for row in record["members"]
        ])
        unhashed = {**record, "snapshot_sha256": None}
        expected = digest(
            (json.dumps(unhashed, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False) + "\n").encode()
        )
        self.assertEqual(expected, record["snapshot_sha256"])
        self.assertEqual(snapshot.snapshot_sha256, record["snapshot_sha256"])
        self.assertEqual(snapshot.archive_sha256, record["archive_sha256"])
        self.assertNotIn("snapshot_id", inspect.signature(create_evidence_snapshot).parameters)
        self.assertNotIn("archive_name", inspect.signature(create_evidence_snapshot).parameters)
        self.assertNotIn("frozen_at", inspect.signature(create_evidence_snapshot).parameters)

        archive_bytes = archive.read_bytes()
        self.assertTrue(archive_bytes.startswith(b"PK\x03\x04"))
        self.assertTrue(archive_bytes.endswith(b"\x00\x00"))
        # Fixed DOS time/date, no flags, stored, version 20, no extra.
        local = struct.unpack_from("<IHHHHHIIIHH", archive_bytes, 0)
        self.assertEqual((20, 0, 0, 0, 33), local[1:6])
        self.assertEqual(0, local[-1])

    def test_no_overwrite_and_surface_names_do_not_collide(self):
        raw = b"first"
        source = self.write("result.json", raw)
        screening = self.create(
            {"result.json": source}, {"result": self.binding("result.json", raw)}
        )
        boundary = self.create(
            {"result.json": source},
            {"result": self.binding("result.json", raw)},
            surface="boundary",
        )
        self.assertNotEqual(screening.commit_path, boundary.commit_path)
        before = screening.commit_path.read_bytes()
        source.write_bytes(b"second")
        with self.assertRaises(ProducerOutputCollision):
            self.create(
                {"result.json": source},
                {"result": self.binding("result.json", b"second")},
            )
        self.assertEqual(before, screening.commit_path.read_bytes())
        self.assertEqual([], list(self.snapshot_root.glob(".quarantine-*")))

    def test_open_yields_live_private_capability_and_cleans_only_extraction(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module_api

        result = b'{"ok":true}\n'
        module_bytes = b"VALUE = 1\n"
        deep = b"DEEP = True\n"
        snapshot = self.create(
            {
                "result.json": self.write("result.json", result),
                "runtime/pkg/module.py": self.write("module.py", module_bytes),
                "runtime/pkg/sub/deep/module.py": self.write("deep.py", deep),
            },
            {"result": self.binding("result.json", result)},
        )
        commit_path, record = self.commit()
        archive_path = self.snapshot_root / record["archive_name"]
        with open_evidence_snapshot(
            surface="screening", run_id="run-001", result_sha256=RESULT_DIGEST,
            snapshot_root=self.snapshot_root,
        ) as validated:
            self.assertIsInstance(validated, ValidatedEvidenceSnapshot)
            validated.require_active()
            result_path = validated.resolve_member("result")
            extraction = result_path.parent
            self.assertEqual(
                extraction / "runtime",
                module_api._resolve_runtime_for_replay(validated),
            )
            self.assertEqual(result, result_path.read_bytes())
            self.assertEqual(0o400, stat.S_IMODE(validated.resolve_member("result").stat().st_mode))
            self.assertEqual(0o500, stat.S_IMODE((extraction / "runtime/pkg").stat().st_mode))
            self.assertEqual(snapshot.snapshot_sha256, validated.snapshot_sha256)
        self.assertFalse(extraction.exists())
        self.assertTrue(commit_path.exists())
        self.assertTrue(archive_path.exists())
        with self.assertRaises(ProducerEvidenceError):
            validated.require_active()
        with self.assertRaises(ProducerEvidenceError):
            validated.resolve_member("result")
        with self.assertRaises((TypeError, ProducerEvidenceError)):
            copy.copy(validated)
        with self.assertRaises((TypeError, ProducerEvidenceError)):
            ValidatedEvidenceSnapshot()
        self.assertFalse(hasattr(validated, "root"))
        self.assertFalse(hasattr(validated, "runtime_root"))

    def test_recovery_is_joint_idempotent_and_orphans_are_not_locators(self):
        expected = self.create()
        first = recover_evidence_snapshot_publication(
            surface="screening", run_id="run-001", result_sha256=RESULT_DIGEST,
            snapshot_root=self.snapshot_root,
        )
        second = recover_evidence_snapshot_publication(
            surface="screening", run_id="run-001", result_sha256=RESULT_DIGEST,
            snapshot_root=self.snapshot_root,
        )
        self.assertEqual(expected, first)
        self.assertEqual(first, second)

        other = self.base / "orphans"
        other.mkdir()
        (other / ".snapshot-00000000-0000-0000-0000-000000000000.zip").write_bytes(b"PK")
        with self.assertRaises(ProducerAuthenticationError):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="run-001",
                result_sha256=RESULT_DIGEST, snapshot_root=other,
            )

    def test_archive_fsync_failure_still_writes_commit_and_internal_recovery(self):
        real_fsync = os.fsync
        failed = False

        def fail_once(fd: int) -> None:
            nonlocal failed
            value = os.fstat(fd)
            if not failed and stat.S_ISREG(value.st_mode):
                failed = True
                raise OSError("injected archive fsync")
            real_fsync(fd)

        with patch(
            "audience_panel_builder.population.validation.evidence_snapshot.os.fsync",
            side_effect=fail_once,
        ):
            snapshot = self.create()
        self.assertTrue(failed)
        self.assertTrue(snapshot.commit_path.exists())

    def test_repeated_recovery_fsync_failure_is_indeterminate_then_resolves(self):
        real_fsync = os.fsync

        def fail_regular(fd: int) -> None:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("injected durable failure")
            real_fsync(fd)

        with patch(
            "audience_panel_builder.population.validation.evidence_snapshot.os.fsync",
            side_effect=fail_regular,
        ), self.assertRaises(ProducerPublicationIndeterminate):
            self.create()
        self.assertTrue((self.snapshot_root / COMMIT_NAME).exists())
        recovered = recover_evidence_snapshot_publication(
            surface="screening", run_id="run-001", result_sha256=RESULT_DIGEST,
            snapshot_root=self.snapshot_root,
        )
        self.assertEqual(SNAPSHOT_ID, recovered.snapshot_id)

    def test_commit_or_archive_substitution_and_mutation_fail_authentication(self):
        self.create()
        commit_path, record = self.commit()
        archive = self.snapshot_root / record["archive_name"]
        for target in (commit_path, archive):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                mode = stat.S_IMODE(target.stat().st_mode)
                os.chmod(target, 0o600)
                target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                os.chmod(target, mode)
                with self.assertRaises(ProducerAuthenticationError):
                    recover_evidence_snapshot_publication(
                        surface="screening", run_id="run-001",
                        result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                    )
                os.chmod(target, 0o600)
                target.write_bytes(original)
                os.chmod(target, mode)

                moved = target.with_suffix(target.suffix + ".moved")
                target.rename(moved)
                target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                os.chmod(target, mode)
                with self.assertRaises(ProducerAuthenticationError):
                    recover_evidence_snapshot_publication(
                        surface="screening", run_id="run-001",
                        result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                    )
                target.unlink()
                moved.rename(target)

    def test_closed_commit_schema_binding_and_self_hash(self):
        self.create()
        path, original = self.commit()
        cases: list[dict[str, object]] = []
        for field in ("status", "archive_sha256", "snapshot_sha256"):
            changed = copy.deepcopy(original)
            changed[field] = "wrong"
            cases.append(changed)
        missing = copy.deepcopy(original)
        missing.pop("frozen_at")
        cases.append(missing)
        extra = copy.deepcopy(original)
        extra["unexpected"] = True
        cases.append(extra)
        bad_binding = copy.deepcopy(original)
        bad_binding["bindings"]["result"]["unexpected"] = True
        cases.append(bad_binding)
        for index, changed in enumerate(cases):
            with self.subTest(index=index):
                os.chmod(path, 0o600)
                path.write_bytes(
                    (json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )
                os.chmod(path, 0o400)
                with self.assertRaises(ProducerAuthenticationError):
                    recover_evidence_snapshot_publication(
                        surface="screening", run_id="run-001",
                        result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                    )
                os.chmod(path, 0o600)
                path.write_bytes(
                    (json.dumps(original, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )
                os.chmod(path, 0o400)

    def test_rejects_bad_sources_members_bindings_and_trusted_root(self):
        source = self.write("result.json")
        hostile = (
            "", ".", "..", "/absolute", "runtime/", "runtime//x",
            "runtime/./x", "runtime/../x", "runtime\\x", "runtime/\x01x",
            "runtime/caf\u00e9",
        )
        for member in hostile:
            with self.subTest(member=member), self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {member: source},
                    {"result": self.binding(member, source.read_bytes())},
                    run_id="hostile-" + str(abs(hash(member))),
                )
        outside = self.base / "outside"
        outside.write_bytes(b"outside")
        directory = self.allowed_root / "directory"
        directory.mkdir()
        symlink = self.allowed_root / "symlink"
        symlink.symlink_to(source)
        for candidate in (outside, directory, symlink):
            with self.subTest(candidate=candidate), self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {"result.json": candidate},
                    {"result": self.binding("result.json", b"outside")},
                    run_id="candidate-" + str(abs(hash(candidate))),
                )
        bad = self.binding("missing.json", source.read_bytes())
        with self.assertRaises(ProducerAuthenticationError):
            self.create({"result.json": source}, {"result": bad}, run_id="bad-binding")
        os.chmod(self.snapshot_root, 0o777)
        with self.assertRaises(ProducerAuthenticationError):
            self.create(run_id="bad-root")

    def test_canonical_zip_rejects_metadata_and_layout_mutations(self):
        self.create()
        _commit_path, record = self.commit()
        archive = self.snapshot_root / record["archive_name"]
        original = bytearray(archive.read_bytes())
        mutations = {
            "flag": (6, 0x08),
            "compression": (8, 0x08),
            "timestamp": (10, 0x01),
            "local-extra-length": (28, 0x01),
            "leading-prefix": None,
            "trailing-byte": None,
            "duplicate-eocd": None,
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                if name == "leading-prefix":
                    changed = b"x" + bytes(original)
                elif name == "trailing-byte":
                    changed = bytes(original) + b"x"
                elif name == "duplicate-eocd":
                    changed = bytes(original) + bytes(original[-22:])
                else:
                    offset, delta = mutation
                    changed_array = bytearray(original)
                    changed_array[offset] ^= delta
                    changed = bytes(changed_array)
                os.chmod(archive, 0o600)
                archive.write_bytes(changed)
                os.chmod(archive, 0o400)
                with self.assertRaises(ProducerAuthenticationError):
                    recover_evidence_snapshot_publication(
                        surface="screening", run_id="run-001",
                        result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                    )
        # The last mutation remains invalid by design.

    def test_source_replacement_during_copy_fails_without_mutating_siblings(self):
        source = self.write("nested/result.json", b"before")
        sibling = self.snapshot_root / "owner"
        sibling.write_bytes(b"untouched")
        real_read = os.read
        fired = False

        def replace(fd: int, size: int) -> bytes:
            nonlocal fired
            chunk = real_read(fd, size)
            if not fired and stat.S_ISREG(os.fstat(fd).st_mode):
                fired = True
                moved = source.with_suffix(".moved")
                source.rename(moved)
                source.write_bytes(b"after!")
            return chunk

        with patch(
            "audience_panel_builder.population.validation.evidence_snapshot.os.read",
            side_effect=replace,
        ), self.assertRaises(ProducerAuthenticationError):
            self.create(
                {"runtime/nested/result.json": source},
                {"result": self.binding("runtime/nested/result.json", b"before")},
            )
        self.assertEqual(b"untouched", sibling.read_bytes())
        self.assertFalse((self.snapshot_root / COMMIT_NAME).exists())

    def test_wrong_modes_symlinks_partial_commit_and_root_swap_fail(self):
        self.create()
        path, record = self.commit()
        archive = self.snapshot_root / record["archive_name"]
        for target in (path, archive):
            os.chmod(target, 0o600)
            with self.assertRaises(ProducerAuthenticationError):
                recover_evidence_snapshot_publication(
                    surface="screening", run_id="run-001",
                    result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                )
            os.chmod(target, 0o400)
        original = path.read_bytes()
        os.chmod(path, 0o600)
        path.write_bytes(original[:10])
        os.chmod(path, 0o400)
        with self.assertRaises(ProducerAuthenticationError):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="run-001",
                result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
            )

    def test_limit_constants_accept_equality_and_reject_one_over(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        raw = b"x"
        source = self.write("x", raw)
        with patch.object(module, "MAX_MEMBER_COUNT", 1), patch.object(
            module, "MAX_MEMBER_BYTES", 1
        ), patch.object(module, "MAX_TOTAL_BYTES", 1), patch.object(
            module, "MAX_ARCHIVE_BYTES", 1024
        ):
            self.create(
                {"x": source}, {"x": self.binding("x", raw)}, run_id="limit-equal"
            )
        with patch.object(module, "MAX_MEMBER_COUNT", 0):
            with self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {"x": source}, {"x": self.binding("x", raw)}, run_id="limit-count"
                )
        with patch.object(module, "MAX_MEMBER_BYTES", 0):
            with self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {"x": source}, {"x": self.binding("x", raw)}, run_id="limit-member"
                )
        with patch.object(module, "MAX_TOTAL_BYTES", 0):
            with self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {"x": source}, {"x": self.binding("x", raw)}, run_id="limit-total"
                )

    def test_real_commit_reader_enforces_byte_depth_binding_and_name_limits(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        snapshot = self.create(run_id="reader")
        commit = snapshot.commit_path
        commit_size = commit.stat().st_size
        with patch.object(module, "MAX_COMMIT_BYTES", commit_size):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="reader", result_sha256=RESULT_DIGEST,
                snapshot_root=self.snapshot_root,
            )
        with patch.object(module, "MAX_COMMIT_BYTES", commit_size - 1):
            with self.assertRaises(ProducerAuthenticationError):
                recover_evidence_snapshot_publication(
                    surface="screening", run_id="reader",
                    result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                )

        raw = b'{"ok":true}\n'
        source = self.write("many.json", raw)
        maximum_bindings = {
            f"binding-{index:04d}": self.binding("result.json", raw)
            for index in range(1_024)
        }
        self.create(
            {"result.json": source}, maximum_bindings, run_id="bindings-equal"
        )
        maximum_bindings["binding-over"] = self.binding("result.json", raw)
        with self.assertRaises(ProducerAuthenticationError):
            self.create(
                {"result.json": source}, maximum_bindings, run_id="bindings-over"
            )

        exact_name = "n" * 128
        self.create(
            {"result.json": source},
            {exact_name: self.binding("result.json", raw)},
            run_id="name-equal",
        )
        with self.assertRaises(ProducerAuthenticationError):
            self.create(
                {"result.json": source},
                {"n" * 129: self.binding("result.json", raw)},
                run_id="name-over",
            )

        record = json.loads(commit.read_text())
        real_depth = module._json_depth(record)
        with patch.object(module, "MAX_COMMIT_DEPTH", real_depth):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="reader", result_sha256=RESULT_DIGEST,
                snapshot_root=self.snapshot_root,
            )
        with patch.object(module, "MAX_COMMIT_DEPTH", real_depth - 1):
            with self.assertRaises(ProducerAuthenticationError):
                recover_evidence_snapshot_publication(
                    surface="screening", run_id="reader",
                    result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                )

    def test_archive_and_member_path_limits_allow_equality_only(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        raw = b"x"
        source = self.write("limit-path", raw)
        component = "a" * 12
        member = component + "/x"
        with patch.object(module, "MAX_COMPONENT_BYTES", len(component)), patch.object(
            module, "MAX_MEMBER_PATH_BYTES", len(member)
        ):
            snapshot = self.create(
                {member: source}, {"x": self.binding(member, raw)},
                run_id="path-equal",
            )
        commit_record = json.loads(snapshot.commit_path.read_text())
        archive = self.snapshot_root / commit_record["archive_name"]
        archive_size = archive.stat().st_size
        with patch.object(module, "MAX_ARCHIVE_BYTES", archive_size):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="path-equal",
                result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
            )
        with patch.object(module, "MAX_ARCHIVE_BYTES", archive_size - 1):
            with self.assertRaises(ProducerAuthenticationError):
                recover_evidence_snapshot_publication(
                    surface="screening", run_id="path-equal",
                    result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
                )
        with patch.object(module, "MAX_COMPONENT_BYTES", len(component) - 1):
            with self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {member: source}, {"x": self.binding(member, raw)},
                    run_id="component-over",
                )
        with patch.object(module, "MAX_MEMBER_PATH_BYTES", len(member) - 1):
            with self.assertRaises(ProducerAuthenticationError):
                self.create(
                    {member: source}, {"x": self.binding(member, raw)},
                    run_id="path-over",
                )

    def test_root_durability_failure_leaves_commit_for_explicit_recovery(self):
        root_identity = (self.snapshot_root.stat().st_dev, self.snapshot_root.stat().st_ino)
        real_fsync = os.fsync

        def fail_root(fd: int) -> None:
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == root_identity:
                raise OSError("injected root durability failure")
            real_fsync(fd)

        with patch(
            "audience_panel_builder.population.validation.evidence_snapshot.os.fsync",
            side_effect=fail_root,
        ), self.assertRaises(ProducerPublicationIndeterminate):
            self.create(run_id="root-durability")
        commit = self.snapshot_root / (
            "screening--root-durability--" + RESULT_DIGEST[7:] + ".snapshot.json"
        )
        self.assertTrue(commit.exists())
        recovered = recover_evidence_snapshot_publication(
            surface="screening", run_id="root-durability",
            result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
        )
        self.assertEqual(commit, recovered.commit_path)

    def test_archive_replacement_during_commit_write_fails_joint_validation(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        real_write_all = module._write_all
        replaced = False

        def replace_archive(fd: int, value: bytes) -> None:
            nonlocal replaced
            if not replaced and value.startswith(b"{") and b'"schema_version"' in value:
                replaced = True
                archive = next(self.snapshot_root.glob(".snapshot-*.zip"))
                moved = archive.with_suffix(".moved")
                archive.rename(moved)
                archive.write_bytes(b"hostile replacement")
                os.chmod(archive, 0o400)
            real_write_all(fd, value)

        with patch.object(module, "_write_all", side_effect=replace_archive), self.assertRaises(
            ProducerAuthenticationError
        ):
            self.create(run_id="archive-write-race")
        self.assertTrue(replaced)
        self.assertTrue(
            self.snapshot_root.joinpath(
                "screening--archive-write-race--"
                + RESULT_DIGEST[7:]
                + ".snapshot.json"
            ).exists()
        )

    def test_final_recovery_read_rechecks_commit_entry_and_root_chain(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        snapshot = self.create(run_id="final-read")
        commit = snapshot.commit_path
        root_identity = (self.snapshot_root.stat().st_dev, self.snapshot_root.stat().st_ino)
        real_fsync = os.fsync
        real_read_bounded = module._read_bounded_fd
        after_recovery_barrier = False
        replaced_commit = False

        def observe_barrier(fd: int) -> None:
            nonlocal after_recovery_barrier
            real_fsync(fd)
            value = os.fstat(fd)
            if (value.st_dev, value.st_ino) == root_identity:
                after_recovery_barrier = True

        def replace_after_commit_read(fd: int, maximum: int, *, label: str):
            nonlocal replaced_commit
            value = real_read_bounded(fd, maximum, label=label)
            if after_recovery_barrier and label == "snapshot commit" and not replaced_commit:
                replaced_commit = True
                original = commit.read_bytes()
                moved = commit.with_suffix(".saved")
                commit.rename(moved)
                commit.write_bytes(original)
                os.chmod(commit, 0o400)
            return value

        with patch.object(module.os, "fsync", side_effect=observe_barrier), patch.object(
            module, "_read_bounded_fd", side_effect=replace_after_commit_read
        ), self.assertRaises(ProducerAuthenticationError):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="final-read",
                result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
            )
        self.assertTrue(replaced_commit)

        # Repeat with a pathname-level root replacement after the final archive read.
        second_root = self.base / "second-snapshots"
        second_root.mkdir()
        second_allowed = self.base / "second-allowed"
        second_allowed.mkdir()
        raw = b'{"second":true}\n'
        source = second_allowed / "result.json"
        source.write_bytes(raw)
        create_evidence_snapshot(
            surface="screening", run_id="root-final-read",
            result_sha256=RESULT_DIGEST,
            sources={"result.json": source},
            bindings={"result": self.binding("result.json", raw)},
            allowed_roots=[second_allowed], snapshot_root=second_root,
        )
        second_identity = (second_root.stat().st_dev, second_root.stat().st_ino)
        after_recovery_barrier = False
        swapped_root = False

        def observe_second_barrier(fd: int) -> None:
            nonlocal after_recovery_barrier
            real_fsync(fd)
            value = os.fstat(fd)
            if (value.st_dev, value.st_ino) == second_identity:
                after_recovery_barrier = True

        def swap_after_archive_read(fd: int, maximum: int, *, label: str):
            nonlocal swapped_root
            value = real_read_bounded(fd, maximum, label=label)
            if after_recovery_barrier and label == "snapshot archive" and not swapped_root:
                swapped_root = True
                second_root.rename(second_root.with_name("second-snapshots-saved"))
                second_root.mkdir()
            return value

        with patch.object(module.os, "fsync", side_effect=observe_second_barrier), patch.object(
            module, "_read_bounded_fd", side_effect=swap_after_archive_read
        ), self.assertRaises(ProducerAuthenticationError):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="root-final-read",
                result_sha256=RESULT_DIGEST, snapshot_root=second_root,
            )
        self.assertTrue(swapped_root)

    def test_open_retains_the_recovered_joint_across_extraction(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        original = b'{"owner":"original"}\n'
        attacker = b'{"owner":"attacker"}\n'
        snapshot = self.create(
            {"result.json": self.write("gap-original.json", original)},
            {"result": self.binding("result.json", original)},
            run_id="gap",
        )
        attacker_root = self.base / "attacker-snapshots"
        attacker_root.mkdir()
        attacker_allowed = self.base / "attacker-allowed"
        attacker_allowed.mkdir()
        attacker_source = attacker_allowed / "result.json"
        attacker_source.write_bytes(attacker)
        attacker_snapshot = create_evidence_snapshot(
            surface="screening", run_id="gap", result_sha256=RESULT_DIGEST,
            sources={"result.json": attacker_source},
            bindings={"result": self.binding("result.json", attacker)},
            allowed_roots=[attacker_allowed], snapshot_root=attacker_root,
        )
        attacker_record = json.loads(attacker_snapshot.commit_path.read_text())
        original_close = module._close_joint
        replaced = False

        def replace_pair(joint) -> None:
            nonlocal replaced
            original_close(joint)
            if replaced:
                return
            replaced = True
            commit = snapshot.commit_path
            commit.unlink()
            commit.write_bytes(attacker_snapshot.commit_path.read_bytes())
            os.chmod(commit, 0o400)
            archive = self.snapshot_root / attacker_record["archive_name"]
            archive.write_bytes(
                (attacker_root / attacker_record["archive_name"]).read_bytes()
            )
            os.chmod(archive, 0o400)

        with patch.object(module, "_close_joint", side_effect=replace_pair):
            with open_evidence_snapshot(
                surface="screening", run_id="gap", result_sha256=RESULT_DIGEST,
                snapshot_root=self.snapshot_root,
            ) as validated:
                self.assertEqual(original, validated.resolve_member("result").read_bytes())
        self.assertTrue(replaced)

    def test_capability_cannot_be_token_forged_or_claim_mutated(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        for leaked_name in (
            "_CAPABILITY_TOKEN",
            "_CapabilityState",
            "_register_capability",
            "_lookup_capability",
            "_invalidate_capability",
        ):
            with self.subTest(leaked_name=leaked_name):
                self.assertFalse(hasattr(module, leaked_name))
        fabricated = object.__new__(ValidatedEvidenceSnapshot)
        with self.assertRaises(ProducerEvidenceError):
            fabricated.require_active()
        with self.assertRaises(ProducerEvidenceError):
            fabricated.resolve_member("result")
        with self.assertRaises((AttributeError, TypeError, ProducerEvidenceError)):
            object.__setattr__(fabricated, "_active", True)

        self.create(run_id="immutable")
        with open_evidence_snapshot(
            surface="screening", run_id="immutable", result_sha256=RESULT_DIGEST,
            snapshot_root=self.snapshot_root,
        ) as validated:
            expected = validated.snapshot_id
            with self.assertRaises((AttributeError, TypeError, ProducerEvidenceError)):
                object.__setattr__(validated, "_snapshot_id", "forged")
            self.assertEqual(expected, validated.snapshot_id)

    def test_source_is_reauthenticated_at_final_archive_commitment(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        before = b"before"
        source = self.write("final-source", before)
        original = module._validate_archive_fd
        calls = 0

        def mutate_after_final_archive_check(root, published):
            nonlocal calls
            members = original(root, published)
            calls += 1
            if calls == 2:
                source.write_bytes(b"after!")
            return members

        with patch.object(
            module, "_validate_archive_fd", side_effect=mutate_after_final_archive_check
        ), self.assertRaises(ProducerAuthenticationError):
            self.create(
                {"result.json": source},
                {"result": self.binding("result.json", before)},
                run_id="final-source",
            )
        self.assertGreaterEqual(calls, 2)

    def test_deep_commit_and_cleanup_identity_failure_stay_closed(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        snapshot = self.create(run_id="deep")
        commit = snapshot.commit_path
        os.chmod(commit, 0o600)
        commit.write_text("[" * 4_000 + "0" + "]" * 4_000)
        os.chmod(commit, 0o400)
        with self.assertRaises(ProducerAuthenticationError):
            recover_evidence_snapshot_publication(
                surface="screening", run_id="deep", result_sha256=RESULT_DIGEST,
                snapshot_root=self.snapshot_root,
            )

        extraction = Path(
            tempfile.mkdtemp(
                prefix="validated-evidence-",
                dir=Path(tempfile.gettempdir()).resolve(),
            )
        )
        os.chmod(extraction, 0o700)
        fd = os.open(extraction, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        moved = extraction.with_name(extraction.name + "-moved")
        extraction.rename(moved)
        extraction.mkdir()
        real_close = os.close
        closed = 0

        def count_close(candidate: int) -> None:
            nonlocal closed
            if candidate == fd:
                closed += 1
            real_close(candidate)

        with patch.object(module.os, "close", side_effect=count_close), self.assertRaises(
            ProducerEvidenceError
        ):
            module._cleanup_extraction(extraction, fd, identity)
        self.assertEqual(1, closed)

    def test_canonical_zip_rejects_closed_header_and_eocd_matrix(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        snapshot = self.create(run_id="zip-matrix")
        record = json.loads(snapshot.commit_path.read_text())
        archive = bytearray(
            (self.snapshot_root / record["archive_name"]).read_bytes()
        )
        eocd = len(archive) - module._EOCD.size
        central = struct.unpack_from("<IHHHHIIH", archive, eocd)[6]
        mutations = {
            "local-signature": (0, 0x01),
            "local-version": (4, 0x01),
            "local-descriptor": (6, 0x08),
            "local-compression": (8, 0x08),
            "local-time": (10, 0x01),
            "local-date": (12, 0x01),
            "local-crc": (14, 0x01),
            "local-compressed-size": (18, 0x01),
            "local-size": (22, 0x01),
            "local-name-length": (26, 0x01),
            "local-extra": (28, 0x01),
            "central-signature": (central, 0x01),
            "central-create-version": (central + 4, 0x01),
            "central-extract-version": (central + 6, 0x01),
            "central-flag": (central + 8, 0x01),
            "central-compression": (central + 10, 0x01),
            "central-time": (central + 12, 0x01),
            "central-date": (central + 14, 0x01),
            "central-crc": (central + 16, 0x01),
            "central-compressed-size": (central + 20, 0x01),
            "central-size": (central + 24, 0x01),
            "central-name-length": (central + 28, 0x01),
            "central-extra": (central + 30, 0x01),
            "central-comment": (central + 32, 0x01),
            "central-disk": (central + 34, 0x01),
            "central-internal": (central + 36, 0x01),
            "central-external": (central + 38, 0x01),
            "central-offset": (central + 42, 0x01),
            "eocd-disk": (eocd + 4, 0x01),
            "eocd-central-disk": (eocd + 6, 0x01),
            "eocd-count-on-disk": (eocd + 8, 0x01),
            "eocd-total-count": (eocd + 10, 0x01),
            "eocd-size": (eocd + 12, 0x01),
            "eocd-offset": (eocd + 16, 0x01),
            "eocd-comment": (eocd + 20, 0x01),
        }
        for name, (offset, delta) in mutations.items():
            changed = bytearray(archive)
            changed[offset] ^= delta
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                module._parse_archive_bytes(changed)

        for field_offset in (18, 22, central + 20, central + 24, central + 42):
            changed = bytearray(archive)
            struct.pack_into("<I", changed, field_offset, 0xFFFFFFFF)
            with self.subTest(zip64_offset=field_offset), self.assertRaises(
                ProducerAuthenticationError
            ):
                module._parse_archive_bytes(changed)

    def test_source_component_replacements_fail_at_every_checkpoint(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        def build_case(name: str):
            case = self.base / name
            allowed = case / "allowed"
            source = allowed / "one" / "two" / "result.json"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"before")
            snapshots = case / "snapshots"
            snapshots.mkdir()
            return allowed, source, snapshots

        def replace(target: Path, source: Path) -> None:
            remaining = source.relative_to(target) if target.is_dir() else None
            moved = target.with_name(target.name + ".moved")
            target.rename(moved)
            if moved.is_dir():
                target.mkdir()
                assert remaining is not None
                replacement_source = target / remaining
                replacement_source.parent.mkdir(parents=True, exist_ok=True)
                replacement_source.write_bytes(b"before")
            else:
                target.write_bytes(b"after!")

        # Immediately after preflight lstat, before descriptor open.
        for depth in range(4):
            allowed, source, snapshots = build_case(f"pre-open-{depth}")
            target = (allowed, allowed / "one", allowed / "one" / "two", source)[depth]
            target_match = module._canonical_path(target)
            real_lstat = os.lstat
            fired = False

            def race_lstat(
                path, *, dir_fd=None, target=target, target_match=target_match,
                source=source,
            ):
                nonlocal fired
                value = real_lstat(path, dir_fd=dir_fd)
                if not fired and Path(path) == target_match:
                    fired = True
                    replace(target, source)
                return value

            with self.subTest(checkpoint="pre-open", depth=depth), patch.object(
                module.os, "lstat", side_effect=race_lstat
            ), self.assertRaises(ProducerAuthenticationError):
                create_evidence_snapshot(
                    surface="screening", run_id=f"pre-open-{depth}",
                    result_sha256=RESULT_DIGEST,
                    sources={"runtime/one/two/result.json": source},
                    bindings={
                        "result": self.binding(
                            "runtime/one/two/result.json", b"before"
                        )
                    },
                    allowed_roots=[allowed], snapshot_root=snapshots,
                )
            self.assertTrue(fired)

        # After archive copy but before its first source reauthentication.
        for depth in range(4):
            allowed, source, snapshots = build_case(f"post-copy-{depth}")
            target = (allowed, allowed / "one", allowed / "one" / "two", source)[depth]
            original_write_archive = module._write_archive

            def race_after_copy(fd, sources, target=target, source=source):
                original_write_archive(fd, sources)
                replace(target, source)

            with self.subTest(checkpoint="post-copy", depth=depth), patch.object(
                module, "_write_archive", side_effect=race_after_copy
            ), self.assertRaises(ProducerAuthenticationError):
                create_evidence_snapshot(
                    surface="screening", run_id=f"post-copy-{depth}",
                    result_sha256=RESULT_DIGEST,
                    sources={"runtime/one/two/result.json": source},
                    bindings={
                        "result": self.binding(
                            "runtime/one/two/result.json", b"before"
                        )
                    },
                    allowed_roots=[allowed], snapshot_root=snapshots,
                )

    def test_cleanup_is_allowlisted_symlink_safe_and_path_reuse_invalidates(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        temp_root = Path(tempfile.gettempdir()).resolve()
        outside = self.base / "outside-cleanup"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_bytes(b"untouched")
        extraction = Path(
            tempfile.mkdtemp(prefix="validated-evidence-", dir=temp_root)
        )
        nested = extraction / "nested"
        nested.mkdir()
        (nested / "outside-link").symlink_to(outside, target_is_directory=True)
        fd = os.open(extraction, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        module._cleanup_extraction(extraction, fd, identity)
        self.assertFalse(extraction.exists())
        self.assertEqual(b"untouched", marker.read_bytes())

        forbidden = self.base / "not-allowlisted"
        forbidden.mkdir()
        forbidden_fd = os.open(
            forbidden, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        forbidden_identity = (
            os.fstat(forbidden_fd).st_dev,
            os.fstat(forbidden_fd).st_ino,
        )
        with self.assertRaises(ProducerEvidenceError):
            module._cleanup_extraction(
                forbidden, forbidden_fd, forbidden_identity
            )
        with self.assertRaises(OSError):
            os.fstat(forbidden_fd)
        self.assertTrue(forbidden.exists())

        self.create(run_id="path-reuse")
        replacement: Path | None = None
        with self.assertRaises(ProducerEvidenceError):
            with open_evidence_snapshot(
                surface="screening", run_id="path-reuse",
                result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
            ) as validated:
                member = validated.resolve_member("result")
                extraction_root = member.parent
                moved = extraction_root.with_name(extraction_root.name + "-moved")
                extraction_root.rename(moved)
                extraction_root.mkdir()
                replacement = extraction_root
                with self.assertRaises(ProducerEvidenceError):
                    validated.require_active()
        self.assertIsNotNone(replacement)
        self.assertTrue(replacement.exists())

    def test_real_reader_member_and_authenticated_counter_boundaries(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        def canonical_empty_archive(count: int) -> bytearray:
            local = bytearray()
            central_rows: list[bytes] = []
            for index in range(count):
                name = f"member-{index:04d}".encode("ascii")
                offset = len(local)
                local.extend(
                    module._LOCAL.pack(
                        module._LOCAL_SIGNATURE, 20, 0, 0,
                        module._DOS_TIME, module._DOS_DATE,
                        0, 0, 0, len(name), 0,
                    )
                )
                local.extend(name)
                central_rows.append(
                    module._CENTRAL.pack(
                        module._CENTRAL_SIGNATURE, (3 << 8) | 20, 20,
                        0, 0, module._DOS_TIME, module._DOS_DATE,
                        0, 0, 0, len(name), 0, 0, 0, 0,
                        module._EXTERNAL_ATTR, offset,
                    ) + name
                )
            central = b"".join(central_rows)
            return bytearray(
                local
                + central
                + module._EOCD.pack(
                    module._EOCD_SIGNATURE, 0, 0, count, count,
                    len(central), len(local), 0,
                )
            )

        exact = canonical_empty_archive(4_096)
        self.assertEqual(4_096, len(module._parse_archive_bytes(exact)))
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(canonical_empty_archive(4_097))

        exact_counters = {
            "member_count": 4_096,
            "largest_member_bytes": 256 * 1024 * 1024,
            "total_uncompressed_bytes": 1024 * 1024 * 1024,
            "archive_bytes": 1024 * 1024 * 1024,
        }
        module._validate_authenticated_resource_counters(**exact_counters)
        for field in exact_counters:
            changed = dict(exact_counters)
            changed[field] += 1
            with self.subTest(field=field), self.assertRaises(
                ProducerAuthenticationError
            ):
                module._validate_authenticated_resource_counters(**changed)

    def test_joint_member_set_layout_and_content_splice_matrix(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        raw_a = b"A"
        raw_b = b"B"
        snapshot = self.create(
            {
                "a": self.write("matrix-a", raw_a),
                "b": self.write("matrix-b", raw_b),
            },
            {"a": self.binding("a", raw_a)},
            run_id="member-matrix",
        )
        record = json.loads(snapshot.commit_path.read_text())
        archive_path = self.snapshot_root / record["archive_name"]
        archive = bytearray(archive_path.read_bytes())
        parsed = module._parse_archive_bytes(archive)
        self.assertEqual(("a", "b"), tuple(member.path for member in parsed))

        # Duplicate and reordered canonical names are rejected by the real reader.
        def rename_central_names(names: tuple[bytes, bytes]) -> bytearray:
            changed = bytearray(archive)
            eocd = len(changed) - module._EOCD.size
            central = struct.unpack_from("<IHHHHIIH", changed, eocd)[6]
            cursor = central
            for name in names:
                row = module._CENTRAL.unpack_from(changed, cursor)
                name_start = cursor + module._CENTRAL.size
                changed[name_start:name_start + row[10]] = name
                cursor = name_start + row[10]
            return changed

        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(rename_central_names((b"a", b"a")))
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(rename_central_names((b"b", b"a")))

        # A gap before the central directory, an overlap/local-offset change,
        # and paired count/offset changes all remain invalid.
        eocd = len(archive) - module._EOCD.size
        central = struct.unpack_from("<IHHHHIIH", archive, eocd)[6]
        gap = bytearray(archive[:central] + b"x" + archive[central:])
        gap_eocd = len(gap) - module._EOCD.size
        struct.pack_into("<I", gap, gap_eocd + 16, central + 1)
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(gap)
        overlap = bytearray(archive)
        second_central = central + module._CENTRAL.size + 1
        struct.pack_into("<I", overlap, second_central + 42, 0)
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(overlap)
        paired = bytearray(archive)
        struct.pack_into("<H", paired, eocd + 8, 1)
        struct.pack_into("<H", paired, eocd + 10, 1)
        struct.pack_into("<I", paired, eocd + 12, len(archive) - central - module._EOCD.size)
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(paired)

        # CRC and content splices fail independently.
        content = bytearray(archive)
        content[parsed[0].data_offset] ^= 0x01
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(content)
        crc = bytearray(archive)
        crc[14] ^= 0x01
        with self.assertRaises(ProducerAuthenticationError):
            module._parse_archive_bytes(crc)

        # Extra, missing, duplicate, and reordered commit member manifests are
        # rejected even with a recomputed canonical self-hash.
        for name in ("extra", "missing", "duplicate", "reordered"):
            case_root = self.base / f"manifest-{name}"
            case_root.mkdir()
            case_allowed = self.base / f"manifest-{name}-allowed"
            case_allowed.mkdir()
            one = case_allowed / "a"
            two = case_allowed / "b"
            one.write_bytes(raw_a)
            two.write_bytes(raw_b)
            case_snapshot = create_evidence_snapshot(
                surface="screening", run_id=f"manifest-{name}",
                result_sha256=RESULT_DIGEST,
                sources={"a": one, "b": two},
                bindings={"a": self.binding("a", raw_a)},
                allowed_roots=[case_allowed], snapshot_root=case_root,
            )
            case_record = json.loads(case_snapshot.commit_path.read_text())
            rows = list(case_record["members"])
            if name == "extra":
                rows.append({
                    "path": "c", "byte_count": 0,
                    "raw_bytes_sha256": digest(b""),
                })
            elif name == "missing":
                rows.pop()
            elif name == "duplicate":
                rows.append(dict(rows[-1]))
            else:
                rows.reverse()
            case_record["members"] = rows
            case_record["snapshot_sha256"] = None
            case_record["snapshot_sha256"] = digest(
                (
                    json.dumps(
                        case_record, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            os.chmod(case_snapshot.commit_path, 0o600)
            case_snapshot.commit_path.write_bytes(
                (
                    json.dumps(
                        case_record, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            os.chmod(case_snapshot.commit_path, 0o400)
            with self.subTest(manifest=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                recover_evidence_snapshot_publication(
                    surface="screening", run_id=f"manifest-{name}",
                    result_sha256=RESULT_DIGEST, snapshot_root=case_root,
                )

    def test_extraction_and_cleanup_failure_matrix(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        snapshot = self.create(run_id="extract-race")
        record = json.loads(snapshot.commit_path.read_text())
        archive_path = self.snapshot_root / record["archive_name"]
        real_pread = os.pread
        raced = False

        def mutate_archive(fd: int, size: int, offset: int) -> bytes:
            nonlocal raced
            value = real_pread(fd, size, offset)
            if not raced:
                raced = True
                original = archive_path.read_bytes()
                os.chmod(archive_path, 0o600)
                archive_path.write_bytes(
                    original[:offset] + bytes([original[offset] ^ 1])
                    + original[offset + 1:]
                )
                os.chmod(archive_path, 0o400)
            return value

        with patch.object(module.os, "pread", side_effect=mutate_archive), self.assertRaises(
            ProducerAuthenticationError
        ):
            with open_evidence_snapshot(
                surface="screening", run_id="extract-race",
                result_sha256=RESULT_DIGEST, snapshot_root=self.snapshot_root,
            ):
                self.fail("mutated extraction must not yield")
        self.assertTrue(raced)

        temp_root = Path(tempfile.gettempdir()).resolve()
        outside = self.base / "cleanup-traversal-outside"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_bytes(b"untouched")
        traversal = Path(
            tempfile.mkdtemp(prefix="validated-evidence-", dir=temp_root)
        )
        nested = traversal / "nested"
        nested.mkdir()
        traversal_fd = os.open(
            traversal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        traversal_identity = (
            os.fstat(traversal_fd).st_dev,
            os.fstat(traversal_fd).st_ino,
        )
        real_open = os.open
        swapped = False

        def swap_nested(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and path == "nested" and dir_fd == traversal_fd:
                swapped = True
                nested.rename(traversal / "nested-moved")
                nested.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with patch.object(module.os, "open", side_effect=swap_nested), self.assertRaises(
            ProducerEvidenceError
        ):
            module._cleanup_extraction(
                traversal, traversal_fd, traversal_identity
            )
        self.assertTrue(swapped)
        self.assertEqual(b"untouched", marker.read_bytes())
        with self.assertRaises(OSError):
            os.fstat(traversal_fd)

        final_root = Path(
            tempfile.mkdtemp(prefix="validated-evidence-", dir=temp_root)
        )
        final_fd = os.open(
            final_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        final_identity = (os.fstat(final_fd).st_dev, os.fstat(final_fd).st_ino)
        real_rmdir = os.rmdir

        def fail_final_rmdir(path, *, dir_fd=None):
            if Path(path) == final_root and dir_fd is None:
                raise OSError("injected final rmdir failure")
            return real_rmdir(path, dir_fd=dir_fd)

        with patch.object(module.os, "rmdir", side_effect=fail_final_rmdir), self.assertRaises(
            ProducerEvidenceError
        ):
            module._cleanup_extraction(final_root, final_fd, final_identity)
        with self.assertRaises(OSError):
            os.fstat(final_fd)
        self.assertTrue(final_root.exists())

        closed_snapshot = self.create(run_id="closed-fd")
        del closed_snapshot
        captured_fd: int | None = None
        real_safe_extract = module._safe_extract

        def capture_fd(joint):
            nonlocal captured_fd
            result = real_safe_extract(joint)
            captured_fd = result[1]
            return result

        with self.assertRaises(ProducerEvidenceError):
            with patch.object(module, "_safe_extract", side_effect=capture_fd):
                with open_evidence_snapshot(
                    surface="screening", run_id="closed-fd",
                    result_sha256=RESULT_DIGEST,
                    snapshot_root=self.snapshot_root,
                ) as validated:
                    self.assertIsNotNone(captured_fd)
                    os.close(captured_fd)
                    with self.assertRaises(ProducerEvidenceError):
                        validated.require_active()

    def test_archive_and_commit_mutation_replacement_stage_matrix(self):
        from audience_panel_builder.population.validation import evidence_snapshot as module

        def target_matches(fd: int, target: str) -> bool:
            try:
                prefix = os.pread(fd, 2, 0)
            except OSError:
                return False
            return prefix == (b"PK" if target == "archive" else b'{"')

        for target in ("archive", "commit"):
            for attack in ("same-inode", "entry-replacement"):
                for phase in ("initial", "chmod", "file-fsync", "post-root-fsync"):
                    with self.subTest(target=target, attack=attack, phase=phase):
                        case = self.base / f"stage-{target}-{attack}-{phase}"
                        allowed = case / "allowed"
                        snapshots = case / "snapshots"
                        allowed.mkdir(parents=True)
                        snapshots.mkdir()
                        source = allowed / "result.json"
                        raw = b'{"stage":true}\n'
                        source.write_bytes(raw)
                        run_id = f"{target}-{attack}-{phase}"
                        commit_path = snapshots / (
                            f"screening--{run_id}--{RESULT_DIGEST[7:]}.snapshot.json"
                        )
                        injected = False

                        def persistent_path() -> Path:
                            if target == "commit":
                                return commit_path
                            return next(snapshots.glob(".snapshot-*.zip"))

                        def inject(fd: int) -> None:
                            nonlocal injected
                            if injected:
                                return
                            injected = True
                            if attack == "same-inode":
                                first = os.pread(fd, 1, 0)
                                os.pwrite(fd, bytes([first[0] ^ 1]), 0)
                                return
                            path = persistent_path()
                            original = path.read_bytes()
                            mode = stat.S_IMODE(os.fstat(fd).st_mode)
                            path.rename(path.with_suffix(path.suffix + ".saved"))
                            path.write_bytes(original)
                            os.chmod(path, mode)

                        patches = []
                        if phase == "initial":
                            real_read = module._read_bounded_fd

                            def after_initial(fd, maximum, *, label):
                                value = real_read(fd, maximum, label=label)
                                expected = (
                                    "new snapshot archive"
                                    if target == "archive"
                                    else "new snapshot commit"
                                )
                                if label == expected:
                                    inject(fd)
                                return value

                            patches.append(
                                patch.object(
                                    module, "_read_bounded_fd",
                                    side_effect=after_initial,
                                )
                            )
                        elif phase == "chmod":
                            real_fchmod = os.fchmod

                            def after_chmod(fd, mode):
                                real_fchmod(fd, mode)
                                if mode == 0o400 and target_matches(fd, target):
                                    inject(fd)

                            patches.append(
                                patch.object(
                                    module.os, "fchmod", side_effect=after_chmod
                                )
                            )
                        elif phase == "file-fsync":
                            real_fsync = os.fsync

                            def after_file_fsync(fd):
                                real_fsync(fd)
                                if (
                                    stat.S_ISREG(os.fstat(fd).st_mode)
                                    and target_matches(fd, target)
                                ):
                                    inject(fd)

                            patches.append(
                                patch.object(
                                    module.os, "fsync",
                                    side_effect=after_file_fsync,
                                )
                            )
                        else:
                            real_fsync = os.fsync
                            root_identity = (
                                snapshots.stat().st_dev,
                                snapshots.stat().st_ino,
                            )
                            root_calls = 0

                            def after_root_fsync(fd):
                                nonlocal root_calls
                                real_fsync(fd)
                                value = os.fstat(fd)
                                if (value.st_dev, value.st_ino) == root_identity:
                                    root_calls += 1
                                    target_call = 1 if target == "archive" else 2
                                    if root_calls == target_call:
                                        path = persistent_path()
                                        os.chmod(path, 0o600)
                                        path_fd = os.open(
                                            path, os.O_RDWR | os.O_NOFOLLOW
                                        )
                                        try:
                                            inject(path_fd)
                                        finally:
                                            os.close(path_fd)
                                        os.chmod(path, 0o400)

                            patches.append(
                                patch.object(
                                    module.os, "fsync",
                                    side_effect=after_root_fsync,
                                )
                            )

                        with patches[0], self.assertRaises(ProducerEvidenceError):
                            create_evidence_snapshot(
                                surface="screening", run_id=run_id,
                                result_sha256=RESULT_DIGEST,
                                sources={"result.json": source},
                                bindings={
                                    "result": self.binding("result.json", raw)
                                },
                                allowed_roots=[allowed], snapshot_root=snapshots,
                            )
                        self.assertTrue(injected)


if __name__ == "__main__":
    unittest.main()
