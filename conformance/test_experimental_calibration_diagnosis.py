"""Attack-first tests for synthetic-only persona-behavior diagnosis."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
)
from audience_panel_builder.population.experimental_calibration.diagnosis import (  # noqa: E402
    _estimate_blocked_contrasts,
    _reconcile_hypothesis_strata,
    diagnose_persona_behavior,
)
from audience_panel_builder.population.experimental_calibration.proposal import (  # noqa: E402
    ProposalNotPermitted,
    build_experimental_proposal,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    validate_evidence_library,
    validate_study_manifest,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    diagnosis_inputs_fixture,
    proposal_inputs_fixture,
    rehash,
)


class ExperimentalCalibrationDiagnosisTests(unittest.TestCase):
    maxDiff = None

    def diagnose(self, **overrides):
        inputs = diagnosis_inputs_fixture()
        inputs.update(overrides)
        return diagnose_persona_behavior(**inputs)

    def test_repeatable_segment_bound_proof_need_miss_is_identified(self):
        result = self.diagnose()
        self.assertEqual("repeatable_behavioral_miss", result["decision"])
        self.assertEqual(
            "finance-pricing-archetype", result["target_persona_id"]
        )
        self.assertEqual(
            "proof_needs",
            result["selected_hypothesis"]["target_persona_field"],
        )
        estimate = result["analysis"]["combined"]
        self.assertAlmostEqual(0.046248873366154256, estimate["point_estimate"])
        self.assertAlmostEqual(
            0.046082821835028105, estimate["bootstrap_mean"]
        )
        self.assertAlmostEqual(0.03808680038309213, estimate["interval_lower"])
        self.assertAlmostEqual(0.054372861382411194, estimate["interval_upper"])
        self.assertEqual(
            {
                "bootstrap_mean": 0.00014207498025438708,
                "interval_lower": 0.00032867597701258776,
                "interval_upper": 0.0003556818800559148,
            },
            estimate["monte_carlo_standard_error"],
        )
        self.assertEqual(
            "unverified_proposal_context",
            result["base_panel_authority_status"],
        )

    def test_full_four_arm_projection_is_required(self):
        inputs = diagnosis_inputs_fixture()
        block_arms = {
            entry["observation"]["experiment_binding"]["arm_id"]
            for entry in inputs["evidence_library_snapshot"]["entries"]
            if entry["experiment_id"] == "fictional-experiment-1"
            and entry["block_id"] == "block-e01-cfo-meta-01"
        }
        self.assertEqual(
            {
                "ease-of-use",
                "peer-validation",
                "quantified-payback",
                "strategic-control",
            },
            block_arms,
        )
        pruned = diagnosis_inputs_fixture(evidence_variant="pruned")
        self.assertEqual(
            "insufficient_evidence",
            diagnose_persona_behavior(**pruned)["decision"],
        )

    def test_blocks_then_experiments_receive_equal_weight_even_when_counts_differ(self):
        manifest = diagnosis_inputs_fixture()["study_manifest"]
        points, combined = _estimate_blocked_contrasts(
            {
                ("experiment-one", "campaign-one"): [0.1] * 6,
                ("experiment-two", "campaign-two"): [0.3] * 12,
            },
            diagnosis_method=manifest["diagnosis_method"],
            monte_carlo_error_targets=manifest[
                "monte_carlo_error_targets"
            ],
        )
        self.assertAlmostEqual(
            0.1, points[("experiment-one", "campaign-one")]
        )
        self.assertAlmostEqual(
            0.3, points[("experiment-two", "campaign-two")]
        )
        self.assertAlmostEqual(0.2, combined["point_estimate"])
        self.assertNotAlmostEqual(
            (0.1 * 6 + 0.3 * 12) / 18,
            combined["point_estimate"],
        )

    def test_blocked_contrast_resists_a_simpson_style_pooled_reversal(self):
        # Every registered within-block contrast is +0.1, while pooling the
        # numerators and denominators across differently sized blocks reverses
        # the sign. The estimator receives block contrasts, never pooled rows.
        within_blocks = [
            9 / 10 - 80 / 100,
            10 / 100 - 0 / 10,
        ]
        pooled = (9 + 10) / (10 + 100) - (80 + 0) / (100 + 10)
        self.assertTrue(all(value > 0 for value in within_blocks))
        self.assertLess(pooled, 0)
        manifest = diagnosis_inputs_fixture()["study_manifest"]
        _, combined = _estimate_blocked_contrasts(
            {
                ("experiment-one", "campaign-one"): within_blocks * 3,
                ("experiment-two", "campaign-two"): within_blocks * 3,
            },
            diagnosis_method=manifest["diagnosis_method"],
            monte_carlo_error_targets=manifest[
                "monte_carlo_error_targets"
            ],
        )
        self.assertAlmostEqual(0.1, combined["point_estimate"])

    def test_null_fixture_near_zero_noise_produces_no_change(self):
        inputs = diagnosis_inputs_fixture(scenario_id="null-effect")
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("no_repeatable_miss", result["decision"])

    def test_all_positive_subthreshold_evidence_produces_no_change(self):
        inputs = diagnosis_inputs_fixture(
            evidence_variant="subthreshold-positive"
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("no_repeatable_miss", result["decision"])
        self.assertEqual([], result["eligible_hypothesis_ids"])
        self.assertIsNone(result["selected_hypothesis"])

    def test_one_experiment_is_insufficient(self):
        inputs = diagnosis_inputs_fixture(experiment_limit=1)
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("insufficient_evidence", result["decision"])

    def test_fewer_than_six_complete_blocks_is_insufficient(self):
        inputs = diagnosis_inputs_fixture(drop_last_block=True)
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("insufficient_evidence", result["decision"])

    def test_duplicate_dependency_is_invalid(self):
        inputs = diagnosis_inputs_fixture()
        snapshot = inputs["evidence_library_snapshot"]
        duplicate = deepcopy(snapshot["entries"][0])
        duplicate["entry_id"] = "fictional-duplicate"
        duplicate["observation"]["observation_id"] = "fictional-duplicate"
        duplicate["observation"] = rehash(
            duplicate["observation"], "observation_sha256"
        )
        duplicate["observation_sha256"] = duplicate["observation"][
            "observation_sha256"
        ]
        duplicate = rehash(duplicate, "entry_sha256")
        snapshot["entries"].append(duplicate)
        snapshot["entries"].sort(key=lambda row: row["entry_id"])
        snapshot["entry_ids"] = [row["entry_id"] for row in snapshot["entries"]]
        snapshot = rehash(snapshot, "library_sha256")
        inputs["evidence_library_snapshot"] = snapshot
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("invalid_evidence", result["decision"])

    def test_incompatible_or_non_authorizing_rows_are_insufficient(self):
        for variant in ("google", "recent", "observational"):
            with self.subTest(variant=variant):
                inputs = diagnosis_inputs_fixture(evidence_variant=variant)
                if variant == "google":
                    design = inputs["experiment_designs"][0]
                    design["analytical_cells"] = [
                        cell
                        for cell in design["analytical_cells"]
                        if cell["platform"] == "meta"
                    ]
                    inputs["experiment_designs"][0] = rehash(
                        design, "design_sha256"
                    )
                    for cause in inputs["alternative_causes"].values():
                        cause["evidence_sha256"] = inputs[
                            "experiment_designs"
                        ][0]["design_sha256"]
                result = diagnose_persona_behavior(**inputs)
                self.assertEqual(
                    "invalid_evidence" if variant == "google"
                    else "insufficient_evidence",
                    result["decision"],
                )

    def test_contradictory_experiment_signs_are_non_identifiable(self):
        inputs = diagnosis_inputs_fixture(contradictory=True)
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("non_identifiable", result["decision"])
        self.assertEqual([], result["eligible_hypothesis_ids"])

    def test_any_negative_experiment_sign_is_non_identifiable(self):
        inputs = diagnosis_inputs_fixture(evidence_variant="weak-contrary")
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("non_identifiable", result["decision"])
        points = [
            row["point_estimate"] for row in result["analysis"]["experiments"]
        ]
        threshold = result["analysis"]["combined"][
            "minimum_practical_effect"
        ]
        self.assertTrue(any(point > threshold for point in points))
        self.assertTrue(any(point < 0.0 for point in points))

    def test_same_hypothesis_strata_reconcile_before_selection(self):
        manifest = diagnosis_inputs_fixture()["study_manifest"]
        hypothesis = {
            "hypothesis_id": "quantified-payback-proof-need",
            "compatibility_key_sha256": "sha256:" + "1" * 64,
        }
        strata = []
        for platform_index, platform in enumerate(("meta", "google"), start=1):
            platform_hypothesis = deepcopy(hypothesis)
            platform_hypothesis["compatibility_key_sha256"] = (
                "sha256:" + str(platform_index) * 64
            )
            point = 0.04 if platform == "meta" else 0.01
            blocks = {
                (f"experiment-{number}", f"{platform}-campaign-{number}"):
                    [point] * 6
                for number in (1, 2)
            }
            strata.append(
                (
                    "eligible" if platform == "meta" else "no_miss",
                    platform_hypothesis,
                    {"estimand_id": f"{platform}-estimand"},
                    {
                        "experiment_blocks": blocks,
                        "experiment_points": {
                            identity: point for identity in blocks
                        },
                        "evidence_rows": [
                            {
                                "entry_id": f"{platform}-entry",
                                "entry_sha256":
                                    "sha256:" + str(platform_index) * 64,
                            }
                        ],
                        "combined": {},
                    },
                )
            )
        reconciled = _reconcile_hypothesis_strata(
            strata,
            manifest=manifest,
        )
        self.assertEqual(1, len(reconciled))
        self.assertEqual("eligible", reconciled[0][0])
        self.assertEqual(4, len(reconciled[0][3]["experiment_points"]))
        self.assertEqual(
            ["google-entry", "meta-entry"],
            sorted(
                row["entry_id"]
                for row in reconciled[0][3]["evidence_rows"]
            ),
        )
        self.assertEqual(
            ["eligible", "no_miss"],
            sorted(row["status"] for row in reconciled[0][3]["strata"]),
        )

    def test_subthreshold_mixed_signs_produce_no_change(self):
        inputs = diagnosis_inputs_fixture(
            evidence_variant="subthreshold-mixed"
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("no_repeatable_miss", result["decision"])
        points = [
            row["point_estimate"] for row in result["analysis"]["experiments"]
        ]
        self.assertTrue(any(point > 0.0 for point in points))
        self.assertTrue(any(point < 0.0 for point in points))
        self.assertTrue(
            all(
                abs(point)
                < result["analysis"]["combined"]["minimum_practical_effect"]
                for point in points
            )
        )

    def test_same_hypothesis_full_combined_gate_can_still_no_change(self):
        manifest = diagnosis_inputs_fixture()["study_manifest"]
        strata = []
        for index, (label, point, status) in enumerate(
            (
                ("strong", 0.03, "eligible"),
                ("weak", 0.0, "no_miss"),
            ),
            start=1,
        ):
            blocks = {
                (f"{label}-experiment-{number}", f"{label}-campaign-{number}"):
                    [point] * 6
                for number in (1, 2)
            }
            strata.append(
                (
                    status,
                    {
                        "hypothesis_id": "quantified-payback-proof-need",
                        "compatibility_key_sha256":
                            "sha256:" + str(index) * 64,
                    },
                    {"estimand_id": f"{label}-estimand"},
                    {
                        "experiment_blocks": blocks,
                        "experiment_points": {
                            identity: point for identity in blocks
                        },
                        "evidence_rows": [
                            {
                                "entry_id": f"{label}-entry",
                                "entry_sha256":
                                    "sha256:" + str(index) * 64,
                            }
                        ],
                        "combined": {},
                    },
                )
            )
        reconciled = _reconcile_hypothesis_strata(
            strata,
            manifest=manifest,
        )
        self.assertEqual("no_miss", reconciled[0][0])
        self.assertAlmostEqual(
            0.015,
            reconciled[0][3]["combined"]["point_estimate"],
        )
        self.assertEqual(4, len(reconciled[0][3]["experiment_points"]))

    def test_subthreshold_all_opposite_signs_produce_no_change(self):
        inputs = diagnosis_inputs_fixture(
            evidence_variant="subthreshold-negative"
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("no_repeatable_miss", result["decision"])
        points = [
            row["point_estimate"] for row in result["analysis"]["experiments"]
        ]
        self.assertTrue(all(point < 0.0 for point in points))
        self.assertTrue(
            all(
                abs(point)
                < result["analysis"]["combined"]["minimum_practical_effect"]
                for point in points
            )
        )

    def test_all_alternative_causes_must_be_cleared_and_authenticated(self):
        for status in ("unknown", "not_cleared"):
            with self.subTest(status=status):
                inputs = diagnosis_inputs_fixture()
                inputs["alternative_causes"]["delivery"]["status"] = status
                result = diagnose_persona_behavior(**inputs)
                self.assertEqual(
                    "alternative_cause_not_cleared", result["decision"]
                )
        inputs = diagnosis_inputs_fixture()
        inputs["alternative_causes"]["delivery"]["evidence_sha256"] = (
            "sha256:" + "f" * 64
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("invalid_evidence", result["decision"])

    def test_stale_or_mismatched_receipt_is_invalid(self):
        inputs = diagnosis_inputs_fixture()
        inputs["evidence_head_receipt"]["projection_sha256"] = (
            "sha256:" + "f" * 64
        )
        inputs["evidence_head_receipt"] = rehash(
            inputs["evidence_head_receipt"], "receipt_sha256"
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("invalid_evidence", result["decision"])

    def test_projection_replays_event_to_entry_bindings(self):
        snapshot = diagnosis_inputs_fixture()["evidence_library_snapshot"]
        snapshot["entries"][0]["ingested_at"] = "2026-07-02T02:00:00Z"
        snapshot["entries"][0] = rehash(
            snapshot["entries"][0], "entry_sha256"
        )
        snapshot = rehash(snapshot, "library_sha256")
        with self.assertRaisesRegex(ContractError, "event"):
            validate_evidence_library(snapshot)

    def test_post_outcome_registry_is_invalid(self):
        inputs = diagnosis_inputs_fixture()
        registry = inputs["attribute_registry"]
        registry["registered_at"] = registry["outcome_access_boundary"][
            "earliest_outcome_accessed_at"
        ]
        registry = rehash(registry, "registry_sha256")
        inputs["attribute_registry"] = registry
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("invalid_evidence", result["decision"])

    def test_unmanifested_hypotheses_or_target_personas_are_invalid(self):
        for second_persona in (False, True):
            with self.subTest(second_persona=second_persona):
                inputs = diagnosis_inputs_fixture(
                    second_hypothesis=True,
                    second_persona=second_persona,
                )
                result = diagnose_persona_behavior(**inputs)
                self.assertEqual("invalid_evidence", result["decision"])

    def test_design_cannot_self_authorize_by_resealing_its_manifest(self):
        inputs = diagnosis_inputs_fixture()
        design = inputs["experiment_designs"][0]
        design["analytical_cells"] = design["analytical_cells"][:8]
        design = rehash(design, "design_sha256")
        inputs["experiment_designs"] = [design]
        scenario_manifest = inputs["scenario_manifests"][0]
        design_bytes = canonical_json_bytes(design)
        binding = next(
            row
            for row in scenario_manifest["public_file_bindings"]
            if row["path"] == "experiment-design.json"
        )
        import hashlib
        binding["byte_count"] = len(design_bytes)
        binding["raw_bytes_sha256"] = (
            "sha256:" + hashlib.sha256(design_bytes).hexdigest()
        )
        inputs["scenario_manifests"] = [
            rehash(scenario_manifest, "manifest_sha256")
        ]
        self.assertEqual(
            "invalid_evidence",
            diagnose_persona_behavior(**inputs)["decision"],
        )

    def test_design_semantics_and_informative_attribute_are_exact(self):
        for mutation in ("attribution", "informative_attribute"):
            with self.subTest(mutation=mutation):
                inputs = diagnosis_inputs_fixture()
                design = inputs["experiment_designs"][0]
                if mutation == "attribution":
                    design["analytical_cells"][0]["attribution"]["model"] = (
                        "last-touch"
                    )
                else:
                    design["behavioral_hypothesis"][
                        "informative_attribute_id"
                    ] = "dominant-background-color"
                inputs["experiment_designs"][0] = rehash(
                    design, "design_sha256"
                )
                self.assertEqual(
                    "invalid_evidence",
                    diagnose_persona_behavior(**inputs)["decision"],
                )

    def test_structural_target_field_is_invalid(self):
        inputs = diagnosis_inputs_fixture()
        inputs["experiment_designs"][0]["behavioral_hypothesis"][
            "target_field"
        ] = "planned_weight"
        inputs["experiment_designs"][0] = rehash(
            inputs["experiment_designs"][0], "design_sha256"
        )
        result = diagnose_persona_behavior(**inputs)
        self.assertEqual("invalid_evidence", result["decision"])

    def test_order_is_deterministic_and_inputs_are_not_mutated(self):
        inputs = diagnosis_inputs_fixture()
        before = canonical_json_bytes(inputs)
        first = diagnose_persona_behavior(**inputs)
        self.assertEqual(before, canonical_json_bytes(inputs))
        reversed_inputs = deepcopy(inputs)
        reversed_inputs["experiment_designs"].reverse()
        # The authenticated projection has a canonical entry order, so only
        # independent top-level design order is caller-reorderable.
        second = diagnose_persona_behavior(**reversed_inputs)
        self.assertEqual(
            first["diagnosis_sha256"], second["diagnosis_sha256"]
        )

    def test_panel_digest_is_unverified_context_not_evidence_compatibility(self):
        inputs = diagnosis_inputs_fixture()
        first = diagnose_persona_behavior(**inputs)
        changed = deepcopy(inputs)
        changed["base_panel_binding"]["panel_sha256"] = "sha256:" + "9" * 64
        second = diagnose_persona_behavior(**changed)
        self.assertEqual(first["decision"], second["decision"])
        self.assertEqual(first["analysis"], second["analysis"])
        self.assertNotEqual(
            first["diagnosis_sha256"], second["diagnosis_sha256"]
        )

    def test_method_and_stopping_overrides_fail_closed(self):
        mutations = [
            ("diagnosis_method", "bootstrap_repetitions", 499),
            ("diagnosis_method", "block_weighting", "impression"),
            ("diagnosis_method", "minimum_practical_effect", 0.0),
            ("stopping_rule", "rule", "early"),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                inputs = diagnosis_inputs_fixture()
                inputs["study_manifest"][section][field] = value
                inputs["study_manifest"] = rehash(
                    inputs["study_manifest"], "manifest_sha256"
                )
                try:
                    result = diagnose_persona_behavior(**inputs)
                except ContractError:
                    continue
                self.assertEqual("invalid_evidence", result["decision"])

    def test_mcse_requires_at_least_two_batches(self):
        manifest = diagnosis_inputs_fixture()["study_manifest"]
        manifest["monte_carlo_error_targets"]["batch_count"] = 1
        manifest = rehash(manifest, "manifest_sha256")
        with self.assertRaisesRegex(ContractError, "integer >= 2"):
            validate_study_manifest(manifest)

    def test_valid_diagnosis_seals_one_non_executable_update_intent(self):
        inputs = proposal_inputs_fixture()
        before = canonical_json_bytes(inputs)
        proposal = build_experimental_proposal(**inputs)
        self.assertEqual(before, canonical_json_bytes(inputs))
        self.assertEqual("profile_snapshot_update", proposal["proposal_type"])
        self.assertEqual(["proof_needs"], proposal["operation"]["changed_fields"])
        self.assertEqual(
            ["Quantified payback and implementation-risk evidence"],
            proposal["operation"]["proposed_after"]["proof_needs"],
        )
        forbidden = {
            "before",
            "target_persona_snapshot_sha256",
            "authoring_projection",
            "candidate",
        }
        self.assertTrue(forbidden.isdisjoint(proposal["operation"]))
        self.assertFalse(proposal["production_executable"])
        self.assertFalse(proposal["activation_permitted"])
        self.assertFalse(proposal["active_panel_mutation_permitted"])

    def test_no_change_diagnosis_seals_no_change_without_operation(self):
        for inputs in (
            proposal_inputs_fixture(scenario_id="null-effect"),
            proposal_inputs_fixture(evidence_variant="subthreshold-mixed"),
            proposal_inputs_fixture(evidence_variant="subthreshold-negative"),
        ):
            with self.subTest(diagnosis=inputs["diagnosis"]["diagnosis_id"]):
                proposal = build_experimental_proposal(**inputs)
                self.assertEqual("no_change", proposal["proposal_type"])
                self.assertIsNone(proposal["operation"])

    def test_abstention_cannot_build_a_proposal(self):
        inputs = proposal_inputs_fixture(contradictory=True)
        with self.assertRaises(ProposalNotPermitted):
            build_experimental_proposal(**inputs)

    def test_proposed_value_must_come_from_frozen_registry(self):
        inputs = proposal_inputs_fixture()
        inputs["diagnosis"]["selected_hypothesis"]["proposed_value"] = ["invented"]
        inputs["diagnosis"] = rehash(inputs["diagnosis"], "diagnosis_sha256")
        with self.assertRaises(ContractError):
            build_experimental_proposal(**inputs)

    def test_proposal_recomputes_the_exact_diagnosis(self):
        inputs = proposal_inputs_fixture()
        inputs["diagnosis"]["analysis"]["experiments"][0][
            "point_estimate"
        ] = 0.99
        inputs["diagnosis"] = rehash(
            inputs["diagnosis"], "diagnosis_sha256"
        )
        with self.assertRaisesRegex(ContractError, "recomputed"):
            build_experimental_proposal(**inputs)

    def test_malformed_design_cli_exits_two(self):
        inputs = diagnosis_inputs_fixture()
        del inputs["experiment_designs"][0]["design_sha256"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "diagnosis.json"
            input_path.write_bytes(canonical_json_bytes(inputs))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "diagnose-experimental-persona-behavior.py"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(2, completed.returncode, completed.stderr)

    def test_proposal_cli_abstains_without_creating_output(self):
        inputs = proposal_inputs_fixture(contradictory=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "proposal.json"
            input_path.write_bytes(canonical_json_bytes(inputs))
            command = [
                sys.executable,
                str(SCRIPTS / "propose-experimental-persona-behavior-update.py"),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(4, completed.returncode, completed.stderr)
            self.assertFalse(output_path.exists())

    def test_diagnosis_and_proposal_clis_publish_new_canonical_outputs(self):
        diagnosis_inputs = diagnosis_inputs_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            diagnosis_input = root / "diagnosis-input.json"
            diagnosis_output = root / "diagnosis.json"
            diagnosis_input.write_bytes(canonical_json_bytes(diagnosis_inputs))
            diagnosed = subprocess.run(
                [
                    sys.executable,
                    str(
                        SCRIPTS
                        / "diagnose-experimental-persona-behavior.py"
                    ),
                    "--input",
                    str(diagnosis_input),
                    "--output",
                    str(diagnosis_output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, diagnosed.returncode, diagnosed.stderr)
            diagnosis = json.loads(diagnosis_output.read_text())
            self.assertEqual(
                "repeatable_behavioral_miss", diagnosis["decision"]
            )
            proposal_inputs = {
                "base_panel_binding": diagnosis_inputs["base_panel_binding"],
                "study_manifest": diagnosis_inputs["study_manifest"],
                "scenario_manifests": diagnosis_inputs["scenario_manifests"],
                "experiment_designs": diagnosis_inputs["experiment_designs"],
                "diagnosis": diagnosis,
                "attribute_registry": diagnosis_inputs["attribute_registry"],
                "evidence_library_snapshot": diagnosis_inputs[
                    "evidence_library_snapshot"
                ],
                "evidence_head_receipt": diagnosis_inputs[
                    "evidence_head_receipt"
                ],
                "alternative_causes": diagnosis_inputs[
                    "alternative_causes"
                ],
                "proposal_id": "proposal-cli-round-trip",
                "proposed_at": "2026-07-20T01:00:00Z",
            }
            proposal_input = root / "proposal-input.json"
            proposal_output = root / "proposal.json"
            proposal_input.write_bytes(canonical_json_bytes(proposal_inputs))
            proposed = subprocess.run(
                [
                    sys.executable,
                    str(
                        SCRIPTS
                        / "propose-experimental-persona-behavior-update.py"
                    ),
                    "--input",
                    str(proposal_input),
                    "--output",
                    str(proposal_output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proposed.returncode, proposed.stderr)
            proposal = json.loads(proposal_output.read_text())
            self.assertEqual(
                "profile_snapshot_update", proposal["proposal_type"]
            )

    def test_proposal_cli_refuses_existing_output(self):
        inputs = proposal_inputs_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "proposal.json"
            input_path.write_bytes(canonical_json_bytes(inputs))
            output_path.write_text("keep", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        SCRIPTS
                        / "propose-experimental-persona-behavior-update.py"
                    ),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, completed.returncode, completed.stderr)
            self.assertEqual("keep", output_path.read_text())


if __name__ == "__main__":
    unittest.main()
