from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "audience-panel-builder"
SCRIPTS = SKILL / "scripts"
LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(LAB_SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.capabilities import validate_capability_inventory  # noqa: E402
from audience_panel_builder.evidence import (  # noqa: E402
    build_evidence_ledger,
    validate_finding_support,
)
from audience_panel_builder.planning import (  # noqa: E402
    build_source_plan,
    validate_research_intake,
)
from audience_panel_builder.review import (  # noqa: E402
    audit_evidence_specificity,
    build_source_link_overrides,
    count_panel_entities,
    render_panel_review_html,
    render_panel_summary,
    render_validation_report,
    validate_panel_review_manifest,
)
from audience_lab.audience_library import _provisional_documents  # noqa: E402
from audience_lab.audience_research import validate_research_brief  # noqa: E402
from audience_panel_builder.social import (  # noqa: E402
    normalize_last30days,
    normalize_mapped_export,
)
from audience_panel_builder.source_scoring import score_source_candidates  # noqa: E402
from audience_panel_builder.synthesis import validate_synthesis_matrix  # noqa: E402


class AudiencePanelBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(
            (SKILL / "references" / "source-registry.json").read_text(encoding="utf-8")
        )

    def intake(self):
        return {
            "schema_version": "audience-panel-research-intake-v1",
            "research_id": "operations-leaders",
            "created_at": "2026-07-23T12:00:00Z",
            "workflow_route": "create_research_backed_panel",
            "target_audience": {
                "audience": "Operations leaders at mid-market software companies",
                "category": "Workflow software",
                "market": "B2B software",
                "geography": "United States",
                "buying_context": "Evaluating a replacement platform",
                "exclusions": ["Individual contributors without purchase influence"],
            },
            "audience_type": "b2b",
            "research_depth": "standard",
            "decision_to_support": "Build a reusable panel for ad screening.",
            "available_inputs": ["last30days_json"],
            "requested_or_supplied_connectors": [
                "sprinklr", "last30days_json_import"
            ],
            "current_language_evidence": "required",
            "languages": ["en"],
            "as_of": "2026-07-23",
            "existing_panel": None,
        }

    def capabilities(self, capabilities=None, provider="Sprinklr"):
        return {
            "schema_version": "connector-capability-inventory-v1",
            "detected_at": "2026-07-23T12:00:00Z",
            "runtime": "codex",
            "connectors": [
                {
                    "connector_id": "social-connector",
                    "provider": provider,
                    "server_or_tool": "mcp-social",
                    "detection_method": "schema inspection and read-only preflight",
                    "status": "available_verified",
                    "schema_fingerprint": "sha256:" + "1" * 64,
                    "capabilities": capabilities
                    if capabilities is not None
                    else ["query_saved_listening_topics", "filter_by_date", "paginate"],
                    "scope": "approved account",
                    "constraints": "saved topics only",
                    "privacy_risk": "public and owned posts",
                }
            ],
            "unresolved_capabilities": [],
        }

    def test_source_plan_combines_structural_survey_and_social_lanes(self):
        plan = build_source_plan(
            self.intake(), self.registry, self.capabilities()
        )
        self.assertEqual("audience-source-plan-v1", plan["schema_version"])
        lanes = {item["lane"] for item in plan["selected_source_families"]}
        self.assertTrue({"structural", "survey", "social_community"}.issubset(lanes))
        social_ids = {
            item["source_family_id"]
            for item in plan["selected_source_families"]
            if item["lane"] == "social_community"
        }
        self.assertEqual(
            {"social-listening-mcp", "last30days-social-discovery"}, social_ids
        )
        self.assertEqual(0, plan["social_collection"]["prevalence_weight"])
        self.assertFalse(
            any(item["lane"] == "social_community" for item in plan["unresolved_requirements"])
        )

    def test_source_plan_flags_missing_social_route_instead_of_inventing_one(self):
        intake = self.intake()
        intake["available_inputs"] = []
        intake["requested_or_supplied_connectors"] = ["none"]
        plan = build_source_plan(
            intake, self.registry, self.capabilities([])
        )
        gap = next(
            item
            for item in plan["unresolved_requirements"]
            if item["lane"] == "social_community"
        )
        self.assertEqual(0, gap["selected_source_families"])
        self.assertTrue(gap["required"])
        self.assertNotIn(
            "social_community",
            {
                item["lane"] for item in plan["selected_source_families"]
            },
        )

    def test_source_plan_flags_registry_review_age(self):
        intake = self.intake()
        intake["created_at"] = "2027-01-23T12:00:00Z"
        registry = copy.deepcopy(self.registry)
        registry["updated_at"] = "2026-01-01T12:00:00Z"
        plan = build_source_plan(intake, registry, self.capabilities())
        self.assertEqual("review_due", plan["registry_freshness"]["status"])
        self.assertGreater(plan["registry_freshness"]["age_days"], 120)
        self.assertIn("Verify every selected", plan["registry_freshness"]["warning"])

    def test_authorized_import_is_a_data_lab_handoff_plan_not_collection(self):
        intake = self.intake()
        intake["workflow_route"] = "import_authorized_audience"
        intake["available_inputs"] = []
        intake["requested_or_supplied_connectors"] = ["none"]
        intake["current_language_evidence"] = "not_applicable"

        self.assertEqual(
            "import_authorized_audience",
            validate_research_intake(intake)["workflow_route"],
        )
        plan = build_source_plan(intake, self.registry, self.capabilities([]))

        self.assertEqual("import_authorized_audience", plan["workflow_route"])
        self.assertEqual("none", plan["evidence_basis"])
        self.assertEqual([], plan["selected_source_families"])
        self.assertEqual(
            [
                {
                    "lane": "first_party",
                    "minimum_sources": 1,
                    "required": True,
                    "reason": (
                        "Require Audience Data Lab privacy profiling, exact "
                        "mapping approval, deterministic transformation, and "
                        "a validated aggregate authorized-audience-handoff-v1."
                    ),
                }
            ],
            plan["lane_requirements"],
        )
        self.assertEqual(
            [
                {
                    "lane": "first_party",
                    "required": True,
                    "minimum_sources": 1,
                    "selected_source_families": 0,
                    "resolution": (
                        "Send arbitrary authorized files to Audience Data Lab. "
                        "Panel Builder may continue only from its validated "
                        "aggregate authorized-audience-handoff-v1 after privacy "
                        "profiling, mapping approval, and deterministic "
                        "transformation."
                    ),
                }
            ],
            plan["unresolved_requirements"],
        )
        self.assertIn(
            "This plan does not inspect, transform, or execute against supplied files.",
            plan["evidence_acceptance"],
        )

    def test_workflow_route_enum_is_closed_to_exactly_six_values(self):
        existing = {
            "panel_id": "operations-leaders",
            "version": "1.0.0",
            "package_sha256": "sha256:" + "a" * 64,
        }
        routes = {
            "create_research_backed_panel",
            "import_authorized_audience",
            "refresh_existing_panel",
            "augment_existing_panel",
            "audit_existing_panel",
            "provisional_immediate_panel",
        }
        for route in sorted(routes):
            with self.subTest(route=route):
                intake = self.intake()
                intake["workflow_route"] = route
                if route in {
                    "refresh_existing_panel",
                    "augment_existing_panel",
                    "audit_existing_panel",
                }:
                    intake["existing_panel"] = existing
                if route == "provisional_immediate_panel":
                    intake["available_inputs"] = []
                self.assertEqual(
                    route,
                    validate_research_intake(intake)["workflow_route"],
                )

        intake = self.intake()
        intake["workflow_route"] = "migrate_v2_to_v3"
        with self.assertRaisesRegex(ContractError, r"\$\.workflow_route"):
            validate_research_intake(intake)

    def test_authorized_import_ignores_generic_performance_lane_augmentation(self):
        intake = self.intake()
        intake["workflow_route"] = "import_authorized_audience"
        intake["available_inputs"] = ["performance_evidence_package"]

        plan = build_source_plan(intake, self.registry, self.capabilities())

        self.assertEqual("none", plan["evidence_basis"])
        self.assertEqual("absent", plan["performance_context"])
        self.assertEqual([], plan["selected_source_families"])
        self.assertEqual(
            ["first_party"],
            [requirement["lane"] for requirement in plan["lane_requirements"]],
        )
        self.assertEqual(
            ["first_party"],
            [
                requirement["lane"]
                for requirement in plan["unresolved_requirements"]
            ],
        )

    def test_provider_name_does_not_imply_listening_capability(self):
        plan = build_source_plan(
            self.intake(),
            self.registry,
            self.capabilities(["read_owned_analytics"], provider="Sprout"),
        )
        social_ids = {
            item["source_family_id"]
            for item in plan["selected_source_families"]
            if item["lane"] == "social_community"
        }
        self.assertNotIn("social-listening-mcp", social_ids)
        self.assertIn("last30days-social-discovery", social_ids)

    def test_capability_inventory_rejects_write_capabilities(self):
        inventory = self.capabilities(["read_owned_posts"])
        inventory["connectors"][0]["capabilities"] = ["publish_post"]
        with self.assertRaises(ContractError):
            validate_capability_inventory(inventory)

    def last30days(self):
        return {
            "schema_version": "1.2",
            "query": "operations leaders workflow replacement",
            "generated_at": "2026-07-23T12:00:00Z",
            "window_days": 30,
            "source_status": {
                "reddit": "ok",
                "linkedin": "rate-limited",
            },
            "freshness_verdicts": [
                {"candidate_id": "reddit-1", "verdict": "current"}
            ],
            "clusters": [
                {
                    "title": "Implementation risk",
                    "summary": "Leaders ask for migration proof.",
                    "sources": ["reddit"],
                    "engagement_total": 120,
                }
            ],
            "results": [
                {
                    "candidate_id": "reddit-1",
                    "title": "How did you replace the old workflow?",
                    "source": "reddit",
                    "url": "https://www.reddit.com/r/operations/example",
                    "published_at": "2026-07-20T10:00:00Z",
                    "summary": "@operator wants migration proof before switching. Contact test@example.com.",
                    "engagement": {"score": 112, "num_comments": 8},
                    "relevance_score": 0.91,
                    "cluster": 0,
                }
            ],
        }

    def test_last30days_normalization_is_versioned_private_and_non_prevalence(self):
        result = normalize_last30days(self.last30days())
        self.assertEqual("social-observation-batch-v1", result["schema_version"])
        self.assertEqual(
            [
                "linkedin: rate-limited",
                "author concentration could not be assessed from this export",
            ],
            result["coverage_warnings"],
        )
        observation = result["observations"][0]
        self.assertEqual("provider_summary", observation["text_fidelity"])
        self.assertIn("[redacted handle]", observation["text_excerpt"])
        self.assertIn("[redacted email]", observation["text_excerpt"])
        self.assertEqual(0, observation["engagement"]["prevalence_weight"])
        self.assertIn("verify_original_source_before_quote", observation["use_constraints"])
        self.assertEqual("current", observation["freshness_verdict"])

    def test_last30days_rejects_raw_comparison_and_unknown_major(self):
        payload = self.last30days()
        payload["schema_version"] = "2.0"
        with self.assertRaises(ContractError):
            normalize_last30days(payload)
        payload = self.last30days()
        payload["comparison"] = True
        with self.assertRaises(ContractError):
            normalize_last30days(payload)
        with self.assertRaises(ContractError):
            normalize_last30days({"query": "raw internal report"})

    def structured_evidence_batch(self):
        return {
            "schema_version": "audience-structured-evidence-batch-v1",
            "batch_id": "cmo-survey-2026",
            "created_at": "2026-07-23T12:00:00Z",
            "source_adapter": "direct-document-research",
            "source_schema_version": "1.0",
            "input_sha256": "sha256:" + "2" * 64,
            "permission": "allowed",
            "source_status": "retrieved_and_verified",
            "items": [
                {
                    "evidence_item_id": "evidence-survey-1",
                    "source_url": "https://example.com/survey",
                    "item_type": "survey_finding",
                    "content_summary": "Operations leaders prioritize implementation proof.",
                    "text_fidelity": "analyst_summary",
                    "content_sha256": "sha256:" + "3" * 64,
                    "source_pointer": "page-12",
                    "upstream_source_ids": ["survey-study-1"],
                    "use_constraints": ["named_population_only"],
                    "quality_flags": [],
                }
            ],
        }

    def test_evidence_ledger_preserves_item_level_support(self):
        social = normalize_last30days(self.last30days())
        ledger = build_evidence_ledger(
            "operations-leaders-source-plan",
            [social, self.structured_evidence_batch()],
            created_at="2026-07-23T13:00:00Z",
        )
        item_ids = {
            item["evidence_item_id"] for item in ledger["evidence_items"]
        }
        self.assertIn("evidence-survey-1", item_ids)
        social_item = next(
            item_id for item_id in item_ids if item_id.startswith("observation-")
        )
        support = {
            "schema_version": "audience-finding-support-v1",
            "created_at": "2026-07-23T13:30:00Z",
            "ledger_sha256": sha256_json(ledger),
            "findings": [
                {
                    "finding_id": "finding-implementation-proof",
                    "evidence_id": "survey-evidence",
                    "evidence_item_ids": ["evidence-survey-1", social_item],
                    "support_role": "supports",
                    "analyst_note": "Survey support plus current-language context.",
                }
            ],
        }
        validate_finding_support(support, ledger)
        support["findings"][0]["evidence_item_ids"].append("missing-item")
        with self.assertRaises(ContractError):
            validate_finding_support(support, ledger)

    def synthesis_inputs(self, *, social_only=False):
        social = normalize_last30days(self.last30days())
        batches = [social]
        if not social_only:
            batches.append(self.structured_evidence_batch())
        ledger = build_evidence_ledger(
            "operations-leaders-source-plan",
            batches,
            created_at="2026-07-23T13:00:00Z",
        )
        social_item = next(
            item["evidence_item_id"]
            for item in ledger["evidence_items"]
            if item["item_type"].startswith("social_")
        )
        evidence_ids = [social_item] if social_only else [
            "evidence-survey-1",
            social_item,
        ]
        support = {
            "schema_version": "audience-finding-support-v1",
            "created_at": "2026-07-23T13:30:00Z",
            "ledger_sha256": sha256_json(ledger),
            "findings": [
                {
                    "finding_id": "finding-implementation-proof",
                    "evidence_id": "survey-evidence",
                    "evidence_item_ids": evidence_ids,
                    "support_role": "supports",
                    "analyst_note": "Direct support and current language.",
                }
            ],
        }
        matrix = {
            "schema_version": "audience-synthesis-matrix-v1",
            "plan_id": "operations-leaders-source-plan",
            "created_at": "2026-07-23T14:00:00Z",
            "ledger_sha256": sha256_json(ledger),
            "questions": [
                {
                    "question_id": "question-proof",
                    "research_question": "What proof reduces perceived implementation risk?",
                    "findings": [
                        {
                            "finding_id": "finding-implementation-proof",
                            "statement": "Implementation evidence reduces perceived switching risk.",
                            "category": "proof_needs",
                            "evidence_item_ids": evidence_ids,
                            "supporting_item_ids": evidence_ids,
                            "qualifying_item_ids": [],
                            "contradicting_item_ids": [],
                            "integration_state": (
                                "single_source" if social_only else "complementary"
                            ),
                            "methodological_limitations": "minor_concerns",
                            "relevance": "no_serious_concerns",
                            "coherence": "no_serious_concerns",
                            "adequacy": (
                                "major_concerns"
                                if social_only
                                else "minor_concerns"
                            ),
                            "confidence": "low" if social_only else "medium",
                            "confidence_reason": "Directly matched but bounded evidence.",
                            "inference_boundary": "Named audience and buying decision only.",
                            "marketer_implication": "Make implementation proof easy to inspect.",
                            "creative_implications": [
                                "Show migration steps and credible customer proof."
                            ],
                            "segment_decision": "not_segment_relevant",
                        }
                    ],
                }
            ],
        }
        return ledger, support, matrix

    def test_synthesis_matrix_links_roles_and_preserves_confidence_rules(self):
        ledger, support, matrix = self.synthesis_inputs()
        validate_synthesis_matrix(matrix, ledger, support)

        mismatch = copy.deepcopy(matrix)
        mismatch["questions"][0]["findings"][0]["supporting_item_ids"] = [
            "evidence-survey-1"
        ]
        with self.assertRaises(ContractError):
            validate_synthesis_matrix(mismatch, ledger, support)

        social_ledger, social_support, social_matrix = self.synthesis_inputs(
            social_only=True
        )
        social_matrix["questions"][0]["findings"][0]["confidence"] = "high"
        social_matrix["questions"][0]["findings"][0][
            "methodological_limitations"
        ] = "no_serious_concerns"
        social_matrix["questions"][0]["findings"][0][
            "adequacy"
        ] = "no_serious_concerns"
        with self.assertRaises(ContractError):
            validate_synthesis_matrix(
                social_matrix,
                social_ledger,
                social_support,
            )

    def mapping(self):
        return {
            "schema_version": "social-export-mapping-v1",
            "batch": {
                "batch_id": "sprinklr-workflow-july",
                "created_at": "2026-07-23T12:00:00Z",
                "provider": "Sprinklr",
                "collector": "social-listening-mcp",
                "collector_version": "2026-07",
                "run_or_dataset_id": "listening-run-123",
                "query": "workflow replacement implementation risk",
                "window_start": "2026-06-23T00:00:00Z",
                "window_end": "2026-07-23T00:00:00Z",
                "collection_method": "authenticated_mcp_export",
                "access_route": "permissioned_client_connector",
                "permitted_use": "audience_research",
                "sort_mode": "newest",
                "item_limit": 200,
                "pagination": "complete_to_limit",
                "completeness": "bounded_by_saved_query",
                "deduplication_control": "provider and canonical URL",
                "bot_spam_control": "provider classification plus manual review",
            },
            "records_path": "items",
            "fields": {
                "source_item_id": "id",
                "platform": "network",
                "source_url": "url",
                "published_at": "publishedAt",
                "unit_of_analysis": "type",
                "title": "title",
                "text": "body",
                "relevance_score": "relevance",
                "cluster_id": "theme",
                "role_status": None,
                "author_id": "author.id",
                "engagement": {
                    "likes": "metrics.likes",
                    "comments": "metrics.comments",
                },
            },
            "constants": {
                "platform": "linkedin",
                "unit_of_analysis": "post",
                "role_status": "unknown",
                "text_fidelity": "verbatim_public_text",
            },
        }

    def mapped_payload(self):
        return {
            "items": [
                {
                    "id": "post-1",
                    "network": "linkedin",
                    "url": "https://www.linkedin.com/posts/example",
                    "publishedAt": "2026-07-20T10:00:00Z",
                    "type": "post",
                    "title": "Implementation proof",
                    "body": "@buyer asked how long migration takes.",
                    "relevance": 0.88,
                    "theme": "migration-risk",
                    "author": {"id": "person-123"},
                    "metrics": {"likes": 40, "comments": 7},
                }
            ]
        }

    def test_mapped_mcp_or_apify_export_uses_one_connector_neutral_contract(self):
        result = normalize_mapped_export(self.mapped_payload(), self.mapping())
        self.assertEqual("mapped-social-export", result["source_adapter"])
        self.assertEqual("Sprinklr", result["collection"]["provider"])
        self.assertEqual(0, result["observations"][0]["engagement"]["prevalence_weight"])
        self.assertEqual("verbatim_public_text", result["observations"][0]["text_fidelity"])
        self.assertNotIn("@buyer", result["observations"][0]["text_excerpt"])
        self.assertTrue(
            result["observations"][0]["author_group_token"].startswith(
                "author-group-"
            )
        )
        self.assertNotIn(
            "person-123", json.dumps(result, sort_keys=True)
        )
        self.assertEqual([], result["coverage_warnings"])

    def candidates(self):
        social = {
            "platform": "linkedin",
            "query": "workflow replacement",
            "window_start": "2026-06-23T00:00:00Z",
            "window_end": "2026-07-23T00:00:00Z",
            "timezone": "UTC",
            "unit_of_analysis": "post",
            "sort_mode": "newest",
            "item_limit": 200,
            "pagination": "complete_to_limit",
            "returned_item_count": 82,
            "completeness": "bounded_by_query",
            "collector": "social-listening-mcp",
            "collector_version": "2026-07",
            "run_or_dataset_id": "run-123",
            "deduplication_control": "canonical URL",
            "bot_spam_control": "provider plus manual",
            "engagement_available": True,
        }
        base_assessments = {
            "audience_match": "exact",
            "decision_match": "exact",
            "methodology_transparency": "documented",
            "collection_quality": "qualitative_curated",
            "recency": "current",
            "geography_match": "exact",
            "subgroup_usefulness": "limited",
            "permitted_use": "allowed",
        }
        return {
            "schema_version": "audience-source-candidates-v1",
            "plan_id": "operations-leaders-source-plan",
            "created_at": "2026-07-23T12:00:00Z",
            "candidates": [
                {
                    "candidate_id": "candidate-social",
                    "source_family_id": "social-listening-mcp",
                    "lane": "social_community",
                    "title": "Workflow implementation discussion",
                    "publisher": "Permissioned listening corpus",
                    "source_url": "https://example.com/listening/workflow",
                    "methodology_url": "https://example.com/listening/method",
                    "publication_date": "2026-07-23",
                    "field_dates": "2026-06-23 to 2026-07-23",
                    "population": "Captured public professional posts",
                    "geography": "United States",
                    "sample_size": 82,
                    "collection_method": "authenticated_mcp_export",
                    "access_route": "permissioned_client_connector",
                    "reuse_status": "allowed",
                    "assessments": base_assessments,
                    "social_collection": social,
                    "upstream_source_ids": [],
                    "evidence_item_ids": ["evidence-social-1"],
                    "notes": "Qualitative use only.",
                },
                {
                    "candidate_id": "candidate-survey",
                    "source_family_id": "cmo-survey",
                    "lane": "survey",
                    "title": "Marketing operations survey",
                    "publisher": "Example Research",
                    "source_url": "https://example.com/survey",
                    "methodology_url": "https://example.com/survey/method",
                    "publication_date": "2026-07-01",
                    "field_dates": "2026-04-01 to 2026-05-01",
                    "population": "U.S. marketing operations leaders",
                    "geography": "United States",
                    "sample_size": 1000,
                    "collection_method": "weighted survey",
                    "access_route": "public report",
                    "reuse_status": "allowed",
                    "assessments": {
                        **base_assessments,
                        "methodology_transparency": "transparent",
                        "collection_quality": "probability_or_census",
                        "subgroup_usefulness": "direct",
                    },
                    "social_collection": None,
                    "upstream_source_ids": ["survey-study-1"],
                    "evidence_item_ids": ["evidence-survey-1"],
                    "notes": "Direct role cut.",
                },
            ],
        }

    def test_source_scoring_keeps_social_qualitative_and_permissions_hard(self):
        scored = score_source_candidates(self.candidates())
        by_id = {item["candidate_id"]: item for item in scored["candidates"]}
        self.assertEqual("accept_as_qualitative", by_id["candidate-social"]["decision"])
        self.assertIn("prevalence weight is zero", " ".join(by_id["candidate-social"]["decision_reasons"]))
        self.assertEqual("accept", by_id["candidate-survey"]["decision"])

        candidates = self.candidates()
        candidates["candidates"][0]["reuse_status"] = "unknown"
        scored = score_source_candidates(candidates)
        social = next(item for item in scored["candidates"] if item["candidate_id"] == "candidate-social")
        self.assertEqual("reject", social["decision"])

    def test_source_scoring_rejects_dependent_duplicate_evidence(self):
        candidates = self.candidates()
        duplicate = copy.deepcopy(candidates["candidates"][1])
        duplicate["candidate_id"] = "candidate-survey-summary"
        duplicate["source_url"] = "https://example.com/survey-summary"
        duplicate["assessments"]["methodology_transparency"] = "documented"
        candidates["candidates"].append(duplicate)
        scored = score_source_candidates(candidates)
        summary = next(
            item for item in scored["candidates"]
            if item["candidate_id"] == "candidate-survey-summary"
        )
        self.assertEqual("reject", summary["decision"])
        self.assertIn("Duplicate or dependent", " ".join(summary["decision_reasons"]))

    def test_cli_planner_emits_one_json_status_and_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            intake_path = root / "intake.json"
            capabilities_path = root / "capabilities.json"
            output = root / "plan.json"
            intake_path.write_text(json.dumps(self.intake()), encoding="utf-8")
            capabilities_path.write_text(
                json.dumps(self.capabilities()), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "plan-research.py"),
                    str(intake_path),
                    str(output),
                    "--capabilities",
                    str(capabilities_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("planned", json.loads(completed.stdout)["status"])
            self.assertEqual("audience-source-plan-v1", json.loads(output.read_text())["schema_version"])
            repeated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "plan-research.py"),
                    str(intake_path),
                    str(output),
                    "--capabilities",
                    str(capabilities_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, repeated.returncode)
            self.assertIn(
                "never overwritten",
                json.loads(repeated.stdout)["message"],
            )

    def test_panel_review_is_marketer_readable_and_separates_profiles(self):
        brief = (
            ROOT
            / "conformance"
            / "fixtures"
            / "audience-research"
            / "approved-brief.json"
        )
        panel = (
            ROOT
            / "conformance"
            / "fixtures"
            / "audience-research"
            / "approved-panel.json"
        )
        canonical_panel = json.loads(panel.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "review"
            run_plan = Path(temp) / "run-plan.json"
            run_results = Path(temp) / "run-results.json"
            run_plan.write_text(
                json.dumps(
                    {"synthetic_replicate_capacity": {"required_total": 1}}
                ),
                encoding="utf-8",
            )
            run_results.write_text(
                json.dumps(
                    {
                        "usage": {
                            "unique_job_slots_planned": 1,
                            "unique_job_slots_dispatched": 0,
                            "accepted_response_records": 0,
                            "rejected_attempts": 0,
                            "total_model_calls": 0,
                        },
                        "raw_provider_returns": [],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "render-panel-review.py"),
                    "--brief",
                    str(brief),
                    "--panel",
                    str(panel),
                    "--output-dir",
                    str(output),
                    "--run-plan",
                    str(run_plan),
                    "--run-results",
                    str(run_results),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            summary = (output / "panel-summary.md").read_text(encoding="utf-8")
            validation = (output / "validation-report.md").read_text(
                encoding="utf-8"
            )
            html = (output / "audience-panel-review.html").read_text(
                encoding="utf-8"
            )
            manifest = json.loads(
                (output / "panel-review-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            approval = (
                output / "panel-construction-approval-request.md"
            ).read_text(encoding="utf-8")
            canonical_copy = (output / "saved-audience-panel.json").read_bytes()
        self.assertIn("1 reusable grounded profile", summary)
        self.assertIn("run-specific synthetic panelists", summary)
        self.assertIn("Role and industry context", summary)
        for label in (
            "Audience groups", "Mindsets", "Buying situations", "Reusable profiles",
            "Requested/planned unique synthetic panelists (job slots)", "Response jobs",
            "Accepted response records", "Retries", "Rejected provider returns", "Model calls",
        ):
            self.assertIn(label, summary)
            self.assertIn(label, validation)
        for phrase in (
            "Directional creative hypothesis stress test.",
            "Tier 1 evidence-grounded panel",
            "Population composition not available in Release A.",
        ):
            self.assertIn(phrase, summary)
            self.assertIn(phrase, validation)
        self.assertNotIn("not a customer survey or a human sample", summary)
        self.assertNotIn("not a representative human sample", summary)
        self.assertNotIn("not a customer survey or a human sample", validation)
        self.assertNotIn("not a representative human sample", validation)
        self.assertNotIn("Statistical representativeness", validation)
        self.assertNotIn("model calls as responses", summary.lower())
        self.assertIn("Requested/planned unique synthetic panelists (job slots):** 1", summary)
        self.assertIn("Response jobs:** 0", validation)
        self.assertNotIn("47 AI profile variations", summary)
        for required_value in (
            "A replacement initiative is funded",
            "Adoption claims lack evidence",
            "A modeled mindset, not population prevalence.",
            "Reduce coordination load",
            "Implementation disruption",
            "Show the implementation mechanism and adoption evidence.",
            "observed",
            "one_context_isolated_replicate_per_job",
            "New first-party evidence",
            "Aggregated non-personal evidence only",
            "Individual targeting",
            "No person-level records are included.",
        ):
            self.assertIn(required_value, summary)
            self.assertIn(required_value, html)

        approved_brief = json.loads(brief.read_text(encoding="utf-8"))
        self.assertIn("## Research Sources", summary)
        self.assertIn("Who this panel represents", html)
        self.assertIn("Full panel record", html)
        self.assertIn("--ip-blue:#4A63F5", html)
        self.assertIn("--ip-mint:#CCFBF1", html)
        self.assertIn("font:17px/1.62", html)
        for source in approved_brief["evidence_sources"]:
            self.assertIn(
                f"[{source['source_label']}]({source['source_url']})",
                summary,
            )
            self.assertIn(f'href="{source["source_url"]}"', html)
            for field in (
                "evidence_id",
                "type",
                "date",
                "collection_method",
                "confidence",
                "limits",
            ):
                self.assertIn(str(source[field]), summary)
                self.assertIn(str(source[field]).replace("_", " "), html)
            for field in ("usable_for", "permitted_uses"):
                for value in source[field]:
                    self.assertIn(value, summary)
                    self.assertIn(value, html)

        def scalar_leaves(value, path="$"):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from scalar_leaves(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from scalar_leaves(child, f"{path}[{index}]")
            elif value is not None:
                yield path, value

        for path, value in scalar_leaves(canonical_panel):
            rendered_value = (
                "Yes" if value is True else "No" if value is False else str(value)
            )
            with self.subTest(canonical_field=path):
                self.assertIn(rendered_value, summary)
                self.assertIn(rendered_value, html)
        self.assertEqual("panel-review-manifest-v1", manifest["schema_version"])
        self.assertEqual(canonical_json_bytes(canonical_panel), canonical_copy)
        self.assertEqual(
            manifest["canonical_panel"]["sha256"],
            hashlib.sha256(canonical_copy).hexdigest(),
        )
        self.assertIn(manifest["review_revision"], approval)
        self.assertIn("Panel review manifest SHA-256", approval)
        self.assertIn("Panel construction approval target SHA-256", approval)

    def test_public_source_url_numeric_slugs_are_not_phone_pii(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        brief["evidence_sources"][0]["source_url"] = (
            "https://example.com/releases/2026-02-23-report-302652945.html"
        )
        errors = validate_research_brief(brief)
        self.assertFalse(
            [error for error in errors if error.code == "pii_phone"], errors
        )

    def test_panel_review_marks_missing_source_link_without_inventing_one(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        brief["evidence_sources"][0]["source_url"] = None
        summary = render_panel_summary(brief, panel)
        html = render_panel_review_html(brief, panel)
        self.assertIn("link not recorded", summary)
        self.assertIn("Link not recorded", html)
        self.assertIn("No direct URL recorded in the approved brief.", html)
        self.assertNotIn('href="None"', html)

        overrides = build_source_link_overrides(
            {
                "candidates": [
                    {
                        "candidate_id": "evidence-1",
                        "decision": "accept",
                        "evidence_item_ids": ["evidence-1"],
                        "source_url": "https://example.com/resolved-source",
                    }
                ]
            }
        )
        linked_summary = render_panel_summary(
            brief, panel, source_links=overrides
        )
        linked_html = render_panel_review_html(
            brief, panel, source_links=overrides
        )
        self.assertIn("https://example.com/resolved-source", linked_summary)
        self.assertIn('href="https://example.com/resolved-source"', linked_html)

    def test_panel_review_manifest_rejects_any_projection_tamper(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "review"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "render-panel-review.py"),
                    "--brief", str(ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json"),
                    "--panel", str(ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json"),
                    "--output-dir", str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            manifest = json.loads((output / "panel-review-manifest.json").read_text())
            with self.assertRaisesRegex(ContractError, "does not match"):
                validate_panel_review_manifest(
                    manifest,
                    panel=panel,
                    summary_bytes=(output / "panel-summary.md").read_bytes() + b" ",
                    html_bytes=(output / "audience-panel-review.html").read_bytes(),
                )

    def test_provisional_panel_review_is_explicitly_unknown_and_unresearched(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        brief, panel = _provisional_documents(
            {
                "scope": {
                    "audience": "Operations leaders",
                    "market": "B2B software",
                    "geography": "United States",
                    "category": "Workflow software",
                    "buying_context": "Evaluating replacement tools",
                    "exclusions": [],
                },
                "user_defined_segments": [
                    {
                        "segment_id": "operations-leaders",
                        "name": "Operations leaders",
                        "description": "User-defined planning segment.",
                    }
                ],
                "accepted_by": "panel-owner",
                "accepted_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
            }
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brief_path = root / "brief.json"
            panel_path = root / "panel.json"
            brief_path.write_text(json.dumps(brief))
            panel_path.write_text(json.dumps(panel))
            output = root / "review"
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "render-panel-review.py"),
                    "--brief", str(brief_path), "--panel", str(panel_path),
                    "--output-dir", str(output),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            summary = (output / "panel-summary.md").read_text()
            html = (output / "audience-panel-review.html").read_text()
        for surface in (summary, html):
            self.assertIn("Provisional, no-research panel", surface)
            self.assertIn("No research evidence", surface)
            self.assertIn("not a research finding", surface)
            self.assertIn("one-run planning", surface)
            self.assertIn("Provisional planning profiles", surface)
            self.assertNotIn("Approved findings", surface)
            self.assertNotIn("These are reusable audience profiles", surface)
            self.assertNotIn("An approved v2 package is a Tier 1", surface)
            self.assertNotIn("Reusable Grounded Profiles", surface)
            self.assertNotIn("See `audience-research-report.html`", surface)
            self.assertIn("No audience research report exists", surface)
            self.assertIn("No research sources", surface)
            self.assertIn("Other audience attributes remain unknown", surface)
        self.assertIn("## Research Sources", summary)
        self.assertNotIn("href=\"javascript:", html)
        self.assertIn("font:17px/1.62", html)
        self.assertIn("1 segment and 1 planning profile", html)
        self.assertIn("all seven decision areas remain explicitly empty", html)
        self.assertNotIn("approved research could support", html.lower())
        for segment in panel["segments"]:
            self.assertEqual([], segment["primary_needs"])
            self.assertEqual([], segment["primary_objections"])
            self.assertEqual([], segment["creative_implications"])
        for archetype in panel["persona_archetypes"]:
            for field in (
                "motivations", "anxieties", "triggers", "objections", "proof_needs"
            ):
                self.assertEqual([], archetype[field])
        for profile in panel["grounded_context_profiles"]:
            for field in ("motivations", "anxieties", "proof_needs"):
                self.assertEqual([], profile["profile_snapshot"][field])
        self.assertEqual([], panel["replicate_strategy"]["fields_allowed_to_vary"])
        for invented in (
            "Evaluate the advertised option",
            "The available audience assumptions may be incomplete",
            "Clear and specific support for the advertised claim",
            "Treat feedback as provisional and directional.",
        ):
            self.assertNotIn(invented, summary)
            self.assertNotIn(invented, html)

        validation = render_validation_report(brief, panel)
        self.assertIn("Provisional planning profiles", validation)
        self.assertIn("Provisional acceptance approves only", validation)
        self.assertNotIn("Panel approval means the evidence", validation)

    def test_projection_keeps_unreferenced_archetypes_and_strata_and_escaped_pipes(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        extra_archetype = copy.deepcopy(panel["persona_archetypes"][0])
        extra_archetype["persona_archetype_id"] = "unreferenced-archetype"
        extra_archetype["display_name"] = "Unreferenced archetype marker"
        extra_archetype["motivations"].append("Archetype-only motivation marker")
        panel["persona_archetypes"].append(extra_archetype)
        extra_stratum = copy.deepcopy(panel["context_strata"][0])
        extra_stratum["context_stratum_id"] = "unreferenced-stratum"
        extra_stratum["dimensions"][0]["value"] = "Alpha | Beta"
        panel["context_strata"].append(extra_stratum)

        summary = render_panel_summary(brief, panel)
        html = render_panel_review_html(brief, panel)
        for marker in (
            "Unreferenced archetype marker",
            "Archetype-only motivation marker",
            "unreferenced-stratum",
        ):
            self.assertIn(marker, summary)
            self.assertIn(marker, html)
        self.assertIn("Alpha \\| Beta", summary)
        self.assertIn("Alpha | Beta", html)
        self.assertIn("<td>Alpha | Beta</td>", html)

    def test_identical_omnibus_finding_sets_require_profile_specific_support_or_exceptions(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        duplicate = copy.deepcopy(panel["persona_archetypes"][0])
        duplicate["persona_archetype_id"] = "second-operator"
        duplicate["display_name"] = "Second operator"
        panel["persona_archetypes"].append(duplicate)
        result = audit_evidence_specificity(brief, panel)
        self.assertEqual("fail", result["status"])
        self.assertEqual(1, len(result["identical_evidence_sets"]))
        panel["persona_archetypes"][0]["inference_boundary"] += (
            " Evidence-specificity exception: this long free-text sentence is not "
            "a structured exception and must not bypass the audit."
        )
        self.assertEqual("fail", audit_evidence_specificity(brief, panel)["status"])
        for archetype in panel["persona_archetypes"]:
            archetype["inference_boundary"] += (
                " Evidence-specificity exception [unsupported_distinction=the two modeled "
                "buyer postures are not distinguished by the approved sources; "
                "missing_research=profile-level interviews comparing those postures are "
                "not available; bounded_use=retain only as a planning hypothesis for this "
                "synthetic test]: the omnibus category finding supports the cohort but not "
                "the modeled distinction, which remains explicitly unverified."
            )
        self.assertEqual("pass", audit_evidence_specificity(brief, panel)["status"])

        distinct = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        second = copy.deepcopy(distinct["persona_archetypes"][0])
        second["persona_archetype_id"] = "second-operator"
        second["finding_ids"] = ["finding-2"]
        distinct["persona_archetypes"].append(second)
        finding_two = copy.deepcopy(brief["findings"][0])
        finding_two["finding_id"] = "finding-2"
        brief["findings"].append(finding_two)
        self.assertEqual("fail", audit_evidence_specificity(brief, distinct)["status"])
        brief["findings"][0]["inference_boundary"] += (
            " Evidence scope: profile:evidence-led-operator."
        )
        brief["findings"][1]["inference_boundary"] += (
            " Evidence scope: profile:second-operator."
        )
        self.assertEqual("pass", audit_evidence_specificity(brief, distinct)["status"])

    def test_broad_finding_variation_cannot_disguise_identical_narrow_support(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        cohort = copy.deepcopy(brief["findings"][0])
        cohort["finding_id"] = "cohort-support"
        cohort["inference_boundary"] += " Evidence scope: cohort:operations-leaders."
        broad_a = copy.deepcopy(brief["findings"][0])
        broad_a["finding_id"] = "broad-a"
        broad_b = copy.deepcopy(brief["findings"][0])
        broad_b["finding_id"] = "broad-b"
        brief["findings"] = [cohort, broad_a, broad_b]
        first = panel["persona_archetypes"][0]
        first["finding_ids"] = ["cohort-support", "broad-a"]
        second = copy.deepcopy(first)
        second["persona_archetype_id"] = "second-operator"
        second["display_name"] = "Second operator"
        second["finding_ids"] = ["cohort-support", "broad-b"]
        panel["persona_archetypes"].append(second)

        result = audit_evidence_specificity(brief, panel)
        self.assertEqual("fail", result["status"])
        self.assertTrue(
            all(row["identical_narrow_finding_set"] for row in result["profiles"])
        )

        broad_a["inference_boundary"] += (
            " Evidence scope: profile:evidence-led-operator."
        )
        broad_b["inference_boundary"] += " Evidence scope: profile:second-operator."
        self.assertEqual("pass", audit_evidence_specificity(brief, panel)["status"])

    def test_panel_review_cli_requires_paired_run_inputs(self):
        brief = ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json"
        panel = ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json"
        with tempfile.TemporaryDirectory() as temp:
            run_plan = Path(temp) / "run-plan.json"
            run_plan.write_text("{}", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "render-panel-review.py"),
                    "--brief", str(brief), "--panel", str(panel),
                    "--output-dir", str(Path(temp) / "review"),
                    "--run-plan", str(run_plan),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(2, completed.returncode)
        self.assertIn("--run-plan and --run-results must be supplied together", completed.stderr)

    def test_panel_review_cli_rejects_non_object_paired_run_json_without_traceback(self):
        brief = ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json"
        panel = ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json"
        with tempfile.TemporaryDirectory() as temp:
            run_plan = Path(temp) / "run-plan.json"
            run_results = Path(temp) / "run-results.json"
            for name, plan_payload, results_payload in (
                ("arrays", "[]", "[]"),
                ("scalars", "1", "1"),
            ):
                with self.subTest(name=name):
                    run_plan.write_text(plan_payload, encoding="utf-8")
                    run_results.write_text(results_payload, encoding="utf-8")
                    completed = subprocess.run(
                        [
                            sys.executable, str(SCRIPTS / "render-panel-review.py"),
                            "--brief", str(brief), "--panel", str(panel),
                            "--output-dir", str(Path(temp) / f"review-{name}"),
                            "--run-plan", str(run_plan), "--run-results", str(run_results),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(2, completed.returncode)
                    self.assertIn("run_plan must be an object", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)

    def test_count_panel_entities_separates_construction_and_lineage_counts(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        run_plan = {
            "maximum_synthetic_panelists": 99,
            "synthetic_replicate_capacity": {"required_total": 7},
        }
        run_results = {
            "usage": {
                "unique_job_slots_planned": 7,
                "unique_job_slots_dispatched": 5,
                "accepted_response_records": 3,
                "rejected_attempts": 2,
                "total_model_calls": 8,
            },
            "dispatch_audit": [{}, {}, {}, {}, {}],
            "responses": [{}, {}, {}],
            "rejected_attempts": [{}, {}],
            "raw_provider_returns": [
                {"attempt_number": 1, "accepted": True},
                {"attempt_number": 1, "accepted": False},
                {"attempt_number": 2, "accepted": True},
                {"attempt_number": 1, "accepted": False},
                {"attempt_number": 2, "accepted": True},
                {"attempt_number": 1, "accepted": True},
                {"attempt_number": 1, "accepted": True},
                {"attempt_number": 1, "accepted": True},
            ],
        }

        counts = count_panel_entities(
            brief=brief,
            panel=panel,
            run_plan=run_plan,
            run_results=run_results,
        )

        self.assertEqual(
            [
                "audience_groups", "mindsets", "buying_situations", "reusable_profiles",
                "requested_synthetic_panelists", "response_jobs", "accepted_response_records",
                "retries", "rejected_provider_returns", "model_calls",
            ],
            list(counts),
        )
        self.assertEqual(
            {
                "audience_groups": 1,
                "mindsets": 1,
                "buying_situations": 1,
                "reusable_profiles": 1,
                "requested_synthetic_panelists": 7,
                "response_jobs": 5,
                "accepted_response_records": 3,
                "retries": 2,
                "rejected_provider_returns": 2,
                "model_calls": 8,
            },
            counts,
        )

    def test_count_panel_entities_uses_null_run_counts_without_paired_run_data(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )

        counts = count_panel_entities(brief=brief, panel=panel)

        self.assertEqual([1, 1, 1, 1], list(counts.values())[:4])
        self.assertEqual([None] * 6, list(counts.values())[4:])
        with self.assertRaisesRegex(ContractError, "together"):
            count_panel_entities(brief=brief, panel=panel, run_plan={})

    def test_count_panel_entities_rejects_count_proxies_and_mismatched_lineage(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        run_plan = {"synthetic_replicate_capacity": {"required_total": 4}}
        run_results = {
            "usage": {
                "unique_job_slots_planned": 4,
                "unique_job_slots_dispatched": 1,
                "accepted_response_records": 1,
                "rejected_attempts": 0,
                "total_model_calls": 1,
            },
            "raw_provider_returns": [{"attempt_number": 1, "accepted": True}],
        }
        with self.assertRaisesRegex(ContractError, "synthetic_replicate_capacity"):
            count_panel_entities(
                brief=brief,
                panel=panel,
                run_plan={"maximum_synthetic_panelists": 4},
                run_results=run_results,
            )
        mismatched = copy.deepcopy(run_results)
        mismatched["usage"]["total_model_calls"] = 2
        with self.assertRaisesRegex(ContractError, "raw_provider_returns"):
            count_panel_entities(
                brief=brief, panel=panel, run_plan=run_plan, run_results=mismatched
            )

    def test_count_panel_entities_requires_valid_planned_and_lineage_relationships(self):
        brief = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-brief.json").read_text()
        )
        panel = json.loads(
            (ROOT / "conformance" / "fixtures" / "audience-research" / "approved-panel.json").read_text()
        )
        run_plan = {"synthetic_replicate_capacity": {"required_total": 4}}
        run_results = {
            "usage": {
                "unique_job_slots_planned": 4,
                "unique_job_slots_dispatched": 1,
                "accepted_response_records": 1,
                "rejected_attempts": 0,
                "total_model_calls": 1,
            },
            "raw_provider_returns": [{"attempt_number": 1, "accepted": True}],
        }
        cases = (
            ("missing planned usage", lambda value: value["usage"].pop("unique_job_slots_planned"), "unique_job_slots_planned"),
            ("dispatched exceeds planned", lambda value: value["usage"].update(unique_job_slots_dispatched=5), "dispatched jobs"),
            ("accepted exceeds dispatched", lambda value: value["usage"].update(accepted_response_records=2, total_model_calls=2) or value.update(raw_provider_returns=[{"attempt_number": 1, "accepted": True}, {"attempt_number": 1, "accepted": True}]), "accepted response records"),
            ("rejected exceeds calls", lambda value: value["usage"].update(rejected_attempts=2), "rejected provider returns"),
            ("accepted exceeds calls", lambda value: value["usage"].update(accepted_response_records=2), "accepted response records"),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                invalid = copy.deepcopy(run_results)
                mutate(invalid)
                with self.assertRaisesRegex(ContractError, message):
                    count_panel_entities(
                        brief=brief, panel=panel, run_plan=run_plan, run_results=invalid
                    )
        for malformed_plan, malformed_results, message in (
            ([], run_results, "run_plan must be an object"),
            (run_plan, [], "run_results must be an object"),
            (1, run_results, "run_plan must be an object"),
            (run_plan, 1, "run_results must be an object"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContractError, message):
                    count_panel_entities(
                        brief=brief,
                        panel=panel,
                        run_plan=malformed_plan,
                        run_results=malformed_results,
                    )

    def test_plugin_local_package_and_library_commands_resolve(self):
        for script in ("build-panel-package.py", "manage-panel-library.py"):
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / script), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("usage:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
