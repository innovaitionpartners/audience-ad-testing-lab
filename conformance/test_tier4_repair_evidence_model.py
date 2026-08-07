from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    validate_comparison,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    approved_seal,
    comparison_fixture,
    preregistration_fixture,
)


class Tier4AuthenticatedEvidenceRepairTests(unittest.TestCase):
    def test_preregistration_requires_authenticated_power_and_segment_inventory(self) -> None:
        registration = preregistration_fixture()
        registration.pop("study_design_power")
        registration.pop("segment_inventory")
        with self.assertRaisesRegex(ContractError, "missing fields"):
            approved_seal(registration)

    def test_status_only_block_diagnostics_are_not_evidence(self) -> None:
        comparison = comparison_fixture()
        comparison.pop("observations")
        comparison.pop("block_evidence")
        comparison.pop("segment_evidence")
        comparison["block_diagnostics"] = {"status": "complete"}
        comparison["comparison_sha256"] = sha256_json({
            **comparison, "comparison_sha256": None,
        })
        with self.assertRaisesRegex(ContractError, "block_diagnostics"):
            validate_comparison(comparison)

    def test_block_observation_binding_rejects_post_hash_substitution(self) -> None:
        comparison = comparison_fixture()
        comparison["comparison_sha256"] = sha256_json(
            {**comparison, "comparison_sha256": None},
        )
        changed = deepcopy(comparison)
        changed["arm_mappings"][0]["observation_sha256"] = (
            "sha256:" + "f" * 64
        )
        changed["comparison_sha256"] = sha256_json(
            {**changed, "comparison_sha256": None},
        )
        with self.assertRaisesRegex(ContractError, "observation"):
            validate_comparison(changed)


if __name__ == "__main__":
    unittest.main()
