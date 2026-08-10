"""Release B2 end-to-end population-runtime compatibility proofs."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
PUBLIC = ROOT / "conformance" / "fixtures" / "population" / "public-proxy"
V3_FIXTURE = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-package-v3"
    / "approved-package-inputs.json"
)
for script_root in (SCRIPTS, PANEL_SCRIPTS):
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))

from audience_lab import audience_package_v3  # noqa: E402
from audience_lab.audience_package_v3 import build_audience_package_v3  # noqa: E402
from audience_lab.audience_resolution_v3 import resolve_audience_v3  # noqa: E402
from audience_lab.audience_library import find_package, register_package  # noqa: E402
from audience_lab.audience_research_v3 import _v2_projection  # noqa: E402
from audience_panel_builder.common import sha256_json  # noqa: E402
from audience_panel_builder.population.composition import (  # noqa: E402
    build_composition_plan,
)
from audience_panel_builder.population.validity import (  # noqa: E402
    assess_population_validity,
    finalize_validity_profile,
)
from conformance import test_audience_v3_migration as migration_contract  # noqa: E402
from conformance import test_audience_package as v2_package_contract  # noqa: E402
from conformance import test_audience_package_v3 as v3_package_contract  # noqa: E402
from conformance import test_dashboard as dashboard_contract  # noqa: E402
from conformance import test_population_core_golden_paths as population_core_contract  # noqa: E402
from conformance import test_progressive_workflow as protected_contract  # noqa: E402
from conformance import test_v3_dispatch_compatibility as dispatch_contract  # noqa: E402
from conformance import test_v3_profile_rosters as roster_contract  # noqa: E402


PERSONA_PROMPT_SHA256 = (
    "8cfc2806d9f6bfdd4a3193eda33c4c3adbd6a4bc69eb0ff6f9187f0b2aab25ff"
)
PROTECTED_PRE_B2_SHA256 = {
    "persona_prompt": PERSONA_PROMPT_SHA256,
    "enriched_prompt_order": (
        "418da32e0777c16f8ac8b8fbda8bd4b2b06d1559f8447b2ca4d65d019e1b700a"
    ),
    "progressive_calls": (
        "f3e4f938302913e9e06de84948dc1bb37f83e6d36b2b9f29f4698ffd578a1298"
    ),
    "enriched_progressive_calls": (
        "e4ba9db0b484fc42b73e512e9de9ef44db9ba1dbbc5dba7da895066aa646e6ba"
    ),
    "validated_response": (
        "cf84bd3d0cd4ef8826e48af6526bc00ec7c378a0842f423135389a043f743ef3"
    ),
    "retry_decisions": (
        "f3ee2c7ce53f9de798c537bd6eae43c545bc52f1ce7f2ec9cdbf9fe83030976c"
    ),
    "complete_exposure_scores": (
        "0d09f97de44254c4419f2b24fcd11e997333b7a11cfc2c8de54cd2d1afbbd2c2"
    ),
    "maxdiff_results": (
        "ab17525b481fd6c7dccb03a9095cde89cc4cfe56088934f033171409094413d1"
    ),
    "pairwise_results": (
        "dc3d8c45650bf0ee4b80ec2754797b8bc387eca1830d2c34e0a8f480b063f8bc"
    ),
    "finalist_summaries": (
        "6bff1388fb0e286451cb29d550c856ed33bd83afb65d574d372be22dccc89bd1"
    ),
    "verbatim_extraction": (
        "b03f32502d699e5ffea9efffd82184f5bbc72c5f2f358e81def560c99cbf6bf1"
    ),
}
GOLDEN_V3_PACKAGE_SHA256 = {
    "public-tier-1": (
        "9099514b19832e983c6169b00a5d3a9b876cfcdabda02272ce1ce4e33a688d06"
    ),
    "public-tier-2": (
        "20d4ea51d92120dfa804cb937ef5619dacfde14121cc7c0ec16f907d056d993a"
    ),
    "authorized-tier-3": (
        "53dacc903ffde34141498f31085bfaaa641a21d224968fc6e8d3dae80a3ca94e"
    ),
}


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


class PopulationRuntimeGoldenPathTests(unittest.TestCase):
    """One discoverable release matrix over the real B2 runtime boundaries."""

    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(V3_FIXTURE.read_text(encoding="utf-8"))
        self.roster_harness = roster_contract.V3ProfileRosterTests()
        self.roster_harness.setUp()

    def _run_existing_contract(
        self,
        case_type: type[unittest.TestCase],
        method_name: str,
    ) -> None:
        """Run the named authoritative contract as part of this release matrix."""

        result = unittest.TestResult()
        case_type(method_name).run(result)
        details = [
            f"{test.id()}: {traceback}"
            for test, traceback in result.failures + result.errors
        ]
        self.assertFalse(result.skipped, result.skipped)
        self.assertTrue(result.wasSuccessful(), "\n".join(details))

    def _tier_three_documents(self) -> dict[str, object]:
        fixture_root = (
            ROOT
            / "conformance"
            / "fixtures"
            / "population"
            / "authorized-marketplace"
        )
        documents = self._actual_population_documents(
            fixture_root=fixture_root,
            filenames={
                "brief": "expected-v3-brief.json",
                "panel": "expected-v3-panel.json",
                "population_frame": "expected-frame.json",
                "composition": "expected-composition.json",
                "validity": "expected-validity.json",
            },
            workflow_id="fictional-marketplace-panel-build",
            auditor_run_id="fictional-marketplace-construction-audit",
        )
        handoff = json.loads(
            (fixture_root / "expected-handoff.json").read_text(
                encoding="utf-8"
            )
        )
        frame = documents["population_frame"]
        binding = frame["source_bindings"][0]
        partition = next(
            item
            for item in frame["units"]
            if item["partition_id"] == binding["partition_id"]
        )
        source_rows = {
            row["segment"]: int(row["respondent_count"])
            for row in csv.DictReader(
                (
                    ROOT
                    / "conformance"
                    / "fixtures"
                    / "authorized-audience"
                    / "source-shapes"
                    / "flat-structural.csv"
                ).read_text(encoding="utf-8").splitlines()
            )
        }
        batch = {
            "schema_version": "audience-frame-observation-batch-v1",
            "batch_id": binding["batch_id"],
            "frame_request_id": frame["frame_request_id"],
            "adapter_id": "authorized-audience-data-lab",
            "source_family": "authorized-aggregate",
            "source": copy.deepcopy(binding["source"]),
            "raw_snapshot_sha256": binding["raw_snapshot_sha256"],
            "normalized_batch_sha256": "",
            "access": copy.deepcopy(binding["access"]),
            "geography": copy.deepcopy(binding["geography"]),
            "unit": partition["unit"],
            "denominator": partition["denominator"],
            "dimensions": copy.deepcopy(frame["structural_dimensions"]),
            "cells": [
                {
                    "cell_id": cell["cell_id"],
                    "dimension_values": copy.deepcopy(
                        cell["dimension_values"]
                    ),
                    "estimate": source_rows[
                        cell["dimension_values"]["cohort"]
                    ],
                    "uncertainty": {
                        "lower": source_rows[
                            cell["dimension_values"]["cohort"]
                        ],
                        "upper": source_rows[
                            cell["dimension_values"]["cohort"]
                        ],
                        "method": "exact approved aggregate count",
                    },
                    "suppressed": cell["suppressed"],
                    "status": cell["status"],
                    "relationship": cell["relationship"],
                    "source_location": (
                        f"approved-cohort-export#{cell['cell_id']}"
                    ),
                }
                for cell in frame["cells"]
            ],
            "selection_notes": binding["selection_notes"],
            "coverage_notes": binding["coverage_notes"],
            "citations": [
                "Approved authorized aggregate cohort export, 2026"
            ],
        }
        batch_hash_input = copy.deepcopy(batch)
        batch_hash_input.pop("normalized_batch_sha256")
        batch["normalized_batch_sha256"] = (
            "sha256:"
            + hashlib.sha256(
                canonical_bytes(batch_hash_input)
            ).hexdigest()
        )
        structural_output = next(
            item
            for item in handoff["outputs"]
            if item["route"] == "structural_frame"
        )
        self.assertEqual(
            binding["normalized_batch_sha256"],
            batch["normalized_batch_sha256"],
        )
        self.assertEqual(
            structural_output["sha256"],
            "sha256:"
            + hashlib.sha256(canonical_bytes(batch)).hexdigest(),
        )
        documents["authorized_runtime_authority"] = {
            "schema_version":
                "authorized-audience-runtime-authority-v1",
            "cohort_id": documents["brief"][
                "authorized_audience_import"
            ]["cohort_id"],
            "handoff": handoff,
            "structural_outputs": [
                {
                    "path": structural_output["path"],
                    "batch": batch,
                }
            ],
        }
        return documents

    def _public_tier_one_documents(self) -> dict[str, object]:
        documents = self._actual_population_documents(
            fixture_root=PUBLIC,
            filenames={
                "brief": "expected-v3-brief.json",
                "panel": "expected-v3-panel.json",
                "population_frame": "expected-population-frame.json",
                "composition": "expected-composition-plan.json",
            },
            workflow_id="marketing-leader-public-proxy-build",
            auditor_run_id="public-proxy-construction-audit",
        )
        return documents

    def _actual_population_documents(
        self,
        *,
        fixture_root: Path,
        filenames: dict[str, str],
        workflow_id: str,
        auditor_run_id: str,
    ) -> dict[str, object]:
        """Seal actual B1 golden documents into a complete v3 package input."""

        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        for key, filename in filenames.items():
            documents[key] = json.loads(
                (fixture_root / filename).read_text(encoding="utf-8")
            )
        composition = documents["composition"]
        for profile in composition["profiles"]:
            profile["support_status"] = "supported"
        composition["allocation_constraints"][0] = (
            "Preserve every explicit materialized profile signature."
        )
        brief = documents["brief"]
        panel = documents["panel"]
        frame = documents["population_frame"]
        if "validity" not in filenames:
            request = json.loads(
                (fixture_root / "frame-request.json").read_text(
                    encoding="utf-8"
                )
            )
            provisional = assess_population_validity(
                frame_request=request,
                population_frame=frame,
                overlay_evidence=[],
                outcome_feedback=[],
            )
            documents["validity"] = finalize_validity_profile(
                provisional_validity=provisional,
                population_frame=frame,
                composition_plan=composition,
                panel_id=panel["panel_id"],
                panel_tier=panel["panel_tier"],
                evidence_basis=panel["evidence_basis"],
                brief_sha256=sha256_json(
                    _v2_projection(brief, brief=True)
                ),
                panel_projection_sha256=sha256_json(
                    _v2_projection(panel, brief=False)
                ),
            )

        validity = documents["validity"]
        authorized_handoff = (
            None
            if panel["authorized_handoff_sha256"] is None
            else json.loads(
                (
                    fixture_root / "expected-handoff.json"
                ).read_text(encoding="utf-8")
            )
        )
        audit = population_core_contract._build_construction_audit(
            brief=brief,
            panel=panel,
            frame=frame,
            composition=composition,
            validity=validity,
            authorized_handoff=authorized_handoff,
            auditor_run_id=auditor_run_id,
        )
        self.assertEqual(
            panel["audit_binding"]["audit_sha256"],
            hashlib.sha256(canonical_bytes(audit)).hexdigest(),
        )
        v2_brief_sha256 = hashlib.sha256(
            canonical_bytes(_v2_projection(brief, brief=True))
        ).hexdigest()
        v2_panel_sha256 = hashlib.sha256(
            canonical_bytes(_v2_projection(panel, brief=False))
        ).hexdigest()
        self.assertEqual(workflow_id, brief["workflow_state_binding"])
        workflow = {
            "schema_version": "panel-workflow-state-v1",
            "workflow_id": workflow_id,
            "panel_id": panel["panel_id"],
            "panel_version": panel["version"],
            "state": "approved",
            "updated_at": "2026-07-24T17:15:00Z",
            "approvals": [
                {
                    "scope": "evidence_synthesis",
                    "status": "approved",
                    "approved_by": "golden-reviewer",
                    "approved_at": "2026-07-24T17:10:00Z",
                    "target_sha256": v2_brief_sha256,
                    "note": "Exact v2 brief projection approved.",
                },
                {
                    "scope": "panel_construction",
                    "status": "approved",
                    "approved_by": "golden-reviewer",
                    "approved_at": "2026-07-24T17:12:00Z",
                    "target_sha256": v2_panel_sha256,
                    "note": "Exact v2 panel projection approved.",
                },
            ],
            "bindings": {
                "brief_sha256": v2_brief_sha256,
                "panel_sha256": v2_panel_sha256,
                "report_inputs_sha256": panel["audit_binding"][
                    "report_inputs_sha256"
                ],
                "audit_sha256": panel["audit_binding"]["audit_sha256"],
                "package_sha256": None,
            },
        }
        documents["workflow_state"] = workflow
        documents["audit"] = audit
        documents["report_inputs"].update(
            panel_id=panel["panel_id"],
            panel_version=panel["version"],
            workflow_state_sha256=hashlib.sha256(
                canonical_bytes(workflow)
            ).hexdigest(),
            frame_sha256=(
                hashlib.sha256(canonical_bytes(frame)).hexdigest()
                if frame["eligibility"]
                in {"eligible_tier_2", "eligible_tier_3"}
                else None
            ),
            composition_sha256=hashlib.sha256(
                canonical_bytes(composition)
            ).hexdigest(),
            validity_sha256=hashlib.sha256(
                canonical_bytes(validity)
            ).hexdigest(),
            source_inventory_sha256=hashlib.sha256(
                canonical_bytes(documents["source_inventory"])
            ).hexdigest(),
            verbatim_inventory_sha256=hashlib.sha256(
                canonical_bytes(documents["verbatim_inventory"])
            ).hexdigest(),
        )
        report_inputs_digest = hashlib.sha256(
            canonical_bytes(documents["report_inputs"])
        ).hexdigest()
        documents["report_manifest"].update(
            panel_id=panel["panel_id"],
            panel_version=panel["version"],
            report_inputs_sha256=report_inputs_digest,
        )
        report_outputs = {
            "audience-research-report.html": documents["report"].encode(
                "utf-8"
            ),
            "source-inventory.json": canonical_bytes(
                documents["source_inventory"]
            ),
            "verbatim-inventory.json": canonical_bytes(
                documents["verbatim_inventory"]
            ),
        }
        for record in documents["report_manifest"]["outputs"]:
            data = report_outputs[record["path"]]
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["bytes"] = len(data)
        return documents

    def _build_package(
        self,
        root: Path,
        documents: dict[str, object],
        *,
        migration_provenance: dict[str, object] | None = None,
        source_v2_package: Path | None = None,
    ) -> Path:
        inputs = self.roster_harness._materialize_inputs(
            root / "inputs",
            bundle="tier_2",
            documents=documents,
        )
        if "authorized_runtime_authority" in documents:
            authority_path = (
                root
                / "inputs"
                / "authorized-audience-runtime-authority.json"
            )
            authority_path.write_bytes(
                canonical_bytes(
                    documents["authorized_runtime_authority"]
                )
            )
            inputs["authorized_runtime_authority"] = authority_path
        if migration_provenance is not None:
            provenance_path = root / "inputs" / "migration-provenance.json"
            provenance_path.write_bytes(canonical_bytes(migration_provenance))
            inputs["migration_provenance"] = provenance_path
        if source_v2_package is not None:
            inputs["source_v2_package"] = source_v2_package
        return build_audience_package_v3(
            inputs=inputs,
            output_dir=root / "package",
        ).package_zip_path

    def _forge_policy_archive(
        self,
        root: Path,
        documents: dict[str, object],
    ) -> Path:
        """Seal adversarial documents while deliberately bypassing validation."""

        package_case = v3_package_contract.AudiencePackageV3Test()
        package_case.fixture = self.fixture
        package_case._reseal_chain(documents)
        inputs = self.roster_harness._materialize_inputs(
            root / "inputs",
            bundle="tier_2",
            documents=documents,
        )
        parsed, files = audience_package_v3._read_inputs(inputs)
        brief = parsed["brief"]
        panel = parsed["panel"]
        frame = parsed["population_frame"]
        workflow = parsed["workflow_state"]
        files["README.txt"] = audience_package_v3._readme_bytes(
            brief,
            panel,
            frame,
        )
        files["package-manifest.json"] = audience_package_v3._canonical_json(
            audience_package_v3._manifest(
                brief=brief,
                panel=panel,
                workflow=workflow,
                files=files,
            )
        )
        package_path = root / "forged-policy-package.zip"
        package_path.write_bytes(audience_package_v3._zip_bytes(files))
        return package_path

    def _migrated_tier_one_documents(
        self,
        root: Path,
        *,
        source_package: Path | None = None,
    ) -> tuple[dict[str, object], dict[str, object], Path]:
        migration_contract.AudienceV3MigrationTests.setUpClass()
        migration_case = migration_contract.AudienceV3MigrationTests()
        if source_package is None:
            source_package = migration_case.build_v2(
                root / "v2-source"
            )
        migration_case.migrate(source_package, root / "migration")
        migrated = migration_case.read_outputs(root / "migration")

        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        documents.update(
            brief=migrated["audience-research-brief-v3.json"],
            panel=migrated["saved-audience-panel-v3.json"],
            population_frame=migrated["migration-provenance.json"][
                "no_defensible_frame_result"
            ],
            composition=migrated["panel-composition-plan.json"],
            validity=migrated["panel-validity-profile.json"],
            workflow_state=None,
            audit=None,
        )
        documents["report_inputs"].update(
            panel_id=documents["panel"]["panel_id"],
            panel_version=documents["panel"]["version"],
            workflow_state_sha256=hashlib.sha256(
                canonical_bytes(None)
            ).hexdigest(),
            frame_sha256=None,
            composition_sha256=hashlib.sha256(
                canonical_bytes(documents["composition"])
            ).hexdigest(),
            validity_sha256=hashlib.sha256(
                canonical_bytes(documents["validity"])
            ).hexdigest(),
            source_inventory_sha256=hashlib.sha256(
                canonical_bytes(documents["source_inventory"])
            ).hexdigest(),
            verbatim_inventory_sha256=hashlib.sha256(
                canonical_bytes(documents["verbatim_inventory"])
            ).hexdigest(),
        )
        documents["report_manifest"].update(
            panel_id=documents["panel"]["panel_id"],
            panel_version=documents["panel"]["version"],
            report_inputs_sha256=hashlib.sha256(
                canonical_bytes(documents["report_inputs"])
            ).hexdigest(),
        )
        report_outputs = {
            "audience-research-report.html": documents["report"].encode(
                "utf-8"
            ),
            "source-inventory.json": canonical_bytes(
                documents["source_inventory"]
            ),
            "verbatim-inventory.json": canonical_bytes(
                documents["verbatim_inventory"]
            ),
        }
        for record in documents["report_manifest"]["outputs"]:
            data = report_outputs[record["path"]]
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["bytes"] = len(data)
        return documents, migrated, source_package

    def test_valid_v2_archive_over_25_mib_remains_migratable(
        self,
    ) -> None:
        migration_contract.AudienceV3MigrationTests.setUpClass()
        migration_case = migration_contract.AudienceV3MigrationTests()
        brief = copy.deepcopy(migration_case.approved_brief)
        panel = copy.deepcopy(migration_case.approved_panel)
        brief["findings"][0]["statement"] = "A" * (
            6 * 1024 * 1024
        )
        panel["segments"][0]["description"] = "B" * (
            4 * 1024 * 1024
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = migration_case.build_v2(
                root / "large-v2",
                brief=brief,
                panel=panel,
            )
            self.assertGreater(
                source.stat().st_size,
                25 * 1024 * 1024,
            )
            documents, migrated, source = (
                self._migrated_tier_one_documents(
                    root / "large-migration",
                    source_package=source,
                )
            )
            package = self._build_package(
                root / "large-v3-package",
                documents,
                migration_provenance=migrated[
                    "migration-provenance.json"
                ],
                source_v2_package=source,
            )
            validated = (
                audience_package_v3.validate_package_archive_v3(
                    package
                )
            )
            members = audience_package_v3.read_v3_archive_members(
                package,
                allowed_files=(
                    audience_package_v3
                    .LEGACY_MIGRATION_ARCHIVE_FILES_V3
                ),
            )

        self.assertEqual("valid", validated["status"])
        self.assertGreater(
            len(members["source-v2-package.zip"]),
            25 * 1024 * 1024,
        )

    def _plan_study(
        self,
        root: Path,
        *,
        package: Path,
        documents: dict[str, object],
        study_id: str,
    ) -> tuple[dict[str, object], Path]:
        panel_scope = documents["panel"]["audience_scope"]
        study_scope = {
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
        run = root / study_id
        run.mkdir(parents=True, exist_ok=True)
        resolution = resolve_audience_v3(
            package_path=package,
            study_scope=study_scope,
            run_directory=run,
        )
        self.assertEqual(
            "ready",
            resolution["resolution_status"],
            resolution["resolution_reasons"],
        )
        request = {
            "study_id": study_id,
            "creative_ids": [
                f"creative-{index}" for index in range(1, 8)
            ],
            "creative_format": "static_image",
            "requested_shortlist_size": 5,
            "maximum_synthetic_panelists": 60,
            "audience_panel": {
                "source": "file",
                "package_path": str(package),
            },
        }
        request_path = run / "study-request.json"
        output_path = run / "study-plan.json"
        request_path.write_bytes(canonical_bytes(request))
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "plan-large-library.py"),
                str(request_path),
                str(output_path),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                str(len(documents["panel"]["segments"])),
                "--boundary-jobs-per-wave",
                "4",
                "--boundary-waves-max",
                "2",
                "--finalist-reserved",
                "4",
                "--assignment-seed",
                "29",
                "--audience-resolution",
                str(run / "audience" / "resolution.json"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stderr + completed.stdout,
        )
        plan = json.loads(output_path.read_text(encoding="utf-8"))
        resolution_path = run / "audience" / "resolution.json"
        self.assertEqual(
            resolution["audience_package"]["package_zip_sha256"],
            plan["audience_package"]["package_zip_sha256"],
        )
        return plan, resolution_path

    def _prepare_and_validate_stage_jobs(
        self,
        *,
        plan: dict[str, object],
        resolution_path: Path,
    ) -> None:
        boundary_authority = dispatch_contract._boundary_authority(plan)
        screening = dispatch_contract.enrich_assignment_jobs(
            plan,
            dispatch_contract._dispatch_context(
                plan, "screening_response"
            ),
            audience_resolution=resolution_path,
        )
        boundary_context = dispatch_contract._dispatch_context(
            plan, "boundary_response"
        )
        boundary_context["boundary_waves"] = [1]
        boundary = dispatch_contract.enrich_assignment_jobs(
            boundary_authority,
            boundary_context,
            manifest=plan,
            audience_resolution=resolution_path,
            allow_directional_allocation=True,
        )
        manifest = dispatch_contract._manifest_from_plan(plan)
        creative_ids = sorted(manifest["outputs"]["creative_asset_hashes"])
        approval = {
            "study_id": plan["study_id"],
            "method": plan["method"],
            "approved_finalist_ids": creative_ids[
                : plan["requested_shortlist_size"]
            ],
            "roster_decision": {
                "status": "approved",
                "approved_at": "2026-07-25T12:00:00Z",
                "approved_by": "study owner",
                "override": False,
                "changed_after_saliency_reveal": False,
            },
        }
        finalist_context = dispatch_contract._dispatch_context(
            plan, "finalist_response"
        )
        finalist_context["requested_job_slots"] = 2
        finalist = dispatch_contract.enrich_assignment_jobs(
            approval,
            finalist_context,
            manifest=manifest,
            audience_resolution=resolution_path,
            allow_directional_allocation=True,
        )
        cases = (
            (
                screening,
                plan["audience_profile_rosters"]["screening"],
                plan,
                plan,
            ),
            (
                boundary,
                plan["audience_profile_rosters"]["boundary_reserve"],
                plan,
                boundary_authority,
            ),
            (
                finalist,
                plan["audience_profile_rosters"]["finalist_reserve"],
                manifest,
                approval,
            ),
        )
        for payload, roster, authority, dispatch_authority in cases:
            self.assertEqual(
                payload,
                dispatch_contract._validate_persisted_v3_jobs(
                    payload,
                    plan=roster,
                    authority=authority,
                    resolution_path=resolution_path,
                    dispatch_authority=dispatch_authority,
                ),
            )

    def _protected_snapshots(self) -> dict[str, object]:
        """Produce every protected pre-B2 output from its production boundary."""

        from audience_lab.complete_exposure import (
            aggregate_complete_exposure,
        )
        from audience_lab.finalists import aggregate_finalists
        from audience_lab.maxdiff import MaxDiffConfig, screen_shortlist
        from audience_lab.pairwise import PairwiseConfig, fit_davidson
        from conformance.test_task9_integration import (
            complete_manifest,
            complete_response,
            finalist_response,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = dispatch_contract._v3_plan(root / "v3")
            context = dispatch_contract._dispatch_context(
                plan, "screening_response"
            )
            v3_job = dispatch_contract.enrich_assignment_jobs(
                plan,
                context,
                audience_resolution=resolution_path,
            )["synthetic_replicate_jobs"][0]
            v2_context = copy.deepcopy(context)
            v2_context["study_id"] = "bound-study"
            v2_job = dispatch_contract._v2_dispatch(
                root / "v2",
                profile_snapshot=v3_job["profile_snapshot"],
                core=copy.deepcopy(
                    plan["assignment"]["synthetic_replicate_jobs"][0]
                ),
                context=v2_context,
            )["synthetic_replicate_jobs"][0]
            protected_job_fields = (
                "reaction_prompts",
                "comparison_prompt",
                "variation_ids",
                "blind_labels",
                "shown_order",
                "reaction_protocol",
            )
            self.assertEqual(
                {key: v2_job[key] for key in protected_job_fields},
                {key: v3_job[key] for key in protected_job_fields},
            )
            enriched_executions = {
                "v2": protected_contract.run_workflow(job=v2_job),
                "v3": protected_contract.run_workflow(job=v3_job),
            }
            self.assertEqual(
                enriched_executions["v2"],
                enriched_executions["v3"],
            )
            enriched_prompt_order = {
                key: copy.deepcopy(v2_job[key])
                for key in protected_job_fields
            }

        execution = protected_contract.run_workflow(
            job=protected_contract.workflow_job("screening_response")
        )
        result = execution["result"]
        complete_records = [
            complete_response(index) for index in range(1, 10)
        ]
        maxdiff_records = json.loads(
            (
                ROOT
                / "conformance"
                / "fixtures"
                / "maxdiff-recovery.json"
            ).read_text(encoding="utf-8")
        )["observations"]
        pairwise_records = protected_contract.load_jsonl_fixture(
            "boundary-responses.jsonl"
        )
        manifest = complete_manifest()
        screening = {
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "validity_status": "valid",
            "selection_status": "resolved",
            "proposed_finalist_ids": ["creative-a", "creative-b"],
        }
        approval = {
            "study_id": manifest["study_id"],
            "approved_finalist_ids": ["creative-a", "creative-b"],
            "roster_decision": {
                "status": "approved",
                "approved_at": "2026-07-22T12:00:00Z",
                "approved_by": "study owner",
                "override": False,
                "changed_after_saliency_reveal": False,
            },
        }
        finalist_records = [
            finalist_response(1, ["creative-a", "creative-b"]),
            finalist_response(2, ["creative-b", "creative-a"]),
            finalist_response(3, ["creative-a", "creative-b"]),
        ]
        return {
            "persona_prompt": (
                ROOT
                / "skills"
                / "audience-ad-testing-lab"
                / "agents"
                / "persona-reviewer-prompt.md"
            ).read_bytes(),
            "enriched_prompt_order": enriched_prompt_order,
            "progressive_calls": execution["calls"],
            "enriched_progressive_calls": enriched_executions["v2"][
                "calls"
            ],
            "validated_response": result["responses"][0],
            "retry_decisions": {
                "rejected_attempts": result["rejected_attempts"],
                "dispatch_audit": result["dispatch_audit"],
            },
            "complete_exposure_scores": aggregate_complete_exposure(
                copy.deepcopy(complete_records),
                study_id="complete-acme-001",
                creative_ids=[
                    "creative-a",
                    "creative-b",
                    "creative-c",
                    "creative-d",
                ],
                top_k=2,
                segment_weights={"segment-1": 1.0},
                seed=19,
            ),
            "maxdiff_results": screen_shortlist(
                copy.deepcopy(maxdiff_records),
                {"S1": 1.0},
                top_k=2,
                config=MaxDiffConfig(
                    penalty_lambda=0.1,
                    bootstrap_count=20,
                    seed=23,
                ),
            ).as_dict(),
            "pairwise_results": fit_davidson(
                copy.deepcopy(pairwise_records),
                PairwiseConfig(
                    tie_parameter=0.4,
                    penalty_lambda=0.1,
                    bootstrap_count=20,
                    seed=29,
                ),
                candidate_ids=("V4", "V5", "V6"),
            ).as_dict(),
            "finalist_summaries": aggregate_finalists(
                copy.deepcopy(manifest),
                copy.deepcopy(screening),
                copy.deepcopy(approval),
                copy.deepcopy(finalist_records),
            ),
            "verbatim_extraction": {
                "raw_provider_returns": result["raw_provider_returns"],
                "per_creative_reactions": result["responses"][0][
                    "per_creative_reactions"
                ],
            },
        }

    def test_public_proxy_source_and_generated_bindings_are_exact(self) -> None:
        frame = json.loads(
            (PUBLIC / "expected-population-frame.json").read_text(
                encoding="utf-8"
            )
        )
        source = json.loads(
            (PUBLIC / "overlay-evidence.json").read_text(encoding="utf-8")
        )
        supported = [
            item
            for item in source["profile_specs"]
            if item["status"] == "supported"
        ]
        self.assertEqual(
            [
                {
                    "conditional_overlay_allocation": 1.0,
                    "overlay_ids": ["proof-seeking", "risk-averse"],
                    "profile_id": "marketing-leader-proxy-v1",
                    "status": "supported",
                    "structural_group_id": "marketing-leader-group",
                    "support_evidence_ids": [
                        "evidence-directional-structure",
                        "evidence-implementation-proof",
                        "evidence-implementation-risk",
                    ],
                    "support_finding_ids": [
                        "finding-directional-structure",
                        "finding-implementation-proof",
                        "finding-implementation-risk",
                    ],
                }
            ],
            supported,
        )
        composition = build_composition_plan(
            population_frame=frame,
            structural_findings=source["structural_findings"],
            overlay_findings=source["overlay_findings"],
            supported_profile_specs=source["profile_specs"],
            requested_tier="tier_2",
            evidence_basis="public",
            plan_id="marketing-leader-proxy-composition",
            plan_version="1.0.0",
            built_at="2026-07-23T12:00:00Z",
        )
        panel = json.loads(
            (PUBLIC / "expected-v3-panel.json").read_text(encoding="utf-8")
        )
        self.assertEqual("tier_1", composition["achieved_tier"])
        self.assertEqual(
            ["no-eligible-population-frame"],
            composition["tier_reason_codes"],
        )
        self.assertEqual(
            {"marketing-leader-proxy-v1"},
            {item["profile_id"] for item in composition["profiles"]},
        )
        self.assertEqual(
            {"marketing-leader-proxy-v1"},
            {
                item["grounded_profile_id"]
                for item in panel["grounded_context_profiles"]
            },
        )
        self.assertEqual(
            sha256_json(composition),
            panel["composition_plan_sha256"],
        )
        self.assertEqual(
            {
                ("persons", "employed-persons-excluding-self-employed"),
                ("firms", "employer-firms"),
                ("establishments", "employer-establishments"),
            },
            {(item["unit"], item["denominator"]) for item in frame["units"]},
        )
        self.assertEqual("experimental", frame["eligibility"])
        self.assertTrue(
            any(
                joint["dimensions"] == ["employment-status", "geography"]
                and not joint["cell_ids"]
                for joint in frame["joints"]
            )
        )

    def test_shipped_scoped_golden_documents_are_not_rewritten_for_runtime(
        self,
    ) -> None:
        cases = (
            (
                self._public_tier_one_documents(),
                PUBLIC,
                {
                    "brief": "expected-v3-brief.json",
                    "panel": "expected-v3-panel.json",
                    "population_frame": "expected-population-frame.json",
                },
            ),
            (
                self._tier_three_documents(),
                (
                    ROOT
                    / "conformance"
                    / "fixtures"
                    / "population"
                    / "authorized-marketplace"
                ),
                {
                    "brief": "expected-v3-brief.json",
                    "panel": "expected-v3-panel.json",
                    "population_frame": "expected-frame.json",
                    "composition": "expected-composition.json",
                    "validity": "expected-validity.json",
                },
            ),
        )
        for documents, fixture_root, filenames in cases:
            for key, filename in filenames.items():
                with self.subTest(
                    panel_id=documents["panel"]["panel_id"],
                    document=key,
                ):
                    self.assertEqual(
                        json.loads(
                            (fixture_root / filename).read_text(
                                encoding="utf-8"
                            )
                        ),
                        documents[key],
                    )
        self.assertEqual(
            [
                (
                    "Directional synthetic ad testing under the named public "
                    "proxy boundary"
                )
            ],
            cases[0][0]["panel"]["governance"]["allowed_uses"],
        )
        self.assertEqual(
            [
                (
                    "Synthetic ad testing for the exact authorized aggregate "
                    "cohort"
                )
            ],
            cases[1][0]["panel"]["governance"]["allowed_uses"],
        )

    def test_package_and_resolution_fail_closed_on_route_permission_forgery(
        self,
    ) -> None:
        cases = (
            (
                "unconfirmed-source",
                copy.deepcopy(self.fixture["bundles"]["tier_2"]),
                lambda documents: documents["population_frame"][
                    "source_bindings"
                ][0]["access"].update(permission_confirmed=False),
            ),
            (
                "internal-only-source",
                copy.deepcopy(self.fixture["bundles"]["tier_2"]),
                lambda documents: documents["population_frame"][
                    "source_bindings"
                ][0]["access"].update(
                    permitted_uses=["internal analysis only"]
                ),
            ),
            (
                "wrong-route-policy",
                copy.deepcopy(self.fixture["bundles"]["tier_2"]),
                lambda documents: documents["panel"]["governance"].update(
                    allowed_uses=[
                        (
                            "Directional synthetic ad testing under the "
                            "named public proxy boundary"
                        )
                    ]
                ),
            ),
            (
                "broadened-tier-three-policy",
                self._tier_three_documents(),
                lambda documents: documents["panel"]["governance"].update(
                    allowed_uses=["Synthetic ad testing"]
                ),
            ),
        )
        for label, documents, mutate in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                mutate(documents)
                forged = self._forge_policy_archive(root / "forged", documents)
                with self.assertRaisesRegex(
                    ValueError,
                    "permission|permitted|allowed_uses|policy",
                ):
                    self._build_package(root / "compiler", documents)
                scope = {
                    key: copy.deepcopy(documents["panel"]["audience_scope"][key])
                    for key in (
                        "audience",
                        "market",
                        "geography",
                        "category",
                        "buying_context",
                        "exclusions",
                    )
                }
                resolution = resolve_audience_v3(
                    package_path=forged,
                    study_scope=scope,
                    run_directory=root / "run",
                )
                self.assertEqual(
                    "incompatible",
                    resolution["resolution_status"],
                )
                self.assertTrue(resolution["resolution_reasons"])

    def test_tier_one_public_tier_two_and_tier_three_reuse_exact_bytes(
        self,
    ) -> None:
        bundles = {
            "public-tier-1": self._public_tier_one_documents(),
            "public-tier-2": copy.deepcopy(self.fixture["bundles"]["tier_2"]),
            "authorized-tier-3": self._tier_three_documents(),
        }
        expected_panel_ids = {
            "public-tier-1": "marketing-leader-public-proxy",
            "public-tier-2": self.fixture["bundles"]["tier_2"]["panel"][
                "panel_id"
            ],
            "authorized-tier-3": "fictional-marketplace-panel",
        }
        expected_tiers = {
            "public-tier-1": ("tier_1", "public"),
            "public-tier-2": ("tier_2", "public"),
            "authorized-tier-3": (
                "tier_3",
                "first_party_aggregate",
            ),
        }
        expected_profile_ids = {
            "public-tier-1": {"marketing-leader-proxy-v1"},
            "public-tier-2": {
                item["grounded_profile_id"]
                for item in self.fixture["bundles"]["tier_2"]["panel"][
                    "grounded_context_profiles"
                ]
            },
            "authorized-tier-3": {
                "finance-pricing-returns",
                "operations-reliability-service",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, documents in bundles.items():
                with self.subTest(label=label):
                    self.assertEqual(
                        expected_panel_ids[label],
                        documents["panel"]["panel_id"],
                    )
                    package = self._build_package(root / label, documents)
                    package_bytes = package.read_bytes()
                    self.assertEqual(
                        GOLDEN_V3_PACKAGE_SHA256[label],
                        hashlib.sha256(package_bytes).hexdigest(),
                    )
                    self.assertEqual(
                        expected_profile_ids[label],
                        {
                            item["grounded_profile_id"]
                            for item in documents["panel"][
                                "grounded_context_profiles"
                            ]
                        },
                    )
                    library_root = root / label / "library"
                    registration = register_package(
                        package,
                        library_root=library_root,
                    )
                    self.assertEqual("registered", registration["status"])
                    registered = find_package(
                        documents["panel"]["panel_id"],
                        documents["panel"]["version"],
                        library_root=library_root,
                    )
                    self.assertEqual(package_bytes, registered.read_bytes())
                    first, first_resolution = self._plan_study(
                        root / label,
                        package=registered,
                        documents=documents,
                        study_id=f"{label}-study-a",
                    )
                    second, second_resolution = self._plan_study(
                        root / label,
                        package=find_package(
                            documents["panel"]["panel_id"],
                            documents["panel"]["version"],
                            library_root=library_root,
                        ),
                        documents=documents,
                        study_id=f"{label}-study-b",
                    )
                    self.assertEqual(package_bytes, package.read_bytes())
                    for study_id in (
                        f"{label}-study-a",
                        f"{label}-study-b",
                    ):
                        self.assertEqual(
                            package_bytes,
                            (
                                root
                                / label
                                / study_id
                                / "audience"
                                / "snapshot"
                                / "audience-panel-package.zip"
                            ).read_bytes(),
                        )
                    self.assertEqual(
                        first["audience_package"]["package_zip_sha256"],
                        second["audience_package"]["package_zip_sha256"],
                    )
                    self.assertEqual(
                        expected_tiers[label],
                        (
                            first["audience_package"]["tier"],
                            first["audience_package"]["evidence_basis"],
                        ),
                    )
                    self.assertNotEqual(
                        first["audience_profile_rosters"]["combined_sha256"],
                        second["audience_profile_rosters"]["combined_sha256"],
                    )
                    self.assertEqual(
                        len(first["assignment"]["synthetic_replicate_jobs"]),
                        first["synthetic_replicate_capacity"][
                            "screening_planned"
                        ],
                    )
                    self.assertEqual(
                        first["synthetic_replicate_capacity"][
                            "required_total"
                        ],
                        (
                            first["synthetic_replicate_capacity"][
                                "screening_planned"
                            ]
                            + first["synthetic_replicate_capacity"][
                                "boundary_reserved"
                            ]
                            + first["synthetic_replicate_capacity"][
                                "finalist_reserved"
                            ]
                        ),
                    )
                    self._prepare_and_validate_stage_jobs(
                        plan=first,
                        resolution_path=first_resolution,
                    )
                    self._prepare_and_validate_stage_jobs(
                        plan=second,
                        resolution_path=second_resolution,
                    )
                    if label == "public-tier-1":
                        self.assertEqual(
                            "directional_tier_1_for_this_run",
                            first["audience_run_claim"],
                        )
                        self.assertNotIn(
                            "frame_fidelity",
                            first["audience_allocation_fidelity"],
                        )
                        self.assertEqual(
                            [
                                {
                                    "overlay_ids": ["risk-averse"],
                                    "reason": (
                                        "No separate structural-overlay pairing "
                                        "is supported."
                                    ),
                                    "reason_code": (
                                        "unsupported-by-approved-evidence"
                                    ),
                                    "structural_group_id": (
                                        "marketing-leader-group"
                                    ),
                                }
                            ],
                            documents["composition"][
                                "unsupported_combinations"
                            ],
                        )

    def test_migrated_tier_one_journey_remains_separate_and_honest(
        self,
    ) -> None:
        migration_contract.AudienceV3MigrationTests.setUpClass()
        try:
            self._run_existing_contract(
                migration_contract.AudienceV3MigrationTests,
                "test_approved_package_emits_only_valid_tier_one_documents",
            )
            self._run_existing_contract(
                migration_contract.AudienceV3MigrationTests,
                (
                    "test_migration_does_not_invent_population_or_"
                    "outcome_evidence"
                ),
            )
        finally:
            migration_contract.AudienceV3MigrationTests.tearDownClass()

    def test_migrated_tier_one_packages_registers_resolves_and_dispatches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents, migrated, source_package = self._migrated_tier_one_documents(
                root
            )
            for key, filename in (
                ("brief", "audience-research-brief-v3.json"),
                ("panel", "saved-audience-panel-v3.json"),
                ("composition", "panel-composition-plan.json"),
                ("validity", "panel-validity-profile.json"),
            ):
                self.assertEqual(migrated[filename], documents[key])
            self.assertIsNone(documents["workflow_state"])
            self.assertIsNone(documents["audit"])
            self.assertEqual(
                "legacy_v2_migration",
                documents["panel"]["audit_binding"]["applicability"],
            )

            with self.assertRaisesRegex(
                ValueError, "migration.*provenance|original.*v2"
            ):
                self._build_package(
                    root / "missing-migration-authority",
                    documents,
                )
            forged_provenance = copy.deepcopy(
                migrated["migration-provenance.json"]
            )
            forged_provenance["source_package"]["package_sha256"] = (
                "sha256:" + "0" * 64
            )
            with self.assertRaisesRegex(
                ValueError, "authentication|rerun|migration"
            ):
                self._build_package(
                    root / "forged-migration-authority",
                    documents,
                    migration_provenance=forged_provenance,
                    source_v2_package=source_package,
                )
            fabricated_audit = copy.deepcopy(documents)
            fabricated_audit["workflow_state"] = copy.deepcopy(
                self.fixture["bundles"]["tier_2"]["workflow_state"]
            )
            fabricated_audit["audit"] = copy.deepcopy(
                self.fixture["bundles"]["tier_2"]["audit"]
            )
            with self.assertRaisesRegex(ValueError, "legacy|migration|null"):
                self._build_package(
                    root / "fabricated-migration-audit",
                    fabricated_audit,
                    migration_provenance=migrated[
                        "migration-provenance.json"
                    ],
                    source_v2_package=source_package,
                )

            package = self._build_package(
                root / "migrated-package",
                documents,
                migration_provenance=migrated["migration-provenance.json"],
                source_v2_package=source_package,
            )
            package_bytes = package.read_bytes()
            library_root = root / "library"
            registration = register_package(
                package,
                library_root=library_root,
            )
            self.assertEqual("registered", registration["status"])
            registered = find_package(
                documents["panel"]["panel_id"],
                documents["panel"]["version"],
                library_root=library_root,
            )
            self.assertEqual(package_bytes, registered.read_bytes())

            plan, resolution_path = self._plan_study(
                root / "migrated-study",
                package=registered,
                documents=documents,
                study_id="migrated-tier-1-study",
            )
            self.assertEqual(
                "tier_1",
                plan["audience_package"]["tier"],
            )
            self.assertEqual(
                "directional_tier_1_for_this_run",
                plan["audience_run_claim"],
            )
            self._prepare_and_validate_stage_jobs(
                plan=plan,
                resolution_path=resolution_path,
            )

    def test_v2_bytes_and_all_protected_worker_outputs_are_unchanged(
        self,
    ) -> None:
        self._run_existing_contract(
            v2_package_contract.AudiencePackageTest,
            "test_build_is_byte_deterministic_and_archive_validates",
        )
        self._run_existing_contract(
            dispatch_contract.V3FrozenProfileDispatchTests,
            "test_v2_enriched_job_and_persona_prompt_bytes_remain_golden",
        )
        self._run_existing_contract(
            protected_contract.ProgressiveWorkflowTests,
            "test_v2_v3_protected_outputs_are_explicitly_equal",
        )
        prompt = (
            ROOT
            / "skills"
            / "audience-ad-testing-lab"
            / "agents"
            / "persona-reviewer-prompt.md"
        )
        self.assertEqual(
            PERSONA_PROMPT_SHA256,
            hashlib.sha256(prompt.read_bytes()).hexdigest(),
        )

    def test_every_pre_b2_output_is_pinned_to_a_literal_sha256(
        self,
    ) -> None:
        snapshots = self._protected_snapshots()
        self.assertEqual(
            PROTECTED_PRE_B2_SHA256,
            {
                name: hashlib.sha256(
                    value
                    if isinstance(value, bytes)
                    else canonical_bytes(value)
                ).hexdigest()
                for name, value in snapshots.items()
            },
        )

    def test_complete_exposure_keeps_boundary_not_applicable(self) -> None:
        self._run_existing_contract(
            roster_contract.V3ProfileRosterTests,
            "test_complete_exposure_preserves_zero_boundary_capacity_with_strict_not_applicable_record",
        )
        self._run_existing_contract(
            dispatch_contract.V3FrozenProfileDispatchTests,
            "test_complete_exposure_boundary_is_not_dispatchable_or_gated",
        )

    def test_distortion_selected_prefix_and_directional_gates_fail_closed(
        self,
    ) -> None:
        for case_type, method_name in (
            (
                roster_contract.V3ProfileRosterTests,
                "test_screening_distortion_exits_six_until_directional_use_is_accepted",
            ),
            (
                dispatch_contract.V3FrozenProfileDispatchTests,
                "test_tier_one_subset_gates_only_missing_must_cover_coverage",
            ),
            (
                dispatch_contract.V3FrozenProfileDispatchTests,
                "test_non_prefix_boundary_selection_fails_before_subset_gating",
            ),
        ):
            with self.subTest(method_name=method_name):
                self._run_existing_contract(case_type, method_name)

    def test_all_dispatch_and_dashboard_bindings_fail_closed_on_tamper(
        self,
    ) -> None:
        for case_type, method_name in (
            (
                dispatch_contract.V3FrozenProfileDispatchTests,
                "test_v3_dispatch_rejects_every_mutable_profile_binding",
            ),
            (
                dispatch_contract.V3FrozenProfileDispatchTests,
                "test_jobs_envelope_validation_is_an_authorization_boundary",
            ),
            (
                dashboard_contract.DashboardV3AllocationTests,
                "test_v3_allocation_index_and_bound_job_envelopes_fail_closed",
            ),
        ):
            with self.subTest(method_name=method_name):
                self._run_existing_contract(case_type, method_name)

    def test_multi_wave_dashboard_uses_latest_cumulative_subset_authority(
        self,
    ) -> None:
        self._run_existing_contract(
            dashboard_contract.DashboardV3AllocationTests,
            "test_partial_reserve_displays_full_and_selected_scope_with_selected_authority",
        )


if __name__ == "__main__":
    unittest.main()
