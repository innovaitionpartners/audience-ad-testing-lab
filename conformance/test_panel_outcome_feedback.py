from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_BUILDER_SCRIPTS = (
    ROOT / "skills" / "audience-panel-builder" / "scripts"
)
sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))

from audience_panel_builder.population.feedback import (  # noqa: E402
    bind_outcome_feedback,
    propose_calibration_refresh,
)


def canonical_bytes(value: object) -> bytes:
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


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


class PanelOutcomeFeedbackTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        fixture_root = ROOT / "conformance" / "fixtures" / "audience-research"
        cls.v2_panel = json.loads(
            (fixture_root / "approved-panel.json").read_text(encoding="utf-8")
        )

    def panel(self) -> dict[str, object]:
        panel = deepcopy(self.v2_panel)
        panel["schema_version"] = "saved-audience-panel-v3"
        panel.update({
            "panel_tier": "tier_3",
            "evidence_basis": "first_party_aggregate",
            "brief_id": "operations-leaders-brief",
            "population_frame_result_sha256": "sha256:" + "1" * 64,
            "population_frame_sha256": "sha256:" + "1" * 64,
            "composition_plan_sha256": "sha256:" + "2" * 64,
            "validity_profile_sha256": "sha256:" + "3" * 64,
            "authorized_handoff_sha256": "sha256:" + "4" * 64,
            "audit_binding": {
                "applicability": "release_b1",
                "auditor_run_id": "construction-audit-run-1",
                "audit_sha256": "5" * 64,
                "report_inputs_sha256": "6" * 64,
                "evidence_ledger_sha256": "7" * 64,
                "finding_support_sha256": "8" * 64,
                "synthesis_matrix_sha256": "9" * 64,
                "report_manifest_sha256": "a" * 64,
            },
            "claim_boundary": "Authorized cohort composition only.",
            "package_status": "unpackaged",
        })
        return panel

    def feedback(
        self,
        *,
        feedback_id: str = "feedback-operations-a",
        study_id: str = "marketplace-study",
        variant_id: str = "approved-message-a",
        cohort_id: str = "operations-leaders",
        metric_name: str = "job-to-be-done-completion",
        metric_definition: str = (
            "Share of exposed eligible cohort members completing the declared "
            "job within the measurement window."
        ),
        exposure_unit: str = "eligible-member",
        outcome_unit: str = "completed-job",
        measurement_window: str = "2026-07-01 through 2026-07-21",
        attribution_window: str = "Seven days after exposure",
        numerator: float | None = 14.0,
        denominator: float | None = 100.0,
        value: float | None = 0.14,
        source_id: str = "authorized-outcome-export",
        source_hash: str = "sha256:" + "b" * 64,
        permission_confirmed: bool = True,
        holdout: bool = True,
    ) -> dict[str, object]:
        return {
            "schema_version": "panel-outcome-feedback-v1",
            "feedback_id": feedback_id,
            "panel_id": "operations-leaders",
            "study_id": study_id,
            "variant_id": variant_id,
            "cohort_id": cohort_id,
            "metric": {
                "name": metric_name,
                "definition": metric_definition,
            },
            "metric_direction": "higher_is_better",
            "units": {
                "exposure": exposure_unit,
                "outcome": outcome_unit,
            },
            "windows": {
                "measurement": measurement_window,
                "attribution": attribution_window,
            },
            "aggregate": {
                "numerator": numerator,
                "denominator": denominator,
                "value": value,
            },
            "design": "observational",
            "source": {
                "source_id": source_id,
                "permission_confirmed": permission_confirmed,
            },
            "holdout": holdout,
            "missingness": "No missing aggregate values in the selected cohort.",
            "limitations": [
                "Aggregate observational feedback does not establish causality."
            ],
            "source_sha256": source_hash,
        }

    def bind(
        self,
        feedback_documents: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return bind_outcome_feedback(
            panel=self.panel(),
            feedback_documents=feedback_documents or [self.feedback()],
            binding_id="marketplace-outcomes-2026q3",
            bound_at="2026-07-24T16:00:00Z",
        )

    def test_arbitrary_metric_definitions_units_and_windows_are_preserved(self) -> None:
        feedback = self.feedback(
            metric_name="attributed-basket-value",
            metric_definition=(
                "Gross attributed basket value in currency units for completed "
                "orders after approved exclusions."
            ),
            outcome_unit="currency-unit",
            measurement_window="Fiscal weeks 27 through 30",
            attribution_window="Twenty-eight days after qualified exposure",
            numerator=None,
            denominator=None,
            value=4821.75,
        )

        binding = self.bind([feedback])
        record = binding["feedback_records"][0]

        self.assertEqual(feedback["metric"], record["metric"])
        self.assertEqual(feedback["units"], record["units"])
        self.assertEqual(feedback["windows"], record["windows"])
        self.assertEqual(feedback["aggregate"], record["aggregate"])

    def test_missing_counts_require_an_aggregate_value(self) -> None:
        valid = self.feedback(numerator=None, denominator=None, value=0.14)
        self.assertEqual(
            {"numerator": None, "denominator": None, "value": 0.14},
            self.bind([valid])["feedback_records"][0]["aggregate"],
        )

        for numerator, denominator in ((14.0, None), (None, 100.0)):
            invalid = self.feedback(
                numerator=numerator,
                denominator=denominator,
                value=None,
            )
            with self.subTest(
                numerator=numerator,
                denominator=denominator,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "numerator and denominator are both required",
                ):
                    self.bind([invalid])

    def test_requires_exact_feedback_panel_and_canonical_v3_panel_surface(self) -> None:
        mismatched = self.feedback()
        mismatched["panel_id"] = "another-panel"
        with self.assertRaisesRegex(ValueError, "panel_id must match"):
            self.bind([mismatched])

        invalid_panel = self.panel()
        invalid_panel["unexpected_surface"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            bind_outcome_feedback(
                panel=invalid_panel,
                feedback_documents=[self.feedback()],
                binding_id="marketplace-outcomes-2026q3",
                bound_at="2026-07-24T16:00:00Z",
            )

    def test_binding_rejects_inherited_v2_panel_surface_violations(self) -> None:
        mutations = (
            (
                "empty panel name",
                "invalid_string",
                lambda panel: panel.update(panel_name=""),
            ),
            (
                "empty segments",
                "empty_array",
                lambda panel: panel.update(segments=[]),
            ),
            (
                "bad scope fingerprint",
                "scope_fingerprint_mismatch",
                lambda panel: panel["audience_scope"].update(
                    scope_fingerprint="sha256:" + "0" * 64
                ),
            ),
            (
                "missing privacy confirmation",
                "missing_field",
                lambda panel: panel["governance"].pop(
                    "privacy_confirmation"
                ),
            ),
        )
        for label, error_code, mutate in mutations:
            panel = self.panel()
            mutate(panel)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, error_code):
                    bind_outcome_feedback(
                        panel=panel,
                        feedback_documents=[self.feedback()],
                        binding_id="marketplace-outcomes-2026q3",
                        bound_at="2026-07-24T16:00:00Z",
                    )

    def test_binding_is_scoped_to_one_study(self) -> None:
        second = self.feedback(
            feedback_id="feedback-operations-b",
            study_id="another-study",
            variant_id="approved-message-b",
        )
        with self.assertRaisesRegex(ValueError, "one study_id"):
            self.bind([self.feedback(), second])

    def test_reused_cohort_requires_exact_exposure_and_measurement_identity(self) -> None:
        for override in (
            {"exposure_unit": "eligible-account"},
            {"measurement_window": "2026-07-22 through 2026-07-31"},
        ):
            second = self.feedback(
                feedback_id="feedback-operations-b",
                variant_id="approved-message-b",
                metric_name="qualified-response-rate",
                **override,
            )
            with self.subTest(override=override):
                with self.assertRaisesRegex(
                    ValueError,
                    "incompatible cohort identity",
                ):
                    self.bind([self.feedback(), second])

    def test_distinct_cohorts_and_variants_may_share_one_study_binding(self) -> None:
        operations = self.feedback()
        finance = self.feedback(
            feedback_id="feedback-finance-b",
            variant_id="approved-message-b",
            cohort_id="finance-leaders",
            metric_name="qualified-response-rate",
        )

        binding = self.bind([finance, operations])

        self.assertEqual(
            ["finance-leaders", "operations-leaders"],
            [item["cohort_id"] for item in binding["cohort_identities"]],
        )
        self.assertEqual(
            ["approved-message-a", "approved-message-b"],
            binding["variant_ids"],
        )

    def test_permission_is_required_and_source_identity_cannot_change_hash(self) -> None:
        denied = self.feedback(permission_confirmed=False)
        with self.assertRaisesRegex(ValueError, "permission_confirmed must be true"):
            self.bind([denied])

        conflicting = self.feedback(
            feedback_id="feedback-finance-b",
            cohort_id="finance-leaders",
            source_hash="sha256:" + "c" * 64,
        )
        with self.assertRaisesRegex(ValueError, "conflicting source hashes"):
            self.bind([self.feedback(), conflicting])

    def test_heldout_and_in_sample_designations_remain_distinct(self) -> None:
        heldout = self.feedback()
        in_sample = self.feedback(
            feedback_id="feedback-operations-b",
            variant_id="approved-message-b",
            metric_name="qualified-response-rate",
            holdout=False,
        )

        binding = self.bind([in_sample, heldout])
        sets = {
            record["feedback_id"]: (record["holdout"], record["evaluation_set"])
            for record in binding["feedback_records"]
        }

        self.assertEqual((True, "held_out"), sets["feedback-operations-a"])
        self.assertEqual((False, "in_sample"), sets["feedback-operations-b"])

    def test_binding_is_sorted_deterministic_and_hash_bound(self) -> None:
        first = self.feedback()
        second = self.feedback(
            feedback_id="feedback-finance-b",
            variant_id="approved-message-b",
            cohort_id="finance-leaders",
            metric_name="qualified-response-rate",
        )

        forward = self.bind([first, second])
        reverse = self.bind([second, first])

        self.assertEqual(forward, reverse)
        self.assertEqual(
            ["feedback-finance-b", "feedback-operations-a"],
            [record["feedback_id"] for record in forward["feedback_records"]],
        )
        unhashed = deepcopy(forward)
        unhashed["binding_sha256"] = None
        self.assertEqual(digest(unhashed), forward["binding_sha256"])
        self.assertEqual(digest(self.panel()), forward["panel_binding"]["panel_sha256"])
        self.assertEqual(
            [digest(second), digest(first)],
            [record["feedback_sha256"] for record in forward["feedback_records"]],
        )

    def test_binding_and_proposal_do_not_mutate_panel_frame_or_composition(self) -> None:
        panel = self.panel()
        feedback = self.feedback()
        frame = {
            "schema_version": "audience-population-frame-v1",
            "frame_id": "operations-frame",
            "structural_weight": 0.6,
        }
        composition = {
            "schema_version": "panel-composition-plan-v1",
            "composition_id": "operations-composition",
            "effective_profile_allocation": 0.6,
        }
        before = tuple(
            canonical_bytes(value)
            for value in (panel, feedback, frame, composition)
        )

        binding = bind_outcome_feedback(
            panel=panel,
            feedback_documents=[feedback],
            binding_id="marketplace-outcomes-2026q3",
            bound_at="2026-07-24T16:00:00Z",
        )
        binding_before = canonical_bytes(binding)
        proposal = propose_calibration_refresh(
            panel=panel,
            feedback_binding=binding,
            proposal_id="marketplace-refresh-proposal",
            proposed_at="2026-07-24T17:00:00Z",
        )
        after = tuple(
            canonical_bytes(value)
            for value in (panel, feedback, frame, composition)
        )

        self.assertEqual(before, after)
        self.assertEqual(binding_before, canonical_bytes(binding))
        self.assertEqual("requires_calibration_approval", proposal["status"])
        self.assertFalse(proposal["executable"])
        self.assertEqual([], proposal["diff"]["operations"])
        self.assertIsNone(proposal["diff"]["proposed_panel_sha256"])
        self.assertEqual(
            binding["binding_sha256"],
            proposal["feedback_binding"]["binding_sha256"],
        )
        unhashed = deepcopy(proposal)
        unhashed["proposal_sha256"] = None
        self.assertEqual(digest(unhashed), proposal["proposal_sha256"])

        forbidden = {
            "score",
            "scores",
            "rank",
            "ranks",
            "profile_weight",
            "profile_weights",
            "frame_weight",
            "frame_weights",
        }

        def keys(value: object):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)

        self.assertFalse(forbidden.intersection(keys(proposal)))

    def test_proposal_rejects_tampered_binding_or_different_panel(self) -> None:
        binding = self.bind()
        tampered = deepcopy(binding)
        tampered["feedback_records"][0]["limitations"].append("Changed later.")
        with self.assertRaisesRegex(ValueError, "binding_sha256"):
            propose_calibration_refresh(
                panel=self.panel(),
                feedback_binding=tampered,
                proposal_id="marketplace-refresh-proposal",
                proposed_at="2026-07-24T17:00:00Z",
            )

        another_panel = self.panel()
        another_panel["panel_id"] = "another-panel"
        with self.assertRaisesRegex(ValueError, "does not match"):
            propose_calibration_refresh(
                panel=another_panel,
                feedback_binding=binding,
                proposal_id="marketplace-refresh-proposal",
                proposed_at="2026-07-24T17:00:00Z",
            )

    def test_cli_writes_canonical_output_once_without_clobbering(self) -> None:
        script = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "scripts"
            / "bind-panel-outcome-feedback.py"
        )
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            panel_path = root / "panel.json"
            feedback_path = root / "feedback.json"
            output_path = root / "binding.json"
            panel_path.write_bytes(canonical_bytes(self.panel()))
            feedback_path.write_bytes(canonical_bytes(self.feedback()))
            command = [
                sys.executable,
                str(script),
                "--panel",
                str(panel_path),
                "--feedback",
                str(feedback_path),
                "--binding-id",
                "marketplace-outcomes-2026q3",
                "--bound-at",
                "2026-07-24T16:00:00Z",
                "--output",
                str(output_path),
            ]

            first = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, first.returncode, first.stderr.decode())
            expected = self.bind()
            self.assertEqual(canonical_bytes(expected), output_path.read_bytes())
            original = output_path.read_bytes()

            second = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(3, second.returncode)
            self.assertEqual(original, output_path.read_bytes())
            response = json.loads(second.stdout)
            self.assertEqual("output_collision", response["error"])


if __name__ == "__main__":
    unittest.main()
