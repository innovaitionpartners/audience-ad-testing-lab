from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
    ProducerRuntimeUnavailable,
)
from audience_panel_builder.population.validation.producer_semantics import (  # noqa: E402
    CANONICAL_DOCUMENT_SERIALIZATION,
    PRODUCER_RAW_SERIALIZATION,
    REPLAY_BOOTSTRAP_SOURCE,
    ProducerSemanticsBundle,
    _build_link_fingerprint,
    _build_numpy_fingerprint,
    _build_policy_bindings,
    _discover_dependency_closure,
    _normalize_finite_json,
    _parse_ldd_output,
    _parse_otool_output,
    _run_root_owned_tool,
    _validate_import_trace,
    _validate_staged_closure,
    build_producer_semantics,
)


ENTRY = "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
SHA = "sha256:" + ("ab" * 32)
MACOS_EXTENSION = "/private/tmp/runtime/numpy/core/_multiarray_umath.cpython-311-darwin.so"
LINUX_EXTENSION = "/tmp/runtime/scipy/special/_ufuncs.cpython-311-x86_64-linux-gnu.so"

MACOS_OTOOL_GOLDEN = (
    f"{MACOS_EXTENSION}:\n"
    "\t/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate "
    "(compatibility version 1.0.0, current version 4.0.0)\n"
    "\t/usr/lib/libSystem.B.dylib "
    "(compatibility version 1.0.0, current version 1345.100.2)\n"
)

LINUX_LDD_GOLDEN = (
    "\tlinux-vdso.so.1 (0x00007ffd4d5f9000)\n"
    "\tlibopenblas.so.0 => /usr/lib/x86_64-linux-gnu/libopenblas.so.0 "
    "(0x00007f9eb5200000)\n"
    "\t/usr/lib/x86_64-linux-gnu/libm.so.6 (0x00007f9eb5110000)\n"
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def fake_runtime_fingerprint() -> dict[str, str]:
    return {
        "python_implementation": "CPython",
        "python_version": "3.11.9",
        "numpy_version": "2.1.0",
        "scipy_version": "1.14.0",
        "platform_system": "Darwin",
        "platform_release": "23.6.0",
        "machine": "arm64",
        "numpy_build_sha256": SHA,
        "blas_lapack_sha256": "sha256:" + ("cd" * 32),
    }


class Tier4ProducerSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "runtime"
        self.stage = self.base / "stage"
        self._write_runtime()

    def _write(self, relative: str, value: str | bytes) -> Path:
        path = self.runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_bytes(value)
        return path

    def _write_runtime(self) -> None:
        self._write(
            ENTRY,
            "from audience_lab.foo import VALUE\n"
            "from audience_lab import second\n"
            "from audience_lab.complete_exposure import CALIBRATION_POLICY_VERSION\n"
            "from audience_lab.maxdiff import MaxDiffConfig\n"
            "from audience_lab.pairwise import PairwiseConfig\n"
            "from audience_lab import transient\n"
            "import sys\n"
            "del sys.modules['audience_lab.transient']\n"
            "_COMPLETE_POLICY = {\n"
            "  'version': 'complete-exposure-calibration-v2',\n"
            "  'scope': 'conditional_synthetic_run_only',\n"
            "  'planned_jobs_per_segment': 9,\n"
            "  'minimum_usable_records_per_segment': 8,\n"
            "  'bootstrap_resamples': 2000,\n"
            "  'finalist_inclusion_threshold': 0.90,\n"
            "  'nonfinalist_inclusion_threshold': 0.10,\n"
            "  'cutoff_tie_policy': 'no_point_estimate_only_decision',\n"
            "  'archetype_sensitivity': "
            "'leave_one_persona_archetype_out_top_k_consistent',\n"
            "  'minimum_archetype_diversity': 2,\n"
            "  'minimum_evaluable_archetype_exclusions': 2,\n"
            "  'calibration_basis': "
            "'deterministic_task9_adversarial_recovery_fixtures',\n"
            "  'human_market_calibration': False,\n"
            "}\n"
            "if __name__ == '__main__':\n"
            "    if sys.argv[1:] == ['--exit-seven']:\n"
            "        raise SystemExit(7)\n"
            "    print(VALUE + second.VALUE)\n",
        )
        self._write("skills/audience-ad-testing-lab/scripts/audience_lab/__init__.py", "")
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/foo.py",
            "from .nested.bar import VALUE\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/second.py",
            "VALUE = 1\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/nested/__init__.py",
            "",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/nested/bar.py",
            "VALUE = 2\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/complete_exposure.py",
            "CALIBRATION_POLICY_VERSION = 'complete-exposure-calibration-v2'\n"
            "PRODUCTION_RESAMPLES = 2000\n"
            "_TIE_TOLERANCE = 1e-12\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/maxdiff.py",
            "_REQUIRED_BOOTSTRAP_COUNT = 2000\n"
            "_MINIMUM_SUCCESSFUL_FIT_FLOOR = 0.95\n"
            "_CLEAR_FINALIST_THRESHOLD = 0.90\n"
            "_CLEAR_NON_FINALIST_THRESHOLD = 0.10\n"
            "_MINIMUM_UTILITY_TIE_TOLERANCE = 1e-12\n"
            "class MaxDiffConfig: pass\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/pairwise.py",
            "_CLEAR_FINALIST_THRESHOLD = 0.90\n"
            "_CLEAR_NON_FINALIST_THRESHOLD = 0.10\n"
            "_MINIMUM_UTILITY_TIE_TOLERANCE = 1e-12\n"
            "class PairwiseConfig: pass\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/transient.py",
            "VALUE = 'captured-before-removal'\n",
        )

    def _complete_configuration(self) -> dict[str, object]:
        return {
            "recovery_configuration": {
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
        }

    def _maxdiff_configuration(self, tolerance: float = 1e-8) -> dict[str, object]:
        return {
            "maxdiff_configuration": {
                "penalty_lambda": 0.1,
                "optimizer_tolerance": tolerance,
                "bootstrap_count": 2000,
                "successful_fit_floor": 0.95,
                "clear_finalist_threshold": 0.90,
                "clear_non_finalist_threshold": 0.10,
                "seed": 123,
            },
            "recovery_configuration": {
                "version": "maxdiff-recovery-v1",
                "calibration_status": "calibrated",
                "library_size_bands": [
                    {"name": "small", "minimum": 4, "maximum": 20}
                ],
                "shortlist_size_bands": [
                    {"name": "short", "minimum": 2, "maximum": 10}
                ],
                "segment_count": {"minimum": 1, "maximum": 10},
                "tie_inability_band": {
                    "minimum_rate": 0.0,
                    "maximum_rate": 1.0,
                },
                "utility_separation_band": {
                    "minimum_log_utility_gap": 0.0,
                    "maximum_log_utility_gap": 100.0,
                },
                "planned_participation_floor": 9,
                "usable_participation_floor": 8,
                "bootstrap_count": 2000,
                "successful_fit_floor": 0.95,
                "shortlist_thresholds": {
                    "clear_finalist": 0.90,
                    "clear_non_finalist": 0.10,
                },
            },
        }

    def _pairwise_configuration(self, tolerance: float = 1e-8) -> dict[str, object]:
        return {
            "pairwise_configuration": {
                "tie_parameter": 0.2,
                "penalty_lambda": 0.1,
                "optimizer_tolerance": tolerance,
                "bootstrap_count": 2000,
                "successful_fit_floor": 0.95,
                "seed": 456,
            }
        }

    def _run_bootstrap(
        self,
        staged_runtime_root: Path,
        producer_args: list[str],
    ) -> tuple[subprocess.CompletedProcess[bytes], bytes]:
        scripts_root = (
            staged_runtime_root / "skills/audience-ad-testing-lab/scripts"
        )
        read_fd, write_fd = os.pipe()
        try:
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
                    str(scripts_root), "aggregate-screening.py", str(write_fd),
                    "--", *producer_args,
                ],
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(write_fd,),
                check=False,
                timeout=240,
            )
        finally:
            os.close(write_fd)
        trace = os.read(read_fd, 1024 * 1024)
        os.close(read_fd)
        return completed, trace

    def test_dependency_closure_is_transitive_complete_and_path_sorted(self):
        rows = _discover_dependency_closure(self.runtime, ENTRY)
        expected_paths = [
            "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/__init__.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/complete_exposure.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/foo.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/maxdiff.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/nested/__init__.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/nested/bar.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/pairwise.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/second.py",
            "skills/audience-ad-testing-lab/scripts/audience_lab/transient.py",
        ]
        self.assertEqual(expected_paths, [row["path"] for row in rows])
        self.assertEqual(
            [
                {"path", "byte_count", "raw_bytes_sha256"}
                for _ in rows
            ],
            [set(row) for row in rows],
        )
        for row in rows:
            raw = (self.runtime / str(row["path"])).read_bytes()
            self.assertEqual(len(raw), row["byte_count"])
            self.assertEqual(digest(raw), row["raw_bytes_sha256"])

    def test_dependency_closure_rejects_unresolved_dynamic_and_symlink_imports(self):
        foo = self.runtime / (
            "skills/audience-ad-testing-lab/scripts/audience_lab/foo.py"
        )
        original = foo.read_text()
        cases = (
            "from .missing import VALUE\n",
            "import importlib\nVALUE = importlib.import_module('audience_lab.second')\n",
            "VALUE = __import__('audience_lab.second')\n",
            "loader = __import__\nVALUE = loader('audience_lab.second')\n",
            "from importlib import import_module as loader\n"
            "VALUE = loader('audience_lab.second')\n",
            "from builtins import __import__ as loader\n"
            "VALUE = loader('audience_lab.second')\n",
            "import importlib as machinery\n"
            "loader = getattr(machinery, 'import_module')\n"
            "VALUE = loader('audience_lab.second')\n",
            "exec(\"from audience_lab.second import VALUE\")\n",
            "code = compile(\"from audience_lab.second import VALUE\", '<x>', 'exec')\n"
            "eval(code)\n",
        )
        for source in cases:
            with self.subTest(source=source):
                foo.write_text(source)
                with self.assertRaises(ProducerAuthenticationError):
                    _discover_dependency_closure(self.runtime, ENTRY)
        foo.unlink()
        target = self.base / "outside.py"
        target.write_text(original)
        foo.symlink_to(target)
        with self.assertRaises(ProducerAuthenticationError):
            _discover_dependency_closure(self.runtime, ENTRY)

    def test_dependency_closure_rejects_intermediate_directory_symlinks(self):
        scripts = self.runtime / "skills/audience-ad-testing-lab/scripts"
        nested = scripts / "audience_lab/nested"
        external = self.base / "external-nested"
        shutil.copytree(nested, external)
        shutil.rmtree(nested)
        nested.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ProducerAuthenticationError):
            _discover_dependency_closure(self.runtime, ENTRY)

    def test_dependency_closure_rejects_path_escape_and_non_utf8_source(self):
        foo = self.runtime / (
            "skills/audience-ad-testing-lab/scripts/audience_lab/foo.py"
        )
        foo.write_text("from ...outside import VALUE\n")
        with self.assertRaises(ProducerAuthenticationError):
            _discover_dependency_closure(self.runtime, ENTRY)
        foo.write_bytes(b"\xff\xfe")
        with self.assertRaises(ProducerAuthenticationError):
            _discover_dependency_closure(self.runtime, ENTRY)

    def test_staged_closure_rejects_missing_extra_symlink_bytecode_and_path_mutation(self):
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        _validate_staged_closure(self.stage, bundle.semantics["dependency_closure"])
        for directory, directories, files in os.walk(self.stage):
            os.chmod(directory, 0o700)
            for name in files:
                os.chmod(Path(directory) / name, 0o600)
        staged_file = self.stage / ENTRY
        raw = staged_file.read_bytes()
        staged_file.unlink()
        with self.assertRaises(ProducerAuthenticationError):
            _validate_staged_closure(self.stage, bundle.semantics["dependency_closure"])
        staged_file.write_bytes(raw)
        extra = self.stage / "skills/audience-ad-testing-lab/scripts/extra.py"
        extra.write_text("EXTRA = True\n")
        with self.assertRaises(ProducerAuthenticationError):
            _validate_staged_closure(self.stage, bundle.semantics["dependency_closure"])
        extra.unlink()
        cache = self.stage / "skills/audience-ad-testing-lab/scripts/__pycache__"
        cache.mkdir()
        (cache / "extra.pyc").write_bytes(b"bytecode")
        with self.assertRaises(ProducerAuthenticationError):
            _validate_staged_closure(self.stage, bundle.semantics["dependency_closure"])
        (cache / "extra.pyc").unlink()
        cache.rmdir()
        staged_file.unlink()
        staged_file.symlink_to(self.runtime / ENTRY)
        with self.assertRaises(ProducerAuthenticationError):
            _validate_staged_closure(self.stage, bundle.semantics["dependency_closure"])

    def test_build_rejects_nonempty_stage_and_detects_source_mutation(self):
        self.stage.mkdir()
        (self.stage / "untrusted.py").write_text("X = 1\n")
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            with self.assertRaises(ProducerAuthenticationError):
                build_producer_semantics(
                    surface="complete_exposure_ordering",
                    runtime_root=self.runtime,
                    staged_runtime_root=self.stage,
                    configuration=self._complete_configuration(),
                    upstream_semantics_sha256=None,
                )
        self.assertEqual(["untrusted.py"], [path.name for path in self.stage.iterdir()])

        self.stage = self.base / "clean-stage"
        real_read = Path.read_bytes
        mutated = False

        def mutate_once(path: Path) -> bytes:
            nonlocal mutated
            value = real_read(path)
            if path.name == "bar.py" and not mutated:
                mutated = True
                path.write_bytes(value + b"# mutation\n")
            return value

        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_read_stable_source",
            side_effect=lambda path, root: mutate_once(path),
        ), patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            with self.assertRaises(ProducerAuthenticationError):
                build_producer_semantics(
                    surface="complete_exposure_ordering",
                    runtime_root=self.runtime,
                    staged_runtime_root=self.stage,
                    configuration=self._complete_configuration(),
                    upstream_semantics_sha256=None,
                )

    def test_bootstrap_is_sealed_and_no_staged_path_is_serialized(self):
        self.assertIn("runpy.run_path", REPLAY_BOOTSTRAP_SOURCE)
        self.assertIn("sys.path.insert", REPLAY_BOOTSTRAP_SOURCE)
        self.assertIn("producer-import-trace-v1", REPLAY_BOOTSTRAP_SOURCE)
        self.assertIn("sys.argv[1:]", REPLAY_BOOTSTRAP_SOURCE)
        self.assertNotIn("AUDIENCE_C1_", REPLAY_BOOTSTRAP_SOURCE)
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        encoded = REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")
        self.assertEqual(digest(encoded), bundle.semantics["bootstrap_sha256"])
        self.assertNotIn(str(self.stage), json.dumps(bundle.semantics))
        self.assertFalse(any(self.stage.rglob("__pycache__")))
        self.assertFalse(any(self.stage.rglob("*.pyc")))

    def test_bootstrap_trace_equals_closure_and_mismatch_fails(self):
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        read_fd, write_fd = os.pipe()
        scripts_root = self.stage / "skills/audience-ad-testing-lab/scripts"
        try:
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
                    str(scripts_root), "aggregate-screening.py", str(write_fd), "--",
                ],
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(write_fd,),
                check=False,
                timeout=10,
            )
        finally:
            os.close(write_fd)
        trace = os.read(read_fd, 1024 * 1024)
        os.close(read_fd)
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        expected = tuple(row["path"] for row in bundle.semantics["dependency_closure"])
        self.assertEqual(
            expected,
            _validate_import_trace(
                trace,
                bundle.semantics["dependency_closure"],
                staged_runtime_root=self.stage,
            ),
        )
        document = json.loads(trace)
        lazy_subset = copy.deepcopy(document)
        lazy_subset["modules"] = [
            row for row in lazy_subset["modules"]
            if row["module"] != "audience_lab.transient"
        ]
        subset_paths = _validate_import_trace(
            _canonical(lazy_subset),
            bundle.semantics["dependency_closure"],
            staged_runtime_root=self.stage,
        )
        self.assertNotIn(
            "skills/audience-ad-testing-lab/scripts/audience_lab/transient.py",
            subset_paths,
        )

        entry_omission = copy.deepcopy(document)
        entry_omission["modules"] = [
            row for row in entry_omission["modules"]
            if row["module"] != "__main__"
        ]
        duplicate = copy.deepcopy(document)
        duplicate["modules"].append(copy.deepcopy(duplicate["modules"][0]))
        duplicate["modules"].sort(key=lambda row: (row["module"], row["path"]))
        module_alias = copy.deepcopy(document)
        module_alias["modules"].append({
            "module": module_alias["modules"][0]["module"],
            "path": "audience_lab/foo.py",
        })
        module_alias["modules"].sort(key=lambda row: (row["module"], row["path"]))
        path_alias = copy.deepcopy(document)
        path_alias["modules"].append({
            "module": "audience_lab.alias",
            "path": path_alias["modules"][0]["path"],
        })
        path_alias["modules"].sort(key=lambda row: (row["module"], row["path"]))
        disagreement = copy.deepcopy(document)
        disagreement["modules"][0] = {
            "module": disagreement["modules"][0]["module"],
            "path": "audience_lab/second.py",
        }
        disagreement["modules"].sort(key=lambda row: (row["module"], row["path"]))
        for altered in (
            {**document, "extra": True},
            {**document, "modules": []},
            entry_omission,
            duplicate,
            module_alias,
            path_alias,
            disagreement,
            {**document, "modules": [
                *document["modules"],
                {"module": "audience_lab.extra", "path": "audience_lab/extra.py"},
            ]},
            {**document, "modules": list(reversed(document["modules"]))},
        ):
            with self.assertRaises(ProducerAuthenticationError):
                _validate_import_trace(
                    _canonical(altered),
                    bundle.semantics["dependency_closure"],
                    staged_runtime_root=self.stage,
                )
        altered_closure = copy.deepcopy(bundle.semantics["dependency_closure"])
        next(
            row for row in altered_closure
            if row["path"].endswith("audience_lab/foo.py")
        )["raw_bytes_sha256"] = SHA
        with self.assertRaises(ProducerAuthenticationError):
            _validate_import_trace(
                trace,
                altered_closure,
                staged_runtime_root=self.stage,
            )

    def test_bootstrap_trace_captures_equal_cardinality_relative_replacements(self):
        entry = self.runtime / ENTRY
        entry.write_text(
            "from audience_lab import replacement\n"
            + entry.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/replacement.py",
            "import sys\n"
            "from . import replacement_a\n"
            "del sys.modules['audience_lab.replacement_a']\n"
            "from . import replacement_b\n"
            "del sys.modules['audience_lab.replacement_b']\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/replacement_a.py",
            "VALUE = 'a'\n",
        )
        self._write(
            "skills/audience-ad-testing-lab/scripts/audience_lab/replacement_b.py",
            "VALUE = 'b'\n",
        )
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        completed, trace = self._run_bootstrap(self.stage, [])
        self.assertEqual(0, completed.returncode, completed.stderr)
        _validate_import_trace(
            trace,
            bundle.semantics["dependency_closure"],
            staged_runtime_root=self.stage,
        )
        identities = {
            (row["module"], row["path"])
            for row in json.loads(trace)["modules"]
        }
        expected_identities = {
            (
                "audience_lab.replacement",
                "audience_lab/replacement.py",
            ),
            (
                "audience_lab.replacement_a",
                "audience_lab/replacement_a.py",
            ),
            (
                "audience_lab.replacement_b",
                "audience_lab/replacement_b.py",
            ),
        }
        self.assertEqual(set(), expected_identities - identities)

    def test_real_bootstrap_help_smoke_uses_only_staged_audience_lab(self):
        stage = self.base / "real-stage"
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=ROOT,
                staged_runtime_root=stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        scripts_root = stage / "skills/audience-ad-testing-lab/scripts"
        read_fd, write_fd = os.pipe()
        try:
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
                    str(scripts_root), "aggregate-screening.py", str(write_fd),
                    "--", "--help",
                ],
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(write_fd,),
                check=False,
                timeout=30,
            )
        finally:
            os.close(write_fd)
        trace = os.read(read_fd, 1024 * 1024)
        os.close(read_fd)
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertIn(b"screening", completed.stdout)
        trace_document = json.loads(trace)
        self.assertEqual("producer-import-trace-v1", trace_document["schema_version"])
        paths = {
            row["path"] for row in trace_document["modules"]
            if row["module"].startswith("audience_lab")
        }
        sealed_relative = {
            str(row["path"]).removeprefix(
                "skills/audience-ad-testing-lab/scripts/"
            )
            for row in bundle.semantics["dependency_closure"]
        }
        self.assertTrue(paths <= sealed_relative)
        self.assertIn(
            "audience_lab/__init__.py",
            paths,
        )

    def test_bootstrap_preserves_exit_and_rejects_non_direct_entry_vector(self):
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        scripts_root = self.stage / "skills/audience-ad-testing-lab/scripts"
        read_fd, write_fd = os.pipe()
        try:
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
                    str(scripts_root), "aggregate-screening.py", str(write_fd),
                    "--", "--exit-seven",
                ],
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(write_fd,),
                check=False,
                timeout=10,
            )
        finally:
            os.close(write_fd)
        trace = os.read(read_fd, 1024 * 1024)
        os.close(read_fd)
        self.assertEqual(7, completed.returncode)
        self.assertEqual(
            "producer-import-trace-v1", json.loads(trace)["schema_version"]
        )
        invalid = subprocess.run(
            [
                sys.executable, "-I", "-B", "-c", REPLAY_BOOTSTRAP_SOURCE,
                str(scripts_root), "audience_lab/foo.py", "1", "--",
            ],
            env={},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(2, invalid.returncode)

    def test_real_valid_surface_bootstrap_traces_are_sealed_subsets(self):
        from conformance.test_maxdiff import (
            full_job_for_response,
            full_response_for_block,
            matching_manifest as maxdiff_manifest,
        )
        from conformance.test_pairwise import (
            boundary_fixture,
            matching_manifest as pairwise_manifest,
            matching_screening_result,
        )
        from conformance.test_task9_integration import (
            complete_calibration_policy,
            complete_job,
            complete_manifest,
            complete_response,
        )

        stage = self.base / "surface-stage"
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=ROOT,
                staged_runtime_root=stage,
                configuration={
                    "recovery_configuration": complete_calibration_policy()
                },
                upstream_semantics_sha256=None,
            )
        closure = bundle.semantics["dependency_closure"]
        inputs = self.base / "surface-inputs"
        inputs.mkdir()

        complete_records = [
            complete_response(
                index,
                ["creative-a", "creative-b", "creative-c", "creative-d"],
            )
            for index in range(1, 10)
        ]
        complete_paths = {
            "manifest": inputs / "complete-manifest.json",
            "jobs": inputs / "complete-jobs.json",
            "responses": inputs / "complete-responses.jsonl",
            "recovery": inputs / "complete-recovery.json",
            "output": inputs / "complete-output.json",
        }
        complete_paths["manifest"].write_text(
            json.dumps(complete_manifest()), encoding="utf-8"
        )
        complete_paths["jobs"].write_text(
            json.dumps({
                "study_id": "complete-acme-001",
                "method": "complete_exposure",
                "record_type": "screening_response",
                "synthetic_replicate_jobs": [
                    complete_job(record) for record in complete_records
                ],
            }),
            encoding="utf-8",
        )
        complete_paths["responses"].write_text(
            "".join(json.dumps(record) + "\n" for record in complete_records),
            encoding="utf-8",
        )
        complete_paths["recovery"].write_text(
            json.dumps(complete_calibration_policy()), encoding="utf-8"
        )
        complete_completed, complete_trace = self._run_bootstrap(
            stage,
            [
                "screening",
                "--manifest", str(complete_paths["manifest"]),
                "--jobs", str(complete_paths["jobs"]),
                "--responses", str(complete_paths["responses"]),
                "--recovery-config", str(complete_paths["recovery"]),
                "--output", str(complete_paths["output"]),
            ],
        )
        self.assertEqual(0, complete_completed.returncode, complete_completed.stderr)
        complete_observed = _validate_import_trace(
            complete_trace, closure, staged_runtime_root=stage
        )
        self.assertLess(len(complete_observed), len(closure))

        maxdiff_response = full_response_for_block(["V1", "V2", "V3", "V4"], 1)
        maxdiff_manifest_payload = maxdiff_manifest()
        maxdiff_manifest_payload["synthetic_replicate_capacity"][
            "screening_planned"
        ] = 1
        capacity = maxdiff_manifest_payload["synthetic_replicate_capacity"]
        required = (
            capacity["screening_planned"]
            + capacity["boundary_reserved"]
            + capacity["finalist_reserved"]
        )
        maxdiff_manifest_payload["maximum_synthetic_panelists"] = max(
            maxdiff_manifest_payload["maximum_synthetic_panelists"], required
        )
        capacity["ceiling_satisfied"] = True
        maxdiff_paths = {
            "manifest": inputs / "maxdiff-manifest.json",
            "jobs": inputs / "maxdiff-jobs.json",
            "responses": inputs / "maxdiff-responses.jsonl",
            "output": inputs / "maxdiff-output.json",
        }
        maxdiff_paths["manifest"].write_text(
            json.dumps(maxdiff_manifest_payload), encoding="utf-8"
        )
        maxdiff_paths["jobs"].write_text(
            json.dumps({
                "study_id": maxdiff_manifest_payload["study_id"],
                "method": "partial_exposure_maxdiff",
                "record_type": "screening_response",
                "synthetic_replicate_jobs": [
                    full_job_for_response(maxdiff_response)
                ],
            }),
            encoding="utf-8",
        )
        maxdiff_paths["responses"].write_text(
            json.dumps(maxdiff_response) + "\n", encoding="utf-8"
        )
        maxdiff_completed, maxdiff_trace = self._run_bootstrap(
            stage,
            [
                "screening",
                "--manifest", str(maxdiff_paths["manifest"]),
                "--jobs", str(maxdiff_paths["jobs"]),
                "--responses", str(maxdiff_paths["responses"]),
                "--recovery-config",
                str(
                    ROOT
                    / "skills/audience-ad-testing-lab/references/"
                    "screening-recovery-config.json"
                ),
                "--output", str(maxdiff_paths["output"]),
            ],
        )
        self.assertEqual(0, maxdiff_completed.returncode, maxdiff_completed.stderr)
        _validate_import_trace(
            maxdiff_trace, closure, staged_runtime_root=stage
        )

        pairwise_records = boundary_fixture()
        pairwise_paths = {
            "manifest": inputs / "pairwise-manifest.json",
            "screening": inputs / "pairwise-screening.json",
            "responses": inputs / "pairwise-responses.jsonl",
            "output": inputs / "pairwise-output.json",
        }
        pairwise_paths["manifest"].write_text(
            json.dumps(pairwise_manifest(records=pairwise_records)),
            encoding="utf-8",
        )
        pairwise_paths["screening"].write_text(
            json.dumps(matching_screening_result(pairwise_records)),
            encoding="utf-8",
        )
        pairwise_paths["responses"].write_text(
            "".join(json.dumps(record) + "\n" for record in pairwise_records),
            encoding="utf-8",
        )
        pairwise_completed, pairwise_trace = self._run_bootstrap(
            stage,
            [
                "boundary",
                "--manifest", str(pairwise_paths["manifest"]),
                "--screening-results", str(pairwise_paths["screening"]),
                "--responses", str(pairwise_paths["responses"]),
                "--output", str(pairwise_paths["output"]),
            ],
        )
        self.assertEqual(0, pairwise_completed.returncode, pairwise_completed.stderr)
        _validate_import_trace(
            pairwise_trace, closure, staged_runtime_root=stage
        )

    def test_recursive_finite_normalization_sorts_maps_and_preserves_list_order(self):
        value = {"z": [3, {"b": 2, "a": 1}], "a": {"d": True, "c": None}}
        normalized = _normalize_finite_json(value)
        self.assertEqual(["a", "z"], list(normalized))
        self.assertEqual([3, {"a": 1, "b": 2}], normalized["z"])
        for invalid in (
            {"x": float("nan")},
            {"x": float("inf")},
            {1: "non-string"},
            {"x": (object(),)},
        ):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ProducerRuntimeUnavailable):
                    _normalize_finite_json(invalid)

    def test_numpy_fingerprint_is_closed_sorted_and_binds_extension_bytes(self):
        one = self.base / "one.so"
        two = self.base / "two.so"
        one.write_bytes(b"numpy-extension")
        two.write_bytes(b"scipy-extension")
        probe = {
            "extension_modules": [
                {"distribution": "scipy", "module": "scipy.special._x", "path": str(two)},
                {"distribution": "numpy", "module": "numpy.core._x", "path": str(one)},
            ],
            "show_config": {"Build Dependencies": {"z": 2, "a": [2, 1]}},
        }
        fingerprint, fingerprint_sha = _build_numpy_fingerprint(probe)
        self.assertEqual(
            {
                "schema_version": "numpy-scipy-build-fingerprint-v1",
                "extension_modules": [
                    {
                        "distribution": "numpy",
                        "module": "numpy.core._x",
                        "path": str(one.resolve()),
                        "byte_count": len(b"numpy-extension"),
                        "raw_bytes_sha256": digest(b"numpy-extension"),
                    },
                    {
                        "distribution": "scipy",
                        "module": "scipy.special._x",
                        "path": str(two.resolve()),
                        "byte_count": len(b"scipy-extension"),
                        "raw_bytes_sha256": digest(b"scipy-extension"),
                    },
                ],
                "show_config": {"Build Dependencies": {"a": [2, 1], "z": 2}},
            },
            fingerprint,
        )
        self.assertEqual(digest(_canonical(fingerprint)), fingerprint_sha)

        duplicate = copy.deepcopy(probe)
        duplicate["extension_modules"][1]["path"] = str(two)
        with self.assertRaises(ProducerRuntimeUnavailable):
            _build_numpy_fingerprint(duplicate)
        for altered in (
            {**probe, "extra": True},
            {**probe, "show_config": {"x": float("nan")}},
        ):
            with self.assertRaises(ProducerRuntimeUnavailable):
                _build_numpy_fingerprint(altered)

    def test_macos_otool_literal_golden_and_exact_grammar(self):
        rows = _parse_otool_output(MACOS_EXTENSION, MACOS_OTOOL_GOLDEN)
        self.assertEqual(2, len(rows))
        self.assertEqual(
            {
                "extension_path": MACOS_EXTENSION,
                "install_name": (
                    "/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate"
                ),
                "compatibility_version": "1.0.0",
                "current_version": "4.0.0",
            },
            rows[0],
        )
        invalid = (
            " " + MACOS_OTOOL_GOLDEN,
            MACOS_OTOOL_GOLDEN.replace("\t/usr/lib", " /usr/lib"),
            MACOS_OTOOL_GOLDEN.replace("compatibility version", "compat version"),
            MACOS_OTOOL_GOLDEN + "\n",
            MACOS_OTOOL_GOLDEN.replace("current version 4.0.0", "current version 4.0.0) extra"),
            MACOS_OTOOL_GOLDEN.replace("\n\t/usr/lib", "\n\n\t/usr/lib"),
            MACOS_OTOOL_GOLDEN.replace("\n\t/usr/lib", "\n \t\n\t/usr/lib"),
            MACOS_OTOOL_GOLDEN.replace("\n\t/usr/lib", "\r\t/usr/lib"),
            MACOS_OTOOL_GOLDEN.replace("\n\t/usr/lib", "\v\t/usr/lib"),
            MACOS_OTOOL_GOLDEN.replace("\n\t/usr/lib", "\f\t/usr/lib"),
        )
        for stdout in invalid:
            with self.subTest(stdout=stdout):
                with self.assertRaises(ProducerRuntimeUnavailable):
                    _parse_otool_output(MACOS_EXTENSION, stdout)

    def test_linux_ldd_literal_golden_accepts_only_exact_virtual_row(self):
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_bind_resolved_library",
            side_effect=lambda extension, soname, path: {
                "extension_path": extension,
                "soname": soname,
                "resolved_path": path,
                "byte_count": 1,
                "raw_bytes_sha256": SHA,
            },
        ):
            rows = _parse_ldd_output(LINUX_EXTENSION, LINUX_LDD_GOLDEN)
        self.assertEqual(2, len(rows))
        self.assertEqual(
            [
                "libopenblas.so.0",
                "/usr/lib/x86_64-linux-gnu/libm.so.6",
            ],
            [row["soname"] for row in rows],
        )

        invalid = (
            LINUX_LDD_GOLDEN.replace(
                "linux-vdso.so.1 (0x00007ffd4d5f9000)",
                "linux-vdso.so.1 => (0x00007ffd4d5f9000)",
            ),
            LINUX_LDD_GOLDEN.replace(
                "linux-vdso.so.1", "linux-gate.so.1"
            ),
            LINUX_LDD_GOLDEN.replace(
                "/usr/lib/x86_64-linux-gnu/libopenblas.so.0", "not found"
            ),
            LINUX_LDD_GOLDEN.replace(
                "/usr/lib/x86_64-linux-gnu/libm.so.6", "relative/libm.so.6"
            ),
            LINUX_LDD_GOLDEN + "\tmalformed loader output\n",
            LINUX_LDD_GOLDEN.replace("\n\tlibopenblas", "\n\n\tlibopenblas"),
            LINUX_LDD_GOLDEN.replace("\n\tlibopenblas", "\n \t\n\tlibopenblas"),
            LINUX_LDD_GOLDEN.replace("\n\tlibopenblas", "\r\tlibopenblas"),
            LINUX_LDD_GOLDEN.replace("\n\tlibopenblas", "\v\tlibopenblas"),
            LINUX_LDD_GOLDEN.replace("\n\tlibopenblas", "\f\tlibopenblas"),
            LINUX_LDD_GOLDEN.removesuffix("\n"),
        )
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_bind_resolved_library",
            return_value={
                "extension_path": LINUX_EXTENSION,
                "soname": "x",
                "resolved_path": "/x",
                "byte_count": 1,
                "raw_bytes_sha256": SHA,
            },
        ):
            for stdout in invalid:
                with self.subTest(stdout=stdout):
                    with self.assertRaises(ProducerRuntimeUnavailable):
                        _parse_ldd_output(LINUX_EXTENSION, stdout)

    def test_link_fingerprints_are_closed_sorted_and_platform_bound(self):
        extensions = [{"path": MACOS_EXTENSION}]
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_run_root_owned_tool",
            return_value=MACOS_OTOOL_GOLDEN,
        ):
            value, value_sha = _build_link_fingerprint(
                platform_system="Darwin",
                platform_release="23.6.0",
                machine="arm64",
                extension_modules=extensions,
                numpy_build_sha256=SHA,
            )
        self.assertEqual("macos-accelerate-link-fingerprint-v1", value["schema_version"])
        self.assertEqual("Accelerate", value["framework"])
        self.assertEqual(digest(_canonical(value)), value_sha)

        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_run_root_owned_tool",
            return_value=MACOS_OTOOL_GOLDEN.replace(
                "/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate",
                "/usr/local/lib/libopenblas.dylib",
            ),
        ):
            with self.assertRaises(ProducerRuntimeUnavailable):
                _build_link_fingerprint(
                    platform_system="Darwin",
                    platform_release="23.6.0",
                    machine="arm64",
                    extension_modules=extensions,
                    numpy_build_sha256=SHA,
                )

        with self.assertRaises(ProducerRuntimeUnavailable):
            _build_link_fingerprint(
                platform_system="Windows",
                platform_release="11",
                machine="AMD64",
                extension_modules=extensions,
                numpy_build_sha256=SHA,
            )
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_run_root_owned_tool",
            return_value=LINUX_LDD_GOLDEN,
        ), patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_bind_resolved_library",
            side_effect=lambda extension, soname, path: {
                "extension_path": extension,
                "soname": soname,
                "resolved_path": path,
                "byte_count": 1,
                "raw_bytes_sha256": SHA,
            },
        ):
            linux, linux_sha = _build_link_fingerprint(
                platform_system="Linux",
                platform_release="6.8.0-31-generic",
                machine="x86_64",
                extension_modules=[{"path": LINUX_EXTENSION}],
                numpy_build_sha256=SHA,
            )
        self.assertEqual(
            "linux-blas-lapack-link-fingerprint-v1", linux["schema_version"]
        )
        self.assertEqual(2, len(linux["libraries"]))
        self.assertEqual(digest(_canonical(linux)), linux_sha)

        with self.assertRaises(ProducerRuntimeUnavailable):
            _run_root_owned_tool(self.base / "missing-ldd", LINUX_EXTENSION)

    def test_policy_bindings_lock_configuration_ties_and_upstream_semantics(self):
        complete = _build_policy_bindings(
            "complete_exposure_ordering",
            self._complete_configuration(),
            None,
            self.runtime,
        )
        self.assertEqual("exact-utility-equality-v1", complete["ordering_equivalence"])
        self.assertEqual(1e-12, complete["cutoff_tie_tolerance"])
        self.assertEqual(
            digest(_canonical(self._complete_configuration()["recovery_configuration"])),
            complete["recovery_configuration_sha256"],
        )

        for surface, configuration, upstream in (
            ("maxdiff_screening_ordering", self._maxdiff_configuration(1e-14), None),
            ("pairwise_boundary_ordering", self._pairwise_configuration(1e-14), SHA),
        ):
            policy = _build_policy_bindings(
                surface, configuration, upstream, self.runtime
            )
            self.assertEqual("rounded-utility-bucket-v1", policy["ordering_equivalence"])
            self.assertEqual(1e-12, policy["effective_ordering_tolerance"])
            self.assertEqual("python-half-even-v1", policy["rounding_rule"])
            self.assertEqual(
                "creative-id-serialization-only-v1", policy["ordering_tiebreak"]
            )
            if surface == "maxdiff_screening_ordering":
                self.assertEqual(
                    digest(_canonical(configuration["maxdiff_configuration"])),
                    policy["maxdiff_configuration_sha256"],
                )
                self.assertEqual(
                    digest(_canonical(configuration["recovery_configuration"])),
                    policy["recovery_configuration_sha256"],
                )
            else:
                self.assertEqual(
                    digest(_canonical(configuration["pairwise_configuration"])),
                    policy["pairwise_configuration_sha256"],
                )

        altered = self._maxdiff_configuration()
        altered["maxdiff_configuration"]["bootstrap_count"] = 1999
        with self.assertRaises(ProducerAuthenticationError):
            _build_policy_bindings(
                "maxdiff_screening_ordering", altered, None, self.runtime
            )
        with self.assertRaises(ProducerAuthenticationError):
            _build_policy_bindings(
                "pairwise_boundary_ordering",
                self._pairwise_configuration(),
                None,
                self.runtime,
            )

    def test_configuration_objects_are_exact_closed_and_exactly_typed(self):
        cases: list[tuple[str, dict[str, object], str | None]] = []
        complete = self._complete_configuration()
        complete["recovery_configuration"]["extra"] = True
        cases.append(("complete_exposure_ordering", complete, None))
        complete = self._complete_configuration()
        del complete["recovery_configuration"]["scope"]
        cases.append(("complete_exposure_ordering", complete, None))
        complete = self._complete_configuration()
        complete["recovery_configuration"]["planned_jobs_per_segment"] = 9.0
        cases.append(("complete_exposure_ordering", complete, None))

        maxdiff = self._maxdiff_configuration()
        del maxdiff["maxdiff_configuration"]["penalty_lambda"]
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["maxdiff_configuration"]["seed"] = 123.0
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["maxdiff_configuration"]["invented"] = "field"
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["recovery_configuration"]["bootstrap_count"] = 2000.0
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["recovery_configuration"]["library_size_bands"][0]["minimum"] = 4.0
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["recovery_configuration"]["extra"] = True
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["recovery_configuration"]["successful_fit_floor"] = 0.90
        cases.append(("maxdiff_screening_ordering", maxdiff, None))
        maxdiff = self._maxdiff_configuration()
        maxdiff["maxdiff_configuration"]["successful_fit_floor"] = 0.90
        cases.append(("maxdiff_screening_ordering", maxdiff, None))

        pairwise = self._pairwise_configuration()
        del pairwise["pairwise_configuration"]["tie_parameter"]
        cases.append(("pairwise_boundary_ordering", pairwise, SHA))
        pairwise = self._pairwise_configuration()
        pairwise["pairwise_configuration"]["bootstrap_count"] = 2000.0
        cases.append(("pairwise_boundary_ordering", pairwise, SHA))
        pairwise = self._pairwise_configuration()
        pairwise["pairwise_configuration"]["extra"] = True
        cases.append(("pairwise_boundary_ordering", pairwise, SHA))

        for surface, configuration, upstream in cases:
            with self.subTest(surface=surface, configuration=configuration):
                with self.assertRaises(ProducerAuthenticationError):
                    _build_policy_bindings(
                        surface, configuration, upstream, self.runtime
                    )

    def test_policy_constants_must_be_present_in_sealed_dependency_closure(self):
        with self.assertRaises(ProducerAuthenticationError):
            _build_policy_bindings(
                "complete_exposure_ordering",
                self._complete_configuration(),
                None,
                self.runtime,
                dependency_closure=[],
            )
        closure = _discover_dependency_closure(self.runtime, ENTRY)
        altered = copy.deepcopy(closure)
        constant_path = (
            "skills/audience-ad-testing-lab/scripts/audience_lab/"
            "complete_exposure.py"
        )
        next(
            row for row in altered if row["path"] == constant_path
        )["raw_bytes_sha256"] = SHA
        with self.assertRaises(ProducerAuthenticationError):
            _build_policy_bindings(
                "complete_exposure_ordering",
                self._complete_configuration(),
                None,
                self.runtime,
                dependency_closure=altered,
            )
        with self.assertRaises(ProducerAuthenticationError):
            _build_policy_bindings(
                "complete_exposure_ordering",
                self._complete_configuration(),
                SHA,
                self.runtime,
            )

    def test_serialization_objects_are_distinct_closed_and_immutable(self):
        self.assertEqual(
            {
                "encoding": "utf-8",
                "indent": 2,
                "sort_keys": True,
                "allow_nan": False,
                "ensure_ascii": True,
                "separators": None,
                "terminal_lf": True,
            },
            PRODUCER_RAW_SERIALIZATION,
        )
        self.assertEqual(
            {
                "encoding": "utf-8",
                "indent": None,
                "sort_keys": True,
                "allow_nan": False,
                "ensure_ascii": False,
                "separators": [",", ":"],
                "terminal_lf": True,
            },
            CANONICAL_DOCUMENT_SERIALIZATION,
        )
        with self.assertRaises(TypeError):
            PRODUCER_RAW_SERIALIZATION["indent"] = 4
        with self.assertRaises(TypeError):
            CANONICAL_DOCUMENT_SERIALIZATION["ensure_ascii"] = True

    def test_build_returns_closed_self_hashed_surface_bundle(self):
        with patch(
            "audience_panel_builder.population.validation.producer_semantics."
            "_build_runtime_fingerprint",
            return_value=fake_runtime_fingerprint(),
        ):
            bundle = build_producer_semantics(
                surface="maxdiff_screening_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration=self._maxdiff_configuration(),
                upstream_semantics_sha256=None,
            )
        self.assertIsInstance(bundle, ProducerSemanticsBundle)
        self.assertEqual(self.stage.resolve(), bundle.staged_runtime_root)
        semantics = bundle.semantics
        self.assertEqual(
            {
                "entry_point", "subcommand", "bootstrap_sha256",
                "dependency_closure", "runtime_fingerprint", "policy_bindings",
                "output_serialization", "producer_semantics_sha256",
            },
            set(semantics),
        )
        self.assertEqual(ENTRY, semantics["entry_point"])
        self.assertEqual("screening", semantics["subcommand"])
        expected = digest(_canonical({**semantics, "producer_semantics_sha256": None}))
        self.assertEqual(expected, semantics["producer_semantics_sha256"])

        changed = copy.deepcopy(semantics)
        changed["dependency_closure"][0]["raw_bytes_sha256"] = SHA
        self.assertNotEqual(
            semantics["producer_semantics_sha256"],
            digest(_canonical({**changed, "producer_semantics_sha256": None})),
        )
        raw = semantics["output_serialization"]["producer_raw_serialization"]
        raw["indent"] = 4
        self.assertEqual(2, PRODUCER_RAW_SERIALIZATION["indent"])

    def test_unknown_surface_and_hostile_runtime_or_stage_fail_closed(self):
        with self.assertRaises(ProducerAuthenticationError):
            build_producer_semantics(
                surface="finalist_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=self.stage,
                configuration={},
                upstream_semantics_sha256=None,
            )
        symlink_root = self.base / "runtime-link"
        symlink_root.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises(ProducerAuthenticationError):
            build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=symlink_root,
                staged_runtime_root=self.stage,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )
        stage_target = self.base / "stage-target"
        stage_target.mkdir()
        stage_link = self.base / "stage-link"
        stage_link.symlink_to(stage_target, target_is_directory=True)
        with self.assertRaises(ProducerAuthenticationError):
            build_producer_semantics(
                surface="complete_exposure_ordering",
                runtime_root=self.runtime,
                staged_runtime_root=stage_link,
                configuration=self._complete_configuration(),
                upstream_semantics_sha256=None,
            )


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
