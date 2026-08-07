from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from audience_lab.dashboard import _validate_lineage_integrity
from audience_lab.lineage import (
    CANONICAL_LINEAGE_FILES,
    materialize_workflow_lineage,
    validate_lineage_records,
)
from conformance.test_progressive_workflow import run_workflow
from conformance.test_task9_integration import complete_manifest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"


def _manifest() -> dict:
    manifest = complete_manifest()
    manifest["study_id"] = "study-screening-001"
    manifest["method"] = "partial_exposure_maxdiff"
    return manifest


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _standalone_module():
    spec = importlib.util.spec_from_file_location(
        "task9_wave3_validate_dashboard", ROOT / "skills/audience-ad-testing-lab/scripts/validate-dashboard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_result(result: dict) -> dict:
    return validate_lineage_records(
        result["responses"],
        result["raw_provider_returns"],
        result["rejected_attempts"],
        result["dispatch_audit"],
        manifest=_manifest(),
    )


def _accepted_reaction(result: dict, position: int) -> dict:
    return next(
        item
        for item in result["raw_provider_returns"]
        if item["stage"] == "reaction"
        and item["position_seen"] == position
        and item["accepted"] is True
    )


class ProductionExhaustionLineageTests(unittest.TestCase):
    def test_real_incomplete_workflow_retains_component_calls_across_all_surfaces(self):
        result = run_workflow(exhaust_first_reaction=True)["result"]

        self.assertEqual("incomplete", result["status"])
        self.assertEqual([], result["responses"])
        self.assertEqual(5, len(result["raw_provider_returns"]))
        self.assertEqual(
            3,
            sum(item["accepted"] for item in result["raw_provider_returns"]),
        )
        self.assertEqual(2, len(result["rejected_attempts"]))

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            bound = materialize_workflow_lineage(result, _manifest(), run_dir)
            self.assertEqual(1, bound["usage"]["unique_job_slots_dispatched"])
            self.assertEqual(0, bound["usage"]["accepted_response_records"])
            self.assertEqual(0, bound["usage"]["accepted_unique_replicates"])
            self.assertEqual(5, bound["usage"]["total_model_calls"])
            self.assertEqual(2, bound["usage"]["rejected_attempts"])

            _validate_lineage_integrity(run_dir, bound, [])

            records = {
                filename: _load_jsonl(run_dir / filename)
                for filename in CANONICAL_LINEAGE_FILES.values()
            }
            exports = []
            for filename in CANONICAL_LINEAGE_FILES.values():
                content = (run_dir / filename).read_bytes()
                exports.append(
                    {
                        "filename": filename,
                        "data_url": "data:application/x-ndjson;base64,"
                        + base64.b64encode(content).decode("ascii"),
                        "content_hash": "sha256:"
                        + hashlib.sha256(content).hexdigest(),
                    }
                )
            errors = _standalone_module()._validate_lineage_sources(
                {"exports": exports}, bound, [], records
            )
            self.assertEqual([], errors)

    def test_real_incomplete_shape_still_rejects_invalid_lineage(self):
        original = run_workflow(exhaust_first_reaction=True)["result"]

        cases = {}
        invalid_position = deepcopy(original)
        _accepted_reaction(invalid_position, 2)["position_seen"] = 0
        cases["invalid_position"] = (invalid_position, "position_seen")

        unauthorized_position = deepcopy(original)
        _accepted_reaction(unauthorized_position, 2)["position_seen"] = 99
        cases["unauthorized_position"] = (
            unauthorized_position,
            "authorized attempt contract",
        )

        numbering_gap = deepcopy(original)
        _accepted_reaction(numbering_gap, 2)["attempt_number"] = 2
        cases["numbering_gap"] = (numbering_gap, "start at 1 and be contiguous")

        duplicate_attempt = deepcopy(original)
        duplicate = deepcopy(_accepted_reaction(duplicate_attempt, 2))
        duplicate["provider_return_id"] += "-forged"
        duplicate_attempt["raw_provider_returns"].append(duplicate)
        cases["duplicate_semantic_attempt"] = (
            duplicate_attempt,
            "duplicate raw attempt identity",
        )

        unauthorized_call = deepcopy(original)
        outside = deepcopy(_accepted_reaction(unauthorized_call, 2))
        outside.update(
            provider_return_id="provider-outside-dispatch-audit",
            synthetic_replicate_id="replicate-outside-dispatch-audit",
            reviewer_dispatch_id="dispatch-outside-dispatch-audit",
        )
        unauthorized_call["raw_provider_returns"].append(outside)
        cases["unauthorized_call"] = (
            unauthorized_call,
            "outside dispatch_audit",
        )

        missing_terminal = deepcopy(original)
        terminal = next(
            item
            for item in missing_terminal["raw_provider_returns"]
            if item["stage"] == "reaction"
            and item["position_seen"] == 1
            and item["attempt_number"] == 2
        )
        missing_terminal["raw_provider_returns"].remove(terminal)
        missing_terminal["rejected_attempts"] = [
            item
            for item in missing_terminal["rejected_attempts"]
            if item["provider_return_id"] != terminal["provider_return_id"]
        ]
        cases["missing_terminal_retry"] = (
            missing_terminal,
            "exactly reconcile",
        )

        for name, (result, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                _validate_result(result)

    def test_incomplete_dispatch_rejects_an_accepted_composite_response(self):
        complete = run_workflow()["result"]
        self.assertEqual("complete", complete["status"])
        self.assertEqual(1, len(complete["responses"]))
        complete["dispatch_audit"][0]["accepted"] = False

        with self.assertRaisesRegex(
            ValueError, "accepted response must map to an accepted dispatch audit"
        ):
            _validate_result(complete)

    def test_materializer_rejects_complete_label_for_incomplete_workflow(self):
        result = run_workflow(exhaust_first_reaction=True)["result"]
        result["status"] = "complete"

        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError,
            "workflow.status must be incomplete",
        ):
            materialize_workflow_lineage(result, _manifest(), Path(directory))


if __name__ == "__main__":
    unittest.main()
