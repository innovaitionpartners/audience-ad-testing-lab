from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audience_lab  # noqa: E402
from audience_lab.audience_research_v3 import (  # noqa: E402
    CELL_STATUSES,
    COMPOSITION_PLAN_VERSION,
    EVIDENCE_BASES,
    FRAME_ELIGIBILITY,
    FRAME_REQUEST_VERSION,
    OBSERVATION_BATCH_VERSION,
    OUTCOME_FEEDBACK_VERSION,
    PANEL_TIERS,
    POPULATION_FRAME_VERSION,
    RESEARCH_BRIEF_V3,
    SAVED_PANEL_V3,
    VALIDITY_AXIS_STATUSES,
    VALIDITY_PROFILE_VERSION,
    _v2_projection,
    validate_audience_research_v3,
    validate_composition_plan,
    validate_frame_request,
    validate_observation_batch,
    validate_outcome_feedback,
    validate_population_frame,
    validate_research_brief_v3,
    validate_saved_panel_v3,
    validate_validity_profile,
)


def canonical_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def bare_digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class AudienceResearchV3ContractTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "conformance" / "fixtures" / "audience-research"
        cls.v2_brief = json.loads((fixture / "approved-brief.json").read_text())
        cls.v2_panel = json.loads((fixture / "approved-panel.json").read_text())

    def assert_canonical(self, expected, validator):
        try:
            canonical = validator(expected)
        except ValueError as exc:
            self.fail(f"valid canonical document was rejected: {exc}")
        self.assertEqual(expected, canonical)
        return canonical

    def test_public_versions_are_importable(self):
        self.assertEqual("audience-frame-request-v1", FRAME_REQUEST_VERSION)
        self.assertEqual(
            "audience-frame-observation-batch-v1", OBSERVATION_BATCH_VERSION
        )
        self.assertEqual("audience-population-frame-v1", POPULATION_FRAME_VERSION)
        self.assertEqual("panel-composition-plan-v1", COMPOSITION_PLAN_VERSION)
        self.assertEqual("audience-research-brief-v3", RESEARCH_BRIEF_V3)
        self.assertEqual("saved-audience-panel-v3", SAVED_PANEL_V3)
        self.assertEqual("panel-outcome-feedback-v1", OUTCOME_FEEDBACK_VERSION)
        self.assertEqual("panel-validity-profile-v1", VALIDITY_PROFILE_VERSION)

    def test_package_exports_add_v3_without_removing_v2(self):
        existing_v2 = {
            "AudienceResearchValidationError",
            "RESEARCH_BRIEF_SCHEMA_VERSION",
            "SAVED_PANEL_SCHEMA_VERSION",
            "ValidationError",
            "compute_scope_fingerprint",
            "require_valid_audience_research_pair",
            "validate_audience_research_pair",
            "validate_research_brief",
            "validate_saved_panel",
        }
        new_v3 = {
            "FRAME_REQUEST_VERSION",
            "OBSERVATION_BATCH_VERSION",
            "POPULATION_FRAME_VERSION",
            "COMPOSITION_PLAN_VERSION",
            "RESEARCH_BRIEF_V3",
            "SAVED_PANEL_V3",
            "OUTCOME_FEEDBACK_VERSION",
            "VALIDITY_PROFILE_VERSION",
            "validate_frame_request",
            "validate_observation_batch",
            "validate_population_frame",
            "validate_research_brief_v3",
            "validate_saved_panel_v3",
            "validate_composition_plan",
            "validate_outcome_feedback",
            "validate_validity_profile",
            "validate_audience_research_v3",
            "validate_v3_runtime_authority",
        }
        self.assertTrue(existing_v2.issubset(audience_lab.__all__))
        self.assertTrue(new_v3.issubset(audience_lab.__all__))

    def frame_request(self):
        return {
            "schema_version": FRAME_REQUEST_VERSION,
            "request_id": "operations-frame-request",
            "target_audience": "Operations leaders at mid-market software companies",
            "decision": "Evaluate campaign creative",
            "desired_claim": "Directional composition claim",
            "geography": ["US"],
            "time_basis": {"as_of": "2026-07-24", "lookback_days": 365},
            "target_unit": "eligible-cohort-member",
            "proxy_universes": [{
                "universe_id": "software-employers",
                "description": "US software employers",
                "unit": "employer",
                "denominator": "eligible-employers",
                "exact": False,
            }],
            "required_dimensions": ["company-size", "role"],
            "required_joints": [["company-size", "role"]],
            "modeled_cell_rules": [{
                "rule_id": "declared-enterprise-operations",
                "unit": "eligible-cohort-member",
                "denominator": "all-eligible-cohort-members",
                "dimension_values": {
                    "company-size": "1000-plus",
                    "role": "operations",
                },
                "method": "declared_weight",
                "structural_weight": 0.3,
                "uncertainty": {"lower": 0.24, "upper": 0.36},
                "rationale": "Approved deterministic fallback if source data is absent.",
            }],
            "calibration_rules": [{
                "rule_id": "authorized-enterprise-calibration",
                "unit": "eligible-cohort-member",
                "denominator": "all-eligible-cohort-members",
                "dimension_values": {
                    "company-size": "1000-plus",
                    "role": "operations",
                },
                "calibration_factor": 2.0,
                "rationale": "Approved cohort-to-frame calibration.",
            }],
            "exclusions": ["Companies below 100 employees"],
            "authorized_evidence_bases": ["public", "first_party_aggregate"],
            "available_capabilities": ["authorized-handoff", "public-adapter"],
            "downgrade_policy": {
                "allow_tier_1": True,
                "allow_experimental": False,
                "reason": "Downgrade rather than invent a denominator.",
            },
        }

    def observation_batch(self):
        batch = {
            "schema_version": OBSERVATION_BATCH_VERSION,
            "batch_id": "operations-frame-batch",
            "frame_request_id": "operations-frame-request",
            "adapter_id": "authorized-handoff",
            "source_family": "authorized-aggregate",
            "source": {
                "publisher": "Acme",
                "program": "Authorized audience export",
                "edition": "2026-Q2",
                "vintage": "2026-06-30",
                "retrieved_at": "2026-07-24T12:00:00Z",
            },
            "raw_snapshot_sha256": "sha256:" + "1" * 64,
            "normalized_batch_sha256": "",
            "access": {
                "access_type": "authorized",
                "permission_confirmed": True,
                "permitted_uses": [
                    "audience-composition",
                    "population-framing",
                ],
            },
            "geography": ["US"],
            "unit": "eligible-cohort-member",
            "denominator": "all-eligible-cohort-members",
            "dimensions": ["company-size", "role"],
            "cells": [{
                "cell_id": "midmarket-operations",
                "dimension_values": {
                    "company-size": "100-999",
                    "role": "operations",
                },
                "estimate": 70.0,
                "uncertainty": {
                    "lower": 64.0,
                    "upper": 76.0,
                    "method": "reported-interval",
                },
                "suppressed": False,
                "status": "observed",
                "relationship": "joint",
                "source_location": "frame-observations-0001.json#cell-1",
            }, {
                "cell_id": "enterprise-operations",
                "dimension_values": {
                    "company-size": "1000-plus",
                    "role": "operations",
                },
                "estimate": 30.0,
                "uncertainty": {
                    "lower": 24.0,
                    "upper": 36.0,
                    "method": "reported-interval",
                },
                "suppressed": False,
                "status": "modeled",
                "relationship": "joint",
                "source_location": "frame-observations-0001.json#cell-2",
            }],
            "selection_notes": "Authorized cohort selected before creative review.",
            "coverage_notes": "Covers all eligible cohort members in the export.",
            "citations": ["authorized-audience-handoff-v1#frame-observations-0001"],
        }
        hash_input = deepcopy(batch)
        hash_input.pop("normalized_batch_sha256")
        batch["normalized_batch_sha256"] = digest(hash_input)
        return batch

    def seal_observation_batch(self, batch):
        hash_input = deepcopy(batch)
        hash_input.pop("normalized_batch_sha256", None)
        batch["normalized_batch_sha256"] = digest(hash_input)
        return batch

    def frame(self, *, eligibility="eligible_tier_3"):
        batch = self.observation_batch()
        return {
            "schema_version": POPULATION_FRAME_VERSION,
            "frame_id": "operations-population-frame",
            "frame_version": "1.0.0",
            "built_at": "2026-07-24T13:00:00Z",
            "frame_request_id": "operations-frame-request",
            "frame_request_sha256": digest(self.frame_request()),
            "target_universe": "Operations leaders at mid-market software companies",
            "proxy_universes": ["software-employers"],
            "claim_boundary": "Authorized cohort composition only.",
            "units": [{
                "partition_id": "eligible-cohort-members",
                "unit": "eligible-cohort-member",
                "denominator": "all-eligible-cohort-members",
                "exact": True,
            }],
            "structural_dimensions": ["company-size", "role"],
            "cells": [{
                "cell_id": "midmarket-operations",
                "partition_id": "eligible-cohort-members",
                "dimension_values": {
                    "company-size": "100-999",
                    "role": "operations",
                },
                "relationship": "joint",
                "origin": "source_observation",
                "modeled_rule_id": None,
                "status": "observed",
                "structural_weight": 0.7,
                "weight_semantic": "authorized_cohort_weight",
                "uncertainty": {"lower": 0.64, "upper": 0.76},
                "suppressed": False,
                "source_observations": [{
                    "batch_id": "operations-frame-batch",
                    "cell_id": "midmarket-operations",
                }],
                "calibration_factor": 1.0,
            }, {
                "cell_id": "enterprise-operations",
                "partition_id": "eligible-cohort-members",
                "dimension_values": {
                    "company-size": "1000-plus",
                    "role": "operations",
                },
                "relationship": "joint",
                "origin": "source_observation",
                "modeled_rule_id": None,
                "status": "modeled",
                "structural_weight": 0.3,
                "weight_semantic": "experimental_modeled_weight",
                "uncertainty": {"lower": 0.24, "upper": 0.36},
                "suppressed": False,
                "source_observations": [{
                    "batch_id": "operations-frame-batch",
                    "cell_id": "enterprise-operations",
                }],
                "calibration_factor": 2.0,
            }],
            "margins": [],
            "joints": [{
                "partition_id": "eligible-cohort-members",
                "dimensions": ["company-size", "role"],
                "cell_ids": [
                    "enterprise-operations",
                    "midmarket-operations",
                ],
                "missing_reason": None,
            }],
            "source_bindings": [{
                "batch_id": batch["batch_id"],
                "normalized_batch_sha256": batch["normalized_batch_sha256"],
                "raw_snapshot_sha256": batch["raw_snapshot_sha256"],
                "partition_id": "eligible-cohort-members",
                "source": deepcopy(batch["source"]),
                "geography": deepcopy(batch["geography"]),
                "access": deepcopy(batch["access"]),
                "selection_notes": batch["selection_notes"],
                "coverage_notes": batch["coverage_notes"],
            }],
            "coverage_assessment": {
                "selection_statement": "The authorized platform cohort was fixed.",
                "coverage_statement": "All eligible cohort members are denominated.",
                "known_gaps": ["Role taxonomy is coarser than the target concept."],
            },
            "modeled_weight_by_dimension": [{
                "partition_id": "eligible-cohort-members",
                "dimension": "company-size",
                "share": 0.3,
                "status": "supported",
            }, {
                "partition_id": "eligible-cohort-members",
                "dimension": "role",
                "share": 0.3,
                "status": "supported",
            }],
            "modeled_weight_share": 0.3,
            "eligibility": eligibility,
            "downgrade_reason": "",
        }

    def no_frame(self):
        return {
            "schema_version": POPULATION_FRAME_VERSION,
            "frame_id": "operations-no-defensible-frame",
            "frame_version": "1.0.0",
            "built_at": "2026-07-24T13:00:00Z",
            "frame_request_id": "operations-frame-request",
            "frame_request_sha256": digest(self.frame_request()),
            "target_universe": (
                "Operations leaders at mid-market software companies"
            ),
            "proxy_universes": ["software-employers"],
            "claim_boundary": "No population claim is supported.",
            "units": [],
            "structural_dimensions": ["company-size", "role"],
            "cells": [],
            "margins": [],
            "joints": [],
            "source_bindings": [],
            "coverage_assessment": {
                "selection_statement": (
                    "No compatible structural observations were available."
                ),
                "coverage_statement": "No defensible population coverage.",
                "known_gaps": ["No compatible unit and denominator partition."],
            },
            "modeled_weight_by_dimension": [],
            "modeled_weight_share": 0.0,
            "eligibility": "no_defensible_frame",
            "downgrade_reason": "no-compatible-observation-partition",
        }

    def frame_with_unavailable_margin(
        self,
        *,
        status,
        suppressed,
        uncertainty,
        missing_reason,
    ):
        frame = self.frame()
        source_observation = status != "missing"
        frame["cells"].append({
            "cell_id": "unavailable-finance-role",
            "partition_id": "eligible-cohort-members",
            "dimension_values": {"role": "finance"},
            "relationship": "marginal",
            "origin": (
                "source_observation" if source_observation else "explicit_missing"
            ),
            "modeled_rule_id": None,
            "status": status,
            "structural_weight": None,
            "weight_semantic": None,
            "uncertainty": uncertainty,
            "suppressed": suppressed,
            "source_observations": (
                [{
                    "batch_id": "operations-frame-batch",
                    "cell_id": "unavailable-finance-role",
                }]
                if source_observation
                else []
            ),
            "calibration_factor": None,
        })
        frame["margins"] = [{
            "partition_id": "eligible-cohort-members",
            "dimensions": ["role"],
            "cell_ids": ["unavailable-finance-role"],
            "missing_reason": missing_reason,
        }]
        return frame

    def mixed_partition_frame(self):
        frame = self.frame()
        frame["units"].append({
            "partition_id": "eligible-employers",
            "unit": "employer",
            "denominator": "eligible-employers",
            "exact": False,
        })
        for cell_id, company_size, weight, status in (
            ("small-employers", "100-999", 0.7, "observed"),
            ("large-employers", "1000-plus", 0.3, "modeled"),
        ):
            frame["cells"].append({
                "cell_id": cell_id,
                "partition_id": "eligible-employers",
                "dimension_values": {
                    "company-size": company_size,
                    "role": "operations",
                },
                "relationship": "joint",
                "origin": "source_observation",
                "modeled_rule_id": None,
                "status": status,
                "structural_weight": weight,
                "weight_semantic": (
                    "experimental_modeled_weight"
                    if status == "modeled"
                    else "population_weight"
                ),
                "uncertainty": {"lower": weight, "upper": weight},
                "suppressed": False,
                "source_observations": [{
                    "batch_id": "employer-frame-batch",
                    "cell_id": cell_id,
                }],
                "calibration_factor": 1.0,
            })
        frame["cells"].append({
            "cell_id": "missing-finance-role",
            "partition_id": "eligible-cohort-members",
            "dimension_values": {"role": "finance"},
            "relationship": "marginal",
            "origin": "explicit_missing",
            "modeled_rule_id": None,
            "status": "missing",
            "structural_weight": None,
            "weight_semantic": None,
            "uncertainty": {"lower": None, "upper": None},
            "suppressed": False,
            "source_observations": [],
            "calibration_factor": None,
        })
        frame["margins"] = [{
            "partition_id": "eligible-cohort-members",
            "dimensions": ["role"],
            "cell_ids": ["missing-finance-role"],
            "missing_reason": "The finance-role margin is unavailable.",
        }]
        frame["joints"].append({
            "partition_id": "eligible-employers",
            "dimensions": ["company-size", "role"],
            "cell_ids": ["large-employers", "small-employers"],
            "missing_reason": None,
        })
        binding = deepcopy(frame["source_bindings"][0])
        binding["batch_id"] = "employer-frame-batch"
        binding["partition_id"] = "eligible-employers"
        binding["normalized_batch_sha256"] = "sha256:" + "3" * 64
        binding["raw_snapshot_sha256"] = "sha256:" + "4" * 64
        frame["source_bindings"].append(binding)
        frame["modeled_weight_by_dimension"].extend([{
            "partition_id": "eligible-employers",
            "dimension": dimension,
            "share": 0.3,
            "status": "supported",
        } for dimension in ("company-size", "role")])
        return frame

    def composition(
        self,
        *,
        frame=None,
        requested_tier=None,
        evidence_basis=None,
    ):
        if frame is None:
            frame = self.frame()
        usable_frame = frame["eligibility"] in {
            "eligible_tier_2",
            "eligible_tier_3",
        }
        if requested_tier is None:
            requested_tier = (
                "tier_3"
                if frame["eligibility"] == "eligible_tier_3"
                else (
                    "tier_2"
                    if frame["eligibility"] == "eligible_tier_2"
                    else "tier_1"
                )
            )
        if evidence_basis is None:
            evidence_basis = (
                "first_party_aggregate"
                if frame["eligibility"] == "eligible_tier_3"
                else ("public" if usable_frame else "none")
            )
        provisional = evidence_basis == "none"
        requested_rank = int(requested_tier[-1])
        maximum_rank = (
            3
            if (
                frame["eligibility"] == "eligible_tier_3"
                and evidence_basis in {"first_party_aggregate", "hybrid"}
            )
            else (2 if usable_frame else 1)
        )
        achieved_tier = f"tier_{min(requested_rank, maximum_rank, 3)}"
        downgraded = achieved_tier != requested_tier
        group_cells = (
            [
                ("midmarket-group", ["midmarket-operations"], 0.7,
                 "authorized_cohort_weight"),
                ("enterprise-group", ["enterprise-operations"], 0.3,
                 "experimental_modeled_weight"),
            ]
            if usable_frame
            else [
                ("midmarket-group", [], 0.7, "planning_allocation"),
                ("enterprise-group", [], 0.3, "planning_allocation"),
            ]
        )
        return {
            "schema_version": COMPOSITION_PLAN_VERSION,
            "composition_id": "operations-panel-composition",
            "plan_version": "1.0.0",
            "built_at": "2026-07-24T14:00:00Z",
            "evidence_basis": evidence_basis,
            "requested_tier": requested_tier,
            "achieved_tier": achieved_tier,
            "tier_reason_codes": (
                ["requested-tier-exceeds-supported-route"] if downgraded else []
            ),
            "lost_claims": (
                ["The requested higher-tier composition claim is not supported."]
                if downgraded
                else []
            ),
            "frame_binding": {
                "frame_result_sha256": digest(frame),
                "frame_sha256": digest(frame) if usable_frame else None,
                "frame_id": frame["frame_id"] if usable_frame else None,
                "selection": (
                    {
                        "partition_id": "eligible-cohort-members",
                        "relationship": "joint",
                        "dimensions": ["company-size", "role"],
                    }
                    if usable_frame
                    else None
                ),
            },
            "structural_groups": [{
                "structural_group_id": group_id,
                "origin": (
                    "frame_cells"
                    if usable_frame
                    else (
                        "tier_1_provisional"
                        if provisional
                        else "tier_1_evidence"
                    )
                ),
                "cell_ids": cell_ids,
                "structural_finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "structural_weight": weight,
                "weight_semantic": semantic,
                "must_cover": True,
            } for group_id, cell_ids, weight, semantic in group_cells],
            "overlay_hypotheses": [{
                "overlay_id": "proof-seeking",
                "description": "Needs implementation proof.",
                "allocation_basis": (
                    "experimental" if provisional else "estimated"
                ),
                "finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "topic_bindings": (
                    []
                    if provisional
                    else [{
                        "topic_id": "implementation-proof",
                        "evidence_ids": ["evidence-implementation-proof"],
                    }]
                ),
            }, {
                "overlay_id": "risk-averse",
                "description": "Needs risk controls.",
                "allocation_basis": (
                    "experimental" if provisional else "estimated"
                ),
                "finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "topic_bindings": (
                    []
                    if provisional
                    else [{
                        "topic_id": "implementation-risk",
                        "evidence_ids": ["evidence-implementation-proof"],
                    }]
                ),
            }],
            "profiles": [{
                "profile_id": "midmarket-proof-seeking",
                "structural_group_id": "midmarket-group",
                "overlay_ids": ["proof-seeking"],
                "support_status": (
                    "provisional" if provisional else "supported"
                ),
                "support_finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "support_evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "conditional_overlay_allocation": 0.75,
                "overlay_weight_semantic": "planning_allocation",
                "effective_profile_allocation": 0.525,
                "effective_weight_semantic": (
                    "authorized_cohort_weight"
                    if usable_frame
                    else "planning_allocation"
                ),
                "source_cell_ids": (
                    ["midmarket-operations"] if usable_frame else []
                ),
            }, {
                "profile_id": "midmarket-risk-averse",
                "structural_group_id": "midmarket-group",
                "overlay_ids": ["risk-averse"],
                "support_status": (
                    "provisional" if provisional else "supported"
                ),
                "support_finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "support_evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "conditional_overlay_allocation": 0.25,
                "overlay_weight_semantic": "planning_allocation",
                "effective_profile_allocation": 0.175,
                "effective_weight_semantic": (
                    "authorized_cohort_weight"
                    if usable_frame
                    else "planning_allocation"
                ),
                "source_cell_ids": (
                    ["midmarket-operations"] if usable_frame else []
                ),
            }, {
                "profile_id": "enterprise-risk-averse",
                "structural_group_id": "enterprise-group",
                "overlay_ids": ["risk-averse"],
                "support_status": (
                    "provisional" if provisional else "supported"
                ),
                "support_finding_ids": (
                    [] if provisional else ["finding-implementation-proof"]
                ),
                "support_evidence_ids": (
                    [] if provisional else ["evidence-implementation-proof"]
                ),
                "conditional_overlay_allocation": 1.0,
                "overlay_weight_semantic": "planning_allocation",
                "effective_profile_allocation": 0.3,
                "effective_weight_semantic": (
                    "experimental_modeled_weight"
                    if usable_frame
                    else "planning_allocation"
                ),
                "source_cell_ids": (
                    ["enterprise-operations"] if usable_frame else []
                ),
            }],
            "unsupported_combinations": [{
                "structural_group_id": "enterprise-group",
                "overlay_ids": ["proof-seeking"],
                "reason_code": "unsupported-by-approved-evidence",
                "reason": "No supporting evidence for this pairing.",
            }],
            "allocation_constraints": ["Preserve must-cover groups."],
            "run_allocation_rules": {
                "reserve_strategy": "largest-remainder",
                "min_one_for_must_cover": True,
            },
            "required_diagnostics": ["effective-allocation-drift"],
            "modeled_cell_share": 0.3 if usable_frame else 0.0,
        }

    def validity(self, *, tier="tier_3", evidence_basis="first_party_aggregate"):
        return {
            "schema_version": VALIDITY_PROFILE_VERSION,
            "validity_id": "operations-panel-validity",
            "binding_state": "panel_final",
            "panel_id": self.v2_panel["panel_id"],
            "panel_tier": tier,
            "evidence_basis": evidence_basis,
            "axes": {
                "structural_frame": {
                    "status": "supported",
                    "coverage": 1.0,
                    "limitations": ["One dimension includes modeled cells."],
                },
                "overlay_evidence": {
                    "status": "directional",
                    "coverage": None,
                    "limitations": ["Overlay allocation is planning-only."],
                },
                "allocation_fidelity": {
                    "status": "supported",
                    "coverage": 1.0,
                    "limitations": [],
                },
                "outcome_calibration": {
                    "status": "not_available",
                    "coverage": None,
                    "limitations": ["No outcome feedback supplied."],
                },
                "external_validation": {
                    "status": "not_available",
                    "coverage": None,
                    "limitations": ["No held-out evidence supplied."],
                },
            },
            "predeclared_validation_design": None,
            "held_out_outcome_evidence": [],
            "source_bindings": {
                "brief_sha256": "sha256:" + "a" * 64,
                "panel_sha256": "sha256:" + "b" * 64,
                "frame_result_sha256": "sha256:" + "c" * 64,
                "frame_sha256": "sha256:" + "c" * 64,
                "composition_sha256": "sha256:" + "d" * 64,
            },
        }

    def outcome_feedback(self):
        return {
            "schema_version": OUTCOME_FEEDBACK_VERSION,
            "feedback_id": "campaign-outcome-1",
            "panel_id": self.v2_panel["panel_id"],
            "study_id": "creative-study-1",
            "variant_id": "variant-a",
            "cohort_id": "operations-cohort",
            "metric": {
                "name": "qualified-conversion-rate",
                "definition": "Qualified conversions divided by attributed exposures.",
            },
            "metric_direction": "higher_is_better",
            "units": {
                "exposure": "served-impression",
                "outcome": "qualified-conversion",
            },
            "windows": {
                "measurement": "2026-07-01/2026-07-14",
                "attribution": "7-day-click",
            },
            "aggregate": {
                "numerator": 25.0,
                "denominator": 1000.0,
                "value": 0.025,
            },
            "design": "observational",
            "source": {
                "source_id": "acme-campaign-export",
                "permission_confirmed": True,
            },
            "holdout": False,
            "missingness": "No missing aggregate rows.",
            "limitations": ["Observational association only."],
            "source_sha256": "sha256:" + "e" * 64,
        }

    def test_closed_enums_and_tier_evidence_independence(self):
        self.assertEqual(
            {"tier_1", "tier_2", "tier_3", "tier_4"}, PANEL_TIERS
        )
        self.assertIn("none", EVIDENCE_BASES)
        self.assertEqual(
            {"observed", "derived", "modeled", "missing"}, CELL_STATUSES
        )
        self.assertIn("no_defensible_frame", FRAME_ELIGIBILITY)
        self.assertIn("held_out_validated", VALIDITY_AXIS_STATUSES)
        for tier in PANEL_TIERS:
            for basis in EVIDENCE_BASES:
                validity = self.validity(tier=tier, evidence_basis=basis)
                if tier == "tier_1":
                    validity["source_bindings"]["frame_sha256"] = None
                if tier == "tier_4":
                    validity["predeclared_validation_design"] = {
                        "design_id": "validation-plan-1",
                        "registered_at": "2026-07-01T12:00:00Z",
                        "holdout_definition": "Chronological 20% holdout.",
                        "metrics": ["qualified-conversion-rate"],
                    }
                    validity["held_out_outcome_evidence"] = [
                        "sha256:" + "f" * 64
                    ]
                    validity["axes"]["external_validation"]["status"] = (
                        "held_out_validated"
                    )
                self.assertEqual(tier, validate_validity_profile(validity)["panel_tier"])
                self.assertEqual(
                    basis, validate_validity_profile(validity)["evidence_basis"]
                )

    def test_frame_request_and_observation_nested_allowlists_are_strict(self):
        self.assertEqual(self.frame_request(), validate_frame_request(self.frame_request()))
        self.assertEqual(
            self.observation_batch(),
            validate_observation_batch(self.observation_batch()),
        )
        for document, validator, mutate in (
            (
                self.frame_request(),
                validate_frame_request,
                lambda value: value["time_basis"].update(extra=True),
            ),
            (
                self.observation_batch(),
                validate_observation_batch,
                lambda value: value["cells"][0]["uncertainty"].update(extra=True),
            ),
        ):
            mutate(document)
            with self.assertRaisesRegex(ValueError, "unknown"):
                validator(document)

    def test_frame_request_predeclares_exact_models_and_calibration(self):
        request = self.frame_request()
        canonical = self.assert_canonical(request, validate_frame_request)
        self.assertFalse(canonical["proxy_universes"][0]["exact"])
        self.assertEqual(
            "declared_weight",
            canonical["modeled_cell_rules"][0]["method"],
        )
        self.assertEqual(
            2.0,
            canonical["calibration_rules"][0]["calibration_factor"],
        )
        invalid = self.frame_request()
        invalid["modeled_cell_rules"][0]["method"] = "arbitrary_expression"
        with self.assertRaisesRegex(ValueError, "declared_weight"):
            validate_frame_request(invalid)
        valid_boundary = self.frame_request()
        valid_boundary["calibration_rules"][0]["calibration_factor"] = 3.0
        self.assertEqual(
            3.0,
            validate_frame_request(valid_boundary)["calibration_rules"][0][
                "calibration_factor"
            ],
        )
        invalid_boundary = self.frame_request()
        invalid_boundary["calibration_rules"][0]["calibration_factor"] = (
            3.0000001
        )
        with self.assertRaisesRegex(ValueError, "at most 3"):
            validate_frame_request(invalid_boundary)

    def test_observation_batch_validates_dynamic_dimensions_and_finite_numbers(self):
        batch = self.observation_batch()
        batch["cells"][0]["dimension_values"]["invented"] = "value"
        with self.assertRaisesRegex(ValueError, "dimension"):
            validate_observation_batch(batch)
        for value in (float("nan"), float("inf"), float("-inf")):
            batch = self.observation_batch()
            batch["cells"][0]["estimate"] = value
            with self.assertRaisesRegex(ValueError, "finite"):
                validate_observation_batch(batch)

    def test_observation_batch_rejects_duplicate_full_coordinates(self):
        batch = self.observation_batch()
        duplicate = deepcopy(batch["cells"][0])
        duplicate["cell_id"] = "duplicate-midmarket-operations"
        duplicate["estimate"] = 10.0
        duplicate["uncertainty"] = {
            "lower": 9.0,
            "upper": 11.0,
            "method": "reported-interval",
        }
        batch["cells"].append(duplicate)
        self.seal_observation_batch(batch)

        with self.assertRaisesRegex(ValueError, "duplicate structural coordinate"):
            validate_observation_batch(batch)

    def test_observation_coordinate_identity_includes_value_and_relationship(self):
        batch = self.observation_batch()
        distinct_value = deepcopy(batch["cells"][0])
        distinct_value["cell_id"] = "small-business-operations"
        distinct_value["dimension_values"]["company-size"] = "1-99"
        distinct_relationship = deepcopy(batch["cells"][0])
        distinct_relationship["cell_id"] = "midmarket-operations-marginal"
        distinct_relationship["relationship"] = "marginal"
        batch["cells"].extend([distinct_value, distinct_relationship])
        self.seal_observation_batch(batch)

        self.assertEqual(batch, validate_observation_batch(batch))

    def test_observation_batch_rejects_stale_normalized_self_hash(self):
        batch = self.observation_batch()
        old_digest = batch["normalized_batch_sha256"]
        batch["cells"][0]["estimate"] = 999.0
        batch["cells"][0]["uncertainty"] = {
            "lower": 999.0,
            "upper": 999.0,
            "method": "reported-interval",
        }
        self.assertEqual(old_digest, batch["normalized_batch_sha256"])

        with self.assertRaisesRegex(ValueError, "normalized_batch_sha256.*match"):
            validate_observation_batch(batch)

    def test_unavailable_observation_preserves_null_uncertainty_bounds(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                batch = self.observation_batch()
                cell = batch["cells"][0]
                cell["status"] = status
                cell["suppressed"] = suppressed
                cell["estimate"] = None
                cell["uncertainty"] = {
                    "lower": None,
                    "upper": None,
                    "method": "Bounds unavailable in the source.",
                }
                self.seal_observation_batch(batch)
                try:
                    canonical = validate_observation_batch(batch)
                except ValueError as exc:
                    self.fail(
                        "valid missing or suppressed observation was rejected: "
                        f"{exc}"
                    )
                canonical_cell = canonical["cells"][0]
                self.assertIsNone(canonical_cell["estimate"])
                self.assertIsNone(canonical_cell["uncertainty"]["lower"])
                self.assertIsNone(canonical_cell["uncertainty"]["upper"])
                self.assertNotEqual(0, canonical_cell["uncertainty"]["lower"])
                self.assertNotEqual(0, canonical_cell["uncertainty"]["upper"])

    def test_unavailable_observation_requires_null_estimate(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                batch = self.observation_batch()
                cell = batch["cells"][0]
                cell["status"] = status
                cell["suppressed"] = suppressed
                cell["estimate"] = 0.0
                with self.assertRaisesRegex(ValueError, "must be null"):
                    validate_observation_batch(batch)

    def test_unavailable_bounds_require_nonempty_uncertainty_method(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                batch = self.observation_batch()
                cell = batch["cells"][0]
                cell["status"] = status
                cell["suppressed"] = suppressed
                cell["estimate"] = None
                cell["uncertainty"] = {
                    "lower": None,
                    "upper": None,
                    "method": " ",
                }
                with self.assertRaisesRegex(ValueError, "method"):
                    validate_observation_batch(batch)

    def test_available_observation_rejects_null_uncertainty_bounds(self):
        for status in ("observed", "derived", "modeled"):
            with self.subTest(status=status):
                batch = self.observation_batch()
                cell = batch["cells"][0]
                cell["status"] = status
                cell["suppressed"] = False
                cell["uncertainty"] = {
                    "lower": None,
                    "upper": None,
                    "method": "Bounds unavailable.",
                }
                with self.assertRaisesRegex(ValueError, "lower.*numeric"):
                    validate_observation_batch(batch)

    def test_population_frame_reconciles_weights_and_source_bindings(self):
        frame = self.frame()
        self.assert_canonical(frame, validate_population_frame)
        frame["cells"][0]["structural_weight"] = 0.59
        with self.assertRaisesRegex(ValueError, "reconcile"):
            validate_population_frame(frame)
        frame = self.frame()
        frame["cells"][0]["source_observations"] = []
        with self.assertRaisesRegex(ValueError, "source"):
            validate_population_frame(frame)
        frame = self.frame()
        frame["source_bindings"][0]["normalized_batch_sha256"] = "forged"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_population_frame(frame)

    def test_population_frame_rejects_duplicate_partition_coordinates(self):
        frame = self.frame()
        duplicate = deepcopy(frame["cells"][0])
        duplicate["cell_id"] = "duplicate-midmarket-operations"
        duplicate["source_observations"] = [{
            "batch_id": "operations-frame-batch",
            "cell_id": "duplicate-midmarket-operations",
        }]
        duplicate["structural_weight"] = 0.35
        duplicate["uncertainty"] = {"lower": 0.32, "upper": 0.38}
        frame["cells"][0]["structural_weight"] = 0.35
        frame["cells"][0]["uncertainty"] = {"lower": 0.32, "upper": 0.38}
        frame["cells"].append(duplicate)
        frame["joints"][0]["cell_ids"].append(duplicate["cell_id"])

        with self.assertRaisesRegex(ValueError, "duplicate structural coordinate"):
            validate_population_frame(frame)

    def test_population_frame_preserves_partition_and_relationship_identity(self):
        frame = self.frame(eligibility="experimental")
        frame["units"].append({
            "partition_id": "eligible-employers",
            "unit": "employer",
            "denominator": "eligible-employers",
            "exact": False,
        })
        first, second = frame["cells"]
        first["dimension_values"] = {"role": "operations"}
        first["relationship"] = "marginal"
        first["structural_weight"] = 1.0
        first["uncertainty"] = {"lower": 1.0, "upper": 1.0}
        second["partition_id"] = "eligible-employers"
        second["structural_weight"] = 1.0
        second["uncertainty"] = {"lower": 1.0, "upper": 1.0}
        second["source_observations"] = [{
            "batch_id": "employer-frame-batch",
            "cell_id": "enterprise-operations",
        }]
        frame["margins"] = [{
            "partition_id": "eligible-cohort-members",
            "dimensions": ["role"],
            "cell_ids": ["midmarket-operations"],
            "missing_reason": None,
        }]
        frame["joints"] = [{
            "partition_id": "eligible-employers",
            "dimensions": ["company-size", "role"],
            "cell_ids": ["enterprise-operations"],
            "missing_reason": None,
        }, {
            "partition_id": "eligible-cohort-members",
            "dimensions": ["company-size", "region"],
            "cell_ids": [],
            "missing_reason": "No source publishes the required joint.",
        }]
        frame["structural_dimensions"].append("region")
        binding = deepcopy(frame["source_bindings"][0])
        binding["batch_id"] = "employer-frame-batch"
        binding["partition_id"] = "eligible-employers"
        binding["normalized_batch_sha256"] = "sha256:" + "3" * 64
        binding["raw_snapshot_sha256"] = "sha256:" + "4" * 64
        frame["source_bindings"].append(binding)
        frame["modeled_weight_by_dimension"] = [{
            "partition_id": "eligible-cohort-members",
            "dimension": "role",
            "share": 0.0,
            "status": "supported",
        }, {
            "partition_id": "eligible-employers",
            "dimension": "company-size",
            "share": 1.0,
            "status": "experimental",
        }, {
            "partition_id": "eligible-employers",
            "dimension": "role",
            "share": 1.0,
            "status": "experimental",
        }]
        frame["modeled_weight_share"] = 1.0
        frame["downgrade_reason"] = "modeled-share-above-threshold"
        self.assert_canonical(frame, validate_population_frame)
        invalid = deepcopy(frame)
        invalid["cells"][0]["dimension_values"]["invented"] = "value"
        with self.assertRaisesRegex(ValueError, "undeclared dimension"):
            validate_population_frame(invalid)

    def test_frame_modeled_share_boundary_is_per_dimension_and_conservative(self):
        frame = self.frame()
        self.assertEqual(
            [
                {
                    "partition_id": "eligible-cohort-members",
                    "dimension": "company-size",
                    "share": 0.3,
                    "status": "supported",
                },
                {
                    "partition_id": "eligible-cohort-members",
                    "dimension": "role",
                    "share": 0.3,
                    "status": "supported",
                },
            ],
            self.assert_canonical(
                frame, validate_population_frame
            )["modeled_weight_by_dimension"],
        )
        experimental = self.frame(eligibility="experimental")
        experimental["cells"][0]["structural_weight"] = 0.6999999
        experimental["cells"][1]["structural_weight"] = 0.3000001
        for row in experimental["modeled_weight_by_dimension"]:
            row["share"] = 0.3000001
            row["status"] = "experimental"
        experimental["modeled_weight_share"] = 0.3000001
        experimental["downgrade_reason"] = "modeled-share-above-threshold"
        self.assert_canonical(experimental, validate_population_frame)
        invalid = deepcopy(experimental)
        invalid["modeled_weight_by_dimension"][0]["status"] = "supported"
        with self.assertRaisesRegex(ValueError, "experimental"):
            validate_population_frame(invalid)

    def test_missing_frame_cell_preserves_nulls_without_changing_weight_total(self):
        frame = self.frame()
        frame["cells"].append({
            "cell_id": "missing-finance-role",
            "partition_id": "eligible-cohort-members",
            "dimension_values": {"role": "finance"},
            "relationship": "marginal",
            "origin": "explicit_missing",
            "modeled_rule_id": None,
            "status": "missing",
            "structural_weight": None,
            "weight_semantic": None,
            "uncertainty": {"lower": None, "upper": None},
            "suppressed": False,
            "source_observations": [],
            "calibration_factor": None,
        })
        frame["margins"] = [{
            "partition_id": "eligible-cohort-members",
            "dimensions": ["role"],
            "cell_ids": ["missing-finance-role"],
            "missing_reason": "The required role margin is unavailable.",
        }]
        self.assert_canonical(frame, validate_population_frame)
        invalid = deepcopy(frame)
        invalid["cells"][-1]["structural_weight"] = 0.0
        with self.assertRaisesRegex(ValueError, "must be null"):
            validate_population_frame(invalid)
        suppressed = deepcopy(frame)
        suppressed_cell = suppressed["cells"][-1]
        suppressed_cell["status"] = "modeled"
        suppressed_cell["suppressed"] = True
        suppressed_cell["origin"] = "source_observation"
        suppressed_cell["source_observations"] = [{
            "batch_id": "operations-frame-batch",
            "cell_id": "suppressed-finance-role",
        }]
        self.assert_canonical(suppressed, validate_population_frame)

    def test_unavailable_frame_cells_require_paired_null_uncertainty(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                frame = self.frame_with_unavailable_margin(
                    status=status,
                    suppressed=suppressed,
                    uncertainty={"lower": 0.0, "upper": 0.0},
                    missing_reason="The source does not publish this margin.",
                )
                with self.assertRaisesRegex(
                    ValueError, "uncertainty.*null|bounds.*null"
                ):
                    validate_population_frame(frame)

    def test_all_unavailable_frame_collections_require_missing_reason(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                frame = self.frame_with_unavailable_margin(
                    status=status,
                    suppressed=suppressed,
                    uncertainty={"lower": None, "upper": None},
                    missing_reason=None,
                )
                with self.assertRaisesRegex(ValueError, "missing_reason"):
                    validate_population_frame(frame)

    def test_source_observation_batch_must_match_cell_partition(self):
        frame = self.frame()
        frame["units"].append({
            "partition_id": "eligible-employers",
            "unit": "employer",
            "denominator": "eligible-employers",
            "exact": False,
        })
        binding = deepcopy(frame["source_bindings"][0])
        binding["batch_id"] = "employer-frame-batch"
        binding["partition_id"] = "eligible-employers"
        binding["normalized_batch_sha256"] = "sha256:" + "3" * 64
        binding["raw_snapshot_sha256"] = "sha256:" + "4" * 64
        frame["source_bindings"].append(binding)
        frame["cells"][0]["source_observations"] = [{
            "batch_id": "employer-frame-batch",
            "cell_id": "midmarket-operations",
        }]
        with self.assertRaisesRegex(ValueError, "partition"):
            validate_population_frame(frame)

    def test_modeled_share_boundary_uses_calculated_share_not_declared_rounding(self):
        frame = self.frame()
        frame["cells"][0]["structural_weight"] = 0.6999999999
        frame["cells"][1]["structural_weight"] = 0.3000000001
        with self.assertRaisesRegex(ValueError, "experimental"):
            validate_population_frame(frame)

        experimental = deepcopy(frame)
        for row in experimental["modeled_weight_by_dimension"]:
            row["share"] = 0.3000000001
            row["status"] = "experimental"
        experimental["modeled_weight_share"] = 0.3000000001
        experimental["eligibility"] = "experimental"
        experimental["downgrade_reason"] = "modeled-share-above-threshold"
        self.assert_canonical(experimental, validate_population_frame)

    def test_no_defensible_frame_is_canonical_but_only_in_no_frame_state(self):
        no_frame = self.no_frame()
        self.assert_canonical(no_frame, validate_population_frame)
        invalid = deepcopy(no_frame)
        invalid["eligibility"] = "experimental"
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            validate_population_frame(invalid)
        invalid = deepcopy(no_frame)
        invalid["coverage_assessment"]["known_gaps"] = []
        with self.assertRaisesRegex(ValueError, "known_gaps"):
            validate_population_frame(invalid)

    def test_population_frame_modeled_share_uses_weight_not_cell_count(self):
        frame = self.frame()
        self.assertEqual(0.3, validate_population_frame(frame)["modeled_weight_share"])
        frame["modeled_weight_share"] = 0.5
        with self.assertRaisesRegex(ValueError, "modeled_weight_share"):
            validate_population_frame(frame)

    def test_composition_reconciles_conditional_and_effective_weights(self):
        frame = self.frame()
        composition = self.composition(frame=frame)
        self.assertEqual(
            composition,
            validate_composition_plan(composition, frame=frame),
        )
        composition["profiles"][0]["conditional_overlay_allocation"] = 0.7
        with self.assertRaisesRegex(ValueError, "conditional"):
            validate_composition_plan(composition, frame=frame)
        composition = self.composition(frame=frame)
        composition["profiles"][0]["effective_profile_allocation"] = 0.44
        with self.assertRaisesRegex(ValueError, "effective"):
            validate_composition_plan(composition, frame=frame)

    def test_composition_selects_one_collection_and_excludes_other_cells(self):
        frame = self.mixed_partition_frame()
        composition = self.composition(frame=frame)
        self.assert_canonical(
            composition,
            lambda value: validate_composition_plan(value, frame=frame),
        )
        for cell_id in ("small-employers", "missing-finance-role"):
            invalid = deepcopy(composition)
            invalid["structural_groups"][0]["cell_ids"].append(cell_id)
            invalid["profiles"][0]["source_cell_ids"].append(cell_id)
            invalid["profiles"][1]["source_cell_ids"].append(cell_id)
            with self.subTest(cell_id=cell_id):
                with self.assertRaisesRegex(
                    ValueError, "selected|available|collection"
                ):
                    validate_composition_plan(invalid, frame=frame)

    def test_composition_requires_evidence_backed_groups_and_topic_overlays(self):
        frame = self.frame()
        composition = self.composition(frame=frame)
        for key in ("structural_finding_ids", "evidence_ids"):
            invalid = deepcopy(composition)
            invalid["structural_groups"][0][key] = []
            with self.subTest(group_key=key):
                with self.assertRaisesRegex(ValueError, key):
                    validate_composition_plan(invalid, frame=frame)
        invalid = deepcopy(composition)
        invalid["overlay_hypotheses"][0]["topic_bindings"][0][
            "evidence_ids"
        ] = ["unbound-evidence"]
        with self.assertRaisesRegex(ValueError, "subset|overlay evidence"):
            validate_composition_plan(invalid, frame=frame)

    def test_composition_supports_exact_multi_overlay_profile_signatures(self):
        frame = self.frame()
        composition = self.composition(frame=frame)
        composition["profiles"][0]["overlay_ids"] = [
            "proof-seeking",
            "risk-averse",
        ]
        self.assert_canonical(
            composition,
            lambda value: validate_composition_plan(value, frame=frame),
        )
        duplicate = deepcopy(composition)
        duplicate["profiles"][0]["overlay_ids"].append("risk-averse")
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_composition_plan(duplicate, frame=frame)
        unsupported = deepcopy(composition)
        unsupported["unsupported_combinations"].append({
            "structural_group_id": "midmarket-group",
            "overlay_ids": ["risk-averse", "proof-seeking"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "The exact combined signature is not supported.",
        })
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_composition_plan(unsupported, frame=frame)

    def test_composition_tier_outcome_and_experimental_overlay_are_strict(self):
        frame = self.frame(eligibility="eligible_tier_2")
        downgraded = self.composition(
            frame=frame,
            requested_tier="tier_3",
            evidence_basis="public",
        )
        self.assertEqual("tier_2", downgraded["achieved_tier"])
        self.assert_canonical(
            downgraded,
            lambda value: validate_composition_plan(value, frame=frame),
        )
        missing_reason = deepcopy(downgraded)
        missing_reason["tier_reason_codes"] = []
        with self.assertRaisesRegex(ValueError, "tier_reason_codes"):
            validate_composition_plan(missing_reason, frame=frame)

        experimental = self.composition(frame=self.frame())
        experimental["overlay_hypotheses"][0]["allocation_basis"] = (
            "experimental"
        )
        experimental["achieved_tier"] = "tier_1"
        experimental["tier_reason_codes"] = [
            "experimental-overlay-allocation"
        ]
        experimental["lost_claims"] = [
            "Population-grounded overlay composition is not supported."
        ]
        with self.assertRaisesRegex(ValueError, "Tier 1|tier_1"):
            validate_composition_plan(
                {**experimental, "achieved_tier": "tier_3"},
                frame=self.frame(),
            )

    def test_composition_tier_1_binds_no_frame_provisional_groups(self):
        frame = self.no_frame()
        composition = self.composition(
            frame=frame,
            requested_tier="tier_1",
            evidence_basis="none",
        )
        self.assert_canonical(
            composition,
            lambda value: validate_composition_plan(value, frame=frame),
        )
        self.assertTrue(all(
            not group["cell_ids"]
            and group["origin"] == "tier_1_provisional"
            and not group["structural_finding_ids"]
            and not group["evidence_ids"]
            for group in composition["structural_groups"]
        ))
        self.assertTrue(all(
            not profile["source_cell_ids"]
            and profile["support_status"] == "provisional"
            and not profile["support_finding_ids"]
            and not profile["support_evidence_ids"]
            for profile in composition["profiles"]
        ))

    def test_composition_modeled_share_is_selected_and_quota_keys_are_forbidden(self):
        frame = self.mixed_partition_frame()
        composition = self.composition(frame=frame)
        self.assertEqual(
            0.3,
            validate_composition_plan(composition, frame=frame)[
                "modeled_cell_share"
            ],
        )
        for key in (
            "study_quota",
            "slot_count",
            "panelist_count",
            "capacity",
        ):
            invalid = deepcopy(composition)
            invalid["profiles"][0][key] = 16
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "quota|slot|panelist|capacity"):
                    validate_composition_plan(invalid, frame=frame)

    def test_mixed_group_modeled_share_uses_effective_cell_weight(self):
        frame = self.frame()
        frame["cells"][0]["weight_semantic"] = "experimental_modeled_weight"
        composition = self.composition(frame=frame)
        composition["structural_groups"] = [{
            "structural_group_id": "all-operations",
            "origin": "frame_cells",
            "cell_ids": ["midmarket-operations", "enterprise-operations"],
            "structural_finding_ids": ["finding-implementation-proof"],
            "evidence_ids": ["evidence-implementation-proof"],
            "structural_weight": 1.0,
            "weight_semantic": "experimental_modeled_weight",
            "must_cover": True,
        }]
        composition["profiles"] = [{
            "profile_id": "all-risk-averse",
            "structural_group_id": "all-operations",
            "overlay_ids": ["risk-averse"],
            "support_status": "supported",
            "support_finding_ids": ["finding-implementation-proof"],
            "support_evidence_ids": ["evidence-implementation-proof"],
            "conditional_overlay_allocation": 1.0,
            "overlay_weight_semantic": "planning_allocation",
            "effective_profile_allocation": 1.0,
            "effective_weight_semantic": "experimental_modeled_weight",
            "source_cell_ids": [
                "midmarket-operations",
                "enterprise-operations",
            ],
        }]
        composition["unsupported_combinations"] = [{
            "structural_group_id": "all-operations",
            "overlay_ids": ["proof-seeking"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "No supporting evidence for this pairing.",
        }]
        composition["modeled_cell_share"] = 0.3
        self.assertEqual(
            0.3,
            validate_composition_plan(composition, frame=frame)[
                "modeled_cell_share"
            ],
        )

    def test_composition_rejects_unsupported_cross_products_and_bad_semantics(self):
        frame = self.frame()
        composition = self.composition(frame=frame)
        composition["profiles"].append({
            **deepcopy(composition["profiles"][0]),
            "profile_id": "forbidden-enterprise-proof",
            "structural_group_id": "enterprise-group",
            "source_cell_ids": ["enterprise-operations"],
            "conditional_overlay_allocation": 0.0,
            "effective_profile_allocation": 0.0,
            "effective_weight_semantic": "experimental_modeled_weight",
        })
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_composition_plan(composition, frame=frame)
        composition = self.composition(frame=frame)
        composition["structural_groups"][0]["weight_semantic"] = "confidence"
        with self.assertRaisesRegex(ValueError, "weight semantic"):
            validate_composition_plan(composition, frame=frame)
        frame["cells"][0]["weight_semantic"] = "experimental_modeled_weight"
        composition = self.composition(frame=frame)
        composition["structural_groups"][0]["cell_ids"].append(
            "enterprise-operations"
        )
        composition["structural_groups"][0]["structural_weight"] = 1.0
        composition["structural_groups"][0]["weight_semantic"] = (
            "experimental_modeled_weight"
        )
        composition["structural_groups"].pop()
        composition["profiles"] = [{
            **deepcopy(composition["profiles"][0]),
            "conditional_overlay_allocation": 1.0,
            "effective_profile_allocation": 1.0,
            "effective_weight_semantic": "experimental_modeled_weight",
        }]
        composition["unsupported_combinations"] = [{
            "structural_group_id": "midmarket-group",
            "overlay_ids": ["risk-averse"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "No supporting evidence for this pairing.",
        }]
        with self.assertRaisesRegex(ValueError, "exactly.*structural group"):
            validate_composition_plan(composition, frame=frame)

    def test_tier_eligibility_and_tier_3_calibration_rules(self):
        self.assertEqual(
            "eligible_tier_2",
            validate_population_frame(self.frame(eligibility="eligible_tier_2"))[
                "eligibility"
            ],
        )
        documents = self.v3_pair()
        documents[2]["cells"][0]["calibration_factor"] = 3.000000001
        self.rebind_population_documents(documents)
        with self.assertRaisesRegex(ValueError, "calibration_factor"):
            validate_audience_research_v3(
                documents[0],
                documents[1],
                frame=documents[2],
                composition=documents[3],
                validity=documents[4],
                workflow_state=documents[5],
                construction_audit=documents[6],
            )

    def test_tier_2_does_not_inherit_tier_3_authorization_or_calibration_rules(self):
        documents = self.v3_pair(tier="tier_2", evidence_basis="public")
        documents[2]["cells"][0]["calibration_factor"] = 3.5
        self.rebind_population_documents(documents)
        try:
            validate_audience_research_v3(
                documents[0],
                documents[1],
                frame=documents[2],
                composition=documents[3],
                validity=documents[4],
                workflow_state=documents[5],
                construction_audit=documents[6],
            )
        except ValueError as exc:
            self.fail(f"valid Tier 1 no-frame result was rejected: {exc}")

    def test_tier_4_requires_held_out_validation_not_a_tier_3_handoff(self):
        documents = self.v3_pair(
            tier="tier_4",
            evidence_basis="first_party_aggregate",
        )
        documents[0]["authorized_audience_import"] = None
        documents[1]["authorized_handoff_sha256"] = None
        self.rebind_population_documents(documents)
        validate_audience_research_v3(
            documents[0],
            documents[1],
            frame=documents[2],
            composition=documents[3],
            validity=documents[4],
            workflow_state=documents[5],
            construction_audit=documents[6],
        )

    def test_tier_4_requires_predeclared_held_out_validation(self):
        validity = self.validity(tier="tier_4")
        with self.assertRaisesRegex(ValueError, "predeclared"):
            validate_validity_profile(validity)
        validity["predeclared_validation_design"] = {
            "design_id": "validation-plan-1",
            "registered_at": "2026-07-01T12:00:00Z",
            "holdout_definition": "Chronological 20% holdout.",
            "metrics": ["qualified-conversion-rate"],
        }
        validity["held_out_outcome_evidence"] = ["sha256:" + "f" * 64]
        validity["axes"]["external_validation"]["status"] = "held_out_validated"
        self.assertEqual("tier_4", validate_validity_profile(validity)["panel_tier"])

    def test_frame_provisional_validity_is_canonical_but_cannot_be_final(self):
        validity = self.validity()
        validity["binding_state"] = "frame_provisional"
        validity["panel_id"] = None
        validity["panel_tier"] = None
        validity["evidence_basis"] = None
        validity["source_bindings"] = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": "sha256:" + "c" * 64,
            "frame_sha256": "sha256:" + "c" * 64,
            "composition_sha256": None,
        }
        self.assert_canonical(validity, validate_validity_profile)
        invalid = deepcopy(validity)
        invalid["source_bindings"]["frame_result_sha256"] = None
        with self.assertRaisesRegex(ValueError, "frame_result_sha256"):
            validate_validity_profile(invalid)
        documents = list(self.v3_pair())
        documents[4] = validity
        documents[1]["validity_profile_sha256"] = digest(validity)
        with self.assertRaisesRegex(ValueError, "panel_final"):
            self.validate_v3_documents(documents)

    def test_panel_final_validity_requires_all_final_identities_and_bindings(self):
        for path in (
            ("panel_id",),
            ("panel_tier",),
            ("evidence_basis",),
            ("source_bindings", "brief_sha256"),
            ("source_bindings", "panel_sha256"),
            ("source_bindings", "composition_sha256"),
        ):
            validity = self.validity()
            target = validity
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = None
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "panel_final"):
                    validate_validity_profile(validity)

    def test_validity_axes_stay_separate_and_forbid_composite_fields_recursively(self):
        validity = self.validity()
        self.assertEqual(validity, validate_validity_profile(validity))
        for key in (
            "confidence",
            "confidence_score",
            "overall_score",
            "composite",
            "percentage",
        ):
            invalid = deepcopy(validity)
            invalid["axes"]["structural_frame"][key] = 0.9
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "forbidden validity field"):
                    validate_validity_profile(invalid)
        invalid = self.validity()
        del invalid["axes"]["outcome_calibration"]
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_validity_profile(invalid)

    def test_outcome_feedback_returns_only_canonical_copy_and_source_digest(self):
        feedback = self.outcome_feedback()
        result = validate_outcome_feedback(feedback)
        self.assertEqual({"canonical_copy", "source_digest"}, set(result))
        self.assertEqual(feedback, result["canonical_copy"])
        self.assertEqual("sha256:" + "e" * 64, result["source_digest"])
        for forbidden in ("score", "rank", "profile_weight", "frame_weight", "panel_version"):
            invalid = deepcopy(feedback)
            invalid[forbidden] = 1
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "unknown|prohibited"):
                    validate_outcome_feedback(invalid)

    def test_outcome_feedback_validation_cannot_mutate_panel_frame_or_composition(self):
        feedback = self.outcome_feedback()
        panel = deepcopy(self.v2_panel)
        frame = self.frame()
        composition = self.composition(frame=frame)
        before = tuple(canonical_bytes(value) for value in (panel, frame, composition))
        first = validate_outcome_feedback(feedback)
        second = validate_outcome_feedback(feedback)
        after = tuple(canonical_bytes(value) for value in (panel, frame, composition))
        self.assertEqual(before, after)
        self.assertEqual(first, second)

    def test_unknown_versions_are_rejected_by_every_validator(self):
        cases = [
            (self.frame_request(), validate_frame_request),
            (self.observation_batch(), validate_observation_batch),
            (self.frame(), validate_population_frame),
            (
                self.composition(),
                lambda value: validate_composition_plan(value, frame=self.frame()),
            ),
            (self.outcome_feedback(), validate_outcome_feedback),
            (self.validity(), validate_validity_profile),
        ]
        for document, validator in cases:
            document["schema_version"] = "future-v99"
            with self.subTest(validator=validator):
                with self.assertRaisesRegex(ValueError, "schema_version"):
                    validator(document)

    def test_public_validators_are_total_over_malformed_recursive_json(self):
        validators = (
            validate_frame_request,
            validate_observation_batch,
            validate_population_frame,
            lambda value: validate_composition_plan(value, frame=self.frame()),
            validate_outcome_feedback,
            validate_validity_profile,
        )
        malformed = (None, False, 7, "wrong", [], [1], {}, {"schema_version": []})
        for validator in validators:
            for value in malformed:
                with self.subTest(validator=validator, value=value):
                    with self.assertRaises(ValueError):
                        validator(value)

    def test_every_nested_object_has_a_strict_allowlist_and_malformed_shapes_fail_closed(self):
        cases = (
            (self.frame_request(), validate_frame_request),
            (self.observation_batch(), validate_observation_batch),
            (self.frame(), validate_population_frame),
            (
                self.composition(),
                lambda value: validate_composition_plan(value, frame=self.frame()),
            ),
            (self.outcome_feedback(), validate_outcome_feedback),
            (self.validity(), validate_validity_profile),
        )

        def paths(value, prefix=()):
            if isinstance(value, dict):
                yield prefix
                for key, child in value.items():
                    yield from paths(child, prefix + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from paths(child, prefix + (index,))

        def replace(value, path, replacement):
            clone = deepcopy(value)
            cursor = clone
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = replacement
            return clone

        def add_unknown(value, path):
            clone = deepcopy(value)
            cursor = clone
            for part in path:
                cursor = cursor[part]
            cursor["unexpected_contract_field"] = True
            return clone

        for original, validator in cases:
            for path in paths(original):
                with self.subTest(validator=validator, unknown_path=path):
                    with self.assertRaisesRegex(ValueError, "unknown|dimension"):
                        validator(add_unknown(original, path))
            for path in paths(original):
                if not path:
                    continue
                with self.subTest(validator=validator, malformed_path=path):
                    with self.assertRaises(ValueError):
                        validator(replace(original, path, {"malformed": True}))

    def v3_pair(self, *, tier="tier_3", evidence_basis="first_party_aggregate"):
        expected_allowed_use = (
            "Synthetic ad testing for the exact authorized aggregate cohort"
            if tier == "tier_3"
            else (
                "Directional synthetic ad testing under the named public proxy boundary"
                if tier == "tier_1" and evidence_basis == "public"
                else "Synthetic ad testing"
            )
        )
        base_v2_panel = deepcopy(self.v2_panel)
        base_v2_panel["governance"]["allowed_uses"] = [
            expected_allowed_use
        ]
        v2_brief_sha256 = bare_digest(self.v2_brief)
        v2_panel_sha256 = bare_digest(base_v2_panel)
        frame = (
            self.no_frame()
            if tier == "tier_1"
            else self.frame(
                eligibility=(
                    "eligible_tier_2"
                    if tier == "tier_2"
                    else "eligible_tier_3"
                )
            )
        )
        usable_frame_sha256 = (
            None
            if frame["eligibility"] == "no_defensible_frame"
            else digest(frame)
        )
        composition = self.composition(
            frame=frame,
            requested_tier=tier,
            evidence_basis=evidence_basis,
        )
        validity = self.validity(tier=tier, evidence_basis=evidence_basis)
        validity["source_bindings"] = {
            "brief_sha256": digest(self.v2_brief),
            "panel_sha256": "sha256:" + v2_panel_sha256,
            "frame_result_sha256": digest(frame),
            "frame_sha256": usable_frame_sha256,
            "composition_sha256": digest(composition),
        }
        if tier == "tier_4":
            validity["predeclared_validation_design"] = {
                "design_id": "validation-plan-1",
                "registered_at": "2026-07-01T12:00:00Z",
                "holdout_definition": "Chronological 20% holdout.",
                "metrics": ["qualified-conversion-rate"],
            }
            validity["held_out_outcome_evidence"] = ["sha256:" + "f" * 64]
            validity["axes"]["external_validation"]["status"] = "held_out_validated"
        brief = deepcopy(self.v2_brief)
        brief["schema_version"] = RESEARCH_BRIEF_V3
        brief.update({
            "panel_tier": tier,
            "evidence_basis": evidence_basis,
            "workflow_state_binding": "operations-leaders-build",
            "population_frame_result_sha256": digest(frame),
            "population_frame_sha256": usable_frame_sha256,
            "authorized_audience_import": {
                "handoff_schema_version": "authorized-audience-handoff-v1",
                "handoff_sha256": "sha256:" + "9" * 64,
                "status": "complete",
                "cohort_id": "operations-cohort",
                "exact_cohort_denominator": "all-eligible-cohort-members",
                "selection_statement": "Cohort fixed before creative review.",
                "coverage_statement": "All eligible cohort members are covered.",
                "max_calibration_factor": 2.0,
            } if tier in {"tier_3", "tier_4"} else None,
            "structural_findings": (
                [] if evidence_basis == "none"
                else ["finding-implementation-proof"]
            ),
            "overlay_findings": (
                [] if evidence_basis == "none"
                else ["finding-implementation-proof"]
            ),
            "claim_boundary": "Authorized cohort composition only.",
            "dimensional_validity": [{
                "dimension": "company-size",
                "status": "directional",
                "limitations": ["Modeled enterprise cell."],
            }],
            "scoped_approvals": [{
                "scope": "panel-construction",
                "status": "approved",
                "target_sha256": "sha256:" + v2_panel_sha256,
            }],
        })
        audit_input_bindings = {
            "brief_sha256": v2_brief_sha256,
            "panel_sha256": v2_panel_sha256,
            "evidence_ledger_sha256": "3" * 64,
            "finding_support_sha256": "4" * 64,
            "synthesis_matrix_sha256": "5" * 64,
            "report_manifest_sha256": "6" * 64,
            "population_frame_result_sha256": bare_digest(frame),
            "population_frame_sha256": (
                None if usable_frame_sha256 is None else bare_digest(frame)
            ),
            "composition_plan_sha256": bare_digest(composition),
            "validity_profile_sha256": bare_digest(validity),
            "authorized_handoff_sha256": (
                "9" * 64 if tier in {"tier_3", "tier_4"} else None
            ),
        }
        audit_checks = []
        for check_id in (
            "approved_evidence_only",
            "finding_support_complete",
            "contradictions_preserved",
            "segment_sufficiency",
            "profile_traceability",
            "inference_boundaries",
            "privacy_boundary",
            "count_semantics",
            "claim_tier",
            "population_frame_traceability",
            "weight_semantics",
            "authorized_handoff_traceability",
        ):
            audit_checks.append({
                "check_id": check_id,
                "status": (
                    "not_applicable"
                    if (
                        check_id == "authorized_handoff_traceability"
                        and tier not in {"tier_3", "tier_4"}
                    )
                    else "pass"
                ),
                "evidence_paths": [{
                    "population_frame_traceability":
                        "population_frame.cells[midmarket-operations]",
                    "weight_semantics":
                        "composition_plan.profiles[midmarket-proof-seeking]",
                    "authorized_handoff_traceability":
                        "authorized_handoff.cohorts[operations-cohort]",
                }.get(check_id, "ledger.evidence_items[evidence-item-1]")],
                "finding_ids": ["finding-implementation-proof"],
                "profile_ids": ["operations-director-evaluating-v1"],
                "message": "Release B1 audit binds exact population inputs.",
            })
        audit = {
            "schema_version": "panel-construction-audit-v2",
            "applicability": "release_b1",
            "panel_id": self.v2_panel["panel_id"],
            "panel_version": self.v2_panel["version"],
            "auditor_run_id": "construction-audit-run-1",
            "audited_at": "2026-07-24T12:30:00Z",
            "input_bindings": deepcopy(audit_input_bindings),
            "checks": audit_checks,
            "result": "pass",
            "limitations": [
                "Synthetic evidence is not population observation."
            ],
        }
        audit_sha256 = bare_digest(audit)
        panel = base_v2_panel
        panel["schema_version"] = SAVED_PANEL_V3
        panel.update({
            "panel_tier": tier,
            "evidence_basis": evidence_basis,
            "brief_id": brief["brief_id"],
            "population_frame_result_sha256": digest(frame),
            "population_frame_sha256": usable_frame_sha256,
            "composition_plan_sha256": digest(composition),
            "validity_profile_sha256": digest(validity),
            "authorized_handoff_sha256": (
                "sha256:" + "9" * 64 if tier in {"tier_3", "tier_4"} else None
            ),
            "audit_binding": {
                "applicability": "release_b1",
                "auditor_run_id": "construction-audit-run-1",
                "audit_sha256": audit_sha256,
                "report_inputs_sha256": "7" * 64,
                "evidence_ledger_sha256": "3" * 64,
                "finding_support_sha256": "4" * 64,
                "synthesis_matrix_sha256": "5" * 64,
                "report_manifest_sha256": "6" * 64,
            },
            "claim_boundary": brief["claim_boundary"],
            "package_status": "unpackaged",
        })
        workflow = {
            "schema_version": "panel-workflow-state-v1",
            "workflow_id": "operations-leaders-build",
            "panel_id": panel["panel_id"],
            "panel_version": panel["version"],
            "state": "approved",
            "updated_at": "2026-07-24T12:00:00Z",
            "approvals": [{
                "scope": "evidence_synthesis",
                "status": "approved",
                "approved_by": "reviewer",
                "approved_at": "2026-07-24T12:00:00Z",
                "target_sha256": v2_brief_sha256,
                "note": "Approved.",
            }, {
                "scope": "panel_construction",
                "status": "approved",
                "approved_by": "reviewer",
                "approved_at": "2026-07-24T12:00:00Z",
                "target_sha256": v2_panel_sha256,
                "note": "Approved.",
            }],
            "bindings": {
                "brief_sha256": v2_brief_sha256,
                "panel_sha256": v2_panel_sha256,
                "report_inputs_sha256": "7" * 64,
                "audit_sha256": audit_sha256,
                "package_sha256": None,
            },
        }
        return brief, panel, frame, composition, validity, workflow, audit

    def validate_v3_documents(self, documents):
        return validate_audience_research_v3(
            documents[0],
            documents[1],
            frame=documents[2],
            composition=documents[3],
            validity=documents[4],
            workflow_state=documents[5],
            construction_audit=documents[6],
        )

    def reseal_audit_digest(self, documents):
        audit_sha256 = bare_digest(documents[6])
        documents[1]["audit_binding"]["audit_sha256"] = audit_sha256
        documents[5]["bindings"]["audit_sha256"] = audit_sha256

    def rebind_population_documents(self, documents):
        frame_digest = digest(documents[2])
        usable_frame_digest = (
            frame_digest
            if documents[2]["eligibility"] in {
                "eligible_tier_2",
                "eligible_tier_3",
            }
            else None
        )
        documents[0]["population_frame_result_sha256"] = frame_digest
        documents[0]["population_frame_sha256"] = usable_frame_digest
        documents[1]["population_frame_result_sha256"] = frame_digest
        documents[1]["population_frame_sha256"] = usable_frame_digest
        documents[3]["frame_binding"]["frame_result_sha256"] = frame_digest
        documents[3]["frame_binding"]["frame_sha256"] = usable_frame_digest
        composition_digest = digest(documents[3])
        documents[1]["composition_plan_sha256"] = composition_digest
        documents[4]["source_bindings"]["frame_result_sha256"] = frame_digest
        documents[4]["source_bindings"]["frame_sha256"] = usable_frame_digest
        documents[4]["source_bindings"]["composition_sha256"] = composition_digest
        validity_digest = digest(documents[4])
        documents[1]["validity_profile_sha256"] = validity_digest
        documents[6]["input_bindings"]["population_frame_result_sha256"] = (
            bare_digest(documents[2])
        )
        documents[6]["input_bindings"]["population_frame_sha256"] = (
            None
            if usable_frame_digest is None
            else bare_digest(documents[2])
        )
        documents[6]["input_bindings"]["composition_plan_sha256"] = (
            bare_digest(documents[3])
        )
        documents[6]["input_bindings"]["validity_profile_sha256"] = (
            bare_digest(documents[4])
        )
        documents[6]["input_bindings"]["authorized_handoff_sha256"] = (
            None
            if documents[1]["authorized_handoff_sha256"] is None
            else documents[1]["authorized_handoff_sha256"].removeprefix(
                "sha256:"
            )
        )
        handoff_check = next(
            check
            for check in documents[6]["checks"]
            if check["check_id"] == "authorized_handoff_traceability"
        )
        handoff_check["status"] = (
            "not_applicable"
            if documents[1]["authorized_handoff_sha256"] is None
            else "pass"
        )
        self.reseal_audit_digest(documents)

    def rebind_v2_document_bindings(self, documents):
        v2_brief_sha256 = bare_digest(
            _v2_projection(documents[0], brief=True)
        )
        v2_panel_sha256 = bare_digest(
            _v2_projection(documents[1], brief=False)
        )
        documents[4]["source_bindings"]["brief_sha256"] = (
            "sha256:" + v2_brief_sha256
        )
        documents[4]["source_bindings"]["panel_sha256"] = (
            "sha256:" + v2_panel_sha256
        )
        documents[1]["validity_profile_sha256"] = digest(documents[4])
        for approval in documents[0]["scoped_approvals"]:
            if approval["scope"] == "evidence-synthesis":
                approval["target_sha256"] = "sha256:" + v2_brief_sha256
            elif approval["scope"] == "panel-construction":
                approval["target_sha256"] = "sha256:" + v2_panel_sha256
        documents[6]["input_bindings"]["brief_sha256"] = v2_brief_sha256
        documents[6]["input_bindings"]["panel_sha256"] = v2_panel_sha256
        documents[6]["input_bindings"]["validity_profile_sha256"] = (
            bare_digest(documents[4])
        )
        self.reseal_audit_digest(documents)
        documents[5]["bindings"]["brief_sha256"] = v2_brief_sha256
        documents[5]["bindings"]["panel_sha256"] = v2_panel_sha256
        for approval in documents[5]["approvals"]:
            if approval["scope"] == "evidence_synthesis":
                approval["target_sha256"] = v2_brief_sha256
            elif approval["scope"] == "panel_construction":
                approval["target_sha256"] = v2_panel_sha256

    def overlay_downgraded_v3_pair(
        self,
        *,
        requested_tier="tier_3",
        evidence_basis="first_party_aggregate",
    ):
        documents = list(self.v3_pair(evidence_basis=evidence_basis))
        documents[3]["requested_tier"] = requested_tier
        documents[3]["overlay_hypotheses"][0]["allocation_basis"] = (
            "experimental"
        )
        documents[3]["achieved_tier"] = "tier_1"
        documents[3]["tier_reason_codes"] = [
            "experimental-overlay-allocation"
        ]
        documents[3]["lost_claims"] = [
            "Population-grounded overlay composition is not supported."
        ]
        documents[0]["panel_tier"] = "tier_1"
        documents[1]["panel_tier"] = "tier_1"
        documents[4]["panel_tier"] = "tier_1"
        documents[1]["governance"]["allowed_uses"] = [
            (
                "Directional synthetic ad testing under the named public proxy boundary"
                if evidence_basis == "public"
                else "Synthetic ad testing"
            )
        ]
        self.rebind_population_documents(documents)
        self.rebind_v2_document_bindings(documents)
        return documents

    def none_evidence_eligible_release_b1_pair(self):
        documents = self.overlay_downgraded_v3_pair()
        documents[0]["evidence_basis"] = "none"
        documents[0]["structural_findings"] = []
        documents[0]["overlay_findings"] = []
        documents[1]["evidence_basis"] = "none"
        documents[3]["evidence_basis"] = "none"
        for group in documents[3]["structural_groups"]:
            group["structural_finding_ids"] = []
            group["evidence_ids"] = []
        for overlay in documents[3]["overlay_hypotheses"]:
            overlay["allocation_basis"] = "experimental"
            overlay["finding_ids"] = []
            overlay["evidence_ids"] = []
            overlay["topic_bindings"] = []
        for profile in documents[3]["profiles"]:
            profile["support_status"] = "provisional"
            profile["support_finding_ids"] = []
            profile["support_evidence_ids"] = []
        documents[4]["evidence_basis"] = "none"
        self.rebind_population_documents(documents)
        return documents

    def legacy_migration_v3_pair(self):
        documents = list(
            self.v3_pair(tier="tier_1", evidence_basis="none")
        )
        documents[1]["audit_binding"] = {
            "applicability": "legacy_v2_migration",
            "status": "not_available",
            "source_package_sha256": "sha256:" + "a" * 64,
            "reason": (
                "The legacy v2 package has no Release B1 construction audit."
            ),
        }
        documents[5] = None
        documents[6] = None
        return documents

    def test_cross_document_validator_binds_tier_3_sources_and_preserves_v2(self):
        documents = self.v3_pair()
        result = self.validate_v3_documents(documents)
        self.assertEqual(7, len(result))
        self.assertEqual(documents[0], result[0])
        self.assertEqual(documents[1], result[1])

    def test_cross_document_permission_policy_is_exact_and_route_bound(self):
        mutations = (
            (
                "source permission must be confirmed",
                lambda documents: documents[2]["source_bindings"][0]["access"].update(
                    permission_confirmed=False
                ),
            ),
            (
                "structural source must authorize population framing",
                lambda documents: documents[2]["source_bindings"][0]["access"].update(
                    permitted_uses=["audience-composition"]
                ),
            ),
            (
                "tier three must use the exact cohort policy",
                lambda documents: documents[1]["governance"].update(
                    allowed_uses=["Synthetic ad testing"]
                ),
            ),
            (
                "near-match policy must fail closed",
                lambda documents: documents[1]["governance"].update(
                    allowed_uses=[
                        "Synthetic ad testing for the exact authorized aggregate cohort."
                    ]
                ),
            ),
        )
        for label, mutate in mutations:
            documents = list(self.v3_pair())
            mutate(documents)
            if "source" in label:
                self.rebind_population_documents(documents)
            else:
                self.rebind_v2_document_bindings(documents)
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    ValueError, "permission|permitted|allowed_uses|policy"
                ):
                    self.validate_v3_documents(documents)

        public_proxy = list(
            self.overlay_downgraded_v3_pair(
                requested_tier="tier_2",
                evidence_basis="public",
            )
        )
        self.assertEqual(
            [
                "Directional synthetic ad testing under the named public proxy boundary"
            ],
            public_proxy[1]["governance"]["allowed_uses"],
        )
        self.validate_v3_documents(public_proxy)

    def test_overlay_driven_tier_1_preserves_eligible_frame_bindings(self):
        for evidence_basis in (
            "public",
            "licensed_aggregate",
            "first_party_aggregate",
            "hybrid",
        ):
            for requested_tier in ("tier_2", "tier_3"):
                documents = self.overlay_downgraded_v3_pair(
                    requested_tier=requested_tier,
                    evidence_basis=evidence_basis,
                )
                self.assertEqual(
                    documents[3],
                    validate_composition_plan(documents[3], frame=documents[2]),
                )
                self.assertEqual(
                    documents[4],
                    validate_validity_profile(documents[4]),
                )
                result = self.validate_v3_documents(documents)
                expected_frame_sha256 = digest(documents[2])
                with self.subTest(
                    evidence_basis=evidence_basis,
                    requested_tier=requested_tier,
                ):
                    self.assertEqual(
                        expected_frame_sha256,
                        result[0]["population_frame_sha256"],
                    )
                    self.assertEqual(
                        expected_frame_sha256,
                        result[1]["population_frame_sha256"],
                    )
                    self.assertEqual(
                        expected_frame_sha256,
                        result[4]["source_bindings"]["frame_sha256"],
                    )
                    self.assertEqual(
                        bare_digest(documents[2]),
                        result[6]["input_bindings"][
                            "population_frame_sha256"
                        ],
                    )
                    self.assertEqual(
                        requested_tier,
                        result[3]["requested_tier"],
                    )
                    self.assertEqual("tier_1", result[3]["achieved_tier"])

    def test_saved_panel_v3_standalone_is_canonical_and_nonmutating(self):
        panel = deepcopy(self.v3_pair()[1])
        before = canonical_bytes(panel)
        canonical = validate_saved_panel_v3(panel)
        self.assertEqual(panel, canonical)
        self.assertEqual(before, canonical_bytes(panel))
        self.assertIsNot(panel, canonical)
        self.assertIsNot(panel["segments"], canonical["segments"])
        canonical["segments"][0]["name"] = "Changed copy"
        self.assertNotEqual(
            canonical["segments"][0]["name"],
            panel["segments"][0]["name"],
        )

    def test_legacy_migration_audit_binding_is_a_strict_tagged_union(self):
        panel = self.legacy_migration_v3_pair()[1]
        self.assertEqual(panel, validate_saved_panel_v3(panel))

        mutations = (
            (
                "mixed release fields",
                lambda value: value["audit_binding"].update(
                    auditor_run_id="migration-no-audit"
                ),
            ),
            (
                "wrong status",
                lambda value: value["audit_binding"].update(
                    status="available"
                ),
            ),
            (
                "empty reason",
                lambda value: value["audit_binding"].update(reason=""),
            ),
            (
                "bad source digest",
                lambda value: value["audit_binding"].update(
                    source_package_sha256="a" * 64
                ),
            ),
            (
                "tier above one",
                lambda value: value.update(panel_tier="tier_2"),
            ),
            (
                "packaged migration",
                lambda value: value.update(package_status="approved"),
            ),
            (
                "usable frame",
                lambda value: value.update(
                    population_frame_sha256="sha256:" + "b" * 64
                ),
            ),
            (
                "authorized handoff",
                lambda value: value.update(
                    authorized_handoff_sha256="sha256:" + "c" * 64
                ),
            ),
        )
        for label, mutate in mutations:
            invalid = deepcopy(panel)
            mutate(invalid)
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_saved_panel_v3(invalid)

        release_b1 = deepcopy(self.v3_pair()[1])
        before = canonical_bytes(release_b1["audit_binding"])
        self.assertEqual(release_b1, validate_saved_panel_v3(release_b1))
        self.assertEqual(before, canonical_bytes(release_b1["audit_binding"]))

    def test_legacy_migration_full_validation_has_stable_null_tail(self):
        documents = self.legacy_migration_v3_pair()
        result = self.validate_v3_documents(documents)
        self.assertEqual(7, len(result))
        self.assertEqual(tuple(documents[:5]), result[:5])
        self.assertEqual((None, None), result[5:])

        for workflow, audit in (
            (self.v3_pair()[5], None),
            (None, self.v3_pair()[6]),
            (self.v3_pair()[5], self.v3_pair()[6]),
        ):
            invalid = self.legacy_migration_v3_pair()
            invalid[5] = workflow
            invalid[6] = audit
            with self.subTest(workflow=workflow is not None, audit=audit is not None):
                with self.assertRaisesRegex(
                    ValueError, "legacy|migration|None|null"
                ):
                    self.validate_v3_documents(invalid)

        release_b1 = list(self.v3_pair())
        for index in (5, 6):
            invalid = list(release_b1)
            invalid[index] = None
            with self.subTest(release_b1_missing=index):
                with self.assertRaisesRegex(
                    ValueError, "release_b1|workflow|audit|object"
                ):
                    self.validate_v3_documents(invalid)

    def test_none_evidence_uses_only_provisional_composition_support(self):
        documents = self.legacy_migration_v3_pair()
        brief = documents[0]
        composition = documents[3]
        self.assertEqual([], brief["structural_findings"])
        self.assertEqual([], brief["overlay_findings"])
        self.assertEqual(brief, validate_research_brief_v3(brief))
        self.assertEqual(
            composition,
            validate_composition_plan(composition, frame=documents[2]),
        )
        self.assertTrue(all(
            group["origin"] == "tier_1_provisional"
            and not group["structural_finding_ids"]
            and not group["evidence_ids"]
            for group in composition["structural_groups"]
        ))
        self.assertTrue(all(
            overlay["allocation_basis"] == "experimental"
            and not overlay["finding_ids"]
            and not overlay["evidence_ids"]
            and not overlay["topic_bindings"]
            for overlay in composition["overlay_hypotheses"]
        ))
        self.assertTrue(all(
            profile["support_status"] == "provisional"
            and not profile["support_finding_ids"]
            and not profile["support_evidence_ids"]
            for profile in composition["profiles"]
        ))

    def test_none_evidence_standalone_requires_tier_one_and_null_usable_frame(
        self,
    ):
        documents = self.v3_pair(tier="tier_1", evidence_basis="none")
        cases = (
            (
                "brief tier",
                documents[0],
                validate_research_brief_v3,
                lambda value: value.update(panel_tier="tier_2"),
            ),
            (
                "brief usable frame",
                documents[0],
                validate_research_brief_v3,
                lambda value: value.update(
                    population_frame_sha256="sha256:" + "f" * 64
                ),
            ),
            (
                "panel tier",
                documents[1],
                validate_saved_panel_v3,
                lambda value: value.update(panel_tier="tier_2"),
            ),
            (
                "panel usable frame",
                documents[1],
                validate_saved_panel_v3,
                lambda value: value.update(
                    population_frame_sha256="sha256:" + "f" * 64
                ),
            ),
        )
        for label, document, validator, mutate in cases:
            invalid = deepcopy(document)
            mutate(invalid)
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    ValueError,
                    "evidence_basis none|Tier 1|tier_1|null usable frame",
                ):
                    validator(invalid)

    def test_none_evidence_composition_rejects_every_eligible_frame_route(self):
        documents = self.none_evidence_eligible_release_b1_pair()
        with self.assertRaisesRegex(
            ValueError,
            "evidence_basis none|eligible|usable frame|no-frame",
        ):
            validate_composition_plan(documents[3], frame=documents[2])

        provisional = self.legacy_migration_v3_pair()
        for label, mutate in (
            (
                "frame-cells origin",
                lambda value: value["structural_groups"][0].update(
                    origin="frame_cells"
                ),
            ),
            (
                "nonnull usable-frame binding",
                lambda value: value["frame_binding"].update(
                    frame_sha256=digest(provisional[2])
                ),
            ),
        ):
            invalid = deepcopy(provisional[3])
            mutate(invalid)
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_composition_plan(invalid, frame=provisional[2])

    def test_none_evidence_full_validation_rejects_passing_eligible_route(self):
        documents = self.none_evidence_eligible_release_b1_pair()
        with self.assertRaisesRegex(
            ValueError,
            "evidence_basis none|eligible|usable frame|no-frame",
        ):
            self.validate_v3_documents(documents)

    def test_none_and_supported_composition_states_cannot_be_mixed(self):
        documents = self.legacy_migration_v3_pair()
        provisional = documents[3]
        provisional_mutations = (
            lambda value: value["structural_groups"][0].update(
                origin="tier_1_evidence"
            ),
            lambda value: value["structural_groups"][0].update(
                evidence_ids=["invented-evidence"]
            ),
            lambda value: value["overlay_hypotheses"][0].update(
                allocation_basis="estimated"
            ),
            lambda value: value["overlay_hypotheses"][0].update(
                topic_bindings=[{
                    "topic_id": "invented-topic",
                    "evidence_ids": [],
                }]
            ),
            lambda value: value["profiles"][0].update(
                support_status="supported"
            ),
            lambda value: value["profiles"][0].update(
                support_finding_ids=["invented-finding"]
            ),
        )
        for index, mutate in enumerate(provisional_mutations):
            invalid = deepcopy(provisional)
            mutate(invalid)
            with self.subTest(provisional_mutation=index):
                with self.assertRaises(ValueError):
                    validate_composition_plan(invalid, frame=documents[2])

        supported = self.composition(frame=self.frame())
        self.assertTrue(all(
            profile["support_status"] == "supported"
            for profile in supported["profiles"]
        ))
        supported_mutations = (
            lambda value: value["structural_groups"][0].update(
                structural_finding_ids=[]
            ),
            lambda value: value["overlay_hypotheses"][0].update(
                topic_bindings=[]
            ),
            lambda value: value["profiles"][0].update(
                support_status="provisional"
            ),
            lambda value: value["profiles"][0].update(
                support_evidence_ids=[]
            ),
        )
        for index, mutate in enumerate(supported_mutations):
            invalid = deepcopy(supported)
            mutate(invalid)
            with self.subTest(supported_mutation=index):
                with self.assertRaises(ValueError):
                    validate_composition_plan(invalid, frame=self.frame())

        brief = deepcopy(self.v3_pair()[0])
        brief["structural_findings"] = []
        with self.assertRaises(ValueError):
            validate_research_brief_v3(brief)

        provisional_brief = deepcopy(documents[0])
        provisional_brief["overlay_findings"] = ["invented-finding"]
        with self.assertRaises(ValueError):
            validate_research_brief_v3(provisional_brief)

    def test_supported_profiles_bind_exact_union_of_group_and_overlays(self):
        frame = self.frame()
        composition = self.composition(frame=frame)
        group_support = (
            ("group-midmarket-finding", "group-midmarket-evidence"),
            ("group-enterprise-finding", "group-enterprise-evidence"),
        )
        for group, (finding_id, evidence_id) in zip(
            composition["structural_groups"],
            group_support,
        ):
            group["structural_finding_ids"] = [finding_id]
            group["evidence_ids"] = [evidence_id]

        overlay_support = (
            ("overlay-proof-finding", "overlay-proof-evidence"),
            ("overlay-risk-finding", "overlay-risk-evidence"),
        )
        for overlay, (finding_id, evidence_id) in zip(
            composition["overlay_hypotheses"],
            overlay_support,
        ):
            overlay["finding_ids"] = [finding_id]
            overlay["evidence_ids"] = [evidence_id]
            overlay["topic_bindings"][0]["evidence_ids"] = [evidence_id]

        groups = {
            group["structural_group_id"]: group
            for group in composition["structural_groups"]
        }
        overlays = {
            overlay["overlay_id"]: overlay
            for overlay in composition["overlay_hypotheses"]
        }
        for profile in composition["profiles"]:
            group = groups[profile["structural_group_id"]]
            selected_overlays = [
                overlays[overlay_id] for overlay_id in profile["overlay_ids"]
            ]
            profile["support_finding_ids"] = sorted({
                *group["structural_finding_ids"],
                *(
                    finding_id
                    for overlay in selected_overlays
                    for finding_id in overlay["finding_ids"]
                ),
            })
            profile["support_evidence_ids"] = sorted({
                *group["evidence_ids"],
                *(
                    evidence_id
                    for overlay in selected_overlays
                    for evidence_id in overlay["evidence_ids"]
                ),
            })

        self.assertEqual(
            composition,
            validate_composition_plan(composition, frame=frame),
        )
        mutations = (
            lambda value: value["profiles"][0]["support_finding_ids"].append(
                "invented-finding"
            ),
            lambda value: value["profiles"][0]["support_evidence_ids"].append(
                "invented-evidence"
            ),
            lambda value: value["profiles"][0].update(
                support_finding_ids=value["profiles"][0][
                    "support_finding_ids"
                ][1:]
            ),
            lambda value: value["profiles"][0].update(
                support_evidence_ids=value["profiles"][0][
                    "support_evidence_ids"
                ][1:]
            ),
        )
        for index, mutate in enumerate(mutations):
            invalid = deepcopy(composition)
            mutate(invalid)
            with self.subTest(mutation=index):
                with self.assertRaisesRegex(
                    ValueError,
                    "support_finding_ids|support_evidence_ids|exact",
                ):
                    validate_composition_plan(invalid, frame=frame)

    def test_research_brief_v3_standalone_is_canonical_and_nonmutating(self):
        brief = deepcopy(self.v3_pair()[0])
        before = canonical_bytes(brief)
        canonical = validate_research_brief_v3(brief)
        self.assertEqual(brief, canonical)
        self.assertEqual(before, canonical_bytes(brief))
        self.assertIsNot(brief, canonical)
        self.assertIsNot(
            brief["target_audience"],
            canonical["target_audience"],
        )
        canonical["target_audience"]["audience"] = "Changed copy"
        self.assertNotEqual(
            canonical["target_audience"]["audience"],
            brief["target_audience"]["audience"],
        )

    def test_research_brief_v3_preserves_inherited_brief_local_checks(self):
        mutations = (
            (
                "empty audience",
                "invalid_string",
                lambda brief: brief["target_audience"].update(audience=""),
            ),
            (
                "privacy confirmation",
                "missing|privacy_confirmation",
                lambda brief: brief.pop("privacy_confirmation"),
            ),
            (
                "approval",
                "brief_not_approved",
                lambda brief: brief["approval"].update(
                    approved_for_panel_creation=False
                ),
            ),
        )
        for label, code, mutate in mutations:
            brief = deepcopy(self.v3_pair()[0])
            mutate(brief)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, code):
                    validate_research_brief_v3(brief)

    def test_saved_panel_v3_rejects_inherited_panel_local_violations(self):
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
                "scope fingerprint",
                "scope_fingerprint_mismatch",
                lambda panel: panel["audience_scope"].update(
                    scope_fingerprint="sha256:" + "0" * 64
                ),
            ),
            (
                "privacy confirmation",
                "missing_field",
                lambda panel: panel["governance"].pop(
                    "privacy_confirmation"
                ),
            ),
            (
                "profile provenance",
                "profile_provenance_mismatch",
                lambda panel: panel["grounded_context_profiles"][0][
                    "context_attribute_provenance"
                ][0].update(value="A different value"),
            ),
            (
                "segment weight",
                "invalid_study_weight",
                lambda panel: panel["segments"][0].update(study_weight=0),
            ),
            (
                "governance uses",
                "empty_array",
                lambda panel: panel["governance"].update(allowed_uses=[]),
            ),
        )
        for label, code, mutate in mutations:
            panel = deepcopy(self.v3_pair()[1])
            mutate(panel)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, code):
                    validate_saved_panel_v3(panel)

    def test_saved_panel_v3_defers_only_brief_owned_finding_checks(self):
        documents = list(self.v3_pair())
        documents[1]["segments"][0]["finding_ids"] = ["missing-finding"]
        self.assertEqual(
            documents[1],
            validate_saved_panel_v3(documents[1]),
        )
        with self.assertRaisesRegex(
            ValueError, "unresolved_finding"
        ):
            self.validate_v3_documents(documents)

        documents = list(self.v3_pair())
        documents[1]["persona_research"]["evidence_ids"].append(
            "evidence-extra"
        )
        documents[1]["segments"][0]["evidence_ids"] = ["evidence-extra"]
        self.assertEqual(
            documents[1],
            validate_saved_panel_v3(documents[1]),
        )
        with self.assertRaisesRegex(
            ValueError, "finding_evidence_mismatch"
        ):
            self.validate_v3_documents(documents)

    def test_eligible_frame_digest_cannot_be_laundered_to_null(self):
        for document_index, field in (
            (0, "population_frame_sha256"),
            (1, "population_frame_sha256"),
        ):
            documents = self.overlay_downgraded_v3_pair()
            documents[document_index][field] = None
            with self.subTest(document=document_index):
                with self.assertRaisesRegex(
                    ValueError,
                    "canonical population frame|null usable frame",
                ):
                    self.validate_v3_documents(documents)

        documents = self.overlay_downgraded_v3_pair()
        documents[4]["source_bindings"]["frame_sha256"] = None
        documents[1]["validity_profile_sha256"] = digest(documents[4])
        documents[6]["input_bindings"]["validity_profile_sha256"] = (
            bare_digest(documents[4])
        )
        self.reseal_audit_digest(documents)
        with self.assertRaisesRegex(ValueError, "validity.source_bindings"):
            self.validate_v3_documents(documents)

        documents = self.overlay_downgraded_v3_pair()
        documents[6]["input_bindings"]["population_frame_sha256"] = None
        self.reseal_audit_digest(documents)
        with self.assertRaisesRegex(ValueError, "expected binding"):
            self.validate_v3_documents(documents)

    def test_no_frame_digest_cannot_be_laundered_to_nonnull(self):
        for document_index, field in (
            (0, "population_frame_sha256"),
            (1, "population_frame_sha256"),
        ):
            documents = list(
                self.v3_pair(tier="tier_1", evidence_basis="none")
            )
            documents[document_index][field] = digest(documents[2])
            with self.subTest(document=document_index):
                with self.assertRaisesRegex(
                    ValueError,
                    "canonical population frame|null usable frame",
                ):
                    self.validate_v3_documents(documents)

        documents = list(
            self.v3_pair(tier="tier_1", evidence_basis="none")
        )
        documents[4]["source_bindings"]["frame_sha256"] = digest(documents[2])
        self.assertEqual(
            documents[4],
            validate_validity_profile(documents[4]),
        )
        documents[1]["validity_profile_sha256"] = digest(documents[4])
        documents[6]["input_bindings"]["validity_profile_sha256"] = (
            bare_digest(documents[4])
        )
        self.reseal_audit_digest(documents)
        with self.assertRaisesRegex(ValueError, "validity.source_bindings"):
            self.validate_v3_documents(documents)

        documents = list(
            self.v3_pair(tier="tier_1", evidence_basis="none")
        )
        documents[6]["input_bindings"]["population_frame_sha256"] = (
            bare_digest(documents[2])
        )
        self.reseal_audit_digest(documents)
        with self.assertRaisesRegex(ValueError, "expected binding"):
            self.validate_v3_documents(documents)

    def test_workflow_bindings_and_approval_targets_match_independent_v2_hashes(self):
        mutations = (
            (
                "brief binding",
                lambda docs: docs[5]["bindings"].update(
                    brief_sha256="f" * 64
                ),
            ),
            (
                "panel binding",
                lambda docs: docs[5]["bindings"].update(
                    panel_sha256="f" * 64
                ),
            ),
            (
                "report inputs binding",
                lambda docs: docs[5]["bindings"].update(
                    report_inputs_sha256="f" * 64
                ),
            ),
            (
                "audit binding",
                lambda docs: docs[5]["bindings"].update(
                    audit_sha256="f" * 64
                ),
            ),
            (
                "package binding",
                lambda docs: docs[5]["bindings"].update(
                    package_sha256="f" * 64
                ),
            ),
            (
                "evidence approval target",
                lambda docs: docs[5]["approvals"][0].update(
                    target_sha256="f" * 64
                ),
            ),
            (
                "panel approval target",
                lambda docs: docs[5]["approvals"][1].update(
                    target_sha256="f" * 64
                ),
            ),
            (
                "scoped panel approval target",
                lambda docs: docs[0]["scoped_approvals"][0].update(
                    target_sha256="sha256:" + "f" * 64
                ),
            ),
        )
        for label, mutate in mutations:
            documents = self.v3_pair()
            mutate(documents)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "binding|target"):
                    self.validate_v3_documents(documents)

    def test_audit_digest_and_each_expected_input_binding_are_independent(self):
        documents = self.v3_pair()
        documents[6]["limitations"][0] = "A different but valid limitation."
        with self.assertRaisesRegex(ValueError, "audit.*digest|audit_sha256"):
            self.validate_v3_documents(documents)

        for key in (
            "brief_sha256",
            "panel_sha256",
            "evidence_ledger_sha256",
            "finding_support_sha256",
            "synthesis_matrix_sha256",
            "report_manifest_sha256",
        ):
            documents = self.v3_pair()
            documents[6]["input_bindings"][key] = "f" * 64
            self.reseal_audit_digest(documents)
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    self.validate_v3_documents(documents)

        for key in (
            "population_frame_result_sha256",
            "population_frame_sha256",
            "composition_plan_sha256",
            "validity_profile_sha256",
            "authorized_handoff_sha256",
        ):
            documents = self.v3_pair()
            documents[6]["input_bindings"][key] = "f" * 64
            self.reseal_audit_digest(documents)
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "expected binding"):
                    self.validate_v3_documents(documents)

    def test_audit_applicability_and_identities_are_exact(self):
        mutations = (
            (
                "applicability",
                lambda docs: docs[1]["audit_binding"].update(
                    applicability="release_b"
                ),
            ),
            (
                "workflow id",
                lambda docs: docs[5].update(workflow_id="different-workflow"),
            ),
            (
                "workflow panel id",
                lambda docs: docs[5].update(panel_id="different-panel"),
            ),
            (
                "workflow panel version",
                lambda docs: docs[5].update(panel_version="9.9.9"),
            ),
            (
                "audit run id",
                lambda docs: docs[6].update(
                    auditor_run_id="different-audit-run"
                ),
            ),
            (
                "audit panel id",
                lambda docs: docs[6].update(panel_id="different-panel"),
            ),
            (
                "audit panel version",
                lambda docs: docs[6].update(panel_version="9.9.9"),
            ),
        )
        for label, mutate in mutations:
            documents = self.v3_pair()
            mutate(documents)
            if label.startswith("audit "):
                self.reseal_audit_digest(documents)
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.validate_v3_documents(documents)

    def test_cross_document_identities_and_digests_fail_one_at_a_time(self):
        mutations = (
            lambda docs: docs[1].update(brief_id="different-brief"),
            lambda docs: docs[1].update(panel_tier="tier_2"),
            lambda docs: docs[1].update(evidence_basis="public"),
            lambda docs: docs[1].update(claim_boundary="Different claim."),
            lambda docs: docs[3].update(frame_id="different-frame"),
            lambda docs: docs[4].update(panel_id="different-panel"),
            lambda docs: docs[4].update(panel_tier="tier_2"),
            lambda docs: docs[4].update(evidence_basis="public"),
            lambda docs: docs[0].update(
                population_frame_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[1].update(
                population_frame_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[1].update(
                composition_plan_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[1].update(
                validity_profile_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[1].update(
                authorized_handoff_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[4]["source_bindings"].update(
                brief_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[4]["source_bindings"].update(
                panel_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[4]["source_bindings"].update(
                frame_sha256="sha256:" + "f" * 64
            ),
            lambda docs: docs[4]["source_bindings"].update(
                composition_sha256="sha256:" + "f" * 64
            ),
        )
        for index, mutate in enumerate(mutations):
            documents = self.v3_pair()
            mutate(documents)
            with self.subTest(mutation=index):
                with self.assertRaises(ValueError):
                    self.validate_v3_documents(documents)

    def test_tier_3_handoff_denominator_and_calibration_bound_match_frame(self):
        documents = self.v3_pair()
        documents[0]["authorized_audience_import"][
            "exact_cohort_denominator"
        ] = "unrelated-denominator"
        with self.assertRaisesRegex(ValueError, "denominator"):
            self.validate_v3_documents(documents)

        documents = self.v3_pair()
        documents[0]["authorized_audience_import"][
            "max_calibration_factor"
        ] = 0.5
        with self.assertRaisesRegex(ValueError, "calibration_factor"):
            self.validate_v3_documents(documents)

    def test_tier_3_runtime_authority_binds_actual_handoff_batch_and_cohort(
        self,
    ):
        documents = list(self.v3_pair())
        brief, panel, frame = documents[:3]
        source_binding = frame["source_bindings"][0]
        source_binding["access"]["access_type"] = "authorized"
        partition = next(
            unit
            for unit in frame["units"]
            if unit["partition_id"] == source_binding["partition_id"]
        )
        batch = {
            "schema_version": "audience-frame-observation-batch-v1",
            "batch_id": source_binding["batch_id"],
            "frame_request_id": frame["frame_request_id"],
            "adapter_id": "authorized-audience-data-lab",
            "source_family": "authorized-aggregate",
            "source": deepcopy(source_binding["source"]),
            "raw_snapshot_sha256": source_binding["raw_snapshot_sha256"],
            "normalized_batch_sha256": "sha256:" + "0" * 64,
            "access": deepcopy(source_binding["access"]),
            "geography": deepcopy(source_binding["geography"]),
            "unit": partition["unit"],
            "denominator": partition["denominator"],
            "dimensions": deepcopy(frame["structural_dimensions"]),
            "cells": [
                {
                    "cell_id": cell["cell_id"],
                    "dimension_values": deepcopy(cell["dimension_values"]),
                    "estimate": cell["structural_weight"],
                    "uncertainty": {
                        **deepcopy(cell["uncertainty"]),
                        "method": "exact authorized aggregate export",
                    },
                    "suppressed": cell["suppressed"],
                    "status": cell["status"],
                    "relationship": cell["relationship"],
                    "source_location": (
                        f"authorized-export#{cell['cell_id']}"
                    ),
                }
                for cell in frame["cells"]
            ],
            "selection_notes": source_binding["selection_notes"],
            "coverage_notes": source_binding["coverage_notes"],
            "citations": ["Authorized aggregate cohort export"],
        }
        batch_hash_input = deepcopy(batch)
        batch_hash_input.pop("normalized_batch_sha256")
        batch["normalized_batch_sha256"] = digest(batch_hash_input)
        source_binding["normalized_batch_sha256"] = batch[
            "normalized_batch_sha256"
        ]
        handoff = {
            "schema_version": "authorized-audience-handoff-v1",
            "status": "complete",
            "source_profile": {
                "path": "approved-source-profile.json",
                "sha256": "sha256:" + "1" * 64,
            },
            "mapping": {
                "path": "approved-mapping.json",
                "sha256": "sha256:" + "2" * 64,
            },
            "transformation_report": {
                "path": "transformation-report.json",
                "sha256": "sha256:" + "3" * 64,
            },
            "outputs": [
                {
                    "path": "frame-observations-0001.json",
                    "route": "structural_frame",
                    "schema_version": batch["schema_version"],
                    "sha256": digest(batch),
                    # The handoff descriptor preserves the selected source
                    # shape; the canonical batch carries the downstream
                    # frame unit and denominator.
                    "unit": "aggregate_cohort",
                    "denominator": "all_respondents",
                    "row_count": len(batch["cells"]),
                    "field_count": 8,
                }
            ],
            "profile_seeds": [],
            "privacy_permission": {
                "aggregate_only": True,
                "minimum_cell_size": 10,
                "permission_confirmed": True,
            },
            "cohort_identity": {
                "cohort_id": brief["authorized_audience_import"][
                    "cohort_id"
                ],
                "source_profile_sha256": "sha256:" + "1" * 64,
                "source_bundle_sha256": "sha256:" + "4" * 64,
                "structural_outputs": [
                    {
                        "path": "frame-observations-0001.json",
                        "sha256": digest(batch),
                        "schema_version": batch["schema_version"],
                        "batch_id": batch["batch_id"],
                        "unit": batch["unit"],
                        "denominator": batch["denominator"],
                        "row_count": len(batch["cells"]),
                    }
                ],
            },
        }
        authority = {
            "schema_version": "authorized-audience-runtime-authority-v1",
            "cohort_id": brief["authorized_audience_import"]["cohort_id"],
            "handoff": handoff,
            "structural_outputs": [
                {
                    "path": "frame-observations-0001.json",
                    "batch": batch,
                }
            ],
        }
        handoff_sha256 = digest(handoff)
        brief["authorized_audience_import"]["handoff_sha256"] = (
            handoff_sha256
        )
        panel["authorized_handoff_sha256"] = handoff_sha256

        validator = getattr(
            audience_lab.audience_research_v3,
            "validate_v3_runtime_authority",
        )
        self.assertEqual(
            authority,
            validator(brief, panel, frame, authority),
        )

        public_relabel = deepcopy(authority)
        public_batch = public_relabel["structural_outputs"][0]["batch"]
        public_batch["access"]["access_type"] = "public"
        public_hash_input = deepcopy(public_batch)
        public_hash_input.pop("normalized_batch_sha256")
        public_batch["normalized_batch_sha256"] = digest(
            public_hash_input
        )
        public_relabel["handoff"]["outputs"][0]["sha256"] = digest(
            public_batch
        )
        public_frame = deepcopy(frame)
        public_frame["source_bindings"][0]["access"]["access_type"] = (
            "public"
        )
        public_frame["source_bindings"][0][
            "normalized_batch_sha256"
        ] = public_batch["normalized_batch_sha256"]
        public_brief = deepcopy(brief)
        public_panel = deepcopy(panel)
        public_handoff_sha256 = digest(public_relabel["handoff"])
        public_brief["authorized_audience_import"][
            "handoff_sha256"
        ] = public_handoff_sha256
        public_panel["authorized_handoff_sha256"] = (
            public_handoff_sha256
        )
        with self.assertRaisesRegex(ValueError, "authorized"):
            validator(
                public_brief,
                public_panel,
                public_frame,
                public_relabel,
            )

        substituted_brief = deepcopy(brief)
        substituted_brief["authorized_audience_import"]["cohort_id"] = (
            "substituted-cohort"
        )
        with self.assertRaisesRegex(ValueError, "cohort"):
            validator(substituted_brief, panel, frame, authority)

    def test_null_frame_composition_requires_planning_semantics_and_zero_modeled_share(self):
        frame = self.no_frame()
        composition = self.composition(
            frame=frame,
            requested_tier="tier_1",
            evidence_basis="none",
        )
        self.assertEqual(
            composition,
            validate_composition_plan(composition, frame=frame),
        )

        for semantic in (
            "authorized_cohort_weight",
            "experimental_modeled_weight",
        ):
            invalid = deepcopy(composition)
            invalid["structural_groups"][0]["weight_semantic"] = semantic
            invalid["profiles"][0]["effective_weight_semantic"] = semantic
            invalid["profiles"][1]["effective_weight_semantic"] = semantic
            with self.subTest(semantic=semantic):
                with self.assertRaisesRegex(ValueError, "planning_allocation"):
                    validate_composition_plan(invalid, frame=frame)

        invalid = deepcopy(composition)
        invalid["modeled_cell_share"] = 0.01
        with self.assertRaisesRegex(ValueError, "modeled_cell_share"):
            validate_composition_plan(invalid, frame=frame)

    def test_cross_validator_rejects_unknown_versions_for_every_versioned_argument(self):
        for index, label in (
            (0, "brief"),
            (1, "panel"),
            (2, "frame"),
            (3, "composition"),
            (4, "validity"),
            (5, "workflow"),
            (6, "audit"),
        ):
            documents = self.v3_pair()
            documents[index]["schema_version"] = "future-v99"
            with self.subTest(argument=label):
                with self.assertRaisesRegex(ValueError, "schema_version"):
                    self.validate_v3_documents(documents)

    def test_cross_validator_rejects_unknown_keys_and_nonfinite_values_in_every_argument(self):
        for index, label in enumerate(
            (
                "brief",
                "panel",
                "frame",
                "composition",
                "validity",
                "workflow",
                "audit",
            )
        ):
            documents = self.v3_pair()
            documents[index]["unexpected_contract_field"] = True
            with self.subTest(argument=label, mutation="unknown"):
                with self.assertRaisesRegex(ValueError, "unknown"):
                    self.validate_v3_documents(documents)

        nonfinite_mutations = (
            lambda docs: docs[0]["authorized_audience_import"].update(
                max_calibration_factor=float("nan")
            ),
            lambda docs: docs[1]["segments"][0].update(
                study_weight=float("inf")
            ),
            lambda docs: docs[2]["cells"][0].update(
                structural_weight=float("-inf")
            ),
            lambda docs: docs[3]["profiles"][0].update(
                conditional_overlay_allocation=float("nan")
            ),
            lambda docs: docs[4]["axes"]["structural_frame"].update(
                coverage=float("inf")
            ),
            lambda docs: docs[5]["bindings"].update(
                brief_sha256=float("nan")
            ),
            lambda docs: docs[6]["input_bindings"].update(
                evidence_ledger_sha256=float("inf")
            ),
        )
        for index, mutate in enumerate(nonfinite_mutations):
            documents = self.v3_pair()
            mutate(documents)
            with self.subTest(argument=index, mutation="nonfinite"):
                with self.assertRaises(ValueError):
                    self.validate_v3_documents(documents)

    def test_cross_validator_is_total_for_each_malformed_argument(self):
        for argument_index in range(7):
            for malformed in (None, False, 7, "wrong", [], [1], {}):
                documents = list(self.v3_pair())
                documents[argument_index] = malformed
                with self.subTest(
                    argument=argument_index,
                    malformed=malformed,
                ):
                    with self.assertRaises(ValueError):
                        self.validate_v3_documents(documents)

    def test_tier_1_no_frame_result_cannot_be_swapped_for_eligible_frame(self):
        documents = list(self.v3_pair(tier="tier_1", evidence_basis="none"))
        validate_audience_research_v3(
            documents[0],
            documents[1],
            frame=documents[2],
            composition=documents[3],
            validity=documents[4],
            workflow_state=documents[5],
            construction_audit=documents[6],
        )
        with self.assertRaisesRegex(ValueError, "canonical population frame"):
            eligible_frame = self.frame()
            documents[0]["population_frame_result_sha256"] = digest(eligible_frame)
            documents[1]["population_frame_result_sha256"] = digest(eligible_frame)
            validate_audience_research_v3(
                documents[0],
                documents[1],
                frame=eligible_frame,
                composition=documents[3],
                validity=documents[4],
                workflow_state=documents[5],
                construction_audit=documents[6],
            )

    def test_cross_document_tier_2_and_3_eligibility_are_not_interchangeable(self):
        documents = self.v3_pair(tier="tier_3")
        documents[2]["eligibility"] = "eligible_tier_2"
        documents[0]["population_frame_result_sha256"] = digest(documents[2])
        documents[0]["population_frame_sha256"] = digest(documents[2])
        documents[1]["population_frame_result_sha256"] = digest(documents[2])
        documents[1]["population_frame_sha256"] = digest(documents[2])
        documents[4]["source_bindings"]["frame_result_sha256"] = digest(
            documents[2]
        )
        documents[4]["source_bindings"]["frame_sha256"] = digest(documents[2])
        documents[1]["validity_profile_sha256"] = digest(documents[4])
        with self.assertRaisesRegex(ValueError, "Tier 3.*eligible_tier_3"):
            validate_audience_research_v3(
                documents[0],
                documents[1],
                frame=documents[2],
                composition=documents[3],
                validity=documents[4],
                workflow_state=documents[5],
                construction_audit=documents[6],
            )

    def test_tier_3_requires_exact_authorized_handoff_metadata(self):
        for key, value in (
            ("exact_cohort_denominator", ""),
            ("selection_statement", ""),
            ("coverage_statement", ""),
            ("max_calibration_factor", 3.01),
        ):
            documents = self.v3_pair()
            documents[0]["authorized_audience_import"][key] = value
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    validate_audience_research_v3(
                        documents[0],
                        documents[1],
                        frame=documents[2],
                        composition=documents[3],
                        validity=documents[4],
                        workflow_state=documents[5],
                        construction_audit=documents[6],
                    )


if __name__ == "__main__":
    unittest.main()
