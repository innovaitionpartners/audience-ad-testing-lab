from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.composition import (  # noqa: E402
    build_composition_plan,
)
from audience_panel_builder.population.validity import (  # noqa: E402
    finalize_validity_profile,
)


FIXTURES = ROOT / "conformance" / "fixtures" / "population" / "public-proxy"


class PopulationCompositionTests(unittest.TestCase):
    maxDiff = None

    def frame(self, *, eligibility: str = "eligible_tier_3") -> dict[str, object]:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )

        return AudienceResearchV3ContractTests().frame(eligibility=eligibility)

    def no_frame(self) -> dict[str, object]:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )

        return AudienceResearchV3ContractTests().no_frame()

    def structural_findings(
        self,
        *,
        tier_one: bool = False,
    ) -> list[dict[str, object]]:
        return [{
            "structural_group_id": "midmarket-group",
            "cell_ids": [] if tier_one else ["midmarket-operations"],
            "structural_finding_ids": ["finding-midmarket-structure"],
            "evidence_ids": ["evidence-midmarket-structure"],
            "must_cover": True,
            "planning_allocation": 0.7 if tier_one else None,
        }, {
            "structural_group_id": "enterprise-group",
            "cell_ids": [] if tier_one else ["enterprise-operations"],
            "structural_finding_ids": ["finding-enterprise-structure"],
            "evidence_ids": ["evidence-enterprise-structure"],
            "must_cover": True,
            "planning_allocation": 0.3 if tier_one else None,
        }]

    def overlay_findings(
        self,
        *,
        second_basis: str = "estimated",
        unrelated: bool = False,
    ) -> list[dict[str, object]]:
        return [{
            "overlay_id": "proof-seeking",
            "description": "Needs implementation proof.",
            "allocation_basis": "observed",
            "finding_ids": ["finding-proof"],
            "evidence_ids": ["evidence-proof"],
            "topic_bindings": [{
                "topic_id": "implementation-proof",
                "evidence_ids": ["evidence-proof"],
            }],
            "decision_relevance": "topic_bound",
        }, {
            "overlay_id": "risk-averse",
            "description": "Needs risk controls.",
            "allocation_basis": second_basis,
            "finding_ids": ["finding-risk"],
            "evidence_ids": ["evidence-risk"],
            "topic_bindings": [{
                "topic_id": "implementation-risk",
                "evidence_ids": ["evidence-risk"],
            }],
            "decision_relevance": (
                "unrelated_affinity" if unrelated else "topic_bound"
            ),
        }]

    def profile_specs(self) -> list[dict[str, object]]:
        return [{
            "status": "supported",
            "profile_id": "midmarket-proof-seeking",
            "structural_group_id": "midmarket-group",
            "overlay_ids": ["proof-seeking"],
            "support_finding_ids": [
                "finding-midmarket-structure",
                "finding-proof",
            ],
            "support_evidence_ids": [
                "evidence-midmarket-structure",
                "evidence-proof",
            ],
            "conditional_overlay_allocation": 0.75,
        }, {
            "status": "supported",
            "profile_id": "midmarket-proof-risk",
            "structural_group_id": "midmarket-group",
            "overlay_ids": ["proof-seeking", "risk-averse"],
            "support_finding_ids": [
                "finding-midmarket-structure",
                "finding-proof",
                "finding-risk",
            ],
            "support_evidence_ids": [
                "evidence-midmarket-structure",
                "evidence-proof",
                "evidence-risk",
            ],
            "conditional_overlay_allocation": 0.25,
        }, {
            "status": "supported",
            "profile_id": "enterprise-risk-averse",
            "structural_group_id": "enterprise-group",
            "overlay_ids": ["risk-averse"],
            "support_finding_ids": [
                "finding-enterprise-structure",
                "finding-risk",
            ],
            "support_evidence_ids": [
                "evidence-enterprise-structure",
                "evidence-risk",
            ],
            "conditional_overlay_allocation": 1.0,
        }, {
            "status": "unsupported",
            "structural_group_id": "enterprise-group",
            "overlay_ids": ["proof-seeking"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "No joint support for this structural-overlay pairing.",
        }]

    def provisional_structural_findings(self) -> list[dict[str, object]]:
        findings = self.structural_findings(tier_one=True)
        for finding in findings:
            finding["structural_finding_ids"] = []
            finding["evidence_ids"] = []
        return findings

    def provisional_overlay_findings(self) -> list[dict[str, object]]:
        findings = self.overlay_findings(second_basis="experimental")
        for finding in findings:
            finding["allocation_basis"] = "experimental"
            finding["finding_ids"] = []
            finding["evidence_ids"] = []
            finding["topic_bindings"] = []
        return findings

    def provisional_profile_specs(self) -> list[dict[str, object]]:
        specs = self.profile_specs()
        for spec in specs:
            if spec["status"] == "supported":
                spec["status"] = "provisional"
                spec["support_finding_ids"] = []
                spec["support_evidence_ids"] = []
        return specs

    def build(
        self,
        *,
        frame: dict[str, object] | None = None,
        structural_findings: list[dict[str, object]] | None = None,
        overlay_findings: list[dict[str, object]] | None = None,
        profile_specs: list[dict[str, object]] | None = None,
        requested_tier: str = "tier_3",
        evidence_basis: str = "first_party_aggregate",
    ) -> dict[str, object]:
        if frame is None:
            frame = self.frame()
        return build_composition_plan(
            population_frame=frame,
            structural_findings=(
                self.structural_findings()
                if structural_findings is None
                else structural_findings
            ),
            overlay_findings=(
                self.overlay_findings()
                if overlay_findings is None
                else overlay_findings
            ),
            supported_profile_specs=(
                self.profile_specs() if profile_specs is None else profile_specs
            ),
            requested_tier=requested_tier,
            evidence_basis=evidence_basis,
            plan_id="operations-panel-composition",
            plan_version="1.0.0",
            built_at="2026-07-24T14:00:00Z",
        )

    def test_selects_exactly_one_partition_and_calculates_all_weights(self) -> None:
        frame = self.frame()
        before = canonical_json_bytes(frame)
        plan = self.build(frame=frame)

        self.assertEqual(before, canonical_json_bytes(frame))
        self.assertEqual("tier_3", plan["achieved_tier"])
        self.assertEqual({
            "partition_id": "eligible-cohort-members",
            "relationship": "joint",
            "dimensions": ["company-size", "role"],
        }, plan["frame_binding"]["selection"])
        self.assertEqual(
            ["enterprise-group", "midmarket-group"],
            [group["structural_group_id"] for group in plan["structural_groups"]],
        )
        by_profile = {
            profile["profile_id"]: profile for profile in plan["profiles"]
        }
        self.assertAlmostEqual(
            0.525,
            by_profile["midmarket-proof-seeking"][
                "effective_profile_allocation"
            ],
        )
        self.assertAlmostEqual(
            0.175,
            by_profile["midmarket-proof-risk"][
                "effective_profile_allocation"
            ],
        )
        self.assertEqual(
            "planning_allocation",
            by_profile["midmarket-proof-risk"]["overlay_weight_semantic"],
        )
        self.assertEqual(
            "authorized_cohort_weight",
            by_profile["midmarket-proof-risk"]["effective_weight_semantic"],
        )
        self.assertTrue(all(
            profile["support_status"] == "supported"
            for profile in plan["profiles"]
        ))
        self.assertTrue(math.isclose(
            1.0,
            math.fsum(
                profile["effective_profile_allocation"]
                for profile in plan["profiles"]
            ),
            abs_tol=1e-9,
        ))
        self.assertEqual(0.3, plan["modeled_cell_share"])
        self.assertTrue(all(group["must_cover"] for group in plan["structural_groups"]))

    def test_keeps_only_explicit_multi_overlay_profiles_and_unsupported_specs(self) -> None:
        plan = self.build()
        signatures = {
            (
                profile["structural_group_id"],
                tuple(profile["overlay_ids"]),
            )
            for profile in plan["profiles"]
        }
        self.assertEqual({
            ("enterprise-group", ("risk-averse",)),
            ("midmarket-group", ("proof-seeking",)),
            ("midmarket-group", ("proof-seeking", "risk-averse")),
        }, signatures)
        self.assertEqual([{
            "structural_group_id": "enterprise-group",
            "overlay_ids": ["proof-seeking"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "No joint support for this structural-overlay pairing.",
        }], plan["unsupported_combinations"])

    def test_rejects_complete_cartesian_product_and_never_expands_missing_pairs(self) -> None:
        complete = []
        for group_id, structural_finding, structural_evidence in (
            ("midmarket-group", "finding-midmarket-structure", "evidence-midmarket-structure"),
            ("enterprise-group", "finding-enterprise-structure", "evidence-enterprise-structure"),
        ):
            for overlay_id, finding_id, evidence_id in (
                ("proof-seeking", "finding-proof", "evidence-proof"),
                ("risk-averse", "finding-risk", "evidence-risk"),
            ):
                complete.append({
                    "status": "supported",
                    "profile_id": f"{group_id}-{overlay_id}",
                    "structural_group_id": group_id,
                    "overlay_ids": [overlay_id],
                    "support_finding_ids": [structural_finding, finding_id],
                    "support_evidence_ids": [structural_evidence, evidence_id],
                    "conditional_overlay_allocation": 0.5,
                })
        with self.assertRaisesRegex(ContractError, "Cartesian"):
            self.build(profile_specs=complete)

    def test_requires_conditional_allocations_and_support_to_resolve(self) -> None:
        for mutation, pattern in (
            (
                lambda specs: specs[0].__setitem__(
                    "conditional_overlay_allocation", 0.5
                ),
                "reconcile",
            ),
            (
                lambda specs: specs[0]["support_evidence_ids"].append(
                    "evidence-unrelated"
                ),
                "support_evidence_ids",
            ),
            (
                lambda specs: specs[0]["overlay_ids"].append("missing-overlay"),
                "overlay_ids",
            ),
        ):
            specs = self.profile_specs()
            mutation(specs)
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ContractError, pattern):
                    self.build(profile_specs=specs)

    def test_rejects_unrelated_affinity_and_non_topic_bound_overlay_evidence(self) -> None:
        with self.assertRaisesRegex(ContractError, "unrelated affinity"):
            self.build(overlay_findings=self.overlay_findings(unrelated=True))
        missing_topic = self.overlay_findings()
        missing_topic[0]["topic_bindings"] = []
        with self.assertRaisesRegex(ContractError, "topic"):
            self.build(overlay_findings=missing_topic)
        wrong_topic_evidence = self.overlay_findings()
        wrong_topic_evidence[0]["topic_bindings"][0]["evidence_ids"] = [
            "evidence-unrelated"
        ]
        with self.assertRaisesRegex(ContractError, "subset"):
            self.build(overlay_findings=wrong_topic_evidence)

    def test_eligible_public_and_incompatible_authorized_routes_cap_tier_two(self) -> None:
        public = self.build(
            frame=self.frame(eligibility="eligible_tier_2"),
            requested_tier="tier_3",
            evidence_basis="public",
        )
        self.assertEqual("tier_2", public["achieved_tier"])
        self.assertIn("public-or-incompatible-frame-caps-tier-2", public["tier_reason_codes"])
        self.assertTrue(public["lost_claims"])

        licensed = self.build(
            frame=self.frame(),
            requested_tier="tier_3",
            evidence_basis="licensed_aggregate",
        )
        self.assertEqual("tier_2", licensed["achieved_tier"])

    def test_tier_four_is_never_constructed(self) -> None:
        plan = self.build(requested_tier="tier_4")
        self.assertEqual("tier_3", plan["achieved_tier"])
        self.assertIn(
            "tier-4-requires-separate-outcome-validation",
            plan["tier_reason_codes"],
        )

    def test_experimental_overlay_forces_tier_one_but_retains_eligible_frame(self) -> None:
        frame = self.frame()
        plan = self.build(
            frame=frame,
            overlay_findings=self.overlay_findings(second_basis="experimental"),
        )
        self.assertEqual("tier_1", plan["achieved_tier"])
        self.assertEqual(sha256_json(frame), plan["frame_binding"]["frame_sha256"])
        self.assertIsNotNone(plan["frame_binding"]["selection"])
        self.assertIn("experimental-overlay-support", plan["tier_reason_codes"])

    def test_no_frame_result_with_research_uses_evidence_backed_tier_one_groups(
        self,
    ) -> None:
        frame = self.no_frame()
        plan = self.build(
            frame=frame,
            structural_findings=self.structural_findings(tier_one=True),
            requested_tier="tier_3",
            evidence_basis="public",
        )
        self.assertEqual("tier_1", plan["achieved_tier"])
        self.assertEqual(sha256_json(frame), plan["frame_binding"]["frame_result_sha256"])
        self.assertIsNone(plan["frame_binding"]["frame_sha256"])
        self.assertTrue(all(
            group["origin"] == "tier_1_evidence"
            and group["cell_ids"] == []
            and group["weight_semantic"] == "planning_allocation"
            for group in plan["structural_groups"]
        ))
        self.assertTrue(all(
            profile["source_cell_ids"] == []
            and profile["support_status"] == "supported"
            for profile in plan["profiles"]
        ))
        self.assertEqual(0.0, plan["modeled_cell_share"])

    def test_none_route_builds_only_explicit_provisional_profiles(self) -> None:
        frame = self.no_frame()
        structural = self.provisional_structural_findings()
        overlays = self.provisional_overlay_findings()
        specs = self.provisional_profile_specs()
        before = copy.deepcopy((frame, structural, overlays, specs))

        plan = self.build(
            frame=frame,
            structural_findings=structural,
            overlay_findings=overlays,
            profile_specs=specs,
            requested_tier="tier_3",
            evidence_basis="none",
        )

        self.assertEqual(before, (frame, structural, overlays, specs))
        self.assertEqual("tier_1", plan["achieved_tier"])
        self.assertTrue(all(
            group["origin"] == "tier_1_provisional"
            and group["structural_finding_ids"] == []
            and group["evidence_ids"] == []
            for group in plan["structural_groups"]
        ))
        self.assertTrue(all(
            overlay["allocation_basis"] == "experimental"
            and overlay["finding_ids"] == []
            and overlay["evidence_ids"] == []
            and overlay["topic_bindings"] == []
            for overlay in plan["overlay_hypotheses"]
        ))
        self.assertTrue(all(
            profile["support_status"] == "provisional"
            and profile["support_finding_ids"] == []
            and profile["support_evidence_ids"] == []
            for profile in plan["profiles"]
        ))
        self.assertEqual({
            ("enterprise-group", ("risk-averse",)),
            ("midmarket-group", ("proof-seeking",)),
            ("midmarket-group", ("proof-seeking", "risk-averse")),
        }, {
            (
                profile["structural_group_id"],
                tuple(profile["overlay_ids"]),
            )
            for profile in plan["profiles"]
        })
        self.assertEqual([{
            "structural_group_id": "enterprise-group",
            "overlay_ids": ["proof-seeking"],
            "reason_code": "unsupported-by-approved-evidence",
            "reason": "No joint support for this structural-overlay pairing.",
        }], plan["unsupported_combinations"])
        self.assertIn(
            "Preserve every explicit materialized profile signature.",
            plan["allocation_constraints"],
        )
        self.assertFalse(any(
            "supported profile" in constraint
            for constraint in plan["allocation_constraints"]
        ))
        self.assertTrue(math.isclose(
            1.0,
            math.fsum(
                profile["effective_profile_allocation"]
                for profile in plan["profiles"]
            ),
            abs_tol=1e-9,
        ))

    def test_none_route_rejects_every_mixed_evidence_state(self) -> None:
        cases = []

        supported = self.provisional_profile_specs()
        supported[0]["status"] = "supported"
        cases.append((
            self.provisional_structural_findings(),
            self.provisional_overlay_findings(),
            supported,
            "status|provisional",
        ))

        structural_finding = self.provisional_structural_findings()
        structural_finding[0]["structural_finding_ids"] = ["invented-finding"]
        cases.append((
            structural_finding,
            self.provisional_overlay_findings(),
            self.provisional_profile_specs(),
            "structural_finding_ids|structural support",
        ))

        structural_evidence = self.provisional_structural_findings()
        structural_evidence[0]["evidence_ids"] = ["invented-evidence"]
        cases.append((
            structural_evidence,
            self.provisional_overlay_findings(),
            self.provisional_profile_specs(),
            "evidence_ids|structural support",
        ))

        overlay_finding = self.provisional_overlay_findings()
        overlay_finding[0]["finding_ids"] = ["invented-finding"]
        cases.append((
            self.provisional_structural_findings(),
            overlay_finding,
            self.provisional_profile_specs(),
            "finding_ids|overlay support",
        ))

        overlay_evidence = self.provisional_overlay_findings()
        overlay_evidence[0]["evidence_ids"] = ["invented-evidence"]
        cases.append((
            self.provisional_structural_findings(),
            overlay_evidence,
            self.provisional_profile_specs(),
            "evidence_ids|overlay support",
        ))

        overlay_topic = self.provisional_overlay_findings()
        overlay_topic[0]["topic_bindings"] = [{
            "topic_id": "invented-topic",
            "evidence_ids": ["invented-evidence"],
        }]
        cases.append((
            self.provisional_structural_findings(),
            overlay_topic,
            self.provisional_profile_specs(),
            "topic_bindings|overlay support",
        ))

        observed_overlay = self.provisional_overlay_findings()
        observed_overlay[0]["allocation_basis"] = "observed"
        cases.append((
            self.provisional_structural_findings(),
            observed_overlay,
            self.provisional_profile_specs(),
            "allocation_basis|experimental",
        ))

        profile_finding = self.provisional_profile_specs()
        profile_finding[0]["support_finding_ids"] = ["invented-finding"]
        cases.append((
            self.provisional_structural_findings(),
            self.provisional_overlay_findings(),
            profile_finding,
            "support_finding_ids|support bindings",
        ))

        profile_evidence = self.provisional_profile_specs()
        profile_evidence[0]["support_evidence_ids"] = ["invented-evidence"]
        cases.append((
            self.provisional_structural_findings(),
            self.provisional_overlay_findings(),
            profile_evidence,
            "support_evidence_ids|support bindings",
        ))

        for structural, overlays, specs, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ContractError, pattern):
                    self.build(
                        frame=self.no_frame(),
                        structural_findings=structural,
                        overlay_findings=overlays,
                        profile_specs=specs,
                        requested_tier="tier_1",
                        evidence_basis="none",
                    )

    def test_evidence_backed_routes_reject_provisional_or_empty_support(
        self,
    ) -> None:
        provisional = self.profile_specs()
        provisional[0]["status"] = "provisional"
        with self.assertRaisesRegex(ContractError, "status|supported"):
            self.build(profile_specs=provisional)

        empty_support = self.profile_specs()
        empty_support[0]["support_finding_ids"] = []
        empty_support[0]["support_evidence_ids"] = []
        with self.assertRaisesRegex(
            ContractError,
            "support_finding_ids|support_evidence_ids",
        ):
            self.build(profile_specs=empty_support)

        with self.assertRaisesRegex(ContractError, "no-frame|usable frame"):
            self.build(
                frame=self.frame(),
                structural_findings=self.provisional_structural_findings(),
                overlay_findings=self.provisional_overlay_findings(),
                profile_specs=self.provisional_profile_specs(),
                requested_tier="tier_1",
                evidence_basis="none",
            )

    def test_requires_canonical_no_frame_result_instead_of_inventing_one(self) -> None:
        with self.assertRaisesRegex(ContractError, "population-frame result"):
            build_composition_plan(
                population_frame=None,
                structural_findings=self.structural_findings(tier_one=True),
                overlay_findings=self.overlay_findings(),
                supported_profile_specs=self.profile_specs(),
                requested_tier="tier_1",
                evidence_basis="none",
                plan_id="operations-panel-composition",
                plan_version="1.0.0",
                built_at="2026-07-24T14:00:00Z",
            )

    def test_rejects_ambiguous_or_partial_frame_collection_selection(self) -> None:
        partial = self.structural_findings()
        partial.pop()
        with self.assertRaisesRegex(ContractError, "exactly one|partition"):
            self.build(structural_findings=partial)

        frame = self.frame()
        duplicate = copy.deepcopy(frame["joints"][0])
        frame["joints"].append(duplicate)
        with self.assertRaisesRegex((ValueError, ContractError), "duplicate|exactly one"):
            self.build(frame=frame)

    def test_output_contains_reusable_constraints_but_no_quota_or_capacity(self) -> None:
        plan = self.build()
        encoded = canonical_json_bytes(plan).decode("utf-8")
        for forbidden in (
            "study_quota",
            "quota_count",
            "slot_count",
            "panelist_count",
            "capacity",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(
            {
                "reserve_strategy": "largest-remainder",
                "min_one_for_must_cover": True,
            },
            plan["run_allocation_rules"],
        )
        self.assertTrue(plan["allocation_constraints"])
        self.assertTrue(plan["required_diagnostics"])

    def test_golden_experimental_public_proxy_is_deterministic(self) -> None:
        frame = json.loads(
            (FIXTURES / "expected-population-frame.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (FIXTURES / "overlay-evidence.json").read_text(encoding="utf-8")
        )
        actual = build_composition_plan(
            population_frame=frame,
            structural_findings=evidence["structural_findings"],
            overlay_findings=evidence["overlay_findings"],
            supported_profile_specs=evidence["profile_specs"],
            requested_tier="tier_2",
            evidence_basis="public",
            plan_id="marketing-leader-proxy-composition",
            plan_version="1.0.0",
            built_at="2026-07-23T12:00:00Z",
        )
        expected = json.loads(
            (FIXTURES / "expected-composition-plan.json").read_text(
                encoding="utf-8"
            )
        )
        for profile in expected["profiles"]:
            profile["support_status"] = "supported"
        expected["allocation_constraints"][0] = (
            "Preserve every explicit materialized profile signature."
        )
        self.assertEqual(expected, actual)
        reversed_actual = build_composition_plan(
            population_frame=frame,
            structural_findings=list(reversed(evidence["structural_findings"])),
            overlay_findings=list(reversed(evidence["overlay_findings"])),
            supported_profile_specs=list(reversed(evidence["profile_specs"])),
            requested_tier="tier_2",
            evidence_basis="public",
            plan_id="marketing-leader-proxy-composition",
            plan_version="1.0.0",
            built_at="2026-07-23T12:00:00Z",
        )
        self.assertEqual(actual, reversed_actual)

    def test_cli_writes_canonical_output_once_and_refuses_clobber(self) -> None:
        frame = self.frame(eligibility="eligible_tier_2")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for label, value in (
                ("population-frame", frame),
                ("structural-findings", self.structural_findings()),
                ("overlay-findings", self.overlay_findings()),
                ("profile-specs", self.profile_specs()),
            ):
                paths[label] = root / f"{label}.json"
                paths[label].write_text(json.dumps(value), encoding="utf-8")
            output = root / "composition.json"
            command = [
                sys.executable,
                str(SCRIPTS / "build-panel-composition.py"),
                "--population-frame",
                str(paths["population-frame"]),
                "--structural-findings",
                str(paths["structural-findings"]),
                "--overlay-findings",
                str(paths["overlay-findings"]),
                "--profile-specs",
                str(paths["profile-specs"]),
                "--requested-tier",
                "tier_2",
                "--evidence-basis",
                "public",
                "--plan-id",
                "operations-panel-composition",
                "--plan-version",
                "1.0.0",
                "--built-at",
                "2026-07-24T14:00:00Z",
                "--output",
                str(output),
            ]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, first.returncode, first.stdout)
            self.assertEqual(
                canonical_json_bytes(self.build(
                    frame=frame,
                    requested_tier="tier_2",
                    evidence_basis="public",
                )),
                output.read_bytes(),
            )
            before = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(3, second.returncode, second.stdout)
            self.assertEqual(before, output.read_bytes())
            self.assertEqual("output_collision", json.loads(second.stdout)["error"])

    def test_finalizes_validity_with_exact_real_bindings_without_mutation(self) -> None:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )

        source = AudienceResearchV3ContractTests()
        source.setUpClass()
        frame = self.frame()
        composition = self.build(frame=frame)
        provisional = source.validity(tier="tier_3")
        provisional["binding_state"] = "frame_provisional"
        provisional["panel_id"] = None
        provisional["panel_tier"] = None
        provisional["evidence_basis"] = None
        provisional["source_bindings"] = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": sha256_json(frame),
            "frame_sha256": sha256_json(frame),
            "composition_sha256": None,
        }
        before = copy.deepcopy(provisional)
        final = finalize_validity_profile(
            provisional_validity=provisional,
            population_frame=frame,
            composition_plan=composition,
            panel_id="operations-leaders",
            panel_tier="tier_3",
            evidence_basis="first_party_aggregate",
            brief_sha256="sha256:" + "1" * 64,
            panel_projection_sha256="sha256:" + "2" * 64,
        )
        self.assertEqual(before, provisional)
        self.assertEqual("panel_final", final["binding_state"])
        self.assertEqual("operations-leaders", final["panel_id"])
        self.assertEqual(sha256_json(frame), final["source_bindings"]["frame_sha256"])
        self.assertEqual("sha256:" + "1" * 64, final["source_bindings"]["brief_sha256"])
        self.assertEqual("sha256:" + "2" * 64, final["source_bindings"]["panel_sha256"])
        self.assertEqual(
            sha256_json(composition),
            final["source_bindings"]["composition_sha256"],
        )

    def test_final_validity_derives_null_or_retained_usable_frame_from_composition(self) -> None:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )

        source = AudienceResearchV3ContractTests()
        source.setUpClass()
        cases = []

        experimental_frame = self.frame(eligibility="experimental")
        experimental_frame["downgrade_reason"] = (
            "Modeled structural support is experimental."
        )
        experimental_provisional = source.validity(tier="tier_1", evidence_basis="none")
        experimental_provisional.update({
            "binding_state": "frame_provisional",
            "panel_id": None,
            "panel_tier": None,
            "evidence_basis": None,
        })
        experimental_provisional["source_bindings"] = {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": sha256_json(experimental_frame),
            # This intentionally reproduces the stale provisional binding.
            "frame_sha256": sha256_json(experimental_frame),
            "composition_sha256": None,
        }
        experimental_composition = self.build(
            frame=experimental_frame,
            structural_findings=self.provisional_structural_findings(),
            overlay_findings=self.provisional_overlay_findings(),
            profile_specs=self.provisional_profile_specs(),
            requested_tier="tier_1",
            evidence_basis="none",
        )
        cases.append((
            experimental_frame,
            experimental_composition,
            experimental_provisional,
            "none",
            None,
        ))

        no_frame = self.no_frame()
        no_frame_provisional = copy.deepcopy(experimental_provisional)
        no_frame_provisional["source_bindings"].update({
            "frame_result_sha256": sha256_json(no_frame),
            "frame_sha256": None,
        })
        no_frame_composition = self.build(
            frame=no_frame,
            structural_findings=self.provisional_structural_findings(),
            overlay_findings=self.provisional_overlay_findings(),
            profile_specs=self.provisional_profile_specs(),
            requested_tier="tier_1",
            evidence_basis="none",
        )
        cases.append((
            no_frame,
            no_frame_composition,
            no_frame_provisional,
            "none",
            None,
        ))

        eligible_frame = self.frame()
        retained_provisional = copy.deepcopy(experimental_provisional)
        retained_provisional["source_bindings"].update({
            "frame_result_sha256": sha256_json(eligible_frame),
            "frame_sha256": sha256_json(eligible_frame),
        })
        retained_composition = self.build(
            frame=eligible_frame,
            overlay_findings=self.overlay_findings(second_basis="experimental"),
        )
        cases.append((
            eligible_frame,
            retained_composition,
            retained_provisional,
            "first_party_aggregate",
            sha256_json(eligible_frame),
        ))

        for frame, composition, provisional, evidence_basis, expected_frame in cases:
            before = copy.deepcopy(provisional)
            with self.subTest(eligibility=frame["eligibility"]):
                final = finalize_validity_profile(
                    provisional_validity=provisional,
                    population_frame=frame,
                    composition_plan=composition,
                    panel_id="operations-leaders",
                    panel_tier="tier_1",
                    evidence_basis=evidence_basis,
                    brief_sha256="sha256:" + "1" * 64,
                    panel_projection_sha256="sha256:" + "2" * 64,
                )
                self.assertEqual(before, provisional)
                self.assertEqual(
                    expected_frame,
                    final["source_bindings"]["frame_sha256"],
                )
                self.assertEqual(
                    sha256_json(composition),
                    final["source_bindings"]["composition_sha256"],
                )

    def test_final_validity_rejects_nonprovisional_or_missing_fake_bindings(self) -> None:
        from conformance.test_audience_research_v3 import (
            AudienceResearchV3ContractTests,
        )

        source = AudienceResearchV3ContractTests()
        source.setUpClass()
        final = source.validity(tier="tier_3")
        frame = self.frame()
        composition = self.build(frame=frame)
        with self.assertRaisesRegex(ContractError, "frame_provisional"):
            finalize_validity_profile(
                provisional_validity=final,
                population_frame=frame,
                composition_plan=composition,
                panel_id="operations-leaders",
                panel_tier="tier_3",
                evidence_basis="first_party_aggregate",
                brief_sha256="sha256:" + "1" * 64,
                panel_projection_sha256="sha256:" + "2" * 64,
            )
        provisional = copy.deepcopy(final)
        provisional.update({
            "binding_state": "frame_provisional",
            "panel_id": None,
            "panel_tier": None,
            "evidence_basis": None,
        })
        provisional["source_bindings"].update({
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": sha256_json(frame),
            "frame_sha256": sha256_json(frame),
            "composition_sha256": None,
        })
        with self.assertRaisesRegex(ContractError, "brief_sha256|digest|SHA-256"):
            finalize_validity_profile(
                provisional_validity=provisional,
                population_frame=frame,
                composition_plan=composition,
                panel_id="operations-leaders",
                panel_tier="tier_3",
                evidence_basis="first_party_aggregate",
                brief_sha256="",
                panel_projection_sha256="sha256:" + "2" * 64,
            )

        tampered_frame = copy.deepcopy(frame)
        tampered_frame["coverage_assessment"]["known_gaps"].append("Tampered.")
        with self.assertRaisesRegex(ContractError, "frame_result|frame result"):
            finalize_validity_profile(
                provisional_validity=provisional,
                population_frame=tampered_frame,
                composition_plan=composition,
                panel_id="operations-leaders",
                panel_tier="tier_3",
                evidence_basis="first_party_aggregate",
                brief_sha256="sha256:" + "1" * 64,
                panel_projection_sha256="sha256:" + "2" * 64,
            )


if __name__ == "__main__":
    unittest.main()
