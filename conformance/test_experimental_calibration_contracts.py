"""Closed-contract and authority-firewall coverage for the calibration sandbox."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population import experimental_calibration as engine  # noqa: E402
from audience_panel_builder.population.experimental_calibration import (  # noqa: E402
    CANDIDATE_VERSION,
    ATTRIBUTE_REGISTRY_VERSION,
    AUTHORING_PROJECTION_VERSION,
    DIAGNOSIS_METHOD_VERSION,
    DIAGNOSIS_VERSION,
    EVIDENCE_LIBRARY_VERSION,
    EXERCISE_VERSION,
    OUTCOME_OBSERVATION_VERSION,
    PROPOSAL_VERSION,
    STUDY_MANIFEST_VERSION,
    validate_experimental_proposal,
    validate_outcome_observation,
    validate_sandbox_candidate_binding,
    validate_study_manifest,
    validate_synthetic_exercise,
    validate_creative_attribute_registry,
    validate_evidence_library,
    validate_diagnosis,
    validate_persona_authoring_projection,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    SYNTHETIC_SCENARIO_REGISTRY,
)
from experimental_persona_calibration_oracle import (  # noqa: E402
    EVALUATION_VERSION,
    ORACLE_VERSION,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    candidate_fixture,
    exercise_fixture,
    outcome_observation_fixture,
    proposal_fixture,
    rehash,
    study_manifest_fixture,
    registry_fixture,
    evidence_library_fixture,
    diagnosis_fixture,
    evaluation_inputs_fixture,
    projection_fixture,
    oracle_fixture,
    evaluation_fixture,
    generator_outcome_observation_fixture,
)
from experimental_persona_calibration_oracle import validate_oracle, validate_synthetic_evaluation  # noqa: E402
from experimental_persona_calibration_oracle.contracts import (  # noqa: E402
    validate_evaluation_phase_receipt,
)
from audience_panel_builder.population.experimental_calibration.adapters import (  # noqa: E402
    normalize_platform_export,
)
from audience_panel_builder.population.experimental_calibration.attributes import (  # noqa: E402
    build_creative_attribute_registry,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    creative_attribute_inputs,
    raw_platform_export_fixture,
)


class ExperimentalCalibrationContractTests(unittest.TestCase):
    def test_proposal_is_synthetic_only_and_production_blocked(self):
        proposal = proposal_fixture()
        checked = validate_experimental_proposal(proposal)
        self.assertEqual("synthetic_fixture_only", checked["evidence_origin"])
        self.assertEqual("not_evaluated", checked["real_world_validation_status"])
        self.assertFalse(checked["production_executable"])
        self.assertTrue(checked["sandbox_candidate_materialization_permitted"])
        self.assertFalse(checked["production_candidate_materialization_permitted"])
        self.assertFalse(checked["activation_permitted"])
        self.assertFalse(checked["active_panel_mutation_permitted"])

    def test_unknown_proposal_key_fails_closed(self):
        proposal = proposal_fixture()
        proposal["helpful_extension"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_structural_change_field_is_rejected(self):
        proposal = proposal_fixture()
        proposal["operation"]["changed_fields"] = ["planned_weight"]
        with self.assertRaisesRegex(ContractError, "allowed persona behavior field"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_oracle_field_is_rejected_from_observation(self):
        observation = outcome_observation_fixture()
        observation["true_behavioral_miss"] = {"proof_needs": ["quantified ROI"]}
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_outcome_observation(rehash(observation, "observation_sha256"))

    def test_compact_generator_lookalike_is_rejected_by_public_validator(self):
        observation = generator_outcome_observation_fixture()
        observation["source"] = {"platform": "meta"}
        observation["observation_id"] = "spoofed-adapter-like-observation"
        observation["delivery"]["impressions"] = 999999
        with self.assertRaisesRegex(ContractError, "source"):
            validate_outcome_observation(
                rehash(observation, "observation_sha256")
            )

    def test_private_stage_fields_are_rejected_at_nested_engine_boundaries(self):
        observation = outcome_observation_fixture()
        observation["source"]["or" + "acle"] = {"leak": True}
        with self.assertRaisesRegex(ContractError, "private-stage field"):
            validate_outcome_observation(rehash(observation, "observation_sha256"))

    def test_versions_and_self_hashes_are_exact(self):
        self.assertEqual("persona-behavior-outcome-observation-v1", OUTCOME_OBSERVATION_VERSION)
        self.assertEqual("synthetic-persona-behavior-study-manifest-v1", STUDY_MANIFEST_VERSION)
        self.assertEqual("experimental-persona-behavior-proposal-v1", PROPOSAL_VERSION)
        self.assertEqual("experimental-persona-panel-candidate-v1", CANDIDATE_VERSION)
        self.assertEqual("creative-attribute-registry-v1", ATTRIBUTE_REGISTRY_VERSION)
        self.assertEqual("experimental-persona-authoring-projection-v1", AUTHORING_PROJECTION_VERSION)
        self.assertEqual("experimental-persona-behavior-diagnosis-v1", DIAGNOSIS_VERSION)
        self.assertEqual("blocked-contrast-bootstrap-v1", DIAGNOSIS_METHOD_VERSION)
        self.assertEqual("persona-behavior-evidence-library-v1", EVIDENCE_LIBRARY_VERSION)
        self.assertEqual("synthetic-persona-behavior-exercise-v1", EXERCISE_VERSION)
        self.assertEqual("synthetic-persona-behavior-oracle-v1", ORACLE_VERSION)
        self.assertEqual("synthetic-persona-behavior-evaluation-v1", EVALUATION_VERSION)
        validate_outcome_observation(outcome_observation_fixture())
        validate_study_manifest(study_manifest_fixture())
        validate_sandbox_candidate_binding(candidate_fixture())
        validate_synthetic_exercise(exercise_fixture())

    def test_all_contract_fixtures_validate_directly(self):
        self.assertEqual(registry_fixture(), validate_creative_attribute_registry(registry_fixture()))
        self.assertEqual(evidence_library_fixture(), validate_evidence_library(evidence_library_fixture()))
        self.assertEqual(diagnosis_fixture(), validate_diagnosis(diagnosis_fixture()))
        self.assertEqual(projection_fixture(), validate_persona_authoring_projection(projection_fixture()))
        self.assertEqual(oracle_fixture(), validate_oracle(oracle_fixture()))
        self.assertEqual(evaluation_fixture(), validate_synthetic_evaluation(evaluation_fixture()))

    def test_manifest_freezes_complete_adapter_and_typed_dgp_parameters(self):
        manifest = study_manifest_fixture()
        checked = validate_study_manifest(manifest)
        self.assertEqual(
            {
                "adapter_id": "frozen-synthetic-panelist-response",
                "version": "1.0.0",
                "source_sha256": "sha256:" + "9" * 64,
                "feature_allowlist": [
                    "creative_attributes",
                    "experiment_design",
                    "persona_snapshot",
                    "study_manifest",
                ],
                "deterministic_tie_rule": "score-descending-creative-id-ascending",
                "seed": 73021,
            },
            checked["synthetic_response_adapter"],
        )
        self.assertEqual(
            ["boolean", "string", "number", "integer"],
            [
                entry["value_type"]
                for entry in checked["scenario_families"][0]["parameters"][
                    "parameter_values"
                ]
            ],
        )

    def test_manifest_response_adapter_rejects_noncanonical_inputs(self):
        mutations = [
            ("unknown fields", lambda adapter: adapter.update({"extra": True})),
            ("prefixed digest", lambda adapter: adapter.update({"source_sha256": "9" * 64})),
            ("semantic version", lambda adapter: adapter.update({"version": "1"})),
            (
                "canonically sorted",
                lambda adapter: adapter.update(
                    {"feature_allowlist": list(reversed(adapter["feature_allowlist"]))}
                ),
            ),
            (
                "unique values",
                lambda adapter: adapter.update(
                    {"feature_allowlist": ["creative_attributes", "creative_attributes"]}
                ),
            ),
            ("must not be empty", lambda adapter: adapter.update({"feature_allowlist": []})),
            (
                "must be one of",
                lambda adapter: adapter.update({"deterministic_tie_rule": "random"}),
            ),
            ("integer", lambda adapter: adapter.update({"seed": True})),
            ("integer", lambda adapter: adapter.update({"seed": -1})),
        ]
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                manifest = study_manifest_fixture()
                mutate(manifest["synthetic_response_adapter"])
                with self.assertRaisesRegex(ContractError, expected):
                    validate_study_manifest(rehash(manifest, "manifest_sha256"))

    def test_manifest_dgp_parameters_reject_noncanonical_inputs(self):
        def parameters(manifest):
            return manifest["scenario_families"][0]["parameters"]

        mutations = [
            ("unknown fields", lambda value: value.update({"extra": True})),
            (
                "canonical lowercase hyphenated identifier",
                lambda value: value.update({"parameter_set_id": "Bad ID"}),
            ),
            ("semantic version", lambda value: value.update({"parameter_version": "v1"})),
            (
                "prefixed digest",
                lambda value: value.update({"parameters_sha256": "0" * 64}),
            ),
            (
                "unknown fields",
                lambda value: value["parameter_values"][0].update({"extra": True}),
            ),
            (
                "non-empty string",
                lambda value: value["parameter_values"][0].update({"name": ""}),
            ),
            (
                "must be one of",
                lambda value: value["parameter_values"][0].update(
                    {"value_type": "decimal"}
                ),
            ),
            (
                "canonically sorted",
                lambda value: value.update(
                    {"parameter_values": list(reversed(value["parameter_values"]))}
                ),
            ),
            (
                "unique names",
                lambda value: value["parameter_values"][1].update({"name": "enabled"}),
            ),
            ("must not be empty", lambda value: value.update({"parameter_values": []})),
            (
                "exact integer",
                lambda value: value["parameter_values"][3].update({"value": True}),
            ),
            (
                "finite number",
                lambda value: value["parameter_values"][2].update({"value": 1}),
            ),
            (
                "exact boolean",
                lambda value: value["parameter_values"][0].update({"value": 1}),
            ),
            (
                "non-empty string",
                lambda value: value["parameter_values"][1].update({"value": ""}),
            ),
            (
                "does not match canonical content",
                lambda value: value.update({"parameters_sha256": "sha256:" + "0" * 64}),
            ),
        ]
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                manifest = study_manifest_fixture()
                mutate(parameters(manifest))
                with self.assertRaisesRegex(ContractError, expected):
                    validate_study_manifest(rehash(manifest, "manifest_sha256"))
        manifest = study_manifest_fixture()
        parameters(manifest)["parameter_values"][2]["value"] = float("inf")
        with self.assertRaisesRegex(ContractError, "NaN or infinity"):
            validate_study_manifest(manifest)

    def test_manifest_freezes_mcse_and_practical_effect_predicates(self):
        checked = validate_study_manifest(study_manifest_fixture())
        self.assertEqual(
            {
                "maximum": 0.01,
                "method_version": "deterministic-batch-quantile-mcse-v1",
                "batch_count": 10,
                "batch_partition_policy":
                    "equal_contiguous_replicate_batches",
                "quantile_interpolation": "linear",
                "reported_measures": [
                    "bootstrap_mean",
                    "interval_lower",
                    "interval_upper",
                ],
            },
            checked["monte_carlo_error_targets"],
        )
        self.assertEqual(
            "directional_point_estimate_strictly_exceeds_threshold",
            checked["diagnosis_method"]["minimum_practical_effect_rule"],
        )

    def test_manifest_rejects_mcse_or_practical_effect_method_drift(self):
        mutations = [
            (
                "must be exactly",
                lambda manifest: manifest["monte_carlo_error_targets"].update(
                    {"batch_partition_policy": "round_robin"}
                ),
            ),
            (
                "frozen measure set",
                lambda manifest: manifest["monte_carlo_error_targets"].update(
                    {"reported_measures": ["bootstrap_mean"]}
                ),
            ),
            (
                "must be divisible",
                lambda manifest: manifest["monte_carlo_error_targets"].update(
                    {"batch_count": 6}
                ),
            ),
            (
                "must be exactly",
                lambda manifest: manifest["diagnosis_method"].update(
                    {"minimum_practical_effect_rule": "interval_lower_exceeds"}
                ),
            ),
        ]
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                manifest = study_manifest_fixture()
                mutate(manifest)
                with self.assertRaisesRegex(ContractError, expected):
                    validate_study_manifest(
                        rehash(manifest, "manifest_sha256")
                    )

    def test_observation_requires_complete_analytical_identity(self):
        observation = outcome_observation_fixture()
        checked = validate_outcome_observation(observation)
        self.assertEqual(
            {
                "experiment_id",
                "campaign_id",
                "block_id",
                "batch_id",
                "arm_id",
                "reference_arm_id",
            },
            set(checked["experiment_binding"]),
        )
        del observation["experiment_binding"]["block_id"]
        with self.assertRaisesRegex(ContractError, "missing fields"):
            validate_outcome_observation(
                rehash(observation, "observation_sha256")
            )

    def test_expanded_adapter_observation_requires_authenticated_raw_source(self):
        raw = raw_platform_export_fixture("meta")
        manifest = json.loads(
            (
                ROOT
                / "conformance/fixtures/experimental-calibration/study-manifest.json"
            ).read_text()
        )
        registry = build_creative_attribute_registry(
            **creative_attribute_inputs()
        )
        observation = normalize_platform_export(
            platform="meta",
            raw_export_bytes=raw,
            source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
            study_manifest=manifest,
            creative_attribute_registry=registry,
        )[0]
        self.assertEqual(
            registry["registry_sha256"],
            observation["creative_attribute_binding"]["registry_sha256"],
        )
        del observation["source"]["source_sha256"]
        with self.assertRaisesRegex(ContractError, "source_sha256"):
            validate_outcome_observation(
                rehash(observation, "observation_sha256")
            )

    def test_registry_rejects_resealed_unknown_nested_field(self):
        registry = registry_fixture()
        registry["attribute_definitions"][0]["helpful_extension"] = True
        with self.assertRaisesRegex(
            ContractError,
            r"attribute_registry\.attribute_definitions\[0\] has unknown fields",
        ):
            validate_creative_attribute_registry(
                rehash(registry, "registry_sha256")
            )

    def test_evidence_library_rejects_resealed_unknown_entry_field(self):
        library = evidence_library_fixture()
        library["entries"][0]["helpful_extension"] = True
        with self.assertRaisesRegex(
            ContractError,
            r"evidence_entry has unknown fields",
        ):
            validate_evidence_library(rehash(library, "library_sha256"))

    def test_evidence_entry_rejects_resealed_correction_identity_drift(self):
        library = evidence_library_fixture()
        entry = library["entries"][0]
        entry["correction_identity_sha256"] = "sha256:" + "0" * 64
        entry = rehash(entry, "entry_sha256")
        library["entries"][0] = entry
        with self.assertRaisesRegex(
            ContractError,
            r"correction_identity_sha256.*immutable analytical row",
        ):
            validate_evidence_library(rehash(library, "library_sha256"))

    def test_diagnosis_rejects_resealed_invalid_decision(self):
        diagnosis = diagnosis_fixture()
        diagnosis["decision"] = "probably_repeatable"
        with self.assertRaisesRegex(
            ContractError,
            r"diagnosis\.decision must be one of",
        ):
            validate_diagnosis(rehash(diagnosis, "diagnosis_sha256"))

    def test_projection_rejects_resealed_stale_snapshot_digest(self):
        projection = projection_fixture()
        projection["grounded_profile_snapshot_bindings"][0][
            "profile_snapshot_sha256"
        ] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(
            ContractError,
            r"profile_snapshot_sha256 does not match canonical content",
        ):
            validate_persona_authoring_projection(
                rehash(projection, "projection_sha256")
            )

    def test_oracle_rejects_resealed_unknown_truth_field(self):
        oracle = oracle_fixture()
        oracle["physical_truth"]["helpful_extension"] = "identified"
        with self.assertRaisesRegex(
            ContractError,
            r"oracle\.physical_truth has unknown fields",
        ):
            validate_oracle(rehash(oracle, "oracle_sha256"))

    def test_oracle_rejects_inconsistent_physical_and_epistemic_truth(self):
        oracle = oracle_fixture()
        oracle["epistemic_truth"]["expected_engine_action"] = "abstain"
        with self.assertRaisesRegex(ContractError, "no_miss truth"):
            validate_oracle(rehash(oracle, "oracle_sha256"))

        oracle = oracle_fixture()
        oracle["epistemic_truth"]["identification_status"] = "non_identifiable"
        oracle["epistemic_truth"]["expected_engine_action"] = (
            "profile_snapshot_update"
        )
        with self.assertRaisesRegex(ContractError, "epistemic abstention"):
            validate_oracle(rehash(oracle, "oracle_sha256"))

    def test_evaluation_rejects_resealed_unknown_measure(self):
        evaluation = evaluation_fixture()
        evaluation["measures"]["accuracy"] = 1.0
        with self.assertRaisesRegex(
            ContractError,
            r"evaluation\.measures has unknown fields",
        ):
            validate_synthetic_evaluation(
                rehash(evaluation, "evaluation_sha256")
            )

    def test_evaluation_phase_receipts_are_closed_and_self_hashed(self):
        receipt = evaluation_inputs_fixture()["phase_receipts"][0]
        self.assertEqual(receipt, validate_evaluation_phase_receipt(receipt))
        receipt["helpful_extension"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_evaluation_phase_receipt(
                rehash(receipt, "phase_receipt_sha256")
            )

    def test_hashes_are_prefixed_complete_and_sorted(self):
        proposal = proposal_fixture()
        proposal["operation"]["evidence_sha256"] = ["a" * 64]
        with self.assertRaisesRegex(ContractError, "prefixed digest"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))
        proposal = proposal_fixture()
        proposal["operation"]["evidence_sha256"] = list(reversed(proposal["operation"]["evidence_sha256"]))
        with self.assertRaisesRegex(ContractError, "canonically sorted"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_no_change_requires_null_operation(self):
        proposal = proposal_fixture()
        proposal["proposal_type"] = "no_change"
        with self.assertRaisesRegex(ContractError, "must be null"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_candidate_is_never_registerable_or_activatable(self):
        candidate = candidate_fixture()
        candidate["registration_permitted"] = True
        with self.assertRaisesRegex(ContractError, "registration_permitted"):
            validate_sandbox_candidate_binding(rehash(candidate, "candidate_binding_sha256"))

    def test_candidate_cannot_claim_unverified_package_provenance(self):
        candidate = candidate_fixture()
        candidate["base_panel_binding"]["panel_package_sha256"] = (
            "sha256:" + "9" * 64
        )
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_sandbox_candidate_binding(
                rehash(candidate, "candidate_binding_sha256")
            )

    def test_candidate_operation_and_diff_reject_structural_injection(self):
        candidate = candidate_fixture()
        candidate["applied_operation"]["changed_fields"] = ["planned_weight"]
        with self.assertRaisesRegex(ContractError, "allowed persona behavior fields"):
            validate_sandbox_candidate_binding(
                rehash(candidate, "candidate_binding_sha256")
            )

    def test_candidate_allowed_diff_is_exactly_derived_from_operation(self):
        candidate = candidate_fixture()
        candidate["allowed_diff"]["changed_paths"] = ["$.planned_weight"]
        with self.assertRaisesRegex(
            ContractError,
            "derived from the applied operation|matching grounded-profile",
        ):
            validate_sandbox_candidate_binding(rehash(candidate, "candidate_binding_sha256"))

    def test_candidate_requires_a_grounded_profile_diff(self):
        candidate = candidate_fixture()
        candidate["allowed_diff"]["changed_paths"] = [
            "$.created_at",
            "$.persona_archetypes[finance-pricing-archetype].proof_needs",
            "$.version",
        ]
        with self.assertRaisesRegex(ContractError, "grounded-profile"):
            validate_sandbox_candidate_binding(
                rehash(candidate, "candidate_binding_sha256")
            )

    def test_candidate_records_only_standalone_validation_as_run(self):
        candidate = candidate_fixture()
        candidate["structural_validation"][
            "production_library_registration"
        ] = "passed"
        with self.assertRaisesRegex(ContractError, "not_run_sandbox_only"):
            validate_sandbox_candidate_binding(
                rehash(candidate, "candidate_binding_sha256")
            )

    def test_projection_rejects_duplicate_profile_identifiers(self):
        projection = projection_fixture()
        projection["grounded_profile_snapshot_bindings"].append(
            deepcopy(projection["grounded_profile_snapshot_bindings"][0])
        )
        with self.assertRaisesRegex(ContractError, "unique profile"):
            validate_persona_authoring_projection(
                rehash(projection, "projection_sha256")
            )

    def test_panelist_jobs_are_closed_and_completely_bound_to_results(self):
        from conformance.experimental_calibration_fixtures import exercise_fixture
        from audience_panel_builder.population.experimental_calibration import validate_synthetic_exercise
        exercise = exercise_fixture()
        exercise["panelist_jobs"][0]["panel_id"] = "not-admitted"
        with self.assertRaisesRegex(ContractError, "panel binding is stale"):
            validate_synthetic_exercise(rehash(exercise, "exercise_sha256"))

    def test_each_roster_member_gets_one_isolated_job_per_aggregate_row(self):
        from conformance.experimental_calibration_fixtures import exercise_fixture
        from audience_panel_builder.population.experimental_calibration import validate_synthetic_exercise
        exercise = exercise_fixture()
        expected_run_count = len(SYNTHETIC_SCENARIO_REGISTRY) * len(
            exercise["panel_bindings"]
        )
        self.assertEqual(expected_run_count, len(exercise["run_results"]))
        roster_sizes = {
            roster["exercise_panel_ref"]: len(roster["members"])
            for roster in exercise["panel_rosters"]
        }
        self.assertEqual(
            4 * sum(
                roster_sizes[result["exercise_panel_ref"]]
                for result in exercise["run_results"]
            ),
            len(exercise["panelist_jobs"]),
        )
        self.assertTrue(
            all(
                len(roster["members"]) == 3
                for roster in exercise["panel_rosters"]
            )
        )
        self.assertEqual(exercise, validate_synthetic_exercise(exercise))

    def test_resealed_forged_exercise_reference_is_rejected(self):
        exercise = exercise_fixture()
        old = exercise["panel_bindings"][1]["exercise_panel_ref"]
        forged = "exercise-panel-" + "f" * 24

        def replace(value):
            if isinstance(value, dict):
                return {key: replace(child) for key, child in value.items()}
            if isinstance(value, list):
                return [replace(child) for child in value]
            return forged if value == old else value

        exercise = replace(exercise)
        for roster in exercise["panel_rosters"]:
            roster["roster_sha256"] = None
            roster["roster_sha256"] = rehash(
                roster, "roster_sha256"
            )["roster_sha256"]
        for result in exercise["run_results"]:
            result["result_sha256"] = None
            result["result_sha256"] = rehash(
                result, "result_sha256"
            )["result_sha256"]
        with self.assertRaisesRegex(ContractError, "canonically derived"):
            validate_synthetic_exercise(
                rehash(exercise, "exercise_sha256")
            )

    def test_resealed_forged_maxdiff_output_is_rejected(self):
        exercise = exercise_fixture()
        result = exercise["run_results"][0]
        scoring = result["scoring_and_aggregation"]
        scoring["maxdiff"] = {"forged": "accepted"}
        scoring["numerical_binding"]["maxdiff_output_sha256"] = (
            "sha256:" + hashlib.sha256(
                b'{"forged":"accepted"}'
            ).hexdigest()
        )
        scoring["scoring_sha256"] = None
        scoring["scoring_sha256"] = rehash(
            scoring, "scoring_sha256"
        )["scoring_sha256"]
        result["result_sha256"] = None
        result["result_sha256"] = rehash(
            result, "result_sha256"
        )["result_sha256"]
        with self.assertRaisesRegex(ContractError, "MaxDiff output schema"):
            validate_synthetic_exercise(
                rehash(exercise, "exercise_sha256")
            )

    def test_resealed_forged_assignment_plan_is_rejected(self):
        exercise = exercise_fixture()
        result = exercise["run_results"][0]
        result["assignment_plan"] = {"forged": "accepted"}
        result["assignment_plan_sha256"] = sha256_json(
            result["assignment_plan"]
        )
        result = rehash(result, "result_sha256")
        exercise["run_results"][0] = result
        with self.assertRaisesRegex(
            ContractError,
            "assignment plan is not the exact job projection",
        ):
            validate_synthetic_exercise(
                rehash(exercise, "exercise_sha256")
            )

    def test_resealed_caller_selected_complete_exposure_top_k_is_rejected(self):
        exercise = exercise_fixture()
        result = exercise["run_results"][0]
        scoring = result["scoring_and_aggregation"]
        complete_input = scoring["scoring_inputs"]["complete_exposure"]
        complete_input["top_k"] = 1
        complete_responses = [
            response
            for response in result["responses"]
            if response["record_type"] == "screening_response"
            and response["method"] == "complete_exposure"
        ]
        from audience_lab.complete_exposure import (
            aggregate_complete_exposure,
        )

        scoring["complete_exposure"] = aggregate_complete_exposure(
            complete_responses,
            study_id=complete_input["study_id"],
            creative_ids=complete_input["creative_ids"],
            top_k=complete_input["top_k"],
            segment_weights=complete_input["segment_weights"],
            seed=complete_input["seed"],
        )
        result["scoring_and_aggregation"] = rehash(
            scoring, "scoring_sha256"
        )
        result = rehash(result, "result_sha256")
        exercise["run_results"][0] = result
        with self.assertRaisesRegex(
            ContractError,
            "scoring inputs are not canonically derived",
        ):
            validate_synthetic_exercise(
                rehash(exercise, "exercise_sha256")
            )

    def test_observation_nested_schema_and_numeric_boolean_fail_closed(self):
        observation = outcome_observation_fixture()
        observation["delivery"]["helpful_extension"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_outcome_observation(rehash(observation, "observation_sha256"))
        observation = outcome_observation_fixture()
        observation["delivery"]["impressions"] = True
        with self.assertRaisesRegex(ContractError, "finite number"):
            validate_outcome_observation(rehash(observation, "observation_sha256"))

    def test_proposal_cannot_claim_target_snapshot_authority(self):
        proposal = proposal_fixture()
        proposal["operation"]["target_persona_snapshot_sha256"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_exact_boolean_constants_reject_integer_lookalikes(self):
        proposal = proposal_fixture()
        proposal["production_executable"] = 0
        with self.assertRaisesRegex(ContractError, "exactly"):
            validate_experimental_proposal(rehash(proposal, "proposal_sha256"))

    def test_invalid_enum_stale_hash_and_nonfinite_values_fail_closed(self):
        observation = outcome_observation_fixture()
        observation["completeness"]["status"] = "final-ish"
        with self.assertRaises(ContractError):
            validate_outcome_observation(rehash(observation, "observation_sha256"))
        observation = outcome_observation_fixture()
        observation["observation_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractError, "does not match canonical content"):
            validate_outcome_observation(observation)
        for value in (float("nan"), float("inf"), float("-inf")):
            observation = outcome_observation_fixture()
            observation["delivery"]["impressions"] = value
            with self.assertRaises(ContractError):
                validate_outcome_observation(observation)

    def test_engine_exports_no_private_evaluation_surface(self):
        exports = set(engine.__all__)
        self.assertFalse({"validate_oracle", "validate_synthetic_evaluation", "generate_scenario", "evaluate_scenario"} & exports)

    def test_engine_modules_do_not_reference_private_truth_surface(self):
        package = ROOT / "skills" / "audience-panel-builder" / "scripts" / "audience_panel_builder" / "population" / "experimental_calibration"
        prohibited = {"experimental_persona_calibration_oracle", "oracle", "hidden_oracle", "true_behavioral_miss", "safe_action_set"}
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            tokens = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            } | {
                node.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.alias)
            } | {
                node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            } | {
                node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            self.assertFalse(prohibited & tokens, path)

    def test_inputs_are_not_mutated(self):
        proposal = proposal_fixture()
        original = deepcopy(proposal)
        validate_experimental_proposal(proposal)
        self.assertEqual(original, proposal)


if __name__ == "__main__":
    unittest.main()
