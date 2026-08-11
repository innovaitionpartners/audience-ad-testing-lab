"""Deterministic generation and oracle-isolation coverage."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from experimental_persona_calibration_oracle.simulation import (  # noqa: E402
    SYNTHETIC_RESPONSE_ADAPTER_SOURCE,
    build_study_manifest,
    generate_and_publish_synthetic_scenario,
    publish_new_file_no_follow,
)
import experimental_persona_calibration_oracle.simulation as simulation_module  # noqa: E402
from experimental_persona_calibration_oracle.sandbox import (  # noqa: E402
    _Argument,
    _Entrypoint,
    EntrypointUnavailable,
    _ENTRYPOINTS,
    _assert_declared_source_closure,
    _assert_original_input_admissible,
    _discover_closure,
    _load_declared_source_manifest,
    _prepare_arguments,
    _run_provider_command,
    run_engine_in_private_stage,
)
import experimental_persona_calibration_oracle.sandbox as sandbox_module  # noqa: E402
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    SYNTHETIC_SCENARIO_REGISTRY,
    SYNTHETIC_SCENARIO_SEED,
    validate_study_manifest,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    exercise_inputs_fixture,
    valid_candidate_inputs,
)


def _digest_for_test(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _parameter_set(
    scenario_id: str,
    *,
    version: str = "1.0.0",
    baseline_rate: float = 0.08,
) -> dict[str, object]:
    visible_effect_rate = (
        0.04
        if scenario_id == "known-proof-need-miss"
        else 0.025
        if scenario_id.startswith("non-identifiable-twin-")
        else 0.0
    )
    noise_family = {
        "delayed-censored-outcomes": "delayed-censored",
        "heavy-tailed-revenue": "heavy-tailed",
        "nonlinear-saturation": "nonlinear-saturation",
        "zero-inflated-value": "zero-inflated",
    }.get(scenario_id, "binomial")
    document: dict[str, object] = {
        "parameter_set_id": f"{scenario_id}-parameters",
        "parameter_version": version,
        "parameter_values": [
            {"name": "baseline-rate", "value_type": "number", "value": baseline_rate},
            {"name": "blocks", "value_type": "integer", "value": 96},
            {"name": "enabled", "value_type": "boolean", "value": True},
            {"name": "noise-family", "value_type": "string", "value": noise_family},
            {
                "name": "visible-effect-rate",
                "value_type": "number",
                "value": visible_effect_rate,
            },
        ],
        "parameters_sha256": None,
    }
    document["parameters_sha256"] = sha256_json(document)
    return document


def _study_manifest(*, seed_delta: int = 0, parameter_version: str = "1.0.0"):
    scenario_order = [
        "null-effect",
        "known-proof-need-miss",
        "non-identifiable-twin-a",
        "non-identifiable-twin-b",
        *sorted(
            set(SYNTHETIC_SCENARIO_REGISTRY)
            - {
                "null-effect",
                "known-proof-need-miss",
                "non-identifiable-twin-a",
                "non-identifiable-twin-b",
            }
        ),
    ]
    scenario_rows = [
        (
            scenario_id,
            SYNTHETIC_SCENARIO_REGISTRY[scenario_id]["dgp_id"],
            SYNTHETIC_SCENARIO_SEED[scenario_id],
            SYNTHETIC_SCENARIO_REGISTRY[scenario_id]["partition"],
        )
        for scenario_id in scenario_order
    ]
    specs = [
        {
            "scenario_id": scenario_id,
            "dgp_id": dgp_id,
            "dgp_version": "1.0.0",
            "seed": seed + seed_delta,
            "repetitions": 1,
            "parameters": _parameter_set(
                scenario_id,
                version=parameter_version,
            ),
            "partition": partition,
        }
        for scenario_id, dgp_id, seed, partition in scenario_rows
    ]
    source_digest = "sha256:" + hashlib.sha256(
        SYNTHETIC_RESPONSE_ADAPTER_SOURCE.read_bytes()
    ).hexdigest()
    return build_study_manifest(
        study_id="fictional-persona-behavior-study",
        created_at="2026-07-29T00:00:00Z",
        generator_version="1.0.0",
        scenario_specs=specs,
        estimands=[{"estimand_id": "cfo-quantified-payback-rate-contrast"}],
        parameter_grid={"rate": [0.0, 0.025, 0.04]},
        seeds=list(
            dict.fromkeys(
                seed + seed_delta
                for _scenario_id, _dgp_id, seed, _partition in scenario_rows
            )
        ),
        repetitions=1,
        monte_carlo_error_targets={
            "maximum": 0.01,
            "method_version": "deterministic-batch-quantile-mcse-v1",
            "batch_count": 10,
            "batch_partition_policy": "equal_contiguous_replicate_batches",
            "quantile_interpolation": "linear",
            "reported_measures": [
                "bootstrap_mean",
                "interval_lower",
                "interval_upper",
            ],
        },
        diagnosis_method={
            "method_version": "blocked-contrast-bootstrap-v1",
            "contrast_source": "registered_numerator_denominator",
            "block_weighting": "equal",
            "experiment_weighting": "equal",
            "minimum_complete_blocks_per_experiment": 6,
            "minimum_independent_experiments": 2,
            "interval_method": "deterministic_percentile_block_bootstrap",
            "interval_level": 0.95,
            "bootstrap_repetitions": 500,
            "bootstrap_seed": 73021,
            "minimum_practical_effect": 0.02,
            "minimum_practical_effect_rule": (
                "directional_point_estimate_strictly_exceeds_threshold"
            ),
            "missingness_policy": "incomplete_block_ineligible",
            "maturity_policy": "finalized_only",
            "observational_policy": "descriptive_only",
            "early_stopping_permitted": False,
        },
        synthetic_response_adapter={
            "adapter_id": "frozen-synthetic-panelist-response",
            "version": "1.0.0",
            "source_sha256": source_digest,
            "feature_allowlist": [
                "creative_attributes",
                "experiment_design",
                "persona_snapshot",
                "study_manifest",
            ],
            "deterministic_tie_rule": (
                "score-descending-creative-id-ascending"
            ),
            "seed": 73021,
        },
        stopping_rule={"rule": "none"},
        performance_measures=[
            "correct-abstention",
            "false-proposal",
            "target-field-accuracy",
        ],
    )


def _publish(temp: Path, manifest: dict[str, object], scenario_id: str):
    return generate_and_publish_synthetic_scenario(
        manifest=manifest,
        scenario_id=scenario_id,
        public_output_dir=temp / f"{scenario_id}-public",
        oracle_output_dir=temp / f"{scenario_id}-oracle",
    )


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_bytes(root: Path) -> bytes:
    return b"".join(
        relative.as_posix().encode("utf-8")
        + b"\0"
        + (root / relative).read_bytes()
        for relative in sorted(
            path.relative_to(root) for path in root.rglob("*") if path.is_file()
        )
    )


class ExperimentalCalibrationSimulationTests(unittest.TestCase):
    def test_manifest_is_frozen_and_partitions_are_disjoint(self):
        manifest = _study_manifest()
        self.assertEqual("verification_only", manifest["purpose"])
        open_ids = {
            row["scenario_id"]
            for row in manifest["scenario_families"]
            if row["partition"] == "open"
        }
        sealed_ids = {
            row["scenario_id"]
            for row in manifest["scenario_families"]
            if row["partition"] == "sealed"
        }
        self.assertFalse(open_ids & sealed_ids)
        self.assertEqual(
            len(SYNTHETIC_SCENARIO_REGISTRY),
            len(open_ids | sealed_ids),
        )
        for row in manifest["scenario_families"]:
            self.assertTrue(row["dgp_id"])
            self.assertEqual("1.0.0", row["dgp_version"])
            self.assertIsInstance(row["seed"], int)
            self.assertEqual(1, row["repetitions"])
            self.assertTrue(row["parameters"]["parameters_sha256"])
        self.assertEqual(
            {
                "maximum": 0.01,
                "method_version": "deterministic-batch-quantile-mcse-v1",
                "batch_count": 10,
                "batch_partition_policy": "equal_contiguous_replicate_batches",
                "quantile_interpolation": "linear",
                "reported_measures": [
                    "bootstrap_mean",
                    "interval_lower",
                    "interval_upper",
                ],
            },
            manifest["monte_carlo_error_targets"],
        )
        self.assertEqual(
            "directional_point_estimate_strictly_exceeds_threshold",
            manifest["diagnosis_method"]["minimum_practical_effect_rule"],
        )

    def test_manifest_rejects_resealed_registry_partition_and_numerical_drift(self):
        def reseal(document: dict[str, object]) -> dict[str, object]:
            result = deepcopy(document)
            result["manifest_sha256"] = None
            result["manifest_sha256"] = sha256_json(result)
            return result

        mutations = (
            lambda value: value.update({"generator_version": "999.0.0"}),
            lambda value: value["scenario_families"][0].update(
                {"dgp_version": "999.0.0"}
            ),
            lambda value: value["scenario_families"][2].update(
                {"partition": "open"}
            ),
            lambda value: value["scenario_families"][0].update({"seed": 9999}),
            lambda value: value["scenario_families"][0].update(
                {"repetitions": 2}
            ),
            lambda value: value["diagnosis_method"].update(
                {"interval_level": 1.5}
            ),
            lambda value: value["diagnosis_method"].update({"interval_level": 1}),
            lambda value: value["diagnosis_method"].update(
                {"interval_level": True}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                manifest = _study_manifest()
                mutate(manifest)
                with self.assertRaises(ContractError):
                    validate_study_manifest(reseal(manifest))

        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(interval_level=invalid):
                manifest = _study_manifest()
                manifest["diagnosis_method"]["interval_level"] = invalid
                with self.assertRaises(ContractError):
                    validate_study_manifest(manifest)

    def test_manifest_builder_rejects_false_generator_dgp_and_partition_claims(self):
        manifest = _study_manifest()
        families = deepcopy(manifest["scenario_families"])
        common = {
            "study_id": manifest["study_id"],
            "created_at": manifest["created_at"],
            "generator_version": manifest["generator_version"],
            "scenario_specs": families,
            "estimands": manifest["estimands"],
            "parameter_grid": manifest["parameter_grid"],
            "seeds": manifest["seeds"],
            "repetitions": manifest["repetitions"],
            "monte_carlo_error_targets": manifest["monte_carlo_error_targets"],
            "diagnosis_method": manifest["diagnosis_method"],
            "synthetic_response_adapter": manifest["synthetic_response_adapter"],
            "stopping_rule": manifest["stopping_rule"],
            "performance_measures": manifest["performance_measures"],
        }
        for mutate in (
            lambda value: value.update({"generator_version": "2.0.0"}),
            lambda value: value["scenario_specs"][0].update(
                {"dgp_version": "2.0.0"}
            ),
            lambda value: value["scenario_specs"][2].update(
                {"partition": "open"}
            ),
        ):
            with self.subTest(mutation=mutate):
                arguments = deepcopy(common)
                mutate(arguments)
                with self.assertRaises(ContractError):
                    build_study_manifest(**arguments)

    def test_adapter_source_precedes_outcomes_and_is_bound_by_exact_bytes(self):
        manifest = _study_manifest()
        source = SYNTHETIC_RESPONSE_ADAPTER_SOURCE
        self.assertTrue(source.is_file())
        digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            manifest["synthetic_response_adapter"]["source_sha256"],
        )
        self.assertNotEqual(
            digest,
            "sha256:" + hashlib.sha256(source.read_bytes() + b"\n").hexdigest(),
        )

    def test_adapter_source_has_no_outcome_or_external_authority_surface(self):
        tree = ast.parse(
            SYNTHETIC_RESPONSE_ADAPTER_SOURCE.read_text(encoding="utf-8")
        )
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual({"__future__"}, imports)
        identifiers = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertFalse(
            {
                "open",
                "environ",
                "getenv",
                "socket",
                "subprocess",
                "urlopen",
                "outcomes",
                "oracle",
            }
            & identifiers
        )

    def test_same_seed_produces_exact_same_public_and_oracle_bytes(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = _publish(Path(first_raw), manifest, "known-proof-need-miss")
            second = _publish(Path(second_raw), manifest, "known-proof-need-miss")
            self.assertEqual(
                _tree_bytes(Path(first["public_output_dir"])),
                _tree_bytes(Path(second["public_output_dir"])),
            )
            self.assertEqual(
                _tree_bytes(Path(first["oracle_output_dir"])),
                _tree_bytes(Path(second["oracle_output_dir"])),
            )

    def test_non_identifiable_twins_have_same_visible_evidence(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            left = _publish(root / "left", manifest, "non-identifiable-twin-a")
            right = _publish(root / "right", manifest, "non-identifiable-twin-b")
            left_public = _load(
                Path(left["public_output_dir"]) / "canonical-observations.json"
            )
            right_public = _load(
                Path(right["public_output_dir"]) / "canonical-observations.json"
            )
            left_oracle = _load(
                Path(left["oracle_output_dir"]) / "hidden-oracle.json"
            )
            right_oracle = _load(
                Path(right["oracle_output_dir"]) / "hidden-oracle.json"
            )
            self.assertEqual(left_public, right_public)
            self.assertNotEqual(
                left_oracle["physical_truth"]["safe_action_set"],
                right_oracle["physical_truth"]["safe_action_set"],
            )
            self.assertEqual(
                "abstain",
                left_oracle["epistemic_truth"]["expected_engine_action"],
            )
            self.assertEqual(
                left_oracle["epistemic_truth"],
                right_oracle["epistemic_truth"],
            )
            self.assertNotEqual(
                left_oracle["failure_mechanism"],
                right_oracle["failure_mechanism"],
            )

    def test_public_tree_has_exact_shape_and_no_private_truth(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            published = _publish(
                Path(raw), manifest, "known-proof-need-miss"
            )
            public_dir = Path(published["public_output_dir"])
            oracle_dir = Path(published["oracle_output_dir"])
            files = {
                path.relative_to(public_dir).as_posix()
                for path in public_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                {
                    "scenario-manifest.json",
                    "experiment-design.json",
                    "canonical-observations.json",
                    "raw/meta/daily-aggregates.json",
                    "raw/google/daily-aggregates.json",
                    "raw/linkedin/daily-aggregates.json",
                    "raw/tiktok/daily-aggregates.json",
                },
                files,
            )
            public_bytes = b"".join(
                path.read_bytes() for path in sorted(public_dir.rglob("*.json"))
            )
            self.assertNotIn(b"true_behavioral_miss", public_bytes)
            self.assertEqual(
                {"hidden-oracle.json", "oracle-manifest.json"},
                {
                    path.name
                    for path in oracle_dir.iterdir()
                    if path.is_file()
                },
            )

    def test_initial_oracles_encode_expected_safe_actions(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            null = _publish(root / "null", manifest, "null-effect")
            known = _publish(
                root / "known", manifest, "known-proof-need-miss"
            )
            null_truth = _load(
                Path(null["oracle_output_dir"]) / "hidden-oracle.json"
            )
            known_truth = _load(
                Path(known["oracle_output_dir"]) / "hidden-oracle.json"
            )
            self.assertIsNone(
                null_truth["physical_truth"]["true_behavioral_miss"]
            )
            self.assertIsNone(null_truth["physical_truth"]["true_operation"])
            self.assertEqual(
                ["no_change"],
                null_truth["physical_truth"]["safe_action_set"],
            )
            self.assertEqual(
                {
                    "expected_engine_action": "no_change",
                    "expected_operation": None,
                    "identification_status": "no_miss",
                },
                null_truth["epistemic_truth"],
            )
            self.assertEqual(
                ["profile_snapshot_update"],
                known_truth["physical_truth"]["safe_action_set"],
            )
            self.assertEqual(
                {
                    "target_persona_id": "finance-pricing-archetype",
                    "target_field": "proof_needs",
                },
                known_truth["physical_truth"]["true_behavioral_miss"],
            )
            expected_operation = {
                "operation_type": "profile_snapshot_update",
                "target_persona_id": "finance-pricing-archetype",
                "target_field": "proof_needs",
                "expected_value": [
                    "Quantified payback and implementation-risk evidence"
                ],
                "value_direction_rule": "exact_array_equality",
            }
            self.assertEqual(
                expected_operation,
                known_truth["physical_truth"]["true_operation"],
            )
            self.assertEqual(
                {
                    "expected_engine_action": "profile_snapshot_update",
                    "expected_operation": expected_operation,
                    "identification_status": "identified",
                },
                known_truth["epistemic_truth"],
            )

    def test_known_miss_has_six_cfo_blocks_per_experiment_in_every_platform(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            published = _publish(
                Path(raw), manifest, "known-proof-need-miss"
            )
            public = Path(published["public_output_dir"])
            for platform_name in ("meta", "google", "linkedin", "tiktok"):
                document = _load(
                    public / "raw" / platform_name / "daily-aggregates.json"
                )
                blocks_by_experiment: dict[str, set[str]] = {}
                for row in document["rows"]:
                    if row["segment_id"] != "cfo":
                        continue
                    blocks_by_experiment.setdefault(
                        row["experiment_id"], set()
                    ).add(row["block_id"])
                self.assertEqual(2, len(blocks_by_experiment), platform_name)
                self.assertTrue(
                    all(len(blocks) >= 6 for blocks in blocks_by_experiment.values()),
                    platform_name,
                )

    def test_design_and_observations_bind_exact_analytical_identity(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            published = _publish(
                Path(raw), manifest, "known-proof-need-miss"
            )
            public = Path(published["public_output_dir"])
            design = _load(public / "experiment-design.json")
            unhashed = deepcopy(design)
            unhashed["design_sha256"] = None
            self.assertEqual(sha256_json(unhashed), design["design_sha256"])
            self.assertEqual(16, len(design["analytical_cells"]))
            for cell in design["analytical_cells"]:
                self.assertGreaterEqual(
                    len(cell["randomization"]["block_ids"]), 6
                )
                self.assertGreaterEqual(
                    len(cell["randomization"]["batch_ids"]), 3
                )
                self.assertEqual(
                    "finalized-leads",
                    cell["estimand"]["registered_numerator"],
                )
                self.assertEqual(
                    "impressions",
                    cell["estimand"]["registered_denominator"],
                )
                self.assertEqual(
                    "treatment_minus_reference_positive",
                    cell["estimand"]["contrast_direction"],
                )
            self.assertEqual(
                "treatment_minus_reference_positive",
                design["behavioral_hypothesis"]["contrast_direction"],
            )
            observation = _load(
                public / "canonical-observations.json"
            )[0]
            self.assertEqual(
                {
                    "experiment",
                    "campaign",
                    "block",
                    "batch",
                    "arm",
                    "reference_arm",
                },
                set(observation["experiment_binding"]),
            )
            self.assertEqual(
                {"segment", "objective", "placement"},
                set(observation["audience_scope"]),
            )
            self.assertEqual(
                {
                    "metric",
                    "registered_numerator",
                    "registered_denominator",
                    "attribution_click_window",
                    "attribution_view_window",
                    "attribution_engaged_view_window",
                    "attribution_model",
                },
                set(observation["measurement_definition"]),
            )

    def test_raw_platform_files_preserve_named_adapter_traps_and_bind_bytes(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            published = _publish(Path(raw), manifest, "null-effect")
            public = Path(published["public_output_dir"])
            scenario_manifest = _load(public / "scenario-manifest.json")
            bindings = {
                row["path"]: row
                for row in scenario_manifest["public_file_bindings"]
            }
            documents = {
                platform_name: _load(
                    public / "raw" / platform_name / "daily-aggregates.json"
                )
                for platform_name in ("meta", "google", "linkedin", "tiktok")
            }
            for platform_name, document in documents.items():
                relative = f"raw/{platform_name}/daily-aggregates.json"
                raw_bytes = (public / relative).read_bytes()
                self.assertEqual(
                    "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
                    bindings[relative]["raw_bytes_sha256"],
                )
                self.assertEqual(
                    manifest["manifest_sha256"],
                    document["source_binding"]["study_manifest_sha256"],
                )
                self.assertFalse(
                    document["reporting_context"][
                        "breakdown_overlap_permitted"
                    ]
                )
                self.assertEqual(
                    {"missing", "omitted-zero", "suppressed", "zero"},
                    {row["state"] for row in document["state_markers"]},
                )
            self.assertTrue(
                {
                    "actions",
                    "action_values",
                    "action_report_time",
                    "attribution_setting",
                    "outbound_clicks",
                    "reach",
                }
                <= set(documents["meta"]["rows"][0])
            )
            self.assertEqual(
                "non-additive", documents["meta"]["reach_aggregation"]
            )
            self.assertTrue(
                {
                    "cost_local",
                    "cost_micros",
                    "currency_code",
                    "data_status",
                    "interactions",
                    "conversions",
                    "all_conversions",
                    "interaction_date",
                    "conversion_date",
                }
                <= set(documents["google"]["rows"][0])
            )
            self.assertIsInstance(
                documents["google"]["rows"][0]["conversions"], float
            )
            self.assertEqual(
                "modeled_and_observed",
                documents["google"]["rows"][0]["data_status"],
            )
            self.assertNotEqual(
                documents["google"]["rows"][0]["interaction_date"],
                documents["google"]["rows"][0]["conversion_date"],
            )
            self.assertTrue(
                {
                    "chargeable_clicks",
                    "landing_page_clicks",
                    "sends",
                    "total_conversions",
                    "post_click_conversions",
                    "post_view_conversions",
                    "cost_local",
                    "cost_usd",
                    "advertiser_conversion_value",
                    "leads",
                    "job_views",
                    "job_applications",
                    "application_starts",
                    "estimation_status",
                    "reporting_delay_days",
                    "suppression_status",
                }
                <= set(documents["linkedin"]["rows"][0])
            )
            self.assertTrue(
                {
                    "clicks_all",
                    "destination_clicks",
                    "cta_conversions",
                    "vta_conversions",
                    "evta_conversions",
                    "cvr_all_clicks",
                    "cvr_all_clicks_denominator",
                    "cvr_destination_clicks",
                    "cvr_destination_clicks_denominator",
                    "interaction_date",
                    "third_party_event_date",
                    "video_p25",
                    "video_p50",
                    "video_p75",
                    "video_p100",
                }
                <= set(documents["tiktok"]["rows"][0])
            )
            self.assertNotEqual(
                documents["tiktok"]["rows"][0]["interaction_date"],
                documents["tiktok"]["rows"][0]["third_party_event_date"],
            )
            for platform_name, document in documents.items():
                reported_metric = {
                    "google": "conversions",
                    "linkedin": "total_conversions",
                    "meta": "outbound_clicks",
                    "tiktok": "cta_conversions",
                }[platform_name]
                states = {
                    row["metric_reporting_state"]["state"]:
                    row["metric_reporting_state"]
                    for row in document["rows"]
                }
                self.assertEqual(
                    {"missing", "observed", "omitted-zero", "suppressed", "zero"},
                    set(states),
                    platform_name,
                )
                self.assertEqual(0, states["zero"]["value"], platform_name)
                self.assertIsNone(states["missing"]["value"], platform_name)
                self.assertIsNone(states["suppressed"]["value"], platform_name)
                self.assertIsNone(states["omitted-zero"]["value"], platform_name)
                self.assertIsInstance(
                    states["observed"]["value"],
                    (int, float),
                    platform_name,
                )
                self.assertEqual(
                    set(states),
                    {row["row_state"] for row in document["rows"]},
                    platform_name,
                )
                for row in document["rows"]:
                    self.assertEqual(
                        row["metric_reporting_state"]["value"],
                        row[reported_metric],
                        platform_name,
                    )

    def test_seed_and_parameter_version_change_committed_hashes(self):
        base = _study_manifest()
        changed_seed = _study_manifest(seed_delta=1)
        changed_parameter = _study_manifest(parameter_version="1.0.1")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hashes = [
                _publish(root / str(index), manifest, "known-proof-need-miss")[
                    "public_manifest_sha256"
                ]
                for index, manifest in enumerate(
                    [base, changed_seed, changed_parameter]
                )
            ]
        self.assertEqual(3, len(set(hashes)))

    def test_existing_or_aliased_output_fails_without_clobbering(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            existing = root / "existing"
            existing.mkdir()
            sentinel = existing / "sentinel"
            sentinel.write_bytes(b"keep")
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=existing,
                    oracle_output_dir=root / "new-oracle",
                )
            self.assertEqual(b"keep", sentinel.read_bytes())
            self.assertFalse((root / "new-oracle").exists())
            new_public = root / "new-public"
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=new_public,
                    oracle_output_dir=existing,
                )
            self.assertFalse(new_public.exists())
            broken_target = root / "not-created"
            broken_link = root / "broken-link"
            broken_link.symlink_to(broken_target)
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=broken_link,
                    oracle_output_dir=root / "symlink-oracle",
                )
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=root / "same",
                    oracle_output_dir=root / "." / "same",
                )

    def test_publication_rejects_symlinked_ancestors_and_race_substitution(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real_public = root / "real-public"
            real_oracle = root / "real-oracle"
            real_public.mkdir()
            real_oracle.mkdir()
            public_link = root / "public-link"
            oracle_link = root / "oracle-link"
            public_link.symlink_to(real_public, target_is_directory=True)
            oracle_link.symlink_to(real_oracle, target_is_directory=True)
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=public_link / "scenario",
                    oracle_output_dir=root / "safe-oracle",
                )
            with self.assertRaises(ContractError):
                generate_and_publish_synthetic_scenario(
                    manifest=manifest,
                    scenario_id="null-effect",
                    public_output_dir=root / "safe-public",
                    oracle_output_dir=oracle_link / "scenario",
                )
            self.assertEqual([], list(real_public.iterdir()))
            self.assertEqual([], list(real_oracle.iterdir()))

            public_parent = root / "public-parent"
            public_parent.mkdir()
            attacker = root / "attacker"
            attacker.mkdir()
            oracle_parent = root / "oracle-parent"
            oracle_parent.mkdir()
            original = simulation_module._assert_no_symlink_ancestors
            substituted = False

            def substitute(path: Path, label: str) -> None:
                nonlocal substituted
                original(path, label)
                if label == "public output directory" and not substituted:
                    substituted = True
                    public_parent.rename(root / "public-parent-original")
                    public_parent.symlink_to(attacker, target_is_directory=True)

            with patch.object(
                simulation_module,
                "_assert_no_symlink_ancestors",
                side_effect=substitute,
            ):
                with self.assertRaises(ContractError):
                    generate_and_publish_synthetic_scenario(
                        manifest=manifest,
                        scenario_id="null-effect",
                        public_output_dir=public_parent / "scenario",
                        oracle_output_dir=oracle_parent / "scenario",
                    )
            self.assertEqual([], list(attacker.iterdir()))

    def test_publication_rejects_post_check_parent_substitution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "parent"
            displaced = root / "displaced-parent"
            parent.mkdir()
            target = parent / "result.json"
            original = simulation_module._assert_directory_path_identity
            substituted = False

            def substitute_after_check(
                path: Path,
                expected: os.stat_result,
                label: str,
            ) -> None:
                nonlocal substituted
                original(path, expected, label)
                if label == "test publication parent" and not substituted:
                    substituted = True
                    parent.rename(displaced)
                    parent.mkdir()
                    target.write_bytes(b"attacker")

            with patch.object(
                simulation_module,
                "_assert_directory_path_identity",
                side_effect=substitute_after_check,
            ):
                with self.assertRaises(ContractError):
                    publish_new_file_no_follow(
                        target,
                        b"expected",
                        "test publication",
                    )
            self.assertEqual(b"attacker", target.read_bytes())
            self.assertEqual(b"expected", (displaced / target.name).read_bytes())

        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            public = root / "public"
            oracle = root / "oracle"
            displaced = root / "displaced-public"
            original = simulation_module._assert_directory_path_identity
            public_checks = 0

            def substitute_after_final_public_check(
                path: Path,
                expected: os.stat_result,
                label: str,
            ) -> None:
                nonlocal public_checks
                original(path, expected, label)
                if label == "public output directory":
                    public_checks += 1
                    if public_checks == 2:
                        public.rename(displaced)
                        public.mkdir()
                        (public / "attacker.json").write_bytes(b"attacker")

            with patch.object(
                simulation_module,
                "_assert_directory_path_identity",
                side_effect=substitute_after_final_public_check,
            ):
                with self.assertRaises(ContractError):
                    generate_and_publish_synthetic_scenario(
                        manifest=manifest,
                        scenario_id="null-effect",
                        public_output_dir=public,
                        oracle_output_dir=oracle,
                    )
            self.assertEqual(b"attacker", (public / "attacker.json").read_bytes())
            self.assertTrue((displaced / "scenario-manifest.json").is_file())

    def test_original_denied_input_provenance_rejects_aliases_and_containers(self):
        manifest = _study_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oracle = root / "oracle"
            oracle.mkdir()
            hidden = oracle / "hidden.json"
            hidden.write_bytes(canonical_json_bytes(manifest))
            public = root / "public.json"
            public.write_bytes(canonical_json_bytes(manifest))
            symlink = root / "oracle-symlink.json"
            symlink.symlink_to(hidden)
            hardlink = root / "oracle-hardlink.json"
            os.link(hidden, hardlink)
            container = root / "container"
            container.mkdir()
            contained_oracle = container / "denied"
            contained_oracle.mkdir()

            for candidate, denied in (
                (hidden, [oracle]),
                (oracle, [oracle]),
                (symlink, [oracle]),
                (hardlink, [oracle]),
                (container, [contained_oracle]),
            ):
                with self.subTest(candidate=candidate), self.assertRaises(
                    ContractError
                ):
                    _assert_original_input_admissible(candidate, denied)

            checked = _assert_original_input_admissible(public, [oracle])
            try:
                self.assertEqual(public.resolve(strict=True), checked.source_path)
            finally:
                checked.close()

            spec = _Entrypoint(
                cli="future.py",
                required_module="future.py",
                source_manifest="future-source-manifest.json",
                arguments=(
                    _Argument(
                        "study_manifest",
                        "--study-manifest",
                        "file",
                        validator="validate_study_manifest",
                    ),
                    _Argument(
                        "diagnosis_id",
                        "--diagnosis-id",
                        "literal",
                    ),
                ),
            )
            inputs = root / "inputs"
            inputs.mkdir()
            arguments, admitted, input_manifest = _prepare_arguments(
                "diagnose",
                spec,
                {
                    "study_manifest": public,
                    "diagnosis_id": "diagnosis-fixture-001",
                },
                inputs,
                root / "result.json",
                [oracle],
            )
            self.assertEqual(
                [
                    "--study-manifest",
                    str(inputs / "00-study_manifest"),
                    "--diagnosis-id",
                    "diagnosis-fixture-001",
                    "--output",
                    str(root / "result.json"),
                ],
                arguments,
            )
            self.assertEqual([inputs / "00-study_manifest"], admitted)
            self.assertEqual(
                "experimental-calibration-role-inputs-v1",
                input_manifest["schema_version"],
            )
            self.assertEqual("diagnose", input_manifest["engine_entrypoint"])
            self.assertEqual(
                "study_manifest",
                input_manifest["roles"][0]["role"],
            )
            role_unhashed = deepcopy(input_manifest["roles"][0])
            role_supplied = role_unhashed["role_input_sha256"]
            role_unhashed["role_input_sha256"] = None
            self.assertEqual(
                _digest_for_test(canonical_json_bytes(role_unhashed)),
                role_supplied,
            )
            self.assertEqual(
                [
                    {
                        "argument_sha256": input_manifest["arguments"][0][
                            "argument_sha256"
                        ],
                        "flag": "--study-manifest",
                        "kind": "file",
                        "name": "study_manifest",
                        "role_input_sha256": input_manifest["roles"][0][
                            "role_input_sha256"
                        ],
                    },
                    {
                        "argument_sha256": input_manifest["arguments"][1][
                            "argument_sha256"
                        ],
                        "flag": "--diagnosis-id",
                        "kind": "literal",
                        "name": "diagnosis_id",
                        "value": "diagnosis-fixture-001",
                        "value_sha256": _digest_for_test(
                            b"diagnosis-fixture-001"
                        ),
                    },
                ],
                input_manifest["arguments"],
            )
            for row in input_manifest["arguments"]:
                unhashed = dict(row)
                supplied = unhashed["argument_sha256"]
                unhashed["argument_sha256"] = None
                self.assertEqual(
                    _digest_for_test(canonical_json_bytes(unhashed)),
                    supplied,
                )
            self.assertEqual(
                _digest_for_test(canonical_json_bytes(input_manifest["arguments"])),
                input_manifest["arguments_sha256"],
            )
            self.assertNotIn(str(root), canonical_json_bytes(input_manifest).decode())

    def test_published_runtime_and_phase_receipt_are_logical_self_hashed_records(self):
        private_runtime = {
            "schema_version": "experimental-calibration-python-runtime-v1",
            "platform": "Darwin",
            "interpreter_path": "/private/provider/python",
            "resolved_interpreter_path": "/protected/python",
            "python_version": [3, 14, 5],
            "executable_sha256": "sha256:" + "1" * 64,
            "runtime_roots": [
                {
                    "path": "/private/provider",
                    "resolved_path": "/protected/provider",
                    "device": 123,
                    "inode": 456,
                }
            ],
            "external_dependency_files": [
                {
                    "path": "numpy/__init__.py",
                    "distribution": "numpy",
                    "distribution_version": "2.4.2",
                    "byte_count": 1234,
                    "raw_bytes_sha256": "sha256:" + "2" * 64,
                }
            ],
            "runtime_binding_sha256": "sha256:" + "3" * 64,
        }
        published_runtime = sandbox_module._published_runtime_binding(
            private_runtime
        )
        published_raw = canonical_json_bytes(published_runtime)
        for forbidden in (
            "/private/provider",
            "/protected/python",
            "device",
            "inode",
            "runtime_roots",
        ):
            self.assertNotIn(forbidden, published_raw.decode())
        runtime_unhashed = deepcopy(published_runtime)
        runtime_supplied = runtime_unhashed["runtime_binding_sha256"]
        runtime_unhashed["runtime_binding_sha256"] = None
        self.assertEqual(
            _digest_for_test(canonical_json_bytes(runtime_unhashed)),
            runtime_supplied,
        )

        input_manifest = {
            "input_manifest_sha256": "sha256:" + "4" * 64,
            "arguments_sha256": "sha256:" + "5" * 64,
            "roles_sha256": "sha256:" + "6" * 64,
        }
        source_manifest = {
            "source_manifest_sha256": "sha256:" + "7" * 64,
            "first_party_files_sha256": "sha256:" + "8" * 64,
            "external_runtime_files_sha256": "sha256:" + "9" * 64,
        }
        receipt = sandbox_module._phase_execution_receipt(
            engine_entrypoint="exercise",
            output_kind="json_file",
            output_sha256="sha256:" + "a" * 64,
            source_manifest=source_manifest,
            input_manifest=input_manifest,
            runtime_binding=published_runtime,
        )
        self.assertEqual(
            "experimental-calibration-phase-execution-receipt-v1",
            receipt["schema_version"],
        )
        self.assertEqual("result.json", receipt["output"]["name"])
        receipt_unhashed = deepcopy(receipt)
        receipt_supplied = receipt_unhashed["phase_execution_receipt_sha256"]
        receipt_unhashed["phase_execution_receipt_sha256"] = None
        self.assertEqual(
            _digest_for_test(canonical_json_bytes(receipt_unhashed)),
            receipt_supplied,
        )
        self.assertNotIn(
            "/private/provider",
            canonical_json_bytes(receipt).decode(),
        )

    def test_denied_input_identity_survives_ancestor_alias_and_post_auth_swap(self):
        manifest = _study_manifest()
        hidden_manifest = _study_manifest(seed_delta=1)
        spec = _Entrypoint(
            cli="future.py",
            required_module="future.py",
            source_manifest="future-source-manifest.json",
            arguments=(
                _Argument(
                    "study_manifest",
                    "--study-manifest",
                    "file",
                    validator="validate_study_manifest",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oracle = root / "oracle"
            oracle.mkdir()
            hidden = oracle / "hidden.json"
            hidden_bytes = canonical_json_bytes(hidden_manifest)
            hidden.write_bytes(hidden_bytes)
            public_parent = root / "public-parent"
            public_parent.mkdir()
            public = public_parent / "manifest.json"
            public_bytes = canonical_json_bytes(manifest)
            public.write_bytes(public_bytes)
            ancestor_alias = root / "public-alias"
            ancestor_alias.symlink_to(public_parent, target_is_directory=True)

            with self.assertRaises(ContractError):
                _prepare_arguments(
                    "diagnose",
                    spec,
                    {"study_manifest": ancestor_alias / public.name},
                    root / "alias-inputs",
                    root / "alias-result.json",
                    [oracle],
                )

            inputs = root / "race-inputs"
            inputs.mkdir()
            original = sandbox_module._assert_original_input_admissible
            swapped = False

            def swap_after_authentication(
                source: Path,
                denied_roots: list[Path],
            ):
                nonlocal swapped
                authenticated = original(source, denied_roots)
                if not swapped:
                    swapped = True
                    public.unlink()
                    os.link(hidden, public)
                return authenticated

            with patch.object(
                sandbox_module,
                "_assert_original_input_admissible",
                side_effect=swap_after_authentication,
            ):
                _prepare_arguments(
                    "diagnose",
                    spec,
                    {"study_manifest": public},
                    inputs,
                    root / "race-result.json",
                    [oracle],
                )
            copied = (inputs / "00-study_manifest").read_bytes()
            self.assertEqual(public_bytes, copied)
            self.assertNotEqual(hidden_bytes, copied)

    def test_declared_source_closure_rejects_missing_extra_changed_dynamic_and_symlinked(self):
        with tempfile.TemporaryDirectory() as raw:
            scripts = Path(raw)
            package = scripts / "audience_panel_builder"
            package.mkdir()
            (package / "__init__.py").write_text("# package\n", encoding="utf-8")
            module = package / "closed.py"
            module.write_text("VALUE = 1\n", encoding="utf-8")
            entry = scripts / "entry.py"
            entry.write_text(
                "import audience_panel_builder.closed\n",
                encoding="utf-8",
            )
            with patch.object(sandbox_module, "_SCRIPTS_ROOT", scripts):
                rows = _discover_closure(entry)
                _assert_declared_source_closure(rows, rows)
                with self.assertRaises(ContractError):
                    _assert_declared_source_closure(rows[:-1], rows)
                extra = [
                    *rows,
                    {
                        "path": "audience_panel_builder/extra.py",
                        "byte_count": 1,
                        "raw_bytes_sha256": "sha256:" + "0" * 64,
                    },
                ]
                with self.assertRaises(ContractError):
                    _assert_declared_source_closure(extra, rows)
                changed = deepcopy(rows)
                changed[0]["raw_bytes_sha256"] = "sha256:" + "0" * 64
                with self.assertRaises(ContractError):
                    _assert_declared_source_closure(changed, rows)

                entry.write_text(
                    "import importlib as loader\n"
                    "loader.import_module('audience_panel_builder.closed')\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ContractError):
                    _discover_closure(entry)

                for stored_alias_source in (
                    (
                        "import builtins as b\n"
                        "loader = getattr(b, '__import__')\n"
                        "loader('json')\n"
                    ),
                    (
                        "import runpy\n"
                        "runner = getattr(runpy, 'run_path')\n"
                        "runner('payload.py')\n"
                    ),
                    (
                        "import importlib\n"
                        "loader = getattr(importlib, 'import_module')\n"
                        "loader('json')\n"
                    ),
                    (
                        "loader = __builtins__['__import__']\n"
                        "loader('json')\n"
                    ),
                    (
                        "import builtins as b\n"
                        "loader = vars(b)['__import__']\n"
                        "loader('json')\n"
                    ),
                    (
                        "import builtins as b\n"
                        "loader = b.__dict__['__import__']\n"
                        "loader('json')\n"
                    ),
                ):
                    with self.subTest(source=stored_alias_source):
                        entry.write_text(stored_alias_source, encoding="utf-8")
                        with self.assertRaises(ContractError):
                            _discover_closure(entry)

                real_entry = scripts / "real-entry.py"
                real_entry.write_text("VALUE = 1\n", encoding="utf-8")
                linked_entry = scripts / "linked-entry.py"
                linked_entry.symlink_to(real_entry)
                with self.assertRaises(ContractError):
                    _discover_closure(linked_entry)

    def test_static_guard_rejects_executable_computed_getattr_chain(self):
        source = (
            'registry = globals()["__builtins__"]\n'
            'attribute = "get"\n'
            "lookup = getattr(registry, attribute)\n"
            'loader = lookup("__import__")\n'
            'loaded = loader("math")\n'
            "result = loaded.sqrt(81)\n"
        )
        namespace: dict[str, object] = {}
        exec(source, namespace)
        self.assertEqual(9.0, namespace["result"])
        with self.assertRaises(ContractError):
            sandbox_module._assert_no_dynamic_authority(
                ast.parse(source),
                "computed-getattr.py",
            )
        for allowed in (
            'import os\nvalue = getattr(os, "O_NOFOLLOW", 0)\n',
            'import os\nvalue = getattr(os, "O_CLOEXEC", 0)\n',
            'import re\nvalue = re.compile("^[a-z]+$")\n',
        ):
            with self.subTest(allowed=allowed):
                sandbox_module._assert_no_dynamic_authority(
                    ast.parse(allowed),
                    "allowed-literal.py",
                )

    def test_static_guard_binds_literal_exceptions_to_untouched_modules(self):
        executable_attacks = (
            (
                "class Carrier:\n"
                "    def __getattr__(self, name):\n"
                '        return globals()["__builtins__"]["__import__"]\n'
                "os = Carrier()\n"
                'loader = getattr(os, "O_NOFOLLOW", 0)\n'
                'result = loader("math").sqrt(81)\n'
            ),
            (
                "class Carrier:\n"
                "    def compile(self, pattern):\n"
                '        return globals()["__builtins__"]["__import__"]\n'
                "re = Carrier()\n"
                'loader = re.compile("^[a-z]+$")\n'
                'result = loader("math").sqrt(81)\n'
            ),
            (
                "import os\n"
                "class Carrier:\n"
                "    def __getattr__(self, name):\n"
                '        return globals()["__builtins__"]["__import__"]\n'
                "os = Carrier()\n"
                'loader = getattr(os, "O_CLOEXEC", 0)\n'
                'result = loader("math").sqrt(81)\n'
            ),
        )
        for source in executable_attacks:
            with self.subTest(source=source):
                namespace: dict[str, object] = {}
                exec(source, namespace)
                self.assertEqual(9.0, namespace["result"])
                with self.assertRaises(ContractError):
                    sandbox_module._assert_no_dynamic_authority(
                        ast.parse(source),
                        "module-lookalike.py",
                    )

        rebinding_shapes = (
            "import os\nos: object = object()\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\n(os := object())\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nfor os in ():\n    pass\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nvalues = [os for os in ()]\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nwith open(__file__) as os:\n    pass\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\ntry:\n    pass\nexcept Exception as os:\n    pass\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\ndel os\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\ndef os():\n    pass\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nclass os:\n    pass\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\ndef wrapper(os):\n    return os\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nwrapper = lambda os: os\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nimport math as os\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import os\nglobals()['os'] = object()\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
            "import re\nre.compile = lambda pattern: pattern\n"
            'value = re.compile("^[a-z]+$")\n',
            "import os\nsetattr(os, 'O_NOFOLLOW', 0)\n"
            'value = getattr(os, "O_NOFOLLOW", 0)\n',
        )
        for source in rebinding_shapes:
            with self.subTest(source=source):
                with self.assertRaises(ContractError):
                    sandbox_module._assert_no_dynamic_authority(
                        ast.parse(source),
                        "module-rebinding.py",
                    )

    def test_committed_materialize_source_manifest_is_exact_and_frozen(self):
        spec = _ENTRYPOINTS["materialize"]
        declared = _load_declared_source_manifest("materialize", spec)
        discovered = _discover_closure(
            SCRIPTS / spec.cli,
            omitted_initializers=spec.namespace_packages,
        )
        _assert_declared_source_closure(declared["files"], discovered)
        self.assertEqual(17, len(discovered))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing = replace(
                spec,
                source_manifest=str(root / "missing.json"),
            )
            with self.assertRaises(EntrypointUnavailable):
                _load_declared_source_manifest("materialize", missing)

            stale_document = deepcopy(declared)
            stale_document["files"][0]["byte_count"] += 1
            stale_path = root / "stale.json"
            stale_path.write_bytes(canonical_json_bytes(stale_document))
            stale = replace(spec, source_manifest=str(stale_path))
            with self.assertRaises(ContractError):
                _load_declared_source_manifest("materialize", stale)

        changed = deepcopy(discovered)
        changed[0]["raw_bytes_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(ContractError):
            _assert_declared_source_closure(declared["files"], changed)
        with self.assertRaises(ContractError):
            _assert_declared_source_closure(
                declared["files"],
                discovered[:-1],
            )
        extra = [
            *discovered,
            {
                "path": "audience_panel_builder/unexpected.py",
                "byte_count": 1,
                "raw_bytes_sha256": "sha256:" + "0" * 64,
            },
        ]
        extra.sort(key=lambda row: row["path"])
        with self.assertRaises(ContractError):
            _assert_declared_source_closure(declared["files"], extra)

    def test_macos_runtime_binds_exact_python_311_or_newer(self):
        if platform.system() != "Darwin":
            self.skipTest("Darwin runtime binding probe")
        binding = sandbox_module._private_stage_runtime_binding("Darwin")
        bound = sandbox_module._bound_interpreter().absolute()
        self.assertEqual(
            "experimental-calibration-python-runtime-v1",
            binding["schema_version"],
        )
        self.assertEqual(str(bound), binding["interpreter_path"])
        self.assertGreaterEqual(tuple(binding["python_version"][:2]), (3, 11))
        resolved = bound.resolve(strict=True)
        self.assertEqual(
            "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest(),
            binding["executable_sha256"],
        )
        self.assertTrue(binding["runtime_roots"])
        for row in binding["runtime_roots"]:
            self.assertEqual(
                Path(row["path"]).resolve(strict=True),
                Path(row["resolved_path"]),
            )
            value = os.stat(row["resolved_path"], follow_symlinks=False)
            self.assertEqual((value.st_dev, value.st_ino), (
                row["device"],
                row["inode"],
            ))
        self.assertEqual([], binding["external_dependency_files"])
        extended = sandbox_module._bind_external_runtime(
            binding,
            [
                {
                    "path": "fictional-dependency.py",
                    "raw_bytes_sha256": "sha256:" + "1" * 64,
                }
            ],
        )
        self.assertEqual(
            binding["interpreter_path"],
            extended["interpreter_path"],
        )
        self.assertNotEqual(
            binding["runtime_binding_sha256"],
            extended["runtime_binding_sha256"],
        )
        expected = deepcopy(binding)
        expected["runtime_binding_sha256"] = None
        self.assertEqual(
            "sha256:" + hashlib.sha256(canonical_json_bytes(expected)).hexdigest(),
            binding["runtime_binding_sha256"],
        )
        old_probe = sandbox_module._runtime_probe_document(bound)
        old_probe["version"] = [3, 10, 99]
        with patch.object(
            sandbox_module,
            "_runtime_probe_document",
            return_value=old_probe,
        ), self.assertRaises(sandbox_module.ProducerRuntimeUnavailable):
            sandbox_module._private_stage_runtime_binding("Darwin")

    @unittest.skipUnless(
        platform.system() == "Linux",
        "Linux runtime loader roots",
    )
    def test_linux_runtime_binds_available_dynamic_loader_roots(self):
        roots = sandbox_module._system_read_roots(Path(sys.executable))
        for candidate in (Path("/lib"), Path("/lib64")):
            if candidate.exists():
                self.assertIn(candidate, roots)

    def test_private_stage_entrypoints_are_closed_and_completed(self):
        self.assertEqual(
            [
                "base_panel_binding",
                "study_manifest",
                "scenario_manifests",
                "experiment_designs",
                "evidence_library_snapshot",
                "evidence_head_receipt",
                "creative_attribute_registry",
                "alternative_causes",
                "diagnosis_id",
                "diagnosed_at",
            ],
            [argument.name for argument in _ENTRYPOINTS["diagnose"].arguments],
        )
        self.assertEqual(
            [
                "base_panel_binding",
                "study_manifest",
                "scenario_manifests",
                "experiment_designs",
                "diagnosis",
                "creative_attribute_registry",
                "evidence_library_snapshot",
                "evidence_head_receipt",
                "alternative_causes",
                "proposal_id",
                "proposed_at",
            ],
            [argument.name for argument in _ENTRYPOINTS["propose"].arguments],
        )
        exercise_arguments = {
            argument.name: argument.flag
            for argument in _ENTRYPOINTS["exercise"].arguments
        }
        self.assertEqual(
            "--candidate-bindings-and-panels",
            exercise_arguments["candidate_bindings_and_panels"],
        )
        self.assertEqual(
            ("numpy", "scipy"),
            _ENTRYPOINTS["exercise"].external_runtime_modules,
        )
        materialize = _ENTRYPOINTS["materialize"]
        self.assertEqual(
            [
                "base_panel",
                "proposal",
                "study_manifest",
                "scenario_manifests",
                "experiment_designs",
                "diagnosis",
                "attribute_registry",
                "evidence_library_snapshot",
                "evidence_head_receipt",
                "alternative_causes",
                "candidate_id",
                "candidate_version",
                "created_at",
            ],
            [argument.name for argument in materialize.arguments],
        )
        materialize_flags = {
            argument.name: argument.flag
            for argument in materialize.arguments
        }
        self.assertEqual(
            "--attribute-registry",
            materialize_flags["attribute_registry"],
        )
        self.assertEqual("--output-dir", materialize.output_flag)
        self.assertEqual("directory", materialize.output_kind)
        for name in ("diagnose", "propose"):
            with self.subTest(file_role=name):
                self.assertEqual(
                    "--private-stage-output",
                    _ENTRYPOINTS[name].output_flag,
                )
                self.assertEqual("json_file", _ENTRYPOINTS[name].output_kind)
                self.assertEqual(
                    (
                        "audience_panel_builder",
                        "audience_panel_builder/population",
                        "audience_panel_builder/population/experimental_calibration",
                    ),
                    _ENTRYPOINTS[name].namespace_packages,
                )
        self.assertEqual(
            "--private-stage-output",
            _ENTRYPOINTS["exercise"].output_flag,
        )
        self.assertEqual("json_file", _ENTRYPOINTS["exercise"].output_kind)
        self.assertEqual(
            (
                "audience_lab",
                "audience_panel_builder",
                "audience_panel_builder/population",
                "audience_panel_builder/population/experimental_calibration",
            ),
            _ENTRYPOINTS["exercise"].namespace_packages,
        )
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ContractError):
                run_engine_in_private_stage(
                    engine_entrypoint="probe",
                    validated_arguments={},
                    oracle_denied_roots=[Path(raw) / "oracle"],
                    output_dir=Path(raw) / "output-probe",
                )

    def test_registered_exercise_candidate_role_accepts_exact_task6_seals(self):
        envelopes = exercise_inputs_fixture()[
            "candidate_bindings_and_panels"
        ]
        validator = sandbox_module._role_validator(
            "validate_candidate_bindings_and_panels_input"
        )

        self.assertEqual(envelopes, validator(envelopes))

        malformed = deepcopy(envelopes)
        malformed[0]["sealed_bundle_manifest"]["candidate_id"] = (
            "different-candidate"
        )
        with self.assertRaises(ContractError):
            validator(malformed)

        duplicate = [deepcopy(envelopes[0]), deepcopy(envelopes[0])]
        with self.assertRaisesRegex(
            ContractError,
            "duplicates authority identity",
        ):
            validator(duplicate)

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
    def test_registered_materialize_role_runs_completed_bundle_cli(self):
        inputs = valid_candidate_inputs()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            oracle = root / "oracle"
            oracle.mkdir()
            (oracle / "hidden-oracle.json").write_bytes(
                canonical_json_bytes({"private": "denied"})
            )
            validated_arguments = {}
            for key in (
                "base_panel",
                "proposal",
                "study_manifest",
                "scenario_manifests",
                "experiment_designs",
                "diagnosis",
                "attribute_registry",
                "evidence_library_snapshot",
                "evidence_head_receipt",
                "alternative_causes",
            ):
                path = root / f"{key}.json"
                path.write_bytes(canonical_json_bytes(inputs[key]))
                validated_arguments[key] = path
            validated_arguments.update(
                {
                    "candidate_id": inputs["candidate_id"],
                    "candidate_version": inputs["candidate_version"],
                    "created_at": inputs["created_at"],
                }
            )
            result = run_engine_in_private_stage(
                engine_entrypoint="materialize",
                validated_arguments=validated_arguments,
                oracle_denied_roots=[oracle],
                output_dir=root / "released",
            )
            forged = deepcopy(inputs["proposal"])
            forged["operation"]["proposed_after"]["proof_needs"] = [
                "Caller-invented staged value"
            ]
            forged["proposal_sha256"] = None
            forged["proposal_sha256"] = sha256_json(forged)
            forged_path = root / "forged-proposal.json"
            forged_path.write_bytes(canonical_json_bytes(forged))
            rejected_arguments = {
                **validated_arguments,
                "proposal": forged_path,
            }
            with self.assertRaises(ContractError):
                run_engine_in_private_stage(
                    engine_entrypoint="materialize",
                    validated_arguments=rejected_arguments,
                    oracle_denied_roots=[oracle],
                    output_dir=root / "rejected",
                )
            self.assertFalse((root / "rejected").exists())
            bundle = Path(result["output_path"])
            self.assertTrue(bundle.is_dir())
            released_root = bundle.parent
            self.assertEqual(
                {
                    "input-manifest.json",
                    "phase-execution-receipt.json",
                    "result",
                    "result-receipt.json",
                    "runtime-binding.json",
                    "source-manifest.json",
                },
                {path.name for path in released_root.iterdir()},
            )
            input_manifest = json.loads(
                (released_root / "input-manifest.json").read_bytes()
            )
            literal_arguments = {
                row["name"]: row
                for row in input_manifest["arguments"]
                if row["kind"] == "literal"
            }
            self.assertEqual(
                {
                    "candidate_id",
                    "candidate_version",
                    "created_at",
                },
                set(literal_arguments),
            )
            self.assertEqual(
                inputs["candidate_id"],
                literal_arguments["candidate_id"]["value"],
            )
            receipt_path = released_root / "phase-execution-receipt.json"
            self.assertEqual(
                str(receipt_path.resolve(strict=True)),
                result["phase_execution_receipt_path"],
            )
            receipt = json.loads(receipt_path.read_bytes())
            self.assertEqual(
                result["phase_execution_receipt_sha256"],
                receipt["phase_execution_receipt_sha256"],
            )
            self.assertEqual(
                result["output_sha256"],
                receipt["output"]["output_sha256"],
            )
            receipt_unhashed = deepcopy(receipt)
            receipt_unhashed["phase_execution_receipt_sha256"] = None
            self.assertEqual(
                _digest_for_test(canonical_json_bytes(receipt_unhashed)),
                receipt["phase_execution_receipt_sha256"],
            )
            runtime_raw = (
                released_root / "runtime-binding.json"
            ).read_bytes()
            receipt_raw = receipt_path.read_bytes()
            for forbidden in (
                str(root),
                "interpreter_path",
                "resolved_interpreter_path",
                "runtime_roots",
                '"device"',
                '"inode"',
            ):
                self.assertNotIn(forbidden.encode(), runtime_raw)
                self.assertNotIn(forbidden.encode(), receipt_raw)
            self.assertEqual(
                {
                    "README.txt",
                    "base-persona-authoring-projection.json",
                    "base-persona-snapshot.json",
                    "bundle-manifest.json",
                    "candidate-audience-panel.json",
                    "candidate-persona-authoring-projection.json",
                    "candidate-persona-snapshot.json",
                    "experimental-candidate-binding.json",
                    "experimental-proposal.json",
                    "persona-behavior-diff.json",
                    "standalone-panel-validation.json",
                },
                {path.name for path in bundle.iterdir()},
            )
            self.assertNotIn(
                b"private",
                b"".join(path.read_bytes() for path in bundle.iterdir()),
            )

    @unittest.skipUnless(
        os.environ.get("RUN_EXPERIMENTAL_CALIBRATION_PROVIDER_TESTS") == "1",
        "set RUN_EXPERIMENTAL_CALIBRATION_PROVIDER_TESTS=1 for the real provider probe",
    )
    def test_real_provider_allows_public_read_and_denies_oracle_escape(self):
        provider_path = (
            Path("/usr/bin/sandbox-exec")
            if platform.system() == "Darwin"
            else Path("/usr/bin/bwrap")
        )
        if not provider_path.is_file():
            self.skipTest(f"real provider unavailable: {provider_path}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            public = root / "public.json"
            oracle = root / "oracle"
            output = root / "output.json"
            oracle.mkdir()
            public.write_bytes(canonical_json_bytes({"public": "allowed"}))
            (oracle / "hidden-oracle.json").write_bytes(
                canonical_json_bytes({"private": "denied"})
            )
            if platform.system() == "Darwin":
                # Prefer host paths that exist on GitHub-hosted macOS runners
                # and local developer machines, not machine-specific apps/dotfiles.
                undeclared_paths = [
                    Path("/System/Library/CoreServices/SystemVersion.plist"),
                    Path("/private/etc/hosts"),
                    Path("/usr/bin/sw_vers"),
                ]
                missing = [path for path in undeclared_paths if not path.is_file()]
                if missing:
                    self.fail(
                        "real macOS allowlist probe paths are unavailable: "
                        + ", ".join(str(path) for path in missing)
                    )
            else:
                undeclared_paths = [
                    root / "undeclared-one.json",
                    root / "undeclared-two.json",
                    root / "undeclared-three.json",
                ]
                for path in undeclared_paths:
                    path.write_bytes(canonical_json_bytes({"not": "admitted"}))
            probe = (
                "import importlib,json,os,pathlib,sys\n"
                "public,oracle,undeclared_opt,undeclared_app,undeclared_user,output=sys.argv[1:]\n"
                "result={"
                "'public':json.loads(pathlib.Path(public).read_text()),"
                "'python_executable':sys.executable,"
                "'python_version':list(sys.version_info[:3])"
                "}\n"
                "for key,action in {\n"
                "'oracle_read':lambda:pathlib.Path(oracle).read_text(),\n"
                "'oracle_import':lambda:importlib.import_module("
                "'experimental_persona_calibration_oracle'),\n"
                "'env_escape':lambda:pathlib.Path(os.environ['ORACLE_PATH']).read_text(),\n"
                "'undeclared_opt':lambda:pathlib.Path(undeclared_opt).read_text(),\n"
                "'undeclared_app':lambda:pathlib.Path(undeclared_app).read_text(),\n"
                "'undeclared_user':lambda:pathlib.Path(undeclared_user).read_text(),\n"
                "'second_write':lambda:pathlib.Path(output).with_name("
                "'second.json').write_text('forbidden'),\n"
                "}.items():\n"
                "  try: action()\n"
                "  except Exception: result[key]='denied'\n"
                "  else: result[key]='escaped'\n"
                "pathlib.Path(output).write_text(json.dumps("
                "result,sort_keys=True,separators=(',',':'))+'\\n')\n"
            )
            result = _run_provider_command(
                source=probe,
                arguments=[
                    str(public),
                    str(oracle / "hidden-oracle.json"),
                    *(str(path) for path in undeclared_paths),
                    str(output),
                ],
                admitted_read_paths=[public],
                denied_roots=[oracle, SCRIPTS / "experimental_persona_calibration_oracle"],
                output_path=output,
                environment={"ORACLE_PATH": str(oracle / "hidden-oracle.json")},
            )
            self.assertEqual({"public": "allowed"}, result["public"])
            self.assertGreaterEqual(tuple(result["python_version"][:2]), (3, 11))
            self.assertEqual(
                sandbox_module._bound_interpreter().resolve(strict=True),
                Path(result["python_executable"]).resolve(strict=True),
            )
            self.assertEqual("denied", result["oracle_read"])
            self.assertEqual("denied", result["oracle_import"])
            self.assertEqual("denied", result["env_escape"])
            self.assertEqual("denied", result["undeclared_opt"])
            self.assertEqual("denied", result["undeclared_app"])
            self.assertEqual("denied", result["undeclared_user"])
            self.assertEqual("denied", result["second_write"])

    def test_cli_reproduces_committed_fixtures_and_refuses_clobbering(self):
        cli = SCRIPTS / "build-synthetic-persona-behavior-study.py"
        committed = ROOT / "conformance" / "fixtures" / "experimental-calibration"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            alias_target = root / "alias-target"
            alias_target.mkdir()
            alias = root / "alias"
            alias.symlink_to(alias_target, target_is_directory=True)
            unsafe = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "--manifest-output",
                    str(alias / "study-manifest.json"),
                    "--public-fixtures-root",
                    str(root / "unsafe-public"),
                    "--oracle-fixtures-root",
                    str(root / "unsafe-oracle"),
                    "--created-at",
                    "2026-07-29T00:00:00Z",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(3, unsafe.returncode, unsafe.stderr.decode())
            self.assertEqual([], list(alias_target.iterdir()))
            self.assertFalse((root / "unsafe-public").exists())
            self.assertFalse((root / "unsafe-oracle").exists())

            generated = root / "generated"
            command = [
                sys.executable,
                str(cli),
                "--manifest-output",
                str(generated / "study-manifest.json"),
                "--public-fixtures-root",
                str(generated),
                "--oracle-fixtures-root",
                str(generated / "oracle"),
                "--created-at",
                "2026-07-29T00:00:00Z",
            ]
            first = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, first.returncode, first.stderr.decode())
            self.assertEqual(_tree_bytes(committed), _tree_bytes(generated))
            second = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(3, second.returncode)


if __name__ == "__main__":
    unittest.main()
