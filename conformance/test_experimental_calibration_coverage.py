"""Anti-tautology tests for executable DGP coverage evidence."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "experimental-calibration"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import sha256_json  # noqa: E402
from conformance.experimental_calibration_coverage import (  # noqa: E402
    CoverageEvidenceError,
    execute_coverage_matrix,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    evaluation_fixture,
)


def _rehash(matrix: dict[str, object]) -> dict[str, object]:
    matrix["coverage_matrix_sha256"] = None
    matrix["coverage_matrix_sha256"] = sha256_json(matrix)
    return matrix


class ExperimentalCalibrationCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads((FIXTURES / "coverage-matrix.json").read_bytes())
        cls.evaluation = evaluation_fixture()

    def execute(self, matrix: dict[str, object]) -> list[dict[str, object]]:
        return execute_coverage_matrix(
            matrix,
            fixture_root=FIXTURES,
            evaluation=self.evaluation,
        )

    def test_declared_cells_execute_real_fixture_and_evaluation_witnesses(self):
        evidence = self.execute(self.matrix)
        included = [
            row
            for row in self.matrix["rows"]
            if row["coverage_status"] == "dgp_generalized"
        ]
        self.assertEqual(
            {
                "zero-inflated-value",
            },
            {row["behavior_id"] for row in included},
        )
        excluded = [
            row
            for row in self.matrix["rows"]
            if row["coverage_status"] == "excluded"
        ]
        self.assertEqual(28, len(excluded))
        self.assertTrue(all(row["exclusion_reason"] for row in excluded))
        self.assertEqual(4, len(evidence))
        self.assertEqual(
            {
                "nonlinear_saturation",
                "delayed_censored",
                "heavy_tailed",
                "zero_inflated",
            },
            {row["dgp_class"] for row in evidence},
        )

    def test_wrong_expected_observation_fails(self):
        matrix = deepcopy(self.matrix)
        included = next(
            row
            for row in matrix["rows"]
            if row["coverage_status"] == "dgp_generalized"
        )
        included["cells"][0]["expected_observation"]["actual_action"] = "propose"
        with self.assertRaisesRegex(CoverageEvidenceError, "observed evidence mismatch"):
            self.execute(_rehash(matrix))

    def test_duplicate_dgp_classes_cannot_masquerade_as_three_families(self):
        matrix = deepcopy(self.matrix)
        included = next(
            row
            for row in matrix["rows"]
            if row["coverage_status"] == "dgp_generalized"
        )
        included["cells"][1]["dgp_class"] = included["cells"][0]["dgp_class"]
        with self.assertRaisesRegex(CoverageEvidenceError, "duplicate DGP classes"):
            self.execute(_rehash(matrix))

    def test_unknown_and_unexecuted_probes_fail(self):
        unknown = deepcopy(self.matrix)
        included = next(
            row
            for row in unknown["rows"]
            if row["coverage_status"] == "dgp_generalized"
        )
        included["cells"][0]["probe_id"] = "unknown-probe-v1"
        with self.assertRaisesRegex(CoverageEvidenceError, "unknown probe"):
            self.execute(_rehash(unknown))

        unexecuted = deepcopy(self.matrix)
        unexecuted["declared_probe_ids"].append("fixture-binding-only-v1")
        with self.assertRaisesRegex(CoverageEvidenceError, "not executed"):
            self.execute(_rehash(unexecuted))


if __name__ == "__main__":
    unittest.main()
