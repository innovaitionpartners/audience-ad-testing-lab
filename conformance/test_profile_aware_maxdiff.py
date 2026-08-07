import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "skills" / "audience-ad-testing-lab" / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from audience_lab.maxdiff import (  # noqa: E402
    compute_analysis_weights,
    profile_conditioned_connectivity,
    usable_participation_counts_by_profile,
)


AGGREGATE_SPEC = importlib.util.spec_from_file_location(
    "aggregate_screening_profile_contract",
    ROOT / "skills" / "audience-ad-testing-lab" / "scripts" / "aggregate-screening.py",
)
assert AGGREGATE_SPEC is not None and AGGREGATE_SPEC.loader is not None
AGGREGATE_MODULE = importlib.util.module_from_spec(AGGREGATE_SPEC)
AGGREGATE_SPEC.loader.exec_module(AGGREGATE_MODULE)


def observation(
    record_id: str,
    profile_id: str,
    block: tuple[str, str, str, str],
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "segment_id": "segment-a",
        "grounded_profile_id": profile_id,
        "persona_archetype_id": f"archetype-{profile_id}",
        "block": list(block),
        "best": block[0],
        "worst": block[-1],
    }


class ProfileAwareMaxDiffTests(unittest.TestCase):
    def test_v3_profile_contract_derives_planned_counts_from_frozen_jobs(self) -> None:
        manifest = {
            "audience_profile_rosters": {
                "screening": {
                    "profile_diagnostics": [
                        {"grounded_profile_id": "profile-a", "target_weight": 0.5},
                        {"grounded_profile_id": "profile-b", "target_weight": 0.5},
                    ]
                }
            }
        }
        jobs = {
            "synthetic_replicate_jobs": [
                {
                    "grounded_profile_id": "profile-a",
                    "variation_ids": ["A", "B", "C", "D"],
                },
                {
                    "grounded_profile_id": "profile-b",
                    "variation_ids": ["A", "B", "D", "E"],
                },
            ]
        }

        weights, planned = AGGREGATE_MODULE._partial_profile_contract(
            manifest,
            jobs,
            ("A", "B", "C", "D", "E"),
        )

        self.assertEqual({"profile-a": 0.5, "profile-b": 0.5}, weights)
        self.assertEqual(1, planned["profile-a"]["C"])
        self.assertEqual(0, planned["profile-a"]["E"])
        self.assertEqual(0, planned["profile-b"]["C"])
        self.assertEqual(1, planned["profile-b"]["E"])

    def test_v3_profile_contract_rejects_unbound_profile_jobs(self) -> None:
        manifest = {
            "audience_profile_rosters": {
                "screening": {
                    "profile_diagnostics": [
                        {"grounded_profile_id": "profile-a", "target_weight": 1.0}
                    ]
                }
            }
        }
        jobs = {
            "synthetic_replicate_jobs": [
                {"variation_ids": ["A", "B", "C", "D"]}
            ]
        }

        with self.assertRaisesRegex(ValueError, "frozen grounded profile"):
            AGGREGATE_MODULE._partial_profile_contract(
                manifest,
                jobs,
                ("A", "B", "C", "D"),
            )

    def test_analysis_weights_preserve_frozen_profile_mix(self) -> None:
        records = [
            observation("a-1", "profile-a", ("A", "B", "C", "D")),
            observation("a-2", "profile-a", ("A", "B", "C", "D")),
            observation("a-3", "profile-a", ("A", "B", "C", "D")),
            observation("b-1", "profile-b", ("A", "B", "C", "D")),
        ]

        weights = compute_analysis_weights(
            records,
            {"segment-a": 1.0},
            profile_weights={"profile-a": 0.5, "profile-b": 0.5},
        )

        self.assertAlmostEqual(2.0, sum(weights[:3]))
        self.assertAlmostEqual(2.0, weights[3])
        self.assertAlmostEqual(1.0, sum(weights) / len(weights))

    def test_connectivity_must_survive_removal_of_any_grounded_profile(self) -> None:
        connected = [
            observation("a-1", "profile-a", ("A", "B", "C", "D")),
            observation("b-1", "profile-b", ("A", "B", "C", "D")),
        ]
        disconnected_after_removal = [
            observation("a-1", "profile-a", ("A", "B", "C", "D")),
            observation("b-1", "profile-b", ("A", "B", "C", "E")),
        ]

        passing = profile_conditioned_connectivity(connected, ("A", "B", "C", "D"))
        failing = profile_conditioned_connectivity(
            disconnected_after_removal,
            ("A", "B", "C", "D", "E"),
        )

        self.assertTrue(passing["survives_any_one_profile_removal"])
        self.assertFalse(failing["survives_any_one_profile_removal"])
        self.assertIn("profile-a", failing["disconnected_after_omitting"])

    def test_usable_participations_are_reported_inside_each_profile(self) -> None:
        records = [
            observation("a-1", "profile-a", ("A", "B", "C", "D")),
            observation("a-2", "profile-a", ("A", "B", "C", "E")),
            observation("b-1", "profile-b", ("A", "B", "D", "E")),
        ]

        counts = usable_participation_counts_by_profile(
            records,
            ("A", "B", "C", "D", "E"),
        )

        self.assertEqual(2, counts["profile-a"]["A"])
        self.assertEqual(1, counts["profile-a"]["E"])
        self.assertEqual(0, counts["profile-b"]["C"])


if __name__ == "__main__":
    unittest.main()
