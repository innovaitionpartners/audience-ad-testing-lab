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
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(PANEL_SCRIPTS))
sys.path.insert(0, str(LAB_SCRIPTS))

from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_panel_builder.approval_gate import (  # noqa: E402
    build_package_proposal,
    require_package_build_ready,
    require_registration_ready,
)
from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.construction_audit import (  # noqa: E402
    construction_audit_sha256,
    require_passing_construction_audit_for_documents,
)
from audience_panel_builder.reporting import render_research_report  # noqa: E402
from audience_panel_builder.review import (  # noqa: E402
    build_panel_review_manifest,
    render_panel_review_html,
    render_panel_summary,
)


PROPOSE_CLI = PANEL_SCRIPTS / "propose-panel-package.py"
BUILD_CLI = PANEL_SCRIPTS / "build-approved-panel-package.py"
REGISTER_CLI = PANEL_SCRIPTS / "register-approved-panel.py"


class ReleaseACompleteApprovalChainTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        from conformance.test_panel_construction_audit import (
            PanelConstructionAuditTests,
        )
        from conformance.test_panel_research_report import PanelResearchReportTests

        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        report_source = PanelResearchReportTests()
        documents = report_source.documents()
        report_source.approved_workflow_state(documents)
        review_summary = render_panel_summary(
            documents["brief"], documents["panel"]
        ).encode()
        review_html = render_panel_review_html(
            documents["brief"], documents["panel"]
        ).encode()
        review_manifest = build_panel_review_manifest(
            panel=documents["panel"], summary_bytes=review_summary,
            html_bytes=review_html, review_revision="review-v1",
            generated_at=documents["panel"]["updated_at"],
        )
        review_manifest_sha = sha256_json(review_manifest).removeprefix(
            "sha256:"
        )
        next(
            approval for approval in documents["workflow_state"]["approvals"]
            if approval["scope"] == "panel_construction"
        )["target_sha256"] = review_manifest_sha
        manifest = render_research_report(
            report_inputs=report_source.report_inputs(documents),
            documents=documents,
            generated_at="2026-07-24T12:30:00Z",
            output_dir=root / "report",
            panel_review_manifest=review_manifest,
            panel_review_summary=review_summary,
            panel_review_html=review_html,
        )
        audit = PanelConstructionAuditTests().audit()
        audit["panel_id"] = documents["panel"]["panel_id"]
        audit["panel_version"] = documents["panel"]["version"]
        audit["input_bindings"] = {
            "brief_sha256": sha256_json(documents["brief"]).removeprefix(
                "sha256:"
            ),
            "panel_sha256": sha256_json(documents["panel"]).removeprefix(
                "sha256:"
            ),
            "evidence_ledger_sha256": sha256_json(
                documents["evidence_ledger"]
            ).removeprefix("sha256:"),
            "finding_support_sha256": sha256_json(
                documents["finding_support"]
            ).removeprefix("sha256:"),
            "synthesis_matrix_sha256": sha256_json(
                documents["synthesis_matrix"]
            ).removeprefix("sha256:"),
            "report_manifest_sha256": sha256_json(manifest).removeprefix(
                "sha256:"
            ),
            "population_frame_sha256": None,
            "composition_plan_sha256": None,
            "validity_profile_sha256": None,
            "authorized_handoff_sha256": None,
        }
        package = build_audience_package(
            documents["brief"],
            documents["panel"],
            root / "direct-package",
            generator_version="1.0.0",
        )
        workflow_state = copy.deepcopy(documents["workflow_state"])
        workflow_state["bindings"].update(
            {
                "report_inputs_sha256": manifest["report_inputs_sha256"],
                "audit_sha256": construction_audit_sha256(audit),
                "package_sha256": package.package_zip_sha256,
            }
        )
        workflow_state["approvals"].append(
            {
                "scope": "package_registration",
                "status": "approved",
                "approved_by": "panel-owner",
                "approved_at": "2026-07-24T13:00:00Z",
                "target_sha256": package.package_zip_sha256,
                "note": "Approved exact proposed package bytes.",
            }
        )
        cls.documents = documents
        cls.manifest = manifest
        cls.review_manifest = review_manifest
        cls.review_manifest_sha = review_manifest_sha
        cls.audit = audit
        cls.workflow_state = workflow_state
        cls.package_path = package.package_zip_path

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(payload))
        return path

    def _write_inputs(
        self,
        root: Path,
        *,
        workflow_state: dict[str, object] | None = None,
        audit: dict[str, object] | None = None,
        documents: dict[str, dict[str, object]] | None = None,
        manifest: dict[str, object] | None = None,
    ) -> dict[str, Path]:
        current_documents = self.documents if documents is None else documents
        values = {
            "workflow-state": (
                self.workflow_state if workflow_state is None else workflow_state
            ),
            "audit": self.audit if audit is None else audit,
            "brief": current_documents["brief"],
            "panel": current_documents["panel"],
            "ledger": current_documents["evidence_ledger"],
            "finding-support": current_documents["finding_support"],
            "synthesis": current_documents["synthesis_matrix"],
            "report-manifest": self.manifest if manifest is None else manifest,
            "panel-review-manifest": self.review_manifest,
        }
        return {
            name: self._write_json(root / f"{name}.json", payload)
            for name, payload in values.items()
        }

    @staticmethod
    def _document_cli_arguments(paths: dict[str, Path]) -> list[str]:
        return [
            "--ledger",
            str(paths["ledger"]),
            "--finding-support",
            str(paths["finding-support"]),
            "--synthesis",
            str(paths["synthesis"]),
            "--report-manifest",
            str(paths["report-manifest"]),
            "--panel-review-manifest",
            str(paths["panel-review-manifest"]),
        ]

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

    def _audit_gate(
        self,
        *,
        audit: dict[str, object] | None = None,
        documents: dict[str, dict[str, object]] | None = None,
        manifest: dict[str, object] | None = None,
    ) -> dict[str, object]:
        current_documents = self.documents if documents is None else documents
        return require_passing_construction_audit_for_documents(
            self.audit if audit is None else audit,
            brief=current_documents["brief"],
            panel=current_documents["panel"],
            evidence_ledger=current_documents["evidence_ledger"],
            finding_support=current_documents["finding_support"],
            synthesis_matrix=current_documents["synthesis_matrix"],
            report_manifest=self.manifest if manifest is None else manifest,
            panel_review_manifest=self.review_manifest,
        )

    def test_document_aware_gate_returns_the_exact_current_chain(self) -> None:
        result = self._audit_gate()
        self.assertEqual(
            {
                "audit",
                "brief",
                "panel",
                "evidence_ledger",
                "finding_support",
                "synthesis_matrix",
                "report_manifest",
                "panel_review_manifest",
                "brief_sha256",
                "panel_sha256",
                "evidence_ledger_sha256",
                "finding_support_sha256",
                "synthesis_matrix_sha256",
                "report_manifest_sha256",
                "panel_review_manifest_sha256",
                "report_inputs_sha256",
                "audit_sha256",
            },
            set(result),
        )
        self.assertEqual(
            self.manifest["report_inputs_sha256"],
            result["report_inputs_sha256"],
        )
        self.assertEqual(
            construction_audit_sha256(self.audit),
            result["audit_sha256"],
        )

    def test_each_canonical_audit_input_substitution_fails_closed(self) -> None:
        substitutions = {}
        changed_documents = copy.deepcopy(self.documents)
        changed_documents["evidence_ledger"]["evidence_items"][0][
            "content_summary"
        ] = "Meaders seek implementation proof before changing core workflows."
        substitutions["evidence ledger"] = (changed_documents, self.manifest)

        changed_documents = copy.deepcopy(self.documents)
        changed_documents["finding_support"]["findings"][0][
            "analyst_note"
        ] = "Eirect support for the reviewed finding."
        substitutions["finding support"] = (changed_documents, self.manifest)

        changed_documents = copy.deepcopy(self.documents)
        changed_documents["synthesis_matrix"]["questions"][0]["findings"][0][
            "confidence_reason"
        ] = "Pne decision-relevant documented source."
        substitutions["synthesis matrix"] = (changed_documents, self.manifest)

        changed_manifest = copy.deepcopy(self.manifest)
        changed_manifest["generated_at"] = "2026-07-24T12:30:01Z"
        substitutions["report manifest"] = (self.documents, changed_manifest)

        for label, (documents, manifest) in substitutions.items():
            with self.subTest(label=label), self.assertRaises(ContractError):
                self._audit_gate(documents=documents, manifest=manifest)
            with tempfile.TemporaryDirectory() as temporary:
                paths = self._write_inputs(
                    Path(temporary) / "inputs",
                    documents=documents,
                    manifest=manifest,
                )
                result = self._run(
                    PROPOSE_CLI,
                    "--workflow-state",
                    str(paths["workflow-state"]),
                    "--audit",
                    str(paths["audit"]),
                    "--brief",
                    str(paths["brief"]),
                    "--panel",
                    str(paths["panel"]),
                    *self._document_cli_arguments(paths),
                )
                with self.subTest(label=label, boundary="proposal CLI"):
                    self.assertEqual(2, result.returncode, result.stdout)

    def test_every_boundary_rejects_missing_or_stale_common_chain(self) -> None:
        chain = self._audit_gate()
        base = {
            "panel_id": self.documents["panel"]["panel_id"],
            "panel_version": self.documents["panel"]["version"],
            "brief_sha256": chain["brief_sha256"],
            "panel_sha256": chain["panel_sha256"],
            "synthesis_sha256": chain["synthesis_matrix_sha256"],
            "report_inputs_sha256": chain["report_inputs_sha256"],
            "audit_sha256": chain["audit_sha256"],
            "panel_review_manifest_sha256": chain[
                "panel_review_manifest_sha256"
            ],
        }
        mutations: dict[str, dict[str, object]] = {}
        for scope in ("evidence_synthesis", "panel_construction"):
            state = copy.deepcopy(self.workflow_state)
            state["approvals"] = [
                row for row in state["approvals"] if row["scope"] != scope
            ]
            mutations[f"missing {scope}"] = state
            state = copy.deepcopy(self.workflow_state)
            next(
                row for row in state["approvals"] if row["scope"] == scope
            )["target_sha256"] = "0" * 64
            mutations[f"stale {scope}"] = state
        for label, key in (
            ("null report inputs", "report_inputs_sha256"),
            ("stale report inputs", "report_inputs_sha256"),
            ("stale audit", "audit_sha256"),
        ):
            state = copy.deepcopy(self.workflow_state)
            state["bindings"][key] = (
                None if label == "null report inputs" else "0" * 64
            )
            mutations[label] = state

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_inputs(root / "inputs")
            for label, state in mutations.items():
                with self.subTest(boundary="proposal", defect=label):
                    with self.assertRaises(ContractError):
                        build_package_proposal(
                            workflow_state=state,
                            construction_audit=self.audit,
                            brief_path=paths["brief"],
                            panel_path=paths["panel"],
                            ledger_path=paths["ledger"],
                            finding_support_path=paths["finding-support"],
                            synthesis_path=paths["synthesis"],
                            report_manifest_path=paths["report-manifest"],
                            panel_review_manifest_path=paths[
                                "panel-review-manifest"
                            ],
                        )
                with self.subTest(boundary="build", defect=label):
                    with self.assertRaises(ContractError):
                        require_package_build_ready(
                            **base,
                            workflow_state=state,
                            proposed_package_sha256=hashlib.sha256(
                                self.package_path.read_bytes()
                            ).hexdigest(),
                        )
                with self.subTest(boundary="registration", defect=label):
                    with self.assertRaises(ContractError):
                        require_registration_ready(
                            **base,
                            workflow_state=state,
                            package_sha256=hashlib.sha256(
                                self.package_path.read_bytes()
                            ).hexdigest(),
                        )

    def test_audit_and_workflow_substitutions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_inputs(Path(temporary) / "inputs")
            changed_audit = copy.deepcopy(self.audit)
            changed_audit["checks"][0]["message"] = (
                "Bpproved evidence only."
            )
            with self.assertRaisesRegex(ContractError, "audit"):
                build_package_proposal(
                    workflow_state=self.workflow_state,
                    construction_audit=changed_audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    ledger_path=paths["ledger"],
                    finding_support_path=paths["finding-support"],
                    synthesis_path=paths["synthesis"],
                    report_manifest_path=paths["report-manifest"],
                    panel_review_manifest_path=paths[
                        "panel-review-manifest"
                    ],
                )

            changed_workflow = copy.deepcopy(self.workflow_state)
            changed_workflow["bindings"]["audit_sha256"] = "0" * 64
            with self.assertRaisesRegex(ContractError, "audit"):
                build_package_proposal(
                    workflow_state=changed_workflow,
                    construction_audit=self.audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    ledger_path=paths["ledger"],
                    finding_support_path=paths["finding-support"],
                    synthesis_path=paths["synthesis"],
                    report_manifest_path=paths["report-manifest"],
                    panel_review_manifest_path=paths[
                        "panel-review-manifest"
                    ],
                )

    def test_registration_with_only_package_approval_has_no_side_effect(self) -> None:
        state = copy.deepcopy(self.workflow_state)
        state["approvals"] = [
            row
            for row in state["approvals"]
            if row["scope"] == "package_registration"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_inputs(root / "inputs", workflow_state=state)
            library = root / "library"
            result = self._run(
                REGISTER_CLI,
                "--workflow-state",
                str(paths["workflow-state"]),
                "--audit",
                str(paths["audit"]),
                "--package",
                str(self.package_path),
                *self._document_cli_arguments(paths),
                "--library-root",
                str(library),
            )
            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("evidence_synthesis", result.stdout)
            self.assertFalse(library.exists())

    def test_valid_gated_clis_preserve_direct_package_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_inputs(root / "inputs")
            document_arguments = self._document_cli_arguments(paths)
            proposal = self._run(
                PROPOSE_CLI,
                "--workflow-state",
                str(paths["workflow-state"]),
                "--audit",
                str(paths["audit"]),
                "--brief",
                str(paths["brief"]),
                "--panel",
                str(paths["panel"]),
                *document_arguments,
            )
            self.assertEqual(0, proposal.returncode, proposal.stdout)
            self.assertEqual(
                hashlib.sha256(self.package_path.read_bytes()).hexdigest(),
                json.loads(proposal.stdout)["package_sha256"],
            )
            output = root / "gated-package"
            built = self._run(
                BUILD_CLI,
                "--workflow-state",
                str(paths["workflow-state"]),
                "--audit",
                str(paths["audit"]),
                "--brief",
                str(paths["brief"]),
                "--panel",
                str(paths["panel"]),
                *document_arguments,
                "--output-dir",
                str(output),
            )
            self.assertEqual(0, built.returncode, built.stdout)
            self.assertEqual(
                self.package_path.read_bytes(),
                (output / "audience-panel-package.zip").read_bytes(),
            )
            library = root / "library"
            registered = self._run(
                REGISTER_CLI,
                "--workflow-state",
                str(paths["workflow-state"]),
                "--audit",
                str(paths["audit"]),
                "--package",
                str(output / "audience-panel-package.zip"),
                *document_arguments,
                "--library-root",
                str(library),
            )
            self.assertEqual(0, registered.returncode, registered.stdout)
            self.assertTrue(library.is_dir())


if __name__ == "__main__":
    unittest.main()
