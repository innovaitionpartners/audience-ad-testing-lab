from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from conformance.test_task9_integration import (
    ROOT,
    complete_job,
    complete_response,
    finalist_response,
    load_json,
    run_cli,
    write_json,
    write_jsonl,
)
from conformance.test_task9_review_fixes import (
    calibrated_manifest,
    complete_inputs,
    run_complete,
)


def _policy() -> dict:
    return load_json(ROOT / "skills/audience-ad-testing-lab/references/complete-exposure-calibration-config.json")


def _mutate_reflective(response: dict) -> None:
    response["reaction_protocol"] = "reflective_reaction_caveat"
    reactions = response.get("per_creative_reactions", response.get("finalist_reviews", []))
    for reaction in reactions:
        reaction["reaction_label"] = "reflective"


def _mutate_blind_labels(response: dict) -> None:
    labels = list(response["blind_labels"].values())
    labels = labels[1:] + labels[:1]
    response["blind_labels"] = dict(zip(response["shown_order"], labels))
    reactions = response.get("per_creative_reactions", response.get("finalist_reviews", []))
    for reaction in reactions:
        reaction["display_label_seen"] = response["blind_labels"][reaction["variation_id"]]


def _mutate_shown_order(response: dict) -> None:
    response["shown_order"] = list(reversed(response["shown_order"]))
    reactions = response.get("per_creative_reactions", response.get("finalist_reviews", []))
    by_creative = {item["variation_id"]: item for item in reactions}
    reordered = []
    for position, creative_id in enumerate(response["shown_order"], 1):
        reaction = by_creative[creative_id]
        reaction["position_seen"] = position
        reaction["display_label_seen"] = response["blind_labels"][creative_id]
        reordered.append(reaction)
    if "per_creative_reactions" in response:
        response["per_creative_reactions"] = reordered
        response["complete_set_evaluation"]["frozen_reaction_ids"] = [
            item["reaction_id"] for item in reordered
        ]
    else:
        response["finalist_reviews"] = reordered


def _mutate_assigned_roster(response: dict) -> None:
    prior = response["assigned_variation_ids"][-1]
    forged = "creative-forged-but-contract-valid"
    response["assigned_variation_ids"] = [
        forged if item == prior else item
        for item in response["assigned_variation_ids"]
    ]
    response["shown_order"] = [
        forged if item == prior else item for item in response["shown_order"]
    ]
    label = response["blind_labels"].pop(prior)
    response["blind_labels"][forged] = label
    reactions = response.get("per_creative_reactions", response.get("finalist_reviews", []))
    for reaction in reactions:
        if reaction["variation_id"] == prior:
            reaction["variation_id"] = forged
            reaction["display_label_seen"] = label
    if "complete_set_evaluation" in response:
        ranking = response["complete_set_evaluation"]["preference_ranking"]
        response["complete_set_evaluation"]["preference_ranking"] = [
            forged if item == prior else item for item in ranking
        ]
    else:
        response["final_preference_ranking"] = [
            forged if item == prior else item
            for item in response["final_preference_ranking"]
        ]


def _valid_binding_mutations() -> tuple[tuple[str, object], ...]:
    return (
        ("profile_snapshot", lambda response: response.__setitem__(
            "profile_snapshot", {"profile_snapshot_id": "forged-valid-snapshot"}
        )),
        ("context_attribute_provenance", lambda response: response.__setitem__(
            "context_attribute_provenance",
            [{
                "attribute": "buying_stage",
                "value": "forged_but_valid",
                "status": "estimated",
                "source_evidence": ["approved-research-brief:forged"],
            }],
        )),
        ("worker_context_isolation", lambda response: response.__setitem__(
            "worker_context_isolation", "shared_context_fallback"
        )),
        ("human_sample_independence", lambda response: response.__setitem__(
            "human_sample_independence", True
        )),
        ("reaction_protocol", _mutate_reflective),
        ("study_id", lambda response: response.__setitem__("study_id", "forged-study")),
        ("response_id", lambda response: response.__setitem__("response_id", "forged-response")),
        ("synthetic_replicate_id", lambda response: response.__setitem__(
            "synthetic_replicate_id", "forged-replicate"
        )),
        ("reviewer_dispatch_id", lambda response: response.__setitem__(
            "reviewer_dispatch_id", "forged-dispatch"
        )),
        ("persona_archetype_id", lambda response: response.__setitem__(
            "persona_archetype_id", "forged-archetype"
        )),
        ("segment_id", lambda response: response.__setitem__("segment_id", "forged-segment")),
        ("record_type", lambda response: response.__setitem__(
            "record_type", "boundary_response"
        )),
        ("method", lambda response: response.__setitem__(
            "method", "partial_exposure_maxdiff"
        )),
        ("assigned_variation_ids", lambda response: response.__setitem__(
            "assigned_variation_ids", list(reversed(response["assigned_variation_ids"]))
        )),
        ("assigned_roster", _mutate_assigned_roster),
        ("blind_labels", _mutate_blind_labels),
        ("shown_order", _mutate_shown_order),
    )


_CONTRACT_VALID_FORGERIES = {
    "profile_snapshot",
    "context_attribute_provenance",
    "worker_context_isolation",
    "reaction_protocol",
    "study_id",
    "response_id",
    "synthetic_replicate_id",
    "reviewer_dispatch_id",
    "persona_archetype_id",
    "segment_id",
    "assigned_variation_ids",
    "assigned_roster",
    "blind_labels",
}


def _finalist_inputs() -> tuple[dict, dict, dict, dict, list[dict]]:
    manifest = calibrated_manifest()
    screening = {
        "study_id": manifest["study_id"],
        "method": manifest["method"],
        "validity_status": "valid",
        "selection_status": "resolved",
        "proposed_finalist_ids": ["creative-a", "creative-b"],
    }
    approval = {
        "study_id": manifest["study_id"],
        "approved_finalist_ids": ["creative-a", "creative-b"],
        "roster_decision": {
            "status": "approved",
            "override": False,
            "approved_at": "2026-07-22T12:00:00Z",
            "approved_by": "study owner",
        },
    }
    responses = [
        finalist_response(index, ["creative-a", "creative-b"])
        for index in range(1, 4)
    ]
    jobs = {
        "study_id": manifest["study_id"],
        "method": manifest["method"],
        "record_type": "finalist_response",
        "synthetic_replicate_jobs": [complete_job(response) for response in responses],
    }
    return manifest, screening, approval, jobs, responses


def _run_finalists(
    temp: Path,
    manifest: dict,
    screening: dict,
    approval: dict,
    jobs: dict,
    responses: list[dict],
):
    paths = {
        name: temp / filename
        for name, filename in (
            ("manifest", "manifest.json"),
            ("screening", "screening.json"),
            ("approval", "approval.json"),
            ("jobs", "jobs.json"),
            ("responses", "responses.jsonl"),
            ("output", "output.json"),
        )
    }
    for name, payload in (
        ("manifest", manifest),
        ("screening", screening),
        ("approval", approval),
        ("jobs", jobs),
    ):
        write_json(paths[name], payload)
    write_jsonl(paths["responses"], responses)
    completed = run_cli(
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "finalists",
        "--manifest", str(paths["manifest"]),
        "--screening-results", str(paths["screening"]),
        "--approval", str(paths["approval"]),
        "--jobs", str(paths["jobs"]),
        "--responses", str(paths["responses"]),
        "--output", str(paths["output"]),
    )
    return completed, load_json(paths["output"])


class CanonicalJobBindingWave2Tests(unittest.TestCase):
    def test_complete_cli_rejects_every_valid_but_forged_job_field(self):
        from audience_lab.responses import validate_response

        for field, mutate in _valid_binding_mutations():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manifest, jobs, responses = complete_inputs()
                mutate(responses[0])
                if field in _CONTRACT_VALID_FORGERIES:
                    self.assertEqual([], validate_response(responses[0]), field)
                completed, payload = run_complete(
                    Path(directory), manifest, jobs, responses, policy=_policy()
                )
                self.assertEqual(4, completed.returncode, completed.stderr)
                self.assertNotEqual("valid", payload.get("validity_status"))

    def test_finalist_cli_rejects_every_valid_but_forged_job_field(self):
        from audience_lab.responses import validate_response

        for field, mutate in _valid_binding_mutations():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manifest, screening, approval, jobs, responses = _finalist_inputs()
                mutate(responses[0])
                if field in _CONTRACT_VALID_FORGERIES:
                    self.assertEqual([], validate_response(responses[0]), field)
                completed, payload = _run_finalists(
                    Path(directory), manifest, screening, approval, jobs, responses
                )
                self.assertEqual(4, completed.returncode, completed.stderr)
                self.assertNotEqual("valid", payload.get("status"))


def _dispatch_context(study_id: str, record_type: str) -> dict:
    return {
        "study_id": study_id,
        "record_type": record_type,
        "reaction_protocol": "progressive_reveal",
        "worker_context_isolation": "isolated",
        "profiles": [{
            "segment_id": "segment-1",
            "persona_archetype_id": "archetype-1",
            "profile_snapshot": {"profile_snapshot_id": "snapshot-1"},
            "context_attribute_provenance": [{
                "attribute": "buying_stage",
                "value": "evaluation",
                "status": "observed",
                "source_evidence": ["research:E1"],
            }],
        }],
        "creative_prompts": {
            "creative-a": "Review A.",
            "creative-b": "Review B.",
            **{f"V{index}": f"Review V{index}." for index in range(1, 11)},
        },
        "comparison_prompts": {
            "complete_exposure": "Rank the frozen set.",
            "partial_exposure_maxdiff": "Compare the frozen set.",
        },
    }


class DispatchAuthorityWave2Tests(unittest.TestCase):
    def test_boundary_authority_cannot_enlarge_manifest_reserve(self):
        from audience_lab.dispatch import enrich_assignment_jobs

        authority = {
            "study_id": "boundary-study",
            "method": "partial_exposure_maxdiff",
            "boundary_plan": {
                "available_boundary_reserve": 9,
                "predeclared_pair_assignments": [
                    {
                        "pair_assignment_id": f"boundary-pair-{index}",
                        "wave": 1,
                        "variation_ids": [f"V{index}", f"V{index + 1}"],
                    }
                    for index in range(1, 10)
                ],
            },
        }
        manifest = {
            "study_id": authority["study_id"],
            "method": authority["method"],
            "synthetic_replicate_capacity": {"boundary_reserved": 8},
        }
        context = _dispatch_context(authority["study_id"], "boundary_response")
        with self.assertRaisesRegex(ValueError, "boundary.*manifest|boundary.*reserve"):
            enrich_assignment_jobs(authority, context, manifest=manifest)

    def test_finalist_dispatch_requires_audited_exact_manifest_approval(self):
        from audience_lab.dispatch import enrich_assignment_jobs

        manifest = calibrated_manifest()
        base = {
            "study_id": manifest["study_id"],
            "method": manifest["method"],
            "approved_finalist_ids": ["creative-a", "creative-b"],
            "roster_decision": {
                "status": "approved",
                "override": False,
                "approved_at": "2026-07-22T12:00:00Z",
                "approved_by": "study owner",
            },
        }
        context = _dispatch_context(manifest["study_id"], "finalist_response")
        cases = {
            "wrong_study": ({**base, "study_id": "forged-study"}, context, "study"),
            "pending": (
                {**base, "roster_decision": {**base["roster_decision"], "status": "pending"}},
                context,
                "status|approval",
            ),
            "missing_decision": (
                {key: value for key, value in base.items() if key != "roster_decision"},
                context,
                "roster_decision",
            ),
            "malformed_audit": (
                {**base, "roster_decision": {**base["roster_decision"], "approved_at": "today"}},
                context,
                "timestamp|ISO",
            ),
            "roster_mismatch": (
                {**base, "approved_finalist_ids": ["creative-a", "not-in-manifest"]},
                context,
                "roster|manifest",
            ),
            "reserve_overrun": (
                base,
                {**context, "requested_job_slots": 9},
                "finalist.*reserve",
            ),
        }
        for name, (approval, mutated_context, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                enrich_assignment_jobs(approval, mutated_context, manifest=manifest)


def _raw_for_response(response: dict) -> list[dict]:
    return [
        {
            "provider_return_id": attempt["provider_return_id"],
            "synthetic_replicate_id": response["synthetic_replicate_id"],
            "reviewer_dispatch_id": response["reviewer_dispatch_id"],
            "stage": attempt["stage"],
            **(
                {"position_seen": attempt["position_seen"]}
                if attempt["stage"] == "reaction" else {}
            ),
            "attempt_number": attempt["attempt_number"],
            "accepted": attempt["outcome"] == "accepted",
            "validation_errors": attempt["validation_errors"],
            "raw_return": {"fixture": True},
        }
        for attempt in response["runtime_attempts"]
    ]


def _exhausted_attempt(provider_id: str, attempt: int, *, position: int = 1) -> dict:
    return {
        "provider_return_id": provider_id,
        "synthetic_replicate_id": "replicate-exhausted-authorized",
        "reviewer_dispatch_id": "dispatch-exhausted-authorized",
        "stage": "reaction",
        "position_seen": position,
        "attempt_number": attempt,
        "accepted": False,
        "validation_errors": ["schema mismatch"],
        "raw_return": {"fixture": "rejected"},
    }


def _retained_component(position: int) -> dict:
    return {
        "provider_return_id": f"provider-retained-position-{position}-attempt-1",
        "synthetic_replicate_id": "replicate-exhausted-authorized",
        "reviewer_dispatch_id": "dispatch-exhausted-authorized",
        "stage": "reaction",
        "position_seen": position,
        "attempt_number": 1,
        "accepted": True,
        "validation_errors": [],
        "raw_return": {"fixture": "accepted concurrent component"},
    }


def _rejected_from_raw(raw: dict) -> dict:
    return {
        key: deepcopy(raw[key])
        for key in (
            "provider_return_id",
            "synthetic_replicate_id",
            "reviewer_dispatch_id",
            "stage",
            "position_seen",
            "attempt_number",
            "validation_errors",
        )
    } | {"disposition": "retry_exhausted"}


def _lineage_fixture() -> tuple[dict, dict]:
    manifest = calibrated_manifest()
    manifest["runtime"]["retry_limit_per_return"] = 1
    response = complete_response(1)
    exhausted = [
        _exhausted_attempt("provider-exhausted-attempt-1", 1),
        _exhausted_attempt("provider-exhausted-attempt-2", 2),
        *[_retained_component(position) for position in (2, 3, 4)],
    ]
    contract = {
        "retry_limit_per_return": 1,
        "reaction_positions": [1, 2, 3, 4],
        "comparison_required": True,
    }
    workflow = {
        "status": "incomplete",
        "responses": [response],
        "raw_provider_returns": _raw_for_response(response) + exhausted,
        "rejected_attempts": [
            _rejected_from_raw(item) for item in exhausted if item["accepted"] is False
        ],
        "dispatch_audit": [
            {
                "record_type": "screening_response",
                "synthetic_replicate_id": response["synthetic_replicate_id"],
                "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                "accepted": True,
                "attempt_contract": deepcopy(contract),
                "reaction_attempts": [1, 1, 1, 1],
                "comparison_attempts": 1,
            },
            {
                "record_type": "screening_response",
                "synthetic_replicate_id": "replicate-exhausted-authorized",
                "reviewer_dispatch_id": "dispatch-exhausted-authorized",
                "accepted": False,
                "attempt_contract": deepcopy(contract),
                "reaction_attempts": [2, 1, 1, 1],
                "comparison_attempts": 0,
            },
        ],
        "requested_replicates": 2,
        "completed_replicates": 1,
    }
    return manifest, workflow


def _mutate_lineage(workflow: dict, case: str) -> None:
    exhausted = [
        item for item in workflow["raw_provider_returns"]
        if item["reviewer_dispatch_id"] == "dispatch-exhausted-authorized"
    ]
    terminal = next(
        item
        for item in exhausted
        if item["stage"] == "reaction"
        and item["position_seen"] == 1
        and item["attempt_number"] == 2
    )
    if case == "attempt_99":
        terminal["attempt_number"] = 99
    elif case == "position_99":
        for item in exhausted:
            item["position_seen"] = 99
    elif case == "duplicate_attempt_1":
        terminal["attempt_number"] = 1
    elif case == "missing_terminal_attempt":
        workflow["raw_provider_returns"].remove(terminal)
    else:
        raise AssertionError(case)
    workflow["rejected_attempts"] = [
        _rejected_from_raw(item)
        for item in workflow["raw_provider_returns"]
        if item["accepted"] is False
    ]


def _jsonl_bytes(records: list[dict]) -> bytes:
    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode()


def _bind_without_semantic_validation(temp: Path, manifest: dict, workflow: dict):
    run_dir = temp / "bound-run"
    run_dir.mkdir()
    names = {
        "accepted_responses": ("panelist-responses.jsonl", workflow["responses"]),
        "raw_provider_returns": ("raw-provider-returns.jsonl", workflow["raw_provider_returns"]),
        "rejected_attempts": ("rejected-attempts.jsonl", workflow["rejected_attempts"]),
        "dispatch_audit": ("dispatch-audit.jsonl", workflow["dispatch_audit"]),
    }
    bound = deepcopy(manifest)
    outputs = bound.setdefault("outputs", {})
    exports = []
    sources = {}
    for logical, (filename, records) in names.items():
        content = _jsonl_bytes(records)
        (run_dir / filename).write_bytes(content)
        outputs[logical] = {
            "path": filename,
            "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
            "record_count": len(records),
        }
        exports.append({
            "filename": filename,
            "data_url": "data:application/x-ndjson;base64," + base64.b64encode(content).decode(),
        })
        sources[filename] = records
    response_replicates = {
        item["synthetic_replicate_id"] for item in workflow["responses"]
    }
    bound["usage"] = {
        "unique_job_slots_planned": bound["synthetic_replicate_capacity"]["screening_planned"],
        "unique_job_slots_dispatched": len(workflow["dispatch_audit"]),
        "accepted_response_records": len(workflow["responses"]),
        "accepted_unique_replicates": len(response_replicates),
        "total_model_calls": len(workflow["raw_provider_returns"]),
        "rejected_attempts": len(workflow["rejected_attempts"]),
    }
    write_json(run_dir / "study-manifest.json", bound)
    return run_dir, bound, {"exports": exports}, sources


def _standalone_module():
    spec = importlib.util.spec_from_file_location(
        "task9_wave2_validate_dashboard", ROOT / "skills/audience-ad-testing-lab/scripts/validate-dashboard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExhaustedLineageWave2Tests(unittest.TestCase):
    def _assert_all_surfaces_reject(self, case: str) -> None:
        from audience_lab.dashboard import DashboardInputError, _validate_lineage_integrity

        manifest, workflow = _lineage_fixture()
        _mutate_lineage(workflow, case)
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source_manifest = temp / "manifest.json"
            workflow_path = temp / "workflow.json"
            write_json(source_manifest, manifest)
            write_json(workflow_path, workflow)
            materialized = run_cli(
                "skills/audience-ad-testing-lab/scripts/materialize-run-lineage.py",
                str(workflow_path), str(source_manifest), str(temp / "materialized"),
            )
            self.assertNotEqual(0, materialized.returncode, case)

            run_dir, bound, payload, sources = _bind_without_semantic_validation(
                temp, manifest, workflow
            )
            with self.assertRaises(DashboardInputError, msg=case):
                _validate_lineage_integrity(run_dir, bound, workflow["responses"])
            errors = _standalone_module()._validate_lineage_sources(
                payload, bound, workflow["responses"], sources
            )
            self.assertTrue(errors, case)

    def test_attempt_99_is_rejected_everywhere(self):
        self._assert_all_surfaces_reject("attempt_99")

    def test_position_99_is_rejected_everywhere(self):
        self._assert_all_surfaces_reject("position_99")

    def test_duplicate_semantic_attempt_one_is_rejected_everywhere(self):
        self._assert_all_surfaces_reject("duplicate_attempt_1")

    def test_missing_terminal_retry_is_rejected_everywhere(self):
        self._assert_all_surfaces_reject("missing_terminal_attempt")

    def test_full_failed_retry_sequence_is_valid_everywhere(self):
        from audience_lab.dashboard import _validate_lineage_integrity

        manifest, workflow = _lineage_fixture()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source_manifest = temp / "manifest.json"
            workflow_path = temp / "workflow.json"
            write_json(source_manifest, manifest)
            write_json(workflow_path, workflow)
            materialized = run_cli(
                "skills/audience-ad-testing-lab/scripts/materialize-run-lineage.py",
                str(workflow_path), str(source_manifest), str(temp / "materialized"),
            )
            self.assertEqual(0, materialized.returncode, materialized.stderr)
            run_dir, bound, payload, sources = _bind_without_semantic_validation(
                temp, manifest, workflow
            )
            _validate_lineage_integrity(run_dir, bound, workflow["responses"])
            errors = _standalone_module()._validate_lineage_sources(
                payload, bound, workflow["responses"], sources
            )
            self.assertEqual([], errors)


class ArchetypeSensitivityWave2Tests(unittest.TestCase):
    def test_calibration_predeclares_archetype_evaluability_thresholds(self):
        policy = _policy()
        self.assertEqual(2, policy["minimum_archetype_diversity"])
        self.assertEqual(2, policy["minimum_evaluable_archetype_exclusions"])

    def test_single_archetype_run_cannot_be_valid(self):
        manifest, jobs, responses = complete_inputs()
        for job, response in zip(jobs["synthetic_replicate_jobs"], responses):
            job["persona_archetype_id"] = "only-archetype"
            response["persona_archetype_id"] = "only-archetype"
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), manifest, jobs, responses, policy=_policy()
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("exploratory", payload["validity_status"])
        self.assertEqual([], payload["proposed_finalist_ids"])
        self.assertIn("archetype_sensitivity_unevaluable", payload["validity_reasons"])
        self.assertFalse(payload["model_diagnostics"]["gates"]["archetype_sensitivity"])

    def test_predeclared_minimum_diversity_can_pass(self):
        manifest, jobs, responses = complete_inputs()
        for index, (job, response) in enumerate(
            zip(jobs["synthetic_replicate_jobs"], responses)
        ):
            archetype = f"archetype-{index % 2}"
            job["persona_archetype_id"] = archetype
            response["persona_archetype_id"] = archetype
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), manifest, jobs, responses, policy=_policy()
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("valid", payload["validity_status"])
        self.assertEqual(2, payload["archetype_sensitivity"]["unique_archetypes"])
        self.assertEqual(2, payload["archetype_sensitivity"]["evaluable_exclusions"])


if __name__ == "__main__":
    unittest.main()
