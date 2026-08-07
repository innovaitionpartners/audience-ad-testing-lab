import json
import itertools
from datetime import datetime, timedelta, timezone
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
SCRIPTS = str(SKILL_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from audience_lab.assignments import (  # noqa: E402
    _choose_assignment_block,
    _pair_projection_context,
    _position_projection_context,
    assignment_diagnostics,
    build_assignments,
)
from audience_lab.planning import reserve_capacity  # noqa: E402


FIXTURE = ROOT / "conformance" / "fixtures" / "assignment-seven-creatives.json"


def provisional_audience() -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "scope": {
            "audience": "Synthetic test audience", "market": "Test market",
            "geography": "United States", "category": "Test category",
            "buying_context": "Evaluating options", "exclusions": [],
        },
        "user_defined_segments": [{
            "segment_id": "segment-1", "name": "Test segment",
            "description": "A provisional segment used to exercise assignment planning.",
        }],
        "accepted_by": "test-owner",
        "accepted_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
    }


class AssignmentTests(unittest.TestCase):
    def fixture(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_assignments_are_deterministic_and_four_item(self):
        first = build_assignments([f"V{i}" for i in range(1, 8)], {"S1": 16}, seed=17)
        second = build_assignments([f"V{i}" for i in range(1, 8)], {"S1": 16}, seed=17)

        self.assertEqual(first, second)
        self.assertEqual(16, len(first.jobs))
        self.assertTrue(all(len(set(job.variation_ids)) == 4 for job in first.jobs))
        self.assertTrue(all(set(job.variation_ids) == set(job.shown_order) for job in first.jobs))
        self.assertTrue(all(job.inclusion_probability == 4 / 7 for job in first.jobs))

    def test_reserve_slot_ids_keep_existing_wave_position_and_finalist_order(self):
        from audience_lab.assignments import (
            build_boundary_reserve_slots,
            build_finalist_reserve_slots,
        )

        boundary = build_boundary_reserve_slots(
            ("segment-a", "segment-b"),
            jobs_per_wave=2,
            waves_max=2,
        )
        finalists = build_finalist_reserve_slots(3)

        self.assertEqual(
            [
                {
                    "slot_id": "boundary-wave-01-job-0001",
                    "reported_segment_id": "segment-a",
                },
                {
                    "slot_id": "boundary-wave-01-job-0002",
                    "reported_segment_id": "segment-b",
                },
                {
                    "slot_id": "boundary-wave-02-job-0001",
                    "reported_segment_id": "segment-a",
                },
                {
                    "slot_id": "boundary-wave-02-job-0002",
                    "reported_segment_id": "segment-b",
                },
            ],
            boundary,
        )
        self.assertEqual(
            [
                {"slot_id": "finalist-0001", "reported_segment_id": None},
                {"slot_id": "finalist-0002", "reported_segment_id": None},
                {"slot_id": "finalist-0003", "reported_segment_id": None},
            ],
            finalists,
        )

    def test_reordered_creative_ids_are_canonicalized_before_pair_accounting(self):
        ordered_ids = [f"V{i}" for i in range(1, 11)]
        reordered_ids = ["V6", "V1", "V10", "V3", "V8", "V2", "V9", "V4", "V7", "V5"]

        ordered = build_assignments(ordered_ids, {"S1": 23}, seed=17)
        reordered = build_assignments(reordered_ids, {"S1": 23}, seed=17)

        self.assertEqual(ordered.jobs, reordered.jobs)
        self.assertEqual(ordered.diagnostics_as_dict(), reordered.diagnostics_as_dict())

    def test_graph_survives_any_one_block_removal(self):
        plan = build_assignments([f"V{i}" for i in range(1, 8)], {"S1": 16}, seed=17)
        diagnostics = assignment_diagnostics(plan)

        self.assertTrue(diagnostics.connected)
        self.assertTrue(diagnostics.one_block_resilient)

    def test_exposure_and_position_ranges_are_minimized(self):
        plan = build_assignments([f"V{i}" for i in range(1, 8)], {"S1": 16}, seed=17)
        diagnostics = assignment_diagnostics(plan)

        self.assertLessEqual(diagnostics.exposure_range, 1)
        self.assertLessEqual(diagnostics.position_range, 1)

    def test_fixture_reproduces_the_frozen_seven_creative_plan(self):
        fixture = self.fixture()
        plan = build_assignments(
            fixture["creative_ids"],
            fixture["segment_allocations"],
            seed=fixture["seed"],
        )

        self.assertEqual(fixture["synthetic_replicate_jobs"], plan.jobs_as_dicts())
        self.assertEqual(fixture["diagnostics"], plan.diagnostics_as_dict())

    def test_multiple_valid_library_sizes_remain_balanced_and_resilient(self):
        for creative_count in (7, 8, 10, 13, 25):
            with self.subTest(creative_count=creative_count):
                job_count = (9 * creative_count + 3) // 4
                plan = build_assignments(
                    [f"V{i:02d}" for i in range(1, creative_count + 1)],
                    {"S1": job_count},
                    seed=29,
                )
                diagnostics = assignment_diagnostics(plan)

                self.assertLessEqual(diagnostics.exposure_range, 1)
                self.assertLessEqual(diagnostics.position_range, 1)
                self.assertTrue(diagnostics.connected)
                self.assertTrue(diagnostics.one_block_resilient)

    def test_exact_objective_precedes_seed_for_large_candidate_spaces(self):
        creative_ids = tuple(f"V{i:02d}" for i in range(1, 21))
        exposure_counts = {creative_id: 0 for creative_id in creative_ids}
        position_counts = {creative_id: [0, 0, 0, 0] for creative_id in creative_ids}
        pair_counts = {
            tuple(sorted(pair)): 1
            for pair in itertools.combinations(creative_ids, 2)
        }
        optimal = {"V07", "V11", "V14", "V19"}
        for pair in itertools.combinations(sorted(optimal), 2):
            pair_counts[pair] = 0
        pair_counts[("V01", "V02")] = 2

        for seed in (1, 17, 999):
            with self.subTest(seed=seed):
                block, _order, objective = _choose_assignment_block(
                    creative_ids,
                    exposure_contexts=(exposure_counts,),
                    position_contexts=(
                        _position_projection_context(creative_ids, position_counts),
                    ),
                    pair_contexts=(_pair_projection_context(pair_counts),),
                    seed=seed,
                    segment_id="S1",
                    block_index=0,
                )
                self.assertEqual(optimal, set(block))
                self.assertEqual((1,), objective.exposure_ranges)
                self.assertEqual((1,), objective.position_ranges)
                self.assertEqual((2,), objective.pair_concurrence_maxima)
                self.assertEqual((0,), objective.pair_concurrence_weights)

    def test_context_strata_are_planned_balanced_and_reported_with_provenance(self):
        context_strata = [
            {
                "context_stratum_id": "active-evaluation",
                "segment_id": "S1",
                "planned_weight": 1,
                "weighting_rule": "equal_within_segment",
                "dimensions": [
                    {
                        "name": "buying_stage",
                        "value": "active_evaluation",
                        "status": "estimated",
                        "source_evidence": ["approved-research-brief:E1"],
                    }
                ],
            },
            {
                "context_stratum_id": "early-exploration",
                "segment_id": "S1",
                "planned_weight": 1,
                "weighting_rule": "equal_within_segment",
                "dimensions": [
                    {
                        "name": "buying_stage",
                        "value": "early_exploration",
                        "status": "experimental",
                        "source_evidence": ["approved-run-plan:scenario-1"],
                    }
                ],
            },
        ]

        plan = build_assignments(
            [f"V{i}" for i in range(1, 8)],
            {"S1": 16},
            seed=17,
            context_strata=context_strata,
        )
        reordered = build_assignments(
            [f"V{i}" for i in range(1, 8)],
            {"S1": 16},
            seed=17,
            context_strata=list(reversed(context_strata)),
        )
        payload = plan.as_dict()

        self.assertEqual(plan, reordered)
        self.assertEqual(
            {"active-evaluation": 8, "early-exploration": 8},
            {
                row["context_stratum_id"]: row["planned_jobs"]
                for row in payload["context_stratum_allocations"]
            },
        )
        self.assertEqual(
            {"active-evaluation", "early-exploration"},
            {job["context_stratum_id"] for job in payload["synthetic_replicate_jobs"]},
        )
        for balance in payload["context_stratum_balance"]:
            self.assertLessEqual(balance["exposure_range"], 1)
            self.assertLessEqual(balance["position_range"], 1)
            self.assertNotIn("outcome", balance)
        self.assertEqual(
            ["approved-research-brief:E1"],
            payload["context_strata"][0]["dimensions"][0]["source_evidence"],
        )

    def test_pair_count_serialization_cannot_collide_on_delimiters(self):
        creative_ids = ("A", "A|B", "B|C", "C", "D", "E", "F")
        plan = build_assignments(creative_ids, {"S1": 16}, seed=17)
        serialized_counts = plan.as_dict()["pair_concurrence"]["counts"]
        serialized_pairs = [tuple(record["variation_ids"]) for record in serialized_counts]

        self.assertEqual(21, len(serialized_counts))
        self.assertEqual(21, len(set(serialized_pairs)))
        self.assertIn(("A", "B|C"), serialized_pairs)
        self.assertIn(("A|B", "C"), serialized_pairs)

    def test_one_hundred_creative_boundary_remains_balanced_and_resilient(self):
        plan = build_assignments(
            [f"V{i:03d}" for i in range(1, 101)],
            {"S1": 225},
            seed=29,
        )
        diagnostics = assignment_diagnostics(plan)

        self.assertEqual(225, len(plan.jobs))
        self.assertEqual(0, diagnostics.exposure_range)
        self.assertLessEqual(diagnostics.position_range, 1)
        self.assertTrue(diagnostics.connected)
        self.assertTrue(diagnostics.one_block_resilient)

    def test_required_segment_graphs_are_individually_resilient(self):
        plan = build_assignments(
            [f"V{i}" for i in range(1, 10)],
            {"growth": 21, "operations": 21},
            seed=41,
        )

        overall = assignment_diagnostics(plan)
        self.assertLessEqual(overall.exposure_range, 1)
        self.assertLessEqual(overall.position_range, 1)

        for segment_id in ("growth", "operations"):
            with self.subTest(segment_id=segment_id):
                diagnostics = assignment_diagnostics(plan, segment_id=segment_id)
                self.assertTrue(diagnostics.connected)
                self.assertTrue(diagnostics.one_block_resilient)
                self.assertLessEqual(diagnostics.exposure_range, 1)
                self.assertLessEqual(diagnostics.position_range, 1)

    def test_feasible_capacity_must_match_segment_allocations(self):
        feasible = reserve_capacity(
            ceiling=48,
            screening_planned=16,
            boundary_jobs_per_wave=6,
            boundary_waves_max=2,
            finalist_reserved=20,
        )
        plan = build_assignments(
            [f"V{i}" for i in range(1, 8)],
            {"S1": 16},
            seed=17,
            capacity_plan=feasible,
        )
        self.assertEqual(16, len(plan.jobs))

        mismatched = reserve_capacity(
            ceiling=49,
            screening_planned=17,
            boundary_jobs_per_wave=6,
            boundary_waves_max=2,
            finalist_reserved=20,
        )
        with self.assertRaisesRegex(ValueError, "screening_planned"):
            build_assignments(
                [f"V{i}" for i in range(1, 8)],
                {"S1": 16},
                seed=17,
                capacity_plan=mismatched,
            )

    def test_rejects_invalid_and_impossible_inputs(self):
        cases = (
            (("V1", "V2", "V3"), {"S1": 16}, "at least four"),
            (("V1", "V2", "V3", "V3"), {"S1": 16}, "unique"),
            (("V1", "V2", "V3", "V4", "V5", "V6", "V7"), {}, "non-empty"),
            (("V1", "V2", "V3", "V4", "V5", "V6", "V7"), {"S1": 0}, "positive"),
            (("V1", "V2", "V3", "V4", "V5", "V6", "V7"), {"S1": 3}, "resilient"),
        )
        for creative_ids, allocations, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    build_assignments(creative_ids, allocations, seed=17)

        infeasible = reserve_capacity(
            ceiling=47,
            screening_planned=16,
            boundary_jobs_per_wave=6,
            boundary_waves_max=2,
            finalist_reserved=20,
        )
        with self.assertRaisesRegex(ValueError, "ceiling"):
            build_assignments(
                [f"V{i}" for i in range(1, 8)],
                {"S1": 16},
                seed=17,
                capacity_plan=infeasible,
            )

        with self.assertRaisesRegex(ValueError, "at most 100"):
            build_assignments(
                [f"V{i}" for i in range(101)],
                {"S1": 228},
                seed=17,
            )

    def test_planner_serializes_assignments_only_for_feasible_partial_exposure(self):
        fixture = ROOT / "conformance" / "fixtures" / "study-request-large.json"
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "study-request.json"
            output_path = Path(directory) / "study-plan.json"
            request_payload = json.loads(fixture.read_text(encoding="utf-8"))
            request_payload["creative_ids"] = list(reversed(request_payload["creative_ids"]))
            request_payload.pop("context_strata", None)
            request_payload["provisional_audience"] = provisional_audience()
            request_path.write_text(json.dumps(request_payload), encoding="utf-8")
            result = subprocess.run(
                [
                    "python3",
                    str(SKILL_ROOT / "scripts" / "plan-large-library.py"),
                    str(request_path),
                    str(output_path),
                    "--burden-pilot",
                    "passed",
                    "--reported-segments",
                    "1",
                    "--boundary-jobs-per-wave",
                    "6",
                    "--boundary-waves-max",
                    "2",
                    "--finalist-reserved",
                    "20",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        assignment = payload["assignment"]
        self.assertEqual(4, assignment["block_size"])
        self.assertTrue(assignment["connected"])
        self.assertTrue(assignment["one_block_resilient"])
        self.assertEqual(23, len(assignment["synthetic_replicate_jobs"]))
        self.assertIn("exposure_counts", assignment)
        self.assertIn("position_counts", assignment)
        self.assertIn("neighbor_counts", assignment)
        self.assertIn("pair_concurrence", assignment)
        self.assertEqual(1, len(assignment["context_strata"]))
        self.assertEqual(1, len(assignment["context_stratum_balance"]))
        self.assertTrue(
            all(job["inclusion_probability"] == 0.4 for job in assignment["synthetic_replicate_jobs"])
        )

    def test_planner_builds_complete_set_jobs_but_leaves_split_and_infeasible_routes_unassigned(self):
        cases = (
            (5, "passed", 50, 0, True),
            (10, "failed", 90, 0, False),
            (10, "passed", 10, 3, False),
        )
        for creative_count, burden_status, ceiling, expected_returncode, assigned in cases:
            with self.subTest(
                creative_count=creative_count,
                burden_status=burden_status,
                ceiling=ceiling,
            ), tempfile.TemporaryDirectory() as directory:
                request_path = Path(directory) / "request.json"
                output_path = Path(directory) / "plan.json"
                request_path.write_text(
                    json.dumps(
                        {
                            "study_id": "routing-check",
                            "creative_ids": [f"V{i}" for i in range(creative_count)],
                            "creative_format": "static_image",
                            "requested_shortlist_size": min(5, creative_count),
                            "maximum_synthetic_panelists": ceiling,
                            "provisional_audience": provisional_audience(),
                        }
                    ),
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "python3",
                        str(SKILL_ROOT / "scripts" / "plan-large-library.py"),
                        str(request_path),
                        str(output_path),
                        "--burden-pilot",
                        burden_status,
                        "--reported-segments",
                        "1",
                        "--boundary-jobs-per-wave",
                        "0",
                        "--boundary-waves-max",
                        "0",
                        "--finalist-reserved",
                        "0",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(expected_returncode, result.returncode, result.stderr)
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual(assigned, "assignment" in payload)
                if assigned:
                    self.assertEqual(9, len(payload["assignment"]["synthetic_replicate_jobs"]))
                    self.assertTrue(
                        all(
                            len(job["variation_ids"]) == creative_count
                            for job in payload["assignment"]["synthetic_replicate_jobs"]
                        )
                    )


if __name__ == "__main__":
    unittest.main()
