from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.construction_audit import (  # noqa: E402
    CONSTRUCTION_AUDIT_SCHEMA_VERSION,
    construction_audit_sha256,
    require_passing_construction_audit,
    validate_release_b1_construction_audit_for_documents,
    validate_construction_audit,
)


HASHES = {
    "brief_sha256": "1" * 64,
    "panel_sha256": "2" * 64,
    "evidence_ledger_sha256": "3" * 64,
    "finding_support_sha256": "4" * 64,
    "synthesis_matrix_sha256": "5" * 64,
    "report_manifest_sha256": "6" * 64,
    "population_frame_sha256": None,
    "composition_plan_sha256": None,
    "validity_profile_sha256": None,
    "authorized_handoff_sha256": None,
}

B1_HASHES = {
    "brief_sha256": "1" * 64,
    "panel_sha256": "2" * 64,
    "evidence_ledger_sha256": "3" * 64,
    "finding_support_sha256": "4" * 64,
    "synthesis_matrix_sha256": "5" * 64,
    "report_manifest_sha256": "6" * 64,
    "population_frame_result_sha256": "7" * 64,
    "population_frame_sha256": "7" * 64,
    "composition_plan_sha256": "8" * 64,
    "validity_profile_sha256": "9" * 64,
    "authorized_handoff_sha256": "a" * 64,
}


class PanelConstructionAuditTests(unittest.TestCase):
    maxDiff = None

    def audit(self) -> dict[str, object]:
        checks = []
        for check_id in (
            "approved_evidence_only", "finding_support_complete",
            "contradictions_preserved", "segment_sufficiency",
            "profile_traceability", "inference_boundaries", "privacy_boundary",
            "count_semantics", "claim_tier", "population_frame_traceability",
            "weight_semantics", "authorized_handoff_traceability",
        ):
            checks.append({
                "check_id": check_id,
                "status": "not_applicable" if check_id in {
                    "population_frame_traceability", "authorized_handoff_traceability",
                } else "pass",
                "evidence_paths": ["ledger.evidence_items[evidence-item-1]"],
                "finding_ids": ["finding-implementation-proof"],
                "profile_ids": ["operations-director-evaluating-v1"],
                "message": "Creative and outcome words are permitted in this free-text message.",
            })
        return {
            "schema_version": CONSTRUCTION_AUDIT_SCHEMA_VERSION,
            "panel_id": "operations-leaders",
            "panel_version": "1.0.0",
            "auditor_run_id": "construction-audit-run-1",
            "audited_at": "2026-07-23T12:30:00Z",
            "input_bindings": copy.deepcopy(HASHES),
            "checks": checks,
            "result": "pass",
            "limitations": ["Population-frame and authorized-handoff checks are unavailable in Release A."],
        }

    def audit_v2(
        self,
        *,
        authorized_handoff=True,
    ) -> dict[str, object]:
        bindings = copy.deepcopy(B1_HASHES)
        if not authorized_handoff:
            bindings["authorized_handoff_sha256"] = None
        checks = []
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
            path = "ledger.evidence_items[evidence-item-1]"
            if check_id == "population_frame_traceability":
                path = "population_frame.cells[midmarket-operations]"
            elif check_id == "weight_semantics":
                path = "composition_plan.profiles[midmarket-proof-seeking]"
            elif check_id == "authorized_handoff_traceability":
                path = "authorized_handoff.cohorts[operations-cohort]"
            checks.append({
                "check_id": check_id,
                "status": (
                    "not_applicable"
                    if (
                        check_id == "authorized_handoff_traceability"
                        and not authorized_handoff
                    )
                    else "pass"
                ),
                "evidence_paths": [path],
                "finding_ids": ["finding-implementation-proof"],
                "profile_ids": ["operations-director-evaluating-v1"],
                "message": "Release B1 checks bind exact population inputs.",
            })
        return {
            "schema_version": "panel-construction-audit-v2",
            "applicability": "release_b1",
            "panel_id": "operations-leaders",
            "panel_version": "1.0.0",
            "auditor_run_id": "construction-audit-run-b1",
            "audited_at": "2026-07-24T15:00:00Z",
            "input_bindings": bindings,
            "checks": checks,
            "result": "pass",
            "limitations": ["Synthetic evidence is not population observation."],
        }

    def test_passing_audit_is_closed_canonical_and_hashable(self) -> None:
        audit = self.audit()
        self.assertEqual(audit, validate_construction_audit(audit, expected_bindings=HASHES))
        self.assertEqual("pass", require_passing_construction_audit(audit, expected_bindings=HASHES)["result"])
        self.assertEqual(
            hashlib.sha256(json.dumps(audit, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n").hexdigest(),
            construction_audit_sha256(audit),
        )

    def test_release_a_fixture_is_a_valid_closed_audit(self) -> None:
        fixture = ROOT / "conformance" / "fixtures" / "audience-panel-builder" / "release-a" / "construction-audit.json"
        fixture_bytes = fixture.read_bytes()
        self.assertEqual(4080, len(fixture_bytes))
        self.assertEqual(
            "7bceed9089a33261f2e1a5222bb83440d74c99ce6e1bcec417c0db5745e9747c",
            hashlib.sha256(fixture_bytes).hexdigest(),
        )
        audit = json.loads(fixture.read_text(encoding="utf-8"))
        expected = {
            "brief_sha256": "1" * 64, "panel_sha256": "2" * 64,
            "evidence_ledger_sha256": "3" * 64, "finding_support_sha256": "4" * 64,
            "synthesis_matrix_sha256": "5" * 64, "report_manifest_sha256": "6" * 64,
            "population_frame_sha256": None, "composition_plan_sha256": None,
            "validity_profile_sha256": None, "authorized_handoff_sha256": None,
        }
        self.assertEqual("pass", validate_construction_audit(audit, expected_bindings=expected)["result"])

    def test_auditor_prompt_covers_b1_bindings_support_weights_and_routes(self) -> None:
        prompt = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "agents"
            / "panel-construction-auditor.md"
        ).read_text(encoding="utf-8")
        for required in (
            'applicability: "release_b1"',
            "population_frame_result_sha256",
            "population_frame_sha256",
            "composition_plan_sha256",
            "validity_profile_sha256",
            "authorized_handoff_sha256",
            "every explicit reusable profile",
            "conditional overlay allocations",
            "semantic route separation",
            "authorized_handoff.outputs[canonical-output-stem]",
            "The Release B1 auditor does not receive study quota or capacity inputs.",
            "every non-outcome auditable canonical output",
            "Outcome-feedback outputs are covered only by the exact authorized-handoff manifest hash",
        ):
            with self.subTest(required=required):
                self.assertIn(required, prompt)
        method = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "references"
            / "construction-method.md"
        ).read_text(encoding="utf-8")
        self.assertIn("every non-outcome auditable canonical output", method)
        self.assertIn(
            "Outcome feedback remains in its separate feedback lane",
            method,
        )

    def test_release_b1_v2_audit_is_strict_canonical_and_hash_bound(self) -> None:
        audit = self.audit_v2()
        self.assertEqual(
            audit,
            validate_construction_audit(
                audit,
                expected_bindings=B1_HASHES,
            ),
        )
        invalid = self.audit_v2()
        invalid["input_bindings"]["population_frame_result_sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "expected binding"):
            validate_construction_audit(
                invalid,
                expected_bindings=B1_HASHES,
            )

    def test_release_b1_check_applicability_follows_exact_bindings(self) -> None:
        for check_id in (
            "population_frame_traceability",
            "weight_semantics",
        ):
            audit = self.audit_v2()
            next(
                check for check in audit["checks"]
                if check["check_id"] == check_id
            )["status"] = "not_applicable"
            with self.subTest(check_id=check_id):
                with self.assertRaisesRegex(ContractError, "release_b1|Release B1"):
                    validate_construction_audit(
                        audit,
                        expected_bindings=B1_HASHES,
                    )
        no_handoff = self.audit_v2(authorized_handoff=False)
        expected = copy.deepcopy(B1_HASHES)
        expected["authorized_handoff_sha256"] = None
        self.assertEqual(
            "pass",
            validate_construction_audit(
                no_handoff,
                expected_bindings=expected,
            )["result"],
        )
        next(
            check for check in no_handoff["checks"]
            if check["check_id"] == "authorized_handoff_traceability"
        )["status"] = "pass"
        with self.assertRaisesRegex(ContractError, "not_applicable"):
            validate_construction_audit(
                no_handoff,
                expected_bindings=expected,
            )

        failing = self.audit_v2()
        next(
            check for check in failing["checks"]
            if check["check_id"] == "authorized_handoff_traceability"
        )["status"] = "fail"
        failing["result"] = "fail"
        self.assertEqual(
            "fail",
            validate_construction_audit(
                failing,
                expected_bindings=B1_HASHES,
            )["result"],
        )

    def test_release_b1_paths_are_population_only_and_creative_blind(self) -> None:
        allowed = (
            "population_frame.cells[midmarket-operations]",
            "composition_plan.profiles[midmarket-proof-seeking]",
            "validity_profile.axes[structural-frame]",
            "authorized_handoff.cohorts[operations-cohort]",
        )
        for path in allowed:
            audit = self.audit_v2()
            audit["checks"][0]["evidence_paths"] = [path]
            with self.subTest(path=path):
                self.assertEqual(
                    "pass",
                    validate_construction_audit(
                        audit,
                        expected_bindings=B1_HASHES,
                    )["result"],
                )
        audit = self.audit_v2()
        audit["checks"][0]["evidence_paths"] = [
            "composition_plan.creative_roster[winning-ad]"
        ]
        with self.assertRaisesRegex(ContractError, "forbidden|allowed"):
            validate_construction_audit(
                audit,
                expected_bindings=B1_HASHES,
            )
        audit = self.audit_v2()
        audit["checks"][0]["evidence_paths"] = [
            "authorized_handoff.outputs[outcome-feedback-0001]"
        ]
        with self.assertRaisesRegex(ContractError, "forbidden"):
            validate_construction_audit(
                audit,
                expected_bindings=B1_HASHES,
            )
        release_a = self.audit()
        release_a["checks"][0]["evidence_paths"] = [
            "finding_support.findings[outcome-anxiety]"
        ]
        self.assertEqual(
            "pass",
            validate_construction_audit(
                release_a,
                expected_bindings=HASHES,
            )["result"],
        )

    def test_release_b1_document_gate_binds_population_documents_and_checks_support(self) -> None:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )
        from conformance.test_population_composition import (
            PopulationCompositionTests,
        )
        from audience_panel_builder.population.validity import (
            finalize_validity_profile,
        )

        source = AudienceResearchV3ContractTests()
        source.setUpClass()
        composition_source = PopulationCompositionTests()
        frame = composition_source.frame(eligibility="eligible_tier_2")
        composition = composition_source.build(
            frame=frame,
            requested_tier="tier_2",
            evidence_basis="public",
        )
        provisional = source.validity(tier="tier_2", evidence_basis="public")
        provisional.update({
            "binding_state": "frame_provisional",
            "panel_id": None,
            "panel_tier": None,
            "evidence_basis": None,
        })
        provisional["source_bindings"] = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": sha256_json(frame),
            "frame_sha256": sha256_json(frame),
            "composition_sha256": None,
        }
        validity = finalize_validity_profile(
            provisional_validity=provisional,
            population_frame=frame,
            composition_plan=composition,
            panel_id="operations-leaders",
            panel_tier="tier_2",
            evidence_basis="public",
            brief_sha256="sha256:" + "1" * 64,
            panel_projection_sha256="sha256:" + "2" * 64,
        )
        research_bindings = {
            "brief_sha256": "1" * 64,
            "panel_sha256": "2" * 64,
            "evidence_ledger_sha256": "3" * 64,
            "finding_support_sha256": "4" * 64,
            "synthesis_matrix_sha256": "5" * 64,
            "report_manifest_sha256": "6" * 64,
        }
        audit = self.audit_v2(authorized_handoff=False)
        audit["input_bindings"].update({
            **research_bindings,
            "population_frame_result_sha256": sha256_json(frame).removeprefix("sha256:"),
            "population_frame_sha256": sha256_json(frame).removeprefix("sha256:"),
            "composition_plan_sha256": sha256_json(composition).removeprefix("sha256:"),
            "validity_profile_sha256": sha256_json(validity).removeprefix("sha256:"),
            "authorized_handoff_sha256": None,
        })
        paths_by_check = {
            "population_frame_traceability": [
                "population_frame.cells[midmarket-operations]",
                "population_frame.cells[enterprise-operations]",
                "composition_plan.structural_groups[midmarket-group]",
                "composition_plan.structural_groups[enterprise-group]",
            ],
            "profile_traceability": [
                f"composition_plan.profiles[{profile['profile_id']}]"
                for profile in composition["profiles"]
            ],
            "inference_boundaries": [
                f"composition_plan.overlay_hypotheses[{overlay['overlay_id']}]"
                for overlay in composition["overlay_hypotheses"]
            ],
            "weight_semantics": [
                *[
                    f"composition_plan.structural_groups[{group['structural_group_id']}]"
                    for group in composition["structural_groups"]
                ],
                *[
                    f"composition_plan.profiles[{profile['profile_id']}]"
                    for profile in composition["profiles"]
                ],
            ],
            "authorized_handoff_traceability": [
                "ledger.evidence_items[evidence-item-1]"
            ],
        }
        for check in audit["checks"]:
            if check["check_id"] in paths_by_check:
                check["evidence_paths"] = paths_by_check[check["check_id"]]

        validated = validate_release_b1_construction_audit_for_documents(
            audit,
            research_bindings=research_bindings,
            population_frame=frame,
            composition_plan=composition,
            validity_profile=validity,
            authorized_handoff=None,
        )
        self.assertEqual("pass", validated["audit"]["result"])
        self.assertEqual(composition, validated["composition_plan"])
        self.assertEqual(validity, validated["validity_profile"])

        for label, mutation, pattern in (
            (
                "frame",
                lambda value: value["coverage_assessment"]["known_gaps"].append(
                    "Tampered."
                ),
                "frame_result_sha256|expected binding",
            ),
            (
                "composition",
                lambda value: value["lost_claims"].append("Tampered."),
                "composition|tier_reason_codes|expected binding",
            ),
            (
                "validity",
                lambda value: value.__setitem__(
                    "binding_state", "frame_provisional"
                ),
                "panel_final|frame_provisional",
            ),
        ):
            invalid_frame = copy.deepcopy(frame)
            invalid_composition = copy.deepcopy(composition)
            invalid_validity = copy.deepcopy(validity)
            target = {
                "frame": invalid_frame,
                "composition": invalid_composition,
                "validity": invalid_validity,
            }[label]
            mutation(target)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ContractError, pattern):
                    validate_release_b1_construction_audit_for_documents(
                        audit,
                        research_bindings=research_bindings,
                        population_frame=invalid_frame,
                        composition_plan=invalid_composition,
                        validity_profile=invalid_validity,
                        authorized_handoff=None,
                    )

        missing_profile_path = copy.deepcopy(audit)
        next(
            check for check in missing_profile_path["checks"]
            if check["check_id"] == "profile_traceability"
        )["evidence_paths"].pop()
        with self.assertRaisesRegex(ContractError, "explicit profile"):
            validate_release_b1_construction_audit_for_documents(
                missing_profile_path,
                research_bindings=research_bindings,
                population_frame=frame,
                composition_plan=composition,
                validity_profile=validity,
                authorized_handoff=None,
            )

        tier_three_frame = composition_source.frame()
        tier_three_composition = composition_source.build(
            frame=tier_three_frame
        )
        tier_three_provisional = copy.deepcopy(provisional)
        tier_three_provisional["source_bindings"].update({
            "frame_result_sha256": sha256_json(tier_three_frame),
            "frame_sha256": sha256_json(tier_three_frame),
        })
        tier_three_validity = finalize_validity_profile(
            provisional_validity=tier_three_provisional,
            population_frame=tier_three_frame,
            composition_plan=tier_three_composition,
            panel_id="operations-leaders",
            panel_tier="tier_3",
            evidence_basis="first_party_aggregate",
            brief_sha256="sha256:" + "1" * 64,
            panel_projection_sha256="sha256:" + "2" * 64,
        )
        tier_three_audit = copy.deepcopy(audit)
        tier_three_audit["input_bindings"].update({
            "population_frame_result_sha256":
                sha256_json(tier_three_frame).removeprefix("sha256:"),
            "population_frame_sha256":
                sha256_json(tier_three_frame).removeprefix("sha256:"),
            "composition_plan_sha256":
                sha256_json(tier_three_composition).removeprefix("sha256:"),
            "validity_profile_sha256":
                sha256_json(tier_three_validity).removeprefix("sha256:"),
        })
        tier_three_audit["input_bindings"]["authorized_handoff_sha256"] = None
        with self.assertRaisesRegex(ContractError, "Tier 3.*authorized handoff"):
            validate_release_b1_construction_audit_for_documents(
                tier_three_audit,
                research_bindings=research_bindings,
                population_frame=tier_three_frame,
                composition_plan=tier_three_composition,
                validity_profile=tier_three_validity,
                authorized_handoff=None,
            )

    def test_release_b1_document_gate_binds_optional_handoff_and_route_separation(self) -> None:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )
        from conformance.test_population_composition import (
            PopulationCompositionTests,
        )
        from audience_panel_builder.population.validity import (
            finalize_validity_profile,
        )

        source = AudienceResearchV3ContractTests()
        source.setUpClass()
        composition_source = PopulationCompositionTests()
        frame = composition_source.frame()
        composition = composition_source.build(frame=frame)
        provisional = source.validity(tier="tier_3")
        provisional.update({
            "binding_state": "frame_provisional",
            "panel_id": None,
            "panel_tier": None,
            "evidence_basis": None,
        })
        provisional["source_bindings"] = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": sha256_json(frame),
            "frame_sha256": sha256_json(frame),
            "composition_sha256": None,
        }
        validity = finalize_validity_profile(
            provisional_validity=provisional,
            population_frame=frame,
            composition_plan=composition,
            panel_id="operations-leaders",
            panel_tier="tier_3",
            evidence_basis="first_party_aggregate",
            brief_sha256="sha256:" + "1" * 64,
            panel_projection_sha256="sha256:" + "2" * 64,
        )
        handoff = {
            "schema_version": "authorized-audience-handoff-v1",
            "status": "complete",
            "source_profile": {
                "path": "approved-source-profile.json",
                "sha256": "sha256:" + "c" * 64,
            },
            "mapping": {
                "path": "approved-mapping.json",
                "sha256": "sha256:" + "d" * 64,
            },
            "transformation_report": {
                "path": "transformation-report.json",
                "sha256": "sha256:" + "e" * 64,
            },
            "outputs": [{
                "path": "frame-observations-0001.json",
                "route": "structural_frame",
                "schema_version": "audience-frame-observation-batch-v1",
                "sha256": "sha256:" + "a" * 64,
                "row_count": 2,
                "field_count": 5,
                "unit": "eligible-cohort-member",
                "denominator": "all-eligible-cohort-members",
            }, {
                "path": "structured-evidence-0001.json",
                "route": "overlay_evidence",
                "schema_version": "audience-structured-evidence-batch-v1",
                "sha256": "sha256:" + "b" * 64,
                "row_count": 2,
                "field_count": 5,
                "unit": "eligible-cohort-member",
                "denominator": "all-eligible-cohort-members",
            }],
            "profile_seeds": [],
            "privacy_permission": {
                "permission_confirmed": True,
                "aggregate_only": True,
                "minimum_cell_size": 10,
            },
            "cohort_identity": {
                "cohort_id": "eligible-operations-cohort",
                "source_profile_sha256": "sha256:" + "c" * 64,
                "source_bundle_sha256": "sha256:" + "f" * 64,
                "structural_outputs": [
                    {
                        "path": "frame-observations-0001.json",
                        "sha256": "sha256:" + "a" * 64,
                        "schema_version":
                            "audience-frame-observation-batch-v1",
                        "batch_id": "eligible-operations-batch",
                        "unit": "eligible-cohort-member",
                        "denominator": "all-eligible-cohort-members",
                        "row_count": 2,
                    }
                ],
            },
        }
        research_bindings = {
            "brief_sha256": "1" * 64,
            "panel_sha256": "2" * 64,
            "evidence_ledger_sha256": "3" * 64,
            "finding_support_sha256": "4" * 64,
            "synthesis_matrix_sha256": "5" * 64,
            "report_manifest_sha256": "6" * 64,
        }
        audit = self.audit_v2()
        audit["input_bindings"].update({
            **research_bindings,
            "population_frame_result_sha256": sha256_json(frame).removeprefix("sha256:"),
            "population_frame_sha256": sha256_json(frame).removeprefix("sha256:"),
            "composition_plan_sha256": sha256_json(composition).removeprefix("sha256:"),
            "validity_profile_sha256": sha256_json(validity).removeprefix("sha256:"),
            "authorized_handoff_sha256": sha256_json(handoff).removeprefix("sha256:"),
        })
        required_paths = {
            "population_frame_traceability": [
                "population_frame.cells[midmarket-operations]",
                "population_frame.cells[enterprise-operations]",
                "composition_plan.structural_groups[midmarket-group]",
                "composition_plan.structural_groups[enterprise-group]",
            ],
            "profile_traceability": [
                f"composition_plan.profiles[{profile['profile_id']}]"
                for profile in composition["profiles"]
            ],
            "inference_boundaries": [
                f"composition_plan.overlay_hypotheses[{overlay['overlay_id']}]"
                for overlay in composition["overlay_hypotheses"]
            ],
            "weight_semantics": [
                *[
                    f"composition_plan.structural_groups[{group['structural_group_id']}]"
                    for group in composition["structural_groups"]
                ],
                *[
                    f"composition_plan.profiles[{profile['profile_id']}]"
                    for profile in composition["profiles"]
                ],
            ],
            "authorized_handoff_traceability": [
                "authorized_handoff.outputs[frame-observations-0001]",
                "authorized_handoff.outputs[structured-evidence-0001]",
            ],
        }
        for check in audit["checks"]:
            if check["check_id"] in required_paths:
                check["evidence_paths"] = required_paths[check["check_id"]]
        self.assertEqual(
            "pass",
            validate_release_b1_construction_audit_for_documents(
                audit,
                research_bindings=research_bindings,
                population_frame=frame,
                composition_plan=composition,
                validity_profile=validity,
                authorized_handoff=handoff,
            )["audit"]["result"],
        )

        unknown = copy.deepcopy(handoff)
        unknown["invented"] = True
        invalid_audit = copy.deepcopy(audit)
        invalid_audit["input_bindings"]["authorized_handoff_sha256"] = (
            sha256_json(unknown).removeprefix("sha256:")
        )
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_release_b1_construction_audit_for_documents(
                invalid_audit,
                research_bindings=research_bindings,
                population_frame=frame,
                composition_plan=composition,
                validity_profile=validity,
                authorized_handoff=unknown,
            )

        route_laundering = copy.deepcopy(handoff)
        route_laundering["outputs"][1]["route"] = "structural_frame"
        with self.assertRaisesRegex(ContractError, "route separation|overlay"):
            validate_release_b1_construction_audit_for_documents(
                audit,
                research_bindings=research_bindings,
                population_frame=frame,
                composition_plan=composition,
                validity_profile=validity,
                authorized_handoff=route_laundering,
            )

    def test_every_check_failure_requires_fail_result_and_cannot_pass(self) -> None:
        for index in range(len(self.audit()["checks"])):
            audit = self.audit()
            if audit["checks"][index]["status"] == "not_applicable":
                continue
            audit["checks"][index]["status"] = "fail"
            with self.assertRaisesRegex(ContractError, "result"):
                validate_construction_audit(audit, expected_bindings=HASHES)
            audit["result"] = "fail"
            self.assertEqual("fail", validate_construction_audit(audit, expected_bindings=HASHES)["result"])
            with self.assertRaisesRegex(ContractError, "passing"):
                require_passing_construction_audit(audit, expected_bindings=HASHES)

    def test_release_a_applicability_map_allows_not_applicable_only_for_unavailable_bindings(self) -> None:
        for index, check in enumerate(self.audit()["checks"]):
            audit = self.audit()
            if check["check_id"] in {"population_frame_traceability", "authorized_handoff_traceability"}:
                audit["checks"][index]["status"] = "pass"
            else:
                audit["checks"][index]["status"] = "not_applicable"
            with self.assertRaisesRegex(ContractError, "Release A"):
                validate_construction_audit(audit, expected_bindings=HASHES)
        all_na = self.audit()
        for check in all_na["checks"]:
            check["status"] = "not_applicable"
        with self.assertRaisesRegex(ContractError, "Release A"):
            validate_construction_audit(all_na, expected_bindings=HASHES)

    def test_rejects_unknown_checks_missing_limitations_bad_bindings_and_nondeterministic_timestamp(self) -> None:
        for mutation, pattern in (
            (lambda value: value["checks"].__setitem__(0, {**value["checks"][0], "check_id": "unknown-check"}), "check_id"),
            (lambda value: value.__setitem__("limitations", []), "limitations"),
            (lambda value: value["input_bindings"].__setitem__("population_frame_sha256", "7" * 64), "Release A"),
            (lambda value: value.__setitem__("audited_at", "2026-07-23 12:30:00"), "RFC 3339"),
        ):
            audit = self.audit()
            mutation(audit)
            with self.assertRaisesRegex(ContractError, pattern):
                validate_construction_audit(audit, expected_bindings=HASHES)
        audit = self.audit()
        audit["input_bindings"]["brief_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "expected binding"):
            validate_construction_audit(audit, expected_bindings=HASHES)

    def test_structural_paths_reject_creative_and_outcome_leakage_without_scanning_free_text(self) -> None:
        for path in (
            "creative_roster.items[creative-1]",
            "ledger.evidence_items[ctr]",
            "report_manifest.outputs[performance-calibration.json]",
            "report_manifest.inputs[test-results.json]",
            "report_manifest.inputs[evaluation-output.json]",
            "report_manifest.inputs[campaign_outcomes.json]",
            "report_manifest.inputs[creative-ids.json]",
            "report_manifest.inputs[conversion_rate.json]",
            "report_manifest.inputs[winner-labels.json]",
        ):
            audit = self.audit()
            audit["checks"][0]["evidence_paths"] = [path]
            with self.assertRaisesRegex(ContractError, "forbidden|allowed canonical"):
                validate_construction_audit(audit, expected_bindings=HASHES)
        self.assertEqual(
            "pass",
            validate_construction_audit(self.audit(), expected_bindings=HASHES)["result"],
        )

    def test_report_manifest_paths_are_allowed_only_under_their_declared_root(self) -> None:
        input_paths = (
            "brief.json", "evidence-ledger.json", "finding-support.json", "plan.json",
            "report-inputs.json", "saved-audience-panel.json", "scored-sources.json",
            "source-inventory.json", "synthesis-matrix.json", "verbatim-inventory.json",
            "workflow-state.json",
        )
        output_paths = (
            "audience-research-report.html", "source-inventory.json", "verbatim-inventory.json",
        )
        for root, paths in (("inputs", input_paths), ("outputs", output_paths)):
            for filename in paths:
                audit = self.audit()
                audit["checks"][0]["evidence_paths"] = [f"report_manifest.{root}[{filename}]"]
                self.assertEqual("pass", validate_construction_audit(audit, expected_bindings=HASHES)["result"])
        for filename in set(input_paths) - set(output_paths):
            audit = self.audit()
            audit["checks"][0]["evidence_paths"] = [f"report_manifest.outputs[{filename}]"]
            with self.assertRaisesRegex(ContractError, "allowed canonical"):
                validate_construction_audit(audit, expected_bindings=HASHES)
        for filename in set(output_paths) - set(input_paths):
            audit = self.audit()
            audit["checks"][0]["evidence_paths"] = [f"report_manifest.inputs[{filename}]"]
            with self.assertRaisesRegex(ContractError, "allowed canonical"):
                validate_construction_audit(audit, expected_bindings=HASHES)

    def test_document_aware_validation_resolves_refs_and_cli_output_is_canonical(self) -> None:
        from conformance.test_panel_research_report import PanelResearchReportTests
        from audience_panel_builder.reporting import render_research_report
        from audience_panel_builder.review import (
            build_panel_review_manifest,
            render_panel_review_html,
            render_panel_summary,
        )

        source = PanelResearchReportTests()
        documents = source.documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = render_panel_summary(documents["brief"], documents["panel"]).encode()
            html = render_panel_review_html(documents["brief"], documents["panel"]).encode()
            review_manifest = build_panel_review_manifest(
                panel=documents["panel"], summary_bytes=summary, html_bytes=html,
                review_revision="review-v1", generated_at=documents["panel"]["updated_at"],
            )
            manifest = render_research_report(
                report_inputs=source.report_inputs(documents), documents=documents,
                generated_at="2026-07-23T12:30:00Z", output_dir=root / "report",
                panel_review_manifest=review_manifest,
                panel_review_summary=summary,
                panel_review_html=html,
            )
            audit = self.audit()
            audit["panel_id"] = documents["panel"]["panel_id"]
            audit["panel_version"] = documents["panel"]["version"]
            audit["input_bindings"] = {
                "brief_sha256": sha256_json(documents["brief"]).removeprefix("sha256:"),
                "panel_sha256": sha256_json(documents["panel"]).removeprefix("sha256:"),
                "evidence_ledger_sha256": sha256_json(documents["evidence_ledger"]).removeprefix("sha256:"),
                "finding_support_sha256": sha256_json(documents["finding_support"]).removeprefix("sha256:"),
                "synthesis_matrix_sha256": sha256_json(documents["synthesis_matrix"]).removeprefix("sha256:"),
                "report_manifest_sha256": sha256_json(manifest).removeprefix("sha256:"),
                "population_frame_sha256": None, "composition_plan_sha256": None,
                "validity_profile_sha256": None, "authorized_handoff_sha256": None,
            }
            audit["checks"][0].update({
                "evidence_paths": ["ledger.evidence_items[evidence-item-1]"],
                "finding_ids": ["finding-implementation-proof"],
                "profile_ids": ["operations-director-evaluating-v1"],
            })
            paths = {"audit": audit, "brief": documents["brief"], "panel": documents["panel"],
                     "ledger": documents["evidence_ledger"], "finding_support": documents["finding_support"],
                     "synthesis": documents["synthesis_matrix"], "report_manifest": manifest,
                     "panel_review_manifest": review_manifest}
            for name, payload in paths.items():
                (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
            command = [sys.executable, str(SCRIPTS / "validate-panel-construction-audit.py"),
                       "--audit", str(root / "audit.json"), "--brief", str(root / "brief.json"),
                       "--panel", str(root / "panel.json"), "--ledger", str(root / "ledger.json"),
                       "--finding-support", str(root / "finding_support.json"), "--synthesis", str(root / "synthesis.json"),
                       "--report-manifest", str(root / "report_manifest.json"),
                       "--panel-review-manifest", str(root / "panel_review_manifest.json")]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, result.returncode, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual({"valid", "result", "audit_sha256"}, set(payload))
            self.assertTrue(payload["valid"])
            for key, unresolved in (
                ("evidence_paths", ["ledger.evidence_items[missing-evidence]"]),
                ("finding_ids", ["missing-finding"]),
                ("profile_ids", ["missing-profile"]),
            ):
                invalid_audit = copy.deepcopy(audit)
                invalid_audit["checks"][0][key] = unresolved
                (root / "audit.json").write_text(json.dumps(invalid_audit), encoding="utf-8")
                invalid = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(2, invalid.returncode)
            self.assertFalse(json.loads(invalid.stdout)["valid"])

    def test_cli_binds_audit_and_manifest_to_each_supplied_document(self) -> None:
        from conformance.test_panel_research_report import PanelResearchReportTests
        from audience_panel_builder.reporting import render_research_report
        from audience_panel_builder.review import (
            build_panel_review_manifest,
            render_panel_review_html,
            render_panel_summary,
        )

        source = PanelResearchReportTests()
        documents = source.documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = render_panel_summary(documents["brief"], documents["panel"]).encode()
            html = render_panel_review_html(documents["brief"], documents["panel"]).encode()
            review_manifest = build_panel_review_manifest(
                panel=documents["panel"], summary_bytes=summary, html_bytes=html,
                review_revision="review-v1", generated_at=documents["panel"]["updated_at"],
            )
            manifest = render_research_report(
                report_inputs=source.report_inputs(documents), documents=documents,
                generated_at="2026-07-23T12:30:00Z", output_dir=root / "report",
                panel_review_manifest=review_manifest,
                panel_review_summary=summary,
                panel_review_html=html,
            )
            audit = self.audit()
            audit["panel_id"] = documents["panel"]["panel_id"]
            audit["panel_version"] = documents["panel"]["version"]
            audit["input_bindings"] = {
                "brief_sha256": sha256_json(documents["brief"]).removeprefix("sha256:"),
                "panel_sha256": sha256_json(documents["panel"]).removeprefix("sha256:"),
                "evidence_ledger_sha256": sha256_json(documents["evidence_ledger"]).removeprefix("sha256:"),
                "finding_support_sha256": sha256_json(documents["finding_support"]).removeprefix("sha256:"),
                "synthesis_matrix_sha256": sha256_json(documents["synthesis_matrix"]).removeprefix("sha256:"),
                "report_manifest_sha256": sha256_json(manifest).removeprefix("sha256:"),
                "population_frame_sha256": None, "composition_plan_sha256": None,
                "validity_profile_sha256": None, "authorized_handoff_sha256": None,
            }
            paths = {"audit": audit, "brief": documents["brief"], "panel": documents["panel"],
                     "ledger": documents["evidence_ledger"], "finding_support": documents["finding_support"],
                     "synthesis": documents["synthesis_matrix"], "report_manifest": manifest,
                     "panel_review_manifest": review_manifest}
            for name, payload in paths.items():
                (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
            command = [sys.executable, str(SCRIPTS / "validate-panel-construction-audit.py"),
                       "--audit", str(root / "audit.json"), "--brief", str(root / "brief.json"),
                       "--panel", str(root / "panel.json"), "--ledger", str(root / "ledger.json"),
                       "--finding-support", str(root / "finding_support.json"), "--synthesis", str(root / "synthesis.json"),
                       "--report-manifest", str(root / "report_manifest.json"),
                       "--panel-review-manifest", str(root / "panel_review_manifest.json")]
            for mutation, pattern in (
                (lambda value: value.__setitem__("panel_id", "different-panel"), "audit.panel_id"),
                (lambda value: next(item for item in value["inputs"] if item["path"] == "saved-audience-panel.json").__setitem__("sha256", "0" * 64), "saved-audience-panel.json"),
                (lambda value: value["inputs"].pop(), "exact sorted Task 3 manifest paths"),
                (lambda value: value["inputs"].append(copy.deepcopy(value["inputs"][0])), "exact sorted Task 3 manifest paths"),
                (lambda value: next(item for item in value["inputs"] if item["path"] == "brief.json").__setitem__("bytes", 0), "brief.json"),
                (lambda value: value.__setitem__("panel_id", "different-panel"), "report_manifest.panel_id"),
            ):
                invalid_audit = copy.deepcopy(audit)
                invalid_manifest = copy.deepcopy(manifest)
                target = invalid_audit if pattern == "audit.panel_id" else invalid_manifest
                mutation(target)
                invalid_audit["input_bindings"]["report_manifest_sha256"] = sha256_json(invalid_manifest).removeprefix("sha256:")
                (root / "audit.json").write_text(json.dumps(invalid_audit), encoding="utf-8")
                (root / "report_manifest.json").write_text(json.dumps(invalid_manifest), encoding="utf-8")
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(pattern, json.loads(result.stdout)["error"])

            (root / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
            (root / "report_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            swapped_review = copy.deepcopy(review_manifest)
            swapped_review["review_revision"] = "review-v2"
            (root / "panel_review_manifest.json").write_text(
                json.dumps(swapped_review), encoding="utf-8"
            )
            swapped = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            self.assertEqual(2, swapped.returncode, swapped.stdout)
            self.assertIn(
                "panel-review-manifest.json",
                json.loads(swapped.stdout)["error"],
            )

            broken_binding = copy.deepcopy(review_manifest)
            broken_binding["canonical_panel"]["sha256"] = "0" * 64
            (root / "panel_review_manifest.json").write_text(
                json.dumps(broken_binding), encoding="utf-8"
            )
            broken = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            self.assertEqual(2, broken.returncode, broken.stdout)
            self.assertIn(
                "exact canonical panel bytes",
                json.loads(broken.stdout)["error"],
            )


if __name__ == "__main__":
    unittest.main()
