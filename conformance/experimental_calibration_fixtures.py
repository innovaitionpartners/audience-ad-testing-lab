"""Fictional, aggregate fixtures for calibration-contract conformance tests."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def rehash(document: dict[str, object], field: str) -> dict[str, object]:
    result = deepcopy(document)
    result[field] = None
    result[field] = "sha256:" + hashlib.sha256(_canonical(result)).hexdigest()
    return result


def digest(letter: str = "a") -> str:
    return "sha256:" + letter * 64


def _digest_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def raw_platform_export_fixture(
    platform: str,
    scenario_id: str = "known-proof-need-miss",
) -> bytes:
    partition = "sealed" if scenario_id.startswith("non-identifiable") else "open"
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
        / partition
        / scenario_id
        / "raw"
        / platform
        / "daily-aggregates.json"
    )
    return path.read_bytes()


def creative_attribute_inputs(
    *,
    registered_at: str = "2026-07-01T00:00:00Z",
    earliest_outcome_accessed_at: str = "2026-07-02T00:00:00Z",
) -> dict[str, object]:
    creative_bindings = [
        {
            "creative_id": creative_id,
            "asset_sha256": digest(letter),
        }
        for creative_id, letter in (
            ("ease-of-use", "1"),
            ("peer-validation", "2"),
            ("quantified-payback", "3"),
            ("strategic-control", "4"),
        )
    ]
    return {
        "registry_id": "fictional-creative-attribute-registry",
        "registered_at": registered_at,
        "creative_bindings": creative_bindings,
        "attribute_definitions": [
            {
                "attribute_id": "dominant-background-color",
                "attribute_version": "1.0.0",
                "attribute_kind": "objective",
                "value_type": "string",
                "behavioral_hypothesis": None,
            },
            {
                "attribute_id": "quantified-payback-proof",
                "attribute_version": "1.0.0",
                "attribute_kind": "interpretive",
                "value_type": "boolean",
                "behavioral_hypothesis": {
                    "hypothesis_id": "quantified-payback-proof-need",
                    "target_persona_id": "finance-pricing-archetype",
                    "target_persona_field": "proof_needs",
                    "proposed_value": [
                        "Quantified payback and implementation-risk evidence"
                    ],
                    "rationale_template": (
                        "Repeated pre-registered contrasts support a proof-needs update."
                    ),
                    "abstention_conditions": [
                        "Evidence is not repeatable across independent experiments."
                    ],
                },
            },
        ],
        "creative_attributes": [
            {
                "creative_id": creative["creative_id"],
                "asset_sha256": creative["asset_sha256"],
                "attribute_id": attribute_id,
                "attribute_version": "1.0.0",
                "method_id": method_id,
                "value": value,
                "annotator": annotator,
                "confidence": confidence,
                "ambiguity": ambiguity,
                "review_status": "approved",
                "annotated_at": "2026-06-30T00:00:00Z",
            }
            for creative in creative_bindings
            for attribute_id, method_id, value, annotator, confidence, ambiguity in (
                (
                    "dominant-background-color",
                    "deterministic-color-v1",
                    "gray",
                    "deterministic-color-process",
                    1.0,
                    "none",
                ),
                (
                    "quantified-payback-proof",
                    "pre-outcome-human-review-v1",
                    creative["creative_id"] == "quantified-payback",
                    "fictional-reviewer",
                    0.9,
                    "low",
                ),
            )
        ],
        "annotation_methods": [
            {
                "method_id": "deterministic-color-v1",
                "method_version": "1.0.0",
                "method_kind": "deterministic",
                "process_identity": "deterministic-color-process",
            },
            {
                "method_id": "pre-outcome-human-review-v1",
                "method_version": "1.0.0",
                "method_kind": "human_review",
                "process_identity": "fictional-reviewer",
            },
        ],
        "reviewed_by": "fictional-reviewer",
        "reviewed_at": registered_at,
        "earliest_outcome_accessed_at": earliest_outcome_accessed_at,
    }


def _parameter_set_fixture(scenario_id: str) -> dict[str, object]:
    return rehash(
        {
            "parameter_set_id": f"fictional-{scenario_id}-parameters",
            "parameter_version": "1.0.0",
            "parameter_values": [
                {"name": "enabled", "value_type": "boolean", "value": True},
                {"name": "label", "value_type": "string", "value": "fictional"},
                {"name": "rate", "value_type": "number", "value": 1.0},
                {"name": "repetitions", "value_type": "integer", "value": 1},
            ],
            "parameters_sha256": None,
        },
        "parameters_sha256",
    )


def study_manifest_fixture() -> dict[str, object]:
    core_families = [
        {
            "scenario_id": scenario_id,
            "dgp_id": dgp_id,
            "dgp_version": "1.0.0",
            "seed": seed,
            "repetitions": 1,
            "parameters": _parameter_set_fixture(scenario_id),
            "partition": partition,
        }
        for scenario_id, dgp_id, seed, partition in (
            ("null-effect", "randomized-block-null", 7, "open"),
            ("known-proof-need-miss", "heterogeneous-cfo-proof-need", 8, "open"),
            ("non-identifiable-twin-a", "non-identifiable-twin-a", 9, "sealed"),
            ("non-identifiable-twin-b", "non-identifiable-twin-b", 9, "sealed"),
        )
    ]
    core_ids = {row["scenario_id"] for row in core_families}
    scenario_families = [
        *core_families,
        *[
            deepcopy(row)
            for row in _generated_study_manifest_fixture()[
                "scenario_families"
            ]
            if row["scenario_id"] not in core_ids
        ],
    ]
    result: dict[str, object] = {
        "schema_version": "synthetic-persona-behavior-study-manifest-v1",
        "study_id": "fictional-study", "created_at": "2026-07-20T00:00:00Z",
        "purpose": "verification_only", "generator_version": "1.0.0",
        "scenario_families": scenario_families,
        "estimands": [{"estimand_id": "fictional-estimand"}],
        "parameter_grid": {"rate": [1]},
        "seeds": list(
            dict.fromkeys(row["seed"] for row in scenario_families)
        ),
        "repetitions": 1,
        "monte_carlo_error_targets": {"maximum": 0.01, "method_version": "deterministic-batch-quantile-mcse-v1", "batch_count": 10, "batch_partition_policy": "equal_contiguous_replicate_batches", "quantile_interpolation": "linear", "reported_measures": ["bootstrap_mean", "interval_lower", "interval_upper"]},
        "diagnosis_method": {"method_version": "blocked-contrast-bootstrap-v1", "contrast_source": "registered_numerator_denominator", "block_weighting": "equal", "experiment_weighting": "equal", "minimum_complete_blocks_per_experiment": 6, "minimum_independent_experiments": 2, "interval_method": "deterministic_percentile_block_bootstrap", "interval_level": 0.95, "bootstrap_repetitions": 100, "bootstrap_seed": 7, "minimum_practical_effect": 0.01, "minimum_practical_effect_rule": "directional_point_estimate_strictly_exceeds_threshold", "missingness_policy": "incomplete_block_ineligible", "maturity_policy": "finalized_only", "observational_policy": "descriptive_only", "early_stopping_permitted": False},
        "synthetic_response_adapter": {"adapter_id": "frozen-synthetic-panelist-response", "version": "1.0.0", "source_sha256": digest("9"), "feature_allowlist": ["creative_attributes", "experiment_design", "persona_snapshot", "study_manifest"], "deterministic_tie_rule": "score-descending-creative-id-ascending", "seed": 73021}, "stopping_rule": {"rule": "none"}, "performance_measures": ["accuracy"], "manifest_sha256": None,
    }
    return rehash(result, "manifest_sha256")


def _panel_binding(persona_id: str = "finance-pricing-archetype") -> dict[str, object]:
    return {"panel_id": "fictional-panel", "panel_version": "1.0.0", "panel_sha256": digest("a"), "persona_id": persona_id, "persona_snapshot_sha256": digest("b")}


def _study_binding() -> dict[str, object]:
    return {"study_id": "fictional-study", "study_manifest_sha256": digest("c")}


def generator_outcome_observation_fixture() -> dict[str, object]:
    result: dict[str, object] = {"schema_version": "persona-behavior-outcome-observation-v1", "observation_id": "fictional-observation", "evidence_origin": "synthetic_fixture_only", "synthetic_study_binding": _study_binding(), "source": {"platform": "fictional"}, "reporting_context": {"timezone": "UTC", "currency": "USD", "report_time_basis": "conversion-date", "maturity": "finalized"}, "entity_identity": {"account": "fictional"}, "experiment_binding": {"experiment": "fictional-experiment", "campaign": "fictional-campaign", "block": "fictional-block", "batch": "fictional-batch", "arm": "fictional-treatment", "reference_arm": "fictional-reference"}, "creative_binding": {"creative": "fictional"}, "creative_attribute_binding": {"registry": "fictional-registry", "hypothesis": "fictional-hypothesis"}, "audience_scope": {"segment": "finance", "objective": "lead-generation", "placement": "fictional-feed"}, "delivery": {"impressions": 10}, "traffic": {"clicks": 1}, "outcome_events": {"conversions": 0}, "measurement_definition": {"metric": "conversion", "registered_numerator": "finalized-leads", "registered_denominator": "impressions", "attribution_click_window": "7-day", "attribution_view_window": "1-day", "attribution_engaged_view_window": "1-day-engaged-view", "attribution_model": "last-touch"}, "denominators": {"kind": "impressions"}, "completeness": {"status": "finalized"}, "design_quality": {"design": "randomized"}, "limitations": [], "observation_sha256": None}
    return rehash(result, "observation_sha256")


def outcome_observation_fixture() -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.adapters import (
        normalize_platform_export,
    )
    from audience_panel_builder.population.experimental_calibration.attributes import (
        build_creative_attribute_registry,
    )

    raw = raw_platform_export_fixture("meta")
    manifest_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
        / "study-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    registry = build_creative_attribute_registry(**creative_attribute_inputs())
    return normalize_platform_export(
        platform="meta",
        raw_export_bytes=raw,
        source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
        study_manifest=manifest,
        creative_attribute_registry=registry,
    )[0]


def proposal_fixture() -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.proposal import (
        build_experimental_proposal,
    )

    return build_experimental_proposal(**proposal_inputs_fixture())


def _applied_operation_fixture() -> dict[str, object]:
    return {
        "operation_type": "profile_snapshot_update",
        "target_persona_id": "finance-pricing-archetype",
        "target_persona_snapshot_sha256": digest("b"),
        "hypothesis_id": "quantified-payback-proof-need",
        "before": {"proof_needs": ["Pricing and returns mechanism"]},
        "proposed_after": {
            "proof_needs": [
                "Quantified payback and implementation-risk evidence"
            ]
        },
        "changed_fields": ["proof_needs"],
        "evidence_sha256": [digest("d"), digest("e")],
        "creative_attribute_registry_sha256": digest("f"),
        "rationale": "A fictional predeclared hypothesis.",
        "constraints": ["fictional-only"],
        "reversibility": "sandbox_reversible",
    }


def candidate_fixture() -> dict[str, object]:
    operation = _applied_operation_fixture()
    base_binding = _panel_binding()
    candidate_panel_binding = _panel_binding()
    candidate_panel_binding["panel_version"] = "1.1.0"
    candidate_panel_binding["panel_sha256"] = digest("c")
    candidate_panel_binding["persona_snapshot_sha256"] = digest("c")
    result: dict[str, object] = {
        "schema_version": "experimental-persona-panel-candidate-v1",
        "candidate_id": "fictional-candidate",
        "created_at": "2026-07-20T00:00:00Z",
        "status": "sandbox_only",
        "evidence_origin": "synthetic_fixture_only",
        "real_world_validation_status": "not_evaluated",
        "registration_permitted": False,
        "activation_permitted": False,
        "active_panel_mutation_permitted": False,
        "base_panel_binding": base_binding,
        "proposal_binding": {
            "proposal_id": "fictional-proposal",
            "proposal_sha256": digest("b"),
        },
        "candidate_panel_binding": candidate_panel_binding,
        "base_authoring_projection_binding": {
            "projection_id": "base-projection",
            "projection_sha256": digest("d"),
        },
        "candidate_authoring_projection_binding": {
            "projection_id": "candidate-projection",
            "projection_sha256": digest("e"),
        },
        "applied_operation": operation,
        "allowed_diff": {
            "changed_paths": [
                "$.created_at",
                "$.grounded_context_profiles[profile-one].profile_snapshot.proof_needs",
                "$.persona_archetypes[finance-pricing-archetype].proof_needs",
                "$.updated_at",
                "$.version",
            ],
        },
        "forbidden_diff_check": {"passed": True, "forbidden_paths": []},
        "structural_validation": {
            "standalone_saved_panel_v3": "passed",
            "production_workflow_state": "not_run_sandbox_only",
            "production_construction_audit": "not_run_sandbox_only",
            "production_package_approval": "not_run_sandbox_only",
            "production_library_registration": "not_run_sandbox_only",
        },
        "synthetic_evaluation_requirement": {"required": True},
        "limitations": ["not registerable"],
        "candidate_binding_sha256": None,
    }
    return rehash(result, "candidate_binding_sha256")


def candidate_base_panel_fixture() -> dict[str, object]:
    """Return a standalone-v3-valid fictional panel with the Task 5 persona."""

    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "audience-package-v3"
        / "approved-package-inputs.json"
    )
    payload = json.loads(path.read_text())
    panel = deepcopy(payload["bundles"]["tier_1"]["panel"])
    panel["updated_at"] = "2026-07-03T12:00:00Z"
    persona = panel["persona_archetypes"][0]
    persona["persona_archetype_id"] = "finance-pricing-archetype"
    persona["display_name"] = "Fictional finance and pricing leader"
    persona["anxieties"] = ["Implementation and commercial risk"]
    persona["decision_context"] = "Evaluating fictional pricing software"
    persona["motivations"] = ["Improve planning confidence"]
    persona["proof_needs"] = ["Pricing and returns mechanism"]
    persona["role_context"] = "Fictional CFO or finance leader"
    snapshot = {
        field: deepcopy(persona[field])
        for field in (
            "anxieties",
            "decision_context",
            "motivations",
            "proof_needs",
            "role_context",
        )
    }
    for profile in panel["grounded_context_profiles"]:
        profile["persona_archetype_id"] = "finance-pricing-archetype"
        profile["profile_snapshot"] = deepcopy(snapshot)
    return panel


def candidate_proposal_fixture(
    base_panel: dict[str, object] | None = None,
    *,
    scenario_id: str = "known-proof-need-miss",
) -> dict[str, object]:
    """Bind the Task 5 proposal intent to an exact fictional v3 base panel."""

    panel = candidate_base_panel_fixture() if base_panel is None else deepcopy(base_panel)
    inputs = candidate_proposal_inputs_fixture(
        panel,
        scenario_id=scenario_id,
    )
    from audience_panel_builder.population.experimental_calibration.proposal import (
        build_experimental_proposal,
    )

    return build_experimental_proposal(**inputs)


def candidate_proposal_inputs_fixture(
    base_panel: dict[str, object] | None = None,
    *,
    scenario_id: str = "known-proof-need-miss",
) -> dict[str, object]:
    """Return complete frozen Task 5 inputs bound to the exact v3 panel."""

    panel = candidate_base_panel_fixture() if base_panel is None else deepcopy(base_panel)
    inputs = proposal_inputs_fixture(scenario_id=scenario_id)
    persona = next(
        row
        for row in panel["persona_archetypes"]
        if row["persona_archetype_id"] == "finance-pricing-archetype"
    )
    snapshot = {
        field: deepcopy(persona[field])
        for field in (
            "anxieties",
            "decision_context",
            "motivations",
            "proof_needs",
            "role_context",
        )
    }
    base_binding = {
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "panel_sha256": "sha256:" + hashlib.sha256(_canonical(panel)).hexdigest(),
        "persona_id": "finance-pricing-archetype",
        "persona_snapshot_sha256": (
            "sha256:" + hashlib.sha256(_canonical(snapshot)).hexdigest()
        ),
    }
    from audience_panel_builder.population.experimental_calibration.diagnosis import (
        diagnose_persona_behavior,
    )

    prior_diagnosis = inputs["diagnosis"]
    inputs["base_panel_binding"] = base_binding
    inputs["diagnosis"] = diagnose_persona_behavior(
        base_panel_binding=base_binding,
        study_manifest=inputs["study_manifest"],
        scenario_manifests=inputs["scenario_manifests"],
        experiment_designs=inputs["experiment_designs"],
        evidence_library_snapshot=inputs["evidence_library_snapshot"],
        evidence_head_receipt=inputs["evidence_head_receipt"],
        attribute_registry=inputs["attribute_registry"],
        alternative_causes=inputs["alternative_causes"],
        diagnosis_id=prior_diagnosis["diagnosis_id"],
        diagnosed_at=prior_diagnosis["diagnosed_at"],
    )
    return inputs


def valid_candidate_inputs(
    *,
    scenario_id: str = "known-proof-need-miss",
) -> dict[str, object]:
    panel = candidate_base_panel_fixture()
    proposal_inputs = candidate_proposal_inputs_fixture(
        panel,
        scenario_id=scenario_id,
    )
    from audience_panel_builder.population.experimental_calibration.proposal import (
        build_experimental_proposal,
    )

    return {
        "base_panel": panel,
        "proposal": build_experimental_proposal(**proposal_inputs),
        **{
            key: deepcopy(proposal_inputs[key])
            for key in (
                "study_manifest",
                "scenario_manifests",
                "experiment_designs",
                "diagnosis",
                "attribute_registry",
                "evidence_library_snapshot",
                "evidence_head_receipt",
                "alternative_causes",
            )
        },
        "candidate_id": "candidate-001",
        "candidate_version": "1.1.0",
        "created_at": "2026-07-21T00:00:00Z",
    }


@lru_cache(maxsize=8)
def _materialized_candidate_envelope_cached(
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.candidate import (
        materialize_sandbox_candidate,
    )

    inputs = valid_candidate_inputs()
    inputs["candidate_id"] = candidate_id
    inputs["candidate_version"] = candidate_version
    inputs["created_at"] = created_at
    return materialize_sandbox_candidate(**inputs)


def materialized_candidate_envelope_fixture(
    *,
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    """Return one complete, separately versioned Task 6 materialized graph."""

    return deepcopy(
        _materialized_candidate_envelope_cached(
            candidate_id,
            candidate_version,
            created_at,
        )
    )


def public_scenario_inputs_fixture() -> dict[str, object]:
    """Load only the exact public open/sealed scenario tree."""

    from audience_panel_builder.population.experimental_calibration.exercise import (
        load_public_scenario_inputs,
    )

    source = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
    )
    with tempfile.TemporaryDirectory() as raw:
        public_root = Path(raw) / "public-scenarios"
        public_root.mkdir()
        for partition in ("open", "sealed"):
            shutil.copytree(source / partition, public_root / partition)
        return load_public_scenario_inputs(public_root)


def _materialized_candidate_from_bundle(
    bundle: Path,
) -> dict[str, object]:
    """Reconstruct the exact Task 6 materialized graph from sealed bytes."""

    members = {
        "base_authoring_projection": "base-persona-authoring-projection.json",
        "base_persona_snapshot": "base-persona-snapshot.json",
        "candidate_authoring_projection": (
            "candidate-persona-authoring-projection.json"
        ),
        "candidate_binding": "experimental-candidate-binding.json",
        "candidate_panel": "candidate-audience-panel.json",
        "candidate_persona_snapshot": "candidate-persona-snapshot.json",
        "experimental_proposal": "experimental-proposal.json",
        "persona_behavior_diff": "persona-behavior-diff.json",
        "standalone_panel_validation": "standalone-panel-validation.json",
    }
    return {
        field: json.loads((bundle / filename).read_bytes())
        for field, filename in members.items()
    }


@lru_cache(maxsize=8)
def _sealed_candidate_envelope_cached(
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    """Run the registered Task 6 stage and retain its exact public seal."""

    from experimental_persona_calibration_oracle.sandbox import (
        run_engine_in_private_stage,
    )

    inputs = valid_candidate_inputs()
    inputs["candidate_id"] = candidate_id
    inputs["candidate_version"] = candidate_version
    inputs["created_at"] = created_at
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        oracle = root / "oracle"
        oracle.mkdir()
        (oracle / "hidden-oracle.json").write_bytes(
            _canonical({"private": "denied"})
        )
        validated_arguments: dict[str, object] = {}
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
            path.write_bytes(_canonical(inputs[key]))
            validated_arguments[key] = path
        validated_arguments.update(
            {
                "candidate_id": candidate_id,
                "candidate_version": candidate_version,
                "created_at": created_at,
            }
        )
        released = root / "released"
        run_engine_in_private_stage(
            engine_entrypoint="materialize",
            validated_arguments=validated_arguments,
            oracle_denied_roots=[oracle],
            output_dir=released,
        )
        bundle = released / "result"
        return {
            "materialized_candidate": _materialized_candidate_from_bundle(
                bundle
            ),
            "sealed_bundle_manifest": json.loads(
                (bundle / "bundle-manifest.json").read_bytes()
            ),
            "candidate_seal_receipt": json.loads(
                (released / "phase-execution-receipt.json").read_bytes()
            ),
        }


def sealed_candidate_envelope_fixture(
    *,
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    """Return a candidate with its real registered Task 6 publication seal."""

    return deepcopy(
        _sealed_candidate_envelope_cached(
            candidate_id,
            candidate_version,
            created_at,
        )
    )


def exercise_inputs_fixture() -> dict[str, object]:
    """Return the plural frozen Task 7 input graph."""

    candidate_inputs = valid_candidate_inputs()

    return {
        "study_manifest": deepcopy(candidate_inputs["study_manifest"]),
        "public_scenario_inputs": public_scenario_inputs_fixture(),
        "creative_attribute_registry": deepcopy(
            candidate_inputs["attribute_registry"]
        ),
        "base_panel": deepcopy(candidate_inputs["base_panel"]),
        "candidate_bindings_and_panels": [
            sealed_candidate_envelope_fixture(
                candidate_id="candidate-001",
                candidate_version="1.1.0",
                created_at="2026-07-21T00:00:00Z",
            ),
            sealed_candidate_envelope_fixture(
                candidate_id="candidate-002",
                candidate_version="1.2.0",
                created_at="2026-07-22T00:00:00Z",
            ),
        ],
        "exercise_id": "fictional-synthetic-panel-exercise",
        "exercised_at": "2026-07-30T00:00:00Z",
    }


@lru_cache(maxsize=1)
def _exercise_fixture_cached() -> dict[str, object]:
    inputs = exercise_inputs_fixture()
    from audience_panel_builder.population.experimental_calibration.exercise import (
        _ATTEMPT_POLICY,
        _build_job,
        _finalist_job,
        _panel_authority,
        _panel_roster,
        authenticate_frozen_adapter_source,
        project_adapter_output_to_ad_testing_response,
    )
    from audience_lab.complete_exposure import aggregate_complete_exposure
    from audience_lab.finalists import aggregate_finalists

    manifest = inputs["study_manifest"]
    registry = inputs["creative_attribute_registry"]
    scenarios = inputs["public_scenario_inputs"]
    panel_bindings, panels = _panel_authority(
        inputs["base_panel"],
        inputs["candidate_bindings_and_panels"],
    )
    rosters = [
        _panel_roster(panels[row["exercise_panel_ref"]], row)
        for row in panel_bindings
    ]
    roster_by_ref = {row["exercise_panel_ref"]: row for row in rosters}
    panelist_jobs = []
    run_results = []
    creative_ids = [
        "ease-of-use",
        "peer-validation",
        "quantified-payback",
        "strategic-control",
    ]
    for scenario in scenarios:
        scenario_manifest = scenario["scenario_manifest"]
        scenario_id = scenario_manifest["scenario_binding"]["scenario_id"]
        design = scenario["experiment_design"]
        family = next(
            row
            for row in manifest["scenario_families"]
            if row["scenario_id"] == scenario_id
        )
        for repetition in range(family["repetitions"]):
            for binding in panel_bindings:
                exercise_ref = binding["exercise_panel_ref"]
                roster = roster_by_ref[exercise_ref]
                jobs = []
                responses = []
                outputs = []
                for member in roster["members"]:
                    assignment = {
                        "segment_id": member["segment_id"],
                        "variation_ids": list(creative_ids),
                        "shown_order": list(reversed(creative_ids)),
                    }
                    study_id = (
                        f"{manifest['study_id']}-{scenario_id}-"
                        f"r{repetition}-{exercise_ref}"
                    )
                    complete_job = _build_job(
                        study_id=study_id,
                        scenario_id=scenario_id,
                        repetition=repetition,
                        panel_binding=binding,
                        member=member,
                        assignment=assignment,
                    )
                    maxdiff_job = _build_job(
                        study_id=study_id,
                        scenario_id=scenario_id,
                        repetition=repetition,
                        panel_binding=binding,
                        member=member,
                        assignment=assignment,
                        phase="maxdiff-screening",
                        record_type="screening_response",
                        method="partial_exposure_maxdiff",
                    )
                    boundary_ids = creative_ids[:2]
                    boundary_job = _build_job(
                        study_id=study_id,
                        scenario_id=scenario_id,
                        repetition=repetition,
                        panel_binding=binding,
                        member=member,
                        assignment=assignment,
                        phase="pairwise-boundary",
                        record_type="boundary_response",
                        method="partial_exposure_maxdiff",
                        variation_ids=boundary_ids,
                        shown_order=boundary_ids,
                    )
                    finalist_ids = creative_ids[:2]
                    finalist_job = _finalist_job(
                        complete_job,
                        creative_ids,
                        finalist_ids,
                    )
                    phase_jobs = (
                        ("complete-exposure", complete_job),
                        ("maxdiff-screening", maxdiff_job),
                        ("pairwise-boundary", boundary_job),
                        ("finalist-verbatim", finalist_job),
                    )
                    for phase, job in phase_jobs:
                        ranked_ids = list(job["variation_ids"])
                        output = {
                            "adapter_id": (
                                "frozen-synthetic-panelist-response"
                            ),
                            "adapter_version": "1.0.0",
                            "dispatch_id": job["dispatch_id"],
                            "tie_rule": (
                                "score-descending-creative-id-ascending"
                            ),
                            "ranking": [
                                {
                                    "position": position,
                                    "creative_id": creative_id,
                                    "score": len(ranked_ids) - position,
                                }
                                for position, creative_id in enumerate(
                                    ranked_ids, 1
                                )
                            ],
                        }
                        response = (
                            project_adapter_output_to_ad_testing_response(
                                adapter_output=output,
                                validated_job=job,
                                frozen_attempt_policy=_ATTEMPT_POLICY,
                            )
                        )
                        jobs.append(job)
                        responses.append(response)
                        outputs.append(output)
                        panelist_jobs.append(
                            {
                                "dispatch_id": job["dispatch_id"],
                                "phase": phase,
                                "scenario_id": scenario_id,
                                "repetition": repetition,
                                "exercise_panel_ref": exercise_ref,
                                "panel_id": binding["panel_id"],
                                "panel_version": binding["panel_version"],
                                "panelist_id": member["panelist_id"],
                                "membership_id": member["membership_id"],
                                "worker_context_isolation": "isolated",
                                "job": job,
                                "job_sha256": (
                                    "sha256:"
                                    + hashlib.sha256(
                                        _canonical(job)
                                    ).hexdigest()
                                ),
                            }
                        )
                response_hashes = [
                    "sha256:" + hashlib.sha256(_canonical(row)).hexdigest()
                    for row in responses
                ]
                complete_responses = [
                    row
                    for row in responses
                    if row["record_type"] == "screening_response"
                    and row["method"] == "complete_exposure"
                ]
                maxdiff_responses = [
                    row
                    for row in responses
                    if row["record_type"] == "screening_response"
                    and row["method"] == "partial_exposure_maxdiff"
                ]
                boundary_responses = [
                    row
                    for row in responses
                    if row["record_type"] == "boundary_response"
                ]
                finalists = [
                    row
                    for row in responses
                    if row["record_type"] == "finalist_response"
                ]
                segment_counts: dict[str, int] = {}
                for response in complete_responses:
                    segment_id = str(response["segment_id"])
                    segment_counts[segment_id] = (
                        segment_counts.get(segment_id, 0) + 1
                    )
                total = len(complete_responses)
                segment_weights = {
                    segment_id: count / total
                    for segment_id, count in sorted(
                        segment_counts.items()
                    )
                }
                seed = int(family["seed"]) + repetition
                complete = aggregate_complete_exposure(
                    complete_responses,
                    study_id=study_id,
                    creative_ids=creative_ids,
                    top_k=2,
                    segment_weights=segment_weights,
                    seed=seed,
                )
                finalist_manifest = {
                    "study_id": study_id,
                    "method": "complete_exposure",
                    "requested_shortlist_size": 2,
                    "outputs": {
                        "creative_asset_hashes": {
                            creative_id: (
                                "sha256:"
                                + hashlib.sha256(
                                    creative_id.encode("utf-8")
                                ).hexdigest()
                            )
                            for creative_id in creative_ids
                        }
                    },
                }
                screening = {
                    "study_id": study_id,
                    "method": "complete_exposure",
                    "validity_status": "valid",
                    "selection_status": "resolved",
                    "proposed_finalist_ids": creative_ids[:2],
                }
                approval = {
                    "study_id": study_id,
                    "method": "complete_exposure",
                    "approved_finalist_ids": creative_ids[:2],
                    "roster_decision": {
                        "status": "approved",
                        "approved_at": "2026-07-30T00:00:00Z",
                        "approved_by": "synthetic-sandbox-harness",
                        "override": False,
                        "changed_after_saliency_reveal": False,
                    },
                }
                finalist_aggregation = aggregate_finalists(
                    finalist_manifest,
                    screening,
                    approval,
                    finalists,
                )
                scoring_inputs = {
                    "complete_exposure": {
                        "study_id": study_id,
                        "creative_ids": list(creative_ids),
                        "top_k": 2,
                        "segment_weights": segment_weights,
                        "seed": seed,
                        "response_sha256s": [
                            "sha256:"
                            + hashlib.sha256(_canonical(row)).hexdigest()
                            for row in complete_responses
                        ],
                    },
                    "maxdiff": {
                        "config": {
                            "penalty_lambda": 0.1,
                            "optimizer_tolerance": 1e-8,
                            "bootstrap_count": 2000,
                            "successful_fit_floor": 0.95,
                            "clear_finalist_threshold": 0.9,
                            "clear_non_finalist_threshold": 0.1,
                            "seed": seed,
                        },
                        "creative_ids": list(creative_ids),
                        "segment_weights": segment_weights,
                        "response_sha256s": [
                            "sha256:"
                            + hashlib.sha256(_canonical(row)).hexdigest()
                            for row in maxdiff_responses
                        ],
                    },
                    "pairwise_boundary": {
                        "config": {
                            "tie_parameter": 0.4,
                            "penalty_lambda": 0.1,
                            "optimizer_tolerance": 1e-8,
                            "bootstrap_count": 20,
                            "successful_fit_floor": 0.95,
                            "seed": seed,
                        },
                        "candidate_ids": list(creative_ids),
                        "target_count": 2,
                        "segment_weights": segment_weights,
                        "boundary_jobs_per_wave": len(
                            boundary_responses
                        ),
                        "boundary_waves_max": 1,
                        "boundary_reserved": len(boundary_responses),
                        "available_boundary_reserve": len(
                            boundary_responses
                        ),
                        "finalist_reserved": len(finalists),
                        "response_sha256s": [
                            "sha256:"
                            + hashlib.sha256(_canonical(row)).hexdigest()
                            for row in boundary_responses
                        ],
                    },
                    "finalist_aggregation": {
                        "manifest": finalist_manifest,
                        "screening_result": screening,
                        "approval": approval,
                        "response_sha256s": [
                            "sha256:"
                            + hashlib.sha256(_canonical(row)).hexdigest()
                            for row in finalists
                        ],
                    },
                }
                maxdiff = {
                    "utilities": {
                        creative_id: float(len(creative_ids) - position)
                        for position, creative_id in enumerate(
                            creative_ids, 1
                        )
                    },
                    "ranked_ids": list(creative_ids),
                    "success": True,
                    "connected": True,
                    "identified": True,
                    "converged": True,
                    "loss": 0.0,
                    "projected_gradient_norm": 0.0,
                    "iterations": 1,
                    "message": "contract-only dependency-deferred fixture",
                    "observation_count": len(maxdiff_responses),
                    "creative_count": len(creative_ids),
                }
                pairwise_boundary = {
                    "status": "resolved",
                    "status_reasons": [],
                    "estimand": "protocol-relative",
                    "stability_diagnostic": "contract-only",
                    "boundary_candidate_ids": list(creative_ids[:2]),
                    "frozen_clear_finalist_ids": [],
                    "frozen_clear_non_finalist_ids": creative_ids[2:],
                    "selected_boundary_ids": list(creative_ids[:2]),
                    "proposed_finalist_ids": list(creative_ids[:2]),
                    "utilities": maxdiff["utilities"],
                    "ranked_ids": list(creative_ids),
                    "conditional_inclusion_frequencies": {
                        creative_id: 0.5 for creative_id in creative_ids
                    },
                    "classifications": {
                        creative_id: (
                            "boundary_candidate"
                            if creative_id in creative_ids[:2]
                            else "clear_non_finalist"
                        )
                        for creative_id in creative_ids
                    },
                    "model_diagnostics": {},
                    "decision_audit": {},
                    "interpretation_limits": [
                        "Contract-only numerical fixture."
                    ],
                }
                numerical_binding = {
                    "maxdiff_input_sha256": (
                        "sha256:"
                        + hashlib.sha256(
                            _canonical(scoring_inputs["maxdiff"])
                        ).hexdigest()
                    ),
                    "maxdiff_output_sha256": (
                        "sha256:"
                        + hashlib.sha256(_canonical(maxdiff)).hexdigest()
                    ),
                    "pairwise_input_sha256": (
                        "sha256:"
                        + hashlib.sha256(
                            _canonical(scoring_inputs["pairwise_boundary"])
                        ).hexdigest()
                    ),
                    "pairwise_output_sha256": (
                        "sha256:"
                        + hashlib.sha256(
                            _canonical(pairwise_boundary)
                        ).hexdigest()
                    ),
                    "dependency_complete_recomputation_required": True,
                }
                scoring = {
                    "scoring_inputs": scoring_inputs,
                    "complete_exposure": complete,
                    "maxdiff": maxdiff,
                    "pairwise_boundary": pairwise_boundary,
                    "finalist_aggregation": finalist_aggregation,
                    "verbatim_projection": {
                        "capture": "frozen_adapter_ranking_projection",
                        "exact_response_sha256": response_hashes,
                        "reaction_records": [
                            deepcopy(reaction)
                            for response in responses
                            for reaction in response.get(
                                "per_creative_reactions",
                                response.get("finalist_reviews", []),
                            )
                        ],
                    },
                    "numerical_binding": numerical_binding,
                    "scoring_sha256": None,
                }
                scoring = rehash(scoring, "scoring_sha256")
                member_count = len(roster["members"])
                capacity = {
                    "screening_planned": 2 * member_count,
                    "boundary_reserved": member_count,
                    "finalist_reserved": member_count,
                    "required_total": 4 * member_count,
                    "ceiling": 4 * member_count,
                    "ceiling_satisfied": True,
                }
                from audience_panel_builder.population.experimental_calibration.contracts import (
                    _exercise_assignment_projection,
                )

                assignment_plan = _exercise_assignment_projection(jobs)
                result = {
                    "scenario_family_id": scenario_id,
                    "scenario_id": scenario_id,
                    "partition": scenario_manifest["partition"],
                    "repetition": repetition,
                    "exercise_panel_ref": exercise_ref,
                    "panel_id": binding["panel_id"],
                    "panel_version": binding["panel_version"],
                    "panel_kind": binding["panel_kind"],
                    "candidate_id": binding["candidate_id"],
                    "scenario_manifest_sha256": scenario_manifest[
                        "manifest_sha256"
                    ],
                    "experiment_design_sha256": design["design_sha256"],
                    "admitted_public_files_sha256": (
                        "sha256:"
                        + hashlib.sha256(
                            _canonical(scenario["admitted_public_files"])
                        ).hexdigest()
                    ),
                    "assignment_plan": assignment_plan,
                    "assignment_plan_sha256": (
                        "sha256:"
                        + hashlib.sha256(_canonical(assignment_plan)).hexdigest()
                    ),
                    "capacity_plan": capacity,
                    "capacity_plan_sha256": (
                        "sha256:"
                        + hashlib.sha256(_canonical(capacity)).hexdigest()
                    ),
                    "job_sha256s": [
                        "sha256:" + hashlib.sha256(_canonical(job)).hexdigest()
                        for job in jobs
                    ],
                    "adapter_outputs": outputs,
                    "adapter_output_sha256s": [
                        "sha256:"
                        + hashlib.sha256(_canonical(output)).hexdigest()
                        for output in outputs
                    ],
                    "responses": responses,
                    "response_sha256s": response_hashes,
                    "finalist_responses": finalists,
                    "finalist_response_sha256s": [
                        "sha256:"
                        + hashlib.sha256(_canonical(response)).hexdigest()
                        for response in finalists
                    ],
                    "scoring_and_aggregation": scoring,
                    "result_sha256": None,
                }
                run_results.append(rehash(result, "result_sha256"))
    result = {
        "schema_version": "synthetic-persona-behavior-exercise-v1",
        "exercise_id": "fictional-synthetic-panel-exercise",
        "exercised_at": "2026-07-30T00:00:00Z",
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "creative_attribute_registry_binding": {
            "registry_id": registry["registry_id"],
            "registry_sha256": registry["registry_sha256"],
        },
        "frozen_adapter_binding": authenticate_frozen_adapter_source(manifest),
        "public_scenario_bindings": [
            {
                "scenario_id": row["scenario_manifest"]["scenario_binding"][
                    "scenario_id"
                ],
                "partition": row["scenario_manifest"]["partition"],
                "repetitions": row["scenario_manifest"]["scenario_binding"][
                    "repetitions"
                ],
                "scenario_manifest_sha256": row["scenario_manifest"][
                    "manifest_sha256"
                ],
                "experiment_design_sha256": row["experiment_design"][
                    "design_sha256"
                ],
                "admitted_public_files_sha256": (
                    "sha256:"
                    + hashlib.sha256(
                        _canonical(row["admitted_public_files"])
                    ).hexdigest()
                ),
            }
            for row in scenarios
        ],
        "panel_bindings": panel_bindings,
        "panel_rosters": rosters,
        "panelist_jobs": panelist_jobs,
        "run_results": run_results,
        "production_authority": {
            "package_created": False,
            "resolution_created": False,
            "registration_permitted": False,
            "activation_permitted": False,
            "active_panel_mutation_permitted": False,
        },
        "limitations": ["fictional synthetic contract fixture only"],
        "exercise_sha256": None,
    }
    return rehash(result, "exercise_sha256")


def exercise_fixture() -> dict[str, object]:
    return deepcopy(_exercise_fixture_cached())


def registry_fixture() -> dict[str, object]:
    inputs = creative_attribute_inputs()
    return rehash(
        {
            "schema_version": "creative-attribute-registry-v1",
            "registry_id": inputs["registry_id"],
            "registered_at": inputs["registered_at"],
            "creative_bindings": inputs["creative_bindings"],
            "attribute_definitions": inputs["attribute_definitions"],
            "creative_attributes": inputs["creative_attributes"],
            "annotation_methods": inputs["annotation_methods"],
            "review": {
                "status": "approved",
                "reviewed_by": inputs["reviewed_by"],
                "reviewed_at": inputs["reviewed_at"],
            },
            "outcome_access_boundary": {
                "status": "pre_outcome",
                "earliest_outcome_accessed_at": inputs[
                    "earliest_outcome_accessed_at"
                ],
            },
            "registry_sha256": None,
        },
        "registry_sha256",
    )


def evidence_library_fixture() -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.evidence_library import (
        append_evidence_entry,
        initialize_evidence_library,
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "library"
        initialize_evidence_library(
            library_root=root,
            library_id="fictional-library",
            created_at="2026-07-01T00:00:00Z",
        )
        return append_evidence_entry(
            library_root=root,
            observation=evidence_observation_fixture(1),
            attribute_registry=registry_fixture(),
            ingested_at="2026-07-02T00:00:00Z",
        )


def evidence_observation_fixture(
    sequence: int = 1,
    *,
    design: str = "randomized",
    segment_id: str | None = None,
) -> dict[str, object]:
    """Return one valid, independent synthetic observation for library tests."""

    result = deepcopy(outcome_observation_fixture())
    suffix = f"{sequence:03d}"
    result["observation_id"] = f"fictional-observation-{suffix}"
    result["source"]["source_sha256"] = (
        "sha256:" + hashlib.sha256(f"fictional-source-{suffix}".encode()).hexdigest()
    )
    result["experiment_binding"].update(
        {
            "experiment_id": f"fictional-experiment-{sequence:03d}",
            "campaign_id": f"fictional-campaign-{sequence:03d}",
            "block_id": f"fictional-block-{sequence:03d}",
            "batch_id": f"fictional-batch-{sequence:03d}",
        }
    )
    result["entity_identity"]["campaign_id"] = f"fictional-campaign-{sequence:03d}"
    result["design_quality"]["design"] = design
    result["design_quality"]["grouping_identity"] = (
        f"fictional-grouping-{sequence:03d}"
    )
    if segment_id is not None:
        result["audience_scope"]["segment_id"] = segment_id
    return rehash(result, "observation_sha256")


def _generated_study_manifest_fixture() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
        / "study-manifest.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _experiment_design_fixture(
    scenario_id: str,
    *,
    second_hypothesis: bool = False,
    second_persona: bool = False,
) -> list[dict[str, object]]:
    partition = "sealed" if scenario_id.startswith("non-identifiable") else "open"
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
        / partition
        / scenario_id
        / "experiment-design.json"
    )
    result = [json.loads(path.read_text(encoding="utf-8"))]
    if second_hypothesis:
        alternate = deepcopy(result[0])
        alternate["design_id"] = f"{scenario_id}-alternate-design"
        alternate["behavioral_hypothesis"] = {
            "contrast_direction": "treatment_minus_reference_positive",
            "hypothesis_id": "quantified-payback-motivation",
            "informative_attribute_id": "quantified-payback-motivation",
            "informative_attribute_value": True,
            "predeclared": True,
            "target_field": "motivations",
            "target_persona_id": (
                "operations-archetype"
                if second_persona
                else "finance-pricing-archetype"
            ),
        }
        result.append(rehash(alternate, "design_sha256"))
    return result


def _scenario_manifest_fixture(scenario_id: str) -> dict[str, object]:
    partition = "sealed" if scenario_id.startswith("non-identifiable") else "open"
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
        / partition
        / scenario_id
        / "scenario-manifest.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnosis_registry(
    *,
    second_hypothesis: bool,
    second_persona: bool,
) -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.attributes import (
        build_creative_attribute_registry,
    )

    inputs = creative_attribute_inputs()
    if second_hypothesis:
        inputs["attribute_definitions"].append(
            {
                "attribute_id": "quantified-payback-motivation",
                "attribute_version": "1.0.0",
                "attribute_kind": "interpretive",
                "value_type": "boolean",
                "behavioral_hypothesis": {
                    "hypothesis_id": "quantified-payback-motivation",
                    "target_persona_id": (
                        "operations-archetype"
                        if second_persona
                        else "finance-pricing-archetype"
                    ),
                    "target_persona_field": "motivations",
                    "proposed_value": ["Prioritize quantified financial return"],
                    "rationale_template": (
                        "Repeated pre-registered contrasts support a motivations update."
                    ),
                    "abstention_conditions": [
                        "Evidence is not repeatable across independent experiments."
                    ],
                },
            }
        )
        for creative in inputs["creative_bindings"]:
            inputs["creative_attributes"].append(
                {
                    "creative_id": creative["creative_id"],
                    "asset_sha256": creative["asset_sha256"],
                    "attribute_id": "quantified-payback-motivation",
                    "attribute_version": "1.0.0",
                    "method_id": "pre-outcome-human-review-v1",
                    "value": creative["creative_id"] == "quantified-payback",
                    "annotator": "fictional-reviewer",
                    "confidence": 0.9,
                    "ambiguity": "low",
                    "review_status": "approved",
                    "annotated_at": "2026-06-30T00:00:00Z",
                }
            )
        inputs["attribute_definitions"].sort(key=lambda row: row["attribute_id"])
        inputs["creative_attributes"].sort(
            key=lambda row: (row["creative_id"], row["attribute_id"])
        )
    return build_creative_attribute_registry(**inputs)


@lru_cache(maxsize=8)
def _diagnosis_evidence_fixture_cached(
    scenario_id: str,
    contradictory: bool,
    second_hypothesis: bool,
    second_persona: bool,
    experiment_limit: int,
    drop_last_block: bool,
    evidence_variant: str,
) -> tuple[dict[str, object], dict[str, object]]:
    from audience_panel_builder.population.experimental_calibration.adapters import (
        normalize_platform_export,
    )
    from audience_panel_builder.population.experimental_calibration.evidence_library import (
        _build_receipt_and_projection,
        _entry_document,
        _event_document,
    )

    manifest = _generated_study_manifest_fixture()
    registry = _diagnosis_registry(
        second_hypothesis=second_hypothesis,
        second_persona=second_persona,
    )
    platforms = (
        ("meta", "google")
        if evidence_variant == "two-platforms"
        else (("google",) if evidence_variant == "google" else ("meta",))
    )
    observations = []
    for platform in platforms:
        raw = raw_platform_export_fixture(platform, scenario_id)
        observations.extend(
            normalize_platform_export(
                platform=platform,
                raw_export_bytes=raw,
                source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
                study_manifest=manifest,
                creative_attribute_registry=registry,
            )
        )
    selected = [
        observation
        for observation in observations
        if observation["audience_scope"]["segment_id"] == "cfo"
        and (
            experiment_limit != 1
            or observation["experiment_binding"]["experiment_id"]
            == "fictional-experiment-1"
        )
        and not (
            drop_last_block
            and observation["experiment_binding"]["block_id"]
            == "block-e02-cfo-meta-06"
        )
    ]
    if evidence_variant == "pruned":
        selected = [
            observation
            for observation in selected
            if observation["experiment_binding"]["arm_id"]
            in {"quantified-payback", "strategic-control"}
        ]
    if evidence_variant in {"recent", "observational"}:
        for observation in selected:
            if evidence_variant == "recent":
                observation["reporting_context"]["maturity"] = "recent"
            else:
                observation["design_quality"]["design"] = "observational"
            rehashed = rehash(observation, "observation_sha256")
            observation.clear()
            observation.update(rehashed)
    if contradictory or evidence_variant in {
        "weak-contrary",
        "subthreshold-mixed",
        "subthreshold-positive",
        "subthreshold-negative",
    }:
        for observation in selected:
            binding = observation["experiment_binding"]
            if (
                binding["arm_id"] == "quantified-payback"
                and (
                    evidence_variant in {
                        "subthreshold-mixed",
                        "subthreshold-positive",
                        "subthreshold-negative",
                    }
                    or binding["experiment_id"] == "fictional-experiment-2"
                )
            ):
                reference = next(
                    row
                    for row in selected
                    if row["experiment_binding"]["experiment_id"]
                    == binding["experiment_id"]
                    and row["experiment_binding"]["block_id"]
                    == binding["block_id"]
                    and row["experiment_binding"]["arm_id"]
                    == "strategic-control"
                )
                reference_leads = next(
                    event
                    for event in reference["outcome_events"]
                    if event["metric_id"] == "lead"
                )["count"]
                lead = next(
                    event
                    for event in observation["outcome_events"]
                    if event["metric_id"] == "lead"
                )
                if evidence_variant in {
                    "subthreshold-mixed",
                    "subthreshold-positive",
                    "subthreshold-negative",
                }:
                    reference_impressions = reference["delivery"][
                        "impressions"
                    ]
                    treatment_impressions = observation["delivery"][
                        "impressions"
                    ]
                    reference_rate = (
                        float(reference_leads) / float(reference_impressions)
                    )
                    direction = (
                        1.0
                        if (
                            evidence_variant == "subthreshold-positive"
                            or (
                                evidence_variant != "subthreshold-negative"
                                and binding["experiment_id"]
                                == "fictional-experiment-1"
                            )
                        )
                        else -1.0
                    )
                    lead["count"] = max(
                        0,
                        round(
                            (reference_rate + direction * 0.01)
                            * float(treatment_impressions)
                        ),
                    )
                else:
                    lead["count"] = max(
                        0,
                        int(reference_leads)
                        - (
                            1
                            if evidence_variant == "weak-contrary"
                            else 20
                        ),
                    )
                observation["source"]["source_sha256"] = (
                    "sha256:"
                    + hashlib.sha256(
                        (
                            f"{evidence_variant}-contradictory-"
                            f"{observation['observation_id']}"
                        ).encode()
                    ).hexdigest()
                )
                rehashed = rehash(observation, "observation_sha256")
                observation.clear()
                observation.update(rehashed)
    selected.sort(key=lambda row: row["observation_id"])
    library_id = f"fictional-{scenario_id}-outcome-history"
    created_at = "2026-07-01T00:00:00Z"
    events: list[dict[str, object]] = []
    entries: dict[str, dict[str, object]] = {}
    event_state: dict[str, object] = {
        "event_count": 0,
        "event_head_sha256": None,
    }
    for index, observation in enumerate(selected, start=1):
        ingested_at = (
            f"2026-07-02T00:{index // 60:02d}:{index % 60:02d}Z"
        )
        entry = _entry_document(
            observation=observation,
            registry=registry,
            ingested_at=ingested_at,
            supersedes_entry_id=None,
        )
        event = _event_document(
            index=event_state,
            entry=entry,
            operation="append",
            effective_at=ingested_at,
            superseded=None,
            correction_reason=None,
        )
        entries[str(entry["entry_id"])] = entry
        events.append(event)
        event_state = {
            "event_count": index,
            "event_head_sha256": event["event_sha256"],
        }
    if not events:
        raise AssertionError("diagnosis evidence fixture must not be empty")
    _, snapshot = _build_receipt_and_projection(
        index={"library_id": library_id, "created_at": created_at},
        events=events,
        entries=entries,
    )
    return snapshot, registry


def diagnosis_inputs_fixture(
    *,
    scenario_id: str = "known-proof-need-miss",
    contradictory: bool = False,
    second_hypothesis: bool = False,
    second_persona: bool = False,
    experiment_limit: int = 0,
    drop_last_block: bool = False,
    evidence_variant: str = "default",
) -> dict[str, object]:
    snapshot, registry = _diagnosis_evidence_fixture_cached(
        scenario_id,
        contradictory,
        second_hypothesis,
        second_persona,
        experiment_limit,
        drop_last_block,
        evidence_variant,
    )
    designs = _experiment_design_fixture(
        scenario_id,
        second_hypothesis=second_hypothesis,
        second_persona=second_persona,
    )
    causes = {
        key: {
            "status": "cleared",
            "evidence_sha256": designs[0]["design_sha256"],
            "rationale": "The fictional randomized design clears this alternative.",
        }
        for key in (
            "delivery",
            "targeting",
            "timing",
            "offer",
            "landing_page",
            "tracking",
            "attribution",
        )
    }
    return {
        "base_panel_binding": _panel_binding(),
        "study_manifest": _generated_study_manifest_fixture(),
        "scenario_manifests": [_scenario_manifest_fixture(scenario_id)],
        "experiment_designs": deepcopy(designs),
        "evidence_library_snapshot": deepcopy(snapshot),
        "evidence_head_receipt": deepcopy(snapshot["head_receipt"]),
        "attribute_registry": deepcopy(registry),
        "alternative_causes": causes,
        "diagnosis_id": f"diagnosis-{scenario_id}",
        "diagnosed_at": "2026-07-20T00:00:00Z",
    }


def proposal_inputs_fixture(
    *,
    scenario_id: str = "known-proof-need-miss",
    contradictory: bool = False,
    evidence_variant: str = "default",
) -> dict[str, object]:
    diagnosis_inputs = diagnosis_inputs_fixture(
        scenario_id=scenario_id,
        contradictory=contradictory,
        evidence_variant=evidence_variant,
    )
    from audience_panel_builder.population.experimental_calibration.diagnosis import (
        diagnose_persona_behavior,
    )

    diagnosis = diagnose_persona_behavior(**diagnosis_inputs)
    return {
        "base_panel_binding": diagnosis_inputs["base_panel_binding"],
        "study_manifest": diagnosis_inputs["study_manifest"],
        "scenario_manifests": diagnosis_inputs["scenario_manifests"],
        "experiment_designs": diagnosis_inputs["experiment_designs"],
        "diagnosis": diagnosis,
        "attribute_registry": diagnosis_inputs["attribute_registry"],
        "evidence_library_snapshot": diagnosis_inputs[
            "evidence_library_snapshot"
        ],
        "evidence_head_receipt": diagnosis_inputs["evidence_head_receipt"],
        "alternative_causes": diagnosis_inputs["alternative_causes"],
        "proposal_id": f"proposal-{scenario_id}",
        "proposed_at": "2026-07-20T01:00:00Z",
    }


def diagnosis_fixture() -> dict[str, object]:
    from audience_panel_builder.population.experimental_calibration.diagnosis import (
        diagnose_persona_behavior,
    )

    return diagnose_persona_behavior(
        **diagnosis_inputs_fixture(scenario_id="null-effect")
    )


def projection_fixture() -> dict[str, object]:
    persona={"persona_archetype_id":"finance-pricing-archetype","anxieties":["risk"],"decision_context":"context","motivations":["return"],"proof_needs":["proof"],"role_context":"CFO"}
    snapshot={"anxieties":["risk"],"decision_context":"context","motivations":["return"],"proof_needs":["proof"],"role_context":"CFO"}
    binding={"profile_id":"profile-one","persona_archetype_id":"finance-pricing-archetype","profile_snapshot":snapshot,"profile_snapshot_sha256":"sha256:"+hashlib.sha256(_canonical(snapshot)).hexdigest()}
    panel_binding = _panel_binding()
    panel_binding["persona_snapshot_sha256"] = (
        "sha256:" + hashlib.sha256(_canonical(snapshot)).hexdigest()
    )
    return rehash({"schema_version":"experimental-persona-authoring-projection-v1","projection_id":"fictional-projection","created_at":"2026-07-20T00:00:00Z","source_role":"saved-audience-panel-v3.persona_archetypes","provenance_status":"canonical_panel_projection_only","panel_binding":panel_binding,"persona_archetypes":[persona],"grounded_profile_snapshot_bindings":[binding],"projection_sha256":None}, "projection_sha256")


def oracle_fixture() -> dict[str, object]:
    return rehash({"schema_version":"synthetic-persona-behavior-oracle-v1","oracle_id":"fictional-oracle","study_manifest_binding":_study_binding(),"scenario_id":"null-effect","repetition":0,"physical_truth":{"true_behavioral_miss":None,"safe_action_set":["no_change"],"true_operation":None},"epistemic_truth":{"identification_status":"no_miss","expected_engine_action":"no_change","expected_operation":None},"failure_mechanism":{"kind":"null-effect"},"counterfactual_values":{"effect":0.0},"oracle_sha256":None}, "oracle_sha256")


def _evaluation_provider_binding(
    *,
    provider_id: str,
    argument_value: object,
    input_value: object,
    output_value: object,
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "arguments_sha256": _digest_json(argument_value),
        "admitted_input_tree_sha256": _digest_json(input_value),
        "first_party_source_closure_sha256": _digest_json(
            f"{provider_id}-first-party"
        ),
        "external_dependency_closure_sha256": _digest_json(
            f"{provider_id}-dependencies"
        ),
        "runtime_binding_sha256": _digest_json(f"{provider_id}-runtime"),
        "output_sha256": _digest_json(output_value),
    }


def _evaluation_phase_receipts(
    *,
    manifest: dict[str, object],
    observations: list[dict[str, object]],
    exercise: dict[str, object],
    diagnoses: list[dict[str, object]],
    proposals: list[dict[str, object]],
    candidates: list[dict[str, object]],
    oracles: list[dict[str, object]],
) -> list[dict[str, object]]:
    family_by_id = {
        str(row["scenario_id"]): row for row in manifest["scenario_families"]
    }
    exercise_scenarios = {
        str(row["scenario_id"]): row
        for row in exercise["public_scenario_bindings"]
    }
    observation_by_id = {
        str(row["scenario_id"]): row for row in observations
    }
    design_to_scenario = {
        str(row["experiment_design_sha256"]): str(row["scenario_id"])
        for row in exercise["public_scenario_bindings"]
    }
    diagnosis_scenarios: dict[str, str] = {}
    for diagnosis in diagnoses:
        scenario_ids = {
            design_to_scenario[str(design_sha256)]
            for design_sha256 in diagnosis["frozen_analysis_bindings"][
                "experiment_design_sha256"
            ]
        }
        if len(scenario_ids) != 1:
            raise AssertionError(
                "evaluation diagnosis fixture must bind one scenario"
            )
        diagnosis_scenarios[str(diagnosis["diagnosis_sha256"])] = str(
            next(iter(scenario_ids))
        )
    proposal_scenarios = {
        str(proposal["proposal_sha256"]): diagnosis_scenarios[
            str(proposal["diagnosis"]["diagnosis_sha256"])
        ]
        for proposal in proposals
    }
    candidate_scenarios = {
        str(candidate["candidate_binding_sha256"]): proposal_scenarios[
            str(candidate["proposal_binding"]["proposal_sha256"])
        ]
        for candidate in candidates
    }

    def scenario_binding(scenario_id: str) -> dict[str, object]:
        return {
            "scenario_id": scenario_id,
            "repetition": 0,
            "partition": family_by_id[scenario_id]["partition"],
            "scenario_manifest_sha256": exercise_scenarios[scenario_id][
                "scenario_manifest_sha256"
            ],
            "observations_sha256": observation_by_id[scenario_id][
                "observations_sha256"
            ],
        }

    def record_binding(
        *,
        kind: str,
        record_id: object,
        sha256: object,
        scenario_id: str | None,
    ) -> dict[str, object]:
        return {
            "kind": kind,
            "record_id": record_id,
            "sha256": sha256,
            "scenario_id": scenario_id,
            "repetition": None if scenario_id is None else 0,
            "partition": (
                "both"
                if scenario_id is None
                else family_by_id[scenario_id]["partition"]
            ),
        }

    open_ids = sorted(
        scenario_id
        for scenario_id, row in family_by_id.items()
        if row["partition"] == "open"
    )
    sealed_ids = sorted(set(family_by_id) - set(open_ids))
    oracle_by_scenario = {
        str(row["scenario_id"]): row for row in oracles
    }
    phase_values = [
        {
            "phase": "open_input",
            "partition": "open",
            "scenario_ids": open_ids,
            "records": [
                record_binding(
                    kind="observation_set",
                    record_id=f"{scenario_id}-r0",
                    sha256=observation_by_id[scenario_id][
                        "observations_sha256"
                    ],
                    scenario_id=scenario_id,
                )
                for scenario_id in open_ids
            ],
            "providers": [
                _evaluation_provider_binding(
                    provider_id="synthetic-study-generator",
                    argument_value={"study_id": manifest["study_id"]},
                    input_value=[
                        scenario_binding(scenario_id)
                        for scenario_id in open_ids
                    ],
                    output_value=[
                        observation_by_id[scenario_id]["observations_sha256"]
                        for scenario_id in open_ids
                    ],
                )
            ],
        },
        {
            "phase": "engine_result",
            "partition": "open",
            "scenario_ids": sorted(
                {
                    *diagnosis_scenarios.values(),
                    *proposal_scenarios.values(),
                }
            ),
            "records": sorted(
                [
                    record_binding(
                        kind="diagnosis",
                        record_id=row["diagnosis_id"],
                        sha256=row["diagnosis_sha256"],
                        scenario_id=diagnosis_scenarios[
                            str(row["diagnosis_sha256"])
                        ],
                    )
                    for row in diagnoses
                ]
                + [
                    record_binding(
                        kind="proposal",
                        record_id=row["proposal_id"],
                        sha256=row["proposal_sha256"],
                        scenario_id=proposal_scenarios[
                            str(row["proposal_sha256"])
                        ],
                    )
                    for row in proposals
                ],
                key=lambda row: (row["kind"], row["record_id"]),
            ),
            "providers": [
                _evaluation_provider_binding(
                    provider_id="diagnosis-and-proposal-engine",
                    argument_value={
                        "diagnosis_sha256s": sorted(
                            row["diagnosis_sha256"] for row in diagnoses
                        ),
                        "proposal_sha256s": sorted(
                            row["proposal_sha256"] for row in proposals
                        ),
                    },
                    input_value=[
                        observation_by_id[scenario_id]["observations_sha256"]
                        for scenario_id in open_ids
                    ],
                    output_value=[*diagnoses, *proposals],
                )
            ],
        },
        {
            "phase": "candidate_seal",
            "partition": "open",
            "scenario_ids": sorted(set(candidate_scenarios.values())),
            "records": [
                record_binding(
                    kind="candidate",
                    record_id=row["candidate_id"],
                    sha256=row["candidate_binding_sha256"],
                    scenario_id=candidate_scenarios[
                        str(row["candidate_binding_sha256"])
                    ],
                )
                for row in candidates
            ],
            "providers": [
                _evaluation_provider_binding(
                    provider_id="candidate-materializer",
                    argument_value={
                        "candidate_ids": [
                            row["candidate_id"] for row in candidates
                        ]
                    },
                    input_value=proposals,
                    output_value=candidates,
                )
            ],
        },
        {
            "phase": "sealed_reveal",
            "partition": "sealed",
            "scenario_ids": sealed_ids,
            "records": sorted(
                [
                    record_binding(
                        kind="observation_set",
                        record_id=f"{scenario_id}-r0",
                        sha256=observation_by_id[scenario_id][
                            "observations_sha256"
                        ],
                        scenario_id=scenario_id,
                    )
                    for scenario_id in sealed_ids
                ]
                + [
                    record_binding(
                        kind="oracle",
                        record_id=oracle_by_scenario[scenario_id][
                            "oracle_id"
                        ],
                        sha256=oracle_by_scenario[scenario_id][
                            "oracle_sha256"
                        ],
                        scenario_id=scenario_id,
                    )
                    for scenario_id in sealed_ids
                ],
                key=lambda row: (row["kind"], row["record_id"]),
            ),
            "providers": [
                _evaluation_provider_binding(
                    provider_id="sealed-fixture-reveal",
                    argument_value={"partition": "sealed"},
                    input_value=[
                        scenario_binding(scenario_id)
                        for scenario_id in sealed_ids
                    ],
                    output_value=[
                        oracle_by_scenario[scenario_id]["oracle_sha256"]
                        for scenario_id in sealed_ids
                    ],
                )
            ],
        },
        {
            "phase": "exercise",
            "partition": "both",
            "scenario_ids": sorted(family_by_id),
            "records": [
                record_binding(
                    kind="exercise",
                    record_id=exercise["exercise_id"],
                    sha256=exercise["exercise_sha256"],
                    scenario_id=None,
                )
            ],
            "providers": [
                _evaluation_provider_binding(
                    provider_id="synthetic-panel-exercise",
                    argument_value={"exercise_id": exercise["exercise_id"]},
                    input_value={
                        "candidate_sha256s": [
                            row["candidate_binding_sha256"]
                            for row in candidates
                        ],
                        "scenario_ids": sorted(family_by_id),
                    },
                    output_value=exercise,
                )
            ],
        },
    ]
    result: list[dict[str, object]] = []
    previous = None
    for sequence, values in enumerate(phase_values):
        document = {
            "schema_version": (
                "synthetic-persona-behavior-evaluation-phase-receipt-v1"
            ),
            "phase_id": (
                f"phase-{sequence + 1}-"
                f"{str(values['phase']).replace('_', '-')}"
            ),
            "phase": values["phase"],
            "sequence": sequence,
            "partition": values["partition"],
            "study_manifest_sha256": manifest["manifest_sha256"],
            "scenario_bindings": [
                scenario_binding(scenario_id)
                for scenario_id in values["scenario_ids"]
            ],
            "record_bindings": values["records"],
            "provider_bindings": values["providers"],
            "previous_phase_receipt_sha256": previous,
            "phase_receipt_sha256": None,
        }
        document = rehash(document, "phase_receipt_sha256")
        result.append(document)
        previous = document["phase_receipt_sha256"]
    return result


@lru_cache(maxsize=1)
def _evaluation_inputs_fixture_cached() -> dict[str, object]:
    fixture_root = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "experimental-calibration"
    )
    manifest = json.loads(
        (fixture_root / "study-manifest.json").read_bytes()
    )
    exercise = exercise_fixture()
    observations: list[dict[str, object]] = []
    oracles: list[dict[str, object]] = []
    for family in manifest["scenario_families"]:
        scenario_id = str(family["scenario_id"])
        partition = str(family["partition"])
        rows = json.loads(
            (
                fixture_root
                / partition
                / scenario_id
                / "canonical-observations.json"
            ).read_bytes()
        )
        observations.append(
            {
                "scenario_id": scenario_id,
                "repetition": 0,
                "observations": rows,
                "observations_sha256": _digest_json(rows),
            }
        )
        oracles.append(
            json.loads(
                (
                    fixture_root
                    / "oracle"
                    / partition
                    / scenario_id
                    / "hidden-oracle.json"
                ).read_bytes()
            )
        )

    null_inputs = proposal_inputs_fixture(scenario_id="null-effect")
    known_inputs = candidate_proposal_inputs_fixture(
        candidate_base_panel_fixture(),
        scenario_id="known-proof-need-miss",
    )
    from audience_panel_builder.population.experimental_calibration.proposal import (
        build_experimental_proposal,
    )

    diagnoses = [
        deepcopy(null_inputs["diagnosis"]),
        deepcopy(known_inputs["diagnosis"]),
    ]
    proposals = [
        build_experimental_proposal(**null_inputs),
        build_experimental_proposal(**known_inputs),
    ]
    candidates = [
        deepcopy(row["materialized_candidate"]["candidate_binding"])
        for row in exercise["panel_bindings"]
        if row["panel_kind"] == "candidate"
    ]
    return {
        "study_manifest": manifest,
        "observations": observations,
        "exercise": exercise,
        "oracle_documents": oracles,
        "diagnoses": diagnoses,
        "proposals": proposals,
        "candidates": candidates,
        "phase_receipts": _evaluation_phase_receipts(
            manifest=manifest,
            observations=observations,
            exercise=exercise,
            diagnoses=diagnoses,
            proposals=proposals,
            candidates=candidates,
            oracles=oracles,
        ),
        "evaluated_at": "2026-07-30T01:00:00Z",
    }


def evaluation_inputs_fixture(
    *,
    diagnoses: list[dict[str, object]] | None = None,
    proposals: list[dict[str, object]] | None = None,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    result = deepcopy(_evaluation_inputs_fixture_cached())
    if diagnoses is not None:
        result["diagnoses"] = deepcopy(diagnoses)
    if proposals is not None:
        result["proposals"] = deepcopy(proposals)
    if candidates is not None:
        result["candidates"] = deepcopy(candidates)
    if any(value is not None for value in (diagnoses, proposals, candidates)):
        result["phase_receipts"] = _evaluation_phase_receipts(
            manifest=result["study_manifest"],
            observations=result["observations"],
            exercise=result["exercise"],
            diagnoses=result["diagnoses"],
            proposals=result["proposals"],
            candidates=result["candidates"],
            oracles=result["oracle_documents"],
        )
    return result


def evaluation_fixture() -> dict[str, object]:
    from experimental_persona_calibration_oracle.evaluator import (
        evaluate_synthetic_study,
    )

    return evaluate_synthetic_study(**evaluation_inputs_fixture())
