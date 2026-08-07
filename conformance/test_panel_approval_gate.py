from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(PANEL_SCRIPTS))
sys.path.insert(0, str(LAB_SCRIPTS))

from audience_lab.audience_library import list_panels  # noqa: E402
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_panel_builder import approval_gate  # noqa: E402
from audience_panel_builder.approval_gate import (  # noqa: E402
    PACKAGE_PROPOSAL_SCHEMA_VERSION,
    build_package_proposal,
    require_package_build_ready,
    require_registration_ready,
)
from audience_panel_builder.common import ContractError, canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.construction_audit import construction_audit_sha256  # noqa: E402
from audience_panel_builder.reporting import render_research_report  # noqa: E402
from audience_panel_builder.review import (  # noqa: E402
    build_panel_review_manifest,
    render_panel_review_html,
    render_panel_summary,
)


PROPOSE_CLI = PANEL_SCRIPTS / "propose-panel-package.py"
BUILD_CLI = PANEL_SCRIPTS / "build-approved-panel-package.py"
REGISTER_CLI = PANEL_SCRIPTS / "register-approved-panel.py"
VALIDATE_AUDIT_CLI = PANEL_SCRIPTS / "validate-panel-construction-audit.py"


def load_register_cli_module():
    spec = importlib.util.spec_from_file_location(
        "register_approved_panel_cli",
        REGISTER_CLI,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load register-approved-panel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PanelApprovalGateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        from conformance.test_panel_construction_audit import (
            PanelConstructionAuditTests,
        )
        from conformance.test_panel_research_report import PanelResearchReportTests

        report_source = PanelResearchReportTests()
        self.documents = report_source.documents()
        report_source.approved_workflow_state(self.documents)
        self.brief = self.documents["brief"]
        self.panel = self.documents["panel"]
        self.brief_sha = sha256_json(self.brief).removeprefix("sha256:")
        self.panel_sha = sha256_json(self.panel).removeprefix("sha256:")
        self.setup_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.setup_temp.cleanup)
        review_summary = render_panel_summary(self.brief, self.panel).encode()
        review_html = render_panel_review_html(self.brief, self.panel).encode()
        self.panel_review_manifest = build_panel_review_manifest(
            panel=self.panel,
            summary_bytes=review_summary,
            html_bytes=review_html,
            review_revision="review-v1",
            generated_at=str(self.panel["updated_at"]),
        )
        self.panel_review_manifest_sha = sha256_json(
            self.panel_review_manifest
        ).removeprefix("sha256:")
        self.report_manifest = render_research_report(
            report_inputs=report_source.report_inputs(self.documents),
            documents=self.documents,
            generated_at="2026-07-24T12:30:00Z",
            output_dir=Path(self.setup_temp.name) / "report",
            panel_review_manifest=self.panel_review_manifest,
            panel_review_summary=review_summary,
            panel_review_html=review_html,
        )
        self.audit = PanelConstructionAuditTests().audit()
        self.audit["panel_id"] = self.panel["panel_id"]
        self.audit["panel_version"] = self.panel["version"]
        self.audit["input_bindings"] = {
            "brief_sha256": self.brief_sha,
            "panel_sha256": self.panel_sha,
            "evidence_ledger_sha256": sha256_json(
                self.documents["evidence_ledger"]
            ).removeprefix("sha256:"),
            "finding_support_sha256": sha256_json(
                self.documents["finding_support"]
            ).removeprefix("sha256:"),
            "synthesis_matrix_sha256": sha256_json(
                self.documents["synthesis_matrix"]
            ).removeprefix("sha256:"),
            "report_manifest_sha256": sha256_json(
                self.report_manifest
            ).removeprefix("sha256:"),
            "population_frame_sha256": None,
            "composition_plan_sha256": None,
            "validity_profile_sha256": None,
            "authorized_handoff_sha256": None,
        }
        self.synthesis_sha = self.audit["input_bindings"][
            "synthesis_matrix_sha256"
        ]
        self.report_inputs_sha = self.report_manifest["report_inputs_sha256"]
        self.audit_sha = construction_audit_sha256(self.audit)

    @staticmethod
    def approval(scope: str, target: str, *, status: str = "approved") -> dict[str, object]:
        decided = status != "pending"
        return {
            "scope": scope,
            "status": status,
            "approved_by": "panel-owner" if decided else "",
            "approved_at": "2026-07-24T12:00:00Z" if decided else "",
            "target_sha256": target,
            "note": "Approved exact bytes." if decided else "",
        }

    def workflow(
        self,
        *,
        state: str = "approved",
        package_sha: str | None = None,
        panel_approval: bool = True,
        package_approval: bool = False,
    ) -> dict[str, object]:
        approvals = [
            self.approval("evidence_synthesis", self.synthesis_sha),
        ]
        if panel_approval:
            approvals.append(
                self.approval(
                    "panel_construction",
                    self.panel_review_manifest_sha,
                )
            )
        if package_approval:
            assert package_sha is not None
            approvals.append(self.approval("package_registration", package_sha))
        return {
            "schema_version": "panel-workflow-state-v1",
            "workflow_id": "operations-leaders-build",
            "panel_id": self.panel["panel_id"],
            "panel_version": self.panel["version"],
            "state": state,
            "updated_at": "2026-07-24T12:00:00Z",
            "approvals": approvals,
            "bindings": {
                "brief_sha256": self.brief_sha,
                "panel_sha256": self.panel_sha,
                "report_inputs_sha256": self.report_inputs_sha,
                "audit_sha256": self.audit_sha,
                "package_sha256": package_sha,
            },
        }

    def write_inputs(
        self,
        root: Path,
        workflow: dict[str, object],
        *,
        documents: dict[str, dict[str, object]] | None = None,
        audit: dict[str, object] | None = None,
        report_manifest: dict[str, object] | None = None,
        panel_review_manifest: dict[str, object] | None = None,
    ) -> dict[str, Path]:
        root.mkdir(parents=True, exist_ok=True)
        current_documents = self.documents if documents is None else documents
        values = {
            "brief": current_documents["brief"],
            "panel": current_documents["panel"],
            "audit": self.audit if audit is None else audit,
            "ledger": current_documents["evidence_ledger"],
            "finding-support": current_documents["finding_support"],
            "synthesis": current_documents["synthesis_matrix"],
            "report-manifest": (
                self.report_manifest
                if report_manifest is None
                else report_manifest
            ),
            "panel-review-manifest": (
                self.panel_review_manifest
                if panel_review_manifest is None
                else panel_review_manifest
            ),
            "workflow-state": workflow,
        }
        paths: dict[str, Path] = {}
        for name, value in values.items():
            path = root / f"{name}.json"
            path.write_bytes(canonical_json_bytes(value))
            paths[name] = path
        return paths

    @staticmethod
    def document_arguments(paths: dict[str, Path]) -> list[str]:
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
    def proposal_path_arguments(paths: dict[str, Path]) -> dict[str, Path]:
        return {
            "ledger_path": paths["ledger"],
            "finding_support_path": paths["finding-support"],
            "synthesis_path": paths["synthesis"],
            "report_manifest_path": paths["report-manifest"],
            "panel_review_manifest_path": paths["panel-review-manifest"],
        }

    def common_gate_arguments(self) -> dict[str, str]:
        return {
            "panel_id": self.panel["panel_id"],
            "panel_version": self.panel["version"],
            "brief_sha256": self.brief_sha,
            "panel_sha256": self.panel_sha,
            "synthesis_sha256": self.synthesis_sha,
            "report_inputs_sha256": self.report_inputs_sha,
            "audit_sha256": self.audit_sha,
            "panel_review_manifest_sha256": self.panel_review_manifest_sha,
        }

    @staticmethod
    def run_cli(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_proposal_is_exact_deterministic_digest_document_and_temp_is_removed(self) -> None:
        captured: list[Path] = []
        original = tempfile.TemporaryDirectory

        def temporary_directory(*args, **kwargs):
            context = original(*args, **kwargs)
            captured.append(Path(context.name))
            return context

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self.write_inputs(root, self.workflow())
            with mock.patch.object(
                approval_gate.tempfile,
                "TemporaryDirectory",
                side_effect=temporary_directory,
            ):
                first = build_package_proposal(
                    workflow_state=self.workflow(),
                    construction_audit=self.audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    **self.proposal_path_arguments(paths),
                )
            second = build_package_proposal(
                workflow_state=copy.deepcopy(self.workflow()),
                construction_audit=copy.deepcopy(self.audit),
                brief_path=paths["brief"],
                panel_path=paths["panel"],
                **self.proposal_path_arguments(paths),
            )
            direct = build_audience_package(self.brief, self.panel, root / "direct")
            self.assertEqual(first, second)
            self.assertEqual(
                {
                    "schema_version",
                    "panel_id",
                    "panel_version",
                    "brief_sha256",
                    "panel_sha256",
                    "audit_sha256",
                    "panel_review_manifest_sha256",
                    "package_manifest_sha256",
                    "package_manifest_byte_count",
                    "package_sha256",
                    "package_byte_count",
                },
                set(first),
            )
            self.assertEqual(PACKAGE_PROPOSAL_SCHEMA_VERSION, first["schema_version"])
            self.assertEqual(self.brief_sha, first["brief_sha256"])
            self.assertEqual(self.panel_sha, first["panel_sha256"])
            self.assertEqual(self.audit_sha, first["audit_sha256"])
            self.assertEqual(
                self.panel_review_manifest_sha,
                first["panel_review_manifest_sha256"],
            )
            self.assertEqual(direct.package_manifest_sha256, first["package_manifest_sha256"])
            self.assertEqual(direct.package_zip_sha256, first["package_sha256"])
            self.assertEqual(direct.manifest_path.stat().st_size, first["package_manifest_byte_count"])
            self.assertEqual(direct.package_zip_path.stat().st_size, first["package_byte_count"])
        self.assertTrue(captured)
        self.assertTrue(all(not path.exists() for path in captured))

    def test_proposal_rejects_every_nonapproved_state_missing_scope_and_failed_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self.write_inputs(root, self.workflow())
            for state in ("dogfood", "provisional", "needs_refresh", "retired", "draft"):
                with self.subTest(state=state), self.assertRaisesRegex(ContractError, "approved"):
                    build_package_proposal(
                        workflow_state=self.workflow(state=state),
                        construction_audit=self.audit,
                        brief_path=paths["brief"],
                        panel_path=paths["panel"],
                        **self.proposal_path_arguments(paths),
                    )
            with self.assertRaisesRegex(ContractError, "panel_construction"):
                build_package_proposal(
                    workflow_state=self.workflow(panel_approval=False),
                    construction_audit=self.audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    **self.proposal_path_arguments(paths),
                )
            failed = copy.deepcopy(self.audit)
            failed["checks"][0]["status"] = "fail"
            failed["result"] = "fail"
            failed_sha = construction_audit_sha256(failed)
            failed_state = self.workflow()
            failed_state["bindings"]["audit_sha256"] = failed_sha
            with self.assertRaisesRegex(ContractError, "passing"):
                build_package_proposal(
                    workflow_state=failed_state,
                    construction_audit=failed,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    **self.proposal_path_arguments(paths),
                )
            stale_audit = copy.deepcopy(self.audit)
            stale_audit["input_bindings"]["brief_sha256"] = "0" * 64
            stale_state = self.workflow()
            stale_state["bindings"]["audit_sha256"] = construction_audit_sha256(stale_audit)
            with self.assertRaisesRegex(ContractError, "expected binding"):
                build_package_proposal(
                    workflow_state=stale_state,
                    construction_audit=stale_audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    **self.proposal_path_arguments(paths),
                )

    def test_valid_failed_audit_is_reported_but_cannot_cross_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = build_audience_package(
                self.brief,
                self.panel,
                root / "direct-package",
            )
            failed_audit = copy.deepcopy(self.audit)
            failed_audit["checks"][0]["status"] = "fail"
            failed_audit["result"] = "fail"
            failed_audit_sha = construction_audit_sha256(failed_audit)
            state = self.workflow(
                package_sha=package.package_zip_sha256,
                package_approval=True,
            )
            state["bindings"]["audit_sha256"] = failed_audit_sha
            paths = self.write_inputs(
                root / "inputs",
                state,
                audit=failed_audit,
            )

            validated = self.run_cli(
                VALIDATE_AUDIT_CLI,
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
            )
            expected_validation = {
                "valid": True,
                "result": "fail",
                "audit_sha256": failed_audit_sha,
            }
            self.assertEqual(0, validated.returncode, validated.stdout)
            self.assertEqual(expected_validation, json.loads(validated.stdout))
            self.assertEqual(
                canonical_json_bytes(expected_validation).decode(),
                validated.stdout,
            )

            files_before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            proposed = self.run_cli(
                PROPOSE_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
            )
            self.assertEqual(2, proposed.returncode, proposed.stdout)
            self.assertIn("not passing", json.loads(proposed.stdout)["message"])
            self.assertEqual(
                files_before,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
            )

            blocked_output = root / "blocked-package"
            built = self.run_cli(
                BUILD_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
                "--output-dir", str(blocked_output),
            )
            self.assertEqual(2, built.returncode, built.stdout)
            self.assertIn("not passing", json.loads(built.stdout)["message"])
            self.assertFalse(blocked_output.exists())

            blocked_library = root / "blocked-library"
            registered = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--package", str(package.package_zip_path),
                *self.document_arguments(paths),
                "--library-root", str(blocked_library),
            )
            self.assertEqual(2, registered.returncode, registered.stdout)
            self.assertIn(
                "not passing",
                json.loads(registered.stdout)["message"],
            )
            self.assertFalse(blocked_library.exists())

    def test_pending_and_rejected_scopes_cannot_propose_build_or_register(self) -> None:
        package_sha = "a" * 64
        with tempfile.TemporaryDirectory() as temp:
            paths = self.write_inputs(Path(temp), self.workflow())
            for status in ("pending", "rejected"):
                proposal_state = self.workflow()
                proposal_state["approvals"][1] = self.approval(
                    "panel_construction",
                    self.panel_review_manifest_sha,
                    status=status,
                )
                with self.subTest(operation="propose", status=status), self.assertRaisesRegex(
                    ContractError,
                    "panel_construction",
                ):
                    build_package_proposal(
                        workflow_state=proposal_state,
                        construction_audit=self.audit,
                        brief_path=paths["brief"],
                        panel_path=paths["panel"],
                        **self.proposal_path_arguments(paths),
                    )

                canonical_state = self.workflow(
                    package_sha=package_sha,
                    package_approval=True,
                )
                canonical_state["approvals"][-1] = self.approval(
                    "package_registration",
                    package_sha,
                    status=status,
                )
                with self.subTest(operation="build", status=status), self.assertRaisesRegex(
                    ContractError,
                    "must be approved",
                ):
                    require_package_build_ready(
                        workflow_state=canonical_state,
                        **self.common_gate_arguments(),
                        proposed_package_sha256=package_sha,
                    )
                with self.subTest(operation="register", status=status), self.assertRaisesRegex(
                    ContractError,
                    "must be approved",
                ):
                    require_registration_ready(
                        workflow_state=canonical_state,
                        **self.common_gate_arguments(),
                        package_sha256=package_sha,
                    )

    def test_build_gate_requires_exact_bindings_scopes_and_approved_state(self) -> None:
        package_sha = "a" * 64
        state = self.workflow(package_sha=package_sha, package_approval=True)
        require_package_build_ready(
            workflow_state=state,
            **self.common_gate_arguments(),
            proposed_package_sha256=package_sha,
        )
        cases = (
            ("brief", {"brief_sha256": "b" * 64}, "brief"),
            ("panel", {"panel_sha256": "b" * 64}, "panel"),
            ("synthesis", {"synthesis_sha256": "b" * 64}, "evidence_synthesis"),
            ("report inputs", {"report_inputs_sha256": "b" * 64}, "report inputs"),
            ("audit", {"audit_sha256": "b" * 64}, "audit"),
            ("package", {"proposed_package_sha256": "b" * 64}, "package"),
        )
        base = {
            "workflow_state": state,
            **self.common_gate_arguments(),
            "proposed_package_sha256": package_sha,
        }
        for name, change, pattern in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, pattern):
                require_package_build_ready(**{**base, **change})
        stale = copy.deepcopy(state)
        stale["approvals"][-1]["target_sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "exact target"):
            require_package_build_ready(**{**base, "workflow_state": stale})
        missing = copy.deepcopy(state)
        missing["approvals"] = [
            item for item in missing["approvals"] if item["scope"] != "package_registration"
        ]
        with self.assertRaisesRegex(ContractError, "package_registration"):
            require_package_build_ready(**{**base, "workflow_state": missing})

        stale_panel_only = copy.deepcopy(state)
        next(
            approval
            for approval in stale_panel_only["approvals"]
            if approval["scope"] == "panel_construction"
        )["target_sha256"] = self.panel_sha
        with self.assertRaisesRegex(ContractError, "exact target"):
            require_package_build_ready(
                **{**base, "workflow_state": stale_panel_only}
            )

    def test_proposal_rejects_swapped_review_manifest_even_for_same_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            swapped = copy.deepcopy(self.panel_review_manifest)
            swapped["review_revision"] = "review-v2"
            paths = self.write_inputs(
                root,
                self.workflow(),
                panel_review_manifest=swapped,
            )
            with self.assertRaisesRegex(
                ContractError,
                "panel-review-manifest.json",
            ):
                build_package_proposal(
                    workflow_state=self.workflow(),
                    construction_audit=self.audit,
                    brief_path=paths["brief"],
                    panel_path=paths["panel"],
                    **self.proposal_path_arguments(paths),
                )

    def test_registration_gate_rejects_wrong_digest_stale_approval_and_nonapproved_states(self) -> None:
        package_sha = "a" * 64
        state = self.workflow(package_sha=package_sha, package_approval=True)
        require_registration_ready(
            workflow_state=state,
            **self.common_gate_arguments(),
            package_sha256=package_sha,
        )
        with self.assertRaisesRegex(ContractError, "package"):
            require_registration_ready(
                workflow_state=state,
                **self.common_gate_arguments(),
                package_sha256="b" * 64,
            )
        stale = copy.deepcopy(state)
        stale["approvals"][-1]["target_sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "exact target"):
            require_registration_ready(
                workflow_state=stale,
                **self.common_gate_arguments(),
                package_sha256=package_sha,
            )
        for status in ("dogfood", "provisional", "needs_refresh", "retired", "draft"):
            with self.subTest(status=status), self.assertRaisesRegex(ContractError, "approved"):
                require_registration_ready(
                    workflow_state={**state, "state": status},
                    **self.common_gate_arguments(),
                    package_sha256=package_sha,
                )

    def test_propose_cli_prints_only_canonical_proposal_and_creates_no_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self.write_inputs(root, self.workflow())
            result = self.run_cli(
                PROPOSE_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(PACKAGE_PROPOSAL_SCHEMA_VERSION, payload["schema_version"])
            self.assertEqual(canonical_json_bytes(payload).decode(), result.stdout)
            self.assertEqual(
                {
                    "brief.json",
                    "panel.json",
                    "audit.json",
                    "ledger.json",
                    "finding-support.json",
                    "synthesis.json",
                    "report-manifest.json",
                    "panel-review-manifest.json",
                    "workflow-state.json",
                },
                {path.name for path in root.iterdir()},
            )

    def test_build_cli_has_no_output_before_package_approval_and_matches_direct_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial = self.write_inputs(root, self.workflow())
            proposal = build_package_proposal(
                workflow_state=self.workflow(),
                construction_audit=self.audit,
                brief_path=initial["brief"],
                panel_path=initial["panel"],
                **self.proposal_path_arguments(initial),
            )
            denied_paths = self.write_inputs(root / "denied-inputs", self.workflow())
            denied_output = root / "denied-package"
            denied = self.run_cli(
                BUILD_CLI,
                "--workflow-state", str(denied_paths["workflow-state"]),
                "--audit", str(denied_paths["audit"]),
                "--brief", str(denied_paths["brief"]),
                "--panel", str(denied_paths["panel"]),
                *self.document_arguments(denied_paths),
                "--output-dir", str(denied_output),
            )
            self.assertEqual(2, denied.returncode)
            self.assertFalse(denied_output.exists())
            self.assertEqual(
                {"status": "error", "error": "validation", "message": "package_registration approval is required"},
                json.loads(denied.stdout),
            )

            approved = self.workflow(
                package_sha=str(proposal["package_sha256"]),
                package_approval=True,
            )
            paths = self.write_inputs(root / "approved-inputs", approved)
            gated_output = root / "gated-package"
            built = self.run_cli(
                BUILD_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
                "--output-dir", str(gated_output),
            )
            self.assertEqual(0, built.returncode, built.stderr)
            built_payload = json.loads(built.stdout)
            self.assertEqual(
                {
                    "status", "output_dir", "panel_id", "panel_version",
                    "package_manifest_sha256", "package_zip_sha256", "package_zip_path",
                },
                set(built_payload),
            )
            direct = build_audience_package(self.brief, self.panel, root / "direct-package")
            self.assertEqual(
                direct.package_zip_path.read_bytes(),
                (gated_output / "audience-panel-package.zip").read_bytes(),
            )
            self.assertEqual(
                hashlib.sha256(direct.package_zip_path.read_bytes()).hexdigest(),
                built_payload["package_zip_sha256"],
            )
            before = (gated_output / "audience-panel-package.zip").read_bytes()
            collision = self.run_cli(
                BUILD_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--brief", str(paths["brief"]),
                "--panel", str(paths["panel"]),
                *self.document_arguments(paths),
                "--output-dir", str(gated_output),
            )
            self.assertEqual(6, collision.returncode)
            self.assertEqual(
                {"status", "error", "message"},
                set(json.loads(collision.stdout)),
            )
            self.assertEqual("package_safety", json.loads(collision.stdout)["error"])
            self.assertEqual(before, (gated_output / "audience-panel-package.zip").read_bytes())

    def test_registration_cli_is_side_effect_free_on_gate_failure_then_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = build_audience_package(self.brief, self.panel, root / "package")
            package_sha = hashlib.sha256(package.package_zip_path.read_bytes()).hexdigest()
            library = root / "library"
            denied_state = self.workflow()
            denied_paths = self.write_inputs(root / "denied", denied_state)
            denied = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(denied_paths["workflow-state"]),
                "--audit", str(denied_paths["audit"]),
                "--package", str(package.package_zip_path),
                *self.document_arguments(denied_paths),
                "--library-root", str(library),
            )
            self.assertEqual(2, denied.returncode)
            self.assertFalse(library.exists())
            denied_payload = json.loads(denied.stdout)
            self.assertEqual(
                {
                    "status": "error",
                    "error": "validation",
                    "message": "package_registration approval is required",
                },
                denied_payload,
            )
            self.assertEqual(canonical_json_bytes(denied_payload).decode(), denied.stdout)

            approved = self.workflow(package_sha=package_sha, package_approval=True)
            paths = self.write_inputs(root / "approved", approved)
            registered = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--package", str(package.package_zip_path),
                *self.document_arguments(paths),
                "--library-root", str(library),
            )
            self.assertEqual(0, registered.returncode, registered.stderr)
            payload = json.loads(registered.stdout)
            self.assertEqual({"status", "panel"}, set(payload))
            self.assertEqual("registered", payload["status"])
            self.assertEqual(
                [self.panel["panel_id"]],
                [item["panel_id"] for item in list_panels(library_root=library)["panels"]],
            )
            repeated = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(paths["workflow-state"]),
                "--audit", str(paths["audit"]),
                "--package", str(package.package_zip_path),
                *self.document_arguments(paths),
                "--library-root", str(library),
            )
            self.assertEqual(0, repeated.returncode, repeated.stderr)
            self.assertEqual("already_registered", json.loads(repeated.stdout)["status"])

            changed_panel = copy.deepcopy(self.panel)
            changed_panel["panel_name"] = "Changed without a version bump"
            changed_package = build_audience_package(
                self.brief,
                changed_panel,
                root / "changed-package",
            )
            changed_panel_sha = sha256_json(changed_panel).removeprefix("sha256:")
            changed_documents = copy.deepcopy(self.documents)
            changed_documents["panel"] = changed_panel
            changed_documents["workflow_state"]["bindings"][
                "panel_sha256"
            ] = changed_panel_sha
            from conformance.test_panel_research_report import (
                PanelResearchReportTests,
            )

            changed_summary = render_panel_summary(
                changed_documents["brief"], changed_panel
            ).encode()
            changed_html = render_panel_review_html(
                changed_documents["brief"], changed_panel
            ).encode()
            changed_review_manifest = build_panel_review_manifest(
                panel=changed_panel,
                summary_bytes=changed_summary,
                html_bytes=changed_html,
                review_revision="review-v2",
                generated_at=str(changed_panel["updated_at"]),
            )
            changed_review_sha = sha256_json(
                changed_review_manifest
            ).removeprefix("sha256:")
            next(
                row
                for row in changed_documents["workflow_state"]["approvals"]
                if row["scope"] == "panel_construction"
            )["target_sha256"] = changed_review_sha
            changed_manifest = render_research_report(
                report_inputs=PanelResearchReportTests().report_inputs(
                    changed_documents
                ),
                documents=changed_documents,
                generated_at="2026-07-24T12:30:00Z",
                output_dir=root / "changed-report",
                panel_review_manifest=changed_review_manifest,
                panel_review_summary=changed_summary,
                panel_review_html=changed_html,
            )
            changed_audit = copy.deepcopy(self.audit)
            changed_audit["input_bindings"]["panel_sha256"] = changed_panel_sha
            changed_audit["input_bindings"]["report_manifest_sha256"] = (
                sha256_json(changed_manifest).removeprefix("sha256:")
            )
            changed_audit_sha = construction_audit_sha256(changed_audit)
            changed_state = self.workflow(
                package_sha=changed_package.package_zip_sha256,
                package_approval=True,
            )
            changed_state["bindings"]["panel_sha256"] = changed_panel_sha
            changed_state["bindings"]["report_inputs_sha256"] = (
                changed_manifest["report_inputs_sha256"]
            )
            changed_state["bindings"]["audit_sha256"] = changed_audit_sha
            changed_state["approvals"][1]["target_sha256"] = changed_review_sha
            changed_root = root / "changed"
            changed_paths = self.write_inputs(
                changed_root,
                changed_state,
                documents=changed_documents,
                audit=changed_audit,
                report_manifest=changed_manifest,
                panel_review_manifest=changed_review_manifest,
            )
            conflict = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(changed_paths["workflow-state"]),
                "--audit", str(changed_paths["audit"]),
                "--package", str(changed_package.package_zip_path),
                *self.document_arguments(changed_paths),
                "--library-root", str(library),
            )
            self.assertEqual(3, conflict.returncode)
            self.assertEqual(
                "immutable_version_conflict",
                json.loads(conflict.stdout)["error"],
            )

    def test_registration_rejects_unrelated_audit_and_packaged_binding_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = build_audience_package(self.brief, self.panel, root / "package")

            unrelated_audit = copy.deepcopy(self.audit)
            unrelated_audit["input_bindings"]["brief_sha256"] = "1" * 64
            unrelated_audit["input_bindings"]["panel_sha256"] = "2" * 64
            unrelated_state = self.workflow(
                package_sha=package.package_zip_sha256,
                package_approval=True,
            )
            unrelated_state["bindings"]["audit_sha256"] = construction_audit_sha256(
                unrelated_audit
            )
            unrelated_paths = self.write_inputs(root / "unrelated", unrelated_state)
            unrelated_paths["audit"].write_bytes(canonical_json_bytes(unrelated_audit))
            unrelated_library = root / "unrelated-library"
            unrelated = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(unrelated_paths["workflow-state"]),
                "--audit", str(unrelated_paths["audit"]),
                "--package", str(package.package_zip_path),
                *self.document_arguments(unrelated_paths),
                "--library-root", str(unrelated_library),
            )
            self.assertEqual(2, unrelated.returncode)
            self.assertIn("expected binding", json.loads(unrelated.stdout)["message"])
            self.assertFalse(unrelated_library.exists())

            changed_panel = copy.deepcopy(self.panel)
            changed_panel["panel_name"] = "Different packaged panel"
            changed_package = build_audience_package(
                self.brief,
                changed_panel,
                root / "different-package",
            )
            mismatch_state = self.workflow(
                package_sha=changed_package.package_zip_sha256,
                package_approval=True,
            )
            mismatch_paths = self.write_inputs(root / "mismatch", mismatch_state)
            mismatch_library = root / "mismatch-library"
            mismatch = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(mismatch_paths["workflow-state"]),
                "--audit", str(mismatch_paths["audit"]),
                "--package", str(changed_package.package_zip_path),
                *self.document_arguments(mismatch_paths),
                "--library-root", str(mismatch_library),
            )
            self.assertEqual(2, mismatch.returncode)
            self.assertIn(
                "canonical panel bytes",
                json.loads(mismatch.stdout)["message"],
            )
            self.assertFalse(mismatch_library.exists())

            changed_brief = copy.deepcopy(self.brief)
            changed_brief["evidence_sources"][0]["source_label"] = (
                "Different packaged source label"
            )
            changed_brief_package = build_audience_package(
                changed_brief,
                self.panel,
                root / "different-brief-package",
            )
            brief_mismatch_state = self.workflow(
                package_sha=changed_brief_package.package_zip_sha256,
                package_approval=True,
            )
            brief_mismatch_paths = self.write_inputs(
                root / "brief-mismatch",
                brief_mismatch_state,
            )
            brief_mismatch_library = root / "brief-mismatch-library"
            brief_mismatch = self.run_cli(
                REGISTER_CLI,
                "--workflow-state", str(brief_mismatch_paths["workflow-state"]),
                "--audit", str(brief_mismatch_paths["audit"]),
                "--package", str(changed_brief_package.package_zip_path),
                *self.document_arguments(brief_mismatch_paths),
                "--library-root", str(brief_mismatch_library),
            )
            self.assertEqual(2, brief_mismatch.returncode)
            self.assertIn(
                "brief.json",
                json.loads(brief_mismatch.stdout)["message"],
            )
            self.assertFalse(brief_mismatch_library.exists())

    def test_registration_passes_the_exact_validated_snapshot_to_the_library(self) -> None:
        module = load_register_cli_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = build_audience_package(self.brief, self.panel, root / "package")
            original = package.package_zip_path.read_bytes()
            state = self.workflow(
                package_sha=hashlib.sha256(original).hexdigest(),
                package_approval=True,
            )
            captured: list[bytes] = []
            read_paths: list[Path] = []
            original_read_bytes = Path.read_bytes

            def track_read(path):
                read_paths.append(path)
                return original_read_bytes(path)

            def consume_snapshot(source, *, library_root=None):
                self.assertIsInstance(source, bytes)
                captured.append(source)
                package.package_zip_path.write_bytes(b"swapped-after-snapshot")
                return {"status": "registered", "panel": {"panel_id": self.panel["panel_id"]}}

            with (
                mock.patch.object(Path, "read_bytes", track_read),
                mock.patch.object(module, "register_package", side_effect=consume_snapshot),
            ):
                result = module._register_approved_package(
                    workflow_state=state,
                    construction_audit=self.audit,
                    evidence_ledger=self.documents["evidence_ledger"],
                    finding_support=self.documents["finding_support"],
                    synthesis_matrix=self.documents["synthesis_matrix"],
                    report_manifest=self.report_manifest,
                    panel_review_manifest=self.panel_review_manifest,
                    package_path=package.package_zip_path,
                    library_root=root / "library",
                )
            self.assertEqual("registered", result["status"])
            self.assertEqual([original], captured)
            self.assertEqual([package.package_zip_path.absolute()], read_paths)
            self.assertEqual(b"swapped-after-snapshot", package.package_zip_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
