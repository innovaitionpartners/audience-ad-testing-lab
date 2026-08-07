from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURE = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-package-v3"
    / "approved-package-inputs.json"
)
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_allocation import (  # noqa: E402
    ALLOCATION_REQUEST_VERSION,
    allocate_stage_profiles,
    evaluate_allocation_subset,
    validate_allocation_plan,
)
from audience_lab.audience_package_v3 import build_audience_package_v3  # noqa: E402
from audience_lab.audience_research_v3 import _v2_projection  # noqa: E402
from audience_lab.audience_resolution_v3 import resolve_audience_v3  # noqa: E402
from audience_lab.contracts import (  # noqa: E402
    validate_boundary_profile_attachments,
    validate_manifest,
)
from conformance.test_task9_integration import complete_manifest  # noqa: E402


ROSTER_KEYS = {
    "schema_version",
    "envelope_sha256",
    "screening",
    "boundary_reserve",
    "finalist_reserve",
    "combined_sha256",
}
V3_FIELDS = {
    "audience_profile_rosters",
    "audience_allocation_fidelity",
    "audience_run_claim",
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


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def bare_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class V3ProfileRosterTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _materialize_inputs(
        self,
        root: Path,
        *,
        bundle: str,
        documents: dict[str, object] | None = None,
    ) -> dict[str, Path]:
        root.mkdir(parents=True)
        source = (
            self.fixture["bundles"][bundle]
            if documents is None
            else documents
        )
        paths: dict[str, Path] = {}
        for key, filename in self.fixture["inputs"].items():
            path = root / filename
            value = source[key]
            path.write_bytes(
                value.encode("utf-8")
                if key == "report"
                else canonical_bytes(value)
            )
            paths[key] = path
        return paths

    def _resolved_run(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
        documents: dict[str, object] | None = None,
    ) -> tuple[Path, Path, Path]:
        built = build_audience_package_v3(
            inputs=self._materialize_inputs(
                root / "inputs",
                bundle=bundle,
                documents=documents,
            ),
            output_dir=root / "package",
        )
        source = (
            self.fixture["bundles"][bundle]
            if documents is None
            else documents
        )
        panel_scope = source["panel"]["audience_scope"]
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
        run = root / "run"
        resolve_audience_v3(
            package_path=built.package_zip_path,
            study_scope=scope,
            run_directory=run,
        )
        return built.package_zip_path, run, run / "audience" / "resolution.json"

    def _plan(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
        documents: dict[str, object] | None = None,
        mutate_envelope=None,
        allow_directional: bool = False,
        maximum_synthetic_panelists: int = 40,
        creative_count: int = 7,
        requested_shortlist_size: int | None = None,
        stale_output: dict[str, object] | None = None,
        boundary_jobs_per_wave: int = 4,
        boundary_waves_max: int = 2,
        finalist_reserved: int = 4,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        package, run, resolution_path = self._resolved_run(
            root,
            bundle=bundle,
            documents=documents,
        )
        if mutate_envelope is not None:
            envelope = json.loads(resolution_path.read_text(encoding="utf-8"))
            mutate_envelope(envelope)
            resolution_path.write_bytes(canonical_bytes(envelope))
        request = {
            "study_id": f"v3-{bundle}-roster-study",
            "creative_ids": [
                f"creative-{index}"
                for index in range(1, creative_count + 1)
            ],
            "creative_format": "static_image",
            "requested_shortlist_size": (
                min(5, creative_count)
                if requested_shortlist_size is None
                else requested_shortlist_size
            ),
            "maximum_synthetic_panelists": maximum_synthetic_panelists,
            "audience_panel": {
                "source": "file",
                "package_path": str(package),
            },
        }
        request_path = root / "study-request.json"
        output_path = run / "study-plan.json"
        if stale_output is not None:
            output_path.write_bytes(canonical_bytes(stale_output))
        request_path.write_bytes(canonical_bytes(request))
        command = [
            sys.executable,
            str(SCRIPTS / "plan-large-library.py"),
            str(request_path),
            str(output_path),
            "--burden-pilot",
            "passed",
            "--reported-segments",
            "1",
            "--boundary-jobs-per-wave",
            str(boundary_jobs_per_wave),
            "--boundary-waves-max",
            str(boundary_waves_max),
            "--finalist-reserved",
            str(finalist_reserved),
            "--assignment-seed",
            "29",
            "--audience-resolution",
            str(resolution_path),
        ]
        if allow_directional:
            command.append("--allow-directional-allocation")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        return completed, output_path, resolution_path

    def _valid_plan(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
        documents: dict[str, object] | None = None,
        **plan_options,
    ) -> tuple[dict[str, object], Path]:
        completed, output, resolution = self._plan(
            root,
            bundle=bundle,
            documents=documents,
            **plan_options,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(output.read_text(encoding="utf-8")), resolution

    def _reseal_chain(self, documents: dict[str, object]) -> None:
        brief = documents["brief"]
        panel = documents["panel"]
        frame = documents["population_frame"]
        composition = documents["composition"]
        validity = documents["validity"]
        workflow = documents["workflow_state"]
        audit = documents["audit"]
        v2_brief = bare_sha256(_v2_projection(brief, brief=True))
        v2_panel = bare_sha256(_v2_projection(panel, brief=False))
        frame_digest = canonical_sha256(frame)
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
        composition_digest = canonical_sha256(composition)
        panel["composition_plan_sha256"] = composition_digest
        validity["source_bindings"] = {
            "brief_sha256": "sha256:" + v2_brief,
            "panel_sha256": "sha256:" + v2_panel,
            "frame_result_sha256": frame_digest,
            "frame_sha256": usable_frame,
            "composition_sha256": composition_digest,
        }
        panel["validity_profile_sha256"] = canonical_sha256(validity)
        for approval in brief["scoped_approvals"]:
            if approval["scope"] == "evidence-synthesis":
                approval["target_sha256"] = "sha256:" + v2_brief
            elif approval["scope"] == "panel-construction":
                approval["target_sha256"] = "sha256:" + v2_panel
        audit["input_bindings"].update(
            brief_sha256=v2_brief,
            panel_sha256=v2_panel,
            population_frame_result_sha256=bare_sha256(frame),
            population_frame_sha256=(
                bare_sha256(frame) if usable_frame is not None else None
            ),
            composition_plan_sha256=bare_sha256(composition),
            validity_profile_sha256=bare_sha256(validity),
        )
        audit_digest = bare_sha256(audit)
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
            workflow_state_sha256=bare_sha256(workflow),
            frame_sha256=(
                None if usable_frame is None else bare_sha256(frame)
            ),
            composition_sha256=bare_sha256(composition),
            validity_sha256=bare_sha256(validity),
            source_inventory_sha256=bare_sha256(
                documents["source_inventory"]
            ),
            verbatim_inventory_sha256=bare_sha256(
                documents["verbatim_inventory"]
            ),
        )
        report_manifest = documents["report_manifest"]
        report_manifest.update(
            panel_id=panel["panel_id"],
            panel_version=panel["version"],
            report_inputs_sha256=bare_sha256(report_inputs),
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

    def _distorted_tier_two_documents(self) -> dict[str, object]:
        documents = copy.deepcopy(self.fixture["bundles"]["tier_2"])
        frame = documents["population_frame"]
        for cell in frame["cells"]:
            if cell["cell_id"] == "midmarket-operations":
                cell["structural_weight"] = 0.99
                cell["uncertainty"] = {"lower": 0.98, "upper": 1.0}
            else:
                cell["structural_weight"] = 0.01
                cell["uncertainty"] = {"lower": 0.0, "upper": 0.02}
        frame["modeled_weight_share"] = 0.01
        for item in frame["modeled_weight_by_dimension"]:
            item["share"] = 0.01
        composition = documents["composition"]
        composition["modeled_cell_share"] = 0.01
        group_weights = {
            "midmarket-group": 0.99,
            "enterprise-group": 0.01,
        }
        for group in composition["structural_groups"]:
            group["structural_weight"] = group_weights[
                group["structural_group_id"]
            ]
        for profile in composition["profiles"]:
            profile["effective_profile_allocation"] = (
                group_weights[profile["structural_group_id"]]
                * profile["conditional_overlay_allocation"]
            )
        self._reseal_chain(documents)
        return documents

    def _tier_one_over_capacity_documents(self) -> dict[str, object]:
        documents = copy.deepcopy(self.fixture["bundles"]["tier_1"])
        panel = documents["panel"]
        composition = documents["composition"]
        base_profile = panel["grounded_context_profiles"][0]
        base_group = composition["structural_groups"][0]
        base_composition_profile = composition["profiles"][0]
        profile_ids = [f"must-cover-profile-{index:02d}" for index in range(17)]
        panel["grounded_context_profiles"] = []
        composition["structural_groups"] = []
        composition["profiles"] = []
        composition["unsupported_combinations"] = []
        for index, profile_id in enumerate(profile_ids):
            group_id = f"must-cover-group-{index:02d}"
            saved = copy.deepcopy(base_profile)
            saved["grounded_profile_id"] = profile_id
            panel["grounded_context_profiles"].append(saved)
            group = copy.deepcopy(base_group)
            group.update(
                structural_group_id=group_id,
                structural_weight=1 / 17,
                must_cover=True,
            )
            composition["structural_groups"].append(group)
            planned = copy.deepcopy(base_composition_profile)
            planned.update(
                profile_id=profile_id,
                structural_group_id=group_id,
                conditional_overlay_allocation=1.0,
                effective_profile_allocation=1 / 17,
            )
            composition["profiles"].append(planned)
        for check in documents["audit"]["checks"]:
            check["profile_ids"] = profile_ids
        self._reseal_chain(documents)
        return documents

    def _manifest_from_plan(self, plan: dict[str, object]) -> dict[str, object]:
        manifest = complete_manifest()
        manifest.update(
            {
                "study_id": plan["study_id"],
                "creative_format": plan["creative_format"],
                "method": plan["method"],
                "requested_shortlist_size": plan["requested_shortlist_size"],
                "maximum_synthetic_panelists": plan[
                    "synthetic_replicate_capacity"
                ]["ceiling"],
                "synthetic_replicate_capacity": plan[
                    "synthetic_replicate_capacity"
                ],
                "assignment": plan["assignment"],
                "audience_lock": plan["audience_lock"],
                "audience_package": plan["audience_package"],
                "grounded_context_profiles": plan[
                    "grounded_context_profiles"
                ],
                "audience_profile_rosters": plan[
                    "audience_profile_rosters"
                ],
                "audience_allocation_fidelity": plan[
                    "audience_allocation_fidelity"
                ],
                "audience_run_claim": plan["audience_run_claim"],
            }
        )
        return manifest

    def _reseal_manifest_rosters(
        self,
        manifest: dict[str, object],
    ) -> None:
        rosters = manifest["audience_profile_rosters"]
        combined_input = self._roster_combined_input(manifest)
        rosters["combined_sha256"] = canonical_sha256(combined_input)
        manifest["audience_allocation_fidelity"]["boundary_reserve"] = (
            copy.deepcopy(rosters["boundary_reserve"]["fidelity"])
        )

    def _roster_combined_input(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        rosters = payload["audience_profile_rosters"]
        return {
            "schema_version": rosters["schema_version"],
            "study_id": payload["study_id"],
            "method": payload["method"],
            "maximum_synthetic_panelists": payload[
                "maximum_synthetic_panelists"
            ],
            "synthetic_replicate_capacity": payload[
                "synthetic_replicate_capacity"
            ],
            "assignment_sha256": canonical_sha256(payload["assignment"]),
            "envelope_sha256": rosters["envelope_sha256"],
            "screening": rosters["screening"],
            "boundary_reserve": rosters["boundary_reserve"],
            "finalist_reserve": rosters["finalist_reserve"],
        }

    def _allocation_profiles(
        self,
        grounded_profiles: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [
            {
                "grounded_profile_id": profile["grounded_profile_id"],
                "reported_segment_id": profile["reported_segment_id"],
                "structural_group_id": profile["structural_group_id"],
                "effective_weight": profile["effective_weight"],
                "conditional_effective_weight": profile["effective_weight"],
                "must_cover_group_ids": copy.deepcopy(
                    profile["must_cover_group_ids"]
                ),
                "profile_snapshot_sha256": profile[
                    "profile_snapshot_sha256"
                ],
                "eligible": profile["eligible"],
            }
            for profile in grounded_profiles
        ]

    def _replace_rosters(
        self,
        plan: dict[str, object],
        *,
        screening_slots: list[dict[str, object]] | None = None,
    ) -> None:
        rosters = plan["audience_profile_rosters"]
        profiles = self._allocation_profiles(
            plan["grounded_context_profiles"]
        )
        segment_ids = sorted(plan["audience_lock"]["segment_weights"])
        capacity = plan["synthetic_replicate_capacity"]
        screening_slots = (
            [
                {
                    "slot_id": job["synthetic_replicate_id"],
                    "reported_segment_id": job["segment_id"],
                }
                for job in plan["assignment"]["synthetic_replicate_jobs"]
            ]
            if screening_slots is None
            else screening_slots
        )
        boundary_slots = [
            {
                "slot_id": (
                    f"boundary-wave-{wave:02d}-job-{position:04d}"
                ),
                "reported_segment_id": segment_ids[
                    index % len(segment_ids)
                ],
            }
            for index, (wave, position) in enumerate(
                (
                    (wave, position)
                    for wave in range(
                        1,
                        capacity["boundary_waves_max"] + 1,
                    )
                    for position in range(
                        1,
                        capacity["boundary_jobs_per_wave"] + 1,
                    )
                )
            )
        ]
        finalist_slots = [
            {
                "slot_id": f"finalist-{index:04d}",
                "reported_segment_id": None,
            }
            for index in range(1, capacity["finalist_reserved"] + 1)
        ]
        must_cover = sorted(
            {
                group_id
                for profile in profiles
                for group_id in profile["must_cover_group_ids"]
            }
        )

        def allocate(
            stage: str,
            prior: dict[str, object],
            slots: list[dict[str, object]],
        ) -> dict[str, object]:
            return allocate_stage_profiles(
                {
                    "schema_version": ALLOCATION_REQUEST_VERSION,
                    "stage": stage,
                    "stage_roster_id": prior["stage_roster_id"],
                    "stable_seed": prior["stable_seed"],
                    "allocation_basis": prior["fidelity"][
                        "allocation_basis"
                    ],
                    "slots": slots,
                    "profiles": profiles,
                    "analysis_weights": (
                        {}
                        if stage == "finalist"
                        else plan["audience_lock"]["segment_weights"]
                    ),
                    "must_cover_group_ids": must_cover,
                    "maximum_absolute_deviation": 0.05,
                    "allow_directional_allocation": False,
                }
            )

        screening = allocate(
            "screening",
            rosters["screening"],
            screening_slots,
        )
        boundary = allocate(
            "boundary",
            rosters["boundary_reserve"],
            boundary_slots,
        )
        finalist = allocate(
            "finalist",
            rosters["finalist_reserve"],
            finalist_slots,
        )
        roster_core = {
            "schema_version": rosters["schema_version"],
            "envelope_sha256": rosters["envelope_sha256"],
            "screening": screening,
            "boundary_reserve": boundary,
            "finalist_reserve": finalist,
        }
        plan["audience_profile_rosters"] = {
            **roster_core,
            "combined_sha256": "",
        }
        plan["audience_profile_rosters"]["combined_sha256"] = canonical_sha256(
            self._roster_combined_input(plan)
        )
        plan["audience_allocation_fidelity"] = {
            stage: plan["audience_profile_rosters"][stage]["fidelity"]
            for stage in (
                "screening",
                "boundary_reserve",
                "finalist_reserve",
            )
        }
        plan["audience_run_claim"] = screening["claim_effect"]

    def _multi_segment_plan(
        self,
        plan: dict[str, object],
    ) -> dict[str, object]:
        result = copy.deepcopy(plan)
        profiles = result["grounded_context_profiles"]
        for profile in profiles[:2]:
            profile["reported_segment_id"] = "segment-a"
        profiles[2]["reported_segment_id"] = "segment-b"
        lock = result["audience_lock"]
        lock["segment_weights"] = {"segment-a": 0.5, "segment-b": 0.5}
        lock["segment_names"] = {
            "segment-a": "Segment A",
            "segment-b": "Segment B",
        }
        original_stratum = result["assignment"]["context_strata"][0]
        result["assignment"]["context_strata"] = []
        for segment_id in ("segment-a", "segment-b"):
            stratum = copy.deepcopy(original_stratum)
            stratum["segment_id"] = segment_id
            stratum["context_stratum_id"] = f"context-{segment_id[-1]}"
            result["assignment"]["context_strata"].append(stratum)
        jobs = result["assignment"]["synthetic_replicate_jobs"]
        for index, job in enumerate(jobs):
            suffix = "a" if index < len(jobs) // 2 else "b"
            job["segment_id"] = f"segment-{suffix}"
            job["context_stratum_id"] = f"context-{suffix}"
        result["reported_segment_ids"] = ["segment-a", "segment-b"]
        result["reported_segments"] = 2
        self._replace_rosters(result)
        return result

    def test_v3_plan_freezes_exact_screening_boundary_and_finalist_rosters(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, resolution_path = self._valid_plan(Path(temporary))

            self.assertEqual(
                {
                    "assignment",
                    "audience_allocation_fidelity",
                    "audience_lock",
                    "audience_package",
                    "audience_profile_rosters",
                    "audience_run_claim",
                    "burden_pilot_status",
                    "creative_count",
                    "creative_format",
                    "grounded_context_profiles",
                    "method",
                    "maximum_synthetic_panelists",
                    "reported_segment_ids",
                    "reported_segments",
                    "requested_shortlist_size",
                    "study_id",
                    "synthetic_replicate_capacity",
                },
                set(plan),
            )
            self.assertEqual(
                {
                    "screening_planned": 16,
                    "boundary_reserved": 8,
                    "finalist_reserved": 4,
                    "required_total": 28,
                    "ceiling": 40,
                    "ceiling_satisfied": True,
                    "boundary_jobs_per_wave": 4,
                    "boundary_waves_max": 2,
                    "shortfall": 0,
                },
                plan["synthetic_replicate_capacity"],
            )
            jobs = plan["assignment"]["synthetic_replicate_jobs"]
            self.assertEqual(16, len(jobs))
            self.assertEqual(
                [
                    f"operations-leaders-replicate-{index:04d}"
                    for index in range(1, 17)
                ],
                [job["synthetic_replicate_id"] for job in jobs],
            )
            self.assertEqual(
                {"operations-leaders"},
                {job["segment_id"] for job in jobs},
            )
            self.assertEqual(
                {"active-evaluation"},
                {job["context_stratum_id"] for job in jobs},
            )

            rosters = plan["audience_profile_rosters"]
            self.assertEqual(ROSTER_KEYS, set(rosters))
            self.assertEqual("audience-profile-rosters-v1", rosters["schema_version"])
            self.assertEqual(
                "sha256:" + hashlib.sha256(resolution_path.read_bytes()).hexdigest(),
                rosters["envelope_sha256"],
            )
            for stage in ("screening", "boundary_reserve", "finalist_reserve"):
                validate_allocation_plan(rosters[stage])

            self.assertEqual(
                [job["synthetic_replicate_id"] for job in jobs],
                [
                    assignment["slot_id"]
                    for assignment in rosters["screening"]["assignments"]
                ],
            )
            self.assertEqual(
                [
                    f"boundary-wave-{wave:02d}-job-{position:04d}"
                    for wave in range(1, 3)
                    for position in range(1, 5)
                ],
                [
                    assignment["slot_id"]
                    for assignment in rosters["boundary_reserve"]["assignments"]
                ],
            )
            self.assertEqual(
                [f"finalist-{index:04d}" for index in range(1, 5)],
                [
                    assignment["slot_id"]
                    for assignment in rosters["finalist_reserve"]["assignments"]
                ],
            )
            known_profiles = {
                item["grounded_profile_id"]
                for item in plan["grounded_context_profiles"]
            }
            for stage in ("screening", "boundary_reserve", "finalist_reserve"):
                self.assertTrue(
                    {
                        assignment["grounded_profile_id"]
                        for assignment in rosters[stage]["assignments"]
                    }
                    <= known_profiles
                )
            self.assertEqual(
                {"operations-leaders"},
                {
                    assignment["reported_segment_id"]
                    for assignment in rosters["finalist_reserve"]["assignments"]
                },
            )
            combined_input = self._roster_combined_input(plan)
            self.assertEqual(
                canonical_sha256(combined_input),
                rosters["combined_sha256"],
            )
            self.assertEqual(
                {
                    stage: rosters[stage]["fidelity"]
                    for stage in (
                        "screening",
                        "boundary_reserve",
                        "finalist_reserve",
                    )
                },
                plan["audience_allocation_fidelity"],
            )
            self.assertEqual(
                rosters["screening"]["claim_effect"],
                plan["audience_run_claim"],
            )

    def test_complete_exposure_preserves_zero_boundary_capacity_with_strict_not_applicable_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(
                Path(temporary),
                creative_count=5,
                maximum_synthetic_panelists=60,
            )

        self.assertEqual("complete_exposure", plan["method"])
        self.assertEqual(
            {
                "screening_planned": 40,
                "boundary_reserved": 0,
                "finalist_reserved": 4,
                "required_total": 44,
                "ceiling": 60,
                "ceiling_satisfied": True,
                "boundary_jobs_per_wave": 0,
                "boundary_waves_max": 0,
                "shortfall": 0,
            },
            plan["synthetic_replicate_capacity"],
        )
        jobs = plan["assignment"]["synthetic_replicate_jobs"]
        self.assertEqual(
            [
                f"operations-leaders-complete-replicate-{index:04d}"
                for index in range(1, 41)
            ],
            [job["synthetic_replicate_id"] for job in jobs],
        )
        self.assertTrue(
            all(
                job["variation_ids"]
                == [
                    "creative-1",
                    "creative-2",
                    "creative-3",
                    "creative-4",
                    "creative-5",
                ]
                for job in jobs
            )
        )
        dynamic = plan["dynamic_complete_exposure_capacity"]
        self.assertEqual(40, dynamic["core_planned_executions"])
        self.assertEqual(12, dynamic["screening_reserved"])
        self.assertEqual(5, min(
            row["minimum_usable_records"]
            for row in dynamic["core_allocation_by_profile"]
        ))
        self.assertEqual(0, dynamic["count_semantics"]["human_respondents"])
        rosters = plan["audience_profile_rosters"]
        validate_allocation_plan(rosters["screening"])
        validate_allocation_plan(rosters["finalist_reserve"])
        self.assertEqual(
            {
                "schema_version": (
                    "audience-profile-allocation-not-applicable-v1"
                ),
                "stage": "boundary",
                "stage_roster_id": (
                    "v3-tier_2-roster-study:boundary-reserve"
                ),
                "status": "not_applicable",
                "reason": "method_complete_exposure",
                "assignments": [],
                "fidelity": {
                    "status": "not_applicable",
                    "allocation_basis": "structural_frame",
                },
            },
            rosters["boundary_reserve"],
        )
        combined_input = self._roster_combined_input(plan)
        self.assertEqual(
            canonical_sha256(combined_input),
            rosters["combined_sha256"],
        )
        self.assertEqual(
            rosters["boundary_reserve"]["fidelity"],
            plan["audience_allocation_fidelity"]["boundary_reserve"],
        )
        self.assertNotIn("boundary_subset", plan)
        self.assertNotIn("boundary_decision", plan)
        self.assertFalse(rosters["boundary_reserve"]["assignments"])
        manifest = self._manifest_from_plan(plan)
        self.assertEqual([], validate_manifest(manifest))

    def test_complete_exposure_boundary_not_applicable_record_fails_closed_on_coherent_tampering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as complete_directory:
            complete_plan, _resolution = self._valid_plan(
                Path(complete_directory),
                creative_count=5,
                maximum_synthetic_panelists=60,
            )
        with tempfile.TemporaryDirectory() as partial_directory:
            partial_plan, _resolution = self._valid_plan(
                Path(partial_directory),
            )
        complete = self._manifest_from_plan(complete_plan)
        partial = self._manifest_from_plan(partial_plan)
        self.assertEqual([], validate_manifest(complete))
        self.assertEqual([], validate_manifest(partial))

        mutations: list[tuple[str, dict[str, object]]] = []
        replacement = copy.deepcopy(complete)
        replacement["audience_profile_rosters"]["boundary_reserve"] = (
            copy.deepcopy(
                partial["audience_profile_rosters"]["boundary_reserve"]
            )
        )
        mutations.append(("allocation_plan_replacement", replacement))
        fake_assignment = copy.deepcopy(complete)
        fake_assignment["audience_profile_rosters"]["boundary_reserve"][
            "assignments"
        ].append(
            {
                "slot_id": "boundary-wave-01-job-0001",
                "grounded_profile_id": "forged",
            }
        )
        mutations.append(("fake_assignment", fake_assignment))
        for field, value in (
            ("schema_version", "audience-profile-allocation-plan-v1"),
            ("stage", "finalist"),
            ("stage_roster_id", "forged:boundary-reserve"),
            ("status", "frame_aligned"),
            ("reason", "method_partial_exposure"),
        ):
            tampered = copy.deepcopy(complete)
            tampered["audience_profile_rosters"]["boundary_reserve"][
                field
            ] = value
            mutations.append((field, tampered))
        fidelity_status = copy.deepcopy(complete)
        fidelity_status["audience_profile_rosters"]["boundary_reserve"][
            "fidelity"
        ]["status"] = "frame_aligned"
        mutations.append(("fidelity_status", fidelity_status))
        fidelity_basis = copy.deepcopy(complete)
        fidelity_basis["audience_profile_rosters"]["boundary_reserve"][
            "fidelity"
        ]["allocation_basis"] = "directional_planning"
        mutations.append(("fidelity_basis", fidelity_basis))
        nonzero_capacity = copy.deepcopy(complete)
        nonzero_capacity["synthetic_replicate_capacity"].update(
            {
                "boundary_reserved": 1,
                "boundary_jobs_per_wave": 1,
                "boundary_waves_max": 1,
                "required_total": 14,
            }
        )
        mutations.append(("nonzero_boundary_capacity", nonzero_capacity))
        boolean_zero_capacity = copy.deepcopy(complete)
        boolean_zero_capacity["synthetic_replicate_capacity"].update(
            {
                "boundary_reserved": False,
                "boundary_jobs_per_wave": False,
                "boundary_waves_max": False,
            }
        )
        mutations.append(
            ("boolean_zero_boundary_capacity", boolean_zero_capacity)
        )
        for field in (
            "boundary_reserved",
            "boundary_jobs_per_wave",
            "boundary_waves_max",
        ):
            for sign, value in (
                ("positive", 0.0),
                ("negative", -0.0),
            ):
                floating_zero_capacity = copy.deepcopy(complete)
                floating_zero_capacity["synthetic_replicate_capacity"][
                    field
                ] = value
                mutations.append(
                    (
                        f"{field}_{sign}_floating_zero",
                        floating_zero_capacity,
                    )
                )
        missing_capacity = copy.deepcopy(complete)
        missing_capacity["synthetic_replicate_capacity"] = None
        mutations.append(("missing_boundary_capacity", missing_capacity))
        method_swap = copy.deepcopy(complete)
        method_swap["method"] = "partial_exposure_maxdiff"
        mutations.append(("method_swap", method_swap))

        partial_with_sentinel = copy.deepcopy(partial)
        partial_with_sentinel["audience_profile_rosters"][
            "boundary_reserve"
        ] = copy.deepcopy(
            complete["audience_profile_rosters"]["boundary_reserve"]
        )
        mutations.append(("partial_with_sentinel", partial_with_sentinel))
        partial_zero_capacity = copy.deepcopy(partial)
        partial_zero_capacity["synthetic_replicate_capacity"].update(
            {
                "boundary_reserved": 0,
                "boundary_jobs_per_wave": 0,
                "boundary_waves_max": 0,
                "required_total": 20,
            }
        )
        mutations.append(("partial_zero_capacity", partial_zero_capacity))

        for name, tampered in mutations:
            with self.subTest(name=name):
                self._reseal_manifest_rosters(tampered)
                self.assertTrue(validate_manifest(tampered))

    def test_approved_tier_one_plans_reusable_directional_rosters_without_gate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(
                Path(temporary),
                bundle="tier_1",
            )

        self.assertEqual(
            "directional_profile_allocation",
            plan["audience_profile_rosters"]["screening"]["fidelity"]["status"],
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            plan["audience_run_claim"],
        )

    def test_screening_distortion_exits_six_until_directional_use_is_accepted(
        self,
    ) -> None:
        documents = self._distorted_tier_two_documents()

        with tempfile.TemporaryDirectory() as first_directory:
            blocked, blocked_output, _resolution = self._plan(
                Path(first_directory),
                documents=documents,
            )
            self.assertEqual(6, blocked.returncode, blocked.stderr)
            decision = json.loads(blocked_output.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as second_directory:
            continued, continued_output, _resolution = self._plan(
                Path(second_directory),
                documents=documents,
                allow_directional=True,
            )
            self.assertEqual(0, continued.returncode, continued.stderr)
            plan = json.loads(continued_output.read_text(encoding="utf-8"))

        self.assertEqual(
            "audience-profile-allocation-plan-v1",
            decision["schema_version"],
        )
        self.assertEqual("requires_user_decision", decision["claim_effect"])
        self.assertEqual("allocation_distorted", decision["fidelity"]["status"])
        self.assertEqual(
            decision["assignments"],
            plan["audience_profile_rosters"]["screening"]["assignments"],
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            plan["audience_run_claim"],
        )

    def test_tier_one_requires_decision_only_when_must_cover_is_omitted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            blocked, output, _resolution = self._plan(
                Path(temporary),
                bundle="tier_1",
                documents=self._tier_one_over_capacity_documents(),
            )
            decision = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(6, blocked.returncode, blocked.stderr)
        self.assertEqual(
            "directional_profile_allocation",
            decision["fidelity"]["status"],
        )
        self.assertEqual("requires_user_decision", decision["claim_effect"])
        self.assertEqual(
            1,
            len(
                decision["must_cover_diagnostics"][
                    "uncovered_group_ids"
                ]
            ),
        )
        self.assertTrue(
            decision["must_cover_diagnostics"]["uncovered_group_ids"][0]
            .startswith("must-cover-group-")
        )

    def test_planner_rejects_any_mutation_of_the_immutable_v3_run_unit(
        self,
    ) -> None:
        mutations = {
            "envelope_profile": lambda envelope, run: envelope[
                "grounded_context_profiles"
            ][0].__setitem__("eligible", False),
            "envelope_member_hash": lambda envelope, run: envelope["snapshot"][
                "members"
            ][0].__setitem__("sha256", "0" * 64),
            "snapshot_member": lambda envelope, run: (
                run
                / "audience"
                / "snapshot"
                / "saved-audience-panel.json"
            ).write_bytes(b"{}\n"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package, run, resolution_path = self._resolved_run(root)
                envelope = json.loads(
                    resolution_path.read_text(encoding="utf-8")
                )
                mutate(envelope, run)
                if name != "snapshot_member":
                    resolution_path.write_bytes(canonical_bytes(envelope))
                request = {
                    "study_id": f"immutable-{name}",
                    "creative_ids": [
                        f"creative-{index}" for index in range(1, 8)
                    ],
                    "creative_format": "static_image",
                    "requested_shortlist_size": 5,
                    "maximum_synthetic_panelists": 40,
                    "audience_panel": {
                        "source": "file",
                        "package_path": str(package),
                    },
                }
                request_path = root / "study-request.json"
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
                        "1",
                        "--boundary-jobs-per-wave",
                        "4",
                        "--boundary-waves-max",
                        "2",
                        "--finalist-reserved",
                        "4",
                        "--audience-resolution",
                        str(resolution_path),
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(2, completed.returncode, completed.stderr)
                self.assertFalse(output_path.exists())

    def test_boundary_and_finalist_prefixes_keep_frozen_profiles_and_exclude_suffix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        rosters = plan["audience_profile_rosters"]

        for stage, prefix_length in (
            ("boundary_reserve", 4),
            ("finalist_reserve", 2),
        ):
            with self.subTest(stage=stage):
                frozen = rosters[stage]
                selected_ids = [
                    item["slot_id"] for item in frozen["assignments"][:prefix_length]
                ]
                subset = evaluate_allocation_subset(
                    frozen,
                    selected_slot_ids=selected_ids,
                    allow_directional_allocation=False,
                )
                self.assertEqual(selected_ids, subset["selected_slot_ids"])
                self.assertEqual(
                    prefix_length,
                    sum(
                        item["assigned_slots"]
                        for item in subset["profile_diagnostics"]
                    ),
                )
                self.assertEqual(
                    frozen["assignments"][:prefix_length],
                    [
                        item
                        for item in frozen["assignments"]
                        if item["slot_id"] in subset["selected_slot_ids"]
                    ],
                )
                self.assertTrue(
                    {
                        item["slot_id"]
                        for item in frozen["assignments"][prefix_length:]
                    }.isdisjoint(subset["selected_slot_ids"])
                )

    def test_v3_capacity_shortfall_writes_only_a_bound_decision_document(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed, output, resolution = self._plan(
                root,
                maximum_synthetic_panelists=20,
                stale_output={
                    "schema_version": "stale-worker-ready-plan",
                    "audience_profile_rosters": {"forged": True},
                },
            )
            decision_path = output.parent / "capacity-decision.json"

            self.assertEqual(3, completed.returncode, completed.stderr)
            self.assertTrue(output.is_file())
            self.assertFalse(
                decision_path.exists(),
                "capacity-decision.json is a stale competing authority",
            )
            decision = json.loads(output.read_text(encoding="utf-8"))
            resolution_hash = (
                "sha256:" + hashlib.sha256(resolution.read_bytes()).hexdigest()
            )

        self.assertEqual(
            {
                "schema_version",
                "decision_status",
                "study_id",
                "envelope_sha256",
                "audience_package_sha256",
                "synthetic_replicate_capacity",
            },
            set(decision),
        )
        self.assertEqual("audience-capacity-decision-v1", decision["schema_version"])
        self.assertEqual("insufficient_capacity", decision["decision_status"])
        self.assertEqual(
            resolution_hash,
            decision["envelope_sha256"],
        )
        self.assertEqual(
            {
                "screening_planned": 16,
                "boundary_reserved": 8,
                "finalist_reserved": 4,
                "required_total": 28,
                "ceiling": 20,
                "ceiling_satisfied": False,
                "boundary_jobs_per_wave": 4,
                "boundary_waves_max": 2,
                "shortfall": 8,
            },
            decision["synthetic_replicate_capacity"],
        )
        self.assertNotIn("audience_profile_rosters", decision)
        self.assertNotIn("assignment", decision)

    def test_ready_and_shortfall_transitions_publish_one_atomic_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shortfall, output, resolution = self._plan(
                root,
                maximum_synthetic_panelists=20,
            )
            self.assertEqual(3, shortfall.returncode, shortfall.stderr)
            self.assertEqual(
                "audience-capacity-decision-v1",
                json.loads(output.read_text(encoding="utf-8"))[
                    "schema_version"
                ],
            )
            request_path = root / "study-request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request["maximum_synthetic_panelists"] = 40
            request_path.write_bytes(canonical_bytes(request))
            command = [
                sys.executable,
                str(SCRIPTS / "plan-large-library.py"),
                str(request_path),
                str(output),
                "--burden-pilot",
                "passed",
                "--reported-segments",
                "1",
                "--boundary-jobs-per-wave",
                "4",
                "--boundary-waves-max",
                "2",
                "--finalist-reserved",
                "4",
                "--assignment-seed",
                "29",
                "--audience-resolution",
                str(resolution),
            ]
            ready = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, ready.returncode, ready.stderr)
            ready_payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "audience-profile-rosters-v1",
                ready_payload["audience_profile_rosters"][
                    "schema_version"
                ],
            )
            request["maximum_synthetic_panelists"] = 20
            request_path.write_bytes(canonical_bytes(request))
            shortfall_again = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                3,
                shortfall_again.returncode,
                shortfall_again.stderr,
            )
            self.assertEqual(
                "audience-capacity-decision-v1",
                json.loads(output.read_text(encoding="utf-8"))[
                    "schema_version"
                ],
            )
            self.assertFalse(
                (output.parent / "capacity-decision.json").exists()
            )

    def test_injected_atomic_write_failure_preserves_only_prior_authority(
        self,
    ) -> None:
        module_path = SCRIPTS / "plan-large-library.py"
        spec = importlib.util.spec_from_file_location(
            "plan_large_library_atomic_test",
            module_path,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "study-plan.json"
            alias = root / "capacity-decision.json"
            prior = {"schema_version": "prior-ready-state"}
            output.write_bytes(canonical_bytes(prior))
            alias.write_bytes(
                canonical_bytes(
                    {"schema_version": "stale-shortfall-alias"}
                )
            )
            module._remove_competing_capacity_alias(output)
            with (
                mock.patch.object(
                    module.os,
                    "replace",
                    side_effect=OSError("injected publication failure"),
                ),
                self.assertRaisesRegex(
                    OSError,
                    "injected publication failure",
                ),
            ):
                module._atomic_json_write(
                    output,
                    {"schema_version": "new-shortfall-state"},
                    indent=None,
                )

            self.assertEqual(canonical_bytes(prior), output.read_bytes())
            self.assertFalse(alias.exists())
            self.assertEqual(
                [],
                list(root.glob(".study-plan.json.*.tmp")),
            )

    def test_manifest_contract_rejects_roster_and_combined_hash_tampering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        manifest = self._manifest_from_plan(plan)
        self.assertEqual([], validate_manifest(manifest))

        mutations: list[tuple[str, dict[str, object]]] = []
        combined = copy.deepcopy(manifest)
        combined["audience_profile_rosters"]["combined_sha256"] = (
            "sha256:" + "0" * 64
        )
        mutations.append(("combined_hash", combined))
        slot = copy.deepcopy(manifest)
        slot["audience_profile_rosters"]["screening"]["assignments"][0][
            "slot_id"
        ] = "forged-slot"
        mutations.append(("slot_id", slot))
        segment = copy.deepcopy(manifest)
        segment["audience_profile_rosters"]["screening"]["assignments"][0][
            "reported_segment_id"
        ] = "forged-segment"
        mutations.append(("segment_id", segment))
        profile = copy.deepcopy(manifest)
        profile["audience_profile_rosters"]["screening"]["assignments"][0][
            "grounded_profile_id"
        ] = "forged-profile"
        mutations.append(("profile_id", profile))
        snapshot = copy.deepcopy(manifest)
        snapshot["audience_profile_rosters"]["screening"]["assignments"][0][
            "profile_snapshot_sha256"
        ] = "sha256:" + "0" * 64
        mutations.append(("snapshot_hash", snapshot))
        reserve_order = copy.deepcopy(manifest)
        reserve_order["audience_profile_rosters"]["boundary_reserve"][
            "assignments"
        ].reverse()
        mutations.append(("reserve_order", reserve_order))
        roster_id = copy.deepcopy(manifest)
        roster_id["audience_profile_rosters"]["screening"][
            "stage_roster_id"
        ] = "forged-screening-roster"
        roster_id["audience_profile_rosters"]["combined_sha256"] = (
            canonical_sha256(self._roster_combined_input(roster_id))
        )
        mutations.append(("stage_roster_id", roster_id))
        stable_seed = copy.deepcopy(manifest)
        stable_seed["audience_profile_rosters"]["finalist_reserve"][
            "stable_seed"
        ] = "forged-stable-seed"
        stable_seed["audience_profile_rosters"]["combined_sha256"] = (
            canonical_sha256(self._roster_combined_input(stable_seed))
        )
        mutations.append(("stable_seed", stable_seed))
        capacity_count = copy.deepcopy(manifest)
        capacity_count["synthetic_replicate_capacity"].update(
            screening_planned=15,
            required_total=27,
        )
        self._reseal_manifest_rosters(capacity_count)
        mutations.append(("capacity_count", capacity_count))
        capacity_ceiling = copy.deepcopy(manifest)
        capacity_ceiling["maximum_synthetic_panelists"] = 41
        self._reseal_manifest_rosters(capacity_ceiling)
        mutations.append(("capacity_ceiling", capacity_ceiling))
        capacity_shortfall = copy.deepcopy(manifest)
        capacity_shortfall["synthetic_replicate_capacity"].update(
            shortfall=1,
            ceiling_satisfied=True,
        )
        self._reseal_manifest_rosters(capacity_shortfall)
        mutations.append(("capacity_shortfall", capacity_shortfall))
        capacity_unknown = copy.deepcopy(manifest)
        capacity_unknown["synthetic_replicate_capacity"]["unknown"] = 1
        self._reseal_manifest_rosters(capacity_unknown)
        mutations.append(("capacity_unknown", capacity_unknown))
        capacity_missing = copy.deepcopy(manifest)
        capacity_missing["synthetic_replicate_capacity"].pop("shortfall")
        self._reseal_manifest_rosters(capacity_missing)
        mutations.append(("capacity_missing", capacity_missing))
        cross_study = copy.deepcopy(manifest)
        cross_study["study_id"] = "cross-study-reseal"
        self._reseal_manifest_rosters(cross_study)
        mutations.append(("cross_study", cross_study))

        for name, tampered in mutations:
            with self.subTest(name=name):
                self.assertTrue(validate_manifest(tampered))

    def test_v3_manifest_rejects_non_string_or_empty_study_before_hashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        source = self._manifest_from_plan(plan)
        self.assertEqual([], validate_manifest(source))

        for invalid in ("", None, 7):
            manifest = copy.deepcopy(source)
            manifest["study_id"] = invalid
            for name, suffix in (
                ("screening", "screening"),
                ("boundary_reserve", "boundary-reserve"),
                ("finalist_reserve", "finalist-reserve"),
            ):
                roster = manifest["audience_profile_rosters"][name]
                roster["stage_roster_id"] = f"{invalid}:{suffix}"
                if "stable_seed" in roster:
                    roster["stable_seed"] = (
                        f"{invalid}:29:audience-profile-allocation-v1"
                    )
            self._reseal_manifest_rosters(manifest)
            with self.subTest(study_id=invalid):
                errors = validate_manifest(manifest)
                self.assertTrue(
                    any("study_id" in error and "non-empty string" in error for error in errors),
                    errors,
                )

    def test_boundary_wave_ids_are_two_digit_below_100_and_variable_width_after(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(
                Path(temporary),
                maximum_synthetic_panelists=130,
                boundary_jobs_per_wave=1,
                boundary_waves_max=100,
                finalist_reserved=4,
            )
        manifest = self._manifest_from_plan(plan)
        ids = [
            item["slot_id"]
            for item in manifest["audience_profile_rosters"][
                "boundary_reserve"
            ]["assignments"]
        ]

        self.assertEqual("boundary-wave-01-job-0001", ids[0])
        self.assertEqual("boundary-wave-99-job-0001", ids[98])
        self.assertEqual("boundary-wave-100-job-0001", ids[99])
        self.assertEqual([], validate_manifest(manifest))
        attachments = []
        for assignment in manifest["audience_profile_rosters"][
            "boundary_reserve"
        ]["assignments"]:
            slot_id = assignment["slot_id"]
            attachments.append(
                {
                    "pair_assignment_id": slot_id,
                    "wave": int(slot_id.split("-")[2]),
                    "variation_ids": ["creative-1", "creative-2"],
                    "audience_slot_id": slot_id,
                    "grounded_profile_id": assignment[
                        "grounded_profile_id"
                    ],
                    "reported_segment_id": assignment[
                        "reported_segment_id"
                    ],
                    "structural_group_id": assignment[
                        "structural_group_id"
                    ],
                    "profile_snapshot_sha256": assignment[
                        "profile_snapshot_sha256"
                    ],
                }
            )
        validated = validate_boundary_profile_attachments(
            {"predeclared_pair_assignments": attachments},
            manifest["audience_profile_rosters"]["boundary_reserve"],
        )
        self.assertEqual(100, len(validated["predeclared_pair_assignments"]))

    def test_manifest_binds_screening_roster_to_exact_job_segment_pairs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        plan = self._multi_segment_plan(plan)
        manifest = self._manifest_from_plan(plan)
        self.assertEqual([], validate_manifest(manifest))

        missing_jobs = copy.deepcopy(manifest)
        missing_jobs["assignment"].pop("synthetic_replicate_jobs")
        missing_job_errors = validate_manifest(missing_jobs)
        self.assertTrue(
            any(
                "requires the original synthetic replicate jobs" in error
                for error in missing_job_errors
            ),
            missing_job_errors,
        )

        jobs = plan["assignment"]["synthetic_replicate_jobs"]
        first_a = next(
            job for job in jobs if job["segment_id"] == "segment-a"
        )
        first_b = next(
            job for job in jobs if job["segment_id"] == "segment-b"
        )
        slots = [
            {
                "slot_id": job["synthetic_replicate_id"],
                "reported_segment_id": job["segment_id"],
            }
            for job in jobs
        ]
        for slot in slots:
            if slot["slot_id"] == first_a["synthetic_replicate_id"]:
                slot["reported_segment_id"] = "segment-b"
            elif slot["slot_id"] == first_b["synthetic_replicate_id"]:
                slot["reported_segment_id"] = "segment-a"
        self._replace_rosters(plan, screening_slots=slots)
        coherently_resealed = self._manifest_from_plan(plan)

        errors = validate_manifest(coherently_resealed)
        self.assertTrue(
            any(
                "exact synthetic replicate ID and segment pairs" in error
                for error in errors
            ),
            errors,
        )

    def test_v3_manifest_requires_exact_grounded_profile_population(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        manifest = self._manifest_from_plan(plan)
        self.assertEqual([], validate_manifest(manifest))

        mutations: list[tuple[str, dict[str, object]]] = []
        removed_collection = copy.deepcopy(manifest)
        removed_collection.pop("grounded_context_profiles")
        mutations.append(("removed_collection", removed_collection))
        duplicate = copy.deepcopy(manifest)
        duplicate["grounded_context_profiles"].append(
            copy.deepcopy(duplicate["grounded_context_profiles"][0])
        )
        mutations.append(("duplicate", duplicate))
        missing = copy.deepcopy(manifest)
        missing["grounded_context_profiles"].pop()
        mutations.append(("missing", missing))
        extra = copy.deepcopy(manifest)
        extra_profile = copy.deepcopy(extra["grounded_context_profiles"][0])
        extra_profile["grounded_profile_id"] = "extra-grounded-profile"
        extra_profile["profile_snapshot_sha256"] = "sha256:" + "e" * 64
        extra["grounded_context_profiles"].append(extra_profile)
        mutations.append(("extra", extra))
        for field, value in (
            ("effective_weight", 0.99),
            ("eligible", False),
            ("must_cover_group_ids", []),
        ):
            tampered = copy.deepcopy(manifest)
            tampered["grounded_context_profiles"][0][field] = value
            mutations.append((field, tampered))

        for name, tampered in mutations:
            with self.subTest(name=name):
                self.assertTrue(validate_manifest(tampered))

    def test_contract_rejects_partial_v3_fields_and_v3_fields_on_v2(self) -> None:
        legacy = complete_manifest()
        self.assertEqual([], validate_manifest(legacy))
        for field in V3_FIELDS:
            with self.subTest(field=field):
                tampered = copy.deepcopy(legacy)
                tampered[field] = {}
                self.assertTrue(validate_manifest(tampered))

        with tempfile.TemporaryDirectory() as temporary:
            plan, _resolution = self._valid_plan(Path(temporary))
        v3_manifest = self._manifest_from_plan(plan)
        for field in V3_FIELDS:
            with self.subTest(missing=field):
                partial = copy.deepcopy(v3_manifest)
                partial.pop(field)
                self.assertTrue(validate_manifest(partial))


if __name__ == "__main__":
    unittest.main()
