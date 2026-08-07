import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.planning import (  # noqa: E402
    StudyRequest,
    choose_method,
    minimum_screening_jobs,
    reserve_capacity,
)
from audience_lab.audience_library import resolve_audience_panel  # noqa: E402
from audience_lab.audience_package import build_audience_package  # noqa: E402


class PlanningTests(unittest.TestCase):
    def study_request(self, creative_count, requested_shortlist_size):
        return {
            "study_id": "two-creative-boundary",
            "creative_ids": [f"creative-{index}" for index in range(creative_count)],
            "creative_format": "static_image",
            "requested_shortlist_size": requested_shortlist_size,
            "maximum_synthetic_panelists": 20,
        }

    def test_two_creative_study_accepts_both_as_the_shortlist(self):
        request = StudyRequest.from_mapping(self.study_request(2, 2))

        self.assertEqual(2, request.requested_shortlist_size)
        self.assertEqual("complete_exposure", choose_method(request.creative_count, True))

    def test_two_creative_study_rejects_shortlist_below_two(self):
        with self.assertRaisesRegex(ValueError, "requested_shortlist_size"):
            StudyRequest.from_mapping(self.study_request(2, 1))

    def test_two_creative_study_rejects_shortlist_above_creative_count(self):
        with self.assertRaisesRegex(ValueError, "cannot exceed creative count"):
            StudyRequest.from_mapping(self.study_request(2, 3))

    def test_three_creative_complete_exposure_can_narrow_to_two_finalists(self):
        request = StudyRequest.from_mapping(self.study_request(3, 2))

        self.assertEqual(2, request.requested_shortlist_size)

    def test_partial_exposure_library_retains_three_finalist_minimum(self):
        with self.assertRaisesRegex(ValueError, "between 3 and 6"):
            StudyRequest.from_mapping(self.study_request(7, 2))

    def test_default_five_finalist_target_remains_valid_when_feasible(self):
        request = StudyRequest.from_mapping(self.study_request(5, 5))

        self.assertEqual(5, request.requested_shortlist_size)
        self.assertEqual("complete_exposure", choose_method(request.creative_count, True))

    def test_seven_creatives_route_to_partial_exposure(self):
        self.assertEqual("partial_exposure_maxdiff", choose_method(7, burden_pilot_passed=True))

    def test_context_strata_preserve_planning_dimensions_and_provenance(self):
        payload = self.study_request(7, 5)
        payload["context_strata"] = [
            {
                "context_stratum_id": "active-evaluation",
                "segment_id": "segment-1",
                "planned_weight": 2,
                "weighting_rule": "research_weight_within_segment",
                "dimensions": [
                    {
                        "name": "buying_stage",
                        "value": "active_evaluation",
                        "status": "observed",
                        "source_evidence": ["approved-research-brief:E1"],
                    }
                ],
            }
        ]

        request = StudyRequest.from_mapping(payload)

        self.assertEqual(1, len(request.context_strata))
        self.assertEqual("active-evaluation", request.context_strata[0].context_stratum_id)
        self.assertEqual("observed", request.context_strata[0].dimensions[0].status)
        self.assertEqual(
            ("approved-research-brief:E1",),
            request.context_strata[0].dimensions[0].source_evidence,
        )

    def test_context_strata_reject_missing_dimension_provenance(self):
        payload = self.study_request(7, 5)
        payload["context_strata"] = [
            {
                "context_stratum_id": "unsupported",
                "segment_id": "segment-1",
                "planned_weight": 1,
                "weighting_rule": "equal_within_segment",
                "dimensions": [
                    {
                        "name": "urgency",
                        "value": "high",
                        "status": "estimated",
                        "source_evidence": [],
                    }
                ],
            }
        ]

        with self.assertRaisesRegex(ValueError, "source_evidence"):
            StudyRequest.from_mapping(payload)

    def test_v2_audience_intake_is_exact_and_cannot_smuggle_context_strata(self):
        payload = self.study_request(7, 5)
        payload["audience_panel"] = {
            "source": "library",
            "panel_id": "operations-leaders",
            "version": "1.0.0",
        }
        request = StudyRequest.from_mapping(payload)
        self.assertEqual("audience_panel", request.audience_route)
        payload["audience_panel"]["extra"] = True
        with self.assertRaisesRegex(ValueError, "allowlist"):
            StudyRequest.from_mapping(payload)
        payload["audience_panel"].pop("extra")
        payload["context_strata"] = [{
            "context_stratum_id": "invented",
            "segment_id": "segment-1",
            "planned_weight": 1,
            "weighting_rule": "invented",
            "dimensions": [{
                "name": "invented", "value": "invented", "status": "experimental",
                "source_evidence": ["invented"],
            }],
        }]
        with self.assertRaisesRegex(ValueError, "resolved audience snapshot"):
            StudyRequest.from_mapping(payload)

    def test_planner_consumes_ready_resolution_and_emits_bound_audience(self):
        brief = json.loads((FIXTURES / "approved-brief.json").read_text())
        panel = json.loads((FIXTURES / "approved-panel.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = build_audience_package(brief, panel, base / "package")
            scope = {
                key: copy.deepcopy(panel["audience_scope"][key])
                for key in ("audience", "market", "geography", "category", "buying_context", "exclusions")
            }
            run = base
            resolution = resolve_audience_panel(
                {"source": "file", "package_path": str(package.package_zip_path)},
                scope,
                run_dir=run,
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            request = self.study_request(7, 5)
            request["maximum_synthetic_panelists"] = 60
            request["audience_panel"] = {
                "source": "file", "package_path": str(package.package_zip_path)
            }
            request_path = base / "request.json"
            output_path = base / "plan.json"
            request_path.write_text(json.dumps(request))
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(request_path), str(output_path), "--burden-pilot", "passed",
                "--reported-segments", "1", "--boundary-jobs-per-wave", "4",
                "--boundary-waves-max", "1", "--finalist-reserved", "10",
                "--audience-resolution", str(run / "audience" / "resolution.json"),
            ], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            plan_bytes = output_path.read_bytes()
            plan = json.loads(output_path.read_text())

            request["audience_panel"]["package_path"] = str(base / "missing.zip")
            request_path.write_text(json.dumps(request))
            mismatched = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(request_path), str(base / "bad-plan.json"), "--burden-pilot", "passed",
                "--reported-segments", "1", "--boundary-jobs-per-wave", "4",
                "--boundary-waves-max", "1", "--finalist-reserved", "10",
                "--audience-resolution", str(run / "audience" / "resolution.json"),
            ], capture_output=True, text=True)
            self.assertNotEqual(0, mismatched.returncode)

            other_run = base / "other-run"
            other_run.mkdir()
            cross_run = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(request_path), str(other_run / "plan.json"),
                "--burden-pilot", "passed", "--reported-segments", "1",
                "--boundary-jobs-per-wave", "4", "--boundary-waves-max", "1",
                "--finalist-reserved", "10", "--audience-resolution",
                str(run / "audience" / "resolution.json"),
            ], capture_output=True, text=True)
            self.assertNotEqual(0, cross_run.returncode)
            self.assertIn("canonical run-relative", cross_run.stderr)

            request.pop("audience_panel")
            request_path.write_text(json.dumps(request))
            downgraded = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(request_path), str(base / "downgraded-plan.json"),
                "--burden-pilot", "passed", "--reported-segments", "1",
                "--boundary-jobs-per-wave", "4", "--boundary-waves-max", "1",
                "--finalist-reserved", "10",
            ], capture_output=True, text=True)
            self.assertNotEqual(0, downgraded.returncode)
            self.assertIn("read-only", downgraded.stderr)

        self.assertEqual(resolution["audience_lock"], plan["audience_lock"])
        self.assertEqual(resolution["grounded_context_profiles"], plan["grounded_context_profiles"])
        self.assertEqual("audience/snapshot", plan["audience_package"]["resolved_snapshot_path"])
        self.assertEqual(resolution["context_strata"], plan["assignment"]["context_strata"])
        self.assertEqual(22952, len(plan_bytes))
        self.assertEqual(
            "ba42611325f4de009d29227b46a297610f59eaf4554f5ac189de1179660208d7",
            hashlib.sha256(plan_bytes).hexdigest(),
        )
        self.assertFalse(
            {
                "audience_profile_rosters",
                "audience_allocation_fidelity",
                "audience_run_claim",
            }
            & set(plan)
        )

    def test_v2_complete_exposure_plan_bytes_remain_unchanged(self):
        brief = json.loads((FIXTURES / "approved-brief.json").read_text())
        panel = json.loads((FIXTURES / "approved-panel.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = build_audience_package(brief, panel, base / "package")
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
            resolve_audience_panel(
                {
                    "source": "file",
                    "package_path": str(package.package_zip_path),
                },
                scope,
                run_dir=base,
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            request = {
                "study_id": "task4-v2-complete-byte-regression",
                "creative_ids": [
                    f"creative-{index}" for index in range(1, 6)
                ],
                "creative_format": "static_image",
                "requested_shortlist_size": 5,
                "maximum_synthetic_panelists": 20,
                "audience_panel": {
                    "source": "file",
                    "package_path": str(package.package_zip_path),
                },
            }
            request_path = base / "request.json"
            output_path = base / "plan.json"
            request_path.write_text(
                json.dumps(request, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
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
                    "--assignment-seed",
                    "29",
                    "--audience-resolution",
                    str(base / "audience" / "resolution.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            plan_bytes = output_path.read_bytes()
            plan = json.loads(plan_bytes)

        self.assertEqual("complete_exposure", plan["method"])
        self.assertEqual(0, plan["synthetic_replicate_capacity"]["boundary_reserved"])
        self.assertEqual(10723, len(plan_bytes))
        self.assertEqual(
            "1f94baef86728c979b9890350280a66641226d6cdb970a9d44eb425ee8b430b7",
            hashlib.sha256(plan_bytes).hexdigest(),
        )
        self.assertFalse(
            {
                "audience_profile_rosters",
                "audience_allocation_fidelity",
                "audience_run_claim",
            }
            & set(plan)
        )

    def test_planner_materializes_and_binds_initial_provisional_audience(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            request = self.study_request(7, 5)
            request["maximum_synthetic_panelists"] = 60
            request["provisional_audience"] = {
                "scope": {
                    "audience": "Operations leaders", "market": "B2B software",
                    "geography": "United States", "category": "Workflow software",
                    "buying_context": "Evaluating replacement tools", "exclusions": [],
                },
                "user_defined_segments": [{
                    "segment_id": "operations-leaders", "name": "Operations leaders",
                    "description": "A user-defined provisional segment.",
                }],
                "accepted_by": "panel-owner",
                "accepted_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
            }
            request_path = base / "request.json"
            output_path = base / "plan.json"
            request_path.write_text(json.dumps(request))
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(request_path), str(output_path), "--burden-pilot", "passed",
                "--reported-segments", "1", "--boundary-jobs-per-wave", "4",
                "--boundary-waves-max", "1", "--finalist-reserved", "10",
            ], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            plan = json.loads(output_path.read_text())
            panel = json.loads((base / "audience/snapshot/saved-audience-panel.json").read_text())
            self.assertIn("audience_package", plan)
            self.assertEqual("provisional_no_research", panel["persona_research"]["status"])
            self.assertFalse(
                {
                    "audience_profile_rosters",
                    "audience_allocation_fidelity",
                    "audience_run_claim",
                }
                & set(plan)
            )

            laundering_run = base / "laundering-run"
            shutil.copytree(base / "audience", laundering_run / "audience")
            laundering_request = self.study_request(7, 5)
            laundering_request["maximum_synthetic_panelists"] = 60
            laundering_request["audience_panel"] = {
                "source": "file",
                "package_path": str(
                    laundering_run / "audience/snapshot/audience-panel-package.zip"
                ),
            }
            laundering_request_path = laundering_run / "request.json"
            laundering_request_path.write_text(json.dumps(laundering_request))
            laundered = subprocess.run([
                sys.executable, str(SCRIPTS / "plan-large-library.py"),
                str(laundering_request_path), str(laundering_run / "plan.json"),
                "--burden-pilot", "passed", "--reported-segments", "1",
                "--boundary-jobs-per-wave", "4", "--boundary-waves-max", "1",
                "--finalist-reserved", "10", "--audience-resolution",
                str(laundering_run / "audience/resolution.json"),
            ], capture_output=True, text=True)
            self.assertNotEqual(0, laundered.returncode)
            self.assertIn("provisional", laundered.stderr.lower())

    def test_nine_planned_participations_survive_one_lost_block(self):
        self.assertEqual(23, minimum_screening_jobs(10, reported_segments=1))

    def test_segment_utilities_multiply_coverage_floor(self):
        self.assertEqual(46, minimum_screening_jobs(10, reported_segments=2))

    def test_boundary_reserve_cannot_consume_finalist_reserve(self):
        plan = reserve_capacity(
            ceiling=90,
            screening_planned=46,
            boundary_jobs_per_wave=8,
            boundary_waves_max=2,
            finalist_reserved=20,
        )
        self.assertEqual(82, plan.required_total)
        self.assertTrue(plan.ceiling_satisfied)
