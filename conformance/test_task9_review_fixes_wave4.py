from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audience_lab.dashboard import DashboardInputError, _validate_lineage_integrity
from audience_lab.lineage import materialize_workflow_lineage
from conformance.test_progressive_workflow import run_workflow
from conformance.test_task9_review_fixes_wave2 import (
    _bind_without_semantic_validation,
    _standalone_module,
)
from conformance.test_task9_review_fixes_wave3 import _manifest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"


def _rejected_from_raw(raw: dict) -> dict:
    record = {
        key: deepcopy(raw[key])
        for key in (
            "provider_return_id",
            "synthetic_replicate_id",
            "reviewer_dispatch_id",
            "stage",
            "attempt_number",
            "validation_errors",
        )
    }
    if raw["stage"] == "reaction":
        record["position_seen"] = raw["position_seen"]
    record["disposition"] = (
        "retry_exhausted" if raw["attempt_number"] == 2 else "retried"
    )
    return record


def _comparison_exhaustion() -> dict:
    result = deepcopy(run_workflow()["result"])
    terminal = next(
        item
        for item in result["raw_provider_returns"]
        if item["stage"] == "comparison" and item["attempt_number"] == 2
    )
    terminal["accepted"] = False
    terminal["validation_errors"] = ["comparison return failed validation"]
    result["rejected_attempts"].append(_rejected_from_raw(terminal))
    result["responses"] = []
    result["status"] = "incomplete"
    result["completed_replicates"] = 0
    result["missing_synthetic_replicate_ids"] = [
        result["dispatch_audit"][0]["synthetic_replicate_id"]
    ]
    result["dispatch_audit"][0]["accepted"] = False
    return result


def _assert_three_surfaces_accept(test: unittest.TestCase, workflow: dict) -> None:
    with tempfile.TemporaryDirectory() as directory:
        run_dir = Path(directory) / "materialized"
        bound = materialize_workflow_lineage(workflow, _manifest(), run_dir)
        _validate_lineage_integrity(run_dir, bound, workflow["responses"])
        test.assertEqual(
            len(workflow["raw_provider_returns"]),
            bound["usage"]["total_model_calls"],
        )
        test.assertEqual(
            len(workflow["raw_provider_returns"]),
            bound["outputs"]["raw_provider_returns"]["record_count"],
        )
        sources = {
            filename: [
                json.loads(line)
                for line in (run_dir / filename).read_text(encoding="utf-8").splitlines()
                if line
            ]
            for filename in (
                "panelist-responses.jsonl",
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "dispatch-audit.jsonl",
            )
        }
        payload = {
            "exports": [
                {
                    "filename": filename,
                    "data_url": "data:application/x-ndjson;base64,"
                    + base64.b64encode((run_dir / filename).read_bytes()).decode(
                        "ascii"
                    ),
                }
                for filename in sources
            ]
        }
        errors = _standalone_module()._validate_lineage_sources(
            payload, bound, workflow["responses"], sources
        )
        test.assertEqual([], errors)


def _assert_three_surfaces_reject(test: unittest.TestCase, workflow: dict) -> None:
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        with test.assertRaises(ValueError):
            materialize_workflow_lineage(
                workflow, _manifest(), temp / "materialized"
            )
        run_dir, bound, payload, sources = _bind_without_semantic_validation(
            temp, _manifest(), workflow
        )
        with test.assertRaises(DashboardInputError):
            _validate_lineage_integrity(run_dir, bound, workflow["responses"])
        errors = _standalone_module()._validate_lineage_sources(
            payload, bound, workflow["responses"], sources
        )
        test.assertTrue(errors)


class CleanEnvironmentE2ETests(unittest.TestCase):
    def test_standalone_e2e_runs_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            environment["PYTHONPYCACHEPREFIX"] = str(Path(directory) / "pycache")
            completed = subprocess.run(
                [
                    sys.executable,
                    "conformance/run_large_library_e2e.py",
                    "--output-report",
                    str(report),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("valid", payload["screening_status"])
        self.assertEqual("incomplete", payload["exhausted_workflow_status"])
        self.assertEqual(216, payload["usage"]["total_model_calls"])


class GenericIncompleteLineageTests(unittest.TestCase):
    def test_valid_reaction_and_comparison_exhaustion_pass_every_surface(self):
        reaction = run_workflow(exhaust_first_reaction=True)["result"]
        comparison = _comparison_exhaustion()

        self.assertEqual(
            [2, 1, 1, 1], reaction["dispatch_audit"][0]["reaction_attempts"]
        )
        self.assertEqual(0, reaction["dispatch_audit"][0]["comparison_attempts"])
        self.assertEqual(5, len(reaction["raw_provider_returns"]))
        self.assertEqual(2, len(reaction["rejected_attempts"]))
        self.assertEqual(
            [2, 1, 1, 1], comparison["dispatch_audit"][0]["reaction_attempts"]
        )
        self.assertEqual(2, comparison["dispatch_audit"][0]["comparison_attempts"])
        self.assertEqual(7, len(comparison["raw_provider_returns"]))
        self.assertEqual(3, len(comparison["rejected_attempts"]))
        _assert_three_surfaces_accept(self, reaction)
        _assert_three_surfaces_accept(self, comparison)

    def test_missing_real_concurrent_calls_fail_every_surface(self):
        missing_reaction = deepcopy(
            run_workflow(exhaust_first_reaction=True)["result"]
        )
        accepted = next(
            item
            for item in missing_reaction["raw_provider_returns"]
            if item["stage"] == "reaction"
            and item["position_seen"] == 2
            and item["accepted"] is True
        )
        missing_reaction["raw_provider_returns"].remove(accepted)

        missing_comparison = _comparison_exhaustion()
        terminal = next(
            item
            for item in missing_comparison["raw_provider_returns"]
            if item["stage"] == "comparison" and item["attempt_number"] == 2
        )
        missing_comparison["raw_provider_returns"].remove(terminal)
        missing_comparison["rejected_attempts"] = [
            item
            for item in missing_comparison["rejected_attempts"]
            if item["provider_return_id"] != terminal["provider_return_id"]
        ]

        _assert_three_surfaces_reject(self, missing_reaction)
        _assert_three_surfaces_reject(self, missing_comparison)

    def test_audit_attempt_counts_must_exactly_match_raw_calls(self):
        reaction_lie = deepcopy(run_workflow(exhaust_first_reaction=True)["result"])
        reaction_lie["dispatch_audit"][0]["reaction_attempts"][1] = 2

        comparison_lie = _comparison_exhaustion()
        comparison_lie["dispatch_audit"][0]["comparison_attempts"] = 1

        _assert_three_surfaces_reject(self, reaction_lie)
        _assert_three_surfaces_reject(self, comparison_lie)


if __name__ == "__main__":
    unittest.main()
