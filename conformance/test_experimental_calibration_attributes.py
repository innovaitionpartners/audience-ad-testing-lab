"""Pre-outcome creative-attribute registry conformance tests."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.experimental_calibration.attributes import (  # noqa: E402
    build_creative_attribute_registry,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    validate_creative_attribute_registry,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    creative_attribute_inputs,
    digest,
    rehash,
)


class ExperimentalCalibrationAttributeTests(unittest.TestCase):
    def test_behavioral_hypothesis_is_frozen_before_outcome_access(self):
        registry = build_creative_attribute_registry(**creative_attribute_inputs())
        hypothesis = registry["attribute_definitions"][1]["behavioral_hypothesis"]
        self.assertEqual("proof_needs", hypothesis["target_persona_field"])
        self.assertEqual(
            ["Quantified payback and implementation-risk evidence"],
            hypothesis["proposed_value"],
        )

    def test_post_outcome_attribute_registration_fails(self):
        with self.assertRaisesRegex(ContractError, "before outcome access"):
            build_creative_attribute_registry(
                **creative_attribute_inputs(
                    registered_at="2026-07-03T00:00:00Z",
                    earliest_outcome_accessed_at="2026-07-02T00:00:00Z",
                )
            )

    def test_exact_outcome_boundary_fails(self):
        with self.assertRaisesRegex(ContractError, "before outcome access"):
            build_creative_attribute_registry(
                **creative_attribute_inputs(
                    registered_at="2026-07-02T00:00:00Z",
                    earliest_outcome_accessed_at="2026-07-02T00:00:00Z",
                )
            )

    def test_registry_is_canonical_and_does_not_mutate_inputs(self):
        inputs = creative_attribute_inputs()
        original = deepcopy(inputs)
        registry = build_creative_attribute_registry(**inputs)
        self.assertEqual(original, inputs)
        self.assertEqual(
            sorted(item["attribute_id"] for item in registry["attribute_definitions"]),
            [item["attribute_id"] for item in registry["attribute_definitions"]],
        )
        self.assertEqual(registry, validate_creative_attribute_registry(registry))

    def test_objective_method_version_is_required(self):
        inputs = creative_attribute_inputs()
        del inputs["annotation_methods"][0]["method_version"]
        with self.assertRaisesRegex(ContractError, "missing fields"):
            build_creative_attribute_registry(**inputs)

    def test_interpretive_review_evidence_is_required(self):
        for key, replacement, expected in (
            ("annotator", "", "non-empty"),
            ("confidence", 1.5, "between zero and one"),
            ("ambiguity", "", "non-empty"),
        ):
            with self.subTest(key=key):
                inputs = creative_attribute_inputs()
                row = next(
                    item
                    for item in inputs["creative_attributes"]
                    if item["attribute_id"] == "quantified-payback-proof"
                )
                row[key] = replacement
                with self.assertRaisesRegex(ContractError, expected):
                    build_creative_attribute_registry(**inputs)

    def test_hypothesis_targets_exactly_one_allowed_field(self):
        inputs = creative_attribute_inputs()
        hypothesis = inputs["attribute_definitions"][1]["behavioral_hypothesis"]
        hypothesis["target_persona_field"] = "segment_weight"
        with self.assertRaisesRegex(ContractError, "allowed persona"):
            build_creative_attribute_registry(**inputs)

    def test_hypothesis_value_and_abstention_must_be_nonempty(self):
        for key, replacement, expected in (
            ("proposed_value", [], "must not be empty"),
            ("abstention_conditions", [], "must not be empty"),
        ):
            with self.subTest(key=key):
                inputs = creative_attribute_inputs()
                inputs["attribute_definitions"][1]["behavioral_hypothesis"][key] = replacement
                with self.assertRaisesRegex(ContractError, expected):
                    build_creative_attribute_registry(**inputs)

    def test_duplicate_creative_attribute_pair_fails(self):
        inputs = creative_attribute_inputs()
        inputs["creative_attributes"].append(deepcopy(inputs["creative_attributes"][0]))
        with self.assertRaisesRegex(ContractError, "duplicate creative/attribute"):
            build_creative_attribute_registry(**inputs)

    def test_changed_asset_hash_fails(self):
        inputs = creative_attribute_inputs()
        inputs["creative_attributes"][0]["asset_sha256"] = digest("f")
        with self.assertRaisesRegex(ContractError, "asset_sha256 does not match"):
            build_creative_attribute_registry(**inputs)

    def test_resealed_registry_with_unknown_nested_field_fails(self):
        registry = build_creative_attribute_registry(**creative_attribute_inputs())
        registry["annotation_methods"][0]["extension"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_creative_attribute_registry(rehash(registry, "registry_sha256"))

    def test_registration_cli_publishes_once_and_refuses_existing_or_symlink(self):
        cli = (
            ROOT
            / "skills/audience-panel-builder/scripts/register-synthetic-creative-attributes.py"
        )
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            input_path = temp / "inputs.json"
            output_path = temp / "registry.json"
            input_path.write_text(json.dumps(creative_attribute_inputs()))
            command = [
                sys.executable,
                str(cli),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
            first = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr.decode())
            self.assertEqual(
                validate_creative_attribute_registry(
                    json.loads(output_path.read_text())
                ),
                json.loads(output_path.read_text()),
            )
            before = output_path.read_bytes()
            existing = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(3, existing.returncode)
            self.assertEqual(before, output_path.read_bytes())
            symlink = temp / "registry-link.json"
            symlink.symlink_to(output_path)
            aliased = subprocess.run(
                command[:-1] + [str(symlink)],
                capture_output=True,
                check=False,
            )
            self.assertEqual(3, aliased.returncode)
            self.assertEqual(before, output_path.read_bytes())
            target = temp / "target"
            target.mkdir()
            linked_parent = temp / "linked-parent"
            linked_parent.symlink_to(target, target_is_directory=True)
            parent_aliased = subprocess.run(
                command[:-1] + [str(linked_parent / "registry.json")],
                capture_output=True,
                check=False,
            )
            self.assertEqual(3, parent_aliased.returncode)
            self.assertFalse((target / "registry.json").exists())


if __name__ == "__main__":
    unittest.main()
