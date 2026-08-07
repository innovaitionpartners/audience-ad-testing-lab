from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import venv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerRuntimeUnavailable,
)
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    ValidatedEvidenceSnapshot,
    create_evidence_snapshot,
    open_evidence_snapshot,
)
from audience_panel_builder.population.validation.producer_replay import (  # noqa: E402
    BOOTSTRAP_SHA256,
    _MAX_TRACE_BYTES,
    _SandboxProvider,
    _build_sandbox_vector,
    _execute_sandbox,
    _fixed_environment,
    _sandbox_profile,
    _trusted_provider,
    replay_producer,
)
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    _resolve_runtime_for_replay,
)
from audience_panel_builder.population.validation.producer_semantics import (  # noqa: E402
    REPLAY_BOOTSTRAP_SOURCE,
    _discover_dependency_closure,
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def producer_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            ensure_ascii=True,
        ).encode("utf-8")
        + b"\n"
    )


class ReplayFixture:
    def __init__(
        self,
        case: unittest.TestCase,
        *,
        surface: str = "complete_exposure_ordering",
        second_write: bool = False,
        non_ascii: bool = False,
        scientific_imports: bool = False,
        delay_seconds: float = 0.0,
        temporary_root: Path | None = None,
    ):
        self.case = case
        self.surface = surface
        self.temporary = tempfile.TemporaryDirectory(
            dir=(
                None
                if temporary_root is None
                else os.fspath(temporary_root)
            )
        )
        case.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.sources = self.base / "sources"
        self.snapshots = self.base / "snapshots"
        self.sources.mkdir()
        self.snapshots.mkdir()
        self.run_id = "replay-run-001"
        self.result_document = {
            "study_id": self.run_id,
            "status": "ok",
            "label": "café" if non_ascii else "acme",
        }
        self.result_raw = producer_bytes(self.result_document)
        self.result_binding = {
            "path": (
                "boundary-results.json"
                if surface == "pairwise_boundary_ordering"
                else "screening-model-results.json"
            ),
            "raw_bytes_sha256": digest(self.result_raw),
            "canonical_document_sha256": digest(
                canonical_json_bytes(self.result_document)
            ),
            "record_count": None,
        }
        self._write_runtime(
            second_write=second_write,
            scientific_imports=scientific_imports,
        )
        self.closure = _discover_dependency_closure(self.sources)
        self.role_members: dict[str, tuple[str, bytes, int | None]] = {}
        if surface == "pairwise_boundary_ordering":
            self._add_json("study_manifest", "inputs/study-manifest.json", {
                "study_id": self.run_id,
                "payload": self.result_document,
                "second_write": second_write,
                "delay_seconds": delay_seconds,
            })
            self._add_json(
                "screening_result",
                "inputs/screening-model-results.json",
                {"study_id": self.run_id},
            )
            self._add_jsonl(
                "boundary_response_projection",
                "inputs/boundary-response-projection.jsonl",
                [{"study_id": self.run_id}],
            )
            self.binding_names = {
                "study_manifest": "study_manifest",
                "screening_result": "screening_result",
                "boundary_response_projection": "boundary_response_projection",
                "result": "result",
            }
        else:
            self._add_json("study_manifest", "inputs/study-manifest.json", {
                "study_id": self.run_id,
                "payload": self.result_document,
                "second_write": second_write,
                "delay_seconds": delay_seconds,
            })
            self._add_json(
                "screening_jobs",
                "inputs/screening-jobs.json",
                {"study_id": self.run_id},
            )
            self._add_jsonl(
                "screening_response_projection",
                "inputs/screening-response-projection.jsonl",
                [{"study_id": self.run_id}],
            )
            self._add_json(
                "recovery_configuration",
                "inputs/recovery-configuration.json",
                {"version": "fixture-v1"},
            )
            self.binding_names = {
                "study_manifest": "study_manifest",
                "screening_jobs": "screening_jobs",
                "screening_response_projection": "screening_response_projection",
                "recovery_configuration": "recovery_configuration",
                "result": "result",
            }
        self._add_raw(
            "result",
            "results/" + str(self.result_binding["path"]),
            self.result_raw,
            None,
        )
        source_map: dict[str, Path] = {}
        bindings: dict[str, dict[str, object]] = {}
        for row in self.closure:
            member = "runtime/" + str(row["path"])
            source_map[member] = self.sources / str(row["path"])
        for role, (member, raw, count) in self.role_members.items():
            source_map[member] = self.sources / member
            document = (
                b"".join(
                    canonical_json_bytes(json.loads(line))
                    for line in raw.decode("utf-8").splitlines()
                )
                if count is not None
                else canonical_json_bytes(json.loads(raw))
            )
            bindings[role] = {
                "member_path": member,
                "raw_bytes_sha256": digest(raw),
                "canonical_document_sha256": digest(document),
                "record_count": count,
            }
        snapshot = create_evidence_snapshot(
            surface=self.surface,
            run_id=self.run_id,
            result_sha256=str(self.result_binding["canonical_document_sha256"]),
            sources=source_map,
            bindings=bindings,
            allowed_roots=[self.sources],
            snapshot_root=self.snapshots,
        )
        self.snapshot_sha256 = snapshot.snapshot_sha256

    def _write(self, relative: str, raw: bytes) -> Path:
        path = self.sources / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def _add_raw(
        self, role: str, relative: str, raw: bytes, count: int | None
    ) -> None:
        self._write(relative, raw)
        self.role_members[role] = (relative, raw, count)

    def _add_json(self, role: str, relative: str, value: object) -> None:
        self._add_raw(
            role,
            relative,
            json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            None,
        )

    def _add_jsonl(
        self, role: str, relative: str, records: list[dict[str, object]]
    ) -> None:
        raw = b"".join(canonical_json_bytes(record) for record in records)
        self._add_raw(role, relative, raw, len(records))

    def _write_runtime(
        self, *, second_write: bool, scientific_imports: bool
    ) -> None:
        del second_write
        self._write(
            "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
            (
                "from audience_lab.fixture_producer import main\n"
                "raise SystemExit(main())\n"
            ).encode("utf-8"),
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/__init__.py",
            b"",
        )
        scientific_source = "import numpy\nimport scipy\n" if scientific_imports else ""
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/fixture_producer.py",
            (
                scientific_source
                + "import argparse\n"
                "import json\n"
                "import time\n"
                "from pathlib import Path\n"
                "\n"
                "def main():\n"
                "    parser = argparse.ArgumentParser()\n"
                "    commands = parser.add_subparsers(dest='command', required=True)\n"
                "    screening = commands.add_parser('screening')\n"
                "    screening.add_argument('--manifest', required=True)\n"
                "    screening.add_argument('--jobs', required=True)\n"
                "    screening.add_argument('--responses', required=True)\n"
                "    screening.add_argument('--dispatch-audit')\n"
                "    screening.add_argument('--recovery-config', required=True)\n"
                "    screening.add_argument('--output', required=True)\n"
                "    boundary = commands.add_parser('boundary')\n"
                "    boundary.add_argument('--manifest', required=True)\n"
                "    boundary.add_argument('--screening-results', required=True)\n"
                "    boundary.add_argument('--responses', required=True)\n"
                "    boundary.add_argument('--output', required=True)\n"
                "    args = parser.parse_args()\n"
                "    manifest = json.loads(Path(args.manifest).read_text())\n"
                "    output = Path(args.output)\n"
                "    output.write_text(json.dumps(manifest['payload'], indent=2, "
                "sort_keys=True, allow_nan=False) + '\\n', encoding='utf-8')\n"
                "    if manifest.get('second_write'):\n"
                "        output.with_name('forbidden-second-write').write_text('x')\n"
                "    time.sleep(float(manifest.get('delay_seconds', 0.0)))\n"
                "    return 0\n"
            ).encode("utf-8"),
        )

    def open(self):
        return open_evidence_snapshot(
            surface=self.surface,
            run_id=self.run_id,
            result_sha256=str(self.result_binding["canonical_document_sha256"]),
            snapshot_root=self.snapshots,
        )


class RealUnchangedProducerFixture:
    """One actual complete-exposure result produced before snapshot freeze."""

    def __init__(self, case: unittest.TestCase):
        from conformance.test_task9_integration import (
            complete_calibration_policy,
            complete_job,
            complete_manifest,
            complete_response,
        )

        self.surface = "complete_exposure_ordering"
        self.temporary = tempfile.TemporaryDirectory()
        case.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.inputs = self.base / "inputs"
        self.snapshots = self.base / "snapshots"
        self.inputs.mkdir()
        self.snapshots.mkdir()
        manifest = complete_manifest()
        self.run_id = str(manifest["study_id"])
        records = [
            complete_response(
                index,
                ["creative-a", "creative-b", "creative-c", "creative-d"],
            )
            for index in range(1, 10)
        ]
        jobs = {
            "study_id": self.run_id,
            "method": "complete_exposure",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": [
                complete_job(record) for record in records
            ],
        }
        recovery = complete_calibration_policy()
        self.paths = {
            "study_manifest": self.inputs / "study-manifest.json",
            "screening_jobs": self.inputs / "screening-jobs.json",
            "screening_response_projection": (
                self.inputs / "screening-response-projection.jsonl"
            ),
            "recovery_configuration": (
                self.inputs / "complete-recovery-configuration.json"
            ),
            "result": self.inputs / "screening-model-results.json",
        }
        self.paths["study_manifest"].write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.paths["screening_jobs"].write_text(
            json.dumps(jobs, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.paths["screening_response_projection"].write_text(
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        self.paths["recovery_configuration"].write_text(
            json.dumps(recovery, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "skills/audience-ad-testing-lab/scripts/"
                    "aggregate-screening.py"
                ),
                "screening",
                "--manifest",
                str(self.paths["study_manifest"]),
                "--jobs",
                str(self.paths["screening_jobs"]),
                "--responses",
                str(self.paths["screening_response_projection"]),
                "--recovery-config",
                str(self.paths["recovery_configuration"]),
                "--output",
                str(self.paths["result"]),
            ],
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONPATH": str(
                    ROOT / "skills/audience-ad-testing-lab/scripts"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"real fixture producer failed: {completed.stderr!r}"
            )
        self.closure = _discover_dependency_closure(ROOT)
        result_raw = self.paths["result"].read_bytes()
        result_document = json.loads(result_raw)
        self.result_binding = {
            "path": "screening-model-results.json",
            "raw_bytes_sha256": digest(result_raw),
            "canonical_document_sha256": digest(
                canonical_json_bytes(result_document)
            ),
            "record_count": None,
        }
        sources: dict[str, Path] = {
            "runtime/" + str(row["path"]): ROOT / str(row["path"])
            for row in self.closure
        }
        bindings: dict[str, dict[str, object]] = {}
        for role, path in self.paths.items():
            member = (
                "results/screening-model-results.json"
                if role == "result"
                else f"inputs/{path.name}"
            )
            sources[member] = path
            raw = path.read_bytes()
            if role == "screening_response_projection":
                canonical = b"".join(
                    canonical_json_bytes(json.loads(line))
                    for line in raw.decode("utf-8").splitlines()
                )
                count: int | None = len(raw.decode("utf-8").splitlines())
            else:
                canonical = canonical_json_bytes(json.loads(raw))
                count = None
            bindings[role] = {
                "member_path": member,
                "raw_bytes_sha256": digest(raw),
                "canonical_document_sha256": digest(canonical),
                "record_count": count,
            }
        create_evidence_snapshot(
            surface=self.surface,
            run_id=self.run_id,
            result_sha256=str(self.result_binding["canonical_document_sha256"]),
            sources=sources,
            bindings=bindings,
            allowed_roots=[ROOT, self.inputs],
            snapshot_root=self.snapshots,
        )
        self.binding_names = {
            "study_manifest": "study_manifest",
            "screening_jobs": "screening_jobs",
            "screening_response_projection": "screening_response_projection",
            "recovery_configuration": "recovery_configuration",
            "result": "result",
        }

    def open(self):
        return open_evidence_snapshot(
            surface=self.surface,
            run_id=self.run_id,
            result_sha256=str(self.result_binding["canonical_document_sha256"]),
            snapshot_root=self.snapshots,
        )


class Tier4ProducerReplayTests(unittest.TestCase):
    def test_bootstrap_digest_is_byte_exact_and_not_runtime_derived(self):
        self.assertEqual(
            "sha256:e567c1fe9d73377dd2829cf649f73510c2476be09024ae898fd4d98340b111be",
            BOOTSTRAP_SHA256,
        )
        self.assertEqual(digest(REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")), BOOTSTRAP_SHA256)

    def test_trusted_provider_is_exact_root_owned_protected_executable(self):
        selected = _trusted_provider(platform_system=platform.system())
        expected = (
            Path("/usr/bin/sandbox-exec")
            if platform.system() == "Darwin"
            else Path("/usr/bin/bwrap")
        )
        self.assertEqual(expected, selected.path)
        value = os.stat(selected.path, follow_symlinks=False)
        self.assertEqual(0, value.st_uid)
        self.assertTrue(stat.S_ISREG(value.st_mode))
        self.assertFalse(value.st_mode & 0o022)
        self.assertTrue(value.st_mode & stat.S_IXUSR)

    def test_trusted_provider_rejects_unknown_os_path_lookup_and_bad_metadata(self):
        with self.assertRaises(ProducerRuntimeUnavailable):
            _trusted_provider(platform_system="Plan9")
        good = os.stat("/usr/bin/sandbox-exec", follow_symlinks=False)
        for changed in (
            {"st_uid": 501},
            {"st_mode": good.st_mode | 0o022},
            {"st_mode": stat.S_IFDIR | 0o755},
            {"st_mode": stat.S_IFREG | 0o644},
        ):
            replacement = list(good)
            field_index = {
                "st_mode": 0,
                "st_uid": 4,
            }
            for name, value in changed.items():
                replacement[field_index[name]] = value
            with patch(
                "audience_panel_builder.population.validation.producer_replay."
                "platform.system",
                return_value="Darwin",
            ), patch(
                "audience_panel_builder.population.validation.producer_replay."
                "os.stat",
                return_value=os.stat_result(replacement),
            ), self.assertRaises(ProducerRuntimeUnavailable):
                _trusted_provider()

    def test_vectors_lock_provider_bootstrap_root_fd_separator_and_option_order(self):
        root = Path("/private/tmp/extract/runtime/skills/audience-ad-testing-lab/scripts")
        output = Path("/private/tmp/replay/result.json")
        interpreter = Path("/private/tmp/venv/bin/python")
        child = [
            str(interpreter), "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
            str(root), "aggregate-screening.py", "9", "--",
            "screening",
            "--manifest", "/x/study-manifest.json",
            "--jobs", "/x/screening-jobs.json",
            "--responses", "/x/responses.jsonl",
            "--dispatch-audit", "/x/audit.jsonl",
            "--recovery-config", "/x/recovery.json",
            "--output", str(output),
        ]
        mac = _build_sandbox_vector(
            _SandboxProvider("macos-sandbox-exec-v1", Path("/usr/bin/sandbox-exec")),
            child=child,
            output=output,
        )
        self.assertEqual(
            ["/usr/bin/sandbox-exec", "-p", _sandbox_profile(output), *child],
            mac,
        )
        linux = _build_sandbox_vector(
            _SandboxProvider("linux-bwrap-v1", Path("/usr/bin/bwrap")),
            child=child,
            output=output,
        )
        self.assertEqual(
            [
                "/usr/bin/bwrap",
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--ro-bind", "/", "/",
                "--bind", str(output), str(output),
                "--",
                *child,
            ],
            linux,
        )

    def test_complete_real_provider_reproduces_exact_raw_and_canonical_bytes(self):
        fixture = ReplayFixture(self, non_ascii=True)
        with fixture.open() as snapshot:
            record = replay_producer(
                surface=fixture.surface,
                snapshot=snapshot,
                staged_input_bindings=fixture.binding_names,
                expected_result_binding=fixture.result_binding,
                expected_import_trace=fixture.closure,
                timeout_seconds=30,
            )
        self.assertEqual(fixture.result_binding, record)

    def test_pairwise_vector_and_real_provider_round_trip(self):
        fixture = ReplayFixture(
            self, surface="pairwise_boundary_ordering"
        )
        with fixture.open() as snapshot:
            self.assertEqual(
                fixture.result_binding,
                replay_producer(
                    surface=fixture.surface,
                    snapshot=snapshot,
                    staged_input_bindings=fixture.binding_names,
                    expected_result_binding=fixture.result_binding,
                    expected_import_trace=fixture.closure,
                    timeout_seconds=30,
                ),
            )

    def test_sandbox_denies_second_write_and_discards_replay_output(self):
        fixture = ReplayFixture(self, second_write=True)
        with fixture.open() as snapshot:
            with self.assertRaises(ProducerAuthenticationError):
                replay_producer(
                    surface=fixture.surface,
                    snapshot=snapshot,
                    staged_input_bindings=fixture.binding_names,
                    expected_result_binding=fixture.result_binding,
                    expected_import_trace=fixture.closure,
                    timeout_seconds=30,
                )
        self.assertFalse(
            any(path.name == "forbidden-second-write" for path in fixture.base.rglob("*"))
        )

    def test_rejects_wrong_roles_unknown_surface_and_fabricated_capability(self):
        fixture = ReplayFixture(self)
        fabricated = object.__new__(ValidatedEvidenceSnapshot)
        for snapshot, surface, names in (
            (fabricated, fixture.surface, fixture.binding_names),
            (None, "unknown", fixture.binding_names),
        ):
            with self.subTest(surface=surface, snapshot=type(snapshot).__name__):
                with self.assertRaises(ProducerEvidenceError):
                    replay_producer(
                        surface=surface,
                        snapshot=snapshot,  # type: ignore[arg-type]
                        staged_input_bindings=names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=fixture.closure,
                    )
        with fixture.open() as snapshot:
            for names in (
                {**fixture.binding_names, "extra": "result"},
                {**fixture.binding_names, "study_manifest": "screening_jobs"},
                {key: value for key, value in fixture.binding_names.items() if key != "result"},
            ):
                with self.subTest(names=names):
                    with self.assertRaises(ProducerAuthenticationError):
                        replay_producer(
                            surface=fixture.surface,
                            snapshot=snapshot,
                            staged_input_bindings=names,
                            expected_result_binding=fixture.result_binding,
                            expected_import_trace=fixture.closure,
                        )

    def test_rejects_inactive_capability_cleanup_failure_and_pathname_reuse(self):
        fixture = ReplayFixture(self)
        context = fixture.open()
        snapshot = context.__enter__()
        extraction = snapshot.resolve_member("result").parents[1]
        context.__exit__(None, None, None)
        extraction.mkdir(mode=0o700)
        self.addCleanup(lambda: extraction.rmdir() if extraction.exists() else None)
        with self.assertRaises(ProducerEvidenceError):
            replay_producer(
                surface=fixture.surface,
                snapshot=snapshot,
                staged_input_bindings=fixture.binding_names,
                expected_result_binding=fixture.result_binding,
                expected_import_trace=fixture.closure,
            )

    def test_rejects_result_digest_raw_bytes_source_trace_and_bootstrap_mutations(self):
        fixture = ReplayFixture(self)
        with fixture.open() as snapshot:
            cases = []
            canonical = copy.deepcopy(fixture.result_binding)
            canonical["canonical_document_sha256"] = "sha256:" + ("ab" * 32)
            cases.append(("canonical", canonical, fixture.closure))
            raw = copy.deepcopy(fixture.result_binding)
            raw["raw_bytes_sha256"] = "sha256:" + ("cd" * 32)
            cases.append(("raw", raw, fixture.closure))
            closure = copy.deepcopy(fixture.closure)
            closure[0]["raw_bytes_sha256"] = "sha256:" + ("ef" * 32)
            cases.append(("source", fixture.result_binding, closure))
            for label, result_binding, expected_trace in cases:
                with self.subTest(label=label):
                    with self.assertRaises(ProducerAuthenticationError):
                        replay_producer(
                            surface=fixture.surface,
                            snapshot=snapshot,
                            staged_input_bindings=fixture.binding_names,
                            expected_result_binding=result_binding,
                            expected_import_trace=expected_trace,
                        )

    def test_rejects_nonzero_signal_timeout_missing_malformed_duplicate_and_outside_trace(self):
        fixture = ReplayFixture(self)
        valid_trace = canonical_json_bytes({
            "schema_version": "producer-import-trace-v1",
            "modules": [
                {"module": "__main__", "path": "aggregate-screening.py"},
                {"module": "audience_lab", "path": "audience_lab/__init__.py"},
                {
                    "module": "audience_lab.fixture_producer",
                    "path": "audience_lab/fixture_producer.py",
                },
            ],
        })
        with fixture.open() as snapshot:
            for label, returncode, trace in (
                ("nonzero", 7, valid_trace),
                ("signal", -9, valid_trace),
                ("missing", 0, b""),
                ("malformed", 0, b"{}\n"),
                ("duplicate", 0, valid_trace + valid_trace),
                (
                    "outside",
                    0,
                    canonical_json_bytes({
                        "schema_version": "producer-import-trace-v1",
                        "modules": [
                            {"module": "__main__", "path": "aggregate-screening.py"},
                            {"module": "audience_lab.outside", "path": "audience_lab/outside.py"},
                        ],
                    }),
                ),
            ):
                with self.subTest(label=label), patch(
                    "audience_panel_builder.population.validation.producer_replay."
                    "_execute_sandbox",
                    return_value=(returncode, trace),
                ), self.assertRaises(ProducerAuthenticationError):
                    replay_producer(
                        surface=fixture.surface,
                        snapshot=snapshot,
                        staged_input_bindings=fixture.binding_names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=fixture.closure,
                    )
            with patch(
                "audience_panel_builder.population.validation.producer_replay."
                "_execute_sandbox",
                side_effect=TimeoutError(),
            ), self.assertRaises(ProducerAuthenticationError):
                replay_producer(
                    surface=fixture.surface,
                    snapshot=snapshot,
                    staged_input_bindings=fixture.binding_names,
                    expected_result_binding=fixture.result_binding,
                    expected_import_trace=fixture.closure,
                    timeout_seconds=1,
                )

    def test_timeout_and_expected_objects_are_closed(self):
        fixture = ReplayFixture(self)
        with fixture.open() as snapshot:
            for timeout in (0, -1, True, 3601):
                with self.subTest(timeout=timeout), self.assertRaises(
                    ProducerAuthenticationError
                ):
                    replay_producer(
                        surface=fixture.surface,
                        snapshot=snapshot,
                        staged_input_bindings=fixture.binding_names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=fixture.closure,
                        timeout_seconds=timeout,
                    )
            extra = {**fixture.result_binding, "extra": True}
            with self.assertRaises(ProducerAuthenticationError):
                replay_producer(
                    surface=fixture.surface,
                    snapshot=snapshot,
                    staged_input_bindings=fixture.binding_names,
                    expected_result_binding=extra,
                    expected_import_trace=fixture.closure,
                )

    def test_subprocess_inherits_only_trace_fd_and_uses_fixed_read_only_temp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        read_only = Path(temporary.name)
        os.chmod(read_only, 0o500)
        environment = _fixed_environment(read_only)
        self.assertEqual(
            {
                "HOME",
                "LANG",
                "LC_ALL",
                "PYTHONDONTWRITEBYTECODE",
                "TEMP",
                "TMP",
                "TMPDIR",
            },
            set(environment),
        )
        self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
        read_fd, write_fd = os.pipe()
        calls: list[dict[str, object]] = []

        class FakePopen:
            pid = 99999999

            def __init__(self, _vector, **kwargs):
                calls.append(kwargs)
                os.write(write_fd, b"trace\n")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def fake_waitid(_kind, pid, _options):
            return SimpleNamespace(
                si_pid=pid,
                si_code=os.CLD_EXITED,
                si_status=0,
            )

        def fake_killpg(_pgid, value):
            if value == 0:
                raise PermissionError

        with patch(
            "audience_panel_builder.population.validation.producer_replay."
            "subprocess.Popen",
            FakePopen,
        ), patch(
            "audience_panel_builder.population.validation.producer_replay."
            "os.waitid",
            side_effect=fake_waitid,
        ), patch(
            "audience_panel_builder.population.validation.producer_replay."
            "os.killpg",
            side_effect=fake_killpg,
        ):
            returncode, trace = _execute_sandbox(
                ["/usr/bin/true"],
                environment=environment,
                read_fd=read_fd,
                write_fd=write_fd,
                timeout_seconds=7,
            )
        self.assertEqual((0, b"trace\n"), (returncode, trace))
        self.assertEqual((write_fd,), calls[0]["pass_fds"])
        self.assertIs(True, calls[0]["close_fds"])
        self.assertEqual(environment, calls[0]["env"])
        self.assertIs(True, calls[0]["start_new_session"])
        self.assertEqual(0o500, stat.S_IMODE(read_only.stat().st_mode))

    def test_rejects_canonical_equal_raw_different_output_and_inode_replacement(self):
        fixture = ReplayFixture(self, non_ascii=True)
        valid_trace = canonical_json_bytes({
            "schema_version": "producer-import-trace-v1",
            "modules": [
                {"module": "__main__", "path": "aggregate-screening.py"},
                {"module": "audience_lab", "path": "audience_lab/__init__.py"},
                {
                    "module": "audience_lab.fixture_producer",
                    "path": "audience_lab/fixture_producer.py",
                },
            ],
        })

        def output_from_vector(vector: list[str]) -> Path:
            return Path(vector[vector.index("--output") + 1])

        compact_output: list[Path] = []

        def write_compact(vector, **_kwargs):
            output = output_from_vector(vector)
            compact_output.append(output)
            output.write_bytes(canonical_json_bytes(fixture.result_document))
            return 0, valid_trace

        with fixture.open() as snapshot, patch(
            "audience_panel_builder.population.validation.producer_replay."
            "_execute_sandbox",
            side_effect=write_compact,
        ), self.assertRaisesRegex(
            ProducerAuthenticationError, "raw result bytes"
        ):
            replay_producer(
                surface=fixture.surface,
                snapshot=snapshot,
                staged_input_bindings=fixture.binding_names,
                expected_result_binding=fixture.result_binding,
                expected_import_trace=fixture.closure,
            )
        self.assertFalse(compact_output[0].exists())

        replaced_output: list[Path] = []

        def replace_inode(vector, **_kwargs):
            output = output_from_vector(vector)
            replaced_output.append(output)
            output.parent.chmod(0o700)
            output.unlink()
            output.write_bytes(fixture.result_raw)
            output.chmod(0o600)
            output.parent.chmod(0o500)
            return 0, valid_trace

        with fixture.open() as snapshot, patch(
            "audience_panel_builder.population.validation.producer_replay."
            "_execute_sandbox",
            side_effect=replace_inode,
        ), self.assertRaisesRegex(
            ProducerAuthenticationError, "output inode"
        ):
            replay_producer(
                surface=fixture.surface,
                snapshot=snapshot,
                staged_input_bindings=fixture.binding_names,
                expected_result_binding=fixture.result_binding,
                expected_import_trace=fixture.closure,
            )
        self.assertFalse(replaced_output[0].exists())

    def test_rejects_nonmapping_and_unsorted_source_envelopes(self):
        fixture = ReplayFixture(self)
        with fixture.open() as snapshot:
            for closure in (
                [object()],
                list(reversed(fixture.closure)),
                [],
            ):
                with self.subTest(closure=repr(closure)), self.assertRaises(
                    ProducerAuthenticationError
                ):
                    replay_producer(
                        surface=fixture.surface,
                        snapshot=snapshot,
                        staged_input_bindings=fixture.binding_names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=closure,  # type: ignore[arg-type]
                    )

    @unittest.skipUnless(
        platform.system() == "Darwin"
        and Path("/usr/bin/sandbox-exec").exists(),
        "real swap-and-restore authority probes require macOS sandbox-exec",
    )
    def test_real_provider_rejects_swap_and_restore_for_every_live_authority(self):
        cases = ("source", "input", "interpreter", "interpreter-hop", "output")
        for label in cases:
            with self.subTest(label=label):
                fixture = ReplayFixture(self, delay_seconds=0.75)
                original_execute = _execute_sandbox
                with fixture.open() as snapshot:
                    runtime = _resolve_runtime_for_replay(snapshot)
                    source = runtime / str(fixture.closure[-1]["path"])
                    input_path = snapshot.resolve_member("study_manifest")
                    temporary_interpreter = fixture.base / "interpreter" / "python"
                    temporary_interpreter.parent.mkdir()
                    interpreter_hop = temporary_interpreter.with_name("python-hop")
                    temporary_interpreter.symlink_to(interpreter_hop.name)
                    interpreter_hop.symlink_to(Path(sys.executable))
                    selected_path = {
                        "source": source,
                        "input": input_path,
                        "interpreter": temporary_interpreter,
                        "interpreter-hop": interpreter_hop,
                    }.get(label)
                    attack_errors: list[BaseException] = []

                    def swap_and_restore(path: Path, output: Path) -> None:
                        try:
                            deadline = time.monotonic() + 5
                            while (
                                (not output.exists() or output.stat().st_size == 0)
                                and time.monotonic() < deadline
                            ):
                                time.sleep(0.01)
                            if not output.exists() or output.stat().st_size == 0:
                                raise AssertionError("producer did not reach delayed output")
                            parent = path.parent
                            original_mode = stat.S_IMODE(parent.stat().st_mode)
                            parent.chmod(original_mode | 0o200)
                            held = parent / (path.name + ".held-authority")
                            path.rename(held)
                            try:
                                if held.is_symlink():
                                    path.symlink_to(os.readlink(held))
                                else:
                                    path.write_bytes(b"temporary attacker bytes")
                                    path.chmod(0o600)
                                path.unlink()
                                held.rename(path)
                            finally:
                                if held.exists() or held.is_symlink():
                                    if path.exists() or path.is_symlink():
                                        path.unlink()
                                    held.rename(path)
                                parent.chmod(original_mode)
                        except BaseException as exc:
                            attack_errors.append(exc)

                    def attacked_execute(vector, **kwargs):
                        output = Path(vector[vector.index("--output") + 1])
                        target = output if label == "output" else selected_path
                        assert target is not None
                        thread = threading.Thread(
                            target=swap_and_restore,
                            args=(target, output),
                        )
                        thread.start()
                        try:
                            result = original_execute(vector, **kwargs)
                        finally:
                            thread.join(timeout=5)
                        if thread.is_alive():
                            raise AssertionError("authority attack thread survived")
                        if attack_errors:
                            raise attack_errors[0]
                        return result

                    executable_patch = (
                        patch(
                            "audience_panel_builder.population.validation."
                            "producer_replay.sys.executable",
                            str(temporary_interpreter),
                        )
                        if label.startswith("interpreter")
                        else patch(
                            "audience_panel_builder.population.validation."
                            "producer_replay.sys.executable",
                            sys.executable,
                        )
                    )
                    with executable_patch, patch(
                        "audience_panel_builder.population.validation."
                        "producer_replay._execute_sandbox",
                        side_effect=attacked_execute,
                    ), self.assertRaises(ProducerAuthenticationError):
                        replay_producer(
                            surface=fixture.surface,
                            snapshot=snapshot,
                            staged_input_bindings=fixture.binding_names,
                            expected_result_binding=fixture.result_binding,
                            expected_import_trace=fixture.closure,
                            timeout_seconds=10,
                        )

    def test_process_group_timeout_kills_descendant_before_marker(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        marker = Path(temporary.name) / "descendant-marker"
        script = (
            "import subprocess,sys,time\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import time; time.sleep(2); open({str(marker)!r}, 'w').write('alive')\""
            "])\n"
            "time.sleep(30)\n"
        )
        read_fd, write_fd = os.pipe()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            _execute_sandbox(
                [sys.executable, "-I", "-B", "-c", script],
                environment={"LANG": "C", "LC_ALL": "C"},
                read_fd=read_fd,
                write_fd=write_fd,
                timeout_seconds=1,
            )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        time.sleep(1.5)
        self.assertFalse(marker.exists())

    def test_process_group_closes_inherited_trace_writer_without_waiting(self):
        script = (
            "import os,subprocess,sys\n"
            "fd=int(sys.argv[1])\n"
            "subprocess.Popen([sys.executable, '-c', "
            "\"import time; time.sleep(5)\"], pass_fds=(fd,))\n"
            "os.write(fd,b'trace\\n')\n"
        )
        read_fd, write_fd = os.pipe()
        started = time.monotonic()
        returncode, trace = _execute_sandbox(
            [sys.executable, "-I", "-B", "-c", script, str(write_fd)],
            environment={"LANG": "C", "LC_ALL": "C"},
            read_fd=read_fd,
            write_fd=write_fd,
            timeout_seconds=10,
        )
        elapsed = time.monotonic() - started
        self.assertEqual((0, b"trace\n"), (returncode, trace))
        self.assertLess(elapsed, 2.0)

    @unittest.skipUnless(
        platform.system() == "Darwin"
        and Path("/usr/bin/sandbox-exec").exists(),
        "real prelaunch interpreter probes require macOS sandbox-exec",
    )
    def test_interpreter_root_chain_rejects_every_prelaunch_swap_restore(self):
        def make_interpreter(fixture: ReplayFixture) -> tuple[Path, dict[str, Path]]:
            environment_root = fixture.base / "temporary-python"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(environment_root)
            executable = environment_root / "bin" / "python"
            final = executable.with_name("python-final")
            executable.rename(final)
            hop_two = executable.with_name("python-hop-two")
            hop_one = executable.with_name("python-hop-one")
            hop_two.symlink_to(final.name)
            hop_one.symlink_to(hop_two.name)
            executable.symlink_to(hop_one.name)
            self.assertTrue(final.is_file())
            self.assertFalse(final.is_symlink())
            return executable, {
                "anchor": environment_root,
                "hop-one": hop_one,
                "hop-two": hop_two,
                "final": final,
            }

        clean = ReplayFixture(self, temporary_root=Path("/private/tmp"))
        clean_interpreter, _clean_targets = make_interpreter(clean)
        with clean.open() as snapshot, patch(
            "audience_panel_builder.population.validation."
            "producer_replay.sys.executable",
            str(clean_interpreter),
        ):
            self.assertEqual(
                clean.result_binding,
                replay_producer(
                    surface=clean.surface,
                    snapshot=snapshot,
                    staged_input_bindings=clean.binding_names,
                    expected_result_binding=clean.result_binding,
                    expected_import_trace=clean.closure,
                    timeout_seconds=10,
                ),
            )

        for label in ("anchor", "hop-one", "hop-two", "final"):
            with self.subTest(label=label):
                fixture = ReplayFixture(
                    self, temporary_root=Path("/private/tmp")
                )
                interpreter, targets = make_interpreter(fixture)
                target = targets[label]
                original_execute = _execute_sandbox

                def swap_restore() -> None:
                    parent = target.parent
                    held = parent / (target.name + ".held-prelaunch")
                    target.rename(held)
                    try:
                        if held.is_dir():
                            target.mkdir()
                            target.rmdir()
                        elif held.is_symlink():
                            target.symlink_to(os.readlink(held))
                            target.unlink()
                        else:
                            target.write_bytes(held.read_bytes())
                            target.chmod(stat.S_IMODE(held.stat().st_mode))
                            target.unlink()
                        held.rename(target)
                    finally:
                        if held.exists() or held.is_symlink():
                            if target.is_dir():
                                target.rmdir()
                            elif target.exists() or target.is_symlink():
                                target.unlink()
                            held.rename(target)

                def attacked_execute(vector, **kwargs):
                    swap_restore()
                    return original_execute(vector, **kwargs)

                with fixture.open() as snapshot, patch(
                    "audience_panel_builder.population.validation."
                    "producer_replay.sys.executable",
                    str(interpreter),
                ), patch(
                    "audience_panel_builder.population.validation."
                    "producer_replay._execute_sandbox",
                    side_effect=attacked_execute,
                ), self.assertRaises(ProducerAuthenticationError):
                    replay_producer(
                        surface=fixture.surface,
                        snapshot=snapshot,
                        staged_input_bindings=fixture.binding_names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=fixture.closure,
                        timeout_seconds=10,
                    )

    def test_external_extraction_anchor_membership_is_not_live_authority(self):
        fixture = ReplayFixture(self)
        original_execute = _execute_sandbox
        with fixture.open() as snapshot:
            selected = snapshot.resolve_member("study_manifest")
            extraction_root = selected.parents[1]
            external_anchor = extraction_root.parent

            def unrelated_anchor_activity(vector, **kwargs):
                sibling = Path(
                    tempfile.mkdtemp(
                        prefix="unrelated-extraction-",
                        dir=external_anchor,
                    )
                )
                sibling.rmdir()
                return original_execute(vector, **kwargs)

            with patch(
                "audience_panel_builder.population.validation."
                "producer_replay._execute_sandbox",
                side_effect=unrelated_anchor_activity,
            ):
                self.assertEqual(
                    fixture.result_binding,
                    replay_producer(
                        surface=fixture.surface,
                        snapshot=snapshot,
                        staged_input_bindings=fixture.binding_names,
                        expected_result_binding=fixture.result_binding,
                        expected_import_trace=fixture.closure,
                        timeout_seconds=10,
                    ),
                )

    @unittest.skipUnless(
        platform.system() == "Darwin"
        and Path("/usr/bin/sandbox-exec").exists(),
        "system-owned interpreter ancestor probe requires macOS",
    )
    def test_system_owned_interpreter_ancestor_allows_unrelated_siblings(self):
        fixture = ReplayFixture(self)
        interpreter_temporary = tempfile.TemporaryDirectory(
            prefix="tier4-system-ancestor-",
            dir="/private/tmp",
        )
        self.addCleanup(interpreter_temporary.cleanup)
        environment_root = Path(interpreter_temporary.name) / "venv"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(environment_root)
        interpreter = environment_root / "bin" / "python"
        private = Path("/private")
        shared = private / "tmp"
        shared_value = shared.stat()
        self.assertEqual(0, shared_value.st_uid)
        self.assertTrue(shared_value.st_mode & stat.S_ISVTX)
        self.assertNotEqual(os.geteuid(), shared_value.st_uid)
        self.assertFalse(os.access(private, os.W_OK | os.X_OK))
        original_execute = _execute_sandbox

        def unrelated_shared_activity(vector, **kwargs):
            sibling = Path(
                tempfile.mkdtemp(
                    prefix="unrelated-interpreter-",
                    dir=shared,
                )
            )
            sibling.rmdir()
            return original_execute(vector, **kwargs)

        with fixture.open() as snapshot, patch(
            "audience_panel_builder.population.validation."
            "producer_replay.sys.executable",
            str(interpreter),
        ), patch(
            "audience_panel_builder.population.validation."
            "producer_replay._execute_sandbox",
            side_effect=unrelated_shared_activity,
        ):
            self.assertEqual(
                fixture.result_binding,
                replay_producer(
                    surface=fixture.surface,
                    snapshot=snapshot,
                    staged_input_bindings=fixture.binding_names,
                    expected_result_binding=fixture.result_binding,
                    expected_import_trace=fixture.closure,
                    timeout_seconds=10,
                ),
            )

    def test_successful_exit_never_signals_after_numeric_group_release(self):
        read_fd, write_fd = os.pipe()
        events: list[tuple[str, int]] = []
        released = False

        class FakePopen:
            pid = 98989898

            def __init__(self, _vector, **_kwargs):
                os.write(write_fd, b"trace\n")

            def poll(self):
                nonlocal released
                released = True
                events.append(("poll-released", self.pid))
                return 0

            def wait(self, timeout=None):
                nonlocal released
                released = True
                events.append(("wait-released", self.pid))
                return 0

        def fake_waitid(_kind, pid, options):
            nonlocal released
            result = SimpleNamespace(
                si_pid=pid,
                si_code=os.CLD_EXITED,
                si_status=0,
            )
            if not options & os.WNOWAIT:
                released = True
                events.append(("waitid-released", pid))
            return result

        def guarded_killpg(pgid, value):
            if released:
                raise AssertionError(
                    "numeric process-group ID was signaled after leader release"
                )
            if value == 0:
                raise PermissionError
            events.append(("signal", value))

        with patch(
            "audience_panel_builder.population.validation.producer_replay."
            "subprocess.Popen",
            FakePopen,
        ), patch(
            "audience_panel_builder.population.validation.producer_replay."
            "os.waitid",
            side_effect=fake_waitid,
        ), patch(
            "audience_panel_builder.population.validation.producer_replay."
            "os.killpg",
            side_effect=guarded_killpg,
        ):
            self.assertEqual(
                (0, b"trace\n"),
                _execute_sandbox(
                    ["/usr/bin/true"],
                    environment={"LANG": "C", "LC_ALL": "C"},
                    read_fd=read_fd,
                    write_fd=write_fd,
                    timeout_seconds=2,
                ),
            )
        self.assertTrue(released)
        self.assertEqual(1, sum(name == "waitid-released" for name, _ in events))
        release_index = next(
            index
            for index, event in enumerate(events)
            if event[0] == "waitid-released"
        )
        self.assertFalse(
            any(name == "signal" for name, _ in events[release_index + 1:])
        )

    def test_controller_error_kills_group_and_reaps_before_marker_or_writer(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        marker = Path(temporary.name) / "controller-error-descendant"
        script = (
            "import os,subprocess,sys,time\n"
            "fd=int(sys.argv[1])\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import time; time.sleep(2); open({str(marker)!r}, 'w').write('alive')\""
            "], pass_fds=(fd,))\n"
            f"os.write(fd,b'x'*{_MAX_TRACE_BYTES + 1})\n"
            "time.sleep(30)\n"
        )
        read_fd, write_fd = os.pipe()
        started = time.monotonic()
        with self.assertRaisesRegex(
            ProducerAuthenticationError, "trace exceeds"
        ):
            _execute_sandbox(
                [sys.executable, "-I", "-B", "-c", script, str(write_fd)],
                environment={"LANG": "C", "LC_ALL": "C"},
                read_fd=read_fd,
                write_fd=write_fd,
                timeout_seconds=10,
            )
        self.assertLess(time.monotonic() - started, 2.0)
        time.sleep(1.5)
        self.assertFalse(marker.exists())

    def test_nonreaping_observation_preserves_exact_signal_status(self):
        read_fd, write_fd = os.pipe()
        returncode, trace = _execute_sandbox(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import os,signal; os.kill(os.getpid(),signal.SIGTERM)",
            ],
            environment={"LANG": "C", "LC_ALL": "C"},
            read_fd=read_fd,
            write_fd=write_fd,
            timeout_seconds=5,
        )
        self.assertEqual((-signal.SIGTERM, b""), (returncode, trace))

    def test_result_path_is_exact_surface_filename_without_normalization(self):
        for surface, expected in (
            ("complete_exposure_ordering", "screening-model-results.json"),
            ("maxdiff_screening_ordering", "screening-model-results.json"),
            ("pairwise_boundary_ordering", "boundary-results.json"),
        ):
            fixture = ReplayFixture(self, surface=surface)
            with fixture.open() as snapshot:
                self.assertEqual(expected, fixture.result_binding["path"])
                for hostile in (
                    f"nested/{expected}",
                    f"./{expected}",
                    f"{expected}/.",
                    expected.upper(),
                    (
                        "boundary-results.json"
                        if expected == "screening-model-results.json"
                        else "screening-model-results.json"
                    ),
                ):
                    binding = copy.deepcopy(fixture.result_binding)
                    binding["path"] = hostile
                    with self.subTest(surface=surface, hostile=hostile), (
                        self.assertRaises(ProducerAuthenticationError)
                    ):
                        replay_producer(
                            surface=surface,
                            snapshot=snapshot,
                            staged_input_bindings=fixture.binding_names,
                            expected_result_binding=binding,
                            expected_import_trace=fixture.closure,
                        )


@unittest.skipUnless(
    os.environ.get("AUDIENCE_TIER4_REAL_PROVIDER") == "1",
    "real provider-backed NumPy/SciPy replay is CI opt-in",
)
class Tier4RealProviderTests(unittest.TestCase):
    def test_provider_replays_unchanged_numpy_scipy_producer(self):
        fixture = RealUnchangedProducerFixture(self)
        with fixture.open() as snapshot:
            replay_producer(
                surface=fixture.surface,
                snapshot=snapshot,
                staged_input_bindings=fixture.binding_names,
                expected_result_binding=fixture.result_binding,
                expected_import_trace=fixture.closure,
                timeout_seconds=60,
            )

    def test_provider_denies_second_write(self):
        denied = ReplayFixture(self, second_write=True)
        with denied.open() as snapshot, self.assertRaises(
            ProducerAuthenticationError
        ):
            replay_producer(
                surface=denied.surface,
                snapshot=snapshot,
                staged_input_bindings=denied.binding_names,
                expected_result_binding=denied.result_binding,
                expected_import_trace=denied.closure,
                timeout_seconds=60,
            )


if __name__ == "__main__":
    unittest.main()
