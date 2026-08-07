from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from conformance.test_task9_integration import (
    ROOT,
    complete_manifest,
    complete_response,
    finalist_response,
    load_json,
    run_cli,
    write_json,
    write_jsonl,
)


CALIBRATION_POLICY = {
    "version": "complete-exposure-calibration-v2",
    "scope": "conditional_synthetic_run_only",
    "planned_jobs_per_segment": 9,
    "minimum_usable_records_per_segment": 8,
    "bootstrap_resamples": 2000,
    "finalist_inclusion_threshold": 0.90,
    "nonfinalist_inclusion_threshold": 0.10,
    "cutoff_tie_policy": "no_point_estimate_only_decision",
    "archetype_sensitivity": "leave_one_persona_archetype_out_top_k_consistent",
    "minimum_archetype_diversity": 2,
    "minimum_evaluable_archetype_exclusions": 2,
    "calibration_basis": "deterministic_task9_adversarial_recovery_fixtures",
    "human_market_calibration": False,
}


def calibrated_manifest() -> dict:
    manifest = complete_manifest()
    manifest["assignment"]["planned_participations_per_creative"] = 9
    manifest["assignment"]["usable_participations_per_creative"] = {
        creative_id: 9
        for creative_id in ("creative-a", "creative-b", "creative-c", "creative-d")
    }
    manifest["synthetic_replicate_capacity"].update(
        {
            "screening_planned": 9,
            "required_total": 17,
            "ceiling_satisfied": True,
        }
    )
    manifest["model"]["complete_exposure_calibration_version"] = CALIBRATION_POLICY[
        "version"
    ]
    manifest["usage"] = {
        "unique_job_slots_planned": 17,
        "unique_job_slots_dispatched": 9,
        "accepted_response_records": 9,
        "accepted_unique_replicates": 9,
        "total_model_calls": 45,
    }
    return manifest


def complete_job(response: dict) -> dict:
    return {
        "study_id": response["study_id"],
        "response_id": response["response_id"],
        "record_type": "screening_response",
        "method": "complete_exposure",
        "synthetic_replicate_id": response["synthetic_replicate_id"],
        "dispatch_id": response["reviewer_dispatch_id"],
        "persona_archetype_id": response["persona_archetype_id"],
        "segment_id": response["segment_id"],
        "profile_snapshot": response["profile_snapshot"],
        "context_attribute_provenance": response[
            "context_attribute_provenance"
        ],
        "worker_context_isolation": response["worker_context_isolation"],
        "human_sample_independence": False,
        "variation_ids": response["assigned_variation_ids"],
        "blind_labels": response["blind_labels"],
        "shown_order": response["shown_order"],
        "reaction_protocol": response["reaction_protocol"],
        "reaction_prompts": ["Evaluate this blind creative."]
        * len(response["shown_order"]),
        "comparison_prompt": "Rank the complete blind creative set.",
    }


def complete_inputs(
    rankings: list[list[str]] | None = None,
) -> tuple[dict, dict, list[dict]]:
    rankings = rankings or [
        ["creative-a", "creative-b", "creative-c", "creative-d"]
        for _ in range(9)
    ]
    responses = [
        complete_response(index, ranking)
        for index, ranking in enumerate(rankings, 1)
    ]
    jobs = {
        "study_id": "complete-acme-001",
        "method": "complete_exposure",
        "record_type": "screening_response",
        "synthetic_replicate_jobs": [complete_job(item) for item in responses],
    }
    return calibrated_manifest(), jobs, responses


def run_complete(
    temp: Path,
    manifest: dict,
    jobs: dict,
    responses: list[dict],
    *,
    policy: dict | None = CALIBRATION_POLICY,
):
    manifest_path = temp / "manifest.json"
    jobs_path = temp / "jobs.json"
    responses_path = temp / "responses.jsonl"
    output_path = temp / "screening.json"
    write_json(manifest_path, manifest)
    write_json(jobs_path, jobs)
    write_jsonl(responses_path, responses)
    args = [
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "screening",
        "--manifest",
        str(manifest_path),
        "--jobs",
        str(jobs_path),
        "--responses",
        str(responses_path),
    ]
    if policy is not None:
        policy_path = temp / "calibration.json"
        write_json(policy_path, policy)
        args.extend(["--recovery-config", str(policy_path)])
    args.extend(["--output", str(output_path)])
    completed = run_cli(*args)
    payload = load_json(output_path) if output_path.exists() else {}
    return completed, payload


class CompleteExposureValidityRegressionTests(unittest.TestCase):
    def test_genuine_full_roster_calibrated_run_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(Path(directory), *complete_inputs())
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("valid", payload["validity_status"])
        self.assertEqual(["creative-a", "creative-b"], payload["proposed_finalist_ids"])
        self.assertTrue(all(payload["model_diagnostics"]["gates"].values()))

    def test_omitting_one_creative_from_every_record_is_invalid(self):
        manifest, jobs, responses = complete_inputs()
        for job, response in zip(jobs["synthetic_replicate_jobs"], responses):
            for key in ("variation_ids", "shown_order"):
                job[key] = [item for item in job[key] if item != "creative-d"]
            job["blind_labels"].pop("creative-d")
            job["reaction_prompts"] = job["reaction_prompts"][:3]
            for key in ("assigned_variation_ids", "shown_order"):
                response[key] = [item for item in response[key] if item != "creative-d"]
            response["blind_labels"].pop("creative-d", None)
            response["per_creative_reactions"] = [
                item
                for item in response["per_creative_reactions"]
                if item["variation_id"] != "creative-d"
            ]
            response["complete_set_evaluation"]["preference_ranking"] = [
                "creative-a",
                "creative-b",
                "creative-c",
            ]
            response["complete_set_evaluation"]["frozen_reaction_ids"] = [
                item["reaction_id"] for item in response["per_creative_reactions"]
            ]
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), manifest, jobs, responses
            )
        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertEqual([], payload.get("proposed_finalist_ids", []))

    def test_one_planned_response_is_invalid_even_when_point_ranking_is_clear(self):
        manifest, jobs, responses = complete_inputs()
        manifest["assignment"]["planned_participations_per_creative"] = 1
        manifest["assignment"]["usable_participations_per_creative"] = {
            creative_id: 1
            for creative_id in manifest["outputs"]["creative_asset_hashes"]
        }
        manifest["synthetic_replicate_capacity"]["screening_planned"] = 1
        manifest["synthetic_replicate_capacity"]["required_total"] = 9
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), manifest, {
                    **jobs,
                    "synthetic_replicate_jobs": jobs["synthetic_replicate_jobs"][:1],
                }, responses[:1]
            )
        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertEqual([], payload.get("proposed_finalist_ids", []))

    def test_unstable_cutoff_is_exploratory_with_no_roster(self):
        rankings = []
        for index in range(9):
            middle = ["creative-b", "creative-c"] if index < 5 else ["creative-c", "creative-b"]
            rankings.append(["creative-a", *middle, "creative-d"])
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), *complete_inputs(rankings)
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("exploratory", payload["validity_status"])
        self.assertEqual("unresolved", payload["selection_status"])
        self.assertEqual([], payload["proposed_finalist_ids"])
        frequencies = payload["top_k_inclusion_frequencies"]
        self.assertEqual(0.639, frequencies["creative-b"])
        self.assertEqual(0.361, frequencies["creative-c"])

    def test_response_must_exactly_match_its_planned_job(self):
        manifest, jobs, responses = complete_inputs()
        responses[0]["reviewer_dispatch_id"] = "dispatch-forged"
        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_complete(
                Path(directory), manifest, jobs, responses
            )
        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])

    def test_missing_or_mismatched_bound_calibration_policy_is_invalid(self):
        self.assertEqual(
            CALIBRATION_POLICY,
            load_json(
                ROOT / "skills/audience-ad-testing-lab/references/complete-exposure-calibration-config.json"
            ),
        )
        manifest, jobs, responses = complete_inputs()
        with tempfile.TemporaryDirectory() as directory:
            missing, missing_payload = run_complete(
                Path(directory), manifest, jobs, responses, policy=None
            )
        self.assertEqual(4, missing.returncode)
        self.assertEqual("invalid", missing_payload["validity_status"])
        mismatched = deepcopy(CALIBRATION_POLICY)
        mismatched["version"] = "complete-exposure-calibration-forged"
        with tempfile.TemporaryDirectory() as directory:
            mismatch, mismatch_payload = run_complete(
                Path(directory), manifest, jobs, responses, policy=mismatched
            )
        self.assertEqual(4, mismatch.returncode)
        self.assertEqual("invalid", mismatch_payload["validity_status"])


class DispatchAuthorityRegressionTests(unittest.TestCase):
    def test_screening_refuses_nine_jobs_when_eight_are_reserved(self):
        manifest, jobs, _ = complete_inputs()
        manifest["synthetic_replicate_capacity"]["screening_planned"] = 8
        context = load_json(
            ROOT / "conformance/fixtures/e2e-large/dispatch-context.json"
        )
        context["study_id"] = manifest["study_id"]
        with self.assertRaisesRegex(ValueError, "screening.*reserve"):
            from audience_lab.dispatch import enrich_assignment_jobs

            enrich_assignment_jobs({**manifest, "assignment": {**manifest["assignment"], **jobs}}, context)

    def test_boundary_refuses_forged_pair_and_wave_before_dispatch(self):
        authority = {
            "study_id": "partial-acme-001",
            "method": "partial_exposure_maxdiff",
            "boundary_plan": {
                "available_boundary_reserve": 1,
                "predeclared_pair_assignments": [
                    {
                        "pair_assignment_id": "pair-authorized",
                        "wave": 1,
                        "variation_ids": ["V3", "V4"],
                    }
                ]
            },
        }
        context = load_json(
            ROOT / "conformance/fixtures/e2e-large/dispatch-context.json"
        )
        context.update(
            {
                "study_id": authority["study_id"],
                "record_type": "boundary_response",
                "requested_boundary_assignments": [
                    {
                        "pair_assignment_id": "pair-forged",
                        "boundary_wave": 99,
                        "variation_ids": ["V1", "V7"],
                    }
                ],
            }
        )
        with self.assertRaisesRegex(ValueError, "unauthorized.*pair|unauthorized.*wave"):
            from audience_lab.dispatch import enrich_assignment_jobs

            enrich_assignment_jobs(authority, context)

    def test_finalist_refuses_nine_jobs_when_eight_are_reserved(self):
        manifest = calibrated_manifest()
        approval = {
            "study_id": manifest["study_id"],
            "method": manifest["method"],
            "approved_finalist_ids": ["creative-a", "creative-b"],
            "roster_decision": {
                "status": "approved",
                "override": False,
                "approved_at": "2026-07-22T10:00:00-04:00",
                "approved_by": "reviewer",
            },
        }
        context = load_json(
            ROOT / "conformance/fixtures/e2e-large/dispatch-context.json"
        )
        context.update(
            {
                "study_id": manifest["study_id"],
                "record_type": "finalist_response",
                "requested_job_slots": 9,
            }
        )
        with self.assertRaisesRegex(ValueError, "finalist.*reserve"):
            from audience_lab.dispatch import enrich_assignment_jobs

            enrich_assignment_jobs(approval, context, manifest=manifest)

    def test_finalist_aggregation_refuses_nine_bound_records_at_eight_reserve(self):
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
                "approved_at": "2026-07-22T10:00:00-04:00",
                "approved_by": "reviewer",
            },
        }
        responses = [
            finalist_response(index, ["creative-a", "creative-b"])
            for index in range(1, 10)
        ]
        jobs = {
            "study_id": manifest["study_id"],
            "method": manifest["method"],
            "record_type": "finalist_response",
            "synthetic_replicate_jobs": [complete_job(item) for item in responses],
        }
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            paths = {
                "manifest": temp / "manifest.json",
                "screening": temp / "screening.json",
                "approval": temp / "approval.json",
                "jobs": temp / "jobs.json",
                "responses": temp / "responses.jsonl",
                "output": temp / "output.json",
            }
            write_json(paths["manifest"], manifest)
            write_json(paths["screening"], screening)
            write_json(paths["approval"], approval)
            write_json(paths["jobs"], jobs)
            write_jsonl(paths["responses"], responses)
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
                "finalists",
                "--manifest",
                str(paths["manifest"]),
                "--screening-results",
                str(paths["screening"]),
                "--approval",
                str(paths["approval"]),
                "--jobs",
                str(paths["jobs"]),
                "--responses",
                str(paths["responses"]),
                "--output",
                str(paths["output"]),
            )
            payload = load_json(paths["output"])
        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["status"])
        self.assertIn("finalist reserve", payload["validation_errors"][0])


class LineageAndSaliencyRegressionTests(unittest.TestCase):
    @staticmethod
    def workflow(response: dict) -> dict:
        raw = []
        for attempt in response["runtime_attempts"]:
            raw.append(
                {
                    "provider_return_id": attempt["provider_return_id"],
                    "synthetic_replicate_id": response["synthetic_replicate_id"],
                    "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                    "stage": attempt["stage"],
                    **(
                        {"position_seen": attempt["position_seen"]}
                        if attempt["stage"] == "reaction"
                        else {}
                    ),
                    "attempt_number": attempt["attempt_number"],
                    "accepted": attempt["outcome"] == "accepted",
                    "validation_errors": attempt["validation_errors"],
                    "raw_return": {"fixture": True},
                }
            )
        return {
            "status": "complete",
            "responses": [response],
            "raw_provider_returns": raw,
            "rejected_attempts": [],
            "dispatch_audit": [
                {
                    "record_type": "screening_response",
                    "synthetic_replicate_id": response["synthetic_replicate_id"],
                    "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                    "accepted": True,
                    "attempt_contract": {
                        "retry_limit_per_return": 1,
                        "reaction_positions": [1, 2, 3, 4],
                        "comparison_required": True,
                    },
                    "reaction_attempts": [1, 1, 1, 1],
                    "comparison_attempts": 1,
                }
            ],
            "requested_replicates": 1,
            "completed_replicates": 1,
        }

    def _materialize(self, workflow: dict):
        directory = tempfile.TemporaryDirectory()
        temp = Path(directory.name)
        workflow_path = temp / "workflow.json"
        manifest_path = temp / "manifest.json"
        write_json(workflow_path, workflow)
        write_json(manifest_path, calibrated_manifest())
        completed = run_cli(
            "skills/audience-ad-testing-lab/scripts/materialize-run-lineage.py",
            str(workflow_path),
            str(manifest_path),
            str(temp / "run"),
        )
        return directory, completed

    def test_raw_replicate_and_position_are_bound_to_accepted_response(self):
        for field, forged in (
            ("synthetic_replicate_id", "replicate-forged"),
            ("position_seen", 99),
        ):
            workflow = self.workflow(complete_response(1))
            target = next(
                item
                for item in workflow["raw_provider_returns"]
                if field != "position_seen" or item["stage"] == "reaction"
            )
            target[field] = forged
            directory, completed = self._materialize(workflow)
            directory.cleanup()
            self.assertNotEqual(0, completed.returncode, field)

    def test_fake_sum_success_without_overlay_is_blocked(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            image_path = temp / "creative.png"
            image_path.write_bytes(png)
            repo = temp / "sum"
            repo.mkdir()
            inference = repo / "inference.py"
            inference.write_text(
                """from pathlib import Path\nimport argparse\np=argparse.ArgumentParser()\np.add_argument('--img_path')\np.add_argument('--condition')\np.add_argument('--output_path')\np.add_argument('--heat_map_type')\na=p.parse_args()\nout=Path(a.output_path)\nout.mkdir(parents=True, exist_ok=True)\n(out / (Path(a.img_path).stem + '_saliencymap.png')).write_bytes(Path(a.img_path).read_bytes())\n""",
                encoding="utf-8",
            )
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/run-sum-saliency.py",
                "--img-path",
                str(image_path),
                "--output-dir",
                str(temp / "outputs"),
                "--condition",
                "2",
                "--sum-repo",
                str(repo),
            )
            payload = json.loads(completed.stdout)
        self.assertEqual(4, completed.returncode)
        self.assertEqual("blocked", payload["status"])
        self.assertIn("overlay", payload["reason"])


class ApprovalOverrideRegressionTests(unittest.TestCase):
    @staticmethod
    def approval(manifest: dict, *, override: bool) -> dict:
        return {
            "study_id": manifest["study_id"],
            "approved_finalist_ids": ["creative-a", "creative-b"],
            "roster_decision": {
                "status": "approved_with_override" if override else "approved",
                "override": override,
                "override_reason": "Human reviewer accepts the unresolved synthetic signal."
                if override
                else "",
                "approved_at": "2026-07-22T10:00:00-04:00",
                "approved_by": "reviewer",
            },
        }

    def test_audited_override_can_proceed_from_complete_exploratory(self):
        from audience_lab.finalists import aggregate_finalists

        manifest = calibrated_manifest()
        screening = {
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "validity_status": "exploratory",
            "selection_status": "unresolved",
            "proposed_finalist_ids": [],
        }
        records = [
            finalist_response(index, ["creative-a", "creative-b"])
            for index in range(1, 9)
        ]
        result = aggregate_finalists(
            manifest, screening, self.approval(manifest, override=True), records
        )
        self.assertEqual("approved_with_override", result["roster_decision"]["status"])
        self.assertEqual([], result["deterministic_proposed_finalist_ids"])

    def test_override_never_bypasses_invalid_complete_or_unresolved_partial(self):
        from audience_lab.finalists import aggregate_finalists

        manifest = calibrated_manifest()
        records = [
            finalist_response(index, ["creative-a", "creative-b"])
            for index in range(1, 9)
        ]
        invalid = {
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "validity_status": "invalid",
            "selection_status": "invalid",
            "proposed_finalist_ids": [],
        }
        with self.assertRaisesRegex(ValueError, "invalid"):
            aggregate_finalists(
                manifest, invalid, self.approval(manifest, override=True), records
            )
        partial_manifest = deepcopy(manifest)
        partial_manifest["method"] = "partial_exposure_maxdiff"
        partial = {
            "study_id": manifest["study_id"],
            "method": "partial_exposure_maxdiff",
            "validity_status": "valid",
            "selection_status": "unresolved",
            "classifications": {"creative-a": "boundary_candidate"},
            "proposed_finalist_ids": [],
        }
        with self.assertRaisesRegex(ValueError, "boundary|unresolved"):
            aggregate_finalists(
                partial_manifest,
                partial,
                self.approval(partial_manifest, override=True),
                records,
            )


if __name__ == "__main__":
    unittest.main()
