from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    TIER4_CLAIM_TEXT,
    TIER4_REFRESH_TRIGGERS,
    TIER4_REQUIRED_DISCLAIMER,
    approve_preregistration_design,
    design_evidence_sha256,
    load_trusted_authority_registry,
    project_synthetic_result_binding,
    project_shared_outcome_evidence,
    seal_preregistration,
    validate_claim_family,
    validate_comparison,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_shared_outcome_evidence,
    validate_tier4_claim,
    validate_validation_observation,
)
import audience_panel_builder.population.validation.contracts as contracts_module  # noqa: E402


def digest(letter: str) -> str:
    return "sha256:" + letter * 64


AUTHORITY_SECRET = b"fictional-tier4-test-authority-secret"
AUTHORITY_SECRET_SHA256 = "sha256:" + hashlib.sha256(AUTHORITY_SECRET).hexdigest()


def authority_registry_document(
    payload: object, *, root_sha256: str = digest("c"),
    index_sha256: str = digest("b"),
) -> dict[str, object]:
    assert isinstance(payload, dict)
    document: dict[str, object] = {
        "schema_version": "panel-validation-authority-registry-v1",
        "registry_id": "tier4-test-authorities",
        "entries": [{
            "authority_id": payload["approval"]["approved_by"],
            "registration_id": payload["registration_id"],
            "approved_at": payload["approval"]["approved_at"],
            "registered_at": payload["registered_at"],
            "authority_root_sha256": root_sha256,
            "authority_index_sha256": index_sha256,
            "design_evidence_sha256": design_evidence_sha256(
                payload,
                authority_root_sha256=root_sha256,
                authority_index_sha256=index_sha256,
            ),
        }],
        "registry_sha256": None,
        "registry_hmac_sha256": None,
    }
    document["registry_sha256"] = sha256_json(document)
    unsigned = deepcopy(document)
    unsigned["registry_hmac_sha256"] = None
    document["registry_hmac_sha256"] = "sha256:" + hmac.new(
        AUTHORITY_SECRET,
        json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return document


def authority_registry_capability(
    payload: object, *, root_sha256: str = digest("c"),
    index_sha256: str = digest("b"),
) -> object:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "trusted-authority-registry.json"
        path.write_text(
            json.dumps(authority_registry_document(
                payload, root_sha256=root_sha256,
                index_sha256=index_sha256,
            )),
            encoding="utf-8",
        )
        with patch.object(
            contracts_module,
            "_authority_secret_fingerprint_for_registry",
            return_value=AUTHORITY_SECRET_SHA256,
        ):
            return load_trusted_authority_registry(
                path, authority_secret=AUTHORITY_SECRET,
            )


def approved_design(payload: object) -> tuple[dict[str, object], object]:
    registry = authority_registry_capability(payload)
    approved, capability = approve_preregistration_design(
        payload,
        authority_registry=registry,
        authority_id="validation-owner",
    )
    return approved, capability


def approved_seal(payload: object) -> dict[str, object]:
    approved, capability = approved_design(payload)
    return seal_preregistration(approved, design_approval=capability)


def outcome_scope() -> dict[str, object]:
    return {
        "cohort_id": "operations-leaders",
        "segment_id": "enterprise",
        "channel": "paid-social",
        "placement": "feed",
        "objective": "qualified-response",
        "geography": "united-states",
        "validation_window": "2026-q3",
    }


def panel_binding(version: str = "1-0-0") -> dict[str, object]:
    return {
        "panel_id": "operations-leaders",
        "panel_version": version,
        "panel_sha256": digest("1"),
        "package_sha256": digest("2"),
    }


def synthetic_binding(run_id: str = "run-base") -> dict[str, object]:
    return {
        "surface": "complete_exposure_ordering",
        "run_id": run_id,
        "result_sha256": digest("3"),
    }


def sealed_synthetic_surface(
    surface: str = "complete_exposure_ordering",
    run_id: str = "run-base",
) -> dict[str, object]:
    identities = {
        "complete_exposure_ordering": {
            "method": "complete_exposure",
            "stage": "screening",
            "result_path": "screening-model-results.json",
        },
        "maxdiff_screening_ordering": {
            "method": "partial_exposure_maxdiff",
            "stage": "screening",
            "result_path": "screening-model-results.json",
        },
        "pairwise_boundary_ordering": {
            "method": "partial_exposure_maxdiff",
            "stage": "boundary",
            "result_path": "boundary-results.json",
        },
    }[surface]
    return {
        "surface": surface,
        "method": identities["method"],
        "stage": identities["stage"],
        "run_id": run_id,
        "result_path": identities["result_path"],
        "result_sha256": digest("3"),
        "result_bytes_sha256": digest("4"),
        "manifest_sha256": digest("5"),
        "lineage_bundle_sha256": digest("6"),
        "producer_evidence_sha256": digest("7"),
        "producer_semantics_sha256": digest("8"),
        "frozen_at": "2026-07-30T00:00:00Z",
        "producer_evidence_sealed_at": "2026-07-31T00:00:00Z",
        "eligible_creatives": [
            {"creative_id": "creative-a", "creative_sha256": digest("9")},
            {"creative_id": "creative-b", "creative_sha256": digest("a")},
        ],
    }


def tie_handling(surface: str = "complete_exposure_ordering") -> dict[str, object]:
    if surface == "complete_exposure_ordering":
        return {
            "ordering_equivalence": "exact-utility-equality-v1",
            "ordering_tiebreak": "creative-id-serialization-only-v1",
        }
    return {
        "ordering_equivalence": "rounded-utility-bucket-v1",
        "ordering_tiebreak": "creative-id-serialization-only-v1",
        "effective_ordering_tolerance": 0.001,
        "rounding_rule": "python-half-even-v1",
    }


def creative_binding() -> dict[str, object]:
    return {"creative_id": "creative-a", "creative_sha256": digest("4")}


def metric() -> dict[str, object]:
    return {
        "name": "qualified-response-rate",
        "definition": "Qualified responses divided by eligible exposures.",
        "direction": "higher_is_better",
        "exposure_unit": "eligible-exposure",
        "outcome_unit": "qualified-response",
        "measurement_window": "2026-q3",
        "attribution_window": "seven-days",
        "practical_equivalence_margin": 0.02,
        "smallest_effect_of_interest": 0.03,
    }


def source() -> dict[str, object]:
    return {
        "source_id": "authorized-outcomes",
        "source_sha256": digest("5"),
        "permission_confirmed": True,
    }


def preregistration_fixture() -> dict[str, object]:
    return {
        "schema_version": "panel-validation-preregistration-v1",
        "registration_id": "validation-q3",
        "registered_at": "2026-08-01T00:00:00Z",
        "registered_by": "validation-owner",
        "status": "registered",
        "panel_binding": panel_binding(),
        "synthetic_surface": sealed_synthetic_surface(),
        "claim_scope": {
            "panel_binding": panel_binding(),
            "synthetic_binding": synthetic_binding(),
            "outcome_scope": outcome_scope(),
        },
        "primary_metric": metric(),
        "secondary_metrics": [],
        "validation_blocks": [
            {
                "block_id": "campaign-q3",
                "study_id": "campaign-study",
                "planned_arm_ids": ["arm-a"],
                "planned_effective_sample": 100.0,
                "planned_segment_membership": [{
                    "arm_id": "arm-a", "segment_ids": ["enterprise"],
                }],
            }
        ],
        "holdout_partition": {"partition_unit": "block", "held_out_ids": ["campaign-q3"]},
        "prior_outcome_access": [],
        "analysis_rules": {
            "tie_handling": tie_handling(),
            "block_weighting": "equal",
            "bootstrap_seed": 17,
            "bootstrap_resamples": 1000,
            "confidence_levels": [0.95],
            "missingness_treatment": "report",
            "pass_rule": "all-required-gates",
            "downgrade_rule": "limitations",
            "stop_rule": "integrity-failure",
            "scope_narrowing_rule": "material-segment-only",
        },
        "eligibility_thresholds": {"minimum_blocks": 1, "minimum_coverage": 1.0},
        "segment_rules": {"materiality_threshold": 0.1, "rule": "evaluate-material-segments"},
        "multiplicity_rules": {"family_id": "family-q3", "family_alpha": 0.05, "member_registration_ids": ["validation-q3"], "correction_method": "holm"},
        "interim_analysis_rules": {"allowed": False, "maximum_looks": 1},
        "study_design_power": {
            "design_status": "approved",
            "method": "preregistered-randomized-power-analysis-v1",
            "smallest_effect_of_interest": 0.03,
            "documented_power": 0.80,
            "evidence_sha256": digest("d"),
            "approval_sha256": digest("6"),
        },
        "segment_inventory": [{
            "segment_id": "enterprise",
            "must_cover": True,
            "effective_panel_weight": 1.0,
            "planned_block_ids": ["campaign-q3"],
            "evidence_sha256": digest("e"),
            "approval_sha256": digest("6"),
        }],
        "approval": {
            "approved_at": "2026-07-31T00:00:00Z",
            "approved_by": "validation-owner",
            "authority_root_sha256": digest("c"),
            "authority_index_sha256": digest("b"),
            "design_evidence_sha256": digest("a"),
            "approval_sha256": digest("6"),
        },
        "registration_sha256": None,
    }


def shared_outcome_evidence_fixture() -> dict[str, object]:
    document = {
        "schema_version": "panel-shared-outcome-evidence-v1",
        "shared_evidence_id": "campaign-q3-arm-a",
        "study_id": "campaign-study",
        "block_id": "campaign-q3",
        "arm_id": "arm-a",
        "creative_binding": creative_binding(),
        "outcome_scope": outcome_scope(),
        "metric": metric(),
        "metric_family": "binary_proportion",
        "units": {"exposure": "eligible-exposure", "outcome": "qualified-response"},
        "assignment": {"design": "randomized", "unit": "campaign-arm", "leakage_detected": False},
        "windows": {"measurement": "2026-q3", "attribution": "seven-days"},
        "aggregate": {"success_count": 20, "eligible_exposure_count": 100},
        "precision": {"confidence_level": 0.95},
        "sample": {
            "eligible_exposure_count": 100,
            "effective_sample_size": 100.0,
        },
        "missingness": {
            "status": "none",
            "eligible_exposure_count": 100,
            "missing_outcome_count": 0,
            "rate": 0.0,
        },
        "segment_ids": ["enterprise"],
        "exclusions": [],
        "source": source(),
        "outcome_accessed_at": "2026-08-02T00:00:00Z",
        "limitations": [],
        "shared_evidence_sha256": None,
    }
    document["shared_evidence_sha256"] = sha256_json({**document, "shared_evidence_sha256": None})
    return document


def registration_binding_fixture(
    preregistration: dict[str, object] | None = None,
) -> dict[str, object]:
    registration = preregistration or approved_seal(preregistration_fixture())
    access_hashes = [entry["access_sha256"] for entry in registration["prior_outcome_access"]]
    return {
        "registration_id": registration["registration_id"],
        "registration_sha256": registration["registration_sha256"],
        "registered_at": registration["registered_at"],
        "status": registration["status"],
        "prior_outcome_access_sha256": sha256_json(access_hashes),
        "prior_outcome_access_hashes": access_hashes,
        "holdout_partition": registration["holdout_partition"],
        "claim_scope": registration["claim_scope"],
        "multiplicity_rules": registration["multiplicity_rules"],
        "preregistration": registration,
    }


def observation_fixture(
    preregistration: dict[str, object] | None = None,
) -> dict[str, object]:
    shared = shared_outcome_evidence_fixture()
    document = {
        "schema_version": "panel-validation-observation-v1",
        "observation_id": "observation-q3-arm-a",
        "registration_binding": registration_binding_fixture(preregistration),
        "shared_outcome_evidence_binding": {"shared_evidence_id": shared["shared_evidence_id"], "study_id": shared["study_id"], "shared_evidence_sha256": shared["shared_evidence_sha256"]},
        "block_id": shared["block_id"], "arm_id": shared["arm_id"], "creative_binding": shared["creative_binding"],
        "synthetic_binding": synthetic_binding(), "panel_binding": panel_binding(),
        "claim_scope": {"panel_binding": panel_binding(), "synthetic_binding": synthetic_binding(), "outcome_scope": outcome_scope()},
        "outcome_scope": outcome_scope(), "metric": metric(), "metric_family": shared["metric_family"],
        "units": shared["units"], "assignment": shared["assignment"], "windows": shared["windows"],
        "aggregate": shared["aggregate"], "precision": shared["precision"], "sample": shared["sample"],
        "missingness": shared["missingness"], "segment_ids": shared["segment_ids"],
        "exclusions": shared["exclusions"], "source": shared["source"],
        "outcome_accessed_at": shared["outcome_accessed_at"], "holdout_status": "eligible_held_out", "limitations": [],
        "observation_sha256": None,
    }
    document["observation_sha256"] = sha256_json({**document, "observation_sha256": None})
    return document


def comparison_fixture(
    registration: dict[str, object] | None = None,
) -> dict[str, object]:
    observation = observation_fixture(registration)
    return {
        "schema_version": "panel-synthetic-outcome-comparison-v1", "comparison_id": "comparison-q3",
        "registration_binding": {
            "registration_id": "validation-q3",
            "registration_sha256": observation["registration_binding"]["registration_sha256"],
        }, "panel_binding": panel_binding(),
        "synthetic_result_binding": synthetic_binding(), "block_binding": {"block_id": "campaign-q3", "study_id": "campaign-study"},
        "metric_binding": metric(), "observations": [observation],
        "arm_mappings": [{
            "arm_id": "arm-a",
            "creative_binding": creative_binding(),
            "observation_sha256": observation["observation_sha256"],
        }],
        "mapping_coverage": {"expected_arms": 1, "mapped_arms": 1}, "observed_ordering": [["creative-a"]], "synthetic_ordering": [["creative-a"]],
        "pairwise_comparisons": [],
        "block_evidence": {
            "observation_sha256": [observation["observation_sha256"]],
            "eligible_exposure_count": 100,
            "missing_outcome_count": 0,
            "planned_effective_sample": 100.0,
            "achieved_effective_sample": 100.0,
        },
        "segment_evidence": [{
            "segment_id": "enterprise",
            "observation_sha256": [observation["observation_sha256"]],
            "arm_ids": ["arm-a"],
            "observed_ordering": [["creative-a"]],
            "synthetic_ordering": [["creative-a"]],
            "pairwise_comparisons": [],
        }],
        "comparison_sha256": None,
    }


def evaluation_fixture() -> dict[str, object]:
    draft = preregistration_fixture()
    draft["analysis_rules"]["bootstrap_resamples"] = 20_000
    registration = approved_seal(draft)
    comparison = comparison_fixture(registration)
    comparison["comparison_sha256"] = sha256_json({
        **comparison, "comparison_sha256": None,
    })
    family = {
        "schema_version": "panel-validation-claim-family-v1",
        "family_id": "family-q3",
        "family_alpha": 0.05,
        "member_registration_ids": ["validation-q3"],
        "member_comparison_sha256": [
            sha256_json([comparison["comparison_sha256"]]),
        ],
        "member_one_sided_p_values": [0.01],
        "correction_method": "holm",
        "adjusted_p_values": [0.01],
        "member_preregistrations": [registration],
        "member_comparisons": [[comparison]],
        "complete": True,
        "family_sha256": None,
    }
    family = seal(family, "family_sha256")
    return {
        "schema_version": "panel-held-out-evaluation-v1", "evaluation_id": "evaluation-q3", "evaluated_at": "2026-08-03T00:00:00Z",
        "registration_binding": {"registration_id": "validation-q3", "registration_sha256": registration["registration_sha256"]}, "panel_binding": panel_binding(),
        "claim_scope": {"panel_binding": panel_binding(), "synthetic_binding": synthetic_binding(), "outcome_scope": outcome_scope()}, "metric_binding": metric(),
        "block_inventory": [{"block_id": "campaign-q3", "comparison_sha256": comparison["comparison_sha256"]}],
        "coverage": {"status": "complete", "block_rate": 1.0, "arm_rate": 1.0, "mapping_rate": 1.0},
        "missingness": {"status": "none", "eligible_exposure_count": 100, "missing_outcome_count": 0, "rate": 0.0},
        "sample_sufficiency": {
            "status": "sufficient",
            "minimum_achieved_ratio": 1.0,
            "blocks": [{
                "block_id": f"campaign-{index:02d}",
                "planned_effective_sample": 100.0,
                "achieved_effective_sample": 100.0,
                "achieved_ratio": 1.0,
            } for index in range(12)],
        },
        "independence": {"status": "independent"}, "leakage": {"status": "clear"}, "multiplicity": {"status": "complete"},
        "repeated_looks": {"status": "none"},
        "power": {"status": "sufficient", "documented_power": 0.8, "smallest_effect_of_interest": 0.03, "method": "preregistered-randomized-power-analysis-v1", "design_status": "approved"},
        "overall_diagnostics": {"status": "pass", "tau": {"available": True, "point": 1.0, "two_sided_lower": 1.0, "two_sided_upper": 1.0, "one_sided_lower": 1.0}, "agreement": {"available": True, "point": 1.0, "two_sided_lower": 1.0, "two_sided_upper": 1.0, "one_sided_lower": 1.0}, "determinate_pair_coverage": 1.0, "one_sided_p_value": 0.01, "holm_adjusted_p_value": 0.01},
        "segment_diagnostics": [{"segment_id": "enterprise", "material": True, "must_cover": True, "effective_panel_weight": 1.0, "eligible_blocks": 6, "planned_blocks": 6, "block_coverage": 1.0, "creative_arms": 18, "tau": {"available": True, "point": 1.0, "two_sided_lower": 1.0, "two_sided_upper": 1.0, "one_sided_lower": 1.0}, "agreement": {"available": True, "point": 1.0, "two_sided_lower": 1.0, "two_sided_upper": 1.0, "one_sided_lower": 1.0}, "clear_reversal": False, "status": "pass"}],
        "influence_diagnostics": {
            "status": "all_leave_outs_meet_registered_point_and_raw_p_thresholds",
            "maximum_block_contribution": 1.0 / 12.0,
            "leave_one_block": [{
                "block_id": "campaign-q3",
                "tau": 1.0,
                "agreement": 1.0,
                "one_sided_p_value": 0.01,
                "registered_point_and_raw_p_thresholds_retained": True,
            }],
            "leave_one_batch": [{
                "study_id": "campaign-q3",
                "tau": 1.0,
                "agreement": 1.0,
                "one_sided_p_value": 0.01,
                "registered_point_and_raw_p_thresholds_retained": True,
            }],
        },
        "preregistration": registration,
        "comparisons": [comparison],
        "claim_family": family,
        "gate_results": {"all_required_gates_passed": True}, "decision": {"status": "tier4_supported"}, "limitations": [], "evaluation_sha256": None,
    }


def claim_fixture() -> dict[str, object]:
    return {
        "schema_version": "panel-tier4-claim-v1", "claim_id": "claim-q3", "issued_at": "2026-08-03T00:00:00Z", "expires_at": "2026-11-03T00:00:00Z", "status": "active",
        "panel_binding": panel_binding(), "registration_binding": {"registration_id": "validation-q3", "registration_sha256": digest("7")}, "evaluation_binding": {"evaluation_id": "evaluation-q3", "evaluation_sha256": digest("b")},
        "claim_scope": {"panel_binding": panel_binding(), "synthetic_binding": synthetic_binding(), "outcome_scope": outcome_scope()}, "claim_text": TIER4_CLAIM_TEXT,
        "required_disclaimer": TIER4_REQUIRED_DISCLAIMER, "diagnostic_summary": {"status": "tier4_supported"}, "limitations": [], "refresh_triggers": list(TIER4_REFRESH_TRIGGERS), "claim_sha256": None,
    }


def claim_family_fixture() -> dict[str, object]:
    registration = approved_seal(preregistration_fixture())
    comparison = comparison_fixture()
    comparison["comparison_sha256"] = sha256_json({
        **comparison, "comparison_sha256": None,
    })
    comparison_hash = sha256_json([comparison["comparison_sha256"]])
    return {
        "schema_version": "panel-validation-claim-family-v1", "family_id": "family-q3", "family_alpha": 0.05,
        "member_registration_ids": ["validation-q3"], "member_comparison_sha256": [comparison_hash], "member_one_sided_p_values": [0.01], "correction_method": "holm", "adjusted_p_values": [0.01], "member_preregistrations": [registration], "member_comparisons": [[comparison]], "complete": True, "family_sha256": None,
    }


def seal(document: dict[str, object], field: str) -> dict[str, object]:
    document[field] = sha256_json({**document, field: None})
    return document


class Tier4ValidationContractTests(unittest.TestCase):
    def test_design_capability_cannot_authorize_resealed_membership_edits(self) -> None:
        draft = preregistration_fixture()
        registry = authority_registry_capability(draft)
        approved, capability = approve_preregistration_design(
            draft,
            authority_registry=registry,
            authority_id="validation-owner",
        )
        changed = deepcopy(approved)
        changed["validation_blocks"][0]["planned_segment_membership"][0][
            "segment_ids"
        ] = []
        with self.assertRaisesRegex(
            ContractError, "does not authorize this exact design",
        ):
            seal_preregistration(changed, design_approval=capability)
        for field in ("bootstrap_seed", "family_alpha", "documented_power"):
            with self.subTest(field=field):
                remint = deepcopy(draft)
                if field == "bootstrap_seed":
                    remint["analysis_rules"]["bootstrap_seed"] += 1
                elif field == "family_alpha":
                    remint["multiplicity_rules"]["family_alpha"] = 0.04
                else:
                    remint["study_design_power"]["documented_power"] = 0.81
                with self.assertRaisesRegex(
                    ContractError, "does not authorize this exact complete design",
                ):
                    approve_preregistration_design(
                        remint,
                        authority_registry=registry,
                        authority_id="validation-owner",
                    )

    def test_segment_inventory_blocks_exactly_match_planned_memberships(self) -> None:
        draft = preregistration_fixture()
        draft["segment_inventory"][0]["planned_block_ids"] = ["other-block"]
        with self.assertRaisesRegex(
            ContractError, "registered validation blocks",
        ):
            approved_seal(draft)
        draft = preregistration_fixture()
        draft["validation_blocks"].append({
            "block_id": "other-block",
            "study_id": "other-study",
            "planned_arm_ids": ["arm-b"],
            "planned_effective_sample": 100.0,
            "planned_segment_membership": [{
                "arm_id": "arm-b", "segment_ids": ["enterprise"],
            }],
        })
        draft["segment_inventory"][0]["planned_block_ids"] = ["other-block"]
        with self.assertRaisesRegex(
            ContractError, "planned_block_ids must exactly equal",
        ):
            approved_seal(draft)

    def test_authority_registry_requires_out_of_band_hmac_secret(self) -> None:
        draft = preregistration_fixture()
        registry = authority_registry_document(draft)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "at least 32 bytes"):
                load_trusted_authority_registry(
                    path, authority_secret=b"short",
                )
            with patch.object(
                contracts_module,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ):
                with self.assertRaisesRegex(ContractError, "runtime-pinned"):
                    load_trusted_authority_registry(
                        path, authority_secret=b"x" * 32,
                    )

    def test_self_consistent_replacement_registry_and_key_are_untrusted(self) -> None:
        draft = preregistration_fixture()
        replacement_secret = b"replacement-authority-secret-material"
        registry = authority_registry_document(draft)
        unsigned = deepcopy(registry)
        unsigned["registry_hmac_sha256"] = None
        registry["registry_hmac_sha256"] = "sha256:" + hmac.new(
            replacement_secret,
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replacement-registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with patch.object(
                contracts_module,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ):
                with self.assertRaisesRegex(ContractError, "runtime-pinned"):
                    load_trusted_authority_registry(
                        path, authority_secret=replacement_secret,
                    )

    def test_metric_family_denominators_reconcile_with_missingness(self) -> None:
        families = {
            "binary_proportion": {
                "success_count": 20, "eligible_exposure_count": 90,
            },
            "continuous_mean": {
                "sample_count": 90, "mean": 4.0,
                "standard_deviation": 2.0,
            },
            "event_rate": {"event_count": 2, "exposure_time": 123.5},
        }
        denominator_fields = {
            "binary_proportion": "eligible_exposure_count",
            "continuous_mean": "sample_count",
            "event_rate": "exposure_time",
        }
        for family, aggregate in families.items():
            with self.subTest(family=family):
                document = shared_outcome_evidence_fixture()
                document["metric_family"] = family
                document["aggregate"] = aggregate
                if family == "event_rate":
                    document["units"]["exposure"] = "person-day"
                document["sample"] = {
                    "eligible_exposure_count": 100,
                    "effective_sample_size": 90.0,
                }
                document["missingness"] = {
                    "status": "present",
                    "eligible_exposure_count": 100,
                    "missing_outcome_count": 10,
                    "rate": 0.10,
                }
                document = seal(document, "shared_evidence_sha256")
                validate_shared_outcome_evidence(document)

                if family != "event_rate":
                    contradictory = deepcopy(document)
                    contradictory["aggregate"][
                        denominator_fields[family]
                    ] = 100
                    contradictory = seal(
                        contradictory, "shared_evidence_sha256",
                    )
                    with self.assertRaisesRegex(
                        ContractError, "denominator must equal analyzable",
                    ):
                        validate_shared_outcome_evidence(contradictory)
                else:
                    self.assertEqual(
                        123.5,
                        validate_shared_outcome_evidence(document)[
                            "aggregate"
                        ]["exposure_time"],
                    )

                overweight = deepcopy(document)
                overweight["sample"]["effective_sample_size"] = 91.0
                overweight = seal(overweight, "shared_evidence_sha256")
                with self.assertRaisesRegex(
                    ContractError, "cannot exceed analyzable",
                ):
                    validate_shared_outcome_evidence(overweight)

    def test_status_only_comparison_and_unpowered_registration_fail_closed(self) -> None:
        registration = preregistration_fixture()
        registration.pop("study_design_power")
        registration.pop("segment_inventory")
        with self.assertRaisesRegex(ContractError, "missing fields"):
            approved_seal(registration)

        comparison = comparison_fixture()
        comparison.pop("observations")
        comparison.pop("block_evidence")
        comparison.pop("segment_evidence")
        comparison["block_diagnostics"] = {"status": "complete"}
        comparison = seal(comparison, "comparison_sha256")
        with self.assertRaisesRegex(ContractError, "block_diagnostics"):
            validate_comparison(comparison)

    def test_comparison_recomputes_observation_and_block_evidence(self) -> None:
        comparison = seal(comparison_fixture(), "comparison_sha256")
        changed = deepcopy(comparison)
        changed["arm_mappings"][0]["observation_sha256"] = digest("f")
        changed = seal(
            {**changed, "comparison_sha256": None},
            "comparison_sha256",
        )
        with self.assertRaisesRegex(ContractError, "observation hashes"):
            validate_comparison(changed)

        changed = deepcopy(comparison)
        changed["block_evidence"]["missing_outcome_count"] = 1
        changed = seal(
            {**changed, "comparison_sha256": None},
            "comparison_sha256",
        )
        with self.assertRaisesRegex(ContractError, "derived"):
            validate_comparison(changed)

    def test_preregistration_requires_the_full_sealed_synthetic_surface(self) -> None:
        legacy = preregistration_fixture()
        legacy["synthetic_surface"] = synthetic_binding()
        with self.assertRaisesRegex(ContractError, "missing fields"):
            approved_seal(legacy)

        sealed = approved_seal(preregistration_fixture())
        surface = sealed["synthetic_surface"]
        self.assertEqual(
            {"surface", "run_id", "result_sha256"},
            set(project_synthetic_result_binding(surface)),
        )
        self.assertEqual(sealed["claim_scope"]["synthetic_binding"], project_synthetic_result_binding(surface))

    def test_downstream_synthetic_bindings_accept_only_the_compact_projection(self) -> None:
        full_surface = sealed_synthetic_surface()
        cases = [
            (preregistration_fixture()["claim_scope"], "synthetic_binding", None),
            (observation_fixture(), "synthetic_binding", "observation_sha256"),
            (comparison_fixture(), "synthetic_result_binding", "comparison_sha256"),
            (evaluation_fixture()["claim_scope"], "synthetic_binding", None),
            (claim_fixture()["claim_scope"], "synthetic_binding", None),
        ]
        for document, key, digest_field in cases:
            with self.subTest(key=key, digest_field=digest_field):
                changed = deepcopy(document)
                changed[key] = full_surface
                if digest_field is None:
                    with self.assertRaisesRegex(ContractError, "unknown fields"):
                        if "outcome_scope" in changed:
                            # Claim scope is nested, so validate it through a complete registration.
                            registration = preregistration_fixture()
                            registration["claim_scope"] = changed
                            approved_seal(registration)
                        else:
                            self.fail("unexpected non-document fixture")
                else:
                    changed[digest_field] = sha256_json({**changed, digest_field: None})
                    validator = {
                        "observation_sha256": validate_validation_observation,
                        "comparison_sha256": validate_comparison,
                    }[digest_field]
                    with self.assertRaisesRegex(ContractError, "unknown fields"):
                        validator(changed)

        for factory, field, validator in (
            (evaluation_fixture, "evaluation_sha256", validate_held_out_evaluation),
            (claim_fixture, "claim_sha256", validate_tier4_claim),
        ):
            changed = factory()
            changed["claim_scope"]["synthetic_binding"] = full_surface  # type: ignore[index]
            changed[field] = sha256_json({**changed, field: None})
            with self.subTest(validator=validator), self.assertRaisesRegex(ContractError, "unknown fields"):
                validator(changed)

    def test_sealed_synthetic_surface_rejects_missing_extra_bad_digest_and_unsorted_creatives(self) -> None:
        mutations = {
            "missing": lambda value: value.pop("producer_evidence_sha256"),
            "extra": lambda value: value.__setitem__("extra", True),
            "digest": lambda value: value.__setitem__("result_bytes_sha256", "not-a-digest"),
            "creative-order": lambda value: value.__setitem__("eligible_creatives", list(reversed(value["eligible_creatives"]))),
        }
        for name, mutate in mutations.items():
            registration = preregistration_fixture()
            mutate(registration["synthetic_surface"])  # type: ignore[arg-type]
            with self.subTest(name=name), self.assertRaises(ContractError):
                approved_seal(registration)

    def test_tie_handling_is_closed_and_surface_specific(self) -> None:
        for surface in (
            "complete_exposure_ordering",
            "maxdiff_screening_ordering",
            "pairwise_boundary_ordering",
        ):
            for name, mutate in (
                ("missing", lambda value: value.pop("ordering_tiebreak")),
                ("extra", lambda value: value.__setitem__("extra", True)),
                ("wrong-type", lambda value: value.__setitem__("ordering_equivalence", 1)),
                ("wrong-fixed-value", lambda value: value.__setitem__("ordering_tiebreak", "other")),
            ):
                registration = preregistration_fixture()
                registration["synthetic_surface"] = sealed_synthetic_surface(surface)  # type: ignore[index]
                registration["claim_scope"]["synthetic_binding"] = synthetic_binding()  # type: ignore[index]
                registration["claim_scope"]["synthetic_binding"]["surface"] = surface  # type: ignore[index]
                registration["analysis_rules"]["tie_handling"] = tie_handling(surface)  # type: ignore[index]
                mutate(registration["analysis_rules"]["tie_handling"])  # type: ignore[arg-type,index]
                with self.subTest(surface=surface, name=name), self.assertRaises(ContractError):
                    approved_seal(registration)

    def test_preregistration_chronology_allows_only_sealing_equalities_and_requires_prior_access_after_freeze(self) -> None:
        registration = preregistration_fixture()
        registration["synthetic_surface"]["frozen_at"] = "2026-08-01T00:00:00Z"  # type: ignore[index]
        registration["synthetic_surface"]["producer_evidence_sealed_at"] = "2026-08-01T00:00:00Z"  # type: ignore[index]
        registration["registered_at"] = "2026-08-01T00:00:00Z"
        self.assertEqual("registered", approved_seal(registration)["status"])

        for name, frozen_at, sealed_at, accessed_at in (
            ("freeze-after-evidence-seal", "2026-08-01T00:00:01Z", "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"),
            ("evidence-seal-after-registration", "2026-07-31T00:00:00Z", "2026-08-01T00:00:01Z", "2026-08-01T00:00:00Z"),
            ("prior-access-equal-freeze", "2026-07-31T00:00:00Z", "2026-08-01T00:00:00Z", "2026-07-31T00:00:00Z"),
            ("prior-access-before-freeze", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", "2026-07-31T00:00:00Z"),
        ):
            registration = preregistration_fixture()
            registration["synthetic_surface"]["frozen_at"] = frozen_at  # type: ignore[index]
            registration["synthetic_surface"]["producer_evidence_sealed_at"] = sealed_at  # type: ignore[index]
            registration["prior_outcome_access"] = [{
                "access_sha256": digest("b"), "accessed_at": accessed_at,
                "kind": "authorized-outcome-review",
            }]
            with self.subTest(name=name), self.assertRaises(ContractError):
                approved_seal(registration)

    def test_observation_chronology_requires_both_freeze_and_registration_before_access(self) -> None:
        for name, frozen_at, sealed_at, registered_at, accessed_at, error in (
            ("freeze-equal-outcome", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "frozen"),
            ("registration-equal-outcome", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "registered"),
            ("freeze-after-outcome", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-01T00:00:00Z", "frozen"),
            ("registration-after-outcome", "2026-07-31T00:00:00Z", "2026-07-31T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-01T00:00:00Z", "registered"),
        ):
            registration = preregistration_fixture()
            registration["synthetic_surface"]["frozen_at"] = frozen_at  # type: ignore[index]
            registration["synthetic_surface"]["producer_evidence_sealed_at"] = sealed_at  # type: ignore[index]
            registration["registered_at"] = registered_at
            registration["approval"]["approved_at"] = "2026-07-30T00:00:00Z"  # type: ignore[index]
            observation = observation_fixture(approved_seal(registration))
            observation["outcome_accessed_at"] = accessed_at
            observation["observation_sha256"] = sha256_json({**observation, "observation_sha256": None})
            if error == "frozen":
                # A valid preregistration requires frozen_at <= registered_at,
                # so this isolated observation-boundary test supplies the
                # already-validated binding with registration safely earlier.
                binding = deepcopy(observation["registration_binding"])
                binding["registered_at"] = "2026-07-31T00:00:00Z"
                with self.subTest(name=name), patch.object(
                    contracts_module, "_registration_binding", return_value=binding,
                ), self.assertRaisesRegex(ContractError, error):
                    validate_validation_observation(observation)
            else:
                with self.subTest(name=name), self.assertRaisesRegex(ContractError, error):
                    validate_validation_observation(observation)

    def test_registration_binds_panel_surface_scope_holdout_and_analysis(self) -> None:
        sealed = approved_seal(preregistration_fixture())
        self.assertEqual("registered", sealed["status"])
        self.assertEqual(sha256_json({**sealed, "registration_sha256": None}), sealed["registration_sha256"])
        self.assertEqual(sealed, validate_preregistration(sealed))

    def test_holdout_boolean_cannot_replace_registration_chronology(self) -> None:
        preregistration = preregistration_fixture()
        preregistration["registered_at"] = "2026-08-02T00:00:00Z"
        observation = observation_fixture(approved_seal(preregistration))
        observation["outcome_accessed_at"] = "2026-08-01T00:00:00Z"
        with self.assertRaisesRegex(ContractError, "registered before outcome"):
            validate_validation_observation(observation)

    def test_every_contract_rejects_unknown_top_level_fields(self) -> None:
        cases = [(approved_seal(preregistration_fixture()), validate_preregistration), (shared_outcome_evidence_fixture(), validate_shared_outcome_evidence), (observation_fixture(), validate_validation_observation), (seal(comparison_fixture(), "comparison_sha256"), validate_comparison), (seal(evaluation_fixture(), "evaluation_sha256"), validate_held_out_evaluation), (seal(claim_fixture(), "claim_sha256"), validate_tier4_claim), (seal(claim_family_fixture(), "family_sha256"), validate_claim_family)]
        for document, validator in cases:
            document["extra"] = True
            with self.subTest(validator=validator), self.assertRaisesRegex(ContractError, "unknown"):
                validator(document)

    def test_valid_contracts_return_independent_deep_copies(self) -> None:
        cases = [
            (shared_outcome_evidence_fixture(), validate_shared_outcome_evidence, "aggregate"),
            (seal(comparison_fixture(), "comparison_sha256"), validate_comparison, "mapping_coverage"),
            (seal(evaluation_fixture(), "evaluation_sha256"), validate_held_out_evaluation, "decision"),
            (seal(claim_family_fixture(), "family_sha256"), validate_claim_family, "member_preregistrations"),
        ]
        for document, validator, nested_key in cases:
            result = validator(document)
            self.assertEqual(document, result)
            self.assertIsNot(document, result)
            if nested_key == "member_preregistrations":
                result[nested_key][0]["registration_id"] = "changed"  # type: ignore[index]
            else:
                result[nested_key][next(iter(result[nested_key]))] = "changed"  # type: ignore[index]
            self.assertNotEqual(document, result)
        family = seal(claim_family_fixture(), "family_sha256")
        self.assertEqual(family, validate_claim_family(family))

    def test_shared_outcome_projection_excludes_panel_specific_bindings(self) -> None:
        base, candidate = observation_fixture(), observation_fixture()
        candidate["panel_binding"] = panel_binding("1-1-0")
        candidate["synthetic_binding"] = synthetic_binding("run-candidate")
        candidate["claim_scope"] = {"panel_binding": panel_binding("1-1-0"), "synthetic_binding": synthetic_binding("run-candidate"), "outcome_scope": outcome_scope()}
        candidate["observation_sha256"] = sha256_json({**candidate, "observation_sha256": None})
        self.assertEqual(project_shared_outcome_evidence(base), project_shared_outcome_evidence(candidate))
        self.assertNotEqual(base["observation_sha256"], candidate["observation_sha256"])

    def test_projection_binding_and_derived_status_are_strict(self) -> None:
        observation = observation_fixture()
        for path, value in (("aggregate", {"success_count": 21, "eligible_exposure_count": 100}), ("source", {**source(), "source_id": "other"}), ("holdout_status", "in_sample")):
            changed = deepcopy(observation)
            changed[path] = value
            changed["observation_sha256"] = sha256_json({**changed, "observation_sha256": None})
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_validation_observation(changed)

    def test_every_outcome_only_field_is_hash_bound(self) -> None:
        mutations = {
            "block": lambda value: value.__setitem__("block_id", "campaign-q4"),
            "arm": lambda value: value.__setitem__("arm_id", "arm-b"),
            "creative": lambda value: value["creative_binding"].__setitem__("creative_id", "creative-b"),
            "metric": lambda value: value["metric"].__setitem__("name", "other-rate"),
            "windows": lambda value: value["windows"].__setitem__("measurement", "2026-q4"),
            "scope": lambda value: value["outcome_scope"].__setitem__("geography", "canada"),
            "source": lambda value: value["source"].__setitem__("source_id", "other-outcomes"),
        }
        for name, mutate in mutations.items():
            changed = observation_fixture()
            mutate(changed)
            changed["observation_sha256"] = sha256_json({**changed, "observation_sha256": None})
            with self.subTest(field=name), self.assertRaises(ContractError):
                validate_validation_observation(changed)

    def test_recursive_json_and_family_integrity_are_strict(self) -> None:
        shared = shared_outcome_evidence_fixture()
        shared["aggregate"]["success_count"] = float("nan")  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "finite"):
            validate_shared_outcome_evidence(shared)
        family = seal(claim_family_fixture(), "family_sha256")
        family["member_registration_ids"] = ["validation-q3", "validation-q3"]
        with self.assertRaisesRegex(ContractError, "unique"):
            validate_claim_family(family)
        family = claim_family_fixture()
        family["complete"] = False
        family = seal(family, "family_sha256")
        with self.assertRaisesRegex(ContractError, "complete"):
            validate_claim_family(family)
        malformed = preregistration_fixture()
        malformed["primary_metric"]["person_id"] = "person-1"  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "person-level"):
            approved_seal(malformed)
        family = claim_family_fixture()
        family["family_alpha"] = 1.0
        family = seal(family, "family_sha256")
        with self.assertRaisesRegex(ContractError, "between zero and one"):
            validate_claim_family(family)

    def test_registration_cli_seals_once_and_never_clobbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft, output = root / "draft.json", root / "registration.json"
            authority_root = root / "authority-root.json"
            authority_index = root / "authority-index.json"
            draft_payload = preregistration_fixture()
            draft.write_text(json.dumps(draft_payload), encoding="utf-8")
            authority_root.write_text('{"authority":"validation-owner"}', encoding="utf-8")
            authority_index.write_text('{"approved":["validation-q3"]}', encoding="utf-8")
            authority_registry = root / "authority-registry.json"
            authority_secret = root / "authority-secret.key"
            authority_secret.write_bytes(AUTHORITY_SECRET)
            authority_secret.chmod(0o600)
            root_sha = "sha256:" + hashlib.sha256(
                authority_root.read_bytes(),
            ).hexdigest()
            index_sha = "sha256:" + hashlib.sha256(
                authority_index.read_bytes(),
            ).hexdigest()
            authority_registry.write_text(json.dumps(
                authority_registry_document(
                    draft_payload,
                    root_sha256=root_sha,
                    index_sha256=index_sha,
                ),
            ), encoding="utf-8")
            script = str(
                ROOT / "skills" / "audience-panel-builder" / "scripts"
                / "register-panel-validation.py"
            )
            wrapper = (
                "import hashlib,runpy,sys;from pathlib import Path;"
                "sys.path.insert(0,str(Path(sys.argv[1]).parent));"
                "import audience_panel_builder.population.validation.contracts as c;"
                "c._authority_secret_fingerprint_for_registry="
                "lambda registry_id:'sha256:'+hashlib.sha256("
                "b'fictional-tier4-test-authority-secret').hexdigest();"
                "sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
            )
            command = [
                sys.executable, "-c", wrapper, script,
                "--input", str(draft), "--output", str(output),
                "--authority-root", str(authority_root),
                "--authority-index", str(authority_index),
                "--authority-registry", str(authority_registry),
                "--authority-secret-file", str(authority_secret),
            ]
            first = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            response = json.loads(first.stdout)
            self.assertEqual({"status", "output", "registration_sha256"}, set(response))
            self.assertEqual("registered", response["status"])
            self.assertEqual(response["registration_sha256"], json.loads(output.read_text(encoding="utf-8"))["registration_sha256"])
            second = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(3, second.returncode)
            authority_secret.chmod(0o644)
            unsafe_command = list(command)
            unsafe_command[unsafe_command.index(str(output))] = str(
                root / "unsafe-registration.json"
            )
            unsafe = subprocess.run(
                unsafe_command, check=False, capture_output=True, text=True,
            )
            self.assertEqual(2, unsafe.returncode)
            self.assertIn("owner-only", unsafe.stdout)

    def test_only_a_preregistered_holdout_block_can_be_eligible(self) -> None:
        preregistration = preregistration_fixture()
        preregistration["validation_blocks"].append({
            "block_id": "campaign-q4",
            "study_id": "campaign-study-q4",
            "planned_arm_ids": ["arm-b"],
            "planned_effective_sample": 100.0,
            "planned_segment_membership": [{
                "arm_id": "arm-b", "segment_ids": ["enterprise"],
            }],
        })  # type: ignore[index]
        preregistration["holdout_partition"] = {"partition_unit": "block", "held_out_ids": ["campaign-q4"]}
        preregistration["segment_inventory"][0]["planned_block_ids"].append(
            "campaign-q4"
        )
        observation = observation_fixture(approved_seal(preregistration))
        observation["holdout_status"] = "in_sample"
        observation["observation_sha256"] = sha256_json({**observation, "observation_sha256": None})
        self.assertEqual("in_sample", validate_validation_observation(observation)["holdout_status"])
        observation = observation_fixture()
        observation["claim_scope"]["outcome_scope"]["geography"] = "canada"  # type: ignore[index]
        observation["outcome_scope"]["geography"] = "canada"  # type: ignore[index]
        observation["observation_sha256"] = sha256_json({**observation, "observation_sha256": None})
        with self.assertRaisesRegex(ContractError, "sealed preregistration"):
            validate_validation_observation(observation)

    def test_observation_binds_exact_registered_study_and_arm(self) -> None:
        observation = observation_fixture()
        observation["shared_outcome_evidence_binding"]["study_id"] = "other-study"  # type: ignore[index]
        observation["observation_sha256"] = sha256_json({**observation, "observation_sha256": None})
        with self.assertRaisesRegex(ContractError, "study_id"):
            validate_validation_observation(observation)
        observation = observation_fixture()
        observation["arm_id"] = "arm-unplanned"
        observation["observation_sha256"] = sha256_json({**observation, "observation_sha256": None})
        with self.assertRaisesRegex(ContractError, "planned"):
            validate_validation_observation(observation)

    def test_claim_family_proves_exact_preregistered_membership(self) -> None:
        family = claim_family_fixture()
        registration = family["member_preregistrations"][0]  # type: ignore[index]
        registration["multiplicity_rules"]["member_registration_ids"] = ["validation-q3", "other-registration"]  # type: ignore[index]
        registration["registration_sha256"] = None  # type: ignore[index]
        registration = approved_seal(registration)
        family["member_preregistrations"] = [registration]
        family = seal(family, "family_sha256")
        with self.assertRaisesRegex(ContractError, "exact preregistered family"):
            validate_claim_family(family)

    def test_permission_counts_comparisons_and_evaluation_gates_fail_closed(self) -> None:
        shared = shared_outcome_evidence_fixture()
        shared["source"]["permission_confirmed"] = False  # type: ignore[index]
        shared["shared_evidence_sha256"] = sha256_json({**shared, "shared_evidence_sha256": None})
        with self.assertRaisesRegex(ContractError, "must be true"):
            validate_shared_outcome_evidence(shared)
        shared = shared_outcome_evidence_fixture()
        shared["aggregate"]["success_count"] = 1.5  # type: ignore[index]
        shared["shared_evidence_sha256"] = sha256_json({**shared, "shared_evidence_sha256": None})
        with self.assertRaisesRegex(ContractError, "integer"):
            validate_shared_outcome_evidence(shared)
        shared = shared_outcome_evidence_fixture()
        shared["aggregate"]["success_count"] = 101  # type: ignore[index]
        shared["shared_evidence_sha256"] = sha256_json({**shared, "shared_evidence_sha256": None})
        with self.assertRaisesRegex(ContractError, "cannot exceed"):
            validate_shared_outcome_evidence(shared)
        preregistration = preregistration_fixture()
        preregistration["eligibility_thresholds"]["minimum_blocks"] = 1.5  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "integer"):
            approved_seal(preregistration)
        comparison = comparison_fixture()
        duplicate_arm = deepcopy(comparison["arm_mappings"][0])  # type: ignore[index]
        duplicate_arm["creative_binding"] = {"creative_id": "creative-b", "creative_sha256": digest("c")}
        comparison["arm_mappings"].append(duplicate_arm)  # type: ignore[index]
        comparison["mapping_coverage"] = {"expected_arms": 2, "mapped_arms": 2}
        comparison = seal(comparison, "comparison_sha256")
        with self.assertRaisesRegex(ContractError, "each arm once"):
            validate_comparison(comparison)
        comparison = comparison_fixture()
        comparison["mapping_coverage"] = {"expected_arms": 1.5, "mapped_arms": 1.5}
        comparison = seal(comparison, "comparison_sha256")
        with self.assertRaisesRegex(ContractError, "integer"):
            validate_comparison(comparison)
        evaluation = evaluation_fixture()
        evaluation["leakage"] = {"status": "leaked"}
        evaluation = seal(evaluation, "evaluation_sha256")
        with self.assertRaisesRegex(ContractError, "match diagnostic"):
            validate_held_out_evaluation(evaluation)
        evaluation = evaluation_fixture()
        evaluation["segment_diagnostics"][0]["status"] = "fail"
        evaluation = seal(evaluation, "evaluation_sha256")
        with self.assertRaisesRegex(ContractError, "derived from numeric evidence"):
            validate_held_out_evaluation(evaluation)

    def test_valid_claim_is_accepted(self) -> None:
        claim = seal(claim_fixture(), "claim_sha256")
        self.assertEqual(claim, validate_tier4_claim(claim))
