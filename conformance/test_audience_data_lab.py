from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "audience-data-lab"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_data_lab.common import ContractError  # noqa: E402
from audience_data_lab.pipeline import (  # noqa: E402
    approve_handoff,
    prepare_private_evidence,
    validate_handoff,
    validate_intake,
)


class AudienceDataLabTests(unittest.TestCase):
    def base_intake(self):
        return {
            "schema_version": "audience-private-data-intake-v1",
            "project_id": "crm-panel-evidence",
            "created_at": "2026-07-23T12:00:00Z",
            "data_kind": "crm",
            "purpose": "Ground a reusable operations-leader audience panel.",
            "covered_population": "Permissioned CRM contacts with buying influence",
            "time_window": {
                "start": "2025-01-01",
                "end": "2026-06-30",
                "timezone": "America/New_York",
            },
            "permission": {
                "confirmed": True,
                "confirmed_by": "data-owner",
                "confirmed_at": "2026-07-23T12:00:00Z",
                "data_owner": "Acme",
                "legal_or_contract_basis": "Approved internal analysis",
                "note": "",
            },
            "columns": {
                "entity_id": "contact_id",
                "direct_identifiers": ["email"],
                "quasi_identifiers": ["title", "region"],
                "sensitive": [],
                "dimensions": ["industry", "buying_stage"],
                "metrics": ["annual_value"],
                "outcome": None,
                "event_date": None,
                "ignored": [],
            },
            "privacy": {
                "minimum_cell_size": 5,
                "release_mode": "aggregate_only",
                "privacy_budget_epsilon": None,
                "suppress_rare_values": True,
                "allow_synthetic_release": False,
            },
            "analysis": {
                "generate_cross_tabs": True,
                "max_cross_tab_dimensions": 2,
                "modeling_mode": "segment_candidates",
                "feature_columns": ["industry", "buying_stage", "annual_value"],
                "cluster_counts": [2, 3],
                "model_seed": 17,
                "minimum_model_rows": 20,
                "temporal_holdout_fraction": 0.2,
            },
            "allowed_uses": ["audience_panel_research"],
            "prohibited_uses": ["individual_targeting"],
            "retention": {
                "raw_input_action": "retain_in_place",
                "working_copy_action": "delete_working_copy",
                "deadline": "2026-07-24T12:00:00Z",
                "approved_by": "data-owner",
            },
        }

    def crm_rows(self):
        rows = []
        for index in range(60):
            first_group = index < 30
            rows.append(
                {
                    "contact_id": f"contact-{index:03d}",
                    "email": f"person{index}@example.com",
                    "title": "VP Operations" if first_group else "COO",
                    "region": "Northeast" if index != 59 else "Rare region",
                    "industry": "Software" if first_group else "Manufacturing",
                    "buying_stage": "Evaluation" if first_group else "Awareness",
                    "annual_value": str(100 + index if first_group else 900 + index),
                }
            )
        return rows

    def write_csv(self, directory, name, rows):
        path = Path(directory) / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_crm_path_releases_aggregate_evidence_without_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_csv(directory, "crm.csv", self.crm_rows())
            audit, handoff, report = prepare_private_evidence(
                source, self.base_intake()
            )
        self.assertEqual("audience-private-data-audit-v1", audit["schema_version"])
        self.assertEqual("audience-first-party-evidence-v1", handoff["schema_version"])
        self.assertEqual(60, audit["entity_count"])
        self.assertEqual(
            "exploratory_candidate_available",
            handoff["segment_candidates"]["status"],
        )
        serialized = json.dumps(handoff)
        self.assertNotIn("person0@example.com", serialized)
        self.assertNotIn("contact-000", serialized)
        self.assertNotIn("Rare region", serialized)
        self.assertIn("No raw rows were released", report)
        validate_handoff(handoff)

    def test_column_classification_is_exhaustive_and_nonoverlapping(self):
        intake = self.base_intake()
        intake["columns"]["dimensions"].append("email")
        with self.assertRaises(ContractError):
            validate_intake(intake)
        with tempfile.TemporaryDirectory() as directory:
            rows = self.crm_rows()
            rows[0]["unclassified"] = "not allowed"
            for row in rows[1:]:
                row["unclassified"] = ""
            source = self.write_csv(directory, "crm.csv", rows)
            with self.assertRaises(ContractError):
                prepare_private_evidence(source, self.base_intake())

    def test_permission_and_advanced_privacy_fail_before_processing(self):
        intake = self.base_intake()
        intake["permission"]["confirmed"] = False
        with self.assertRaises(ContractError):
            validate_intake(intake)
        intake = self.base_intake()
        intake["privacy"]["release_mode"] = "differential_privacy"
        with self.assertRaises(ContractError):
            validate_intake(intake)
        intake = self.base_intake()
        intake["privacy"]["allow_synthetic_release"] = True
        with self.assertRaises(ContractError):
            validate_intake(intake)

    def performance_intake(self):
        intake = self.base_intake()
        intake["project_id"] = "campaign-performance"
        intake["data_kind"] = "performance"
        intake["purpose"] = "Evaluate historical qualified-lead prediction."
        intake["covered_population"] = "Permissioned campaign delivery records"
        intake["columns"] = {
            "entity_id": "account_id",
            "direct_identifiers": ["account_name"],
            "quasi_identifiers": ["region"],
            "sensitive": [],
            "dimensions": ["industry", "platform"],
            "metrics": ["spend", "impressions"],
            "outcome": "qualified_lead",
            "event_date": "event_date",
            "ignored": [],
        }
        intake["analysis"] = {
            "generate_cross_tabs": True,
            "max_cross_tab_dimensions": 2,
            "modeling_mode": "performance_prediction",
            "feature_columns": ["industry", "platform", "spend", "impressions"],
            "cluster_counts": [],
            "model_seed": 11,
            "minimum_model_rows": 80,
            "temporal_holdout_fraction": 0.2,
        }
        intake["allowed_uses"] = ["ad_test_calibration"]
        return intake

    def performance_rows(self):
        rows = []
        for index in range(140):
            strong = index % 2 == 0
            rows.append(
                {
                    "account_id": f"account-{index:03d}",
                    "account_name": f"Private Account {index}",
                    "region": "United States",
                    "industry": "Software" if strong else "Manufacturing",
                    "platform": "LinkedIn",
                    "spend": str(500 + index * 3),
                    "impressions": str(3000 + index * 25),
                    "qualified_lead": "1" if strong else "0",
                    "event_date": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
                }
            )
        return rows

    def test_performance_path_uses_chronological_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_csv(
                directory, "performance.csv", self.performance_rows()
            )
            _, handoff, _ = prepare_private_evidence(
                source, self.performance_intake()
            )
        self.assertEqual("audience-performance-evidence-v1", handoff["schema_version"])
        self.assertEqual(
            "retrospectively_evaluated",
            handoff["model_results"]["validation_state"],
        )
        self.assertEqual("chronological", handoff["temporal_split"]["strategy"])
        self.assertLess(
            handoff["temporal_split"]["train_end"],
            handoff["temporal_split"]["holdout_start"],
        )
        self.assertIn(
            "Retrospective model evaluation",
            handoff["calibration_scope"]["claim"],
        )
        self.assertNotIn("Private Account", json.dumps(handoff))

    def test_handoff_schema_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_csv(directory, "crm.csv", self.crm_rows())
            _, handoff, _ = prepare_private_evidence(
                source, self.base_intake()
            )
        invalid = copy.deepcopy(handoff)
        invalid["helpful_extra"] = True
        with self.assertRaises(ContractError):
            validate_handoff(invalid)

    def test_approval_changes_only_status_and_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_csv(directory, "crm.csv", self.crm_rows())
            _, handoff, _ = prepare_private_evidence(
                source, self.base_intake()
            )
        approval = {
            "schema_version": "audience-evidence-approval-v1",
            "approved_for_downstream_use": True,
            "approved_by": "data-owner",
            "approved_at": "2026-07-23T15:00:00Z",
            "approval_note": "Approved aggregate evidence for panel research.",
        }
        approved = approve_handoff(handoff, approval)
        self.assertEqual("approved", approved["status"])
        frozen_draft = {
            key: value
            for key, value in handoff.items()
            if key not in {"status", "approval"}
        }
        frozen_approved = {
            key: value
            for key, value in approved.items()
            if key not in {"status", "approval"}
        }
        self.assertEqual(frozen_draft, frozen_approved)
        with self.assertRaises(ContractError):
            approve_handoff(approved, approval)

    def test_approved_handoffs_cross_skill_boundaries_without_raw_rows(self):
        approval = {
            "schema_version": "audience-evidence-approval-v1",
            "approved_for_downstream_use": True,
            "approved_by": "data-owner",
            "approved_at": "2026-07-23T15:00:00Z",
            "approval_note": "Approved aggregate evidence.",
        }
        with tempfile.TemporaryDirectory() as directory:
            crm_source = self.write_csv(directory, "crm.csv", self.crm_rows())
            _, crm_draft, _ = prepare_private_evidence(
                crm_source, self.base_intake()
            )
            crm_path = Path(directory) / "first-party.json"
            crm_path.write_text(
                json.dumps(approve_handoff(crm_draft, approval)),
                encoding="utf-8",
            )
            panel_validation = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "skills"
                        / "audience-panel-builder"
                        / "scripts"
                        / "validate-data-handoff.py"
                    ),
                    str(crm_path),
                    "--expected",
                    "first_party",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                0, panel_validation.returncode, panel_validation.stderr
            )

            performance_source = self.write_csv(
                directory, "performance.csv", self.performance_rows()
            )
            _, performance_draft, _ = prepare_private_evidence(
                performance_source, self.performance_intake()
            )
            performance_path = Path(directory) / "performance-evidence.json"
            performance_path.write_text(
                json.dumps(approve_handoff(performance_draft, approval)),
                encoding="utf-8",
            )
            ad_validation = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "skills"
                        / "audience-ad-testing-lab"
                        / "scripts"
                        / "validate-performance-evidence.py"
                    ),
                    str(performance_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, ad_validation.returncode, ad_validation.stderr)

    def test_cli_writes_individually_openable_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_csv(directory, "crm.csv", self.crm_rows())
            intake_path = Path(directory) / "intake.json"
            intake_path.write_text(
                json.dumps(self.base_intake()), encoding="utf-8"
            )
            output = Path(directory) / "output"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "prepare-private-evidence.py"),
                    str(source),
                    str(intake_path),
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((output / "data-methodology-report.html").is_file())
            self.assertTrue((output / "private-data-audit.json").is_file())
            handoff = output / "audience-first-party-evidence.json"
            self.assertTrue(handoff.is_file())
            validation = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-private-evidence.py"),
                    str(handoff),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, validation.returncode, validation.stderr)
            repeated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "prepare-private-evidence.py"),
                    str(source),
                    str(intake_path),
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, repeated.returncode)
            self.assertIn("never overwritten", repeated.stderr)

            approval_path = Path(directory) / "approval.json"
            approval_path.write_text(
                json.dumps(
                    {
                        "schema_version": "audience-evidence-approval-v1",
                        "approved_for_downstream_use": True,
                        "approved_by": "data-owner",
                        "approved_at": "2026-07-23T15:00:00Z",
                        "approval_note": "Approved aggregate evidence.",
                    }
                ),
                encoding="utf-8",
            )
            approved_path = Path(directory) / "approved.json"
            approved = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "approve-private-evidence.py"),
                    str(handoff),
                    str(approval_path),
                    str(approved_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, approved.returncode, approved.stderr)
            repeated_approval = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "approve-private-evidence.py"),
                    str(handoff),
                    str(approval_path),
                    str(approved_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, repeated_approval.returncode)
            self.assertIn("never overwritten", repeated_approval.stderr)


if __name__ == "__main__":
    unittest.main()
