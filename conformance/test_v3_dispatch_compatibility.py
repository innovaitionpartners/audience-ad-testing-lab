from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audience_lab.dispatch import enrich_assignment_jobs  # noqa: E402
from audience_lab.audience_allocation import (  # noqa: E402
    evaluate_allocation_subset,
)
from audience_lab.contracts import validate_v3_jobs_envelope  # noqa: E402
from audience_lab.audience_library import (  # noqa: E402
    audience_package_binding,
    load_audience_resolution,
    resolve_audience_panel,
)
from audience_lab.audience_package import build_audience_package  # noqa: E402
from conformance import test_v3_profile_rosters as roster_harness  # noqa: E402
from conformance.test_progressive_workflow import (  # noqa: E402
    run_workflow,
    write_workflow_legacy_v2_evidence,
)


V2_AUDIENCE_FIXTURES = (
    ROOT / "conformance" / "fixtures" / "audience-research"
)
V2_CLI_JOBS_SHA256 = (
    "e3f7985d23b5dbcff3e798fe6b4b61baabb7e7a2e0d1bc4c9287f2974ca17e00"
)
PERSONA_PROMPT_SHA256 = (
    "8cfc2806d9f6bfdd4a3193eda33c4c3adbd6a4bc69eb0ff6f9187f0b2aab25ff"
)


def _canonical_pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _v3_plan(root: Path, **options):
    harness = roster_harness.V3ProfileRosterTests()
    harness.setUp()
    return harness._valid_plan(root, **options)


def _manifest_from_plan(plan: dict[str, object]) -> dict[str, object]:
    harness = roster_harness.V3ProfileRosterTests()
    manifest = harness._manifest_from_plan(plan)
    creative_ids = sorted(
        {
            creative_id
            for job in plan["assignment"]["synthetic_replicate_jobs"]
            for creative_id in job["variation_ids"]
        }
    )
    manifest["outputs"]["creative_asset_hashes"] = {
        creative_id: f"sha256:{index:064x}"
        for index, creative_id in enumerate(creative_ids, 1)
    }
    return manifest


def _dispatch_context(plan: dict[str, object], record_type: str) -> dict[str, object]:
    creative_ids = sorted(
        {
            creative_id
            for job in plan["assignment"]["synthetic_replicate_jobs"]
            for creative_id in job["variation_ids"]
        }
    )
    return {
        "study_id": plan["study_id"],
        "record_type": record_type,
        "reaction_protocol": "progressive_reveal",
        "worker_context_isolation": "isolated",
        "creative_prompts": {
            creative_id: f"Review {creative_id}."
            for creative_id in creative_ids
        },
        "comparison_prompts": {
            "partial_exposure_maxdiff": "Choose strongest and weakest.",
            "complete_exposure": "Rank the set.",
        },
    }


def _boundary_authority(plan: dict[str, object]) -> dict[str, object]:
    assignments = []
    roster = plan["audience_profile_rosters"]["boundary_reserve"]
    for index, frozen in enumerate(roster["assignments"]):
        wave = int(frozen["slot_id"].split("-")[2])
        assignments.append(
            {
                "pair_assignment_id": frozen["slot_id"],
                "wave": wave,
                "variation_ids": [
                    f"creative-{index % 3 + 1}",
                    f"creative-{(index + 1) % 3 + 1}",
                ],
                "audience_slot_id": frozen["slot_id"],
                "grounded_profile_id": frozen["grounded_profile_id"],
                "reported_segment_id": frozen["reported_segment_id"],
                "structural_group_id": frozen["structural_group_id"],
                "profile_snapshot_sha256": frozen[
                    "profile_snapshot_sha256"
                ],
            }
        )
    creative_ids = sorted(
        {
            creative_id
            for job in plan["assignment"]["synthetic_replicate_jobs"]
            for creative_id in job["variation_ids"]
        }
    )
    clear_finalist_count = max(
        int(plan["requested_shortlist_size"]) - 2,
        0,
    )
    clear_finalist_ids = set(
        creative_ids[3 : 3 + clear_finalist_count]
    )
    return {
        "study_id": plan["study_id"],
        "method": plan["method"],
        "requested_top_k": plan["requested_shortlist_size"],
        "validity_status": "valid",
        "selection_status": "boundary_required",
        "classifications": {
            creative_id: (
                "boundary_candidate"
                if creative_id
                in {"creative-1", "creative-2", "creative-3"}
                else (
                    "clear_finalist"
                    if creative_id in clear_finalist_ids
                    else "clear_non_finalist"
                )
            )
            for creative_id in creative_ids
        },
        "boundary_plan": {
            "plan_version": "predeclared-boundary-v1",
            "frozen_before_dispatch": True,
            "available_boundary_reserve": len(assignments),
            "predeclared_pair_assignments": assignments,
        },
    }


def _run_prepare(
    root: Path,
    *,
    authority: dict[str, object],
    context: dict[str, object],
    manifest: dict[str, object],
    resolution_path: Path,
    allow_directional: bool = False,
    prior_jobs_envelope: dict[str, object] | None = None,
    prior_responses: list[dict[str, object]] | None = None,
    prior_boundary_result: dict[str, object] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    authority_path = root / "dispatch-authority.json"
    context_path = root / "dispatch-context.json"
    manifest_path = root / "manifest.json"
    output_path = root / "jobs.json"
    authority_path.write_text(
        json.dumps(authority, sort_keys=True), encoding="utf-8"
    )
    context_path.write_text(
        json.dumps(context, sort_keys=True), encoding="utf-8"
    )
    canonical_manifest = (
        _manifest_from_plan(manifest)
        if "outputs" not in manifest
        else manifest
    )
    manifest_path.write_text(
        json.dumps(
            canonical_manifest,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(SCRIPTS / "prepare-panel-jobs.py"),
        str(authority_path),
        str(context_path),
        str(output_path),
        "--manifest",
        str(manifest_path),
        "--audience-resolution",
        str(resolution_path),
    ]
    if allow_directional:
        command.append("--allow-directional-allocation")
    if prior_jobs_envelope is not None:
        prior_path = root / "prior-jobs-envelope.json"
        prior_path.write_text(
            json.dumps(prior_jobs_envelope, sort_keys=True),
            encoding="utf-8",
        )
        command.extend(["--prior-jobs-envelope", str(prior_path)])
    if prior_responses is not None:
        prior_responses_path = root / "prior-responses.json"
        prior_responses_path.write_text(
            json.dumps({"responses": prior_responses}, sort_keys=True),
            encoding="utf-8",
        )
        command.extend(["--prior-responses", str(prior_responses_path)])
    if prior_boundary_result is not None:
        prior_boundary_path = root / "prior-boundary-result.json"
        prior_boundary_path.write_text(
            json.dumps(
                prior_boundary_result,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        command.extend(
            ["--prior-boundary-result", str(prior_boundary_path)]
        )
    return (
        subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        ),
        output_path,
    )


def _v2_dispatch(
    root: Path,
    *,
    profile_snapshot: dict[str, object] | None = None,
    core: dict[str, object] | None = None,
    context: dict[str, object] | None = None,
    allow_directional: bool = False,
) -> dict[str, object]:
    brief = json.loads(
        (V2_AUDIENCE_FIXTURES / "approved-brief.json").read_text(
            encoding="utf-8"
        )
    )
    panel = json.loads(
        (V2_AUDIENCE_FIXTURES / "approved-panel.json").read_text(
            encoding="utf-8"
        )
    )
    if profile_snapshot is not None:
        panel["grounded_context_profiles"][0]["profile_snapshot"] = copy.deepcopy(
            profile_snapshot
        )
    scope = {
        key: copy.deepcopy(panel["audience_scope"][key])
        for key in (
            "audience",
            "market",
            "geography",
            "category",
            "buying_context",
            "exclusions",
        )
    }
    package = build_audience_package(brief, panel, root / "v2-package")
    resolution = resolve_audience_panel(
        {"source": "file", "package_path": str(package.package_zip_path)},
        scope,
        run_dir=root / "v2-run",
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    binding = audience_package_binding(root / "v2-run", resolution)
    plan = {
        "study_id": "bound-study",
        "method": "partial_exposure_maxdiff",
        "audience_lock": resolution["audience_lock"],
        "audience_package": binding,
        "synthetic_replicate_capacity": {"screening_planned": 1},
        "assignment": {
            "synthetic_replicate_jobs": [
                core
                or {
                    "synthetic_replicate_id": "replicate-1",
                    "segment_id": "operations-leaders",
                    "context_stratum_id": "active-evaluation",
                    "variation_ids": ["V1", "V2", "V3", "V4"],
                    "shown_order": ["V1", "V2", "V3", "V4"],
                }
            ]
        },
    }
    dispatch_context = context or {
        "study_id": "bound-study",
        "record_type": "screening_response",
        "reaction_protocol": "progressive_reveal",
        "worker_context_isolation": "isolated",
        "creative_prompts": {
            f"V{index}": f"Review V{index}."
            for index in range(1, 5)
        },
        "comparison_prompts": {
            "partial_exposure_maxdiff": "Choose strongest and weakest.",
            "complete_exposure": "Rank the set.",
        },
    }
    return enrich_assignment_jobs(
        plan,
        dispatch_context,
        audience_resolution=root
        / "v2-run"
        / "audience"
        / "resolution.json",
        allow_directional_allocation=allow_directional,
    )


def _prepare_honest_v2_producer_evidence(
    root: Path,
) -> dict[str, object]:
    payload = _v2_dispatch(root)
    resolution_path = root / "v2-run" / "audience" / "resolution.json"
    resolution = load_audience_resolution(resolution_path)
    package_binding = audience_package_binding(
        root / "v2-run",
        resolution,
    )
    source_job = payload["synthetic_replicate_jobs"][0]
    assignment_core = {
        "study_id": payload["study_id"],
        "method": payload["method"],
        "audience_lock": resolution["audience_lock"],
        "audience_package": package_binding,
        "synthetic_replicate_capacity": {
            "screening_planned": 1,
        },
        "assignment": {
            "synthetic_replicate_jobs": [
                {
                    "synthetic_replicate_id": source_job[
                        "synthetic_replicate_id"
                    ],
                    "segment_id": source_job["segment_id"],
                    "context_stratum_id": source_job.get(
                        "context_stratum_id"
                    ),
                    "variation_ids": source_job["variation_ids"],
                    "shown_order": source_job["shown_order"],
                }
            ]
        },
    }
    dispatch_context = {
        "study_id": payload["study_id"],
        "record_type": payload["record_type"],
        "reaction_protocol": "progressive_reveal",
        "worker_context_isolation": "isolated",
        "creative_prompts": {
            variation_id: f"Review {variation_id}."
            for variation_id in source_job["variation_ids"]
        },
        "comparison_prompts": {
            "partial_exposure_maxdiff": "Choose strongest and weakest.",
            "complete_exposure": "Rank the set.",
        },
    }
    assignment_path = root / "v2-assignment.json"
    context_path = root / "v2-context.json"
    jobs_path = root / "v2-jobs.json"
    provenance_path = root / "legacy-v2-origin.json"
    assignment_path.write_text(
        json.dumps(assignment_core, sort_keys=True),
        encoding="utf-8",
    )
    context_path.write_text(
        json.dumps(dispatch_context, sort_keys=True),
        encoding="utf-8",
    )
    prepared = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "prepare-panel-jobs.py"),
            str(assignment_path),
            str(context_path),
            str(jobs_path),
            "--audience-resolution",
            str(resolution_path),
            "--legacy-v2-origin-authority-output",
            str(provenance_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if prepared.returncode != 0:
        raise AssertionError(prepared.stderr)
    return {
        "payload": payload,
        "jobs_path": jobs_path,
        "provenance_path": provenance_path,
        "evidence_directory": root / "legacy-v2-origin.evidence",
        "prepared": prepared,
    }


def _validate_persisted_v3_jobs(
    payload: dict[str, object],
    *,
    plan: dict[str, object],
    authority: dict[str, object],
    resolution_path: Path,
    dispatch_authority: dict[str, object],
) -> dict[str, object]:
    kwargs = {
        "allocation_plan": plan,
        "authority": authority,
        "audience_resolution": resolution_path,
    }
    if (
        "dispatch_authority"
        in inspect.signature(validate_v3_jobs_envelope).parameters
    ):
        kwargs["dispatch_authority"] = dispatch_authority
    return validate_v3_jobs_envelope(payload, **kwargs)


def _legacy_v2_origin_authority(
    payload: dict[str, object],
) -> dict[str, object]:
    jobs = payload["synthetic_replicate_jobs"]
    return {
        "schema_version": "audience-jobs-origin-authority-v1",
        "origin": "legacy_v2",
        "producer": "prepare-panel-jobs.py",
        "producer_version": "2.0.0",
        "study_id": payload["study_id"],
        "method": payload["method"],
        "record_type": payload["record_type"],
        "synthetic_replicate_ids": [
            job["synthetic_replicate_id"] for job in jobs
        ],
    }


def _honest_v2_producer_record(
    payload: dict[str, object],
) -> dict[str, object]:
    jobs = payload["synthetic_replicate_jobs"]
    return {
        "schema_version": "audience-jobs-producer-record-v2",
        "origin": "legacy_v2",
        "producer": "prepare-panel-jobs.py",
        "producer_version": "2.1.0",
        "source_assignment_core": {
            "study_id": payload["study_id"],
            "method": payload["method"],
            "audience_package": {
                "panel_id": "operations-leaders",
                "panel_version": "1.0.0",
                "panel_sha256": "1" * 64,
                "panel_byte_count": 1,
                "brief_id": "operations-leaders-brief",
                "brief_sha256": "2" * 64,
                "brief_byte_count": 1,
                "package_manifest_sha256": "3" * 64,
                "package_manifest_byte_count": 1,
                "package_zip_sha256": "4" * 64,
                "package_zip_byte_count": 1,
                "resolved_snapshot_path": "audience/snapshot",
            },
            "assignment": {
                "synthetic_replicate_jobs": [
                    {
                        "synthetic_replicate_id":
                            job["synthetic_replicate_id"],
                        "segment_id": job["segment_id"],
                        "context_stratum_id":
                            job.get("context_stratum_id"),
                        "variation_ids": job["variation_ids"],
                        "shown_order": job["shown_order"],
                    }
                    for job in jobs
                ]
            },
        },
        "source_dispatch_context": {
            "study_id": payload["study_id"],
            "record_type": payload["record_type"],
        },
        "source_manifest": None,
        "canonical_job_cores": jobs,
    }


def _boundary_continuation_evidence(
    plan: dict[str, object],
    prior: dict[str, object],
    *,
    earlier_responses: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    prior_jobs = prior["synthetic_replicate_jobs"]
    prior_responses = copy.deepcopy(earlier_responses or [])
    for job in prior_jobs:
        response = run_workflow(job=job)["result"]["responses"][0]
        response["pair_assignment_id"] = job["pair_assignment_id"]
        response["boundary_wave"] = job["boundary_wave"]
        prior_responses.append(response)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest_path = root / "manifest.json"
        screening_path = root / "screening-results.json"
        responses_path = root / "responses.jsonl"
        output_path = root / "boundary-results.json"
        manifest_path.write_text(
            json.dumps(
                _manifest_from_plan(plan),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        screening_path.write_text(
            json.dumps(
                _boundary_authority(plan),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        responses_path.write_text(
            "".join(
                json.dumps(response, sort_keys=True, allow_nan=False)
                + "\n"
                for response in prior_responses
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "aggregate-screening.py"),
                "boundary",
                "--manifest",
                str(manifest_path),
                "--screening-results",
                str(screening_path),
                "--responses",
                str(responses_path),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "canonical boundary aggregation failed: "
                f"{completed.stdout}\n{completed.stderr}"
            )
        boundary_result = json.loads(
            output_path.read_text(encoding="utf-8")
        )
    return prior_responses, boundary_result


def _minimal_boundary_continuation_evidence(
    plan: dict[str, object],
    prior: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    prior_jobs = prior["synthetic_replicate_jobs"]
    prior_responses = [
        run_workflow(job=job)["result"]["responses"][0]
        for job in prior_jobs
    ]
    prior_ids = [job["audience_slot_id"] for job in prior_jobs]
    prior_wave = int(prior_ids[0].split("-")[2])
    roster_ids = [
        item["slot_id"]
        for item in plan["audience_profile_rosters"][
            "boundary_reserve"
        ]["assignments"]
    ]
    next_ids = [
        slot_id
        for slot_id in roster_ids
        if int(slot_id.split("-")[2]) == prior_wave + 1
    ]
    return prior_responses, {
        "status": "unresolved",
        "decision_audit": {
            "stopping_decision": {
                "resolved": False,
                "reason": "next_predeclared_wave_required",
                "wave": prior_wave,
            },
            "waves": [
                {
                    "wave": prior_wave,
                    "completed": True,
                    "predeclared_job_ids": prior_ids,
                    "received_job_ids": prior_ids,
                    "usable_response_ids": sorted(
                        response["response_id"]
                        for response in prior_responses
                    ),
                }
            ],
            "next_wave_job_ids": next_ids,
        },
    }


class V3FrozenProfileDispatchTests(unittest.TestCase):
    maxDiff = None

    def test_screening_dispatch_selects_the_exact_frozen_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, resolution_path = _v3_plan(Path(temporary))
            try:
                payload = enrich_assignment_jobs(
                    plan,
                    _dispatch_context(plan, "screening_response"),
                    audience_resolution=resolution_path,
                )
            except ValueError as exc:
                self.fail(f"v3 frozen-profile dispatch was rejected: {exc}")

        assignment = plan["audience_profile_rosters"]["screening"][
            "assignments"
        ][0]
        profile = next(
            item
            for item in plan["grounded_context_profiles"]
            if item["grounded_profile_id"]
            == assignment["grounded_profile_id"]
        )
        job = payload["synthetic_replicate_jobs"][0]
        self.assertEqual(assignment["slot_id"], job["audience_slot_id"])
        self.assertEqual(
            assignment["grounded_profile_id"],
            job["grounded_profile_id"],
        )
        self.assertEqual(
            assignment["profile_snapshot_sha256"],
            job["profile_snapshot_sha256"],
        )
        self.assertEqual(profile["profile_snapshot"], job["profile_snapshot"])
        self.assertEqual(profile["reported_segment_id"], job["segment_id"])
        self.assertEqual(
            profile["context_stratum_id"],
            job["context_stratum_id"],
        )

    def test_v2_enriched_job_and_persona_prompt_bytes_remain_golden(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = _v2_dispatch(Path(temporary))
        cli_bytes = (
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        prompt_bytes = (
            ROOT
            / "skills"
            / "audience-ad-testing-lab"
            / "agents"
            / "persona-reviewer-prompt.md"
        ).read_bytes()

        self.assertEqual(
            V2_CLI_JOBS_SHA256, hashlib.sha256(cli_bytes).hexdigest()
        )
        self.assertEqual(2089, len(cli_bytes))
        self.assertEqual(
            PERSONA_PROMPT_SHA256,
            hashlib.sha256(prompt_bytes).hexdigest(),
        )
        self.assertEqual(10173, len(prompt_bytes))

    def test_directional_allocation_flag_is_rejected_for_v2_dispatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "v3"):
                _v2_dispatch(
                    Path(temporary),
                    allow_directional=True,
                )

    def test_v2_cli_validation_requires_explicit_legacy_origin_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = _v2_dispatch(root)
            resolution_path = (
                root / "v2-run" / "audience" / "resolution.json"
            )
            resolution = load_audience_resolution(resolution_path)
            package_binding = audience_package_binding(
                root / "v2-run",
                resolution,
            )
            source_job = payload["synthetic_replicate_jobs"][0]
            assignment_core = {
                "study_id": payload["study_id"],
                "method": payload["method"],
                "audience_lock": resolution["audience_lock"],
                "audience_package": package_binding,
                "synthetic_replicate_capacity": {
                    "screening_planned": 1,
                },
                "assignment": {
                    "synthetic_replicate_jobs": [
                        {
                            "synthetic_replicate_id": source_job[
                                "synthetic_replicate_id"
                            ],
                            "segment_id": source_job["segment_id"],
                            "context_stratum_id": source_job.get(
                                "context_stratum_id"
                            ),
                            "variation_ids": source_job["variation_ids"],
                            "shown_order": source_job["shown_order"],
                        }
                    ]
                },
            }
            dispatch_context = {
                "study_id": payload["study_id"],
                "record_type": payload["record_type"],
                "reaction_protocol": "progressive_reveal",
                "worker_context_isolation": "isolated",
                "creative_prompts": {
                    variation_id: f"Review {variation_id}."
                    for variation_id in source_job["variation_ids"]
                },
                "comparison_prompts": {
                    "partial_exposure_maxdiff":
                        "Choose strongest and weakest.",
                    "complete_exposure": "Rank the set.",
                },
            }
            assignment_path = root / "v2-assignment.json"
            context_path = root / "v2-context.json"
            jobs_path = root / "v2-jobs.json"
            provenance_path = root / "legacy-v2-origin.json"
            assignment_path.write_text(
                json.dumps(assignment_core, sort_keys=True),
                encoding="utf-8",
            )
            context_path.write_text(
                json.dumps(dispatch_context, sort_keys=True),
                encoding="utf-8",
            )
            prepared = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "prepare-panel-jobs.py"),
                    str(assignment_path),
                    str(context_path),
                    str(jobs_path),
                    "--audience-resolution",
                    str(resolution_path),
                    "--legacy-v2-origin-authority-output",
                    str(provenance_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            producer_record = json.loads(
                provenance_path.read_text(encoding="utf-8")
            )
            self.assertIn(
                "producer_evidence",
                producer_record["source_dispatch_context"],
            )
            evidence = producer_record["source_dispatch_context"][
                "producer_evidence"
            ]
            self.assertEqual(
                "audience-jobs-producer-evidence-v1",
                evidence["schema_version"],
            )
            for binding_name in (
                "source_package",
                "source_package_validation",
                "source_assignment",
                "source_dispatch_context",
                "produced_jobs",
            ):
                binding = evidence[binding_name]
                evidence_path = provenance_path.parent / binding["path"]
                self.assertTrue(evidence_path.is_file())
                self.assertEqual(
                    binding["sha256"],
                    hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                )
            validated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(jobs_path),
                    "--legacy-v2-origin-authority",
                    str(provenance_path),
                    "--expected-count",
                    "1",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        self.assertEqual(0, validated.returncode, validated.stdout)
        self.assertIn("validation passed", validated.stdout.lower())

    def test_workflow_accepts_real_independent_v2_producer_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = _prepare_honest_v2_producer_evidence(
                Path(temporary)
            )
            job = prepared["payload"]["synthetic_replicate_jobs"][0]
            execution = run_workflow(
                job=job,
                legacy_origin_authority=str(
                    prepared["provenance_path"].resolve()
                ),
                screening_status="no_meaningful_difference",
            )
            prepared["evidence_directory"].chmod(0o700)

        self.assertEqual("complete", execution["result"]["status"])
        self.assertEqual(1, len(execution["result"]["responses"]))

    def test_workflow_rejects_valid_v2_zip_current_schema_candidate_only_forgery_before_worker_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root / "v3")
            payload = enrich_assignment_jobs(
                plan,
                _dispatch_context(plan, "screening_response"),
                audience_resolution=resolution_path,
            )
            stripped_job = copy.deepcopy(
                payload["synthetic_replicate_jobs"][0]
            )
            for field in (
                "audience_slot_id",
                "grounded_profile_id",
                "profile_snapshot_sha256",
            ):
                stripped_job.pop(field)
            record_path, evidence_directory = (
                write_workflow_legacy_v2_evidence(
                    root / "forged",
                    stripped_job,
                )
            )
            candidate_path = root / "candidate-jobs.json"
            candidate_path.write_bytes(
                _canonical_pretty_bytes(
                    {
                        "study_id": stripped_job["study_id"],
                        "method": stripped_job["method"],
                        "record_type": stripped_job["record_type"],
                        "synthetic_replicate_jobs": [stripped_job],
                    }
                )
            )
            python_rejection = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(candidate_path),
                    "--legacy-v2-origin-authority",
                    str(record_path),
                    "--expected-count",
                    "1",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            workflow_attempt = run_workflow(
                job=stripped_job,
                legacy_origin_authority=str(record_path),
                capture_failure=True,
            )
            evidence_directory.chmod(0o700)

        self.assertNotEqual(0, python_rejection.returncode)
        self.assertRegex(
            python_rejection.stdout.lower(),
            "resolved audience|complete produced job cores|source package",
        )
        self.assertIn(
            "error",
            workflow_attempt,
            "candidate-only Workflow forgery reached "
            f"{len(workflow_attempt['calls'])} worker calls",
        )
        self.assertEqual([], workflow_attempt["calls"])

    def test_workflow_rejects_exact_helper_with_alternate_semantic_dependencies_before_worker_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root / "v3")
            payload = enrich_assignment_jobs(
                plan,
                _dispatch_context(plan, "screening_response"),
                audience_resolution=resolution_path,
            )
            stripped_job = copy.deepcopy(
                payload["synthetic_replicate_jobs"][0]
            )
            for field in (
                "audience_slot_id",
                "grounded_profile_id",
                "profile_snapshot_sha256",
            ):
                stripped_job.pop(field)
            record_path, evidence_directory = (
                write_workflow_legacy_v2_evidence(
                    root / "forged",
                    stripped_job,
                )
            )
            candidate_path = root / "candidate-jobs.json"
            candidate_path.write_bytes(
                _canonical_pretty_bytes(
                    {
                        "study_id": stripped_job["study_id"],
                        "method": stripped_job["method"],
                        "record_type": stripped_job["record_type"],
                        "synthetic_replicate_jobs": [stripped_job],
                    }
                )
            )
            python_rejection = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(candidate_path),
                    "--legacy-v2-origin-authority",
                    str(record_path),
                    "--expected-count",
                    "1",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            alternate_root = root / "caller-controlled-runtime"
            alternate_scripts = alternate_root / "scripts"
            alternate_package = alternate_scripts / "audience_lab"
            alternate_package.mkdir(parents=True)
            shutil.copyfile(
                SCRIPTS / "validate-workflow-v2-origin.py",
                alternate_scripts / "validate-workflow-v2-origin.py",
            )
            semantic_bundle = (
                SCRIPTS / "workflow-v2-semantic-bundle.b85"
            )
            if semantic_bundle.is_file():
                shutil.copyfile(
                    semantic_bundle,
                    alternate_scripts / semantic_bundle.name,
                )
            (alternate_package / "__init__.py").write_text(
                '"""Caller-controlled alternate semantic package."""\n',
                encoding="utf-8",
            )
            (alternate_package / "legacy_v2_origin.py").write_text(
                "def validate_legacy_v2_producer_record("
                "record, candidate_jobs_payload, *, record_path=None):\n"
                "    return dict(record)\n",
                encoding="utf-8",
            )
            workflow_attempt = run_workflow(
                job=stripped_job,
                legacy_origin_authority=str(record_path),
                capture_failure=True,
                workflow_cwd=alternate_root,
            )
            evidence_directory.chmod(0o700)

        self.assertNotEqual(0, python_rejection.returncode)
        self.assertRegex(
            python_rejection.stdout.lower(),
            "resolved audience|complete produced job cores|source package",
        )
        self.assertIn(
            "error",
            workflow_attempt,
            "exact approved helper loaded caller-controlled semantic "
            f"dependencies and reached {len(workflow_attempt['calls'])} "
            "worker calls",
        )
        self.assertEqual([], workflow_attempt["calls"])

    def test_workflow_semantic_preflight_failures_stop_before_worker_calls(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = _prepare_honest_v2_producer_evidence(
                root / "honest"
            )
            job = prepared["payload"]["synthetic_replicate_jobs"][0]
            unavailable_cwd = root / "without-shipped-validator"
            unavailable_cwd.mkdir()
            helper_hash_cwd = root / "helper-hash-mismatch"
            helper_hash_scripts = helper_hash_cwd / "scripts"
            helper_hash_scripts.mkdir(parents=True)
            helper_hash_scripts.joinpath(
                "validate-workflow-v2-origin.py"
            ).write_bytes(
                (
                    SCRIPTS / "validate-workflow-v2-origin.py"
                ).read_bytes()
                + b"\n"
            )
            shutil.copyfile(
                SCRIPTS / "workflow-v2-semantic-bundle.b85",
                helper_hash_scripts
                / "workflow-v2-semantic-bundle.b85",
            )
            bundle_hash_cwd = root / "bundle-hash-mismatch"
            bundle_hash_scripts = bundle_hash_cwd / "scripts"
            bundle_hash_scripts.mkdir(parents=True)
            shutil.copyfile(
                SCRIPTS / "validate-workflow-v2-origin.py",
                bundle_hash_scripts
                / "validate-workflow-v2-origin.py",
            )
            bundle_hash_scripts.joinpath(
                "workflow-v2-semantic-bundle.b85"
            ).write_bytes(
                (
                    SCRIPTS / "workflow-v2-semantic-bundle.b85"
                ).read_bytes()
                + b"\n"
            )
            cases = {
                "capability_unavailable": {
                    "builtin_module_mode": "capability_unavailable",
                },
                "missing_interpreter": {
                    "builtin_module_mode": "missing_interpreter",
                },
                "timeout": {"builtin_module_mode": "timeout"},
                "signal": {"builtin_module_mode": "signal"},
                "stderr": {"builtin_module_mode": "stderr"},
                "nonzero": {"builtin_module_mode": "nonzero"},
                "malformed_output": {
                    "builtin_module_mode": "malformed_output",
                },
                "verdict_hash_mismatch": {
                    "builtin_module_mode": "hash_mismatch",
                },
                "cleanup_failure": {
                    "builtin_module_mode": "cleanup_failure",
                },
                "missing_helper": {"workflow_cwd": unavailable_cwd},
                "helper_hash_mismatch": {
                    "workflow_cwd": helper_hash_cwd,
                },
                "bundle_hash_mismatch": {
                    "workflow_cwd": bundle_hash_cwd,
                },
            }
            for case, overrides in cases.items():
                with self.subTest(case=case):
                    attempt = run_workflow(
                        job=job,
                        legacy_origin_authority=str(
                            prepared["provenance_path"].resolve()
                        ),
                        capture_failure=True,
                        **overrides,
                    )
                    self.assertIn("error", attempt)
                    self.assertEqual([], attempt["calls"])
            prepared["evidence_directory"].chmod(0o700)

    def test_v2_cli_rejects_unsafe_or_mismatched_independent_evidence(
        self,
    ) -> None:
        evidence_binding_names = (
            "source_package",
            "source_package_validation",
            "source_assignment",
            "source_dispatch_context",
            "source_manifest",
            "produced_jobs",
        )

        def reseal(record: dict[str, object]) -> None:
            evidence = record["source_dispatch_context"][
                "producer_evidence"
            ]
            identity = {
                name: evidence[name]
                for name in evidence_binding_names
            }
            evidence["evidence_id"] = hashlib.sha256(
                _canonical_pretty_bytes(identity)
            ).hexdigest()

        cases = (
            "unsafe_path",
            "symlink",
            "missing",
            "noncanonical_json",
            "hash_mismatch",
            "assignment_mismatch",
            "invalid_package",
            "package_claim_mismatch",
            "job_core_mismatch",
        )
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    prepared = _prepare_honest_v2_producer_evidence(
                        root
                    )
                    record_path = prepared["provenance_path"]
                    jobs_path = prepared["jobs_path"]
                    evidence_directory = prepared[
                        "evidence_directory"
                    ]
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    evidence = record["source_dispatch_context"][
                        "producer_evidence"
                    ]
                    evidence_directory.chmod(0o700)
                    record_path.chmod(0o600)

                    if case == "unsafe_path":
                        evidence["source_assignment"][
                            "path"
                        ] = "../source-assignment.json"
                        reseal(record)
                    elif case in {"symlink", "missing"}:
                        binding = evidence["source_assignment"]
                        target = record_path.parent / binding["path"]
                        raw = target.read_bytes()
                        target.unlink()
                        if case == "symlink":
                            outside = root / "outside-assignment.json"
                            outside.write_bytes(raw)
                            outside.chmod(0o400)
                            target.symlink_to(outside)
                    else:
                        binding_name = {
                            "noncanonical_json": "source_assignment",
                            "hash_mismatch": "source_assignment",
                            "assignment_mismatch": "source_assignment",
                            "invalid_package": "source_package",
                            "package_claim_mismatch":
                                "source_assignment",
                            "job_core_mismatch": "produced_jobs",
                        }[case]
                        binding = evidence[binding_name]
                        target = record_path.parent / binding["path"]
                        target.chmod(0o600)
                        raw = target.read_bytes()
                        if case == "noncanonical_json":
                            raw += b" "
                        elif case == "hash_mismatch":
                            binding["sha256"] = "0" * 64
                        elif case == "assignment_mismatch":
                            value = json.loads(raw)
                            value["study_id"] = "different-study"
                            raw = _canonical_pretty_bytes(value)
                        elif case == "invalid_package":
                            raw = b"\x00" + raw[1:]
                        elif case == "package_claim_mismatch":
                            value = json.loads(raw)
                            value["audience_package"][
                                "package_zip_sha256"
                            ] = "f" * 64
                            raw = _canonical_pretty_bytes(value)
                            record["source_assignment_core"][
                                "audience_package"
                            ]["package_zip_sha256"] = "f" * 64
                        elif case == "job_core_mismatch":
                            value = json.loads(raw)
                            value["synthetic_replicate_jobs"][0][
                                "dispatch_id"
                            ] = "different-dispatch"
                            raw = _canonical_pretty_bytes(value)
                        if case != "hash_mismatch":
                            target.write_bytes(raw)
                            target.chmod(0o400)
                            binding["sha256"] = hashlib.sha256(
                                raw
                            ).hexdigest()
                            binding["byte_count"] = len(raw)
                        reseal(record)

                    record_path.write_bytes(
                        _canonical_pretty_bytes(record)
                    )
                    record_path.chmod(0o400)
                    evidence_directory.chmod(0o500)
                    rejected = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPTS / "validate-panel-run.py"),
                            str(jobs_path),
                            "--legacy-v2-origin-authority",
                            str(record_path),
                            "--expected-count",
                            "1",
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    evidence_directory.chmod(0o700)

                self.assertNotEqual(
                    0,
                    rejected.returncode,
                    f"{case} unexpectedly passed: {rejected.stdout}",
                )

    def test_stripped_v3_jobs_cannot_downgrade_to_implicit_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            payload = enrich_assignment_jobs(
                plan,
                _dispatch_context(plan, "screening_response"),
                audience_resolution=resolution_path,
            )
            stripped = copy.deepcopy(payload)
            for field in (
                "audience_allocation_subset",
                "audience_run_claim",
                "audience_dispatch",
            ):
                stripped.pop(field)
            for job in stripped["synthetic_replicate_jobs"]:
                for field in (
                    "audience_slot_id",
                    "grounded_profile_id",
                    "profile_snapshot_sha256",
                ):
                    job.pop(field)
            jobs_path = root / "stripped-v3-jobs.json"
            jobs_path.write_text(
                json.dumps(stripped, sort_keys=True),
                encoding="utf-8",
            )

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(jobs_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            forged_authority_path = root / "forged-legacy-origin.json"
            forged_authority_path.write_text(
                json.dumps(
                    _legacy_v2_origin_authority(stripped),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            forged = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(jobs_path),
                    "--legacy-v2-origin-authority",
                    str(forged_authority_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("origin", rejected.stdout.lower())
        self.assertNotEqual(0, forged.returncode)
        self.assertRegex(
            forged.stdout.lower(),
            "producer evidence|origin authority|legacy",
        )

    def test_current_schema_forged_v2_record_with_invented_evidence_is_rejected_by_cli(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            payload = enrich_assignment_jobs(
                plan,
                _dispatch_context(plan, "screening_response"),
                audience_resolution=resolution_path,
            )
            stripped = copy.deepcopy(payload)
            for field in (
                "audience_allocation_subset",
                "audience_run_claim",
                "audience_dispatch",
            ):
                stripped.pop(field)
            for job in stripped["synthetic_replicate_jobs"]:
                for field in (
                    "audience_slot_id",
                    "grounded_profile_id",
                    "profile_snapshot_sha256",
                ):
                    job.pop(field)
            jobs_path = root / "stripped-v3-jobs.json"
            jobs_path.write_text(
                json.dumps(stripped, sort_keys=True),
                encoding="utf-8",
            )
            forged_authority_path = root / "forged-current-v2-origin.json"
            forged_authority_path.write_text(
                json.dumps(
                    _honest_v2_producer_record(stripped),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            forged_authority_path.chmod(0o400)
            forged = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(jobs_path),
                    "--legacy-v2-origin-authority",
                    str(forged_authority_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, forged.returncode)
        self.assertRegex(
            forged.stdout.lower(),
            "independent|producer evidence|package",
        )

    def test_identical_grounded_profile_content_keeps_v2_and_v3_worker_inputs_identical(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root / "v3")
            context = _dispatch_context(plan, "screening_response")
            v3_payload = enrich_assignment_jobs(
                plan,
                context,
                audience_resolution=resolution_path,
            )
            v3_job = v3_payload["synthetic_replicate_jobs"][0]
            v2_core = copy.deepcopy(
                plan["assignment"]["synthetic_replicate_jobs"][0]
            )
            v2_context = copy.deepcopy(context)
            v2_context["study_id"] = "bound-study"
            v2_payload = _v2_dispatch(
                root,
                profile_snapshot=v3_job["profile_snapshot"],
                core=v2_core,
                context=v2_context,
            )
            v2_job = v2_payload["synthetic_replicate_jobs"][0]

        self.assertEqual(v2_job["profile_snapshot"], v3_job["profile_snapshot"])
        self.assertEqual(v2_job["reaction_prompts"], v3_job["reaction_prompts"])
        self.assertEqual(
            v2_job["comparison_prompt"], v3_job["comparison_prompt"]
        )

    def test_v3_dispatch_rejects_every_mutable_profile_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, resolution_path = _v3_plan(Path(temporary))
            context = _dispatch_context(plan, "screening_response")
            mutations = {}

            wrong_slot = copy.deepcopy(plan)
            wrong_slot["assignment"]["synthetic_replicate_jobs"][0][
                "synthetic_replicate_id"
            ] = "unknown-slot"
            mutations["wrong slot"] = (wrong_slot, context)

            wrong_segment = copy.deepcopy(plan)
            wrong_segment["assignment"]["synthetic_replicate_jobs"][0][
                "segment_id"
            ] = "wrong-segment"
            mutations["wrong segment"] = (wrong_segment, context)

            wrong_context = copy.deepcopy(plan)
            wrong_context["assignment"]["synthetic_replicate_jobs"][0][
                "context_stratum_id"
            ] = "wrong-context"
            mutations["wrong context"] = (wrong_context, context)

            wrong_profile = copy.deepcopy(plan)
            wrong_profile["assignment"]["synthetic_replicate_jobs"][0][
                "grounded_profile_id"
            ] = "free-form-profile"
            mutations["wrong profile"] = (wrong_profile, context)

            wrong_hash = copy.deepcopy(plan)
            wrong_hash["assignment"]["synthetic_replicate_jobs"][0][
                "profile_snapshot_sha256"
            ] = "sha256:" + "0" * 64
            mutations["wrong snapshot hash"] = (wrong_hash, context)

            absent_roster = copy.deepcopy(plan)
            absent_roster.pop("audience_profile_rosters")
            mutations["absent roster"] = (absent_roster, context)

            duplicate_slot = copy.deepcopy(plan)
            screening = duplicate_slot["audience_profile_rosters"][
                "screening"
            ]
            screening["assignments"][1]["slot_id"] = screening[
                "assignments"
            ][0]["slot_id"]
            mutations["duplicate slot"] = (duplicate_slot, context)

            injected_context = copy.deepcopy(context)
            injected_context["profiles"] = [
                copy.deepcopy(plan["grounded_context_profiles"][0])
            ]
            mutations["free-form profile injection"] = (
                copy.deepcopy(plan),
                injected_context,
            )

            for label, (mutated_plan, mutated_context) in mutations.items():
                with self.subTest(label=label), self.assertRaises(ValueError):
                    enrich_assignment_jobs(
                        mutated_plan,
                        mutated_context,
                        audience_resolution=resolution_path,
                    )

    def test_distorted_boundary_subset_writes_only_the_exit_six_decision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1]
            completed, output_path = _run_prepare(
                root,
                authority=_boundary_authority(plan),
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(6, completed.returncode, completed.stderr)
        self.assertEqual(
            "audience-profile-allocation-subset-v1",
            payload["schema_version"],
        )
        self.assertEqual(
            "requires_user_decision", payload["claim_effect"]
        )
        self.assertNotIn("synthetic_replicate_jobs", payload)

    def test_minimal_hand_authored_boundary_result_is_not_continuation_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(
                root,
                boundary_jobs_per_wave=3,
            )
            authority = _boundary_authority(plan)
            first_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            first_context["boundary_waves"] = [1]
            first, first_output = _run_prepare(
                root,
                authority=authority,
                context=first_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, first.returncode, first.stderr)
            prior = json.loads(first_output.read_text(encoding="utf-8"))
            responses, fabricated = (
                _minimal_boundary_continuation_evidence(plan, prior)
            )
            second_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            second_context["boundary_waves"] = [2]
            completed, output_path = _run_prepare(
                root,
                authority=authority,
                context=second_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=prior,
                prior_responses=responses,
                prior_boundary_result=fabricated,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("canonical", completed.stderr.lower())
        self.assertFalse(output_path.exists())

    def test_later_boundary_wave_requires_validated_prior_cumulative_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(
                root,
                boundary_jobs_per_wave=3,
            )
            authority = _boundary_authority(plan)
            second_wave = _dispatch_context(plan, "boundary_response")
            second_wave["boundary_waves"] = [2]
            fresh, fresh_output = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            fresh_created_output = fresh_output.exists()
            first_wave = _dispatch_context(plan, "boundary_response")
            first_wave["boundary_waves"] = [1]
            first, first_output = _run_prepare(
                root,
                authority=authority,
                context=first_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, first.returncode, first.stderr)
            prior = json.loads(first_output.read_text(encoding="utf-8"))
            prior_responses, prior_boundary_result = (
                _boundary_continuation_evidence(plan, prior)
            )
            roster_ids = [
                item["slot_id"]
                for item in plan["audience_profile_rosters"][
                    "boundary_reserve"
                ]["assignments"]
            ]
            wave_size = plan["synthetic_replicate_capacity"][
                "boundary_jobs_per_wave"
            ]
            tampered_prior = copy.deepcopy(prior)
            tampered_prior["synthetic_replicate_jobs"][0][
                "profile_snapshot"
            ] = {"injected": True}
            first_output.unlink()
            rejected_prior, rejected_output = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=tampered_prior,
                prior_responses=prior_responses,
                prior_boundary_result=prior_boundary_result,
            )
            rejected_created_output = rejected_output.exists()
            wrong_next = copy.deepcopy(prior_boundary_result)
            wrong_next["decision_audit"]["next_wave_job_ids"] = list(
                reversed(
                    wrong_next["decision_audit"]["next_wave_job_ids"]
                )
            )
            wrong_next_result, wrong_next_output = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=prior,
                prior_responses=prior_responses,
                prior_boundary_result=wrong_next,
            )
            wrong_next_created_output = wrong_next_output.exists()
            minimal_result = {
                "status": "unresolved",
                "decision_audit": {
                    "stopping_decision": {
                        "resolved": False,
                        "reason": "next_predeclared_wave_required",
                        "wave": 1,
                    },
                    "waves": prior_boundary_result[
                        "decision_audit"
                    ]["waves"],
                    "next_wave_job_ids": prior_boundary_result[
                        "decision_audit"
                    ]["next_wave_job_ids"],
                },
            }
            minimal, minimal_output = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=prior,
                prior_responses=prior_responses,
                prior_boundary_result=minimal_result,
            )
            minimal_created_output = minimal_output.exists()
            continued, continued_output = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=prior,
                prior_responses=prior_responses,
                prior_boundary_result=prior_boundary_result,
            )
            payload = (
                json.loads(continued_output.read_text(encoding="utf-8"))
                if continued_output.exists()
                else {}
            )

        self.assertEqual(2, fresh.returncode)
        self.assertIn("prior", fresh.stderr.lower())
        self.assertFalse(fresh_created_output)
        self.assertEqual(2, rejected_prior.returncode)
        self.assertIn("canonical profile", rejected_prior.stderr)
        self.assertFalse(rejected_created_output)
        self.assertEqual(2, wrong_next_result.returncode)
        self.assertIn("canonical aggregator", wrong_next_result.stderr)
        self.assertFalse(wrong_next_created_output)
        self.assertEqual(2, minimal.returncode)
        self.assertIn("canonical", minimal.stderr.lower())
        self.assertFalse(minimal_created_output)
        self.assertEqual(0, continued.returncode, continued.stderr)
        self.assertEqual(
            roster_ids,
            payload["audience_allocation_subset"][
                "selected_slot_ids"
            ],
        )
        self.assertEqual(
            roster_ids[wave_size:],
            payload["audience_dispatch"][
                "newly_authorized_slot_ids"
            ],
        )
        self.assertEqual(
            "frame_aligned",
            payload["audience_run_claim"],
        )
        self.assertEqual(
            roster_ids[wave_size:],
            [
                job["audience_slot_id"]
                for job in payload["synthetic_replicate_jobs"]
            ],
        )
        first_second_wave_job = payload["synthetic_replicate_jobs"][0]
        self.assertEqual(
            list(reversed(first_second_wave_job["variation_ids"])),
            first_second_wave_job["shown_order"],
        )

    def test_honest_canonical_cumulative_evidence_authorizes_wave_three(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(
                root,
                boundary_jobs_per_wave=3,
                boundary_waves_max=3,
            )
            authority = _boundary_authority(plan)

            wave_one_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_one_context["boundary_waves"] = [1]
            wave_one, wave_one_path = _run_prepare(
                root,
                authority=authority,
                context=wave_one_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, wave_one.returncode, wave_one.stderr)
            wave_one_envelope = json.loads(
                wave_one_path.read_text(encoding="utf-8")
            )
            wave_one_responses, wave_one_result = (
                _boundary_continuation_evidence(
                    plan,
                    wave_one_envelope,
                )
            )

            wave_two_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_two_context["boundary_waves"] = [2]
            wave_two, wave_two_path = _run_prepare(
                root,
                authority=authority,
                context=wave_two_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=wave_one_envelope,
                prior_responses=wave_one_responses,
                prior_boundary_result=wave_one_result,
            )
            self.assertEqual(0, wave_two.returncode, wave_two.stderr)
            wave_two_envelope = json.loads(
                wave_two_path.read_text(encoding="utf-8")
            )
            cumulative_responses, wave_two_result = (
                _boundary_continuation_evidence(
                    plan,
                    wave_two_envelope,
                    earlier_responses=wave_one_responses,
                )
            )
            self.assertEqual("unresolved", wave_two_result["status"])

            wave_three_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_three_context["boundary_waves"] = [3]
            wave_three, wave_three_path = _run_prepare(
                root,
                authority=authority,
                context=wave_three_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=wave_two_envelope,
                prior_responses=cumulative_responses,
                prior_boundary_result=wave_two_result,
            )
            wave_three_payload = (
                json.loads(wave_three_path.read_text(encoding="utf-8"))
                if wave_three_path.exists()
                else {}
            )
            roster_ids = [
                assignment["slot_id"]
                for assignment in plan["audience_profile_rosters"][
                    "boundary_reserve"
                ]["assignments"]
            ]

        self.assertEqual(0, wave_three.returncode, wave_three.stderr)
        self.assertEqual(
            roster_ids,
            wave_three_payload["audience_allocation_subset"][
                "selected_slot_ids"
            ],
        )
        self.assertEqual(
            roster_ids[-3:],
            wave_three_payload["audience_dispatch"][
                "newly_authorized_slot_ids"
            ],
        )

    def test_wave_three_rejects_inexact_cumulative_response_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(
                root,
                boundary_jobs_per_wave=3,
                boundary_waves_max=3,
            )
            authority = _boundary_authority(plan)
            wave_one_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_one_context["boundary_waves"] = [1]
            wave_one, wave_one_path = _run_prepare(
                root,
                authority=authority,
                context=wave_one_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, wave_one.returncode, wave_one.stderr)
            wave_one_envelope = json.loads(
                wave_one_path.read_text(encoding="utf-8")
            )
            wave_one_responses, wave_one_result = (
                _boundary_continuation_evidence(
                    plan,
                    wave_one_envelope,
                )
            )
            wave_two_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_two_context["boundary_waves"] = [2]
            wave_two, wave_two_path = _run_prepare(
                root,
                authority=authority,
                context=wave_two_context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
                prior_jobs_envelope=wave_one_envelope,
                prior_responses=wave_one_responses,
                prior_boundary_result=wave_one_result,
            )
            self.assertEqual(0, wave_two.returncode, wave_two.stderr)
            wave_two_envelope = json.loads(
                wave_two_path.read_text(encoding="utf-8")
            )
            cumulative_responses, wave_two_result = (
                _boundary_continuation_evidence(
                    plan,
                    wave_two_envelope,
                    earlier_responses=wave_one_responses,
                )
            )
            future_id = plan["audience_profile_rosters"][
                "boundary_reserve"
            ]["assignments"][-1]["slot_id"]
            attacks = {
                "missing_earlier": copy.deepcopy(
                    cumulative_responses[1:]
                ),
                "duplicate_earlier": copy.deepcopy(
                    cumulative_responses
                    + [cumulative_responses[0]]
                ),
                "reordered": list(
                    reversed(copy.deepcopy(cumulative_responses))
                ),
                "substituted_earlier": copy.deepcopy(
                    cumulative_responses
                ),
                "future_wave": copy.deepcopy(cumulative_responses),
            }
            attacks["substituted_earlier"][0][
                "synthetic_replicate_id"
            ] = "substituted-earlier-wave"
            future = copy.deepcopy(cumulative_responses[-1])
            future["synthetic_replicate_id"] = future_id
            future["response_id"] = f"boundary_response-{future_id}"
            future["reviewer_dispatch_id"] = (
                f"dispatch-boundary_response-{future_id}"
            )
            future["audience_slot_id"] = future_id
            attacks["future_wave"].append(future)
            wave_three_context = _dispatch_context(
                plan,
                "boundary_response",
            )
            wave_three_context["boundary_waves"] = [3]
            wave_two_path.unlink()
            outcomes = {}
            for name, attacked_responses in attacks.items():
                outcome, output_path = _run_prepare(
                    root,
                    authority=authority,
                    context=wave_three_context,
                    manifest=plan,
                    resolution_path=resolution_path,
                    allow_directional=True,
                    prior_jobs_envelope=wave_two_envelope,
                    prior_responses=attacked_responses,
                    prior_boundary_result=wave_two_result,
                )
                outcomes[name] = (
                    outcome,
                    output_path.exists(),
                )

        for name, (outcome, created_output) in outcomes.items():
            with self.subTest(name=name):
                self.assertEqual(2, outcome.returncode)
                self.assertIn("cumulative", outcome.stderr.lower())
                self.assertFalse(created_output)

    def test_complete_cumulative_boundary_request_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1, 2]
            completed, output_path = _run_prepare(
                root,
                authority=_boundary_authority(plan),
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            created = output_path.exists()

        self.assertEqual(2, completed.returncode)
        self.assertIn("exactly one", completed.stderr)
        self.assertFalse(created)

    def test_jobs_envelope_validation_is_an_authorization_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1]
            completed, output_path = _run_prepare(
                root,
                authority=_boundary_authority(plan),
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            valid = json.loads(output_path.read_text(encoding="utf-8"))
            roster = plan["audience_profile_rosters"]["boundary_reserve"]
            selected = valid["audience_allocation_subset"][
                "selected_slot_ids"
            ]
            assignments = {
                item["slot_id"]: item for item in roster["assignments"]
            }
            profiles = {
                item["grounded_profile_id"]: item
                for item in plan["grounded_context_profiles"]
            }

            decision_jobs = copy.deepcopy(valid)
            decision_jobs["audience_allocation_subset"] = (
                evaluate_allocation_subset(
                    roster,
                    selected_slot_ids=selected,
                    allow_directional_allocation=False,
                )
            )
            decision_jobs["audience_run_claim"] = "requires_user_decision"

            outside_subset = copy.deepcopy(valid)
            outside_ids = [
                item["slot_id"] for item in roster["assignments"][4:]
            ]
            outside_subset["audience_dispatch"][
                "newly_authorized_slot_ids"
            ] = outside_ids
            for job, slot_id in zip(
                outside_subset["synthetic_replicate_jobs"],
                outside_ids,
                strict=True,
            ):
                assignment = assignments[slot_id]
                profile = profiles[assignment["grounded_profile_id"]]
                job.update(
                    {
                        "synthetic_replicate_id": slot_id,
                        "audience_slot_id": slot_id,
                        "grounded_profile_id": assignment[
                            "grounded_profile_id"
                        ],
                        "profile_snapshot_sha256": assignment[
                            "profile_snapshot_sha256"
                        ],
                        "segment_id": assignment["reported_segment_id"],
                        "profile_snapshot": profile["profile_snapshot"],
                        "context_stratum_id": profile[
                            "context_stratum_id"
                        ],
                    }
                )

            snapshot_tamper = copy.deepcopy(valid)
            snapshot_tamper["synthetic_replicate_jobs"][0][
                "profile_snapshot"
            ] = {"injected": True}

            context_tamper = copy.deepcopy(valid)
            context_tamper["synthetic_replicate_jobs"][0][
                "context_stratum_id"
            ] = "injected-context"

            segment_tamper = copy.deepcopy(valid)
            segment_tamper["synthetic_replicate_jobs"][0][
                "segment_id"
            ] = "injected-segment"

            profile_tamper = copy.deepcopy(valid)
            profile_tamper["synthetic_replicate_jobs"][0][
                "grounded_profile_id"
            ] = "injected-profile"

            hash_tamper = copy.deepcopy(valid)
            hash_tamper["synthetic_replicate_jobs"][0][
                "profile_snapshot_sha256"
            ] = "sha256:" + "0" * 64

            contract_tamper = copy.deepcopy(valid)
            contract_tamper["synthetic_replicate_jobs"][0][
                "reaction_prompts"
            ] = []

            worker_tamper = copy.deepcopy(valid)
            worker_tamper["synthetic_replicate_jobs"][0][
                "synthetic_replicate_id"
            ] = "forged-worker-slot"

            pair_tamper = copy.deepcopy(valid)
            pair_tamper["synthetic_replicate_jobs"][0][
                "pair_assignment_id"
            ] = "forged-pair"

            wave_tamper = copy.deepcopy(valid)
            wave_tamper["synthetic_replicate_jobs"][0][
                "boundary_wave"
            ] = 99

            coherent_identity_tamper = copy.deepcopy(valid)
            coherent_identity_job = coherent_identity_tamper[
                "synthetic_replicate_jobs"
            ][0]
            coherent_identity_job.update(
                {
                    "synthetic_replicate_id": "forged-worker-slot",
                    "response_id": (
                        "boundary_response-forged-worker-slot"
                    ),
                    "dispatch_id": (
                        "dispatch-boundary_response-forged-worker-slot"
                    ),
                    "pair_assignment_id": "forged-worker-slot",
                    "boundary_wave": 99,
                }
            )

            coherent_creative_tamper = copy.deepcopy(valid)
            coherent_creative_job = coherent_creative_tamper[
                "synthetic_replicate_jobs"
            ][0]
            coherent_creative_job.update(
                {
                    "variation_ids": ["creative-7", "creative-8"],
                    "assigned_variation_ids": [
                        "creative-7",
                        "creative-8",
                    ],
                    "shown_order": ["creative-8", "creative-7"],
                    "blind_labels": {
                        "creative-8": "A",
                        "creative-7": "B",
                    },
                    "reaction_prompts": [
                        "Review creative-8.",
                        "Review creative-7.",
                    ],
                }
            )

            duplicate_identity_tamper = copy.deepcopy(valid)
            for field in (
                "synthetic_replicate_id",
                "response_id",
                "dispatch_id",
            ):
                duplicate_identity_tamper[
                    "synthetic_replicate_jobs"
                ][1][field] = duplicate_identity_tamper[
                    "synthetic_replicate_jobs"
                ][0][field]

            for label, payload in (
                ("decision jobs", decision_jobs),
                ("outside subset", outside_subset),
                ("snapshot", snapshot_tamper),
                ("context", context_tamper),
                ("segment", segment_tamper),
                ("profile", profile_tamper),
                ("hash", hash_tamper),
                ("job contract", contract_tamper),
                ("worker slot", worker_tamper),
                ("pair assignment", pair_tamper),
                ("boundary wave", wave_tamper),
                ("coherent identity", coherent_identity_tamper),
                ("coherent creatives", coherent_creative_tamper),
                ("duplicate identity", duplicate_identity_tamper),
            ):
                with self.subTest(label=label):
                    with self.assertRaises(ValueError):
                        _validate_persisted_v3_jobs(
                            payload,
                            plan=roster,
                            authority=plan,
                            resolution_path=resolution_path,
                            dispatch_authority=_boundary_authority(plan),
                        )

    def test_pre_dispatch_cli_authenticates_whole_v3_envelope_and_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            authority = _boundary_authority(plan)
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1]
            prepared, jobs_path = _run_prepare(
                root,
                authority=authority,
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            expected_count = len(
                json.loads(jobs_path.read_text(encoding="utf-8"))[
                    "synthetic_replicate_jobs"
                ]
            )
            command = [
                sys.executable,
                str(SCRIPTS / "validate-panel-run.py"),
                str(jobs_path),
                "--manifest",
                str(root / "manifest.json"),
                "--audience-resolution",
                str(resolution_path),
                "--dispatch-authority",
                str(root / "dispatch-authority.json"),
                "--expected-count",
                str(expected_count),
            ]
            authenticated = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            unauthenticated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate-panel-run.py"),
                    str(jobs_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            valid = json.loads(jobs_path.read_text(encoding="utf-8"))
            mutations = (
                ("profile-snapshot", "profile_snapshot", {"injected": True}),
                ("context", "context_stratum_id", "injected-context"),
                ("segment", "segment_id", "injected-segment"),
                ("profile-id", "grounded_profile_id", "injected-profile"),
                (
                    "snapshot-hash",
                    "profile_snapshot_sha256",
                    "sha256:" + "0" * 64,
                ),
                ("reaction-core", "reaction_prompts", []),
                (
                    "worker-slot",
                    "synthetic_replicate_id",
                    "forged-worker-slot",
                ),
                ("pair", "pair_assignment_id", "forged-pair"),
                ("wave", "boundary_wave", 99),
                (
                    "creative-core",
                    "variation_ids",
                    ["creative-7", "creative-8"],
                ),
            )
            tampered_results = {}
            for label, field, value in mutations:
                tampered = copy.deepcopy(valid)
                tampered["synthetic_replicate_jobs"][0][field] = value
                tampered_path = root / f"tampered-{label}-jobs.json"
                tampered_path.write_text(
                    json.dumps(tampered, sort_keys=True),
                    encoding="utf-8",
                )
                tampered_results[label] = subprocess.run(
                    [*command[:2], str(tampered_path), *command[3:]],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

        self.assertEqual(0, authenticated.returncode, authenticated.stdout)
        self.assertIn("validation passed", authenticated.stdout.lower())
        self.assertEqual(1, unauthenticated.returncode)
        self.assertIn("require --manifest", unauthenticated.stdout)
        for label, tampered_result in tampered_results.items():
            with self.subTest(label=label):
                self.assertEqual(1, tampered_result.returncode)
        self.assertIn(
            "profile_snapshot_sha256",
            tampered_results["snapshot-hash"].stdout,
        )

    def test_persisted_jobs_match_positive_screening_boundary_and_finalist_cores(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            boundary_authority = _boundary_authority(plan)

            screening = enrich_assignment_jobs(
                plan,
                _dispatch_context(plan, "screening_response"),
                audience_resolution=resolution_path,
            )

            boundary_context = _dispatch_context(
                plan, "boundary_response"
            )
            boundary_context["boundary_waves"] = [1]
            boundary = enrich_assignment_jobs(
                boundary_authority,
                boundary_context,
                manifest=plan,
                audience_resolution=resolution_path,
                allow_directional_allocation=True,
            )

            manifest = _manifest_from_plan(plan)
            creative_ids = sorted(
                manifest["outputs"]["creative_asset_hashes"]
            )
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
            finalist_context = _dispatch_context(
                plan, "finalist_response"
            )
            finalist_context["requested_job_slots"] = 2
            finalists = enrich_assignment_jobs(
                approval,
                finalist_context,
                manifest=manifest,
                audience_resolution=resolution_path,
                allow_directional_allocation=True,
            )

            cases = (
                (
                    "screening",
                    screening,
                    plan["audience_profile_rosters"]["screening"],
                    plan,
                    plan,
                ),
                (
                    "boundary",
                    boundary,
                    plan["audience_profile_rosters"][
                        "boundary_reserve"
                    ],
                    plan,
                    boundary_authority,
                ),
                (
                    "finalist",
                    finalists,
                    plan["audience_profile_rosters"][
                        "finalist_reserve"
                    ],
                    manifest,
                    approval,
                ),
            )
            for (
                label,
                payload,
                roster,
                authority,
                dispatch_authority,
            ) in cases:
                with self.subTest(label=label):
                    self.assertEqual(
                        payload,
                        _validate_persisted_v3_jobs(
                            payload,
                            plan=roster,
                            authority=authority,
                            resolution_path=resolution_path,
                            dispatch_authority=dispatch_authority,
                        ),
                    )

            screening_tamper = copy.deepcopy(screening)
            screening_tamper["synthetic_replicate_jobs"][0][
                "inclusion_probability"
            ] += 0.01
            with self.assertRaises(ValueError):
                _validate_persisted_v3_jobs(
                    screening_tamper,
                    plan=plan["audience_profile_rosters"]["screening"],
                    authority=plan,
                    resolution_path=resolution_path,
                    dispatch_authority=plan,
                )
            with self.assertRaisesRegex(
                ValueError,
                "screening dispatch authority",
            ):
                _validate_persisted_v3_jobs(
                    screening,
                    plan=plan["audience_profile_rosters"]["screening"],
                    authority=plan,
                    resolution_path=resolution_path,
                    dispatch_authority={},
                )

            finalist_tamper = copy.deepcopy(finalists)
            finalist_job = finalist_tamper[
                "synthetic_replicate_jobs"
            ][0]
            finalist_job["shown_order"] = list(
                reversed(finalist_job["shown_order"])
            )
            finalist_job["blind_labels"] = {
                creative_id: chr(ord("A") + index)
                for index, creative_id in enumerate(
                    finalist_job["shown_order"]
                )
            }
            with self.assertRaises(ValueError):
                _validate_persisted_v3_jobs(
                    finalist_tamper,
                    plan=plan["audience_profile_rosters"][
                        "finalist_reserve"
                    ],
                    authority=manifest,
                    resolution_path=resolution_path,
                    dispatch_authority=approval,
                )

    def test_screening_rejects_every_conflicting_duplicate_v3_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, resolution_path = _v3_plan(Path(temporary))
            context = _dispatch_context(plan, "screening_response")
            mutations = {
                "study_id": lambda manifest: manifest.update(
                    study_id="other-study"
                ),
                "method": lambda manifest: manifest.update(
                    method="complete_exposure"
                ),
                "capacity": lambda manifest: manifest[
                    "synthetic_replicate_capacity"
                ].update(screening_planned=999),
                "assignment": lambda manifest: manifest["assignment"][
                    "synthetic_replicate_jobs"
                ][0].update(shown_order=["creative-4", "creative-3"]),
            }
            for label, mutate in mutations.items():
                manifest = copy.deepcopy(plan)
                mutate(manifest)
                with self.subTest(label=label), self.assertRaises(ValueError):
                    enrich_assignment_jobs(
                        plan,
                        context,
                        manifest=manifest,
                        audience_resolution=resolution_path,
                    )

    def test_v2_and_v3_use_one_behavior_neutral_job_shaper(self) -> None:
        source = (
            SCRIPTS / "audience_lab" / "dispatch.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(3, source.count("_shape_worker_jobs("))
        self.assertEqual(1, source.count("Blind creative {blind_labels"))

    def test_tier_one_subset_gates_only_missing_must_cover_coverage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root, bundle="tier_1")
            authority = _boundary_authority(plan)
            first_wave = _dispatch_context(plan, "boundary_response")
            first_wave["boundary_waves"] = [1]
            blocked, decision_path = _run_prepare(
                root,
                authority=authority,
                context=first_wave,
                manifest=plan,
                resolution_path=resolution_path,
            )
            decision = json.loads(
                decision_path.read_text(encoding="utf-8")
            )
            accepted_first, prior_path = _run_prepare(
                root,
                authority=authority,
                context=first_wave,
                manifest=plan,
                resolution_path=resolution_path,
                allow_directional=True,
            )
            self.assertEqual(
                0, accepted_first.returncode, accepted_first.stderr
            )
            prior = json.loads(prior_path.read_text(encoding="utf-8"))

            second_wave = _dispatch_context(plan, "boundary_response")
            second_wave["boundary_waves"] = [2]
            prior_responses, prior_boundary_result = (
                _boundary_continuation_evidence(plan, prior)
            )
            continued, jobs_path = _run_prepare(
                root,
                authority=authority,
                context=second_wave,
                manifest=plan,
                resolution_path=resolution_path,
                prior_jobs_envelope=prior,
                prior_responses=prior_responses,
                prior_boundary_result=prior_boundary_result,
            )
            jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

        self.assertEqual(6, blocked.returncode)
        self.assertEqual(
            "directional_profile_allocation",
            decision["fidelity"]["status"],
        )
        self.assertFalse(
            decision["fidelity"]["all_must_cover_groups_represented"]
        )
        self.assertEqual(0, continued.returncode, continued.stderr)
        self.assertEqual(
            "directional_profile_allocation",
            jobs["audience_allocation_subset"]["fidelity"]["status"],
        )
        self.assertTrue(
            jobs["audience_allocation_subset"]["fidelity"][
                "all_must_cover_groups_represented"
            ]
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            jobs["audience_run_claim"],
        )

    def test_non_prefix_boundary_selection_fails_before_subset_gating(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            authority = _boundary_authority(plan)
            requested = authority["boundary_plan"][
                "predeclared_pair_assignments"
            ]
            context = _dispatch_context(plan, "boundary_response")
            context["requested_boundary_assignments"] = [
                requested[0],
                requested[2],
            ]
            completed, output_path = _run_prepare(
                root,
                authority=authority,
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("complete frozen waves", completed.stderr)
        self.assertFalse(output_path.exists())

    def test_v3_jobs_bind_every_job_to_the_typed_envelope_study(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1]
            payload = enrich_assignment_jobs(
                _boundary_authority(plan),
                context,
                manifest=plan,
                audience_resolution=resolution_path,
                allow_directional_allocation=True,
            )
            tampered = copy.deepcopy(payload)
            tampered["synthetic_replicate_jobs"][0]["study_id"] = (
                "different-study"
            )

            with self.assertRaisesRegex(ValueError, "job.*study_id|study_id.*job"):
                validate_v3_jobs_envelope(
                    tampered,
                    allocation_plan=plan["audience_profile_rosters"][
                        "boundary_reserve"
                    ],
                    authority=plan,
                    audience_resolution=resolution_path,
                    dispatch_authority=_boundary_authority(plan),
                )

    def test_boundary_profile_tampering_fails_before_subset_decision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            authority = _boundary_authority(plan)
            authority["boundary_plan"]["predeclared_pair_assignments"][0][
                "grounded_profile_id"
            ] = "injected-profile"
            context = _dispatch_context(plan, "boundary_response")
            context["boundary_waves"] = [1]
            completed, output_path = _run_prepare(
                root,
                authority=authority,
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("grounded_profile_id", completed.stderr)
        self.assertFalse(output_path.exists())

    def test_complete_exposure_boundary_is_not_dispatchable_or_gated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(
                root,
                creative_count=5,
                maximum_synthetic_panelists=60,
            )
            context = _dispatch_context(plan, "boundary_response")
            stale_output = root / "jobs.json"
            stale_output.write_text(
                '{"forged":"stale-boundary-jobs"}\n',
                encoding="utf-8",
            )
            completed, output_path = _run_prepare(
                root,
                authority={
                    "study_id": plan["study_id"],
                    "method": plan["method"],
                    "boundary_plan": {
                        "plan_version": "predeclared-boundary-v1",
                        "frozen_before_dispatch": True,
                        "available_boundary_reserve": 1,
                        "predeclared_pair_assignments": [
                            {
                                "pair_assignment_id": "forged-boundary-slot",
                                "wave": 1,
                                "variation_ids": [
                                    "creative-1",
                                    "creative-2",
                                ],
                            }
                        ],
                    },
                },
                context=context,
                manifest=plan,
                resolution_path=resolution_path,
            )
            output_created = output_path.exists()

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertFalse(output_created)
        self.assertIn("dispatch_status=not_applicable", completed.stdout)
        self.assertIn("reason=method_complete_exposure", completed.stdout)

    def test_complete_exposure_rejects_an_allocation_plan_at_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            complete, resolution_path = _v3_plan(
                root / "complete",
                creative_count=5,
                maximum_synthetic_panelists=60,
            )
            partial, _ = _v3_plan(root / "partial")
            forged = copy.deepcopy(complete)
            forged["audience_profile_rosters"]["boundary_reserve"] = (
                copy.deepcopy(
                    partial["audience_profile_rosters"][
                        "boundary_reserve"
                    ]
                )
            )
            forged["audience_allocation_fidelity"]["boundary_reserve"] = (
                copy.deepcopy(
                    forged["audience_profile_rosters"][
                        "boundary_reserve"
                    ]["fidelity"]
                )
            )
            completed, output_path = _run_prepare(
                root,
                authority={
                    "study_id": forged["study_id"],
                    "method": forged["method"],
                    "boundary_plan": {
                        "plan_version": "predeclared-boundary-v1",
                        "frozen_before_dispatch": True,
                        "available_boundary_reserve": 0,
                        "predeclared_pair_assignments": [],
                    },
                },
                context=_dispatch_context(
                    forged, "boundary_response"
                ),
                manifest=forged,
                resolution_path=resolution_path,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("complete_exposure", completed.stderr)
        self.assertFalse(output_path.exists())

    def test_non_prefix_finalist_slot_selection_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, resolution_path = _v3_plan(root)
            manifest = _manifest_from_plan(plan)
            creative_ids = sorted(
                manifest["outputs"]["creative_asset_hashes"]
            )
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
            context = _dispatch_context(plan, "finalist_response")
            roster_ids = [
                item["slot_id"]
                for item in plan["audience_profile_rosters"][
                    "finalist_reserve"
                ]["assignments"]
            ]
            context.update(
                {
                    "requested_job_slots": 2,
                    "requested_finalist_slot_ids": [
                        roster_ids[0],
                        roster_ids[2],
                    ],
                }
            )
            completed, output_path = _run_prepare(
                root,
                authority=approval,
                context=context,
                manifest=manifest,
                resolution_path=resolution_path,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn("prefix", completed.stderr.lower())
        self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
