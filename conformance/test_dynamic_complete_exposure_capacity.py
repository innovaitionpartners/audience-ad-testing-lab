from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.dynamic_complete_exposure_capacity import (  # noqa: E402
    POLICY_VERSION,
    plan_dynamic_complete_exposure_capacity,
)


def profile(
    profile_id: str,
    segment_id: str,
    conditional_weight: float,
) -> dict[str, object]:
    return {
        "grounded_profile_id": profile_id,
        "reported_segment_id": segment_id,
        "conditional_effective_weight": conditional_weight,
        "eligible": True,
    }


class DynamicCompleteExposureCapacityTests(unittest.TestCase):
    maxDiff = None

    def test_equal_six_profile_design_selects_balanced_core_and_reserve(self):
        profiles = [
            profile(f"{segment}-{suffix}", segment, 0.5)
            for segment in ("agency", "in-house", "independent")
            for suffix in ("a", "b")
        ]

        plan = plan_dynamic_complete_exposure_capacity(
            profiles=profiles,
            segment_weights={
                "agency": 1 / 3,
                "in-house": 1 / 3,
                "independent": 1 / 3,
            },
            creative_count=4,
            maximum_total_executions=40,
            finalist_reserved=4,
        )

        self.assertEqual(POLICY_VERSION, plan["policy_version"])
        self.assertEqual(30, plan["core_planned_executions"])
        self.assertEqual(
            {"agency": 10, "in-house": 10, "independent": 10},
            plan["core_allocation_by_segment"],
        )
        self.assertEqual(
            {profile_id: 5 for profile_id in sorted(
                item["grounded_profile_id"] for item in profiles
            )},
            {
                item["grounded_profile_id"]: item["planned_executions"]
                for item in plan["core_allocation_by_profile"]
            },
        )
        self.assertTrue(plan["weight_fidelity"]["exact"])
        self.assertEqual(1, len(plan["balanced_reserve_blocks"]))
        self.assertEqual(6, plan["screening_reserved"])
        self.assertEqual(36, plan["maximum_screening_slots"])
        self.assertEqual(40, plan["required_total_with_reserve"])
        self.assertEqual(40, plan["maximum_authorized_unique_execution_slots"])
        self.assertTrue(plan["authorized_total_capacity_satisfied"])
        reserve = plan["balanced_reserve_blocks"][0]
        self.assertEqual(6, reserve["planned_executions"])
        self.assertEqual(
            set(item["grounded_profile_id"] for item in profiles),
            set(reserve["allocation_by_profile"]),
        )
        self.assertTrue(all(value == 1 for value in reserve["allocation_by_profile"].values()))
        self.assertEqual(0, plan["count_semantics"]["human_respondents"])
        self.assertFalse(plan["count_semantics"]["human_sample_independence"])

    def test_profile_weights_not_segment_count_determine_capacity(self):
        plan = plan_dynamic_complete_exposure_capacity(
            profiles=[
                profile("majority", "only-segment", 0.75),
                profile("minority", "only-segment", 0.25),
            ],
            segment_weights={"only-segment": 1.0},
            creative_count=3,
            maximum_total_executions=24,
            finalist_reserved=4,
        )

        self.assertEqual(20, plan["core_planned_executions"])
        self.assertNotEqual(9, plan["core_planned_executions"])
        self.assertEqual(
            {"majority": 15, "minority": 5},
            {
                item["grounded_profile_id"]: item["planned_executions"]
                for item in plan["core_allocation_by_profile"]
            },
        )
        self.assertEqual([], plan["balanced_reserve_blocks"])

    def test_usable_floor_excludes_failure_replacements_from_core(self):
        plan = plan_dynamic_complete_exposure_capacity(
            profiles=[profile("solo", "segment", 1.0)],
            segment_weights={"segment": 1.0},
            creative_count=6,
            maximum_total_executions=18,
            finalist_reserved=4,
        )

        allocation = plan["core_allocation_by_profile"][0]
        self.assertEqual(6, allocation["minimum_usable_records"])
        self.assertEqual("balanced_reserve_blocks", allocation["failure_handling"])
        self.assertEqual(6, allocation["planned_executions"])
        self.assertIn("creative presentation positions", plan["selection_rationale"][0])

    def test_plan_is_deterministic_across_profile_input_order(self):
        profiles = [
            profile("zeta", "segment-b", 1.0),
            profile("beta", "segment-a", 0.4),
            profile("alpha", "segment-a", 0.6),
        ]
        arguments = {
            "segment_weights": {"segment-b": 0.3, "segment-a": 0.7},
            "creative_count": 3,
            "maximum_total_executions": 34,
            "finalist_reserved": 4,
        }

        forward = plan_dynamic_complete_exposure_capacity(
            profiles=profiles,
            **arguments,
        )
        reverse = plan_dynamic_complete_exposure_capacity(
            profiles=list(reversed(profiles)),
            **arguments,
        )

        self.assertEqual(forward, reverse)

    def test_insufficient_ceiling_still_reports_required_core(self):
        profiles = [
            profile(f"profile-{index}", "segment", 1 / 9)
            for index in range(9)
        ]

        plan = plan_dynamic_complete_exposure_capacity(
            profiles=profiles,
            segment_weights={"segment": 1.0},
            creative_count=6,
            maximum_total_executions=24,
            finalist_reserved=4,
        )

        self.assertEqual(54, plan["core_planned_executions"])
        self.assertEqual(54, plan["maximum_screening_slots"])
        self.assertEqual(58, plan["required_total_with_reserve"])
        self.assertEqual(24, plan["maximum_authorized_unique_execution_slots"])
        self.assertFalse(plan["authorized_total_capacity_satisfied"])
        self.assertEqual(34, plan["authorized_capacity_shortfall"])
        self.assertEqual([], plan["balanced_reserve_blocks"])

    def test_complete_assignment_builder_uses_dynamic_segment_counts(self):
        module_path = SCRIPTS / "plan-large-library.py"
        spec = importlib.util.spec_from_file_location("plan_large_library", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assignment = module._complete_assignment_payload(
            ("creative-1", "creative-2", "creative-3"),
            {"segment-a": 4, "segment-b": 6},
            17,
            (),
        )

        self.assertEqual(
            {"segment-a": 4, "segment-b": 6},
            assignment["segment_allocations"],
        )
        self.assertEqual(10, len(assignment["synthetic_replicate_jobs"]))
        self.assertEqual(
            4,
            sum(
                job["segment_id"] == "segment-a"
                for job in assignment["synthetic_replicate_jobs"]
            ),
        )

    def test_rejects_unusable_profile_weight_configuration(self):
        with self.assertRaisesRegex(ValueError, "positive conditional weight"):
            plan_dynamic_complete_exposure_capacity(
                profiles=[profile("zero", "segment", 0.0)],
                segment_weights={"segment": 1.0},
                creative_count=3,
                maximum_total_executions=14,
                finalist_reserved=4,
            )


if __name__ == "__main__":
    unittest.main()
