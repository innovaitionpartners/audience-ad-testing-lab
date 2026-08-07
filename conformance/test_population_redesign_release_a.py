from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
RESEARCH_FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"
RELEASE_A_FIXTURES = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-panel-builder"
    / "release-a"
)
sys.path.insert(0, str(PANEL_SCRIPTS))
sys.path.insert(0, str(LAB_SCRIPTS))

from audience_lab.audience_library import list_panels  # noqa: E402
from audience_lab.audience_package import (  # noqa: E402
    build_audience_package,
    validate_package_archive,
)
from audience_panel_builder.common import (  # noqa: E402
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.construction_audit import (  # noqa: E402
    construction_audit_sha256,
)
from audience_panel_builder.source_scoring import (  # noqa: E402
    score_source_candidates,
)


RENDER_REPORT_CLI = PANEL_SCRIPTS / "render-research-report.py"
RENDER_REVIEW_CLI = PANEL_SCRIPTS / "render-panel-review.py"
VALIDATE_SUPPORT_CLI = PANEL_SCRIPTS / "validate-finding-support.py"
VALIDATE_SYNTHESIS_CLI = PANEL_SCRIPTS / "validate-synthesis-matrix.py"
VALIDATE_AUDIT_CLI = PANEL_SCRIPTS / "validate-panel-construction-audit.py"
PROPOSE_CLI = PANEL_SCRIPTS / "propose-panel-package.py"
BUILD_CLI = PANEL_SCRIPTS / "build-approved-panel-package.py"
REGISTER_CLI = PANEL_SCRIPTS / "register-approved-panel.py"
GENERATED_AT = "2026-07-24T12:30:00Z"
REPORT_OUTPUT_PATHS = {
    "audience-research-report-manifest.json",
    "audience-research-report.html",
    "source-inventory.json",
    "verbatim-inventory.json",
}
PACKAGE_OUTPUT_PATHS = {
    "README.txt",
    "audience-panel-package.zip",
    "audience-research-report.html",
    "package-manifest.json",
    "persona-research-brief.json",
    "research-sources.csv",
    "saved-audience-panel.json",
}
LIBRARY_OUTPUT_PATHS = {
    "index.json",
    *{
        f"panels/operations-leaders/1.0.0/{path}"
        for path in PACKAGE_OUTPUT_PATHS
    },
}


class HonestReleaseAWorkflowTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.brief = self._load(RESEARCH_FIXTURES / "approved-brief.json")
        self.panel = self._load(RESEARCH_FIXTURES / "approved-panel.json")
        self.dogfood_state = self._load(
            RELEASE_A_FIXTURES / "workflow-state-dogfood.json"
        )
        self.approved_state = self._load(
            RELEASE_A_FIXTURES / "workflow-state-approved.json"
        )
        self.audit_template = self._load(
            RELEASE_A_FIXTURES / "construction-audit.json"
        )
        self.documents = self._research_documents()

    @staticmethod
    def _load(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssertionError(f"{path} must contain an object")
        return value

    @staticmethod
    def _run(
        script: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))
        return path

    def _tree_snapshot(
        self,
        root: Path,
        *,
        expected_paths: set[str],
    ) -> dict[str, bytes]:
        self.assertTrue(root.is_dir(), f"missing snapshot root: {root}")
        snapshot = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(expected_paths, set(snapshot))
        return snapshot

    def _research_documents(self) -> dict[str, dict[str, object]]:
        ledger = {
            "schema_version": "audience-evidence-ledger-v1",
            "ledger_id": "operations-leaders-ledger",
            "created_at": "2026-07-24T12:00:00Z",
            "plan_id": "operations-leaders-plan",
            "imports": [
                {
                    "import_id": "research-import",
                    "source_adapter": "document-research",
                    "source_schema_version": "1.0",
                    "input_sha256": "sha256:" + "1" * 64,
                    "permission": "allowed",
                    "source_status": "verified",
                    "accepted_count": 1,
                    "rejected_count": 0,
                    "deduplicated_count": 0,
                }
            ],
            "evidence_items": [
                {
                    "evidence_item_id": "evidence-item-1",
                    "import_id": "research-import",
                    "source_url": "https://example.com/research/workflow-adoption",
                    "item_type": "survey_finding",
                    "content_summary": (
                        "Operations leaders seek credible adoption evidence "
                        "before replacing core workflows."
                    ),
                    "text_fidelity": "analyst_summary",
                    "content_sha256": "sha256:" + "2" * 64,
                    "source_pointer": "page-4",
                    "upstream_source_ids": ["evidence-1"],
                    "use_constraints": ["directional_only"],
                    "quality_flags": [],
                }
            ],
            "summary": {
                "imports": 1,
                "accepted_items": 1,
                "deduplicated_items": 0,
                "rejected_items": 0,
            },
        }
        finding_support = {
            "schema_version": "audience-finding-support-v1",
            "created_at": "2026-07-24T12:05:00Z",
            "ledger_sha256": sha256_json(ledger),
            "findings": [
                {
                    "finding_id": "finding-1",
                    "evidence_id": "evidence-1",
                    "evidence_item_ids": ["evidence-item-1"],
                    "support_role": "supports",
                    "analyst_note": "Direct support for the approved brief finding.",
                }
            ],
        }
        synthesis = {
            "schema_version": "audience-synthesis-matrix-v1",
            "plan_id": "operations-leaders-plan",
            "created_at": "2026-07-24T12:10:00Z",
            "ledger_sha256": sha256_json(ledger),
            "questions": [
                {
                    "question_id": "implementation-proof",
                    "research_question": (
                        "What proof reduces perceived implementation risk?"
                    ),
                    "findings": [
                        {
                            "finding_id": "finding-1",
                            "statement": (
                                "Operations leaders seek credible adoption "
                                "evidence before replacing core workflows."
                            ),
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
                            "confidence_reason": (
                                "One decision-relevant documented source."
                            ),
                            "inference_boundary": (
                                "Directional finding only; not population prevalence."
                            ),
                            "marketer_implication": (
                                "Show implementation mechanism and adoption evidence."
                            ),
                            "creative_implications": [
                                "Make implementation proof visible."
                            ],
                            "segment_decision": "candidate",
                        }
                    ],
                }
            ],
        }
        candidate = {
            "candidate_id": "evidence-1",
            "source_family_id": "industry-report",
            "lane": "survey",
            "title": "Fictional workflow adoption report",
            "publisher": "Fictional Research Group",
            "source_url": "https://example.com/research/workflow-adoption",
            "methodology_url": "https://example.com/research/methodology",
            "publication_date": "2026-06-15",
            "field_dates": "2026-06",
            "population": "Operations leaders",
            "geography": "United States",
            "sample_size": 100,
            "collection_method": "Survey",
            "access_route": "public",
            "reuse_status": "allowed",
            "assessments": {
                "audience_match": "exact",
                "decision_match": "exact",
                "methodology_transparency": "documented",
                "collection_quality": "documented",
                "recency": "current",
                "geography_match": "exact",
                "subgroup_usefulness": "direct",
                "permitted_use": "allowed",
            },
            "social_collection": None,
            "upstream_source_ids": ["evidence-1"],
            "evidence_item_ids": ["evidence-item-1"],
            "notes": "Used as directional evidence.",
        }
        scored_sources = score_source_candidates(
            {
                "schema_version": "audience-source-candidates-v1",
                "plan_id": "operations-leaders-plan",
                "created_at": "2026-07-24T12:00:00Z",
                "candidates": [candidate],
            }
        )
        return {
            "plan": {
                "plan_id": "operations-leaders-plan",
                "workflow_route": "create_research_backed_panel",
            },
            "scored_sources": scored_sources,
            "ledger": ledger,
            "finding_support": finding_support,
            "synthesis": synthesis,
        }

    def _write_research_inputs(
        self,
        root: Path,
        workflow_state: dict[str, object],
    ) -> dict[str, Path]:
        values = {
            "workflow-state": workflow_state,
            "brief": self.brief,
            "panel": self.panel,
            **self.documents,
        }
        return {
            name: self._write_json(root / f"{name}.json", value)
            for name, value in values.items()
        }

    def _render_report(
        self,
        paths: dict[str, Path],
        output_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        review_dir = output_dir.parent / f"{output_dir.name}-panel-review"
        review = self._run(
            RENDER_REVIEW_CLI,
            "--brief", str(paths["brief"]),
            "--panel", str(paths["panel"]),
            "--output-dir", str(review_dir),
        )
        if review.returncode != 0:
            return review
        return self._run(
            RENDER_REPORT_CLI,
            "--workflow-state",
            str(paths["workflow-state"]),
            "--brief",
            str(paths["brief"]),
            "--panel",
            str(paths["panel"]),
            "--plan",
            str(paths["plan"]),
            "--scored-sources",
            str(paths["scored_sources"]),
            "--ledger",
            str(paths["ledger"]),
            "--finding-support",
            str(paths["finding_support"]),
            "--synthesis",
            str(paths["synthesis"]),
            "--panel-review-manifest",
            str(review_dir / "panel-review-manifest.json"),
            "--panel-summary",
            str(review_dir / "panel-summary.md"),
            "--panel-review-html",
            str(review_dir / "audience-panel-review.html"),
            "--generated-at",
            GENERATED_AT,
            "--output-dir",
            str(output_dir),
        )

    def _audit_for_manifest(
        self,
        manifest: dict[str, object],
    ) -> dict[str, object]:
        audit = copy.deepcopy(self.audit_template)
        audit["input_bindings"].update(
            {
                "brief_sha256": sha256_json(self.brief).removeprefix(
                    "sha256:"
                ),
                "panel_sha256": sha256_json(self.panel).removeprefix(
                    "sha256:"
                ),
                "evidence_ledger_sha256": sha256_json(
                    self.documents["ledger"]
                ).removeprefix("sha256:"),
                "finding_support_sha256": sha256_json(
                    self.documents["finding_support"]
                ).removeprefix("sha256:"),
                "synthesis_matrix_sha256": sha256_json(
                    self.documents["synthesis"]
                ).removeprefix("sha256:"),
                "report_manifest_sha256": sha256_json(manifest).removeprefix(
                    "sha256:"
                ),
            }
        )
        return audit

    def _validate_audit(
        self,
        paths: dict[str, Path],
        audit_path: Path,
        manifest_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        review_manifest_path = (
            manifest_path.parent.parent
            / f"{manifest_path.parent.name}-panel-review"
            / "panel-review-manifest.json"
        )
        return self._run(
            VALIDATE_AUDIT_CLI,
            "--audit",
            str(audit_path),
            "--brief",
            str(paths["brief"]),
            "--panel",
            str(paths["panel"]),
            "--ledger",
            str(paths["ledger"]),
            "--finding-support",
            str(paths["finding_support"]),
            "--synthesis",
            str(paths["synthesis"]),
            "--report-manifest",
            str(manifest_path),
            "--panel-review-manifest",
            str(review_manifest_path),
        )

    @staticmethod
    def _gate_document_arguments(
        paths: dict[str, Path],
        manifest_path: Path,
    ) -> list[str]:
        review_manifest_path = (
            manifest_path.parent.parent
            / f"{manifest_path.parent.name}-panel-review"
            / "panel-review-manifest.json"
        )
        return [
            "--ledger",
            str(paths["ledger"]),
            "--finding-support",
            str(paths["finding_support"]),
            "--synthesis",
            str(paths["synthesis"]),
            "--report-manifest",
            str(manifest_path),
            "--panel-review-manifest",
            str(review_manifest_path),
        ]

    def test_documented_validation_loop_matches_runtime_lifecycle(self) -> None:
        workflow = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "references"
            / "route-workflows-and-output-templates.md"
        ).read_text(encoding="utf-8")
        validation_loop = workflow.split("## Validation Loop", 1)[1].split(
            "## Final Deliverable Templates",
            1,
        )[0]
        ordered_markers = (
            "scripts/plan-research.py",
            "scripts/normalize-social-evidence.py",
            "scripts/validate-data-handoff.py",
            "scripts/build-evidence-ledger.py",
            "scripts/score-research-sources.py",
            "scripts/validate-finding-support.py",
            "scripts/validate-synthesis-matrix.py",
            "scripts/render-panel-review.py",
            "scripts/render-research-report.py",
            "scripts/validate-panel-construction-audit.py",
            "scripts/propose-panel-package.py",
            "Approval: record the exact proposed package hash",
            "scripts/build-approved-panel-package.py",
            "scripts/register-approved-panel.py",
        )
        positions = [validation_loop.index(marker) for marker in ordered_markers]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("Optional upstream handoff gate", validation_loop)
        self.assertIn(
            "before it enters the evidence ledger or synthesis",
            validation_loop,
        )

    def test_honest_release_a_workflow_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            brief_sha = sha256_json(self.brief).removeprefix("sha256:")
            panel_sha = sha256_json(self.panel).removeprefix("sha256:")
            synthesis_sha = sha256_json(
                self.documents["synthesis"]
            ).removeprefix("sha256:")
            for state in (self.dogfood_state, self.approved_state):
                self.assertEqual(brief_sha, state["bindings"]["brief_sha256"])
                self.assertEqual(panel_sha, state["bindings"]["panel_sha256"])
            approvals = {
                row["scope"]: row for row in self.approved_state["approvals"]
            }
            self.assertEqual(
                synthesis_sha,
                approvals["evidence_synthesis"]["target_sha256"],
            )
            self.assertEqual(
                "approved",
                approvals["panel_construction"]["status"],
            )

            dogfood_paths = self._write_research_inputs(
                root / "dogfood-inputs",
                self.dogfood_state,
            )
            support = self._run(
                VALIDATE_SUPPORT_CLI,
                str(dogfood_paths["ledger"]),
                str(dogfood_paths["finding_support"]),
            )
            self.assertEqual(0, support.returncode, support.stderr)
            synthesis = self._run(
                VALIDATE_SYNTHESIS_CLI,
                str(dogfood_paths["ledger"]),
                str(dogfood_paths["finding_support"]),
                str(dogfood_paths["synthesis"]),
            )
            self.assertEqual(0, synthesis.returncode, synthesis.stderr)
            review_dir = root / "dogfood-review"
            review = self._run(
                RENDER_REVIEW_CLI,
                "--brief",
                str(dogfood_paths["brief"]),
                "--panel",
                str(dogfood_paths["panel"]),
                "--output-dir",
                str(review_dir),
                "--plan",
                str(dogfood_paths["plan"]),
                "--scored-sources",
                str(dogfood_paths["scored_sources"]),
                "--ledger",
                str(dogfood_paths["ledger"]),
                "--finding-support",
                str(dogfood_paths["finding_support"]),
                "--synthesis-matrix",
                str(dogfood_paths["synthesis"]),
            )
            self.assertEqual(0, review.returncode, review.stderr)
            self.assertEqual(
                {
                    "panel-summary.md",
                    "audience-panel-review.html",
                    "panel-review-manifest.json",
                    "panel-construction-approval-request.md",
                    "saved-audience-panel.json",
                    "validation-report.md",
                },
                {path.name for path in review_dir.iterdir()},
            )

            dogfood_report_dir = root / "dogfood-report"
            dogfood_render = self._render_report(
                dogfood_paths,
                dogfood_report_dir,
            )
            self.assertEqual(0, dogfood_render.returncode, dogfood_render.stdout)
            dogfood_html = (
                dogfood_report_dir / "audience-research-report.html"
            ).read_text(encoding="utf-8")
            self.assertIn("DOGFOOD", dogfood_html)
            self.assertIn("Population validity is unavailable", dogfood_html)
            dogfood_manifest_path = (
                dogfood_report_dir / "audience-research-report-manifest.json"
            )
            dogfood_manifest = self._load(dogfood_manifest_path)
            dogfood_audit_payload = self._audit_for_manifest(dogfood_manifest)
            dogfood_audit_path = self._write_json(
                root / "dogfood-audit.json",
                dogfood_audit_payload,
            )
            dogfood_audit = self._validate_audit(
                dogfood_paths,
                dogfood_audit_path,
                dogfood_manifest_path,
            )
            self.assertEqual(0, dogfood_audit.returncode, dogfood_audit.stdout)
            self.assertEqual("pass", json.loads(dogfood_audit.stdout)["result"])

            dogfood_proposal = self._run(
                PROPOSE_CLI,
                "--workflow-state",
                str(dogfood_paths["workflow-state"]),
                "--audit",
                str(dogfood_audit_path),
                "--brief",
                str(dogfood_paths["brief"]),
                "--panel",
                str(dogfood_paths["panel"]),
                *self._gate_document_arguments(
                    dogfood_paths,
                    dogfood_manifest_path,
                ),
            )
            self.assertEqual(2, dogfood_proposal.returncode)
            self.assertIn("must be approved", dogfood_proposal.stdout)
            dogfood_package_dir = root / "dogfood-package"
            dogfood_build = self._run(
                BUILD_CLI,
                "--workflow-state",
                str(dogfood_paths["workflow-state"]),
                "--audit",
                str(dogfood_audit_path),
                "--brief",
                str(dogfood_paths["brief"]),
                "--panel",
                str(dogfood_paths["panel"]),
                *self._gate_document_arguments(
                    dogfood_paths,
                    dogfood_manifest_path,
                ),
                "--output-dir",
                str(dogfood_package_dir),
            )
            self.assertEqual(2, dogfood_build.returncode)
            self.assertFalse(dogfood_package_dir.exists())
            self.assertFalse(
                any(root.rglob("audience-panel-package.zip")),
                "dogfood/proposal path must retain no reusable package",
            )

            approved_paths = self._write_research_inputs(
                root / "approved-inputs",
                self.approved_state,
            )
            approved_report_a = root / "approved-report-a"
            approved_report_b = root / "approved-report-b"
            first_render = self._render_report(approved_paths, approved_report_a)
            second_render = self._render_report(approved_paths, approved_report_b)
            self.assertEqual(0, first_render.returncode, first_render.stdout)
            self.assertEqual(0, second_render.returncode, second_render.stdout)
            first_files = {
                path.name: path.read_bytes() for path in approved_report_a.iterdir()
            }
            second_files = {
                path.name: path.read_bytes() for path in approved_report_b.iterdir()
            }
            self.assertEqual(first_files, second_files)
            self.assertIn(b"APPROVED", first_files["audience-research-report.html"])
            approved_manifest_path = (
                approved_report_a / "audience-research-report-manifest.json"
            )
            approved_manifest = self._load(approved_manifest_path)
            approved_audit = self._audit_for_manifest(approved_manifest)
            approved_audit_path = self._write_json(
                root / "approved-audit.json",
                approved_audit,
            )
            approved_audit_result = self._validate_audit(
                approved_paths,
                approved_audit_path,
                approved_manifest_path,
            )
            self.assertEqual(
                0,
                approved_audit_result.returncode,
                approved_audit_result.stdout,
            )
            approved_audit_sha = construction_audit_sha256(approved_audit)

            proposal_state = copy.deepcopy(self.approved_state)
            approved_review_manifest = self._load(
                approved_report_a.parent
                / f"{approved_report_a.name}-panel-review"
                / "panel-review-manifest.json"
            )
            next(
                approval
                for approval in proposal_state["approvals"]
                if approval["scope"] == "panel_construction"
            )["target_sha256"] = sha256_json(
                approved_review_manifest
            ).removeprefix("sha256:")
            proposal_state["bindings"]["report_inputs_sha256"] = (
                approved_manifest["report_inputs_sha256"]
            )
            proposal_state["bindings"]["audit_sha256"] = approved_audit_sha
            proposal_state_path = self._write_json(
                root / "proposal-state.json",
                proposal_state,
            )
            proposal = self._run(
                PROPOSE_CLI,
                "--workflow-state",
                str(proposal_state_path),
                "--audit",
                str(approved_audit_path),
                "--brief",
                str(approved_paths["brief"]),
                "--panel",
                str(approved_paths["panel"]),
                *self._gate_document_arguments(
                    approved_paths,
                    approved_manifest_path,
                ),
            )
            self.assertEqual(0, proposal.returncode, proposal.stdout)
            proposal_payload = json.loads(proposal.stdout)
            package_sha = proposal_payload["package_sha256"]

            final_state = copy.deepcopy(proposal_state)
            final_state["bindings"]["package_sha256"] = package_sha
            final_state["approvals"].append(
                {
                    "scope": "package_registration",
                    "status": "approved",
                    "approved_by": "panel-owner",
                    "approved_at": "2026-07-24T13:00:00Z",
                    "target_sha256": package_sha,
                    "note": "Approved exact proposed v2 package bytes.",
                }
            )
            final_state_path = self._write_json(
                root / "final-approved-state.json",
                final_state,
            )
            gated_package_dir = root / "approved-package"
            gated_build = self._run(
                BUILD_CLI,
                "--workflow-state",
                str(final_state_path),
                "--audit",
                str(approved_audit_path),
                "--brief",
                str(approved_paths["brief"]),
                "--panel",
                str(approved_paths["panel"]),
                *self._gate_document_arguments(
                    approved_paths,
                    approved_manifest_path,
                ),
                "--output-dir",
                str(gated_package_dir),
            )
            self.assertEqual(0, gated_build.returncode, gated_build.stdout)
            gated_zip = gated_package_dir / "audience-panel-package.zip"
            direct = build_audience_package(
                self.brief,
                self.panel,
                root / "direct-v2-package",
                generator_version="1.0.0",
            )
            self.assertEqual(
                direct.package_zip_path.read_bytes(),
                gated_zip.read_bytes(),
            )
            self.assertEqual(
                package_sha,
                hashlib.sha256(gated_zip.read_bytes()).hexdigest(),
            )
            package_validation = validate_package_archive(gated_zip)
            self.assertEqual("audience-panel-package-v2", package_validation["schema_version"])
            with zipfile.ZipFile(gated_zip) as archive:
                package_manifest = json.loads(
                    archive.read("package-manifest.json")
                )
            self.assertEqual("1.0.0", package_manifest["generator_version"])

            library = root / "approved-library"
            registered = self._run(
                REGISTER_CLI,
                "--workflow-state",
                str(final_state_path),
                "--audit",
                str(approved_audit_path),
                "--package",
                str(gated_zip),
                *self._gate_document_arguments(
                    approved_paths,
                    approved_manifest_path,
                ),
                "--library-root",
                str(library),
            )
            self.assertEqual(0, registered.returncode, registered.stdout)
            self.assertEqual("registered", json.loads(registered.stdout)["status"])
            self.assertEqual(
                [self.panel["panel_id"]],
                [
                    item["panel_id"]
                    for item in list_panels(library_root=library)["panels"]
                ],
            )
            library_snapshot = self._tree_snapshot(
                library,
                expected_paths=LIBRARY_OUTPUT_PATHS,
            )
            approved_report_a_snapshot = self._tree_snapshot(
                approved_report_a,
                expected_paths=REPORT_OUTPUT_PATHS,
            )
            approved_report_b_snapshot = self._tree_snapshot(
                approved_report_b,
                expected_paths=REPORT_OUTPUT_PATHS,
            )
            approved_package_snapshot = self._tree_snapshot(
                gated_package_dir,
                expected_paths=PACKAGE_OUTPUT_PATHS,
            )

            dogfood_library = root / "dogfood-library"
            dogfood_register = self._run(
                REGISTER_CLI,
                "--workflow-state",
                str(dogfood_paths["workflow-state"]),
                "--audit",
                str(dogfood_audit_path),
                "--package",
                str(gated_zip),
                *self._gate_document_arguments(
                    dogfood_paths,
                    dogfood_manifest_path,
                ),
                "--library-root",
                str(dogfood_library),
            )
            self.assertEqual(2, dogfood_register.returncode)
            self.assertIn("must be approved", dogfood_register.stdout)
            self.assertFalse(dogfood_library.exists())

            original_panel_bytes = approved_paths["panel"].read_bytes()
            stale_panel_bytes = original_panel_bytes.replace(
                b"Operations Leaders",
                b"Operations Leaderx",
                1,
            )
            self.assertEqual(len(original_panel_bytes), len(stale_panel_bytes))
            self.assertEqual(
                1,
                sum(
                    first != second
                    for first, second in zip(
                        original_panel_bytes,
                        stale_panel_bytes,
                    )
                ),
            )
            stale_panel_path = root / "stale-panel.json"
            stale_panel_path.write_bytes(stale_panel_bytes)
            stale_paths = dict(approved_paths)
            stale_paths["panel"] = stale_panel_path

            stale_report_dir = root / "stale-report"
            stale_report = self._render_report(stale_paths, stale_report_dir)
            self.assertEqual(2, stale_report.returncode)
            self.assertFalse(stale_report_dir.exists())
            stale_audit = self._validate_audit(
                stale_paths,
                approved_audit_path,
                approved_manifest_path,
            )
            self.assertEqual(2, stale_audit.returncode)
            stale_package_dir = root / "stale-gated-package"
            stale_build = self._run(
                BUILD_CLI,
                "--workflow-state",
                str(final_state_path),
                "--audit",
                str(approved_audit_path),
                "--brief",
                str(approved_paths["brief"]),
                "--panel",
                str(stale_panel_path),
                *self._gate_document_arguments(
                    approved_paths,
                    approved_manifest_path,
                ),
                "--output-dir",
                str(stale_package_dir),
            )
            self.assertEqual(2, stale_build.returncode)
            self.assertFalse(stale_package_dir.exists())

            stale_panel = self._load(stale_panel_path)
            stale_direct = build_audience_package(
                self.brief,
                stale_panel,
                root / "stale-attack-package",
                generator_version="1.0.0",
            )
            stale_register = self._run(
                REGISTER_CLI,
                "--workflow-state",
                str(final_state_path),
                "--audit",
                str(approved_audit_path),
                "--package",
                str(stale_direct.package_zip_path),
                *self._gate_document_arguments(
                    approved_paths,
                    approved_manifest_path,
                ),
                "--library-root",
                str(library),
            )
            self.assertEqual(2, stale_register.returncode)
            self.assertIn("exact canonical panel bytes", stale_register.stdout)
            self.assertEqual(
                library_snapshot,
                self._tree_snapshot(
                    library,
                    expected_paths=LIBRARY_OUTPUT_PATHS,
                ),
            )
            self.assertEqual(
                approved_report_a_snapshot,
                self._tree_snapshot(
                    approved_report_a,
                    expected_paths=REPORT_OUTPUT_PATHS,
                ),
            )
            self.assertEqual(
                approved_report_b_snapshot,
                self._tree_snapshot(
                    approved_report_b,
                    expected_paths=REPORT_OUTPUT_PATHS,
                ),
            )
            self.assertEqual(
                approved_package_snapshot,
                self._tree_snapshot(
                    gated_package_dir,
                    expected_paths=PACKAGE_OUTPUT_PATHS,
                ),
            )
            self.assertEqual(
                package_sha,
                hashlib.sha256(gated_zip.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                [self.panel["panel_id"]],
                [
                    item["panel_id"]
                    for item in list_panels(library_root=library)["panels"]
                ],
            )


if __name__ == "__main__":
    unittest.main()
