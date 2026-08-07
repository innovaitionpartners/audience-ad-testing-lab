from __future__ import annotations

import copy
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
from audience_panel_builder.evidence import (  # noqa: E402
    validate_evidence_ledger,
    validate_finding_support,
)
from audience_panel_builder.reporting import (  # noqa: E402
    REPORT_INPUT_SCHEMA_VERSION,
    REPORT_MANIFEST_SCHEMA_VERSION,
    build_source_inventory,
    build_verbatim_inventory,
    render_research_report,
    validate_report_inputs,
)
from audience_panel_builder.review import (  # noqa: E402
    build_panel_review_manifest,
    render_panel_review_html,
    render_panel_summary,
)
from audience_panel_builder.source_scoring import score_source_candidates  # noqa: E402
from audience_panel_builder.workflow_state import workflow_state_sha256  # noqa: E402
from conformance import test_audience_research_v3 as v3_contract_tests  # noqa: E402


HASH = "a" * 64
_runtime_render_research_report = render_research_report


def render_research_report(*args, **kwargs):
    """Test helper that supplies the now-mandatory exact review bundle."""

    if kwargs.get("panel_review_manifest") is None:
        documents = kwargs["documents"]
        summary = render_panel_summary(
            documents["brief"], documents["panel"]
        ).encode("utf-8")
        html = render_panel_review_html(
            documents["brief"], documents["panel"]
        ).encode("utf-8")
        kwargs.update(
            panel_review_manifest=build_panel_review_manifest(
                panel=documents["panel"],
                summary_bytes=summary,
                html_bytes=html,
                review_revision="review-v1",
                generated_at=documents["panel"]["updated_at"],
            ),
            panel_review_summary=summary,
            panel_review_html=html,
        )
    return _runtime_render_research_report(*args, **kwargs)


class PanelResearchReportTests(unittest.TestCase):
    maxDiff = None

    def panel_review_cli_args(
        self, root: Path, documents: dict[str, dict[str, object]], *, label: str
    ) -> list[str]:
        summary = render_panel_summary(documents["brief"], documents["panel"])
        html = render_panel_review_html(documents["brief"], documents["panel"])
        manifest = build_panel_review_manifest(
            panel=documents["panel"],
            summary_bytes=summary.encode("utf-8"),
            html_bytes=html.encode("utf-8"),
            review_revision="review-v1",
            generated_at=documents["panel"]["updated_at"],
        )
        manifest_path = root / f"{label}-panel-review-manifest.json"
        summary_path = root / f"{label}-panel-summary.md"
        html_path = root / f"{label}-panel-review.html"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        summary_path.write_text(summary, encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        return [
            "--panel-review-manifest", str(manifest_path),
            "--panel-summary", str(summary_path),
            "--panel-review-html", str(html_path),
        ]

    def documents(self) -> dict[str, dict[str, object]]:
        fixtures = ROOT / "conformance" / "fixtures" / "audience-research"
        brief = json.loads((fixtures / "approved-brief.json").read_text())
        panel = json.loads((fixtures / "approved-panel.json").read_text())
        ledger = {
            "schema_version": "audience-evidence-ledger-v1",
            "ledger_id": "operations-leaders-ledger",
            "created_at": "2026-07-23T12:00:00Z",
            "plan_id": "operations-leaders-plan",
            "imports": [{
                "import_id": "research-import",
                "source_adapter": "document-research",
                "source_schema_version": "1.0",
                "input_sha256": "sha256:" + "1" * 64,
                "permission": "allowed",
                "source_status": "verified",
                "accepted_count": 1,
                "rejected_count": 0,
                "deduplicated_count": 0,
            }],
            "evidence_items": [{
                "evidence_item_id": "evidence-item-1",
                "import_id": "research-import",
                "source_url": "https://example.test/research",
                "item_type": "survey_finding",
                "content_summary": "Leaders seek implementation proof before changing core workflows.",
                "text_fidelity": "analyst_summary",
                "content_sha256": "sha256:" + "2" * 64,
                "source_pointer": "page-4",
                "upstream_source_ids": ["study-1"],
                "use_constraints": ["directional_only"],
                "quality_flags": [],
            }],
            "summary": {"imports": 1, "accepted_items": 1, "deduplicated_items": 0, "rejected_items": 0},
        }
        support = {
            "schema_version": "audience-finding-support-v1",
            "created_at": "2026-07-23T12:05:00Z",
            "ledger_sha256": sha256_json(ledger),
            "findings": [{
                "finding_id": "finding-implementation-proof",
                "evidence_id": "evidence-1",
                "evidence_item_ids": ["evidence-item-1"],
                "support_role": "supports",
                "analyst_note": "Direct support for the reviewed finding.",
            }],
        }
        synthesis = {
            "schema_version": "audience-synthesis-matrix-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:10:00Z",
            "ledger_sha256": sha256_json(ledger),
            "questions": [{
                "question_id": "implementation-proof",
                "research_question": "What proof reduces implementation risk?",
                "findings": [{
                    "finding_id": "finding-implementation-proof",
                    "statement": "Leaders seek implementation proof before changing core workflows.",
                    "category": "proof_needs",
                    "evidence_item_ids": ["evidence-item-1"],
                    "supporting_item_ids": ["evidence-item-1"],
                    "qualifying_item_ids": [],
                    "contradicting_item_ids": [],
                    "integration_state": "single_source",
                    "methodological_limitations": "minor_concerns",
                    "relevance": "no_serious_concerns",
                    "coherence": "minor_concerns",
                    "adequacy": "minor_concerns",
                    "confidence": "medium",
                    "confidence_reason": "One decision-relevant documented source.",
                    "inference_boundary": "Directional finding only; not population prevalence.",
                    "marketer_implication": "Show implementation mechanism and proof.",
                    "creative_implications": ["Make proof visible."],
                    "segment_decision": "candidate",
                }],
            }],
        }
        candidate = {
            "candidate_id": "study-1",
            "source_family_id": "survey-source",
            "lane": "survey",
            "title": "=Unsafe-looking source title",
            "publisher": "Fictional Research Group",
            "source_url": "https://example.test/research",
            "methodology_url": "https://example.test/method",
            "publication_date": "2026-07-01",
            "field_dates": "2026-06",
            "population": "Operations leaders",
            "geography": "United States",
            "sample_size": 100,
            "collection_method": "Survey",
            "access_route": "public",
            "reuse_status": "allowed",
            "assessments": {
                "audience_match": "exact", "decision_match": "exact",
                "methodology_transparency": "documented", "collection_quality": "documented",
                "recency": "current", "geography_match": "exact",
                "subgroup_usefulness": "direct", "permitted_use": "allowed",
            },
            "social_collection": None,
            "upstream_source_ids": ["study-1"],
            "evidence_item_ids": ["evidence-item-1"],
            "notes": "Used as directional evidence.",
        }
        scored_sources = score_source_candidates({
            "schema_version": "audience-source-candidates-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z",
            "candidates": [candidate],
        })
        workflow_state = {
            "schema_version": "panel-workflow-state-v1",
            "workflow_id": "operations-leaders-build",
            "panel_id": "operations-leaders",
            "panel_version": "1.0.0",
            "state": "dogfood",
            "updated_at": "2026-07-23T12:15:00Z",
            "approvals": [],
            "bindings": {
                "brief_sha256": sha256_json(brief).removeprefix("sha256:"),
                "panel_sha256": sha256_json(panel).removeprefix("sha256:"),
                "report_inputs_sha256": None, "audit_sha256": None, "package_sha256": None,
            },
        }
        documents = {
            "workflow_state": workflow_state,
            "brief": brief,
            "panel": panel,
            "plan": {"plan_id": "operations-leaders-plan", "workflow_route": "create_research_backed_panel"},
            "scored_sources": scored_sources,
            "evidence_ledger": ledger,
            "finding_support": support,
            "synthesis_matrix": synthesis,
        }
        documents["source_inventory"] = build_source_inventory(
            scored_sources=scored_sources, evidence_ledger=ledger,
        )
        documents["verbatim_inventory"] = build_verbatim_inventory(
            evidence_ledger=ledger, finding_support=support,
        )
        return documents

    def approved_workflow_state(self, documents: dict[str, dict[str, object]]) -> None:
        state = documents["workflow_state"]
        state["state"] = "approved"
        state["approvals"] = [
            {
                "scope": "evidence_synthesis", "status": "approved",
                "approved_by": "reviewer", "approved_at": "2026-07-23T12:20:00Z",
                "target_sha256": sha256_json(documents["synthesis_matrix"]).removeprefix("sha256:"),
                "note": "Evidence synthesis approved.",
            },
            {
                "scope": "panel_construction", "status": "approved",
                "approved_by": "reviewer", "approved_at": "2026-07-23T12:20:00Z",
                "target_sha256": sha256_json(documents["panel"]).removeprefix("sha256:"),
                "note": "Panel construction approved.",
            },
        ]

    def report_inputs(self, documents: dict[str, dict[str, object]]) -> dict[str, object]:
        validity = {"population_claim": "not_available", "reason": "No population frame or composition document is available in Release A."}
        return {
            "schema_version": REPORT_INPUT_SCHEMA_VERSION,
            "panel_id": "operations-leaders",
            "panel_version": "1.0.0",
            "workflow_state_sha256": workflow_state_sha256(documents["workflow_state"]),
            "frame_sha256": None,
            "evidence_ledger_sha256": sha256_json(documents["evidence_ledger"]).removeprefix("sha256:"),
            "finding_support_sha256": sha256_json(documents["finding_support"]).removeprefix("sha256:"),
            "synthesis_matrix_sha256": sha256_json(documents["synthesis_matrix"]).removeprefix("sha256:"),
            "scored_sources_sha256": sha256_json(documents["scored_sources"]).removeprefix("sha256:"),
            "composition_sha256": None,
            "validity_sha256": sha256_json(validity).removeprefix("sha256:"),
            "source_inventory_sha256": sha256_json(documents["source_inventory"]).removeprefix("sha256:"),
            "verbatim_inventory_sha256": sha256_json(documents["verbatim_inventory"]).removeprefix("sha256:"),
        }

    def test_report_requires_an_explicit_exact_panel_review_bundle(self):
        documents = self.documents()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "are required together"):
                _runtime_render_research_report(
                    report_inputs=self.report_inputs(documents),
                    documents=documents,
                    generated_at="2026-07-23T12:30:00Z",
                    output_dir=Path(temp) / "report",
                )

    def v3_report_documents(
        self,
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        documents = self.documents()
        frame = {
            "schema_version": "audience-population-frame-v1",
            "frame_id": "operations-public-proxy-frame",
            "frame_version": "1.0.0",
            "built_at": "2026-07-24T13:00:00Z",
            "frame_request_id": "operations-public-proxy-request",
            "frame_request_sha256": "sha256:" + "1" * 64,
            "target_universe": "U.S. operations leaders",
            "proxy_universes": ["employed-person-proxy"],
            "claim_boundary": (
                "Public proxy frame only. This proxy does not represent the "
                "full commercial target audience."
            ),
            "units": [{
                "partition_id": "employed-persons",
                "unit": "persons",
                "denominator": "employed-persons-excluding-self-employed",
                "exact": False,
            }],
            "structural_dimensions": ["geography", "role"],
            "cells": [{
                "cell_id": "operations-observed",
                "partition_id": "employed-persons",
                "dimension_values": {"role": "operations"},
                "relationship": "marginal",
                "origin": "source_observation",
                "modeled_rule_id": None,
                "status": "observed",
                "structural_weight": 0.7,
                "weight_semantic": "population_weight",
                "uncertainty": {"lower": 0.65, "upper": 0.75},
                "suppressed": False,
                "source_observations": [{
                    "batch_id": "operations-frame-batch",
                    "cell_id": "operations-observed",
                }],
                "calibration_factor": 1.0,
            }, {
                "cell_id": "operations-modeled",
                "partition_id": "employed-persons",
                "dimension_values": {"role": "adjacent-operations"},
                "relationship": "marginal",
                "origin": "modeled_rule",
                "modeled_rule_id": "adjacent-operations-rule",
                "status": "modeled",
                "structural_weight": 0.3,
                "weight_semantic": "experimental_modeled_weight",
                "uncertainty": {"lower": 0.25, "upper": 0.35},
                "suppressed": False,
                "source_observations": [],
                "calibration_factor": 1.0,
            }],
            "margins": [{
                "partition_id": "employed-persons",
                "dimensions": ["role"],
                "cell_ids": ["operations-modeled", "operations-observed"],
                "missing_reason": None,
            }],
            "joints": [{
                "partition_id": "employed-persons",
                "dimensions": ["geography", "role"],
                "cell_ids": [],
                "missing_reason": (
                    "The critical geography × role joint is not available."
                ),
            }],
            "source_bindings": [{
                "batch_id": "operations-frame-batch",
                "normalized_batch_sha256": "sha256:" + "2" * 64,
                "raw_snapshot_sha256": "sha256:" + "3" * 64,
                "partition_id": "employed-persons",
                "source": {
                    "publisher": "Fictional statistical office",
                    "program": "Employment proxy",
                    "edition": "2026",
                    "vintage": "2026-07-01",
                    "retrieved_at": "2026-07-24T12:00:00Z",
                },
                "geography": ["US"],
                "access": {
                    "access_type": "public",
                    "permission_confirmed": True,
                    "permitted_uses": ["population-framing"],
                },
                "selection_notes": "Selected before panel composition.",
                "coverage_notes": "Transformation loss: none recorded.",
            }],
            "coverage_assessment": {
                "selection_statement": "One named public proxy was selected.",
                "coverage_statement": "Self-employed people are excluded.",
                "known_gaps": ["Missing geography × role joint."],
            },
            "modeled_weight_by_dimension": [{
                "partition_id": "employed-persons",
                "dimension": "role",
                "share": 0.3,
                "status": "supported",
            }],
            "modeled_weight_share": 0.3,
            "eligibility": "eligible_tier_2",
            "downgrade_reason": "",
        }
        composition = {
            "schema_version": "panel-composition-plan-v1",
            "composition_id": "operations-composition",
            "plan_version": "1.0.0",
            "built_at": "2026-07-24T14:00:00Z",
            "evidence_basis": "public",
            "requested_tier": "tier_2",
            "achieved_tier": "tier_2",
            "tier_reason_codes": [],
            "lost_claims": [],
            "frame_binding": {
                "frame_result_sha256": sha256_json(frame),
                "frame_sha256": sha256_json(frame),
                "frame_id": frame["frame_id"],
                "selection": {
                    "partition_id": "employed-persons",
                    "relationship": "marginal",
                    "dimensions": ["role"],
                },
            },
            "structural_groups": [{
                "structural_group_id": "observed-group",
                "origin": "frame_cells",
                "cell_ids": ["operations-observed"],
                "structural_finding_ids": ["finding-implementation-proof"],
                "evidence_ids": ["evidence-item-1"],
                "structural_weight": 0.7,
                "weight_semantic": "population_weight",
                "must_cover": True,
            }, {
                "structural_group_id": "modeled-group",
                "origin": "frame_cells",
                "cell_ids": ["operations-modeled"],
                "structural_finding_ids": ["finding-implementation-proof"],
                "evidence_ids": ["evidence-item-1"],
                "structural_weight": 0.3,
                "weight_semantic": "experimental_modeled_weight",
                "must_cover": True,
            }],
            "overlay_hypotheses": [{
                "overlay_id": "proof-seeking",
                "description": "Seeks implementation proof.",
                "allocation_basis": "estimated",
                "finding_ids": ["finding-implementation-proof"],
                "evidence_ids": ["evidence-item-1"],
                "topic_bindings": [{
                    "topic_id": "implementation-proof",
                    "evidence_ids": ["evidence-item-1"],
                }],
            }, {
                "overlay_id": "risk-averse",
                "description": "Seeks visible risk controls.",
                "allocation_basis": "estimated",
                "finding_ids": ["finding-implementation-proof"],
                "evidence_ids": ["evidence-item-1"],
                "topic_bindings": [{
                    "topic_id": "implementation-risk",
                    "evidence_ids": ["evidence-item-1"],
                }],
            }],
            "profiles": [{
                "profile_id": "observed-proof-seeking",
                "structural_group_id": "observed-group",
                "overlay_ids": ["proof-seeking", "risk-averse"],
                "support_status": "supported",
                "support_finding_ids": ["finding-implementation-proof"],
                "support_evidence_ids": ["evidence-item-1"],
                "conditional_overlay_allocation": 1.0,
                "overlay_weight_semantic": "planning_allocation",
                "effective_profile_allocation": 0.7,
                "effective_weight_semantic": "population_weight",
                "source_cell_ids": ["operations-observed"],
            }, {
                "profile_id": "modeled-proof-seeking",
                "structural_group_id": "modeled-group",
                "overlay_ids": ["risk-averse"],
                "support_status": "supported",
                "support_finding_ids": ["finding-implementation-proof"],
                "support_evidence_ids": ["evidence-item-1"],
                "conditional_overlay_allocation": 1.0,
                "overlay_weight_semantic": "planning_allocation",
                "effective_profile_allocation": 0.3,
                "effective_weight_semantic": "experimental_modeled_weight",
                "source_cell_ids": ["operations-modeled"],
            }],
            "unsupported_combinations": [{
                "structural_group_id": "modeled-group",
                "overlay_ids": ["proof-seeking"],
                "reason_code": "unsupported-by-approved-evidence",
                "reason": "The modeled group lacks support for this overlay.",
            }],
            "allocation_constraints": ["Preserve explicit structural groups."],
            "run_allocation_rules": {
                "reserve_strategy": "largest-remainder",
                "min_one_for_must_cover": True,
            },
            "required_diagnostics": ["effective-allocation-drift"],
            "modeled_cell_share": 0.3,
        }
        frame_digest = sha256_json(frame)
        composition_digest = sha256_json(composition)
        validity = {
            "schema_version": "panel-validity-profile-v1",
            "validity_id": "operations-panel-validity",
            "binding_state": "panel_final",
            "panel_id": documents["panel"]["panel_id"],
            "panel_tier": "tier_2",
            "evidence_basis": "public",
            "axes": {
                "structural_frame": {
                    "status": "supported",
                    "coverage": 1.0,
                    "limitations": ["Missing geography × role joint."],
                },
                "overlay_evidence": {
                    "status": "directional",
                    "coverage": None,
                    "limitations": ["Overlay allocation is planning-only."],
                },
                "allocation_fidelity": {
                    "status": "not_available",
                    "coverage": None,
                    "limitations": ["No run allocation exists in B1."],
                },
                "outcome_calibration": {
                    "status": "not_available",
                    "coverage": None,
                    "limitations": ["No held-out outcomes supplied."],
                },
                "external_validation": {
                    "status": "not_available",
                    "coverage": None,
                    "limitations": ["No external validation supplied."],
                },
            },
            "predeclared_validation_design": None,
            "held_out_outcome_evidence": [],
            "source_bindings": {
                "brief_sha256": sha256_json(documents["brief"]),
                "panel_sha256": sha256_json(documents["panel"]),
                "frame_result_sha256": frame_digest,
                "frame_sha256": frame_digest,
                "composition_sha256": composition_digest,
            },
        }
        documents.update({
            "population_frame": frame,
            "composition_plan": composition,
            "validity_profile": validity,
        })
        inputs = self.report_inputs(documents)
        inputs.update({
            "frame_sha256": frame_digest.removeprefix("sha256:"),
            "composition_sha256": composition_digest.removeprefix("sha256:"),
            "validity_sha256": sha256_json(validity).removeprefix("sha256:"),
        })
        return documents, inputs

    def canonical_v3_report_documents(
        self,
        *,
        tier: str = "tier_2",
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        documents = self.documents()
        contracts = v3_contract_tests.AudienceResearchV3ContractTests()
        contracts.v2_brief = copy.deepcopy(documents["brief"])
        contracts.v2_panel = copy.deepcopy(documents["panel"])
        evidence_basis = "none" if tier == "tier_1" else "public"
        bundle = list(
            contracts.v3_pair(tier=tier, evidence_basis=evidence_basis)
        )
        contracts.validate_v3_documents(bundle)
        return self.report_documents_from_v3_bundle(bundle)

    def report_documents_from_v3_bundle(
        self,
        bundle: list[dict[str, object]],
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        documents = self.documents()
        brief, panel, frame, composition, validity, _workflow, _audit = bundle
        documents.update({
            "brief": brief,
            "panel": panel,
            "population_frame": frame,
            "composition_plan": composition,
            "validity_profile": validity,
        })
        inputs = self.report_inputs(documents)
        inputs.update({
            "frame_sha256": (
                None
                if frame["eligibility"] not in {
                    "eligible_tier_2",
                    "eligible_tier_3",
                }
                else sha256_json(frame).removeprefix("sha256:")
            ),
            "composition_sha256": sha256_json(composition).removeprefix("sha256:"),
            "validity_sha256": sha256_json(validity).removeprefix("sha256:"),
        })
        return documents, inputs

    def test_report_inputs_are_closed_and_allow_only_release_a_nulls(self):
        inputs = self.report_inputs(self.documents())
        self.assertEqual(inputs, validate_report_inputs(inputs))
        unknown = copy.deepcopy(inputs)
        unknown["unexpected"] = True
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_report_inputs(unknown)
        invalid = copy.deepcopy(inputs)
        invalid["evidence_ledger_sha256"] = None
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            validate_report_inputs(invalid)

    def test_v3_report_requires_composition_and_validity_and_checks_frame_hash(self):
        documents, inputs = self.v3_report_documents()
        self.assertEqual(inputs, validate_report_inputs(inputs))
        for key in ("composition_sha256", "validity_sha256"):
            invalid = copy.deepcopy(inputs)
            invalid[key] = None
            with self.subTest(key=key):
                with self.assertRaises(ContractError):
                    validate_report_inputs(invalid)
        null_frame = copy.deepcopy(inputs)
        null_frame["frame_sha256"] = None
        self.assertEqual(null_frame, validate_report_inputs(null_frame))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "population frame"):
                render_research_report(
                    report_inputs=null_frame,
                    documents=documents,
                    generated_at="2026-07-24T13:30:00Z",
                    output_dir=Path(temp) / "false-null-frame",
                )
        stale = copy.deepcopy(inputs)
        stale["frame_sha256"] = HASH
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "population frame"):
                render_research_report(
                    report_inputs=stale,
                    documents=documents,
                    generated_at="2026-07-24T13:30:00Z",
                    output_dir=Path(temp) / "stale-frame",
                )

    def test_v3_report_exposes_boundaries_units_cells_gaps_and_separate_axes(self):
        documents, inputs = self.v3_report_documents()
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "v3-report"
            render_research_report(
                report_inputs=inputs,
                documents=documents,
                generated_at="2026-07-24T13:30:00Z",
                output_dir=output_dir,
            )
            html = (output_dir / "audience-research-report.html").read_text()
        for exact in (
            "Public proxy frame only",
            "persons",
            "employed-persons-excluding-self-employed",
            "Observed",
            "Modeled",
            "30.0%",
            "Missing geography × role joint.",
            "Transformation loss: none recorded.",
            "population_weight",
            "experimental_modeled_weight",
            "Structural Frame",
            "Overlay Evidence",
            "Allocation Fidelity",
            "Outcome Calibration",
            "External Validation",
            "Selected structural collection",
            "employed-persons / marginal / role",
            "Requested Tier 2; achieved Tier 2",
            "proof-seeking + risk-averse",
            "unsupported-by-approved-evidence",
        ):
            self.assertIn(exact, html)
        self.assertNotIn("Composite confidence", html)

    def test_report_dispatches_real_canonical_v3_brief_and_panel(self):
        documents, inputs = self.canonical_v3_report_documents()
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "canonical-v3-report"
            render_research_report(
                report_inputs=inputs,
                documents=documents,
                generated_at="2026-07-24T13:30:00Z",
                output_dir=output_dir,
            )
            html = (output_dir / "audience-research-report.html").read_text()

        self.assertEqual("audience-research-brief-v3", documents["brief"]["schema_version"])
        self.assertEqual("saved-audience-panel-v3", documents["panel"]["schema_version"])
        self.assertIn("Authorized cohort composition only", html)

    def test_panel_review_cli_projects_canonical_v3_without_schema_migration(self):
        documents, _ = self.canonical_v3_report_documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brief_path = root / "brief.json"
            panel_path = root / "panel.json"
            brief_path.write_text(json.dumps(documents["brief"]))
            panel_path.write_text(json.dumps(documents["panel"]))
            output_dir = root / "review"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "render-panel-review.py"),
                    "--brief", str(brief_path),
                    "--panel", str(panel_path),
                    "--output-dir", str(output_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            summary = (output_dir / "panel-summary.md").read_text()
            html = (output_dir / "audience-panel-review.html").read_text()
            manifest = json.loads((output_dir / "panel-review-manifest.json").read_text())
        self.assertIn("saved-audience-panel-v3", summary)
        self.assertEqual("saved-audience-panel-v3", documents["panel"]["schema_version"])
        self.assertEqual("panel-review-manifest-v1", manifest["schema_version"])
        self.assertIn(f"Panel tier:** `{documents['panel']['panel_tier']}`", summary)
        self.assertIn(documents["panel"]["claim_boundary"], summary)
        self.assertNotIn("approved v2 package", summary.lower())
        if documents["panel"]["panel_tier"] != "tier_1":
            self.assertNotIn("Population composition is not available in Release A", summary)

        def scalar_leaves(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from scalar_leaves(child)
            elif isinstance(value, list):
                for child in value:
                    yield from scalar_leaves(child)
            elif value is not None:
                yield value

        for value in scalar_leaves(documents["panel"]):
            rendered = "Yes" if value is True else "No" if value is False else str(value)
            self.assertIn(rendered, summary)
            self.assertIn(rendered, html)

    def test_provisional_v3_review_keeps_v3_fields_and_honest_status(self):
        documents, _ = self.canonical_v3_report_documents()
        documents["brief"].update(
            status="provisional_no_research",
            research_mode="provisional_no_research",
        )
        documents["panel"]["persona_research"].update(
            mode="provisional_no_research",
            status="provisional_no_research",
            source_state="no_research_sources",
            source_types=[],
            evidence_ids=[],
        )

        summary = render_panel_summary(documents["brief"], documents["panel"])
        html = render_panel_review_html(documents["brief"], documents["panel"])

        def scalar_leaves(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from scalar_leaves(child)
            elif isinstance(value, list):
                for child in value:
                    yield from scalar_leaves(child)
            elif value is not None:
                yield value

        for key in (
            "panel_tier",
            "evidence_basis",
            "claim_boundary",
            "package_status",
            "audit_binding",
            "population_frame",
            "composition_plan",
            "validity_profile",
        ):
            value = documents["panel"].get(key)
            if value is None:
                value = documents["brief"].get(key)
            if value is None:
                continue
            for leaf in scalar_leaves(value):
                rendered = "Yes" if leaf is True else "No" if leaf is False else str(leaf)
                self.assertIn(rendered, summary)
                self.assertIn(rendered, html)
        self.assertIn("provisional planning profile", summary.lower())
        self.assertIn("no audience research report exists", summary.lower())
        self.assertNotIn("approved reusable panel", summary.lower())

    def test_tier_one_v3_report_binds_no_frame_result_and_null_frame_reference(self):
        documents, inputs = self.canonical_v3_report_documents(tier="tier_1")
        self.assertIsNone(inputs["frame_sha256"])
        self.assertEqual(inputs, validate_report_inputs(inputs))

        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "tier-one-v3-report"
            render_research_report(
                report_inputs=inputs,
                documents=documents,
                generated_at="2026-07-24T13:30:00Z",
                output_dir=output_dir,
            )
            manifest = json.loads(
                (output_dir / "audience-research-report-manifest.json").read_text()
            )
        self.assertIn(
            "population-frame.json",
            [entry["path"] for entry in manifest["inputs"]],
        )

        wrong_result = copy.deepcopy(documents)
        wrong_result["validity_profile"]["source_bindings"][
            "frame_result_sha256"
        ] = "sha256:" + HASH
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "source_bindings"):
                render_research_report(
                    report_inputs=inputs,
                    documents=wrong_result,
                    generated_at="2026-07-24T13:30:00Z",
                    output_dir=Path(temp) / "wrong-frame-result",
                )

        nonnull_frame_reference = copy.deepcopy(documents)
        nonnull_frame_reference["validity_profile"]["source_bindings"][
            "frame_sha256"
        ] = sha256_json(documents["population_frame"])
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "frame_sha256"):
                render_research_report(
                    report_inputs=inputs,
                    documents=nonnull_frame_reference,
                    generated_at="2026-07-24T13:30:00Z",
                    output_dir=Path(temp) / "nonnull-frame-reference",
                )

    def test_experimental_result_is_verified_but_has_no_usable_frame_binding(self):
        base = self.documents()
        contracts = v3_contract_tests.AudienceResearchV3ContractTests()
        contracts.v2_brief = copy.deepcopy(base["brief"])
        contracts.v2_panel = copy.deepcopy(base["panel"])
        bundle = list(contracts.v3_pair(tier="tier_1", evidence_basis="none"))
        bundle[2] = contracts.frame(eligibility="experimental")
        bundle[2]["downgrade_reason"] = "modeled-share-above-threshold"
        bundle[3] = contracts.composition(
            frame=bundle[2],
            requested_tier="tier_1",
            evidence_basis="none",
        )
        contracts.rebind_population_documents(bundle)
        contracts.validate_v3_documents(bundle)
        documents, inputs = self.report_documents_from_v3_bundle(bundle)

        self.assertIsNone(inputs["frame_sha256"])
        with tempfile.TemporaryDirectory() as temp:
            render_research_report(
                report_inputs=inputs,
                documents=documents,
                generated_at="2026-07-24T13:30:00Z",
                output_dir=Path(temp) / "experimental-result-report",
            )

    def test_overlay_driven_tier_one_preserves_eligible_frame_and_selection(self):
        base = self.documents()
        contracts = v3_contract_tests.AudienceResearchV3ContractTests()
        contracts.v2_brief = copy.deepcopy(base["brief"])
        contracts.v2_panel = copy.deepcopy(base["panel"])
        bundle = contracts.overlay_downgraded_v3_pair(requested_tier="tier_3")
        contracts.validate_v3_documents(bundle)
        documents, inputs = self.report_documents_from_v3_bundle(bundle)

        self.assertEqual(
            sha256_json(documents["population_frame"]).removeprefix("sha256:"),
            inputs["frame_sha256"],
        )
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "overlay-tier-one-report"
            render_research_report(
                report_inputs=inputs,
                documents=documents,
                generated_at="2026-07-24T13:30:00Z",
                output_dir=output_dir,
            )
            html = (output_dir / "audience-research-report.html").read_text()
        self.assertIn("Requested Tier 3; achieved Tier 1", html)
        self.assertIn("eligible-cohort-members / joint / company-size × role", html)

    def test_cli_renders_v3_only_with_all_population_documents_and_hashes(self):
        documents, inputs = self.v3_report_documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for name, payload in {**documents, "report_inputs": inputs}.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths[name] = path
            output = root / "v3-report"
            command = [
                sys.executable, str(SCRIPTS / "render-research-report.py"),
                "--workflow-state", str(paths["workflow_state"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                "--plan", str(paths["plan"]),
                "--scored-sources", str(paths["scored_sources"]),
                "--ledger", str(paths["evidence_ledger"]),
                "--finding-support", str(paths["finding_support"]),
                "--synthesis", str(paths["synthesis_matrix"]),
                "--report-inputs", str(paths["report_inputs"]),
                "--population-frame", str(paths["population_frame"]),
                "--composition-plan", str(paths["composition_plan"]),
                "--validity-profile", str(paths["validity_profile"]),
                "--generated-at", "2026-07-24T13:30:00Z",
                "--output-dir", str(output),
                *self.panel_review_cli_args(root, documents, label="v3"),
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("rendered", json.loads(completed.stdout)["status"])
            self.assertIn(
                "Public proxy frame only",
                (output / "audience-research-report.html").read_text(),
            )

    def test_inventories_are_deterministic_and_bound_to_sources_and_findings(self):
        documents = self.documents()
        source_inventory = documents["source_inventory"]
        self.assertEqual("Source 1", source_inventory["sources"][0]["provenance_label"])
        self.assertFalse(source_inventory["sources"][0]["provenance_label"].startswith("="))
        verbatim_inventory = documents["verbatim_inventory"]
        self.assertEqual(["finding-implementation-proof"], verbatim_inventory["excerpts"][0]["finding_ids"])
        broken = copy.deepcopy(documents["finding_support"])
        broken["findings"][0]["evidence_item_ids"] = ["unknown-item"]
        with self.assertRaises(ContractError):
            build_verbatim_inventory(
                evidence_ledger=documents["evidence_ledger"], finding_support=broken,
            )

    def test_source_inventory_rejects_cross_source_evidence_substitution(self):
        documents = self.documents()
        second_item = {
            **documents["evidence_ledger"]["evidence_items"][0],
            "evidence_item_id": "evidence-item-2",
            "import_id": "research-import-two",
            "source_url": "https://example.test/other-research",
            "content_sha256": "sha256:" + "3" * 64,
            "upstream_source_ids": ["study-2"],
        }
        documents["evidence_ledger"]["imports"].append({
            **documents["evidence_ledger"]["imports"][0],
            "import_id": "research-import-two",
            "input_sha256": "sha256:" + "4" * 64,
        })
        documents["evidence_ledger"]["evidence_items"].append(second_item)
        documents["evidence_ledger"]["summary"]["imports"] = 2
        documents["evidence_ledger"]["summary"]["accepted_items"] = 2
        candidate = copy.deepcopy(documents["scored_sources"]["candidates"][0])
        for key in ("score", "tier", "decision", "decision_reasons"):
            del candidate[key]
        candidate["evidence_item_ids"] = ["evidence-item-2"]
        documents["scored_sources"] = score_source_candidates({
            "schema_version": "audience-source-candidates-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z",
            "candidates": [candidate],
        })
        with self.assertRaisesRegex(ContractError, "canonical upstream identity"):
            build_source_inventory(
                scored_sources=documents["scored_sources"],
                evidence_ledger=documents["evidence_ledger"],
            )

    def test_source_inventory_matches_exact_upstream_identity_sets_and_urls(self):
        documents = self.documents()
        survey_item = documents["evidence_ledger"]["evidence_items"][0]
        survey_item["upstream_source_ids"] = ["survey-study-1"]
        survey_candidate = copy.deepcopy(documents["scored_sources"]["candidates"][0])
        for key in ("score", "tier", "decision", "decision_reasons"):
            del survey_candidate[key]
        survey_candidate["upstream_source_ids"] = ["survey-study-1"]
        survey = score_source_candidates({
            "schema_version": "audience-source-candidates-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z",
            "candidates": [survey_candidate],
        })
        self.assertEqual(
            "Source 1",
            build_source_inventory(scored_sources=survey, evidence_ledger=documents["evidence_ledger"])["sources"][0]["provenance_label"],
        )
        social_item = copy.deepcopy(survey_item)
        social_item["upstream_source_ids"] = []
        social_item["item_type"] = "social_discovery_result"
        social_ledger = copy.deepcopy(documents["evidence_ledger"])
        social_ledger["evidence_items"] = [social_item]
        social_candidate = copy.deepcopy(survey_candidate)
        social_candidate.update({
            "candidate_id": "social-source-1",
            "source_family_id": "social-source",
            "lane": "social_community",
            "upstream_source_ids": [],
            "social_collection": {
                "platform": "community", "query": "implementation proof",
                "window_start": "2026-07-01T00:00:00Z", "window_end": "2026-07-23T00:00:00Z",
                "timezone": "UTC", "unit_of_analysis": "post", "sort_mode": "recent",
                "item_limit": 10, "pagination": "none", "returned_item_count": 1,
                "completeness": "bounded", "collector": "fixture", "collector_version": "1.0",
                "run_or_dataset_id": "social-run-1", "deduplication_control": "content hash",
                "bot_spam_control": "manual review", "engagement_available": False,
            },
        })
        social = score_source_candidates({
            "schema_version": "audience-source-candidates-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z",
            "candidates": [social_candidate],
        })
        self.assertEqual(
            "social-source-1",
            build_source_inventory(scored_sources=social, evidence_ledger=social_ledger)["sources"][0]["source_id"],
        )
        different_identity = copy.deepcopy(survey_candidate)
        different_identity["upstream_source_ids"] = ["other-study"]
        mismatch_identity = score_source_candidates({
            "schema_version": "audience-source-candidates-v1", "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z", "candidates": [different_identity],
        })
        with self.assertRaisesRegex(ContractError, "canonical upstream identity"):
            build_source_inventory(scored_sources=mismatch_identity, evidence_ledger=documents["evidence_ledger"])
        different_url = copy.deepcopy(survey_candidate)
        different_url["source_url"] = "https://example.test/other-research"
        mismatch_url = score_source_candidates({
            "schema_version": "audience-source-candidates-v1", "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-23T12:00:00Z", "candidates": [different_url],
        })
        with self.assertRaisesRegex(ContractError, "canonical upstream identity"):
            build_source_inventory(scored_sources=mismatch_url, evidence_ledger=documents["evidence_ledger"])

    def test_report_reconciles_current_workflow_bindings_and_approved_scopes(self):
        documents = self.documents()
        stale = copy.deepcopy(documents)
        stale["workflow_state"]["bindings"]["brief_sha256"] = HASH
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "bindings.brief_sha256"):
                render_research_report(
                    report_inputs=self.report_inputs(stale), documents=stale,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "stale",
                )
        draft = copy.deepcopy(documents)
        draft["workflow_state"]["state"] = "draft"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "draft"):
                render_research_report(
                    report_inputs=self.report_inputs(draft), documents=draft,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "draft",
                )
        unapproved = copy.deepcopy(documents)
        unapproved["workflow_state"]["state"] = "approved"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "evidence_synthesis"):
                render_research_report(
                    report_inputs=self.report_inputs(unapproved), documents=unapproved,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "unapproved",
                )
        approved = copy.deepcopy(documents)
        self.approved_workflow_state(approved)
        with tempfile.TemporaryDirectory() as temp:
            render_research_report(
                report_inputs=self.report_inputs(approved), documents=approved,
                generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "approved",
            )
            self.assertIn("APPROVED", (Path(temp) / "approved" / "audience-research-report.html").read_text())

    def test_render_requires_all_canonical_evidence_documents_and_matching_hashes(self):
        documents = self.documents()
        inputs = self.report_inputs(documents)
        missing = copy.deepcopy(documents)
        del missing["synthesis_matrix"]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "missing canonical documents"):
                render_research_report(
                    report_inputs=inputs, documents=missing,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "missing",
                )
        mismatched = copy.deepcopy(inputs)
        mismatched["evidence_ledger_sha256"] = HASH
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "does not match"):
                render_research_report(
                    report_inputs=mismatched, documents=documents,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "mismatch",
                )

    def test_render_is_escaped_self_contained_deterministic_and_no_clobber(self):
        documents = self.documents()
        documents["synthesis_matrix"]["questions"][0]["findings"][0]["statement"] = "<script>bad()</script> & <em>unsafe</em>"
        documents["synthesis_matrix"]["questions"][0]["findings"][0]["inference_boundary"] = "javascript:alert(1)"
        inputs = self.report_inputs(documents)
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            first_result = render_research_report(
                report_inputs=inputs, documents=documents,
                generated_at="2026-07-23T12:30:00Z", output_dir=first,
            )
            second_result = render_research_report(
                report_inputs=inputs, documents=documents,
                generated_at="2026-07-23T12:30:00Z", output_dir=second,
            )
            self.assertEqual(REPORT_MANIFEST_SCHEMA_VERSION, first_result["schema_version"])
            self.assertEqual(first_result, second_result)
            html = (first / "audience-research-report.html").read_text()
            self.assertIn("DOGFOOD", html)
            self.assertIn("Population validity is unavailable", html)
            self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", html)
            self.assertNotIn("<script", html)
            self.assertNotIn('href="javascript:', html)
            self.assertNotIn("<iframe", html)
            self.assertNotIn("<form", html)
            self.assertNotIn("http://", html.replace("https://example.test/research", ""))
            with self.assertRaisesRegex(ContractError, "already exists"):
                render_research_report(
                    report_inputs=inputs, documents=documents,
                    generated_at="2026-07-23T12:30:00Z", output_dir=first,
                )

    def test_readable_findings_preserve_confidence_boundaries_and_support_roles(self):
        documents = self.documents()
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "report"
            render_research_report(
                report_inputs=self.report_inputs(documents), documents=documents,
                generated_at="2026-07-23T12:30:00Z", output_dir=output_dir,
            )
            html = (output_dir / "audience-research-report.html").read_text()
        for value in (
            "One decision-relevant documented source.",
            "Directional finding only; not population prevalence.",
            "minor concerns",
            "Supports",
            "Qualifies",
            "Contradicts",
            "evidence-item-1",
        ):
            self.assertIn(value, html)

    def test_unsafe_citation_schemes_and_noncanonical_inventories_are_rejected(self):
        documents = self.documents()
        documents["source_inventory"]["sources"][0]["source_url"] = "javascript:alert(1)"
        inputs = self.report_inputs(documents)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "HTTP or HTTPS"):
                render_research_report(
                    report_inputs=inputs, documents=documents,
                    generated_at="2026-07-23T12:30:00Z", output_dir=Path(temp) / "unsafe",
                )

    def test_cli_returns_canonical_json_and_distinguishes_output_collision(self):
        documents = self.documents()
        inputs = self.report_inputs(documents)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for name, payload in {**documents, "report_inputs": inputs}.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths[name] = path
            command = [
                sys.executable, str(SCRIPTS / "render-research-report.py"),
                "--workflow-state", str(paths["workflow_state"]), "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]), "--plan", str(paths["plan"]),
                "--scored-sources", str(paths["scored_sources"]), "--ledger", str(paths["evidence_ledger"]),
                "--finding-support", str(paths["finding_support"]), "--synthesis", str(paths["synthesis_matrix"]),
                "--generated-at", "2026-07-23T12:30:00Z",
                "--output-dir", str(root / "report"),
                *self.panel_review_cli_args(root, documents, label="v2"),
            ]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual("rendered", json.loads(first.stdout)["status"])
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(3, second.returncode)
            self.assertEqual("output_collision", json.loads(second.stdout)["error"])
            invalid_inputs = copy.deepcopy(inputs)
            invalid_inputs["frame_sha256"] = HASH
            paths["report_inputs"].write_text(json.dumps(invalid_inputs), encoding="utf-8")
            frame_command = [*command, "--report-inputs", str(paths["report_inputs"]), "--output-dir", str(root / "frame-report")]
            invalid = subprocess.run(frame_command, capture_output=True, text=True, check=False)
            self.assertEqual(2, invalid.returncode)
            self.assertEqual("validation", json.loads(invalid.stdout)["error"])

    def test_cli_malformed_panel_without_report_inputs_returns_one_json_error(self):
        documents = self.documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for name, payload in documents.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths[name] = path
            paths["panel"].write_text("{}", encoding="utf-8")
            command = [
                sys.executable, str(SCRIPTS / "render-research-report.py"),
                "--workflow-state", str(paths["workflow_state"]), "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]), "--plan", str(paths["plan"]),
                "--scored-sources", str(paths["scored_sources"]), "--ledger", str(paths["evidence_ledger"]),
                "--finding-support", str(paths["finding_support"]), "--synthesis", str(paths["synthesis_matrix"]),
                "--generated-at", "2026-07-23T12:30:00Z", "--output-dir", str(root / "report"),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertEqual("validation", json.loads(result.stdout)["error"])

    def test_reporting_module_imports_with_only_panel_builder_scripts_on_path(self):
        command = [
            sys.executable, "-I", "-c",
            f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import audience_panel_builder.reporting",
        ]
        result = subprocess.run(command, cwd=tempfile.gettempdir(), capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
