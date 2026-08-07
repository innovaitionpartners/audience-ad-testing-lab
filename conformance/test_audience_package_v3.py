from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
BUILD_CLI = SCRIPTS / "build-audience-package-v3.py"
FIXTURE = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-package-v3"
    / "approved-package-inputs.json"
)
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(PANEL_SCRIPTS))

from audience_lab.audience_package import (  # noqa: E402
    MAX_ARCHIVE_ENTRIES,
    PackageSafetyError,
    PackageValidationError,
    read_safe_archive_members,
)
from audience_lab.audience_package_dispatch import (  # noqa: E402
    validate_supported_audience_package,
)
from audience_lab.audience_package_v3 import (  # noqa: E402
    ARCHIVE_FILES_V3,
    AUTHORIZED_RUNTIME_AUTHORITY_MEMBER,
    GENERATOR_VERSION_V3,
    PACKAGE_FILES_V3,
    PANEL_REVIEW_MANIFEST_MEMBER,
    PACKAGE_SCHEMA_VERSION_V3,
    TIER3_ARCHIVE_FILES_V3,
    build_audience_package_v3,
    validate_package_archive_v3,
)
from audience_lab import audience_package, audience_package_v3  # noqa: E402
from audience_lab.audience_research_v3 import _v2_projection  # noqa: E402
from audience_lab.audience_resolution_v3 import (  # noqa: E402
    resolve_audience_v3,
)
from conformance import test_audience_research_v3 as research_harness  # noqa: E402
from audience_panel_builder import review as panel_review  # noqa: E402


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def bare_digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def prefixed_digest(value: object) -> str:
    return "sha256:" + bare_digest(value)


class AudiencePackageV3Test(unittest.TestCase):
    """Executable contract tests for the Release B2 package boundary."""

    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _materialize(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
        documents: dict[str, object] | None = None,
    ) -> dict[str, Path]:
        source = self.fixture["bundles"][bundle] if documents is None else documents
        root.mkdir(parents=True)
        paths: dict[str, Path] = {}
        for key, filename in self.fixture["inputs"].items():
            path = root / filename
            value = source[key]
            data = value.encode("utf-8") if key == "report" else canonical_bytes(value)
            path.write_bytes(data)
            paths[key] = path
        if "panel_review_manifest" in source:
            path = root / PANEL_REVIEW_MANIFEST_MEMBER
            path.write_bytes(canonical_bytes(source["panel_review_manifest"]))
            paths["panel_review_manifest"] = path
        if "authorized_runtime_authority" in source:
            authority_path = root / AUTHORIZED_RUNTIME_AUTHORITY_MEMBER
            authority_path.write_bytes(
                canonical_bytes(
                    source["authorized_runtime_authority"]
                )
            )
            paths["authorized_runtime_authority"] = authority_path
        expected = set(self.fixture["inputs"])
        if "authorized_runtime_authority" in source:
            expected.add("authorized_runtime_authority")
        if "panel_review_manifest" in source:
            expected.add("panel_review_manifest")
        self.assertEqual(expected, set(paths))
        return paths

    def _build(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
        documents: dict[str, object] | None = None,
    ):
        return build_audience_package_v3(
            inputs=self._materialize(
                root / "inputs",
                bundle=bundle,
                documents=documents,
            ),
            output_dir=root / "output",
        )

    @staticmethod
    def _members(raw: bytes) -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    @staticmethod
    def _ordinary_zip(
        members: list[tuple[str, bytes]],
        *,
        compression: int = zipfile.ZIP_STORED,
    ) -> bytes:
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=compression) as archive:
            for name, data in members:
                archive.writestr(name, data)
        return raw.getvalue()

    def _reseal_chain(self, documents: dict[str, object]) -> None:
        brief = documents["brief"]
        panel = documents["panel"]
        frame = documents["population_frame"]
        composition = documents["composition"]
        validity = documents["validity"]
        workflow = documents["workflow_state"]
        audit = documents["audit"]
        v2_brief = bare_digest(_v2_projection(brief, brief=True))
        v2_panel = bare_digest(_v2_projection(panel, brief=False))
        frame_digest = prefixed_digest(frame)
        usable_frame = (
            frame_digest
            if frame["eligibility"] in {"eligible_tier_2", "eligible_tier_3"}
            else None
        )
        brief["population_frame_result_sha256"] = frame_digest
        brief["population_frame_sha256"] = usable_frame
        panel["population_frame_result_sha256"] = frame_digest
        panel["population_frame_sha256"] = usable_frame
        composition["frame_binding"]["frame_result_sha256"] = frame_digest
        composition["frame_binding"]["frame_sha256"] = usable_frame
        composition_digest = prefixed_digest(composition)
        panel["composition_plan_sha256"] = composition_digest
        validity["source_bindings"] = {
            "brief_sha256": "sha256:" + v2_brief,
            "panel_sha256": "sha256:" + v2_panel,
            "frame_result_sha256": frame_digest,
            "frame_sha256": usable_frame,
            "composition_sha256": composition_digest,
        }
        panel["validity_profile_sha256"] = prefixed_digest(validity)
        for approval in brief["scoped_approvals"]:
            if approval["scope"] == "evidence-synthesis":
                approval["target_sha256"] = "sha256:" + v2_brief
            elif approval["scope"] == "panel-construction":
                approval["target_sha256"] = "sha256:" + v2_panel
        audit["input_bindings"].update(
            brief_sha256=v2_brief,
            panel_sha256=v2_panel,
            population_frame_result_sha256=bare_digest(frame),
            population_frame_sha256=(
                bare_digest(frame) if usable_frame is not None else None
            ),
            composition_plan_sha256=bare_digest(composition),
            validity_profile_sha256=bare_digest(validity),
        )
        audit_digest = bare_digest(audit)
        panel["audit_binding"]["audit_sha256"] = audit_digest
        workflow["bindings"].update(
            brief_sha256=v2_brief,
            panel_sha256=v2_panel,
            audit_sha256=audit_digest,
        )
        for approval in workflow["approvals"]:
            if approval["scope"] == "evidence_synthesis":
                approval["target_sha256"] = v2_brief
            elif approval["scope"] == "panel_construction":
                approval["target_sha256"] = v2_panel
        report_inputs = documents["report_inputs"]
        report_inputs.update(
            panel_id=panel["panel_id"],
            panel_version=panel["version"],
            workflow_state_sha256=bare_digest(workflow),
            frame_sha256=(
                None if usable_frame is None else bare_digest(frame)
            ),
            composition_sha256=bare_digest(composition),
            validity_sha256=bare_digest(validity),
            source_inventory_sha256=bare_digest(documents["source_inventory"]),
            verbatim_inventory_sha256=bare_digest(
                documents["verbatim_inventory"]
            ),
        )
        report_manifest = documents["report_manifest"]
        report_manifest.update(
            panel_id=panel["panel_id"],
            panel_version=panel["version"],
            report_inputs_sha256=bare_digest(report_inputs),
        )
        outputs = {
            "audience-research-report.html": documents["report"].encode("utf-8"),
            "source-inventory.json": canonical_bytes(
                documents["source_inventory"]
            ),
            "verbatim-inventory.json": canonical_bytes(
                documents["verbatim_inventory"]
            ),
        }
        for record in report_manifest["outputs"]:
            data = outputs[record["path"]]
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["bytes"] = len(data)

    def _tier3_documents_with_runtime_authority(
        self,
    ) -> dict[str, object]:
        harness = research_harness.AudienceResearchV3ContractTests()
        harness.setUpClass()
        tier3 = list(harness.v3_pair())
        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        tier3[1]["grounded_context_profiles"] = copy.deepcopy(
            documents["panel"]["grounded_context_profiles"]
        )
        for key, value in zip(
            (
                "brief",
                "panel",
                "population_frame",
                "composition",
                "validity",
                "workflow_state",
                "audit",
            ),
            tier3,
            strict=True,
        ):
            documents[key] = copy.deepcopy(value)

        brief = documents["brief"]
        panel = documents["panel"]
        frame = documents["population_frame"]
        batch = harness.observation_batch()
        source_binding = frame["source_bindings"][0]
        self.assertEqual(
            batch["normalized_batch_sha256"],
            source_binding["normalized_batch_sha256"],
        )
        partition = next(
            item
            for item in frame["units"]
            if item["partition_id"] == source_binding["partition_id"]
        )
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
                    "sha256": prefixed_digest(batch),
                    "unit": partition["unit"],
                    "denominator": partition["denominator"],
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
                        "sha256": prefixed_digest(batch),
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
            "schema_version":
                "authorized-audience-runtime-authority-v1",
            "cohort_id": brief["authorized_audience_import"][
                "cohort_id"
            ],
            "handoff": handoff,
            "structural_outputs": [
                {
                    "path": "frame-observations-0001.json",
                    "batch": batch,
                }
            ],
        }
        handoff_sha256 = prefixed_digest(handoff)
        brief["authorized_audience_import"]["handoff_sha256"] = (
            handoff_sha256
        )
        panel["authorized_handoff_sha256"] = handoff_sha256
        documents["audit"]["input_bindings"][
            "authorized_handoff_sha256"
        ] = handoff_sha256.removeprefix("sha256:")
        documents["authorized_runtime_authority"] = authority
        self._reseal_chain(documents)
        return documents

    def _review_bound_documents(self) -> dict[str, object]:
        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        panel_bytes = canonical_bytes(documents["panel"])
        review_manifest = {
            "schema_version": "panel-review-manifest-v1",
            "panel_id": documents["panel"]["panel_id"],
            "panel_version": documents["panel"]["version"],
            "review_revision": "review-v1",
            "generated_at": documents["panel"]["updated_at"],
            "canonical_panel": {
                "path": "saved-audience-panel.json",
                "media_type": "application/json",
                "sha256": hashlib.sha256(panel_bytes).hexdigest(),
                "bytes": len(panel_bytes),
            },
            "review_outputs": [
                {
                    "path": "audience-panel-review.html",
                    "media_type": "text/html",
                    "sha256": hashlib.sha256(b"<html></html>").hexdigest(),
                    "bytes": len(b"<html></html>"),
                },
                {
                    "path": "panel-summary.md",
                    "media_type": "text/markdown",
                    "sha256": hashlib.sha256(b"# Review\n").hexdigest(),
                    "bytes": len(b"# Review\n"),
                },
            ],
        }
        documents["panel_review_manifest"] = review_manifest
        review_bytes = canonical_bytes(review_manifest)
        review_digest = hashlib.sha256(review_bytes).hexdigest()
        workflow_snapshot = copy.deepcopy(documents["workflow_state"])
        workflow_snapshot_bytes = canonical_bytes(workflow_snapshot)
        documents["report_inputs"]["workflow_state_sha256"] = bare_digest(
            workflow_snapshot
        )
        report_manifest = documents["report_manifest"]
        report_manifest["schema_version"] = "audience-research-report-manifest-v2"
        report_manifest["report_inputs_sha256"] = bare_digest(
            documents["report_inputs"]
        )
        unavailable_report_sources = {
            "evidence-ledger.json": {"fixture": "evidence-ledger"},
            "finding-support.json": {"fixture": "finding-support"},
            "plan.json": {"fixture": "research-plan"},
            "scored-sources.json": {"fixture": "scored-sources"},
            "synthesis-matrix.json": {"fixture": "synthesis-matrix"},
        }
        report_input_bytes = {
            "brief.json": canonical_bytes(documents["brief"]),
            "composition-plan.json": canonical_bytes(documents["composition"]),
            PANEL_REVIEW_MANIFEST_MEMBER: review_bytes,
            "population-frame.json": canonical_bytes(
                documents["population_frame"]
            ),
            "report-inputs.json": canonical_bytes(documents["report_inputs"]),
            "saved-audience-panel.json": canonical_bytes(documents["panel"]),
            "source-inventory.json": canonical_bytes(
                documents["source_inventory"]
            ),
            "validity-profile.json": canonical_bytes(documents["validity"]),
            "verbatim-inventory.json": canonical_bytes(
                documents["verbatim_inventory"]
            ),
            "workflow-state.json": workflow_snapshot_bytes,
            **{
                path: canonical_bytes(value)
                for path, value in unavailable_report_sources.items()
            },
        }
        report_manifest["inputs"] = [
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for path, data in sorted(report_input_bytes.items())
        ]
        report_manifest_digest = bare_digest(report_manifest)
        documents["audit"]["input_bindings"][
            "report_manifest_sha256"
        ] = report_manifest_digest
        audit_digest = bare_digest(documents["audit"])
        documents["workflow_state"]["bindings"].update(
            report_inputs_sha256=bare_digest(documents["report_inputs"]),
            audit_sha256=audit_digest,
        )
        for approval in documents["workflow_state"]["approvals"]:
            if approval["scope"] == "panel_construction":
                approval["target_sha256"] = review_digest
        return documents

    def test_report_manifest_v2_packages_and_rejects_swapped_review_manifest(self) -> None:
        documents = self._review_bound_documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self._build(root / "valid", documents=documents)
            validation = validate_package_archive_v3(result.package_zip_path)
            self.assertEqual("valid", validation["status"])
            dispatched = validate_supported_audience_package(
                result.package_zip_path
            )
            self.assertEqual("valid", dispatched["status"])
            members = self._members(result.package_zip_path.read_bytes())
            self.assertIn(PANEL_REVIEW_MANIFEST_MEMBER, members)

            def reseal_external_chain(candidate: dict[str, object]) -> None:
                candidate["audit"]["input_bindings"][
                    "report_manifest_sha256"
                ] = bare_digest(candidate["report_manifest"])
                candidate["workflow_state"]["bindings"][
                    "audit_sha256"
                ] = bare_digest(candidate["audit"])

            for label, mutate, pattern in (
                (
                    "missing",
                    lambda manifest: manifest["inputs"].pop(0),
                    "exact sorted v3 input path list",
                ),
                (
                    "duplicate",
                    lambda manifest: manifest["inputs"].append(
                        copy.deepcopy(manifest["inputs"][0])
                    ),
                    "exact sorted v3 input path list",
                ),
                (
                    "unknown",
                    lambda manifest: manifest["inputs"][0].__setitem__(
                        "path", "unknown.json"
                    ),
                    "exact sorted v3 input path list",
                ),
                (
                    "stale-entry",
                    lambda manifest: next(
                        entry
                        for entry in manifest["inputs"]
                        if entry["path"] == "composition-plan.json"
                    ).__setitem__("sha256", "0" * 64),
                    "composition-plan.json does not bind the exact canonical bytes",
                ),
            ):
                invalid_manifest = copy.deepcopy(documents)
                mutate(invalid_manifest["report_manifest"])
                reseal_external_chain(invalid_manifest)
                with self.subTest(v2_manifest_input=label), self.assertRaisesRegex(
                    PackageValidationError,
                    pattern,
                ):
                    self._build(
                        root / f"invalid-{label}",
                        documents=invalid_manifest,
                    )

            tampered = copy.deepcopy(documents)
            tampered["panel_review_manifest"]["review_revision"] = "review-v2"
            tampered_review_digest = bare_digest(
                tampered["panel_review_manifest"]
            )
            for approval in tampered["workflow_state"]["approvals"]:
                if approval["scope"] == "panel_construction":
                    approval["target_sha256"] = tampered_review_digest
            with self.assertRaisesRegex(
                PackageValidationError,
                "panel-review-manifest.json does not bind the exact canonical bytes",
            ):
                self._build(root / "tampered", documents=tampered)

            stale = copy.deepcopy(documents)
            for approval in stale["workflow_state"]["approvals"]:
                if approval["scope"] == "panel_construction":
                    approval["target_sha256"] = bare_digest(stale["panel"])
            with self.assertRaisesRegex(
                PackageValidationError,
                "exact panel review manifest",
            ):
                self._build(root / "stale", documents=stale)

            stale_audit = copy.deepcopy(documents)
            stale_audit["audit"]["input_bindings"][
                "report_manifest_sha256"
            ] = "0" * 64
            stale_audit["workflow_state"]["bindings"][
                "audit_sha256"
            ] = bare_digest(stale_audit["audit"])
            with self.assertRaisesRegex(
                PackageValidationError,
                "report_manifest_sha256 must bind the exact packaged report manifest",
            ):
                self._build(root / "stale-audit", documents=stale_audit)

            wrong_snapshot = copy.deepcopy(documents)
            wrong_snapshot["report_inputs"]["workflow_state_sha256"] = (
                bare_digest(wrong_snapshot["workflow_state"])
            )
            wrong_snapshot["report_manifest"]["report_inputs_sha256"] = (
                bare_digest(wrong_snapshot["report_inputs"])
            )
            wrong_snapshot_report_digest = bare_digest(
                wrong_snapshot["report_manifest"]
            )
            wrong_snapshot["audit"]["input_bindings"][
                "report_manifest_sha256"
            ] = wrong_snapshot_report_digest
            wrong_snapshot["workflow_state"]["bindings"].update(
                report_inputs_sha256=bare_digest(
                    wrong_snapshot["report_inputs"]
                ),
                audit_sha256=bare_digest(wrong_snapshot["audit"]),
            )
            with self.assertRaisesRegex(
                PackageValidationError,
                "report inputs must exactly bind",
            ):
                self._build(
                    root / "wrong-workflow-snapshot",
                    documents=wrong_snapshot,
                )

    def test_core_v2_builder_and_production_cli_require_review_manifest(self) -> None:
        documents = self._review_bound_documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self._materialize(root / "inputs", documents=documents)
            inputs_without_review = dict(inputs)
            inputs_without_review.pop("panel_review_manifest")
            with self.assertRaisesRegex(
                PackageValidationError,
                "requires panel_review_manifest input",
            ):
                build_audience_package_v3(
                    inputs=inputs_without_review,
                    output_dir=root / "missing-core-output",
                )

            arguments: list[str] = []
            for key, path in inputs_without_review.items():
                arguments.extend((f"--{key.replace('_', '-')}", str(path)))
            arguments.extend(("--output-dir", str(root / "missing-cli-output")))
            result = subprocess.run(
                [sys.executable, str(BUILD_CLI), *arguments],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("--panel-review-manifest", result.stdout)
            self.assertFalse((root / "missing-cli-output").exists())

    def test_new_package_validation_independently_enforces_specificity(self) -> None:
        documents = self._review_bound_documents()
        specificity_failure = {
            "status": "fail",
            "profiles": [
                {
                    "persona_archetype_id": "broad-only-profile",
                    "status": "fail",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            panel_review,
            "audit_evidence_specificity",
            return_value=specificity_failure,
        ):
            with self.assertRaisesRegex(
                PackageValidationError,
                "evidence specificity failed.*broad-only-profile",
            ):
                self._build(Path(temp), documents=documents)

    def _coherently_relabelled_tier3_archive(
        self,
        root: Path,
    ) -> bytes:
        documents = self._tier3_documents_with_runtime_authority()
        built = self._build(root / "source", documents=documents)
        members = self._members(built.package_zip_path.read_bytes())
        relabelled = copy.deepcopy(documents)
        relabelled["brief"]["authorized_audience_import"][
            "cohort_id"
        ] = "coherently-substituted-cohort"
        relabelled["authorized_runtime_authority"]["cohort_id"] = (
            "coherently-substituted-cohort"
        )
        self._reseal_chain(relabelled)
        for key, filename in self.fixture["inputs"].items():
            value = relabelled[key]
            members[filename] = (
                value.encode("utf-8")
                if key == "report"
                else canonical_bytes(value)
            )
        members[AUTHORIZED_RUNTIME_AUTHORITY_MEMBER] = canonical_bytes(
            relabelled["authorized_runtime_authority"]
        )
        members.pop("package-manifest.json")
        members["package-manifest.json"] = canonical_bytes(
            audience_package_v3._manifest(
                brief=relabelled["brief"],
                panel=relabelled["panel"],
                workflow=relabelled["workflow_state"],
                files=members,
            )
        )
        return audience_package_v3._zip_bytes(members)

    def test_eligible_public_tier_two_fixture_builds_and_validates_every_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._build(root)
            archive = validate_package_archive_v3(result.package_zip_path)
            dispatched = validate_supported_audience_package(
                result.package_zip_path
            )
        documents = self.fixture["bundles"]["tier_2"]
        frame = documents["population_frame"]
        composition_ids = {
            item["profile_id"] for item in documents["composition"]["profiles"]
        }
        grounded_ids = {
            item["grounded_profile_id"]
            for item in documents["panel"]["grounded_context_profiles"]
        }
        self.assertEqual("tier_2", documents["brief"]["panel_tier"])
        self.assertEqual("public", documents["brief"]["evidence_basis"])
        self.assertEqual("eligible_tier_2", frame["eligibility"])
        self.assertEqual(
            {("firms", "employer-firms")},
            {(item["unit"], item["denominator"]) for item in frame["units"]},
        )
        self.assertEqual(composition_ids, grounded_ids)
        self.assertEqual("valid", archive["status"])
        self.assertEqual(archive, dispatched)

    def test_tier_three_authority_builds_resolves_and_is_mandatory(
        self,
    ) -> None:
        documents = self._tier3_documents_with_runtime_authority()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            built = self._build(root, documents=documents)
            archive = validate_package_archive_v3(
                built.package_zip_path
            )
            members = self._members(
                built.package_zip_path.read_bytes()
            )
            panel_scope = documents["panel"]["audience_scope"]
            scope = {
                key: copy.deepcopy(panel_scope[key])
                for key in (
                    "audience",
                    "market",
                    "geography",
                    "category",
                    "buying_context",
                    "exclusions",
                )
            }
            resolved = resolve_audience_v3(
                package_path=built.package_zip_path,
                study_scope=scope,
                run_directory=root / "run",
            )

        self.assertEqual("valid", archive["status"])
        self.assertEqual(set(TIER3_ARCHIVE_FILES_V3), set(members))
        self.assertEqual("ready", resolved["resolution_status"])

        missing = copy.deepcopy(documents)
        missing.pop("authorized_runtime_authority")
        tampered = copy.deepcopy(documents)
        tampered["authorized_runtime_authority"]["cohort_id"] = (
            "substituted-cohort"
        )
        cohort_substitution = copy.deepcopy(documents)
        cohort_substitution["brief"]["authorized_audience_import"][
            "cohort_id"
        ] = "substituted-cohort"
        self._reseal_chain(cohort_substitution)
        for label, candidate in (
            ("missing", missing),
            ("tampered", tampered),
            ("coherently-resealed-cohort", cohort_substitution),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(
                    PackageValidationError,
                    "runtime authority|authorized_runtime_authority",
                ):
                    self._build(
                        Path(temporary),
                        documents=candidate,
                    )

    def test_authenticated_source_identity_rejects_coherent_cohort_relabelling(
        self,
    ) -> None:
        documents = self._tier3_documents_with_runtime_authority()
        panel_scope = documents["panel"]["audience_scope"]
        scope = {
            key: copy.deepcopy(panel_scope[key])
            for key in (
                "audience",
                "market",
                "geography",
                "category",
                "buying_context",
                "exclusions",
            )
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            malicious = self._coherently_relabelled_tier3_archive(root)
            package_path = root / "coherently-relabelled.zip"
            package_path.write_bytes(malicious)
            with self.assertRaisesRegex(
                PackageValidationError,
                "cohort|source identity|runtime authority",
            ):
                validate_package_archive_v3(package_path)
            resolution = resolve_audience_v3(
                package_path=package_path,
                study_scope=scope,
                run_directory=root / "malicious-run",
            )
            self.assertEqual(
                "incompatible",
                resolution["resolution_status"],
            )
            self.assertTrue(
                any(
                    "cohort" in str(reason)
                    or "runtime authority" in str(reason)
                    for reason in resolution["resolution_reasons"]
                )
            )

    def test_build_is_deterministic_canonical_and_has_exact_archive_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = self._build(Path(first))
            reversed_documents = copy.deepcopy(
                self.fixture["bundles"]["tier_2"]
            )
            for key, value in tuple(reversed_documents.items()):
                if isinstance(value, dict):
                    reversed_documents[key] = dict(reversed(tuple(value.items())))
            two = self._build(Path(second), documents=reversed_documents)
            self.assertEqual(
                one.package_zip_path.read_bytes(),
                two.package_zip_path.read_bytes(),
            )
            with zipfile.ZipFile(one.package_zip_path) as archive:
                self.assertEqual(list(ARCHIVE_FILES_V3), archive.namelist())
                for info in archive.infolist():
                    self.assertEqual((1980, 1, 1, 0, 0, 0), info.date_time)
                    self.assertEqual(zipfile.ZIP_STORED, info.compress_type)
                    self.assertEqual(0o600, (info.external_attr >> 16) & 0o777)
                for name in PACKAGE_FILES_V3:
                    if name.endswith(".json"):
                        value = json.loads(archive.read(name))
                        self.assertEqual(
                            canonical_bytes(value), archive.read(name)
                        )

    def test_manifest_uses_exact_top_level_binding_and_file_record_allowlists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        manifest = json.loads(members["package-manifest.json"])
        self.assertEqual(
            {
                "schema_version",
                "generator_version",
                "panel_id",
                "panel_version",
                "brief_id",
                "workflow_id",
                "generated_at",
                "tier",
                "evidence_basis",
                "bindings",
                "files",
            },
            set(manifest),
        )
        self.assertEqual(
            {
                "workflow_state_sha256",
                "population_frame_sha256",
                "composition_plan_sha256",
                "validity_profile_sha256",
                "report_inputs_sha256",
                "report_manifest_sha256",
                "construction_audit_sha256",
            },
            set(manifest["bindings"]),
        )
        self.assertEqual(set(PACKAGE_FILES_V3), set(manifest["files"]))
        self.assertTrue(
            all(set(record) == {"path", "sha256", "byte_count"}
                for record in manifest["files"].values())
        )

    def test_all_report_source_and_document_bindings_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        manifest = json.loads(members["package-manifest.json"])
        expected = {
            "workflow_state_sha256": "panel-workflow-state.json",
            "population_frame_sha256": "audience-population-frame.json",
            "composition_plan_sha256": "panel-composition-plan.json",
            "validity_profile_sha256": "panel-validity-profile.json",
            "report_inputs_sha256": "research-report-inputs.json",
            "report_manifest_sha256":
                "audience-research-report-manifest.json",
            "construction_audit_sha256": "panel-construction-audit.json",
        }
        for binding, member in expected.items():
            self.assertEqual(
                hashlib.sha256(members[member]).hexdigest(),
                manifest["bindings"][binding],
            )
        report_inputs = json.loads(members["research-report-inputs.json"])
        self.assertEqual(
            hashlib.sha256(members["source-inventory.json"]).hexdigest(),
            report_inputs["source_inventory_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(members["verbatim-inventory.json"]).hexdigest(),
            report_inputs["verbatim_inventory_sha256"],
        )

    def test_report_inputs_must_bind_the_packaged_documents_and_inventories(
        self,
    ) -> None:
        for field in (
            "workflow_state_sha256",
            "frame_sha256",
            "composition_sha256",
            "validity_sha256",
            "source_inventory_sha256",
            "verbatim_inventory_sha256",
        ):
            documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
            documents["report_inputs"][field] = "0" * 64
            documents["report_manifest"]["report_inputs_sha256"] = bare_digest(
                documents["report_inputs"]
            )
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(
                    PackageValidationError, "report inputs.*bind"
                ):
                    self._build(Path(temporary), documents=documents)
        for field in ("panel_id", "panel_version"):
            documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
            changed_value = "other-panel" if field == "panel_id" else "9.0.0"
            documents["report_inputs"][field] = changed_value
            documents["report_manifest"][field] = changed_value
            documents["report_manifest"]["report_inputs_sha256"] = bare_digest(
                documents["report_inputs"]
            )
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(
                    PackageValidationError, "report inputs.*bind"
                ):
                    self._build(Path(temporary), documents=documents)

    def test_tier_one_packages_the_negative_frame_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary), bundle="tier_1")
            self.assertEqual(
                "valid",
                validate_package_archive_v3(result.package_zip_path)["status"],
            )
            members = self._members(result.package_zip_path.read_bytes())
        frame = json.loads(members["audience-population-frame.json"])
        brief = json.loads(members["audience-research-brief.json"])
        panel = json.loads(members["saved-audience-panel.json"])
        self.assertEqual("no_defensible_frame", frame["eligibility"])
        self.assertEqual([], frame["cells"])
        self.assertIsNone(brief["population_frame_sha256"])
        self.assertIsNone(panel["population_frame_sha256"])

    def test_tier_two_and_three_reject_a_no_frame_result(self) -> None:
        for tier in ("tier_2", "tier_3"):
            documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
            documents["population_frame"] = copy.deepcopy(
                self.fixture["bundles"]["tier_1"]["population_frame"]
            )
            if tier == "tier_3":
                for key in ("brief", "panel", "validity"):
                    documents[key]["panel_tier"] = "tier_3"
                    documents[key]["evidence_basis"] = "first_party_aggregate"
                documents["composition"]["requested_tier"] = "tier_3"
                documents["composition"]["achieved_tier"] = "tier_3"
                documents["composition"]["evidence_basis"] = (
                    "first_party_aggregate"
                )
                documents["brief"]["authorized_audience_import"] = {
                    "handoff_schema_version":
                        "authorized-audience-handoff-v1",
                    "handoff_sha256": "sha256:" + "9" * 64,
                    "status": "complete",
                    "cohort_id": "operations-cohort",
                    "exact_cohort_denominator":
                        "all-eligible-cohort-members",
                    "selection_statement":
                        "Cohort fixed before creative review.",
                    "coverage_statement":
                        "All eligible cohort members are covered.",
                    "max_calibration_factor": 2.0,
                }
                documents["panel"]["authorized_handoff_sha256"] = (
                    "sha256:" + "9" * 64
                )
                documents["audit"]["input_bindings"][
                    "authorized_handoff_sha256"
                ] = "9" * 64
            self._reseal_chain(documents)
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(
                    PackageValidationError, "requires a frame"
                ):
                    self._build(Path(temporary), documents=documents)

    def test_profile_identity_set_mismatch_is_rejected(self) -> None:
        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        documents["panel"]["grounded_context_profiles"].pop()
        self._reseal_chain(documents)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                PackageValidationError, "profile IDs must exactly match"
            ):
                self._build(Path(temporary), documents=documents)

    def test_unknown_manifest_keys_and_exact_dispatch_versions_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        for mutation in ("top_level", "binding", "file_record"):
            changed = dict(members)
            manifest = json.loads(changed["package-manifest.json"])
            if mutation == "top_level":
                manifest["unknown"] = True
            elif mutation == "binding":
                manifest["bindings"]["unknown"] = "0" * 64
            else:
                manifest["files"][PACKAGE_FILES_V3[0]]["unknown"] = True
            changed["package-manifest.json"] = canonical_bytes(manifest)
            raw = audience_package_v3._zip_bytes(changed)
            with self.subTest(mutation=mutation):
                with self.assertRaises(PackageValidationError):
                    validate_package_archive_v3(raw)
        for schema, generator in (
            ("unknown-package-schema", GENERATOR_VERSION_V3),
            (PACKAGE_SCHEMA_VERSION_V3, "9.0.0"),
        ):
            manifest = {
                "schema_version": schema,
                "generator_version": generator,
            }
            raw = self._ordinary_zip(
                [("package-manifest.json", canonical_bytes(manifest))]
            )
            with self.subTest(schema=schema, generator=generator):
                with self.assertRaisesRegex(
                    PackageValidationError, "unsupported package"
                ):
                    validate_supported_audience_package(raw)

    def test_hostile_archive_names_duplicates_and_extra_entries_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = list(self._members(result.package_zip_path.read_bytes()).items())
        for hostile in (
            "../escape",
            "/absolute",
            "nested/member",
            r"windows\member",
        ):
            raw = self._ordinary_zip(members + [(hostile, b"x")])
            with self.subTest(hostile=hostile):
                with self.assertRaises(PackageSafetyError):
                    validate_package_archive_v3(raw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate = self._ordinary_zip(members + [(members[0][0], b"x")])
        with self.assertRaises(PackageSafetyError):
            validate_package_archive_v3(duplicate)
        extra = self._ordinary_zip(members + [("extra.txt", b"x")])
        with self.assertRaises(PackageSafetyError):
            validate_package_archive_v3(extra)

    def test_archive_entry_compression_and_total_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = list(self._members(result.package_zip_path.read_bytes()).items())
        bomb_members = [
            (name, b"A" * 1_000_000 if name == "README.txt" else data)
            for name, data in members
        ]
        bomb = self._ordinary_zip(
            bomb_members, compression=zipfile.ZIP_DEFLATED
        )
        with self.assertRaisesRegex(PackageSafetyError, "compression ratio"):
            validate_package_archive_v3(bomb)
        with mock.patch.object(audience_package, "MAX_ENTRY_BYTES", 10):
            with self.assertRaisesRegex(PackageSafetyError, "size limit"):
                validate_package_archive_v3(
                    self._ordinary_zip(members)
                )
        with mock.patch.object(audience_package, "MAX_TOTAL_BYTES", 100):
            with self.assertRaisesRegex(PackageSafetyError, "total uncompressed"):
                validate_package_archive_v3(
                    self._ordinary_zip(members)
                )
        too_many = self._ordinary_zip(
            members
            + [
                (f"extra-{index}", b"x")
                for index in range(MAX_ARCHIVE_ENTRIES - len(members) + 1)
            ]
        )
        with self.assertRaisesRegex(PackageSafetyError, "too many"):
            validate_package_archive_v3(too_many)

    def test_only_the_nested_v2_source_member_gets_a_bounded_size_exception(
        self,
    ) -> None:
        allowed = (
            "source-v2-package.zip",
            "package-manifest.json",
        )
        nested = self._ordinary_zip(
            [
                ("source-v2-package.zip", b"v" * 11),
                ("package-manifest.json", b"{}"),
            ]
        )
        ordinary = self._ordinary_zip(
            [
                ("README.txt", b"r" * 11),
                ("package-manifest.json", b"{}"),
            ]
        )
        oversized_nested = self._ordinary_zip(
            [
                ("source-v2-package.zip", b"v" * 16),
                ("package-manifest.json", b"{}"),
            ]
        )
        with (
            mock.patch.object(audience_package, "MAX_ENTRY_BYTES", 10),
            mock.patch.object(audience_package, "MAX_TOTAL_BYTES", 20),
        ):
            members = read_safe_archive_members(
                nested,
                allowed_files=allowed,
                entry_size_overrides={"source-v2-package.zip": 15},
                max_total_bytes=25,
            )
            self.assertEqual(11, len(members["source-v2-package.zip"]))
            with self.assertRaisesRegex(PackageSafetyError, "size limit"):
                read_safe_archive_members(
                    ordinary,
                    allowed_files=(
                        "README.txt",
                        "package-manifest.json",
                    ),
                    entry_size_overrides={
                        "source-v2-package.zip": 15,
                    },
                    max_total_bytes=25,
                )
            with self.assertRaisesRegex(PackageSafetyError, "size limit"):
                read_safe_archive_members(
                    oversized_nested,
                    allowed_files=allowed,
                    entry_size_overrides={
                        "source-v2-package.zip": 15,
                    },
                    max_total_bytes=25,
                )

    def test_wrong_timestamp_permissions_and_compression_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        for mutation in ("timestamp", "permissions", "compression"):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                for name in ARCHIVE_FILES_V3:
                    info = zipfile.ZipInfo(
                        name,
                        date_time=(
                            (2026, 1, 1, 0, 0, 0)
                            if mutation == "timestamp"
                            else (1980, 1, 1, 0, 0, 0)
                        ),
                    )
                    info.create_system = 3
                    info.external_attr = (
                        stat.S_IFREG
                        | (0o644 if mutation == "permissions" else 0o600)
                    ) << 16
                    info.compress_type = (
                        zipfile.ZIP_DEFLATED
                        if mutation == "compression"
                        else zipfile.ZIP_STORED
                    )
                    archive.writestr(info, members[name])
            with self.subTest(mutation=mutation):
                with self.assertRaises(PackageValidationError):
                    validate_package_archive_v3(raw.getvalue())

    def test_noncanonical_json_and_unsafe_html_are_rejected_in_archives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        noncanonical = dict(members)
        noncanonical["source-inventory.json"] += b" "
        with self.assertRaises(PackageValidationError):
            validate_package_archive_v3(
                self._ordinary_zip(list(noncanonical.items()))
            )
        unsafe = dict(members)
        unsafe["audience-research-report.html"] = (
            b"<!doctype html><script>alert(1)</script>"
        )
        report_manifest = json.loads(
            unsafe["audience-research-report-manifest.json"]
        )
        record = next(
            item
            for item in report_manifest["outputs"]
            if item["path"] == "audience-research-report.html"
        )
        record["sha256"] = hashlib.sha256(
            unsafe["audience-research-report.html"]
        ).hexdigest()
        record["bytes"] = len(unsafe["audience-research-report.html"])
        unsafe["audience-research-report-manifest.json"] = canonical_bytes(
            report_manifest
        )
        manifest = json.loads(unsafe["package-manifest.json"])
        for name in PACKAGE_FILES_V3:
            manifest["files"][name]["sha256"] = hashlib.sha256(
                unsafe[name]
            ).hexdigest()
            manifest["files"][name]["byte_count"] = len(unsafe[name])
        manifest["bindings"]["report_manifest_sha256"] = hashlib.sha256(
            unsafe["audience-research-report-manifest.json"]
        ).hexdigest()
        unsafe["package-manifest.json"] = canonical_bytes(manifest)
        with self.assertRaisesRegex(PackageSafetyError, "forbidden <script>"):
            validate_package_archive_v3(
                audience_package_v3._zip_bytes(unsafe)
            )

    def test_tampering_each_member_and_each_bound_class_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            members = self._members(result.package_zip_path.read_bytes())
        for name in ARCHIVE_FILES_V3:
            changed = dict(members)
            changed[name] += b" "
            with self.subTest(member=name):
                with self.assertRaises(PackageValidationError):
                    validate_package_archive_v3(
                        self._ordinary_zip(list(changed.items()))
                    )
        manifest = json.loads(members["package-manifest.json"])
        for binding in manifest["bindings"]:
            changed = dict(members)
            altered = copy.deepcopy(manifest)
            altered["bindings"][binding] = "0" * 64
            changed["package-manifest.json"] = canonical_bytes(altered)
            with self.subTest(binding=binding):
                with self.assertRaises(PackageValidationError):
                    validate_package_archive_v3(
                        audience_package_v3._zip_bytes(changed)
                    )

    def test_dispatch_reads_and_validates_one_immutable_snapshot(self) -> None:
        class OneReadStream(io.BytesIO):
            reads = 0

            def read(self, *args, **kwargs):
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError(
                        "dispatcher re-read a replaceable archive source"
                    )
                return super().read(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            result = self._build(Path(temporary))
            source = OneReadStream(result.package_zip_path.read_bytes())
            self.assertEqual(
                "valid",
                validate_supported_audience_package(source)["status"],
            )
            self.assertEqual(1, source.reads)
