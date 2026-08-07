from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
FIXTURES = ROOT / "conformance" / "fixtures"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def partial_response(index: int, block: list[str] | None = None) -> dict:
    source = json.loads(
        (FIXTURES / "screening-responses-valid.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    response = deepcopy(source)
    block = block or ["V1", "V2", "V3", "V4"]
    prior = list(response["assigned_variation_ids"])
    replacement = dict(zip(prior, block, strict=True))
    suffix = f"{index:04d}"
    response.update(
        {
            "study_id": "partial-binding-study",
            "response_id": f"response-{suffix}",
            "synthetic_replicate_id": f"replicate-{suffix}",
            "reviewer_dispatch_id": f"dispatch-{suffix}",
            "persona_archetype_id": f"archetype-{index % 3}",
            "segment_id": "S1",
            "assigned_variation_ids": block,
            "shown_order": [replacement[item] for item in response["shown_order"]],
            "blind_labels": {
                replacement[item]: label
                for item, label in response["blind_labels"].items()
            },
        }
    )
    provider_map: dict[str, str] = {}
    for attempt in response["runtime_attempts"]:
        old = attempt["provider_return_id"]
        new = f"{old}-{suffix}"
        provider_map[old] = new
        attempt["provider_return_id"] = new
        attempt["attempt_id"] = new
    for position, reaction in enumerate(response["per_creative_reactions"], 1):
        reaction["reaction_id"] = f"reaction-{suffix}-{position}"
        reaction["variation_id"] = response["shown_order"][position - 1]
        reaction["display_label_seen"] = response["blind_labels"][reaction["variation_id"]]
        reaction["source_provenance"]["provider_return_id"] = provider_map[
            reaction["source_provenance"]["provider_return_id"]
        ]
    choice = response["comparative_choice"]
    choice["best_variation_id"] = replacement[choice["best_variation_id"]]
    choice["weakest_variation_id"] = replacement[choice["weakest_variation_id"]]
    choice["frozen_reaction_ids"] = [
        item["reaction_id"] for item in response["per_creative_reactions"]
    ]
    choice["source_provenance"]["provider_return_id"] = provider_map[
        choice["source_provenance"]["provider_return_id"]
    ]
    return response


def job_for(response: dict) -> dict:
    return {
        "study_id": response["study_id"],
        "response_id": response["response_id"],
        "record_type": "screening_response",
        "method": "partial_exposure_maxdiff",
        "synthetic_replicate_id": response["synthetic_replicate_id"],
        "dispatch_id": response["reviewer_dispatch_id"],
        "persona_archetype_id": response["persona_archetype_id"],
        "segment_id": response["segment_id"],
        "profile_snapshot": deepcopy(response["profile_snapshot"]),
        "context_attribute_provenance": deepcopy(
            response["context_attribute_provenance"]
        ),
        "worker_context_isolation": response["worker_context_isolation"],
        "human_sample_independence": False,
        "variation_ids": list(response["assigned_variation_ids"]),
        "blind_labels": deepcopy(response["blind_labels"]),
        "shown_order": list(response["shown_order"]),
        "reaction_protocol": response["reaction_protocol"],
        "reaction_prompts": ["Review this blind creative."] * 4,
        "comparison_prompt": "Choose the strongest and weakest creative.",
    }


def partial_manifest(creative_ids: list[str], screening_planned: int) -> dict:
    manifest = read_json(FIXTURES / "manifest-valid.json")
    manifest["study_id"] = "partial-binding-study"
    manifest["requested_shortlist_size"] = 2
    manifest["maximum_synthetic_panelists"] = screening_planned + 8
    manifest["synthetic_replicate_capacity"] = {
        "screening_planned": screening_planned,
        "boundary_reserved": 4,
        "boundary_jobs_per_wave": 2,
        "boundary_waves_max": 2,
        "finalist_reserved": 4,
        "ceiling_satisfied": True,
    }
    manifest["audience_lock"]["segment_weights"] = {"S1": 1.0}
    manifest["outputs"]["creative_asset_hashes"] = {
        creative_id: f"sha256:{index:064x}"
        for index, creative_id in enumerate(creative_ids, 1)
    }
    manifest["assignment"]["block_size"] = 4
    manifest["assignment"]["planned_participations_per_creative"] = 9
    manifest["assignment"]["usable_participations_per_creative"] = {
        creative_id: 9 for creative_id in creative_ids
    }
    return manifest


def audit_for(job: dict, accepted: bool) -> dict:
    return {
        "record_type": "screening_response",
        "synthetic_replicate_id": job["synthetic_replicate_id"],
        "reviewer_dispatch_id": job["dispatch_id"],
        "accepted": accepted,
        "attempt_contract": {
            "retry_limit_per_return": 1,
            "reaction_positions": [1, 2, 3, 4],
            "comparison_required": True,
        },
        "reaction_attempts": [1, 1, 1, 1] if accepted else [2, 1, 1, 1],
        "comparison_attempts": 1 if accepted else 0,
    }


def complete_response_for_job(job: dict, index: int) -> dict:
    response = deepcopy(partial_response(index))
    response.update(
        {
            "study_id": job["study_id"],
            "response_id": job["response_id"],
            "record_type": "screening_response",
            "method": "complete_exposure",
            "synthetic_replicate_id": job["synthetic_replicate_id"],
            "reviewer_dispatch_id": job["dispatch_id"],
            "persona_archetype_id": job["persona_archetype_id"],
            "segment_id": job["segment_id"],
            "profile_snapshot": deepcopy(job["profile_snapshot"]),
            "context_attribute_provenance": deepcopy(
                job["context_attribute_provenance"]
            ),
            "worker_context_isolation": job["worker_context_isolation"],
            "human_sample_independence": False,
            "assigned_variation_ids": list(job["variation_ids"]),
            "blind_labels": deepcopy(job["blind_labels"]),
            "shown_order": list(job["shown_order"]),
            "reaction_protocol": job["reaction_protocol"],
        }
    )
    if "context_stratum_id" in job:
        response["context_stratum_id"] = job["context_stratum_id"]
    for position, reaction in enumerate(response["per_creative_reactions"], 1):
        reaction["reaction_id"] = f"complete-reaction-{index:04d}-{position}"
        reaction["variation_id"] = job["shown_order"][position - 1]
        reaction["display_label_seen"] = job["blind_labels"][reaction["variation_id"]]
    comparison = response.pop("comparative_choice")
    response.pop("usable_maxdiff_block")
    response["complete_set_evaluation"] = {
        "status": "ranked",
        "preference_ranking": list(job["variation_ids"]),
        "frozen_reaction_ids": [
            reaction["reaction_id"] for reaction in response["per_creative_reactions"]
        ],
        "source_provenance": comparison["source_provenance"],
    }
    response["usable_complete_exposure_observation"] = True
    return response


def run_partial(
    temp: Path,
    manifest: dict,
    jobs: list[dict],
    responses: list[dict],
    *,
    include_jobs: bool = True,
    audit: list[dict] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    manifest_path = temp / "manifest.json"
    jobs_path = temp / "jobs.json"
    responses_path = temp / "responses.jsonl"
    audit_path = temp / "dispatch-audit.jsonl"
    output_path = temp / "screening.json"
    write_json(manifest_path, manifest)
    write_json(
        jobs_path,
        {
            "study_id": manifest["study_id"],
            "method": "partial_exposure_maxdiff",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": jobs,
        },
    )
    write_jsonl(responses_path, responses)
    command = [
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "screening",
        "--manifest",
        str(manifest_path),
    ]
    if include_jobs:
        command.extend(["--jobs", str(jobs_path)])
    if audit is not None:
        write_jsonl(audit_path, audit)
        command.extend(["--dispatch-audit", str(audit_path)])
    command.extend(
        [
            "--responses",
            str(responses_path),
            "--recovery-config",
            "skills/audience-ad-testing-lab/references/screening-recovery-config.json",
            "--output",
            str(output_path),
        ]
    )
    completed = run_cli(*command)
    return completed, read_json(output_path) if output_path.exists() else None


class PartialExposureFrozenJobCliTests(unittest.TestCase):
    def test_partial_cli_requires_jobs_and_rejects_forged_or_mismatched_binding(self):
        cases = {}
        response = partial_response(1)
        job = job_for(response)
        cases["jobs_omitted"] = (False, [job], [response])

        forged = deepcopy(response)
        forged["synthetic_replicate_id"] = "forged-replicate"
        forged["response_id"] = "forged-response"
        forged["reviewer_dispatch_id"] = "forged-dispatch"
        cases["unplanned_internally_consistent_replicate"] = (True, [job], [forged])

        altered = deepcopy(response)
        altered["profile_snapshot"] = {"profile_snapshot_id": "forged-profile"}
        cases["altered_exact_job_field"] = (True, [job], [altered])

        wrong_envelope_job = deepcopy(job)
        wrong_envelope_job["study_id"] = "wrong-study"
        wrong_envelope_job["method"] = "complete_exposure"
        wrong_envelope_job["record_type"] = "finalist_response"
        cases["wrong_job_study_method_stage"] = (
            True,
            [wrong_envelope_job],
            [response],
        )

        wrong_roster = deepcopy(job)
        wrong_roster["variation_ids"] = ["V1", "V2", "V3", "V9"]
        wrong_roster["shown_order"] = ["V1", "V2", "V3", "V9"]
        wrong_roster["blind_labels"] = dict(zip(wrong_roster["shown_order"], "ABCD"))
        cases["wrong_job_roster"] = (True, [wrong_roster], [response])

        cases["duplicate_accepted_job"] = (True, [job, deepcopy(job)], [response])

        for name, (include_jobs, jobs, responses) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                manifest = partial_manifest(["V1", "V2", "V3", "V4"], len(jobs))
                completed, payload = run_partial(
                    Path(directory),
                    manifest,
                    jobs,
                    responses,
                    include_jobs=include_jobs,
                )
                self.assertEqual(4, completed.returncode, completed.stderr)
                self.assertIsNotNone(payload)
                self.assertEqual("invalid", payload["validity_status"])

    def test_authorized_exhausted_slot_models_only_bound_accepted_records(self):
        from audience_lab.assignments import build_assignments

        cores = build_assignments(
            [f"V{index}" for index in range(1, 8)], {"S1": 16}, seed=17
        ).jobs_as_dicts()
        responses = [
            partial_response(index, list(core["variation_ids"]))
            for index, core in enumerate(cores, 1)
        ]
        jobs = [job_for(response) for response in responses]
        accepted = responses[:-1]
        audit = [audit_for(job, index < len(accepted)) for index, job in enumerate(jobs)]
        manifest = partial_manifest([f"V{index}" for index in range(1, 8)], len(jobs))
        manifest["collection_open"] = True

        with tempfile.TemporaryDirectory() as directory:
            completed, payload = run_partial(
                Path(directory), manifest, jobs, accepted, audit=audit
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("incomplete", payload["validity_status"])
        self.assertTrue(payload["utilities"])
        self.assertEqual(len(accepted), payload["model_diagnostics"]["accepted_response_records"])


class NamedContextPlannerPipelineTests(unittest.TestCase):
    def _request(self, segments: list[str]) -> dict:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return {
            "study_id": "named-complete-study",
            "creative_ids": ["creative-a", "creative-b", "creative-c", "creative-d"],
            "creative_format": "copy_only",
            "requested_shortlist_size": 3,
            "maximum_synthetic_panelists": 30,
            "provisional_audience": {
                "scope": {
                    "audience": "Named planning test audience", "market": "B2B",
                    "geography": "United States", "category": "Software",
                    "buying_context": "Evaluation", "exclusions": [],
                },
                "user_defined_segments": [{
                    "segment_id": segment, "name": segment.replace("-", " ").title(),
                    "description": f"Provisional planning segment for {segment}.",
                } for segment in segments],
                "accepted_by": "test-owner",
                "accepted_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
            },
        }

    def test_named_complete_plan_never_synthesizes_segment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            request_path = temp / "request.json"
            plan_path = temp / "plan.json"
            write_json(request_path, self._request(["marketing-leader"]))
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
                str(request_path),
                str(plan_path),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                "1",
                "--boundary-jobs-per-wave",
                "0",
                "--boundary-waves-max",
                "0",
                "--finalist-reserved",
                "8",
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            plan = read_json(plan_path)

        jobs = plan["assignment"]["synthetic_replicate_jobs"]
        self.assertEqual({"marketing-leader"}, {item["segment_id"] for item in jobs})
        self.assertEqual(
            {"marketing-leader-provisional-context"},
            {item["context_stratum_id"] for item in jobs},
        )
        self.assertNotIn("segment-1", json.dumps(plan))

    def test_context_coverage_count_mismatch_is_rejected(self):
        for segments, reported in ((["marketing-leader"], 2), (["marketing-leader", "finance-leader"], 1)):
            with self.subTest(segments=segments, reported=reported), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                request_path = temp / "request.json"
                plan_path = temp / "plan.json"
                write_json(request_path, self._request(segments))
                completed = run_cli(
                    "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
                    str(request_path),
                    str(plan_path),
                    "--burden-pilot",
                    "passed",
                    "--reported-segments",
                    str(reported),
                    "--boundary-jobs-per-wave",
                    "0",
                    "--boundary-waves-max",
                    "0",
                    "--finalist-reserved",
                    "8",
                )
                self.assertEqual(2, completed.returncode)
                self.assertIn("reported", completed.stderr.lower())

    def test_two_named_segments_have_balanced_complete_allocations(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            request_path = temp / "request.json"
            plan_path = temp / "plan.json"
            write_json(
                request_path,
                self._request(["marketing-leader", "finance-leader"]),
            )
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
                str(request_path),
                str(plan_path),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                "2",
                "--boundary-jobs-per-wave",
                "0",
                "--boundary-waves-max",
                "0",
                "--finalist-reserved",
                "8",
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            assignment = read_json(plan_path)["assignment"]

        self.assertEqual(
            {"marketing-leader": 9, "finance-leader": 9},
            assignment["segment_allocations"],
        )
        self.assertEqual(
            {"marketing-leader", "finance-leader"},
            {item["segment_id"] for item in assignment["synthetic_replicate_jobs"]},
        )

    def test_named_context_survives_production_planner_adapter_and_aggregation(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            request_path = temp / "request.json"
            plan_path = temp / "plan.json"
            context_path = temp / "dispatch-context.json"
            mismatched_context_path = temp / "dispatch-context-mismatched.json"
            mismatched_jobs_path = temp / "jobs-mismatched.json"
            jobs_path = temp / "jobs.json"
            manifest_path = temp / "manifest.json"
            responses_path = temp / "responses.jsonl"
            output_path = temp / "screening.json"
            write_json(request_path, self._request(["marketing-leader"]))
            planned = run_cli(
                "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
                str(request_path),
                str(plan_path),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                "1",
                "--boundary-jobs-per-wave",
                "0",
                "--boundary-waves-max",
                "0",
                "--finalist-reserved",
                "8",
            )
            self.assertEqual(0, planned.returncode, planned.stderr)
            plan = read_json(plan_path)

            write_json(
                context_path,
                {
                    "study_id": plan["study_id"],
                    "record_type": "screening_response",
                    "reaction_protocol": "progressive_reveal",
                    "worker_context_isolation": "isolated",
                    "creative_prompts": {
                        creative_id: f"Review {creative_id}."
                        for creative_id in self._request([])["creative_ids"]
                    },
                    "comparison_prompts": {
                        "complete_exposure": "Rank the complete frozen set.",
                        "partial_exposure_maxdiff": "Choose strongest and weakest.",
                    },
                },
            )
            mismatched_context = read_json(context_path)
            injected_profile = deepcopy(plan["grounded_context_profiles"][0])
            injected_profile["context_stratum_id"] = "marketing-leader-awareness"
            mismatched_context["profiles"] = [injected_profile]
            write_json(mismatched_context_path, mismatched_context)

            manifest = read_json(FIXTURES / "manifest-valid.json")
            manifest.update(
                {
                    "study_id": plan["study_id"],
                    "creative_format": "copy_only",
                    "method": "complete_exposure",
                    "requested_shortlist_size": 3,
                    "maximum_synthetic_panelists": 30,
                    "synthetic_replicate_capacity": {
                        **plan["synthetic_replicate_capacity"],
                        "boundary_reserved": 0,
                        "boundary_jobs_per_wave": 0,
                        "boundary_waves_max": 0,
                    },
                }
            )
            manifest["audience_lock"] = deepcopy(plan["audience_lock"])
            manifest["audience_package"] = deepcopy(plan["audience_package"])
            manifest["assignment"] = {
                **plan["assignment"],
                "randomization_seed": "named-complete-seed",
                "planned_participations_per_creative": 9,
                "usable_participations_per_creative": {
                    creative_id: 9 for creative_id in self._request([])["creative_ids"]
                },
            }
            manifest["model"]["complete_exposure_calibration_version"] = (
                "complete-exposure-calibration-v2"
            )
            manifest["outputs"]["creative_asset_hashes"] = {
                creative_id: f"sha256:{index:064x}"
                for index, creative_id in enumerate(self._request([])["creative_ids"], 1)
            }
            write_json(manifest_path, manifest)

            rejected = run_cli(
                "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
                str(plan_path),
                str(mismatched_context_path),
                str(mismatched_jobs_path),
                "--manifest",
                str(manifest_path),
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("absent from the resolved", rejected.stderr)

            prepared = run_cli(
                "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
                str(plan_path),
                str(context_path),
                str(jobs_path),
                "--manifest",
                str(manifest_path),
            )
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            jobs = read_json(jobs_path)["synthetic_replicate_jobs"]
            responses = [
                complete_response_for_job(job, index)
                for index, job in enumerate(jobs, 1)
            ]
            write_jsonl(responses_path, responses)
            aggregated = run_cli(
                "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
                "screening",
                "--manifest",
                str(manifest_path),
                "--jobs",
                str(jobs_path),
                "--responses",
                str(responses_path),
                "--recovery-config",
                "skills/audience-ad-testing-lab/references/complete-exposure-calibration-config.json",
                "--output",
                str(output_path),
            )
            self.assertEqual(0, aggregated.returncode, aggregated.stderr)
            result = read_json(output_path)

        self.assertEqual(
            {"marketing-leader"}, {job["segment_id"] for job in jobs}
        )
        self.assertEqual(
            {"marketing-leader-provisional-context"},
            {job["context_stratum_id"] for job in jobs},
        )
        self.assertEqual(
            {"marketing-leader": 9},
            result["model_diagnostics"]["accepted_response_records_by_segment"],
        )
        self.assertEqual(
            {"marketing-leader-provisional-context": 9},
            result["model_diagnostics"][
                "accepted_response_records_by_context_stratum"
            ],
        )


if __name__ == "__main__":
    unittest.main()
