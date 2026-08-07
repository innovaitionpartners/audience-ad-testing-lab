"""Hidden-oracle evaluation and static sandbox-report coverage."""

from __future__ import annotations

from copy import deepcopy
import html
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.population.experimental_calibration.proposal import (  # noqa: E402
    build_experimental_proposal,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    evaluation_inputs_fixture,
    proposal_inputs_fixture,
    rehash,
)
from experimental_persona_calibration_oracle import (  # noqa: E402
    validate_synthetic_evaluation,
)
from experimental_persona_calibration_oracle.evaluator import (  # noqa: E402
    OracleIsolationFailure,
    SealedHoldoutFailure,
    _classify_result,
    _operation_measures,
    _visible_result_state,
    evaluate_synthetic_study,
)
from experimental_persona_calibration_oracle.reporting import (  # noqa: E402
    UnsafeReportTemplate,
    render_experimental_report,
)


class HiddenOracleEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = evaluation_inputs_fixture()

    def evaluate(self, **overrides: object) -> dict[str, object]:
        inputs = deepcopy(self.inputs)
        inputs.update(overrides)
        return evaluate_synthetic_study(**inputs)

    def scenario(self, evaluation: dict[str, object], scenario_id: str) -> dict[str, object]:
        for family in evaluation["scenario_family_results"]:
            if family["scenario_family_id"] == scenario_id:
                return family["scenario_results"][0]
        self.fail(f"missing scenario {scenario_id}")

    def test_known_miss_candidate_matches_hidden_safe_action(self):
        evaluation = self.evaluate()
        row = self.scenario(evaluation, "known-proof-need-miss")
        self.assertEqual("correct_proposal", row["result"])
        self.assertTrue(row["measures"]["target_persona_correct"])
        self.assertTrue(row["measures"]["changed_fields_correct"])
        self.assertEqual(0, row["measures"]["forbidden_diff_count"])
        self.assertTrue(row["measures"]["candidate_build_correct"])
        self.assertEqual(1.0, row["measures"]["uncertainty_coverage"])
        self.assertNotIn(
            "uncertainty interval did not cover hidden truth",
            " ".join(row["failure_details"]).lower(),
        )

    def test_null_truth_is_no_change_without_false_proposal(self):
        row = self.scenario(self.evaluate(), "null-effect")
        self.assertEqual("correct_no_change", row["result"])
        self.assertFalse(row["measures"]["false_proposal"])
        self.assertFalse(row["measures"]["missed_proposal"])

    def test_non_identifiable_twins_are_graded_as_one_epistemic_family(self):
        evaluation = self.evaluate()
        rows = [
            self.scenario(evaluation, scenario_id)
            for scenario_id in (
                "non-identifiable-twin-a",
                "non-identifiable-twin-b",
            )
        ]
        self.assertTrue(all(row["result"] == "correct_abstention" for row in rows))
        self.assertTrue(all(row["measures"]["correct_abstention"] for row in rows))
        self.assertTrue(
            all(row["epistemic_family_id"] == "non-identifiable-twins" for row in rows)
        )

    def test_every_family_repetition_and_exercise_leaf_remains_visible(self):
        evaluation = self.evaluate()
        exercise = self.inputs["exercise"]
        expected = {
            (
                row["scenario_id"],
                row["repetition"],
                row["exercise_panel_ref"],
            )
            for row in exercise["run_results"]
        }
        actual = {
            (
                scenario["scenario_id"],
                scenario["repetition"],
                leaf["exercise_panel_ref"],
            )
            for family in evaluation["scenario_family_results"]
            for scenario in family["scenario_results"]
            for leaf in scenario["exercise_leaves"]
        }
        self.assertEqual(expected, actual)

    def test_family_rows_cannot_be_collapsed_or_hidden_by_one_score(self):
        evaluation = self.evaluate()
        self.assertNotIn("overall_score", evaluation)
        self.assertEqual(
            {
                row["scenario_id"]
                for row in self.inputs["study_manifest"]["scenario_families"]
            },
            {
                row["scenario_family_id"]
                for row in evaluation["scenario_family_results"]
            },
        )
        self.assertEqual(
            len(evaluation["scenario_family_results"]),
            len(evaluation["measures"]["robustness_by_dgp_family"]),
        )

    def test_projection_derives_all_five_visible_states(self):
        correct = [{"scenario_results": [{"result": "correct_abstention"}]}]
        invalid = [{"scenario_results": [{"result": "false_proposal"}]}]
        no_change = [{"proposal_type": "no_change"}]
        update = [{"proposal_type": "profile_snapshot_update"}]
        cases = (
            ("Unable to determine", correct, [], []),
            ("No change recommended", correct, no_change, []),
            ("Behavioral update proposed", correct, update, []),
            ("Sandbox candidate created", correct, update, [{}]),
            ("Evidence invalid", invalid, update, [{}]),
        )
        for expected, families, proposals, candidates in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    expected,
                    _visible_result_state(
                        family_results=families,
                        proposals=proposals,
                        candidates=candidates,
                    ),
                )

    def test_stochastic_measures_include_repetitions_point_and_mcse(self):
        measures = self.evaluate()["measures"]
        for name in (
            "false_proposal_rate_under_null",
            "correct_abstention_rate",
        ):
            self.assertEqual(
                {"repetitions", "point_estimate", "monte_carlo_standard_error"},
                set(measures[name]),
            )

    def test_contract_fixture_keeps_dependency_complete_replay_open(self):
        evaluation = self.evaluate()
        self.assertFalse(
            evaluation["measures"]["dependency_complete_numerical_replay"]
        )
        self.assertTrue(
            all(
                leaf["numerical_replay"] == "dependency_deferred"
                for family in evaluation["scenario_family_results"]
                for scenario in family["scenario_results"]
                for leaf in scenario["exercise_leaves"]
            )
        )

    def test_wrong_target_field_direction_and_value_are_distinguishable(self):
        proposal = deepcopy(next(
            row
            for row in self.inputs["proposals"]
            if row["proposal_type"] == "profile_snapshot_update"
        ))
        proposal["operation"]["changed_fields"] = ["anxieties"]
        proposal["operation"]["proposed_after"] = {"anxieties": ["wrong"]}
        proposal["expected_effect"]["direction"] = "negative"
        oracle = next(
            row
            for row in self.inputs["oracle_documents"]
            if row["scenario_id"] == "known-proof-need-miss"
        )
        measures = _operation_measures(
            expected=oracle["epistemic_truth"]["expected_operation"],
            proposal=proposal,
        )
        proposal["operation"]["target_persona_id"] = "wrong-persona"
        target_measures = _operation_measures(
            expected=oracle["epistemic_truth"]["expected_operation"],
            proposal=proposal,
        )
        self.assertFalse(target_measures["target_persona_correct"])
        self.assertFalse(measures["changed_fields_correct"])
        self.assertTrue(measures["direction_error"])
        self.assertTrue(measures["value_error"])

    def test_false_missed_and_incorrect_certainty_results_are_distinct(self):
        self.assertEqual(
            "false_proposal",
            _classify_result(
                expected_action="no_change",
                actual_action="profile_snapshot_update",
                exact_operation=False,
            ),
        )
        self.assertEqual(
            "missed_proposal",
            _classify_result(
                expected_action="profile_snapshot_update",
                actual_action="abstain",
                exact_operation=False,
            ),
        )
        self.assertEqual(
            "incorrect_certainty",
            _classify_result(
                expected_action="abstain",
                actual_action="no_change",
                exact_operation=False,
            ),
        )

    def test_missing_exercise_leaf_fails_before_grading(self):
        exercise = deepcopy(self.inputs["exercise"])
        exercise["run_results"].pop()
        exercise = rehash(exercise, "exercise_sha256")
        with self.assertRaisesRegex(ContractError, "matrix|missing"):
            self.evaluate(exercise=exercise)

    def test_oracle_study_or_scenario_mismatch_fails(self):
        oracles = deepcopy(self.inputs["oracle_documents"])
        oracles[0]["study_manifest_binding"]["study_id"] = "other-study"
        oracles[0] = rehash(oracles[0], "oracle_sha256")
        with self.assertRaisesRegex(ContractError, "study"):
            self.evaluate(oracle_documents=oracles)

    def test_hidden_fields_in_engine_visible_inputs_fail(self):
        observations = deepcopy(self.inputs["observations"])
        observations[0]["observations"][0]["hidden_oracle"] = {"effect": 1}
        observations[0]["observations"][0] = rehash(
            observations[0]["observations"][0], "observation_sha256"
        )
        observations[0]["observations_sha256"] = (
            "sha256:"
            + hashlib.sha256(
                canonical_json_bytes(observations[0]["observations"])
            ).hexdigest()
        )
        with self.assertRaisesRegex(
            ContractError,
            "private-stage|unknown fields|hidden-oracle material",
        ):
            self.evaluate(observations=observations)

    def test_phase_receipt_chain_rejects_missing_reordered_and_forged_links(self):
        for transform in (
            lambda rows: rows[:-1],
            lambda rows: [rows[1], rows[0], *rows[2:]],
            lambda rows: [
                *rows[:2],
                {
                    **rows[2],
                    "previous_phase_receipt_sha256": "sha256:" + "0" * 64,
                },
                *rows[3:],
            ],
        ):
            receipts = transform(deepcopy(self.inputs["phase_receipts"]))
            if len(receipts) == 5:
                receipts[2] = rehash(receipts[2], "phase_receipt_sha256")
            with self.assertRaisesRegex(ContractError, "phase receipt|phase chain"):
                self.evaluate(phase_receipts=receipts)

    def test_sealed_scenarios_cannot_appear_before_reveal(self):
        receipts = deepcopy(self.inputs["phase_receipts"])
        receipts[0]["scenario_bindings"].append(
            deepcopy(receipts[3]["scenario_bindings"][0])
        )
        receipts[0] = rehash(receipts[0], "phase_receipt_sha256")
        for index in range(1, len(receipts)):
            receipts[index]["previous_phase_receipt_sha256"] = receipts[index - 1][
                "phase_receipt_sha256"
            ]
            receipts[index] = rehash(receipts[index], "phase_receipt_sha256")
        with self.assertRaises(SealedHoldoutFailure):
            self.evaluate(phase_receipts=receipts)

    def test_sealed_twin_diagnoses_cannot_enter_engine_results(self):
        for scenario_id in (
            "non-identifiable-twin-a",
            "non-identifiable-twin-b",
        ):
            with self.subTest(scenario_id=scenario_id):
                sealed = proposal_inputs_fixture(scenario_id=scenario_id)
                inputs = evaluation_inputs_fixture(
                    diagnoses=[
                        *deepcopy(self.inputs["diagnoses"]),
                        sealed["diagnosis"],
                    ],
                )
                with self.assertRaises(SealedHoldoutFailure):
                    evaluate_synthetic_study(**inputs)

    def test_sealed_twin_proposals_cannot_enter_engine_results(self):
        for scenario_id in (
            "non-identifiable-twin-a",
            "non-identifiable-twin-b",
        ):
            with self.subTest(scenario_id=scenario_id):
                sealed = proposal_inputs_fixture(scenario_id=scenario_id)
                proposal = build_experimental_proposal(**sealed)
                inputs = evaluation_inputs_fixture(
                    diagnoses=[
                        *deepcopy(self.inputs["diagnoses"]),
                        sealed["diagnosis"],
                    ],
                    proposals=[
                        *deepcopy(self.inputs["proposals"]),
                        proposal,
                    ],
                )
                with self.assertRaises(SealedHoldoutFailure):
                    evaluate_synthetic_study(**inputs)

    def test_phase_records_bind_their_actual_scenario_and_partition(self):
        manifest = {
            row["scenario_id"]: row["partition"]
            for row in self.inputs["study_manifest"]["scenario_families"]
        }
        for receipt in self.inputs["phase_receipts"]:
            for record in receipt["record_bindings"]:
                if record["kind"] == "exercise":
                    self.assertIsNone(record["scenario_id"])
                    self.assertIsNone(record["repetition"])
                    self.assertEqual("both", record["partition"])
                    continue
                self.assertEqual(
                    manifest[record["scenario_id"]],
                    record["partition"],
                )
                self.assertEqual(0, record["repetition"])

    def test_candidate_forbidden_diff_is_counted_and_fails_family(self):
        candidates = deepcopy(self.inputs["candidates"])
        candidates[0]["forbidden_diff_check"] = {
            "passed": False,
            "forbidden_paths": ["$.structural_composition"],
        }
        candidates[0] = rehash(candidates[0], "candidate_binding_sha256")
        with self.assertRaisesRegex(ContractError, "forbidden"):
            self.evaluate(candidates=candidates)

    def test_zero_activation_and_mutation_is_derived_not_claimed(self):
        evaluation = self.evaluate()
        self.assertTrue(evaluation["measures"]["zero_activation_mutation"])
        self.assertEqual(
            {
                "package_created": False,
                "resolution_created": False,
                "registration_permitted": False,
                "activation_permitted": False,
                "active_panel_mutation_permitted": False,
            },
            evaluation["production_authority"],
        )

    def test_reversed_input_order_is_deterministic(self):
        first = self.evaluate()
        second = self.evaluate(
            observations=list(reversed(self.inputs["observations"])),
            oracle_documents=list(reversed(self.inputs["oracle_documents"])),
            diagnoses=list(reversed(self.inputs["diagnoses"])),
            proposals=list(reversed(self.inputs["proposals"])),
            candidates=list(reversed(self.inputs["candidates"])),
        )
        for value in (first, second):
            value["evaluation_sha256"] = None
        self.assertEqual(first, second)

    def test_validation_rejects_a_resealed_derived_measure(self):
        evaluation = self.evaluate()
        evaluation["measures"]["failure_count"] += 1
        with self.assertRaisesRegex(ContractError, "derived|match"):
            validate_synthetic_evaluation(
                rehash(evaluation, "evaluation_sha256")
            )


class ExperimentalReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = evaluation_inputs_fixture()
        cls.evaluation = evaluate_synthetic_study(**deepcopy(cls.inputs))
        cls.template = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "assets"
            / "experimental-persona-behavior-report-template.html"
        ).read_text()

    def render(self, **overrides: object) -> str:
        values = {
            "evaluation": deepcopy(self.evaluation),
            "proposals": deepcopy(self.inputs["proposals"]),
            "candidates": deepcopy(self.inputs["candidates"]),
            "template": self.template,
        }
        values.update(overrides)
        return render_experimental_report(**values)

    def test_report_is_plain_synthetic_only_and_non_activating(self):
        report = self.render()
        for required in (
            "Experimental Persona Behavior Calibration Sandbox",
            "Built and evaluated with fictional synthetic fixtures only.",
            "This output does not validate real-world panel accuracy.",
            "The proposal is not proven to improve real-world outcomes.",
            "This report cannot modify an active panel.",
            "Sandbox candidate created",
            "Cannot be registered or activated",
            "Existing persona behavior",
            "Proposed hypothesis",
            "Exact persona diff",
            "Associations",
            "Alternative explanations",
            "Measurement context",
            "Family results",
            "Limits",
            "Technical bindings",
        ):
            self.assertIn(required, report)

    def test_report_faithfully_renders_every_validated_visible_state(self):
        states = (
            "No change recommended",
            "Unable to determine",
            "Behavioral update proposed",
            "Sandbox candidate created",
            "Evidence invalid",
        )
        for state in states:
            with self.subTest(state=state):
                evaluation = deepcopy(self.evaluation)
                evaluation["report_projection"]["visible_result_state"] = state
                evaluation = rehash(evaluation, "evaluation_sha256")
                self.assertIn(
                    f'<p class="state">{state}</p>',
                    self.render(evaluation=evaluation),
                )

    def test_report_omits_internal_and_prohibited_claim_language(self):
        report = self.render().lower()
        for prohibited in (
            r"\bc1\b",
            r"\bc2\b",
            r"\btier 4\b",
            r"\bcalibrated\b",
            r"\bproven improvement\b",
            r"\bcfos prefer\b",
            r"\bwill improve\b",
        ):
            self.assertIsNone(re.search(prohibited, report))

    def test_report_escapes_values_and_never_uses_innerhtml(self):
        evaluation = deepcopy(self.evaluation)
        evaluation["report_projection"]["existing_persona_behavior"][0][
            "value"
        ] = ["<img src=x onerror=alert(1)>"]
        evaluation = rehash(evaluation, "evaluation_sha256")
        report = self.render(evaluation=evaluation)
        self.assertIn(html.escape("<img src=x onerror=alert(1)>"), report)
        self.assertNotIn("<img src=x", report)
        self.assertNotIn("innerHTML", report)

    def test_unsafe_templates_are_rejected(self):
        for template in (
            "<script>alert(1)</script>{{CONTENT}}",
            '<div onclick="x()">{{CONTENT}}</div>',
            '<img src="https://example.test/x.png">{{CONTENT}}',
            '<table background="https://example.test/x.png">{{CONTENT}}</table>',
            "<style>body{background:url(x)}</style>{{CONTENT}}",
            '<base href="/">{{CONTENT}}',
            '<meta http-equiv="refresh" content="0">{{CONTENT}}',
        ):
            with self.assertRaises(UnsafeReportTemplate):
                self.render(template=template)

    def test_renderer_has_no_oracle_argument_or_oracle_bytes(self):
        report = self.render()
        self.assertNotIn("oracle_sha256", report)
        self.assertNotIn("hidden-oracle", report)


class ExperimentalEvaluationCliTests(unittest.TestCase):
    def test_evaluator_and_renderer_write_exclusively(self):
        inputs = evaluation_inputs_fixture()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            input_root = root / "inputs"
            output_root = root / "outputs"
            input_root.mkdir()
            output_root.mkdir()
            paths: dict[str, Path] = {}
            for key in (
                "study_manifest",
                "observations",
                "exercise",
                "oracle_documents",
                "diagnoses",
                "proposals",
                "candidates",
                "phase_receipts",
            ):
                path = input_root / f"{key}.json"
                path.write_bytes(canonical_json_bytes(inputs[key]))
                paths[key] = path
            evaluation_path = output_root / "evaluation.json"
            command = [
                sys.executable,
                str(SCRIPTS / "evaluate-synthetic-persona-behavior-proposal.py"),
                "--study-manifest",
                str(paths["study_manifest"]),
                "--observations",
                str(paths["observations"]),
                "--exercise",
                str(paths["exercise"]),
                "--oracles",
                str(paths["oracle_documents"]),
                "--diagnoses",
                str(paths["diagnoses"]),
                "--proposals",
                str(paths["proposals"]),
                "--candidates",
                str(paths["candidates"]),
                "--phase-receipts",
                str(paths["phase_receipts"]),
                "--evaluated-at",
                str(inputs["evaluated_at"]),
                "--output",
                str(evaluation_path),
            ]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(3, second.returncode)

            report_path = output_root / "report.html"
            render_command = [
                sys.executable,
                str(SCRIPTS / "render-experimental-persona-behavior-report.py"),
                "--evaluation",
                str(evaluation_path),
                "--proposals",
                str(paths["proposals"]),
                "--candidates",
                str(paths["candidates"]),
                "--template",
                str(
                    ROOT
                    / "skills"
                    / "audience-panel-builder"
                    / "assets"
                    / "experimental-persona-behavior-report-template.html"
                ),
                "--output",
                str(report_path),
            ]
            rendered = subprocess.run(
                render_command, capture_output=True, text=True
            )
            self.assertEqual(0, rendered.returncode, rendered.stderr)
            self.assertIn(
                "fictional synthetic fixtures only",
                report_path.read_text(),
            )

    def test_evaluator_rejects_output_containing_an_input(self):
        inputs = evaluation_inputs_fixture()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            input_root = root / "inputs"
            input_root.mkdir()
            manifest = input_root / "study.json"
            manifest.write_bytes(canonical_json_bytes(inputs["study_manifest"]))
            output = input_root / "evaluation.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        SCRIPTS
                        / "evaluate-synthetic-persona-behavior-proposal.py"
                    ),
                    "--study-manifest",
                    str(manifest),
                    "--observations",
                    str(manifest),
                    "--exercise",
                    str(manifest),
                    "--oracles",
                    str(manifest),
                    "--diagnoses",
                    str(manifest),
                    "--proposals",
                    str(manifest),
                    "--candidates",
                    str(manifest),
                    "--phase-receipts",
                    str(manifest),
                    "--evaluated-at",
                    str(inputs["evaluated_at"]),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, result.returncode)


if __name__ == "__main__":
    unittest.main()
