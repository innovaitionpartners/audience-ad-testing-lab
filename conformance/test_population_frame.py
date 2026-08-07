"""Conformance tests for source-neutral population-frame construction."""

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
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
AD_TESTING_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "population" / "public-proxy"
CLI = PANEL_SCRIPTS / "build-population-frame.py"
sys.path.insert(0, str(PANEL_SCRIPTS))
sys.path.insert(0, str(AD_TESTING_SCRIPTS))

from audience_lab.audience_research_v3 import (
    validate_population_frame,
    validate_validity_profile,
)
from audience_panel_builder.common import canonical_json_bytes
from audience_panel_builder.population.adapters.bls_oews import BlsOewsAdapter
from audience_panel_builder.population.adapters.census_cbp import CensusCbpAdapter
from audience_panel_builder.population.adapters.census_susb import CensusSusbAdapter
from audience_panel_builder.population.frame import build_population_frame
from audience_panel_builder.population.validity import assess_population_validity


BUILT_AT = "2026-07-24T13:00:00Z"


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _seal_batch(batch: dict[str, object]) -> dict[str, object]:
    hash_input = deepcopy(batch)
    hash_input.pop("normalized_batch_sha256", None)
    batch["normalized_batch_sha256"] = (
        "sha256:" + hashlib.sha256(canonical_json_bytes(hash_input)).hexdigest()
    )
    return batch


def _source_batch(
    *,
    status: str = "observed",
    suppressed: bool = False,
    permission_confirmed: bool = True,
    vintage: str = "2026-07-01",
) -> dict[str, object]:
    unavailable = status == "missing" or suppressed
    return _seal_batch({
        "schema_version": "audience-frame-observation-batch-v1",
        "batch_id": "authorized-role-batch",
        "frame_request_id": "authorized-role-frame",
        "adapter_id": "authorized-audience-handoff-v1",
        "source_family": "authorized-aggregate",
        "source": {
            "publisher": "Authorized aggregate source",
            "program": "Approved cohort export",
            "edition": "2026-07",
            "vintage": vintage,
            "retrieved_at": "2026-07-24T12:00:00Z",
        },
        "raw_snapshot_sha256": _digest("1"),
        "normalized_batch_sha256": "",
        "access": {
            "access_type": "authorized",
            "permission_confirmed": permission_confirmed,
            "permitted_uses": ["audience_panel_research"],
        },
        "geography": ["US"],
        "unit": "cohort-members",
        "denominator": "all-eligible-cohort-members",
        "dimensions": ["role"],
        "cells": [{
            "cell_id": "operations-role",
            "dimension_values": {"role": "operations"},
            "estimate": None if unavailable else 70.0,
            "uncertainty": {
                "lower": None if unavailable else 70.0,
                "upper": None if unavailable else 70.0,
                "method": (
                    "not-available-suppressed"
                    if unavailable
                    else "published-count-no-interval"
                ),
            },
            "suppressed": suppressed,
            "status": status,
            "relationship": "marginal",
            "source_location": "authorized://cohort/roles#operations",
        }],
        "selection_notes": "Eligible cohort fixed before creative review.",
        "coverage_notes": "All eligible cohort members were included.",
        "citations": ["authorized://cohort/roles"],
    })


def _authorized_request(
    *,
    modeled_weight: float = 0.3,
    calibration_factor: float = 1.0,
) -> dict[str, object]:
    return {
        "schema_version": "audience-frame-request-v1",
        "request_id": "authorized-role-frame",
        "target_audience": "Authorized cohort members",
        "decision": "Construct an authorized audience-calibrated panel.",
        "desired_claim": "Represent the authorized cohort only.",
        "geography": ["US"],
        "time_basis": {"as_of": "2026-07-24", "lookback_days": 365},
        "target_unit": "cohort-members",
        "proxy_universes": [{
            "universe_id": "authorized-cohort",
            "description": "All eligible authorized cohort members.",
            "unit": "cohort-members",
            "denominator": "all-eligible-cohort-members",
            "exact": True,
        }],
        "required_dimensions": ["role"],
        "required_joints": [],
        "modeled_cell_rules": [{
            "rule_id": "finance-declared-weight",
            "unit": "cohort-members",
            "denominator": "all-eligible-cohort-members",
            "dimension_values": {"role": "finance"},
            "method": "declared_weight",
            "structural_weight": modeled_weight,
            "uncertainty": {
                "lower": modeled_weight,
                "upper": modeled_weight,
            },
            "rationale": "Predeclared residual cohort share.",
        }],
        "calibration_rules": [{
            "rule_id": "finance-calibration",
            "unit": "cohort-members",
            "denominator": "all-eligible-cohort-members",
            "dimension_values": {"role": "finance"},
            "calibration_factor": calibration_factor,
            "rationale": "Predeclared authorized-cohort calibration.",
        }],
        "exclusions": [],
        "authorized_evidence_bases": ["first_party_aggregate"],
        "available_capabilities": ["authorized-handoff"],
        "downgrade_policy": {
            "allow_tier_1": True,
            "allow_experimental": True,
            "reason": "Downgrade rather than invent unsupported structure.",
        },
    }


def _public_target_with_authorized_context_request() -> dict[str, object]:
    request = _authorized_request()
    request.update({
        "target_audience": "People represented by a public role proxy.",
        "desired_claim": "Represent the public proxy only.",
        "target_unit": "persons",
        "proxy_universes": [{
            "universe_id": "public-person-proxy",
            "description": "A nonexact public people proxy.",
            "unit": "persons",
            "denominator": "employed-persons",
            "exact": False,
        }, {
            "universe_id": "unrelated-authorized-cohort",
            "description": "An exact but unrelated authorized cohort.",
            "unit": "cohort-members",
            "denominator": "all-eligible-cohort-members",
            "exact": True,
        }],
        "modeled_cell_rules": [],
        "calibration_rules": [],
        "authorized_evidence_bases": ["hybrid"],
        "available_capabilities": ["authorized-handoff", "public-adapter"],
    })
    return request


def _public_target_batch() -> dict[str, object]:
    batch = _source_batch()
    batch.update({
        "batch_id": "public-person-role-batch",
        "adapter_id": "public-role-adapter",
        "source_family": "public-government",
        "unit": "persons",
        "denominator": "employed-persons",
    })
    batch["cells"][0]["cell_id"] = "public-operations-role"
    batch["access"] = {
        "access_type": "public",
        "permission_confirmed": True,
        "permitted_uses": ["population-framing"],
    }
    return _seal_batch(batch)


def _feedback(*, holdout: bool) -> dict[str, object]:
    return {
        "schema_version": "panel-outcome-feedback-v1",
        "feedback_id": "heldout-outcome-1" if holdout else "fit-outcome-1",
        "panel_id": "future-panel",
        "study_id": "creative-study-1",
        "variant_id": "variant-a",
        "cohort_id": "authorized-cohort",
        "metric": {
            "name": "qualified-conversion-rate",
            "definition": "Qualified conversions divided by exposures.",
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
        "design": "experimental",
        "source": {
            "source_id": "approved-performance-export",
            "permission_confirmed": True,
        },
        "holdout": holdout,
        "missingness": "No missing aggregate outcomes.",
        "limitations": ["One aggregate cohort outcome."],
        "source_sha256": _digest("9" if holdout else "8"),
    }


def _overlay_batch() -> dict[str, object]:
    return {
        "schema_version": "audience-structured-evidence-batch-v1",
        "batch_id": "approved-overlay",
        "created_at": "2026-07-24T12:00:00Z",
        "source_adapter": "approved-aggregate-evidence",
        "source_schema_version": "1.0.0",
        "input_sha256": _digest("7"),
        "permission": "allowed",
        "source_status": "approved",
        "items": [{
            "evidence_item_id": "overlay-item-1",
            "source_url": "https://example.test/approved-overlay",
            "item_type": "aggregate_research",
            "content_summary": "Cohort members emphasize reliable workflows.",
            "text_fidelity": "summary",
            "content_sha256": _digest("6"),
            "source_pointer": "/items/0",
            "upstream_source_ids": ["approved-study-1"],
            "use_constraints": ["overlay_only"],
            "quality_flags": [],
        }],
    }


class PopulationFrameTests(unittest.TestCase):
    maxDiff = None

    def public_request(self) -> dict[str, object]:
        return json.loads(
            (FIXTURES / "frame-request.json").read_text(encoding="utf-8")
        )

    def public_batches(self) -> list[dict[str, object]]:
        def fixture(name: str) -> dict[str, object]:
            return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

        mapping = {
            "batch_id": "public-proxy-frame-batch",
            "frame_request_id": "public-proxy-frame",
        }
        batches = [
            BlsOewsAdapter().normalize(
                fixture("bls-oews-may-2025.json"),
                {**mapping, "batch_id": "bls-public-proxy-frame-batch"},
            ),
            CensusSusbAdapter().normalize(
                fixture("census-susb-2022.json"),
                {**mapping, "batch_id": "susb-public-proxy-frame-batch"},
            ),
            CensusCbpAdapter().normalize(
                fixture("census-cbp-2023.json"),
                {**mapping, "batch_id": "cbp-public-proxy-frame-batch"},
            ),
        ]
        return batches

    def test_golden_public_proxy_keeps_units_separate_and_missing_joint_explicit(self):
        request = self.public_request()
        batches = self.public_batches()
        original_request = deepcopy(request)
        original_batches = deepcopy(batches)

        frame = build_population_frame(
            frame_request=request,
            observation_batches=list(reversed(batches)),
            built_at=BUILT_AT,
        )

        self.assertEqual(frame, validate_population_frame(frame))
        self.assertEqual(original_request, request)
        self.assertEqual(original_batches, batches)
        self.assertEqual(
            [
                ("establishments", "employer-establishments"),
                ("firms", "employer-firms"),
                ("persons", "employed-persons-excluding-self-employed"),
            ],
            sorted((unit["unit"], unit["denominator"]) for unit in frame["units"]),
        )
        self.assertEqual(3, len({cell["partition_id"] for cell in frame["cells"]}))
        missing_joint = next(
            joint
            for joint in frame["joints"]
            if joint["dimensions"] == ["employment-status", "geography"]
        )
        self.assertEqual([], missing_joint["cell_ids"])
        self.assertIn("not available", missing_joint["missing_reason"].lower())
        suppressed = next(
            cell for cell in frame["cells"] if cell["suppressed"]
        )
        self.assertEqual("missing", suppressed["status"])
        self.assertIsNone(suppressed["structural_weight"])
        self.assertEqual(
            {"lower": None, "upper": None}, suppressed["uncertainty"]
        )
        self.assertEqual(
            json.loads(
                (FIXTURES / "expected-population-frame.json").read_text(
                    encoding="utf-8"
                )
            ),
            frame,
        )

    def test_declared_modeled_share_uses_effective_weight_and_exact_boundary(self):
        frame = build_population_frame(
            frame_request=_authorized_request(modeled_weight=0.3),
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        modeled = next(cell for cell in frame["cells"] if cell["origin"] == "modeled_rule")
        observed = next(cell for cell in frame["cells"] if cell["origin"] == "source_observation")
        self.assertEqual(0.3, modeled["structural_weight"])
        self.assertAlmostEqual(0.7, observed["structural_weight"])
        self.assertEqual("supported", frame["modeled_weight_by_dimension"][0]["status"])
        self.assertEqual("eligible_tier_3", frame["eligibility"])

        experimental = build_population_frame(
            frame_request=_authorized_request(modeled_weight=0.3000001),
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        self.assertEqual("experimental", experimental["eligibility"])
        self.assertEqual(
            "experimental",
            experimental["modeled_weight_by_dimension"][0]["status"],
        )
        self.assertIn("modeled-share-above-threshold", experimental["downgrade_reason"])

    def test_frame_rejects_duplicate_coordinate_across_accepted_batches(self):
        first = _source_batch()
        second = deepcopy(first)
        second["batch_id"] = "authorized-role-batch-second-snapshot"
        second["cells"][0]["cell_id"] = "operations-role-second-snapshot"
        second["cells"][0]["estimate"] = 30.0
        second["cells"][0]["uncertainty"] = {
            "lower": 30.0,
            "upper": 30.0,
            "method": "published-count-no-interval",
        }
        _seal_batch(second)

        with self.assertRaisesRegex(ValueError, "duplicate structural coordinate"):
            build_population_frame(
                frame_request=_authorized_request(),
                observation_batches=[first, second],
                built_at=BUILT_AT,
            )

    def test_frame_rejects_source_coordinate_redeclared_by_modeled_rule(self):
        request = _authorized_request()
        request["modeled_cell_rules"][0]["dimension_values"] = {
            "role": "operations"
        }

        with self.assertRaisesRegex(ValueError, "duplicate structural coordinate"):
            build_population_frame(
                frame_request=request,
                observation_batches=[_source_batch()],
                built_at=BUILT_AT,
            )

    def test_frame_coordinate_identity_includes_partition(self):
        request = _authorized_request()
        request["authorized_evidence_bases"] = ["hybrid"]
        request["proxy_universes"].append({
            "universe_id": "public-firm-role-proxy",
            "description": "Public firm role proxy.",
            "unit": "firms",
            "denominator": "employer-firms",
            "exact": False,
        })
        public = _source_batch()
        public["batch_id"] = "public-firm-role-batch"
        public["adapter_id"] = "public-firm-role-adapter"
        public["source_family"] = "public-government"
        public["unit"] = "firms"
        public["denominator"] = "employer-firms"
        public["cells"][0]["cell_id"] = "public-operations-role"
        public["access"] = {
            "access_type": "public",
            "permission_confirmed": True,
            "permitted_uses": ["population-framing"],
        }
        _seal_batch(public)

        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_source_batch(), public],
            built_at=BUILT_AT,
        )

        operations = [
            cell
            for cell in frame["cells"]
            if cell["dimension_values"] == {"role": "operations"}
        ]
        self.assertEqual(2, len(operations))
        self.assertEqual(2, len({cell["partition_id"] for cell in operations}))

    def test_frame_rejects_tampered_batch_with_stale_normalized_digest(self):
        batch = _source_batch()
        stale_digest = batch["normalized_batch_sha256"]
        batch["cells"][0]["estimate"] = 999.0
        batch["cells"][0]["uncertainty"] = {
            "lower": 999.0,
            "upper": 999.0,
            "method": "published-count-no-interval",
        }

        with self.assertRaisesRegex(ValueError, "normalized_batch_sha256.*match"):
            build_population_frame(
                frame_request=_authorized_request(),
                observation_batches=[batch],
                built_at=BUILT_AT,
            )
        self.assertEqual(stale_digest, batch["normalized_batch_sha256"])

    def test_frame_binds_the_verified_normalized_batch_digest(self):
        batch = _source_batch()
        frame = build_population_frame(
            frame_request=_authorized_request(),
            observation_batches=[batch],
            built_at=BUILT_AT,
        )

        self.assertEqual(
            batch["normalized_batch_sha256"],
            frame["source_bindings"][0]["normalized_batch_sha256"],
        )

    def test_calibration_factor_exact_three_passes_and_above_three_is_rejected(self):
        frame = build_population_frame(
            frame_request=_authorized_request(calibration_factor=3.0),
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        modeled = next(cell for cell in frame["cells"] if cell["origin"] == "modeled_rule")
        self.assertEqual(3.0, modeled["calibration_factor"])

        with self.assertRaisesRegex(ValueError, "at most 3.0"):
            build_population_frame(
                frame_request=_authorized_request(calibration_factor=3.0000001),
                observation_batches=[_source_batch()],
                built_at=BUILT_AT,
            )

    def test_tier_three_requires_exact_authorized_denominator_not_exact_public_proxy(self):
        request = _authorized_request()
        request["authorized_evidence_bases"] = ["hybrid"]
        request["proxy_universes"].append({
            "universe_id": "public-firm-proxy",
            "description": "A nonexact public context proxy.",
            "unit": "firms",
            "denominator": "employer-firms",
            "exact": False,
        })
        public_batch = _source_batch()
        public_batch.update({
            "batch_id": "public-firm-batch",
            "adapter_id": "public-firm-adapter",
            "source_family": "public-government",
            "unit": "firms",
            "denominator": "employer-firms",
        })
        public_batch["access"] = {
            "access_type": "public",
            "permission_confirmed": True,
            "permitted_uses": ["population-framing"],
        }
        _seal_batch(public_batch)
        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_source_batch(), public_batch],
            built_at=BUILT_AT,
        )
        self.assertEqual("eligible_tier_3", frame["eligibility"])
        self.assertFalse(
            next(unit for unit in frame["units"] if unit["unit"] == "firms")[
                "exact"
            ]
        )

    def test_vintage_geography_permission_and_frame_request_mismatches_downgrade(self):
        cases = []
        old = _source_batch(vintage="2020-01-01")
        cases.append((old, "vintage-outside-request-window"))
        unpermissioned = _source_batch(permission_confirmed=False)
        cases.append((unpermissioned, "permission-not-confirmed"))
        wrong_geography = _source_batch()
        wrong_geography["geography"] = ["GB"]
        _seal_batch(wrong_geography)
        cases.append((wrong_geography, "geography-mismatch"))
        wrong_request = _source_batch()
        wrong_request["frame_request_id"] = "different-frame-request"
        _seal_batch(wrong_request)
        cases.append((wrong_request, "frame-request-mismatch"))

        for batch, reason in cases:
            with self.subTest(reason=reason):
                frame = build_population_frame(
                    frame_request=_authorized_request(),
                    observation_batches=[batch],
                    built_at=BUILT_AT,
                )
                self.assertEqual("no_defensible_frame", frame["eligibility"])
                self.assertEqual([], frame["cells"])
                self.assertIn(reason, frame["downgrade_reason"])
                self.assertEqual(frame, validate_population_frame(frame))

    def test_evidence_basis_mismatch_cannot_enter_the_structural_frame(self):
        request = _authorized_request()
        request["authorized_evidence_bases"] = ["public"]
        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertIn("evidence-basis-mismatch", frame["downgrade_reason"])

    def test_modeled_rules_cannot_create_an_unobserved_unit_partition(self):
        request = _authorized_request()
        request["proxy_universes"].append({
            "universe_id": "unobserved-firms",
            "description": "A declared proxy without a source batch.",
            "unit": "firms",
            "denominator": "employer-firms",
            "exact": False,
        })
        request["modeled_cell_rules"].append({
            "rule_id": "unobserved-firm-model",
            "unit": "firms",
            "denominator": "employer-firms",
            "dimension_values": {"role": "owner"},
            "method": "declared_weight",
            "structural_weight": 1.0,
            "uncertainty": {"lower": 1.0, "upper": 1.0},
            "rationale": "Must not create a source-free partition.",
        })
        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        self.assertEqual(
            [("cohort-members", "all-eligible-cohort-members")],
            [(unit["unit"], unit["denominator"]) for unit in frame["units"]],
        )
        self.assertFalse(
            any(
                cell["modeled_rule_id"] == "unobserved-firm-model"
                for cell in frame["cells"]
            )
        )

    def test_no_frame_result_is_canonical_and_preserves_tier_one_null_semantics(self):
        frame = build_population_frame(
            frame_request=_authorized_request(),
            observation_batches=[],
            built_at=BUILT_AT,
        )
        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertEqual([], frame["units"])
        self.assertEqual([], frame["cells"])
        self.assertEqual([], frame["margins"])
        self.assertEqual([], frame["joints"])
        self.assertEqual([], frame["source_bindings"])
        self.assertEqual(0.0, frame["modeled_weight_share"])
        self.assertIn("Tier 1", frame["claim_boundary"])
        self.assertEqual(frame, validate_population_frame(frame))

        validity = assess_population_validity(
            frame_request=_authorized_request(),
            population_frame=frame,
            overlay_evidence=[],
            outcome_feedback=[],
        )
        self.assertIsNone(validity["source_bindings"]["frame_sha256"])
        self.assertIsNotNone(validity["source_bindings"]["frame_result_sha256"])

    def test_all_suppressed_source_cells_emit_no_frame_instead_of_inventing_zero(self):
        frame = build_population_frame(
            frame_request=_authorized_request(modeled_weight=0.0),
            observation_batches=[
                _source_batch(status="missing", suppressed=True)
            ],
            built_at=BUILT_AT,
        )
        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertEqual([], frame["cells"])
        self.assertIn("no-available-weighted-cells", frame["downgrade_reason"])

    def test_unavailable_joint_stays_null_beside_a_weighted_margin(self):
        request = _authorized_request()
        request["required_dimensions"].append("geography")
        missing_joint = _source_batch(status="missing", suppressed=True)
        missing_joint["batch_id"] = "authorized-role-geography-batch"
        missing_joint["dimensions"] = ["geography", "role"]
        missing_joint["cells"][0]["cell_id"] = "suppressed-role-geography"
        missing_joint["cells"][0]["dimension_values"] = {
            "geography": "US",
            "role": "operations",
        }
        missing_joint["cells"][0]["relationship"] = "joint"
        _seal_batch(missing_joint)

        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_source_batch(), missing_joint],
            built_at=BUILT_AT,
        )

        retained = next(
            cell for cell in frame["cells"]
            if cell["cell_id"] == "suppressed-role-geography"
        )
        self.assertIsNone(retained["structural_weight"])
        self.assertIsNone(retained["uncertainty"]["lower"])
        joint = next(
            row for row in frame["joints"]
            if row["cell_ids"] == ["suppressed-role-geography"]
        )
        self.assertIn("missing or suppressed", joint["missing_reason"])

    def test_required_joint_that_is_wholly_unavailable_is_missing_critical(self):
        for status, suppressed in (("missing", False), ("observed", True)):
            with self.subTest(status=status, suppressed=suppressed):
                request = _authorized_request()
                request["required_dimensions"].append("geography")
                request["required_joints"] = [["geography", "role"]]
                unavailable_joint = _source_batch(
                    status=status,
                    suppressed=suppressed,
                )
                unavailable_joint["batch_id"] = "authorized-role-geography-batch"
                unavailable_joint["dimensions"] = ["geography", "role"]
                unavailable_joint["cells"][0].update({
                    "cell_id": "unavailable-role-geography",
                    "dimension_values": {
                        "geography": "US",
                        "role": "operations",
                    },
                    "relationship": "joint",
                })
                _seal_batch(unavailable_joint)

                frame = build_population_frame(
                    frame_request=request,
                    observation_batches=[_source_batch(), unavailable_joint],
                    built_at=BUILT_AT,
                )

                reason = (
                    "missing-critical-joint:"
                    "cohort-members-all-eligible-cohort-members:"
                    "geography-role"
                )
                self.assertEqual("experimental", frame["eligibility"])
                self.assertIn(reason, frame["downgrade_reason"])
                joint = next(
                    row
                    for row in frame["joints"]
                    if row["dimensions"] == ["geography", "role"]
                )
                self.assertEqual(
                    "The required critical joint is not available from "
                    "the selected source observations.",
                    joint["missing_reason"],
                )

    def test_unusable_unrelated_authorized_partition_cannot_elevate_public_target(self):
        authorized = _source_batch(status="missing")
        frame = build_population_frame(
            frame_request=_public_target_with_authorized_context_request(),
            observation_batches=[_public_target_batch(), authorized],
            built_at=BUILT_AT,
        )

        self.assertEqual("eligible_tier_2", frame["eligibility"])
        self.assertTrue(
            any(
                binding["access"]["access_type"] == "authorized"
                for binding in frame["source_bindings"]
            )
        )

    def test_zero_weight_authorized_target_row_cannot_elevate_public_support(self):
        request = _authorized_request()
        request["modeled_cell_rules"] = []
        request["calibration_rules"] = []
        request["authorized_evidence_bases"] = ["hybrid"]

        public = _source_batch()
        public["batch_id"] = "public-target-role-batch"
        public["cells"][0]["cell_id"] = "public-positive-role"
        public["access"] = {
            "access_type": "public",
            "permission_confirmed": True,
            "permitted_uses": ["population-framing"],
        }
        _seal_batch(public)
        authorized_zero = _source_batch()
        authorized_zero["batch_id"] = "authorized-zero-target-role-batch"
        authorized_zero["cells"][0].update({
            "cell_id": "authorized-zero-target-role",
            "dimension_values": {"role": "finance"},
            "estimate": 0.0,
            "uncertainty": {
                "lower": 0.0,
                "upper": 0.0,
                "method": "published-zero",
            },
        })
        _seal_batch(authorized_zero)

        frame = build_population_frame(
            frame_request=request,
            observation_batches=[public, authorized_zero],
            built_at=BUILT_AT,
        )

        zero_row = next(
            cell
            for cell in frame["cells"]
            if cell["cell_id"] == "authorized-zero-target-role"
        )
        self.assertEqual("observed", zero_row["status"])
        self.assertEqual(0.0, zero_row["structural_weight"])
        self.assertEqual("eligible_tier_2", frame["eligibility"])

    def test_zero_total_collection_is_explicitly_skipped_without_relabeling(self):
        zero_total = _source_batch()
        zero_total["cells"][0].update({
            "cell_id": "authorized-zero-role",
            "estimate": 0.0,
            "uncertainty": {
                "lower": 0.0,
                "upper": 0.0,
                "method": "published-zero",
            },
        })
        _seal_batch(zero_total)

        frame = build_population_frame(
            frame_request=_public_target_with_authorized_context_request(),
            observation_batches=[_public_target_batch(), zero_total],
            built_at=BUILT_AT,
        )

        self.assertEqual(frame, validate_population_frame(frame))
        self.assertEqual("eligible_tier_2", frame["eligibility"])
        self.assertFalse(
            any(cell["cell_id"] == "authorized-zero-role" for cell in frame["cells"])
        )
        self.assertIn(
            "nonpositive-collection-total:"
            "cohort-members-all-eligible-cohort-members:marginal:role",
            frame["coverage_assessment"]["known_gaps"],
        )

    def test_zero_total_source_cannot_anchor_a_partial_modeled_collection(self):
        zero_total = _source_batch()
        zero_total["cells"][0].update({
            "cell_id": "authorized-zero-role",
            "estimate": 0.0,
            "uncertainty": {
                "lower": 0.0,
                "upper": 0.0,
                "method": "published-zero",
            },
        })
        _seal_batch(zero_total)

        frame = build_population_frame(
            frame_request=_authorized_request(modeled_weight=0.3),
            observation_batches=[zero_total],
            built_at=BUILT_AT,
        )

        self.assertEqual(frame, validate_population_frame(frame))
        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertEqual([], frame["cells"])
        self.assertIn(
            "nonpositive-collection-total:"
            "cohort-members-all-eligible-cohort-members:marginal:role",
            frame["downgrade_reason"],
        )

    def test_mixed_geography_batch_is_rejected_instead_of_partially_accepted(self):
        mixed = _source_batch()
        mixed["geography"] = ["US", "GB"]
        _seal_batch(mixed)

        frame = build_population_frame(
            frame_request=_authorized_request(),
            observation_batches=[mixed],
            built_at=BUILT_AT,
        )

        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertEqual([], frame["cells"])
        self.assertIn("geography-mismatch", frame["downgrade_reason"])

    def test_validity_axes_remain_separate_and_only_heldout_feedback_calibrates(self):
        frame = build_population_frame(
            frame_request=_authorized_request(),
            observation_batches=[_source_batch()],
            built_at=BUILT_AT,
        )
        fit_only = assess_population_validity(
            frame_request=_authorized_request(),
            population_frame=frame,
            overlay_evidence=[],
            outcome_feedback=[_feedback(holdout=False)],
        )
        self.assertEqual("frame_provisional", fit_only["binding_state"])
        self.assertIsNone(fit_only["panel_id"])
        self.assertEqual(
            "not_available",
            fit_only["axes"]["allocation_fidelity"]["status"],
        )
        self.assertEqual(
            "not_available",
            fit_only["axes"]["outcome_calibration"]["status"],
        )

        heldout = assess_population_validity(
            frame_request=_authorized_request(),
            population_frame=frame,
            overlay_evidence=[_overlay_batch()],
            outcome_feedback=[_feedback(holdout=True)],
        )
        self.assertEqual(
            "directional",
            heldout["axes"]["outcome_calibration"]["status"],
        )
        self.assertEqual(
            "not_available",
            heldout["axes"]["external_validation"]["status"],
        )
        self.assertEqual([_digest("9")], heldout["held_out_outcome_evidence"])
        self.assertEqual(heldout, validate_validity_profile(heldout))
        forbidden = {
            "confidence", "confidence_score", "overall_score",
            "composite", "percentage",
        }

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value).union(*(keys(child) for child in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(child) for child in value))
            return set()

        self.assertFalse(forbidden.intersection(keys(heldout)))
        expected = json.loads(
            (FIXTURES / "expected-validity-profile.json").read_text(
                encoding="utf-8"
            )
        )
        verified_frame_digest = (
            "sha256:" + hashlib.sha256(canonical_json_bytes(frame)).hexdigest()
        )
        expected["source_bindings"]["frame_result_sha256"] = verified_frame_digest
        expected["source_bindings"]["frame_sha256"] = verified_frame_digest
        self.assertEqual(
            expected,
            heldout,
        )

    def test_cli_writes_canonical_outputs_without_clobbering(self):
        request = _authorized_request()
        batch = _source_batch()
        expected_frame = build_population_frame(
            frame_request=request,
            observation_batches=[batch],
            built_at=BUILT_AT,
        )
        expected_validity = assess_population_validity(
            frame_request=request,
            population_frame=expected_frame,
            overlay_evidence=[],
            outcome_feedback=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            batch_path = root / "batch.json"
            output = root / "frame.json"
            validity_output = root / "validity.json"
            request_path.write_bytes(canonical_json_bytes(request))
            batch_path.write_bytes(canonical_json_bytes(batch))
            command = [
                sys.executable,
                str(CLI),
                "--frame-request", str(request_path),
                "--observation-batch", str(batch_path),
                "--built-at", BUILT_AT,
                "--output", str(output),
                "--validity-output", str(validity_output),
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(expected_frame, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(
                expected_validity,
                json.loads(validity_output.read_text(encoding="utf-8")),
            )
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(3, second.returncode)
            self.assertEqual("output_collision", json.loads(second.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
