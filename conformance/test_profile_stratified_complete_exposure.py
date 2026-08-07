from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audience_lab.complete_exposure import aggregate_complete_exposure  # noqa: E402


def response(
    replicate_id: str,
    *,
    segment_id: str,
    profile_id: str | None,
    archetype_id: str,
    ranking: list[str],
    reaction: str = "same recorded rationale",
) -> dict:
    payload = {
        "synthetic_replicate_id": replicate_id,
        "segment_id": segment_id,
        "persona_archetype_id": archetype_id,
        "usable_complete_exposure_observation": True,
        "runtime_attempts": [{"attempt": 1}],
        "complete_set_evaluation": {
            "status": "ranked",
            "preference_ranking": ranking,
        },
        "per_creative_reactions": [
            {
                "variation_id": creative_id,
                "immediate_reaction": reaction,
                "judgment_status": "judged",
            }
            for creative_id in ranking
        ],
    }
    if profile_id is not None:
        payload["grounded_profile_id"] = profile_id
    return payload


def aggregate(records: list[dict], **overrides: object) -> dict:
    arguments = {
        "study_id": "profile-stratified-study",
        "creative_ids": ["A", "B"],
        "top_k": 1,
        "segment_weights": {"s1": 0.5, "s2": 0.5},
        "profile_weights": {
            "p1": 0.5,
            "p2": 0.5,
            "p3": 0.5,
            "p4": 0.5,
        },
        "seed": 41,
        "minimum_usable_records_per_profile": 1,
    }
    arguments.update(overrides)
    return aggregate_complete_exposure(records, **arguments)


class ProfileStratifiedCompleteExposureTests(unittest.TestCase):
    def test_single_profile_provisional_scope_marks_sensitivity_not_applicable(self):
        records = [
            response(
                f"p1-{index}",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
                reaction=f"distinct recorded rationale {index}",
            )
            for index in range(5)
        ]

        result = aggregate(
            records,
            segment_weights={"s1": 1.0},
            profile_weights={"p1": 1.0},
            minimum_usable_records_per_segment=1,
            minimum_usable_records_per_profile=5,
        )

        self.assertEqual("valid", result["validity_status"])
        self.assertEqual(["A"], result["proposed_finalist_ids"])
        self.assertTrue(result["grounded_profile_sensitivity"]["not_applicable"])
        self.assertTrue(result["archetype_sensitivity"]["not_applicable"])
        self.assertIn(
            "conditional on one locked grounded profile",
            " ".join(result["interpretation_limits"]),
        )

    def test_profile_then_segment_weighting_prevents_execution_count_bias(self):
        records = [
            response(
                f"p1-{index}",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
            )
            for index in range(5)
        ]
        records.extend(
            [
                response(
                    "p2-1",
                    segment_id="s1",
                    profile_id="p2",
                    archetype_id="arch-2",
                    ranking=["B", "A"],
                ),
                response(
                    "p3-1",
                    segment_id="s2",
                    profile_id="p3",
                    archetype_id="arch-3",
                    ranking=["A", "B"],
                ),
                response(
                    "p4-1",
                    segment_id="s2",
                    profile_id="p4",
                    archetype_id="arch-4",
                    ranking=["A", "B"],
                ),
            ]
        )

        result = aggregate(records)

        self.assertAlmostEqual(0.25, result["utilities"]["A"])
        self.assertAlmostEqual(-0.25, result["utilities"]["B"])
        self.assertEqual(
            {"p1": 5, "p2": 1, "p3": 1, "p4": 1},
            result["model_diagnostics"]["usable_observations_by_grounded_profile"],
        )
        self.assertEqual(
            "profile_then_segment_frozen_weights",
            result["model_diagnostics"]["weighting"]["method"],
        )

    def test_per_profile_usable_floor_is_a_resolution_gate(self):
        records = [
            response(
                "p1-1",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
            ),
            response(
                "p1-2",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
            ),
            response(
                "p2-1",
                segment_id="s1",
                profile_id="p2",
                archetype_id="arch-2",
                ranking=["A", "B"],
            ),
        ]

        result = aggregate(
            records,
            segment_weights={"s1": 1.0},
            profile_weights={"p1": 0.5, "p2": 0.5},
            minimum_usable_records_per_profile=2,
        )

        self.assertEqual("exploratory", result["validity_status"])
        self.assertEqual([], result["proposed_finalist_ids"])
        self.assertIn(
            "grounded_profile_usable_record_floor_not_met",
            result["validity_reasons"],
        )
        self.assertEqual(
            ["p2"],
            result["model_diagnostics"]["grounded_profiles_below_usable_floor"],
        )
        self.assertFalse(
            result["model_diagnostics"]["gates"]["grounded_profile_usable_record_floor"]
        )

    def test_bootstrap_is_profile_stratified_and_duplicate_adjusted(self):
        records = []
        for profile_id, segment_id, archetype_id in (
            ("p1", "s1", "arch-1"),
            ("p2", "s2", "arch-2"),
        ):
            records.extend(
                response(
                    f"{profile_id}-{index}",
                    segment_id=segment_id,
                    profile_id=profile_id,
                    archetype_id=archetype_id,
                    ranking=["A", "B"],
                )
                for index in range(3)
            )

        result = aggregate(
            records,
            profile_weights={"p1": 1.0, "p2": 1.0},
        )
        diagnostics = result["model_diagnostics"]

        self.assertEqual("locked_grounded_profile", diagnostics["bootstrap"]["stratification"])
        self.assertEqual(
            "whole_synthetic_execution_record",
            diagnostics["bootstrap"]["resample_unit"],
        )
        self.assertTrue(diagnostics["bootstrap"]["near_duplicate_cluster_adjusted"])
        duplicate = diagnostics["response_duplication"]
        self.assertEqual(6, duplicate["usable_record_count"])
        self.assertEqual(2, duplicate["effective_pattern_cluster_count"])
        self.assertEqual(
            {"p1": 1, "p2": 1},
            duplicate["effective_pattern_clusters_by_grounded_profile"],
        )
        self.assertEqual(
            {"p1": 2, "p2": 2},
            duplicate["records_redundant_within_grounded_profile"],
        )

    def test_near_duplicate_content_is_one_stability_resample_unit(self):
        shared = (
            "clear practical evidence with credible details about implementation "
            "timing adoption risk workflow value proof and operational tradeoffs "
            "for a careful buyer evaluating the full claim in context"
        )
        records = [
            response(
                "p1-1",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
                reaction=shared + " today",
            ),
            response(
                "p1-2",
                segment_id="s1",
                profile_id="p1",
                archetype_id="arch-1",
                ranking=["A", "B"],
                reaction=shared + " now",
            ),
            response(
                "p2-1",
                segment_id="s2",
                profile_id="p2",
                archetype_id="arch-2",
                ranking=["A", "B"],
                reaction="a separate profile rationale",
            ),
        ]

        result = aggregate(
            records,
            profile_weights={"p1": 1.0, "p2": 1.0},
        )
        duplicate = result["model_diagnostics"]["response_duplication"]

        self.assertEqual(1, duplicate["near_duplicate_pairs_by_grounded_profile"]["p1"])
        self.assertEqual(1, duplicate["effective_pattern_clusters_by_grounded_profile"]["p1"])
        self.assertEqual(2, result["model_diagnostics"]["bootstrap"]["effective_resample_units"])

    def test_grounded_profile_sensitivity_and_disagreement_are_distinct(self):
        records = []
        for profile_id, profile_count, ranking, archetype_id in (
            ("p1", 3, ["A", "B"], "consensus"),
            ("p2", 3, ["A", "B"], "consensus"),
            ("p3", 3, ["B", "A"], "dissent"),
        ):
            records.extend(
                response(
                    f"{profile_id}-{index}",
                    segment_id="s1",
                    profile_id=profile_id,
                    archetype_id=archetype_id,
                    ranking=ranking,
                    reaction=f"{profile_id} rationale",
                )
                for index in range(profile_count)
            )

        result = aggregate(
            records,
            segment_weights={"s1": 1.0},
            profile_weights={"p1": 0.3, "p2": 0.3, "p3": 0.4},
        )

        profile_sensitivity = result["grounded_profile_sensitivity"]
        archetype_sensitivity = result["archetype_sensitivity"]
        self.assertEqual("leave_one_grounded_profile_out", profile_sensitivity["method"])
        self.assertEqual("leave_one_persona_archetype_out", archetype_sensitivity["method"])
        self.assertIn("p1", profile_sensitivity["top_k_changed_for"])
        self.assertIn("p2", profile_sensitivity["top_k_changed_for"])
        disagreement = result["model_diagnostics"]["grounded_profile_disagreement"]
        self.assertTrue(disagreement["present"])
        self.assertEqual(2, disagreement["distinct_resolved_top_k_count"])
        self.assertTrue(disagreement["shortlist_fragile"])

    def test_calls_without_profile_weights_keep_v2_aggregation_path(self):
        records = []
        for segment_id, archetype_id in (("s1", "arch-1"), ("s2", "arch-2")):
            records.extend(
                response(
                    f"{segment_id}-{index}",
                    segment_id=segment_id,
                    profile_id=None,
                    archetype_id=archetype_id,
                    ranking=["A", "B"],
                )
                for index in range(8)
            )

        result = aggregate_complete_exposure(
            records,
            study_id="legacy-v2-study",
            creative_ids=["A", "B"],
            top_k=1,
            segment_weights={"s1": 0.5, "s2": 0.5},
            seed=17,
        )

        self.assertNotIn("grounded_profile_sensitivity", result)
        self.assertEqual(
            "locked_segment",
            result["model_diagnostics"]["bootstrap"]["stratification"],
        )
        self.assertEqual(
            "complete-exposure-calibration-v2",
            result["recovery_config_version"],
        )


if __name__ == "__main__":
    unittest.main()
