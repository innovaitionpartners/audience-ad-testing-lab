"""Synthetic-only base-versus-candidate exercise conformance."""

from __future__ import annotations

import contextlib
from copy import deepcopy
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PANEL_BUILDER_SCRIPTS = (
    ROOT / "skills" / "audience-panel-builder" / "scripts"
)
AD_TESTING_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))
sys.path.insert(0, str(AD_TESTING_SCRIPTS))

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.experimental_calibration import (  # noqa: E402
    exercise as exercise_module,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    SYNTHETIC_SCENARIO_REGISTRY,
)
from audience_panel_builder.population.experimental_calibration.exercise import (  # noqa: E402
    ExerciseDependencyUnavailable,
    ExerciseSourceIsolationFailure,
    authenticate_candidate_seal_envelope,
    authenticate_frozen_adapter_source,
    build_synthetic_panel_exercise,
    load_public_scenario_inputs,
    project_adapter_output_to_ad_testing_response,
    runtime_dependencies_available,
)
from audience_lab.responses import validate_job, validate_response  # noqa: E402
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    exercise_inputs_fixture,
    materialized_candidate_envelope_fixture,
    public_scenario_inputs_fixture,
    sealed_candidate_envelope_fixture,
)
from experimental_persona_calibration_oracle.sandbox import (  # noqa: E402
    _ENTRYPOINTS,
    _assert_declared_source_closure,
    _discover_closure,
    _load_declared_source_manifest,
)


FIXTURES = ROOT / "conformance" / "fixtures" / "experimental-calibration"
CLI = (
    ROOT
    / "skills"
    / "audience-panel-builder"
    / "scripts"
    / "run-synthetic-persona-behavior-exercise.py"
)
MANIFEST = (
    ROOT
    / "skills"
    / "audience-panel-builder"
    / "scripts"
    / "audience_panel_builder"
    / "population"
    / "experimental_calibration"
    / "private_stage_manifests"
    / "exercise.json"
)


def _job(
    record_type: str,
    method: str,
    variation_ids: list[str],
) -> dict[str, object]:
    shown = list(reversed(variation_ids))
    job: dict[str, object] = {
        "study_id": "fictional-exercise-study",
        "response_id": f"response-{record_type}",
        "record_type": record_type,
        "method": method,
        "synthetic_replicate_id": f"replicate-{record_type}",
        "dispatch_id": f"dispatch-{record_type}",
        "persona_archetype_id": "finance-pricing-archetype",
        "segment_id": "operations-leaders",
        "context_stratum_id": "active-evaluation",
        "audience_slot_id": f"replicate-{record_type}",
        "grounded_profile_id": "midmarket-proof-seeking",
        "profile_snapshot_sha256": "sha256:" + "1" * 64,
        "profile_snapshot": {
            "anxieties": ["Implementation and commercial risk"],
            "decision_context": "Evaluating fictional pricing software",
            "motivations": ["Improve planning confidence"],
            "proof_needs": ["Pricing and returns mechanism"],
            "role_context": "Fictional CFO or finance leader",
        },
        "context_attribute_provenance": [
            {
                "attribute": "buying_stage",
                "value": "active_evaluation",
                "status": "observed",
                "source_evidence": ["fictional-evidence"],
            }
        ],
        "worker_context_isolation": "isolated",
        "human_sample_independence": False,
        "variation_ids": variation_ids,
        "shown_order": shown,
        "blind_labels": {
            creative_id: chr(ord("A") + index)
            for index, creative_id in enumerate(shown)
        },
        "reaction_protocol": "progressive_reveal",
        "reaction_prompts": [
            f"Review blind creative {index + 1}."
            for index in range(len(shown))
        ],
        "comparison_prompt": "Compare only the frozen blind creatives.",
    }
    return job


def _adapter_output(job: dict[str, object]) -> dict[str, object]:
    ranking = list(job["variation_ids"])
    return {
        "adapter_id": "frozen-synthetic-panelist-response",
        "adapter_version": "1.0.0",
        "dispatch_id": job["dispatch_id"],
        "tie_rule": "score-descending-creative-id-ascending",
        "ranking": [
            {
                "position": position,
                "creative_id": creative_id,
                "score": 1000 - position,
            }
            for position, creative_id in enumerate(ranking, 1)
        ],
    }


ATTEMPT_POLICY = {
    "capture": "frozen_adapter_ranking_projection",
    "reaction_text": "Deterministic synthetic machinery probe.",
}


class ExperimentalCalibrationExerciseTests(unittest.TestCase):
    def test_exercise_module_exposes_the_approved_interfaces(self):
        self.assertTrue(callable(build_synthetic_panel_exercise))
        self.assertTrue(callable(project_adapter_output_to_ad_testing_response))

    def test_frozen_adapter_bytes_are_authenticated_twice_and_match_manifest(self):
        study_manifest = json.loads(
            (FIXTURES / "study-manifest.json").read_text(encoding="utf-8")
        )
        binding = authenticate_frozen_adapter_source(study_manifest)
        self.assertEqual(
            study_manifest["synthetic_response_adapter"]["source_sha256"],
            binding["source_sha256"],
        )
        self.assertEqual(binding["first_read_sha256"], binding["second_read_sha256"])
        self.assertEqual(
            {
                "creative_attributes",
                "experiment_design",
                "persona_snapshot",
                "study_manifest",
            },
            set(binding["feature_allowlist"]),
        )
        source = inspect.getsource(
            __import__(
                "audience_panel_builder.population.experimental_calibration."
                "synthetic_response_adapter",
                fromlist=["synthetic_panelist_response"],
            ).synthetic_panelist_response
        )
        self.assertNotIn("canonical_observations", source)
        self.assertNotIn("hidden_oracle", source)

    def test_frozen_adapter_source_authentication_failure_is_distinct(self):
        study_manifest = json.loads(
            (FIXTURES / "study-manifest.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing-adapter.py"
            with mock.patch.object(
                exercise_module,
                "_ADAPTER_SOURCE",
                missing,
            ):
                with self.assertRaises(ExerciseSourceIsolationFailure):
                    authenticate_frozen_adapter_source(study_manifest)

    def test_public_scenario_loader_admits_only_manifest_bound_regular_files(self):
        rows = public_scenario_inputs_fixture()
        self.assertEqual(
            set(SYNTHETIC_SCENARIO_REGISTRY),
            {
                row["scenario_manifest"]["scenario_binding"]["scenario_id"]
                for row in rows
            },
        )
        for row in rows:
            self.assertEqual(
                {
                    "admitted_public_files",
                    "experiment_design",
                    "scenario_manifest",
                },
                set(row),
            )
            self.assertFalse(
                any("oracle" in item["path"].casefold() for item in row["admitted_public_files"])
            )
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "public"
            import shutil

            copied.mkdir()
            for partition in ("open", "sealed"):
                shutil.copytree(
                    FIXTURES / partition,
                    copied / partition,
                )
            (copied / "open" / "known-proof-need-miss" / "extra.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ContractError, "exact manifest-bound"):
                load_public_scenario_inputs(copied)
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "public"
            copied.mkdir()
            for partition in ("open", "sealed"):
                shutil.copytree(
                    FIXTURES / partition,
                    copied / partition,
                )
            (copied / "unexpected.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "exactly open and sealed"):
                load_public_scenario_inputs(copied)
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "public"
            copied.mkdir()
            (copied / "open").symlink_to(
                FIXTURES / "open",
                target_is_directory=True,
            )
            shutil.copytree(FIXTURES / "sealed", copied / "sealed")
            with self.assertRaisesRegex(ContractError, "non-symlinked directory"):
                load_public_scenario_inputs(copied)
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "public"
            copied.mkdir()
            for partition in ("open", "sealed"):
                shutil.copytree(
                    FIXTURES / partition,
                    copied / partition,
                )
            (
                copied
                / "open"
                / "known-proof-need-miss"
                / "undeclared-empty"
            ).mkdir()
            with self.assertRaisesRegex(
                ContractError,
                "exact manifest-bound directories",
            ):
                load_public_scenario_inputs(copied)

    def test_all_four_ad_testing_response_paths_validate(self):
        cases = (
            ("screening_response", "complete_exposure", ["a", "b", "c", "d"]),
            (
                "screening_response",
                "partial_exposure_maxdiff",
                ["a", "b", "c", "d"],
            ),
            ("boundary_response", "partial_exposure_maxdiff", ["a", "b"]),
            ("finalist_response", "complete_exposure", ["a", "b"]),
        )
        for record_type, method, creative_ids in cases:
            with self.subTest(record_type=record_type, method=method):
                job = _job(record_type, method, creative_ids)
                self.assertEqual([], validate_job(job))
                response = project_adapter_output_to_ad_testing_response(
                    adapter_output=_adapter_output(job),
                    validated_job=job,
                    frozen_attempt_policy=ATTEMPT_POLICY,
                )
                self.assertEqual([], validate_response(response, job))
                self.assertEqual(
                    job["dispatch_id"], response["reviewer_dispatch_id"]
                )
                self.assertEqual(
                    {
                        "audience_slot_id",
                        "grounded_profile_id",
                        "profile_snapshot_sha256",
                    },
                    {
                        key
                        for key in response
                        if key
                        in {
                            "audience_slot_id",
                            "grounded_profile_id",
                            "profile_snapshot_sha256",
                        }
                    },
                )

    def test_missing_pinned_optimizer_dependencies_fail_closed(self):
        if runtime_dependencies_available():
            self.skipTest("pinned NumPy/SciPy are available on this host")
        with self.assertRaises(ExerciseDependencyUnavailable):
            build_synthetic_panel_exercise(**exercise_inputs_fixture())

    @unittest.skipUnless(
        runtime_dependencies_available(),
        "dependency-complete exercise runs in Task 9 CI",
    )
    def test_complete_matrix_cardinality_hashes_and_input_nonmutation(self):
        inputs = exercise_inputs_fixture()
        before = canonical_json_bytes(inputs)
        exercise = build_synthetic_panel_exercise(**inputs)
        self.assertEqual(before, canonical_json_bytes(inputs))
        panels = exercise["panel_bindings"]
        expected = {
            (
                family["scenario_id"],
                repetition,
                panel["exercise_panel_ref"],
            )
            for family in inputs["study_manifest"]["scenario_families"]
            for repetition in range(family["repetitions"])
            for panel in panels
        }
        self.assertEqual(
            expected,
            {
                (
                    row["scenario_id"],
                    row["repetition"],
                    row["exercise_panel_ref"],
                )
                for row in exercise["run_results"]
            },
        )
        roster_sizes = {
            row["exercise_panel_ref"]: len(row["members"])
            for row in exercise["panel_rosters"]
        }
        expected_job_count = sum(
            4 * roster_sizes[row["exercise_panel_ref"]]
            for row in exercise["run_results"]
        )
        self.assertEqual(
            expected_job_count,
            len(exercise["panelist_jobs"]),
        )
        self.assertEqual(
            len(exercise["panelist_jobs"]),
            len({row["dispatch_id"] for row in exercise["panelist_jobs"]}),
        )
        expected_phases = {
            "complete-exposure",
            "maxdiff-screening",
            "pairwise-boundary",
            "finalist-verbatim",
        }
        self.assertEqual(
            expected_phases,
            {row["phase"] for row in exercise["panelist_jobs"]},
        )
        for result in exercise["run_results"]:
            with self.subTest(
                scenario_id=result["scenario_id"],
                repetition=result["repetition"],
                exercise_panel_ref=result["exercise_panel_ref"],
            ):
                member_count = roster_sizes[result["exercise_panel_ref"]]
                phases = [
                    row["phase"]
                    for row in exercise["panelist_jobs"]
                    if (
                        row["scenario_id"] == result["scenario_id"]
                        and row["repetition"] == result["repetition"]
                        and row["exercise_panel_ref"]
                        == result["exercise_panel_ref"]
                    )
                ]
                self.assertEqual(4 * member_count, len(phases))
                self.assertEqual(
                    {phase: member_count for phase in expected_phases},
                    {phase: phases.count(phase) for phase in expected_phases},
                )
                self.assertEqual(
                    {
                        "screening_planned": 2 * member_count,
                        "boundary_reserved": member_count,
                        "finalist_reserved": member_count,
                        "required_total": 4 * member_count,
                        "ceiling": 4 * member_count,
                        "ceiling_satisfied": True,
                    },
                    result["capacity_plan"],
                )
        self.assertTrue(
            all(
                row["worker_context_isolation"] == "isolated"
                for row in exercise["panelist_jobs"]
            )
        )
        self.assertEqual(
            exercise,
            build_synthetic_panel_exercise(**deepcopy(inputs)),
        )

    def test_candidate_envelope_is_nonempty_plural_and_exact(self):
        inputs = exercise_inputs_fixture()
        candidates = inputs["candidate_bindings_and_panels"]
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(
            len(candidates),
            len(
                {
                    row["materialized_candidate"]["candidate_binding"][
                        "candidate_id"
                    ]
                    for row in candidates
                }
            ),
        )
        tampered = deepcopy(inputs)
        tampered["candidate_bindings_and_panels"][0][
            "materialized_candidate"
        ]["candidate_panel"]["panel_name"] = "forged"
        with self.assertRaises(ContractError):
            build_synthetic_panel_exercise(**tampered)
        empty = deepcopy(inputs)
        empty["candidate_bindings_and_panels"] = []
        with self.assertRaisesRegex(ContractError, "nonempty plural"):
            build_synthetic_panel_exercise(**empty)

    def test_registered_role_has_committed_exact_source_manifest(self):
        role = _ENTRYPOINTS["exercise"]
        self.assertEqual("numpy", role.external_runtime_modules[0])
        self.assertEqual("scipy", role.external_runtime_modules[1])
        self.assertTrue(MANIFEST.is_file())
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        supplied = document["source_manifest_sha256"]
        document["source_manifest_sha256"] = None
        self.assertEqual(
            supplied,
            "sha256:" + hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
        )
        paths = {row["path"] for row in document["files"]}
        self.assertIn(
            "audience_panel_builder/population/experimental_calibration/exercise.py",
            paths,
        )
        self.assertIn("run-synthetic-persona-behavior-exercise.py", paths)
        discovered = _discover_closure(
            CLI,
            omitted_initializers=role.namespace_packages,
        )
        declared = _load_declared_source_manifest("exercise", role)
        _assert_declared_source_closure(declared["files"], discovered)
        self.assertEqual(34, len(discovered))

    def test_cli_uses_closed_exit_codes_and_never_accepts_production_authority(self):
        source = CLI.read_text(encoding="utf-8")
        for forbidden in (
            "audience_library",
            "register_package",
            "active_pointer",
            "activation",
            "package_manifest",
        ):
            self.assertNotIn(forbidden, source)
        completed = subprocess.run(
            [sys.executable, str(CLI)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(2, completed.returncode)

    def test_cli_distinguishes_source_isolation_contract_and_output_failures(self):
        spec = importlib.util.spec_from_file_location(
            "run_synthetic_persona_behavior_exercise_test",
            CLI,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "exercise.json"
            argv = [
                "--study-manifest",
                "study.json",
                "--public-scenarios-root",
                "public",
                "--creative-attribute-registry",
                "attributes.json",
                "--base-panel",
                "base.json",
                "--candidate-bindings-and-panels",
                "candidates.json",
                "--exercise-id",
                "exercise-001",
                "--exercised-at",
                "2026-07-30T00:00:00Z",
                "--output",
                str(output),
            ]
            cases = (
                (ExerciseSourceIsolationFailure("source auth failed"), 4),
                (ContractError("ordinary contract failed"), 2),
                (OSError("unsafe output failed"), 3),
            )
            for failure, expected in cases:
                with self.subTest(failure=type(failure).__name__):
                    with (
                        mock.patch.object(module, "_read_json", return_value={}),
                        mock.patch.object(
                            module,
                            "load_public_scenario_inputs",
                            return_value=[],
                        ),
                        mock.patch.object(
                            module,
                            "build_synthetic_panel_exercise",
                            side_effect=failure,
                        ),
                    ):
                        with contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(expected, module.main(argv))

    def test_materialized_fixture_contains_complete_task6_graph(self):
        envelope = materialized_candidate_envelope_fixture(
            candidate_id="candidate-001",
            candidate_version="1.1.0",
            created_at="2026-07-21T00:00:00Z",
        )
        self.assertEqual(
            {
                "base_authoring_projection",
                "base_persona_snapshot",
                "candidate_authoring_projection",
                "candidate_binding",
                "candidate_panel",
                "candidate_persona_snapshot",
                "experimental_proposal",
                "persona_behavior_diff",
                "standalone_panel_validation",
            },
            set(envelope),
        )

    @unittest.skipUnless(
        (
            platform.system() == "Darwin"
            and Path("/usr/bin/sandbox-exec").is_file()
        )
        or (
            platform.system() == "Linux"
            and Path("/usr/bin/bwrap").is_file()
        ),
        "real private-stage provider is unavailable",
    )
    def test_candidate_must_byte_match_its_registered_task6_seal(self):
        first = sealed_candidate_envelope_fixture(
            candidate_id="candidate-001",
            candidate_version="1.1.0",
            created_at="2026-07-21T00:00:00Z",
        )
        second = sealed_candidate_envelope_fixture(
            candidate_id="candidate-002",
            candidate_version="1.2.0",
            created_at="2026-07-22T00:00:00Z",
        )
        receipt = first["candidate_seal_receipt"]
        self.assertEqual(
            "experimental-calibration-phase-execution-receipt-v1",
            receipt["schema_version"],
        )
        self.assertEqual("materialize", receipt["engine_entrypoint"])
        self.assertEqual(
            {"kind": "directory", "name": "result"},
            {
                key: receipt["output"][key]
                for key in ("kind", "name")
            },
        )
        self.assertEqual(
            first,
            authenticate_candidate_seal_envelope(first),
        )

        rebranded = deepcopy(first)
        rebranded["materialized_candidate"] = second[
            "materialized_candidate"
        ]
        with self.assertRaisesRegex(
            ContractError,
            "Task 6|candidate seal|sealed bundle",
        ):
            authenticate_candidate_seal_envelope(rebranded)


if __name__ == "__main__":
    unittest.main()
