from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
SCRIPTS = SKILL_ROOT / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FAST_CI_MARKERS = (
    "actions/setup-python@v6",
    "actions/setup-node@v6",
    "@anthropic-ai/claude-code@2.1.220",
    "@openai/codex@0.146.0",
    "requirements-screening.txt",
    "requirements-private-data.txt",
    "requirements-outcome-data-prep.txt",
    "numpy==2.4.2 scipy==1.17.0 openpyxl==3.1.5",
    "conformance.test_package",
    "conformance.test_audience_data_lab",
    "conformance.test_audience_panel_builder",
    "conformance.test_audience_prompt_contracts",
    "validate-dashboard.py",
    "Check canonical skill layout",
    "py_compile",
    "json.tool",
    "run_plugin_install_smoke.py",
    "Audit public package boundary and reserved terminology",
    "Reject maintainer-specific absolute paths",
    "Check skill frontmatter",
    "ci_fast_validation.py verify-inventory",
    "ci_fast_validation.py verify-release-identity",
    "ci_fast_validation.py run workflow-contracts",
    "ci_fast_validation.py run smoke",
)
RELEASE_GATE_MARKERS = (
    "uses: ./.github/actions/setup-private-stage",
    "generate-calibration-manifests.py",
    "generate-runtime-release-manifest.py",
    "ci_fast_validation.py verify-release-identity",
    "ci_fast_validation.py run outcome-release",
    "calibration-engine-and-evaluation",
    "calibration-contracts-and-lifecycle",
)
PRIVATE_STAGE_SETUP_MARKERS = (
    "sudo apt-get install --yes bubblewrap",
    "sudo sysctl --write kernel.apparmor_restrict_unprivileged_userns=0",
    'test -x /usr/bin/bwrap',
    'stat -c \'%u\' /usr/bin/bwrap',
    "Seal the private-stage Python runtime",
    "stat.S_IMODE(path.stat().st_mode) & ~0o022",
)
FULL_CONFORMANCE_MARKERS = (
    "workflow_dispatch",
    "sudo apt-get install --yes bubblewrap",
    "sudo sysctl --write kernel.apparmor_restrict_unprivileged_userns=0",
    'test -x /usr/bin/bwrap',
    'stat -c \'%u\' /usr/bin/bwrap',
    "Seal the private-stage Python runtime",
    "stat.S_IMODE(path.stat().st_mode) & ~0o022",
    "python3 -m unittest discover -s conformance -p 'test_*.py' -v",
    "env -u PYTHONPATH python3 conformance/run_large_library_e2e.py",
    "Verify two-study reuse proof",
    "AUDIENCE_LAB_RUN_MAX_DESIGN_BENCHMARK=1",
    "conformance.test_maxdiff.MaxDiffMaximumDesignBenchmark",
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def run_cli(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        env=environment,
    )


def screening_source() -> dict:
    return load_jsonl(FIXTURES / "screening-responses-valid.jsonl")[0]


def complete_response(index: int, ranking: list[str] | None = None) -> dict:
    response = deepcopy(screening_source())
    suffix = f"ce-{index:02d}"
    response["study_id"] = "complete-acme-001"
    response["method"] = "complete_exposure"
    response["response_id"] = f"response-{suffix}"
    response["synthetic_replicate_id"] = f"replicate-{suffix}"
    response["reviewer_dispatch_id"] = f"dispatch-{suffix}"
    response["persona_archetype_id"] = f"archetype-{index % 3}"
    response["segment_id"] = "segment-1"
    response["assigned_variation_ids"] = ["creative-a", "creative-b", "creative-c", "creative-d"]
    response["shown_order"] = ["creative-d", "creative-b", "creative-c", "creative-a"]
    response["blind_labels"] = {
        "creative-a": "D",
        "creative-b": "B",
        "creative-c": "C",
        "creative-d": "A",
    }
    provider_map: dict[str, str] = {}
    for attempt in response["runtime_attempts"]:
        prior = attempt["provider_return_id"]
        current = f"{prior}-{suffix}"
        provider_map[prior] = current
        attempt["provider_return_id"] = current
        attempt["attempt_id"] = current
    for position, reaction in enumerate(response["per_creative_reactions"], 1):
        reaction["reaction_id"] = f"reaction-{suffix}-{position}"
        reaction["variation_id"] = response["shown_order"][position - 1]
        reaction["display_label_seen"] = response["blind_labels"][reaction["variation_id"]]
        provenance = reaction["source_provenance"]
        provenance["provider_return_id"] = provider_map[provenance["provider_return_id"]]
    comparison_source = response.pop("comparative_choice")["source_provenance"]
    comparison_source["provider_return_id"] = provider_map[comparison_source["provider_return_id"]]
    response.pop("usable_maxdiff_block")
    response["complete_set_evaluation"] = {
        "status": "ranked",
        "preference_ranking": ranking
        or ["creative-a", "creative-b", "creative-c", "creative-d"],
        "frozen_reaction_ids": [
            item["reaction_id"] for item in response["per_creative_reactions"]
        ],
        "source_provenance": comparison_source,
    }
    response["usable_complete_exposure_observation"] = True
    return response


def complete_manifest() -> dict:
    manifest = load_json(FIXTURES / "manifest-valid.json")
    manifest["study_id"] = "complete-acme-001"
    manifest["method"] = "complete_exposure"
    manifest["requested_shortlist_size"] = 2
    manifest["creative_format"] = "copy_only"
    manifest["audience_lock"]["segment_weights"] = {"segment-1": 1.0}
    manifest["assignment"]["block_size"] = 4
    manifest["assignment"]["planned_participations_per_creative"] = 9
    manifest["assignment"]["usable_participations_per_creative"] = {
        creative_id: 9
        for creative_id in ("creative-a", "creative-b", "creative-c", "creative-d")
    }
    manifest["synthetic_replicate_capacity"] = {
        "screening_planned": 9,
        "boundary_reserved": 0,
        "boundary_jobs_per_wave": 0,
        "boundary_waves_max": 0,
        "finalist_reserved": 8,
        "ceiling_satisfied": True,
    }
    old_key = "arti" + "facts"
    old_outputs = manifest.pop(old_key, {})
    manifest["outputs"] = {
        "creative_asset_hashes": {
            creative_id: f"sha256:{index:064x}"
            for index, creative_id in enumerate(
                ("creative-a", "creative-b", "creative-c", "creative-d"), 1
            )
        },
        **{
            key: value
            for key, value in old_outputs.items()
            if key != "creative_asset_hashes"
        },
    }
    manifest["model"]["complete_exposure_calibration_version"] = (
        "complete-exposure-calibration-v2"
    )
    manifest["runtime"]["retry_limit_per_return"] = 1
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
        "record_type": response["record_type"],
        "method": response["method"],
        "synthetic_replicate_id": response["synthetic_replicate_id"],
        "dispatch_id": response["reviewer_dispatch_id"],
        "persona_archetype_id": response["persona_archetype_id"],
        "segment_id": response["segment_id"],
        "profile_snapshot": response["profile_snapshot"],
        "context_attribute_provenance": response["context_attribute_provenance"],
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


def complete_calibration_policy() -> dict:
    return {
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


def finalist_response(index: int, ranking: list[str]) -> dict:
    source = complete_response(index, ranking=["creative-a", "creative-b", "creative-c", "creative-d"])
    keep = ["creative-a", "creative-b"]
    source["record_type"] = "finalist_response"
    source["response_id"] = f"finalist-response-{index:02d}"
    source["synthetic_replicate_id"] = f"finalist-replicate-{index:02d}"
    source["reviewer_dispatch_id"] = f"finalist-dispatch-{index:02d}"
    source["assigned_variation_ids"] = keep
    source["shown_order"] = keep if index % 2 else list(reversed(keep))
    source["blind_labels"] = {
        creative_id: chr(ord("A") + position)
        for position, creative_id in enumerate(source["shown_order"])
    }
    source["runtime_attempts"] = []
    reviews: list[dict] = []
    for position, creative_id in enumerate(source["shown_order"], 1):
        provider = f"finalist-{index:02d}-reaction-{position}-attempt-1"
        source["runtime_attempts"].append(
            {
                "attempt_id": provider,
                "stage": "reaction",
                "position_seen": position,
                "attempt_number": 1,
                "provider_return_id": provider,
                "outcome": "accepted",
                "validation_errors": [],
            }
        )
        score = 5 if creative_id == ranking[0] else 3
        reviews.append(
            {
                "reaction_id": f"finalist-reaction-{index:02d}-{position}",
                "variation_id": creative_id,
                "display_label_seen": source["blind_labels"][creative_id],
                "position_seen": position,
                "reaction_label": "immediate",
                "immediate_reaction": "A concrete finalist reaction.",
                "judgment_status": "judged",
                "source_provenance": {
                    "provider_return_id": provider,
                    "capture": "verbatim_provider_return",
                },
                "rubric_scores": {
                    key: score
                    for key in (
                        "comprehension",
                        "relevance",
                        "credibility",
                        "offer_appeal",
                        "motivation",
                        "friction",
                        "attention_potential",
                        "overall",
                    )
                },
                "feedback": ["Preserve the concrete proof."],
                "rubric_source_provenance": {},
            }
        )
    comparison = f"finalist-{index:02d}-comparison-attempt-1"
    source["runtime_attempts"].append(
        {
            "attempt_id": comparison,
            "stage": "comparison",
            "attempt_number": 1,
            "provider_return_id": comparison,
            "outcome": "accepted",
            "validation_errors": [],
        }
    )
    for review in reviews:
        review["rubric_source_provenance"] = {
            "provider_return_id": comparison,
            "capture": "verbatim_provider_return",
        }
    source.pop("per_creative_reactions")
    source.pop("complete_set_evaluation")
    source.pop("usable_complete_exposure_observation")
    source["finalist_reviews"] = reviews
    source["final_preference_ranking"] = ranking
    return source


class MethodAwareContractTests(unittest.TestCase):
    def test_manifest_uses_outputs_only_and_rejects_the_retired_key(self):
        from audience_lab.contracts import validate_manifest

        manifest = complete_manifest()
        self.assertEqual([], validate_manifest(manifest))

        retired = deepcopy(manifest)
        retired["arti" + "facts"] = retired.pop("outputs")
        errors = validate_manifest(retired)
        self.assertTrue(any("outputs" in error for error in errors))
        self.assertTrue(any("retired" in error for error in errors))

    def test_boundary_creative_attachment_cannot_reassign_frozen_v3_profiles(self):
        from audience_lab.contracts import validate_boundary_profile_attachments

        roster = {
            "schema_version": "audience-profile-allocation-plan-v1",
            "stage": "boundary",
            "stage_roster_id": "study-1:boundary-reserve",
            "stable_seed": "study-1:29",
            "assignments": [
                {
                    "slot_id": "boundary-wave-01-job-0001",
                    "grounded_profile_id": "profile-a",
                    "reported_segment_id": "segment-a",
                    "structural_group_id": "group-a",
                    "profile_snapshot_sha256": "sha256:" + "a" * 64,
                },
                {
                    "slot_id": "boundary-wave-01-job-0002",
                    "grounded_profile_id": "profile-b",
                    "reported_segment_id": "segment-a",
                    "structural_group_id": "group-b",
                    "profile_snapshot_sha256": "sha256:" + "b" * 64,
                },
            ],
        }
        attached = {
            "predeclared_pair_assignments": [
                {
                    "pair_assignment_id": assignment["slot_id"],
                    "wave": 1,
                    "variation_ids": ["creative-a", "creative-b"],
                    "audience_slot_id": assignment["slot_id"],
                    "grounded_profile_id": assignment["grounded_profile_id"],
                    "reported_segment_id": assignment["reported_segment_id"],
                    "structural_group_id": assignment["structural_group_id"],
                    "profile_snapshot_sha256": assignment[
                        "profile_snapshot_sha256"
                    ],
                }
                for assignment in roster["assignments"]
            ]
        }
        self.assertEqual(
            attached,
            validate_boundary_profile_attachments(attached, roster),
        )

        mutations = []
        removed = deepcopy(attached)
        removed["predeclared_pair_assignments"].pop()
        mutations.append(removed)
        reordered = deepcopy(attached)
        reordered["predeclared_pair_assignments"].reverse()
        mutations.append(reordered)
        reassigned = deepcopy(attached)
        reassigned["predeclared_pair_assignments"][0][
            "grounded_profile_id"
        ] = "profile-b"
        mutations.append(reassigned)
        wave_tampered = deepcopy(attached)
        wave_tampered["predeclared_pair_assignments"][0]["wave"] = 2
        mutations.append(wave_tampered)
        added = deepcopy(attached)
        added["predeclared_pair_assignments"].append(
            deepcopy(added["predeclared_pair_assignments"][0])
        )
        mutations.append(added)

        for tampered in mutations:
            with self.subTest(tampered=tampered), self.assertRaises(ValueError):
                validate_boundary_profile_attachments(tampered, roster)

    def test_screening_freeze_attaches_pairs_to_existing_v3_profile_slots(self):
        from audience_lab.contracts import validate_boundary_profile_attachments

        module_spec = importlib.util.spec_from_file_location(
            "task4_aggregate_screening",
            SCRIPTS / "aggregate-screening.py",
        )
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        aggregate_screening = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(aggregate_screening)
        roster = {
            "schema_version": "audience-profile-allocation-plan-v1",
            "stage": "boundary",
            "stage_roster_id": "study-1:boundary-reserve",
            "stable_seed": "study-1:29",
            "assignments": [
                {
                    "slot_id": f"boundary-wave-01-job-{index:04d}",
                    "grounded_profile_id": f"profile-{index}",
                    "reported_segment_id": "segment-a",
                    "structural_group_id": f"group-{index}",
                    "profile_snapshot_sha256": (
                        "sha256:" + str(index) * 64
                    ),
                }
                for index in (1, 2)
            ],
        }
        manifest = {
            "requested_shortlist_size": 2,
            "synthetic_replicate_capacity": {
                "boundary_jobs_per_wave": 2,
                "boundary_waves_max": 1,
                "boundary_reserved": 2,
            },
            "audience_profile_rosters": {
                "boundary_reserve": roster,
            },
        }
        screening = {
            "validity_status": "valid",
            "classifications": {
                "creative-a": "clear_finalist",
                "creative-b": "boundary_candidate",
                "creative-c": "boundary_candidate",
                "creative-d": "clear_non_finalist",
            },
        }

        aggregate_screening._freeze_boundary_plan(screening, manifest)

        attached = screening["boundary_plan"]
        self.assertEqual(
            attached,
            validate_boundary_profile_attachments(attached, roster),
        )
        self.assertEqual(
            [
                {
                    "audience_slot_id": assignment["slot_id"],
                    "grounded_profile_id": assignment["grounded_profile_id"],
                    "reported_segment_id": assignment["reported_segment_id"],
                    "structural_group_id": assignment["structural_group_id"],
                    "profile_snapshot_sha256": assignment[
                        "profile_snapshot_sha256"
                    ],
                }
                for assignment in roster["assignments"]
            ],
            [
                {
                    key: item[key]
                    for key in (
                        "audience_slot_id",
                        "grounded_profile_id",
                        "reported_segment_id",
                        "structural_group_id",
                        "profile_snapshot_sha256",
                    )
                }
                for item in attached["predeclared_pair_assignments"]
            ],
        )
        wave_tampered = deepcopy(attached)
        wave_tampered["predeclared_pair_assignments"][0]["wave"] = 2
        with self.assertRaises(ValueError):
            validate_boundary_profile_attachments(wave_tampered, roster)

    def test_job_and_response_require_explicit_method_without_defaulting(self):
        from audience_lab.responses import validate_job, validate_response

        job = load_json(FIXTURES / "screening-jobs-valid.json")["synthetic_replicate_jobs"][0]
        response = screening_source()
        job.pop("method")
        response.pop("method")
        self.assertTrue(any("method" in error for error in validate_job(job)))
        self.assertTrue(any("method" in error for error in validate_response(response)))

    def test_complete_exposure_has_a_distinct_valid_record_and_rejects_maxdiff_leakage(self):
        from audience_lab.responses import validate_response

        response = complete_response(1)
        self.assertEqual([], validate_response(response))

        leaked = deepcopy(response)
        leaked["comparative_choice"] = {
            "status": "best_worst",
            "best_variation_id": "creative-a",
            "weakest_variation_id": "creative-d",
        }
        leaked["usable_maxdiff_block"] = True
        errors = validate_response(leaked)
        self.assertTrue(any("complete_exposure" in error for error in errors))


class DeterministicAggregationTests(unittest.TestCase):
    def test_complete_exposure_screening_cli_runs_production_resamples(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "manifest.json"
            jobs_path = temp / "jobs.json"
            responses_path = temp / "responses.jsonl"
            recovery_path = temp / "calibration.json"
            output_path = temp / "screening.json"
            write_json(manifest_path, complete_manifest())
            responses = [
                complete_response(index, ["creative-a", "creative-b", "creative-c", "creative-d"])
                for index in range(1, 10)
            ]
            write_json(
                jobs_path,
                {
                    "study_id": "complete-acme-001",
                    "method": "complete_exposure",
                    "record_type": "screening_response",
                    "synthetic_replicate_jobs": [complete_job(item) for item in responses],
                },
            )
            write_json(recovery_path, complete_calibration_policy())
            write_jsonl(
                responses_path,
                responses,
            )
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
                "screening",
                "--manifest",
                str(manifest_path),
                "--jobs",
                str(jobs_path),
                "--responses",
                str(responses_path),
                "--recovery-config",
                str(recovery_path),
                "--output",
                str(output_path),
            )
            payload = load_json(output_path) if output_path.exists() else {}

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("complete_exposure", payload["method"])
        self.assertEqual("valid", payload["validity_status"])
        self.assertEqual(["creative-a", "creative-b"], payload["proposed_finalist_ids"])
        self.assertEqual(2000, payload["model_diagnostics"]["bootstrap"]["requested_fits"])
        serialized = json.dumps(payload, sort_keys=True).lower()
        for forbidden in ("maxdiff", "davidson", "four-item", "four_item"):
            self.assertNotIn(forbidden, serialized)

    def test_finalist_cli_derives_counts_shares_and_exact_rubric_distributions(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = complete_manifest()
            manifest_path = temp / "manifest.json"
            screening_path = temp / "screening.json"
            approval_path = temp / "approval.json"
            jobs_path = temp / "finalist-jobs.json"
            responses_path = temp / "finalists.jsonl"
            output_path = temp / "finalist-results.json"
            write_json(manifest_path, manifest)
            write_json(
                screening_path,
                {
                    "study_id": manifest["study_id"],
                    "method": "complete_exposure",
                    "validity_status": "valid",
                    "selection_status": "resolved",
                    "proposed_finalist_ids": ["creative-a", "creative-b"],
                },
            )
            write_json(
                approval_path,
                {
                    "study_id": manifest["study_id"],
                    "approved_finalist_ids": ["creative-a", "creative-b"],
                    "roster_decision": {
                        "status": "approved",
                        "approved_at": "2026-07-22T12:00:00Z",
                        "approved_by": "study owner",
                        "override": False,
                        "changed_after_saliency_reveal": False,
                    },
                },
            )
            responses = [
                finalist_response(1, ["creative-a", "creative-b"]),
                finalist_response(2, ["creative-b", "creative-a"]),
                finalist_response(3, ["creative-a", "creative-b"]),
            ]
            write_json(
                jobs_path,
                {
                    "study_id": manifest["study_id"],
                    "method": manifest["method"],
                    "record_type": "finalist_response",
                    "synthetic_replicate_jobs": [complete_job(item) for item in responses],
                },
            )
            write_jsonl(responses_path, responses)
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
                "finalists",
                "--manifest",
                str(manifest_path),
                "--screening-results",
                str(screening_path),
                "--approval",
                str(approval_path),
                "--jobs",
                str(jobs_path),
                "--responses",
                str(responses_path),
                "--output",
                str(output_path),
            )
            payload = load_json(output_path) if output_path.exists() else {}

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(3, payload["accepted_response_records"])
        self.assertEqual(3, payload["accepted_unique_replicates"])
        self.assertEqual(9, payload["total_model_calls"])
        self.assertEqual({"creative-a": 2, "creative-b": 1}, payload["first_choice_counts"])
        self.assertAlmostEqual(2 / 3, payload["conditional_first_choice_share"]["creative-a"])
        self.assertEqual(
            {"3": 1, "5": 2},
            payload["rubric_summary"]["creative-a"]["overall"]["distribution"],
        )


class LineageAndAccountingTests(unittest.TestCase):
    def test_workflow_output_materializes_hash_bound_lineage_without_spending_retry_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = complete_manifest()
            response = complete_response(1)
            first = response["runtime_attempts"][0]
            accepted_id = first["provider_return_id"] + "-retry"
            first.update(outcome="rejected", validation_errors=["schema mismatch"])
            retry = deepcopy(first)
            retry.update(
                attempt_id=accepted_id,
                provider_return_id=accepted_id,
                attempt_number=2,
                outcome="accepted",
                validation_errors=[],
            )
            response["runtime_attempts"].insert(1, retry)
            response["per_creative_reactions"][0]["source_provenance"]["provider_return_id"] = accepted_id
            raw_returns = []
            for attempt in response["runtime_attempts"]:
                raw_returns.append(
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
            workflow = {
                "status": "complete",
                "requested_replicates": 1,
                "completed_replicates": 1,
                "responses": [response],
                "raw_provider_returns": raw_returns,
                "rejected_attempts": [
                    {
                        "provider_return_id": first["provider_return_id"],
                        "synthetic_replicate_id": response["synthetic_replicate_id"],
                        "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                        "stage": "reaction",
                        "position_seen": 1,
                        "attempt_number": 1,
                        "validation_errors": ["schema mismatch"],
                        "disposition": "retried",
                    }
                ],
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
                        "reaction_attempts": [2, 1, 1, 1],
                        "comparison_attempts": 1,
                    }
                ],
            }
            source_manifest = temp / "source-manifest.json"
            workflow_path = temp / "workflow.json"
            write_json(source_manifest, manifest)
            write_json(workflow_path, workflow)
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/materialize-run-lineage.py",
                str(workflow_path),
                str(source_manifest),
                str(temp / "run"),
            )
            run_dir = temp / "run"
            materialized = load_json(run_dir / "study-manifest.json") if (run_dir / "study-manifest.json").exists() else {}

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(6, materialized["usage"]["total_model_calls"])
            self.assertEqual(1, materialized["usage"]["unique_job_slots_dispatched"])
            self.assertEqual(1, materialized["usage"]["accepted_response_records"])
            for key, filename in (
                ("accepted_responses", "panelist-responses.jsonl"),
                ("raw_provider_returns", "raw-provider-returns.jsonl"),
                ("rejected_attempts", "rejected-attempts.jsonl"),
                ("dispatch_audit", "dispatch-audit.jsonl"),
            ):
                binding = materialized["outputs"][key]
                target = run_dir / filename
                self.assertEqual(filename, binding["path"])
                self.assertEqual(
                    "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
                    binding["content_hash"],
                )


class RuntimeAndPackageIntegrationTests(unittest.TestCase):
    def test_task3_assignment_core_adapter_produces_task4_valid_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            package_dir = temp / "audience-package"
            built = run_cli(
                "skills/audience-ad-testing-lab/scripts/build-audience-package.py",
                "--brief", "conformance/fixtures/audience-research/approved-brief.json",
                "--panel", "conformance/fixtures/audience-research/approved-panel.json",
                "--output-dir", str(package_dir),
            )
            package_path = package_dir / "audience-panel-package.zip"
            request = load_json(FIXTURES / "e2e-large" / "study-request.json")
            request["audience_panel"]["package_path"] = str(package_path)
            request_path = temp / "study-request.json"
            write_json(request_path, request)
            panel = load_json(FIXTURES / "audience-research" / "approved-panel.json")
            scope = {
                key: panel["audience_scope"][key]
                for key in (
                    "audience", "market", "geography", "category",
                    "buying_context", "exclusions",
                )
            }
            intake_path = temp / "intake.json"
            scope_path = temp / "scope.json"
            write_json(intake_path, {"audience_panel": request["audience_panel"]})
            write_json(scope_path, scope)
            resolved = run_cli(
                "skills/audience-ad-testing-lab/scripts/manage-audience-library.py",
                "resolve", str(intake_path), str(scope_path), str(temp),
            )
            resolution_path = temp / "audience" / "resolution.json"
            plan = temp / "plan.json"
            jobs = temp / "jobs.json"
            legacy_origin = temp / "legacy-v2-origin.json"
            planned = run_cli(
                "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
                str(request_path),
                str(plan),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                "1",
                "--boundary-jobs-per-wave",
                "8",
                "--boundary-waves-max",
                "2",
                "--finalist-reserved",
                "8",
                "--assignment-seed",
                "29",
                "--audience-resolution",
                str(resolution_path),
            )
            adapted = run_cli(
                "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
                str(plan),
                "conformance/fixtures/e2e-large/dispatch-context.json",
                str(jobs),
                "--audience-resolution",
                str(resolution_path),
                "--legacy-v2-origin-authority-output",
                str(legacy_origin),
            )
            validated = run_cli(
                "skills/audience-ad-testing-lab/scripts/validate-panel-run.py",
                str(jobs),
                "--legacy-v2-origin-authority",
                str(legacy_origin),
                "--expected-count",
                "16",
            )

        self.assertEqual(0, built.returncode, built.stderr)
        self.assertEqual(0, resolved.returncode, resolved.stderr)
        self.assertEqual(0, planned.returncode, planned.stderr)
        self.assertEqual(0, adapted.returncode, adapted.stderr)
        self.assertEqual(0, validated.returncode, validated.stdout + validated.stderr)

    def test_valid_large_library_e2e_runs_real_clis_and_preserves_invariants(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "e2e-report.json"
            completed = run_cli(
                "conformance/run_large_library_e2e.py",
                "--output-report",
                str(report),
            )
            payload = load_json(report) if report.exists() else {}

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("resolved", payload["boundary_status"])
        self.assertEqual(2000, payload["screening_resamples"])
        self.assertEqual(2000, payload["boundary_resamples"])
        self.assertTrue(payload["all_responses_valid_against_exact_job"])
        self.assertTrue(payload["boundary_plan_frozen_before_dispatch"])
        self.assertTrue(payload["only_authorized_boundary_waves_used"])
        self.assertEqual(payload["finalist_reserve_before"], payload["finalist_reserve_after"])
        self.assertTrue(payload["fresh_replicate_ids_across_stages"])
        self.assertTrue(payload["saliency_shortlist_invariant"])
        self.assertTrue(payload["dashboard_valid"])
        self.assertTrue(payload["exhausted_dispatch_rendered_and_validated"])
        self.assertTrue(payload["exhausted_dispatch_authorized"])
        self.assertEqual("incomplete", payload["exhausted_workflow_status"])
        self.assertEqual(0, payload["exhausted_composite_response_count"])
        self.assertEqual(5, payload["exhausted_provider_call_count"])
        self.assertEqual(3, payload["exhausted_accepted_component_call_count"])
        self.assertEqual([1, 2], payload["exhausted_retry_attempt_numbers"])
        self.assertEqual(
            payload["usage"]["accepted_response_records"] + 1,
            payload["usage"]["unique_job_slots_dispatched"],
        )
        self.assertEqual(47, payload["usage"]["accepted_response_records"])
        self.assertEqual(48, payload["usage"]["unique_job_slots_dispatched"])
        self.assertEqual(216, payload["usage"]["total_model_calls"])
        self.assertEqual(2, len(payload["studies"]))
        self.assertNotEqual(payload["studies"][0]["study_id"], payload["studies"][1]["study_id"])
        self.assertTrue(payload["audience_research_built_once"])
        self.assertEqual(1, payload["audience_research_build_count"])
        self.assertTrue(payload["bundled_local_evidence_verified"])
        self.assertFalse(payload["invented_public_urls"])
        self.assertEqual("approved", payload["research_brief_status"])
        self.assertTrue(payload["research_approval_verified"])
        self.assertTrue(payload["panel_registered_in_temporary_library"])
        self.assertTrue(payload["second_study_resolved_from_library"])
        self.assertFalse(payload["second_study_rebuilt_research"])
        self.assertTrue(payload["exact_package_bytes_reused"])
        self.assertTrue(payload["exact_package_hashes_reused"])
        self.assertTrue(payload["real_user_library_untouched"])
        self.assertEqual(2, payload["audience_segment_count"])
        self.assertEqual(4, payload["audience_mindset_count"])
        self.assertTrue(all(study["dashboard_valid"] for study in payload["studies"]))
        self.assertTrue(all(study["package_valid"] for study in payload["studies"]))
        self.assertEqual(
            payload["studies"][0]["package_zip_sha256"],
            payload["studies"][1]["package_zip_sha256"],
        )
        self.assertEqual(
            payload["studies"][0]["package_manifest_sha256"],
            payload["studies"][1]["package_manifest_sha256"],
        )

    def test_sum_failure_is_a_nonzero_blocking_result(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            image = temp / "image.png"
            image.write_bytes(b"not-a-provider-test-image")
            completed = run_cli(
                "skills/audience-ad-testing-lab/scripts/run-sum-saliency.py",
                "--img-path",
                str(image),
                "--output-dir",
                str(temp / "out"),
                "--condition",
                "2",
            )
            payload = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("blocked", payload["status"])
        self.assertTrue(payload["blocking"])

    def test_canonical_skill_bundle_clis_execute(self):
        bundle = SKILL_ROOT
        required = (
            bundle / "requirements-screening.txt",
            bundle / "scripts" / "plan-large-library.py",
            bundle / "scripts" / "prepare-panel-jobs.py",
            bundle / "scripts" / "aggregate-screening.py",
            bundle / "scripts" / "materialize-run-lineage.py",
            bundle / "scripts" / "render-dashboard.py",
            bundle / "scripts" / "audience_lab" / "responses.py",
        )
        self.assertTrue(all(path.is_file() for path in required))
        self.assertFalse((bundle / "agents" / "creative-specialist-prompt.md").exists())
        for duplicate in (
            "SKILL.md",
            "agents",
            "assets",
            "references",
            "requirements-screening.txt",
            "scripts",
        ):
            self.assertFalse((ROOT / duplicate).exists(), duplicate)
        help_result = run_cli(
            "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
            "--help",
        )
        self.assertEqual(0, help_result.returncode, help_result.stderr)

    def test_fast_ci_runs_every_private_stage_gate_without_node_syntax_check(self):
        workflow = (ROOT / ".github" / "workflows" / "validate-package.yml").read_text(
            encoding="utf-8"
        )
        partition_runner = (ROOT / "conformance" / "ci_fast_validation.py").read_text(
            encoding="utf-8"
        )
        normalized_workflow = " ".join(f"{workflow}\n{partition_runner}".split())
        for required in FAST_CI_MARKERS:
            self.assertIn(" ".join(required.split()), normalized_workflow)
        self.assertNotIn("sudo sysctl --write", workflow)
        self.assertNotIn("node --check", workflow)
        self.assertNotIn("uses: ./.github/actions/setup-private-stage", workflow)
        for heavy in (
            "ci_fast_validation.py run outcome-release",
            "calibration-engine-and-evaluation",
            "calibration-contracts-and-lifecycle",
        ):
            self.assertNotIn(heavy, workflow)

    def test_split_fast_ci_is_closed_parallel_and_fail_safe(self):
        workflow = (ROOT / ".github" / "workflows" / "validate-package.yml").read_text(
            encoding="utf-8"
        )
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        setup = (
            ROOT / ".github" / "actions" / "setup-private-stage" / "action.yml"
        ).read_text(encoding="utf-8")
        jobs = workflow.split("jobs:\n", 1)[1]
        self.assertEqual(
            ["contract-and-package"],
            re.findall(r"^  ([a-z][a-z0-9-]*):$", jobs, re.MULTILINE),
        )
        self.assertNotIn("\n  validate:\n", workflow)
        self.assertNotIn("python3 -m unittest", workflow)
        for required in (
            "contract-and-package:",
            "ci_fast_validation.py verify-inventory",
            "ci_fast_validation.py verify-release-identity",
            "ci_fast_validation.py run workflow-contracts",
            "ci_fast_validation.py run smoke",
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        ):
            self.assertIn(required, workflow)

        release_jobs = release.split("jobs:\n", 1)[1]
        self.assertEqual(
            ["update-manifests", "verify-release"],
            re.findall(r"^  ([a-z][a-z0-9-]*):$", release_jobs, re.MULTILINE),
        )
        verify = release.split("  verify-release:\n", 1)[1]
        needs_list = re.search(
            r"^    needs:\n((?:^      - [a-z][a-z0-9-]*\n)+)",
            verify,
            re.MULTILINE,
        )
        needs_scalar = re.search(
            r"^    needs:\s*([a-z][a-z0-9-]*)\s*$",
            verify,
            re.MULTILINE,
        )
        if needs_list is not None:
            dependencies = re.findall(
                r"^      - ([a-z][a-z0-9-]*)$",
                needs_list.group(1),
                re.MULTILINE,
            )
        elif needs_scalar is not None:
            dependencies = [needs_scalar.group(1)]
        else:
            self.fail("verify-release must declare needs: update-manifests")
        self.assertEqual(["update-manifests"], dependencies)
        normalized_release = " ".join(release.split())
        for required in RELEASE_GATE_MARKERS:
            self.assertIn(" ".join(required.split()), normalized_release)
        self.assertEqual(
            1,
            release.count("uses: ./.github/actions/setup-private-stage"),
        )
        self.assertIn('tags:\n      - "v*"', release)

        for required in (
            "actions/setup-python@v6",
            "cache: pip",
            *PRIVATE_STAGE_SETUP_MARKERS,
            "numpy==2.4.2 scipy==1.17.0 openpyxl==3.1.5",
        ):
            self.assertIn(required, setup)
        self.assertEqual(
            1,
            setup.count(
                "sudo sysctl --write "
                "kernel.apparmor_restrict_unprivileged_userns=0"
            ),
        )

    def test_manual_full_conformance_runs_every_extended_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "full-conformance.yml").read_text(
            encoding="utf-8"
        )
        normalized_workflow = " ".join(workflow.split())
        for required in FULL_CONFORMANCE_MARKERS:
            self.assertIn(" ".join(required.split()), normalized_workflow)
        self.assertEqual(
            2,
            workflow.count(
                "sudo sysctl --write "
                "kernel.apparmor_restrict_unprivileged_userns=0"
            ),
        )
        self.assertIn('cron: "0 6 * * 1-6"', workflow)
        self.assertIn('cron: "0 6 * * 0"', workflow)
        self.assertIn('tags:\n      - "v*"', workflow)
        self.assertIn("inputs.run_maximum_benchmark", workflow)
        self.assertEqual(
            1,
            workflow.count(
                "python3 -m unittest discover -s conformance -p 'test_*.py' -v"
            ),
        )
        self.assertNotIn(
            "name: Run experimental persona behavior calibration sandbox",
            workflow,
        )
        self.assertNotIn("name: Run complete outcome data prep suite", workflow)


class ScreenshotContractTests(unittest.TestCase):
    def test_plugin_screenshots_are_real_1440_by_900_png_files(self):
        for name in (
            "dashboard-summary.png",
            "dashboard-panelists.png",
            "dashboard-visual-evidence.png",
        ):
            with self.subTest(name=name):
                data = (ROOT / "docs" / "screenshots" / name).read_bytes()
                self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
                self.assertEqual((1440, 900), struct.unpack(">II", data[16:24]))


if __name__ == "__main__":
    unittest.main()
