from pathlib import Path
from copy import deepcopy
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from audience_lab.contracts import (  # noqa: E402
    ValidityStatus,
    load_json,
    resolve_validity,
    validate_base_response,
    validate_manifest,
)


class ContractTests(unittest.TestCase):
    def test_public_base_response_accepts_only_explicit_nonhuman_independence(self):
        response = json.loads(
            (ROOT / "conformance/fixtures/screening-responses-valid.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertFalse(
            any(
                "human_sample_independence" in error
                for error in validate_base_response(response)
            )
        )
        response["human_sample_independence"] = True
        self.assertIn(
            "human_sample_independence must be false",
            validate_base_response(response),
        )

    def test_valid_manifest_has_explicit_external_validity(self):
        payload = load_json(ROOT / "conformance/fixtures/manifest-valid.json")
        self.assertEqual([], validate_manifest(payload))
        self.assertEqual("not_evaluated", payload["external_validity"]["human_alignment_validation"])

    def test_v2_audience_package_binding_is_exact_and_hash_bound(self):
        manifest = load_json(ROOT / "conformance/fixtures/manifest-valid.json")
        manifest["audience_package"] = {
            "panel_id": "operations-leaders", "panel_version": "1.0.0",
            "panel_sha256": "a" * 64, "panel_byte_count": 100,
            "brief_id": "operations-leaders-brief", "brief_sha256": "b" * 64,
            "brief_byte_count": 200, "package_manifest_sha256": "c" * 64,
            "package_manifest_byte_count": 300, "package_zip_sha256": "d" * 64,
            "package_zip_byte_count": 400, "resolved_snapshot_path": "audience/snapshot",
        }
        manifest["audience_lock"] = {
            "persona_research_brief_id": "operations-leaders-brief",
            "panel_id": "operations-leaders", "panel_version": "1.0.0",
            "segment_weights": {"operations-leaders": 1.0},
            "segment_names": {"operations-leaders": "Operations leaders"},
            "archetype_names": {"evidence-led": "Evidence-led"},
            "segment_weight_provenance": [{
                "segment_id": "operations-leaders", "source": "saved_audience_panel",
                "weighting_rule": "planning_allocation",
            }],
            "unique_archetypes": 1, "unique_grounded_context_profiles": 1,
            "attribute_provenance": [{
                "attribute": "buying_stage", "status": "observed",
                "source_evidence": ["evidence-1"], "weighting_rule": "planning_allocation",
            }],
        }
        self.assertEqual([], validate_manifest(manifest))
        for mutate in (
            lambda value: value["audience_package"].update(extra=True),
            lambda value: value["audience_package"].update(resolved_snapshot_path="/tmp/snapshot"),
            lambda value: value["audience_package"].update(panel_sha256="sha256:" + "a" * 64),
            lambda value: value["audience_lock"].update(panel_version="2.0.0"),
            lambda value: value["audience_lock"].update(extra=True),
        ):
            changed = deepcopy(manifest)
            mutate(changed)
            self.assertTrue(validate_manifest(changed))

        stripped = deepcopy(manifest)
        stripped.pop("audience_package")
        self.assertTrue(any(
            "audience_package" in error or "read-only" in error
            for error in validate_manifest(stripped)
        ))

    def test_disconnected_model_is_invalid_even_with_regularization(self):
        status = resolve_validity({"collection_open": False, "connected": False, "identified": True})
        self.assertEqual(ValidityStatus.INVALID, status)

    def test_manifest_fixtures_match_the_full_design_schema(self):
        schema_fields = {
            "manifest": {
                "study_id",
                "study_version",
                "creative_format",
                "method",
                "requested_shortlist_size",
                "maximum_synthetic_panelists",
                "synthetic_replicate_capacity",
                "audience_lock",
                "assignment",
                "model",
                "runtime",
                "outputs",
                "external_validity",
                "validity_status",
                "validity_reasons",
            },
            "synthetic_replicate_capacity": {
                "screening_planned",
                "boundary_reserved",
                "boundary_jobs_per_wave",
                "boundary_waves_max",
                "finalist_reserved",
                "ceiling_satisfied",
            },
            "audience_lock": {
                "persona_research_brief_id",
                "panel_id",
                "panel_version",
                "segment_weights",
                "segment_weight_provenance",
                "first_party_data_notes_id_or_hash",
                "unique_archetypes",
                "unique_grounded_context_profiles",
                "attribute_provenance",
            },
            "assignment": {
                "block_size",
                "randomization_seed",
                "instantiation_seed",
                "assignment_version",
                "planned_participations_per_creative",
                "usable_participations_per_creative",
            },
            "model": {
                "maxdiff_version",
                "penalty_type",
                "penalty_lambda",
                "optimizer_tolerance",
                "bootstrap_count",
                "successful_bootstrap_rate",
                "clear_finalist_threshold",
                "clear_non_finalist_threshold",
                "pairwise_model",
                "pairwise_tie_parameter",
                "pairwise_penalty_lambda",
                "pairwise_optimizer_tolerance",
            },
            "runtime": {
                "orchestration_mode",
                "provider",
                "model_revision",
                "decoding_parameters",
                "prompt_contract_version",
                "rendered_prompt_hashes",
                "code_commit",
                "worker_context_isolation",
                "retry_limit_per_return",
            },
            "outputs": {
                "creative_asset_hashes",
                "raw_provider_returns",
                "rejected_attempts",
            },
            "external_validity": {
                "human_alignment_validation",
                "field_performance_calibration",
            },
        }
        nested_sections = tuple(key for key in schema_fields if key != "manifest")

        for fixture_name in ("manifest-valid.json", "manifest-invalid-disconnected.json"):
            with self.subTest(fixture_name=fixture_name):
                payload = load_json(ROOT / "conformance/fixtures" / fixture_name)
                self.assertSetEqual(schema_fields["manifest"], set(payload))
                for section in nested_sections:
                    self.assertSetEqual(schema_fields[section], set(payload[section]))

    def test_manifest_rejects_multiple_creative_formats(self):
        payload = load_json(ROOT / "conformance/fixtures/manifest-valid.json")
        payload["creative_format"] = ["static_image", "carousel"]

        self.assertIn("creative_format must name exactly one supported format", validate_manifest(payload))

    def test_manifest_rejects_libraries_larger_than_100_creatives(self):
        payload = load_json(ROOT / "conformance/fixtures/manifest-valid.json")
        payload["outputs"]["creative_asset_hashes"] = {
            f"creative-{index}": f"sha256:{index:064x}" for index in range(101)
        }

        self.assertIn(
            "outputs.creative_asset_hashes must contain at most 100 creatives",
            validate_manifest(payload),
        )

    def test_manifest_rejects_unknown_validity_status(self):
        payload = load_json(ROOT / "conformance/fixtures/manifest-valid.json")
        payload["validity_status"] = "pending"

        self.assertIn("validity_status is invalid", validate_manifest(payload))
