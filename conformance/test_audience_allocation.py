from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_allocation import (  # noqa: E402
    ALLOCATION_FIDELITY_STATUSES,
    ALLOCATION_PLAN_VERSION,
    ALLOCATION_REQUEST_VERSION,
    ALLOCATION_SUBSET_VERSION,
    allocate_stage_profiles,
    evaluate_allocation_subset,
    validate_allocation_plan,
    validate_allocation_request,
    validate_allocation_subset,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64


def profile(
    profile_id: str,
    segment_id: str,
    group_id: str,
    weight: float,
    conditional_weight: float,
    *,
    must_cover: tuple[str, ...] = (),
    snapshot_hash: str = HASH_A,
    eligible: bool = True,
) -> dict[str, object]:
    return {
        "grounded_profile_id": profile_id,
        "reported_segment_id": segment_id,
        "structural_group_id": group_id,
        "effective_weight": weight,
        "conditional_effective_weight": conditional_weight,
        "must_cover_group_ids": list(must_cover),
        "profile_snapshot_sha256": snapshot_hash,
        "eligible": eligible,
    }


def request(
    *,
    stage: str = "screening",
    slots: list[dict[str, object]] | None = None,
    profiles: list[dict[str, object]] | None = None,
    analysis_weights: dict[str, float] | None = None,
    must_cover: tuple[str, ...] = (),
    basis: str = "directional_planning",
    allow_directional: bool = False,
    seed: str = "allocation-seed",
) -> dict[str, object]:
    if slots is None:
        slots = [
            {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
            {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
        ]
    if profiles is None:
        profiles = [
            profile("profile-a", "segment-a", "group-a", 0.5, 0.5, snapshot_hash=HASH_A),
            profile("profile-b", "segment-a", "group-b", 0.5, 0.5, snapshot_hash=HASH_B),
        ]
    if analysis_weights is None:
        analysis_weights = {} if stage == "finalist" else {"segment-a": 1.0}
    return {
        "schema_version": ALLOCATION_REQUEST_VERSION,
        "stage": stage,
        "stage_roster_id": "roster-001",
        "stable_seed": seed,
        "allocation_basis": basis,
        "slots": slots,
        "profiles": profiles,
        "analysis_weights": analysis_weights,
        "must_cover_group_ids": list(must_cover),
        "maximum_absolute_deviation": 0.05,
        "allow_directional_allocation": allow_directional,
    }


def assignments_by_profile(plan: dict[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for assignment in plan["assignments"]:
        profile_id = assignment["grounded_profile_id"]
        result[profile_id] = result.get(profile_id, 0) + 1
    return result


def diagnostics_by_id(
    records: list[dict[str, object]],
    key: str,
) -> dict[str, dict[str, object]]:
    return {record[key]: record for record in records}


def canonical_sha256(value: object) -> str:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def brute_force_objective(payload: dict[str, object]) -> tuple[float, int, float]:
    """Independent exhaustive oracle for small requests."""

    slots = payload["slots"]
    profiles = [item for item in payload["profiles"] if item["eligible"]]
    choices: list[list[dict[str, object]]] = []
    for slot in slots:
        if payload["stage"] == "finalist":
            choices.append(profiles)
        else:
            choices.append(
                [
                    item
                    for item in profiles
                    if item["reported_segment_id"] == slot["reported_segment_id"]
                ]
            )
    candidates = list(itertools.product(*choices))

    if payload["stage"] == "finalist":
        total = sum(item["effective_weight"] for item in profiles)
        target = {
            item["grounded_profile_id"]: item["effective_weight"] / total
            for item in profiles
        }
        ideal = {
            profile_id: len(slots) * weight
            for profile_id, weight in target.items()
        }
    else:
        target = {}
        ideal = {}
        for segment_id, analysis_weight in payload["analysis_weights"].items():
            segment_profiles = [
                item for item in profiles if item["reported_segment_id"] == segment_id
            ]
            segment_slots = [
                item for item in slots if item["reported_segment_id"] == segment_id
            ]
            total = sum(
                item["conditional_effective_weight"] for item in segment_profiles
            )
            for item in segment_profiles:
                conditional = item["conditional_effective_weight"] / total
                target[item["grounded_profile_id"]] = analysis_weight * conditional
                ideal[item["grounded_profile_id"]] = len(segment_slots) * conditional

    group_target = {
        group_id: sum(
            target[item["grounded_profile_id"]]
            for item in profiles
            if group_id in item["must_cover_group_ids"]
        )
        for group_id in payload["must_cover_group_ids"]
    }
    matched_floors = brute_force_matching(payload)
    anchors = {
        slot_id: profile_id
        for _digest, _group_id, slot_id, profile_id in matched_floors
    }
    scored: list[float] = []
    for roster in candidates:
        assignment_by_slot = {
            slot["slot_id"]: selected
            for slot, selected in zip(slots, roster)
        }
        if any(
            assignment_by_slot[slot_id]["grounded_profile_id"] != profile_id
            for slot_id, profile_id in anchors.items()
        ):
            continue
        counts = {
            item["grounded_profile_id"]: sum(
                selected["grounded_profile_id"] == item["grounded_profile_id"]
                for selected in roster
            )
            for item in profiles
        }
        scored.append(
            sum(
                abs(counts[profile_id] - ideal[profile_id])
                for profile_id in ideal
            )
        )
    matched_groups = [item[1] for item in matched_floors]
    return (
        sum(group_target[group_id] for group_id in matched_groups),
        len(matched_groups),
        min(scored),
    )


def brute_force_matching(
    payload: dict[str, object],
) -> tuple[tuple[str, str, str, str], ...]:
    """Independent exhaustive oracle for weighted must-cover floor matching."""

    profiles = [item for item in payload["profiles"] if item["eligible"]]
    if payload["stage"] == "finalist":
        total = sum(item["effective_weight"] for item in profiles)
        profile_target = {
            item["grounded_profile_id"]: item["effective_weight"] / total
            for item in profiles
        }
    else:
        profile_target = {}
        for segment_id, analysis_weight in payload["analysis_weights"].items():
            segment_profiles = [
                item for item in profiles if item["reported_segment_id"] == segment_id
            ]
            total = sum(
                item["conditional_effective_weight"] for item in segment_profiles
            )
            for item in segment_profiles:
                profile_target[item["grounded_profile_id"]] = (
                    analysis_weight
                    * item["conditional_effective_weight"]
                    / total
                )
    group_target = {
        group_id: sum(
            profile_target[item["grounded_profile_id"]]
            for item in profiles
            if group_id in item["must_cover_group_ids"]
        )
        for group_id in payload["must_cover_group_ids"]
    }
    options = []
    for group_id in payload["must_cover_group_ids"]:
        edges: list[tuple[str, str, str, str] | None] = [None]
        for slot in payload["slots"]:
            for item in profiles:
                if group_id not in item["must_cover_group_ids"]:
                    continue
                if (
                    payload["stage"] != "finalist"
                    and slot["reported_segment_id"] != item["reported_segment_id"]
                ):
                    continue
                digest = hashlib.sha256(
                    (
                        payload["stable_seed"]
                        + "\0"
                        + payload["stage_roster_id"]
                        + "\0"
                        + slot["slot_id"]
                        + "\0"
                        + item["grounded_profile_id"]
                    ).encode("utf-8")
                ).hexdigest()
                edges.append(
                    (
                        digest,
                        group_id,
                        slot["slot_id"],
                        item["grounded_profile_id"],
                    )
                )
        options.append(edges)

    feasible: list[
        tuple[float, int, tuple[tuple[str, str, str, str], ...]]
    ] = []
    for raw_selection in itertools.product(*options):
        selection = tuple(item for item in raw_selection if item is not None)
        slots = [item[2] for item in selection]
        if len(slots) != len(set(slots)):
            continue
        feasible.append(
            (
                sum(group_target[item[1]] for item in selection),
                len(selection),
                tuple(sorted(selection)),
            )
        )
    best_weight = max(item[0] for item in feasible)
    best_count = max(
        item[1] for item in feasible if math.isclose(item[0], best_weight, abs_tol=1e-12)
    )
    return min(
        item[2]
        for item in feasible
        if math.isclose(item[0], best_weight, abs_tol=1e-12)
        and item[1] == best_count
    )


class AllocationRequestValidationTest(unittest.TestCase):
    def test_request_validator_is_strict_and_returns_an_independent_copy(self) -> None:
        payload = request()
        validated = validate_allocation_request(payload)
        self.assertEqual(payload, validated)
        self.assertIsNot(payload, validated)

        for mutation in (
            lambda item: item.update({"extra": True}),
            lambda item: item["slots"].append(copy.deepcopy(item["slots"][0])),
            lambda item: item["profiles"].append(copy.deepcopy(item["profiles"][0])),
            lambda item: item["profiles"][0].update({"effective_weight": math.nan}),
            lambda item: item["profiles"][0].update(
                {"conditional_effective_weight": math.inf}
            ),
            lambda item: item["profiles"][0].pop("profile_snapshot_sha256"),
            lambda item: item["profiles"][0].update(
                {"profile_snapshot_sha256": "sha256:not-a-hash"}
            ),
        ):
            invalid = copy.deepcopy(payload)
            mutation(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_allocation_request(invalid)

    def test_stage_segment_contracts_fail_closed(self) -> None:
        screening = request()
        screening["analysis_weights"] = {"unknown-segment": 1.0}
        with self.assertRaises(ValueError):
            validate_allocation_request(screening)

        finalist = request(
            stage="finalist",
            slots=[{"slot_id": "finalist-01", "reported_segment_id": "segment-a"}],
        )
        with self.assertRaises(ValueError):
            validate_allocation_request(finalist)


class StageAllocationTest(unittest.TestCase):
    def test_reusable_tier_one_is_directional_without_a_frame_gate(self) -> None:
        payload = request(
            slots=[
                {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
                {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
            ],
            must_cover=("group-a", "group-b"),
            profiles=[
                profile(
                    "profile-a",
                    "segment-a",
                    "group-a",
                    0.5,
                    0.5,
                    must_cover=("group-a",),
                    snapshot_hash=HASH_A,
                ),
                profile(
                    "profile-b",
                    "segment-a",
                    "group-b",
                    0.5,
                    0.5,
                    must_cover=("group-b",),
                    snapshot_hash=HASH_B,
                ),
            ],
        )
        original = copy.deepcopy(payload)
        plan = allocate_stage_profiles(payload)

        self.assertEqual(original, payload)
        self.assertEqual(ALLOCATION_PLAN_VERSION, plan["schema_version"])
        self.assertEqual("directional_profile_allocation", plan["fidelity"]["status"])
        self.assertEqual("directional_tier_1_for_this_run", plan["claim_effect"])
        self.assertEqual([], plan["must_cover_diagnostics"]["uncovered_group_ids"])
        self.assertEqual(2, len(plan["assignments"]))
        self.assertEqual(
            {HASH_A, HASH_B},
            {item["profile_snapshot_sha256"] for item in plan["assignments"]},
        )
        self.assertEqual(
            ["slot-02", "slot-01"],
            [item["slot_id"] for item in plan["assignments"]],
        )
        self.assertEqual(plan, validate_allocation_plan(plan))

    def test_directional_numeric_deviation_never_becomes_distorted(self) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
                profiles=[
                    profile(
                        "profile-a", "segment-a", "group-a", 0.9499999999, 0.9499999999
                    ),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-b",
                        0.0500000001,
                        0.0500000001,
                        snapshot_hash=HASH_B,
                    ),
                ],
            )
        )
        self.assertEqual("directional_profile_allocation", plan["fidelity"]["status"])
        self.assertGreater(
            plan["fidelity"]["observed_maximum_absolute_deviation"],
            0.05,
        )
        self.assertEqual("directional_tier_1_for_this_run", plan["claim_effect"])

    def test_tier_one_missing_must_cover_group_requires_an_explicit_choice(self) -> None:
        payload = request(
            slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
            must_cover=("group-light", "group-heavy"),
            profiles=[
                profile(
                    "light",
                    "segment-a",
                    "group-light",
                    0.2,
                    0.2,
                    must_cover=("group-light",),
                    snapshot_hash=HASH_A,
                ),
                profile(
                    "heavy",
                    "segment-a",
                    "group-heavy",
                    0.8,
                    0.8,
                    must_cover=("group-heavy",),
                    snapshot_hash=HASH_B,
                ),
            ],
        )
        blocked = allocate_stage_profiles(payload)
        self.assertEqual(["group-light"], blocked["must_cover_diagnostics"]["uncovered_group_ids"])
        self.assertEqual("requires_user_decision", blocked["claim_effect"])

        payload["allow_directional_allocation"] = True
        continued = allocate_stage_profiles(payload)
        self.assertEqual(
            "directional_tier_1_for_this_run",
            continued["claim_effect"],
        )

    def test_conditional_weights_allocate_within_each_existing_segment(self) -> None:
        slots = [
            {"slot_id": f"a-{index}", "reported_segment_id": "segment-a"}
            for index in range(1, 4)
        ] + [
            {"slot_id": f"b-{index}", "reported_segment_id": "segment-b"}
            for index in range(1, 3)
        ]
        for stage in ("screening", "boundary"):
            with self.subTest(stage=stage):
                plan = allocate_stage_profiles(
                    request(
                        stage=stage,
                        slots=slots,
                        profiles=[
                            profile("a-major", "segment-a", "group-a-major", 0.2, 0.8),
                            profile(
                                "a-minor",
                                "segment-a",
                                "group-a-minor",
                                0.05,
                                0.2,
                                snapshot_hash=HASH_B,
                            ),
                            profile(
                                "b-minor",
                                "segment-b",
                                "group-b-minor",
                                0.075,
                                0.1,
                                snapshot_hash=HASH_C,
                            ),
                            profile(
                                "b-major",
                                "segment-b",
                                "group-b-major",
                                0.675,
                                0.9,
                                snapshot_hash=HASH_D,
                            ),
                        ],
                        analysis_weights={"segment-a": 0.25, "segment-b": 0.75},
                    )
                )
                self.assertEqual(
                    {"a-major": 2, "a-minor": 1, "b-major": 2},
                    assignments_by_profile(plan),
                )
                for assignment in plan["assignments"]:
                    if assignment["slot_id"].startswith("a-"):
                        self.assertTrue(
                            assignment["grounded_profile_id"].startswith("a-")
                        )
                    else:
                        self.assertTrue(
                            assignment["grounded_profile_id"].startswith("b-")
                        )

    def test_finalists_allocate_globally_and_emit_the_profiles_actual_segment(self) -> None:
        plan = allocate_stage_profiles(
            request(
                stage="finalist",
                slots=[
                    {"slot_id": f"finalist-{index}", "reported_segment_id": None}
                    for index in range(1, 5)
                ],
                profiles=[
                    profile("profile-a", "segment-a", "group-a", 0.75, 1.0),
                    profile(
                        "profile-b",
                        "segment-b",
                        "group-b",
                        0.25,
                        1.0,
                        snapshot_hash=HASH_B,
                    ),
                ],
                analysis_weights={},
            )
        )
        self.assertEqual({"profile-a": 3, "profile-b": 1}, assignments_by_profile(plan))
        expected_segments = {"profile-a": "segment-a", "profile-b": "segment-b"}
        self.assertTrue(
            all(
                item["reported_segment_id"]
                == expected_segments[item["grounded_profile_id"]]
                for item in plan["assignments"]
            )
        )

    def test_matching_does_not_greedily_consume_the_only_slot_for_another_group(
        self,
    ) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-a", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-b", "reported_segment_id": "segment-b"},
                ],
                profiles=[
                    profile(
                        "flex-a",
                        "segment-a",
                        "group-flex",
                        0.25,
                        0.5,
                        must_cover=("group-flex",),
                    ),
                    profile(
                        "flex-b",
                        "segment-b",
                        "group-flex",
                        0.25,
                        1.0,
                        must_cover=("group-flex",),
                        snapshot_hash=HASH_B,
                    ),
                    profile(
                        "only-a",
                        "segment-a",
                        "group-only",
                        0.5,
                        0.5,
                        must_cover=("group-only",),
                        snapshot_hash=HASH_C,
                    ),
                ],
                analysis_weights={"segment-a": 0.5, "segment-b": 0.5},
                must_cover=("group-flex", "group-only"),
            )
        )
        self.assertEqual([], plan["must_cover_diagnostics"]["uncovered_group_ids"])
        self.assertEqual(
            {"slot-a": "only-a", "slot-b": "flex-b"},
            {
                item["slot_id"]: item["grounded_profile_id"]
                for item in plan["assignments"]
            },
        )

    def test_zero_weight_group_is_covered_after_positive_weight_groups(self) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
                ],
                profiles=[
                    profile(
                        "positive",
                        "segment-a",
                        "positive-group",
                        1.0,
                        1.0,
                        must_cover=("positive-group",),
                    ),
                    profile(
                        "zero",
                        "segment-a",
                        "zero-group",
                        0.0,
                        0.0,
                        must_cover=("zero-group",),
                        snapshot_hash=HASH_B,
                    ),
                ],
                must_cover=("positive-group", "zero-group"),
            )
        )
        self.assertEqual(
            {"positive": 1, "zero": 1},
            assignments_by_profile(plan),
        )
        self.assertEqual([], plan["must_cover_diagnostics"]["uncovered_group_ids"])

    def test_multi_group_profiles_require_one_distinct_matched_slot_per_group(
        self,
    ) -> None:
        shared_profile = profile(
            "shared-profile",
            "segment-a",
            "shared-structural-group",
            1.0,
            1.0,
            must_cover=("group-1", "group-2"),
        )
        one_slot = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"}
                ],
                profiles=[shared_profile],
                must_cover=("group-1", "group-2"),
            )
        )
        matched_group = one_slot["must_cover_diagnostics"]["matches"][0][
            "must_cover_group_id"
        ]
        self.assertEqual(1, len(one_slot["must_cover_diagnostics"]["matches"]))
        self.assertEqual(
            [matched_group],
            one_slot["must_cover_diagnostics"]["covered_group_ids"],
        )
        self.assertEqual(
            [
                group_id
                for group_id in ("group-1", "group-2")
                if group_id != matched_group
            ],
            one_slot["must_cover_diagnostics"]["uncovered_group_ids"],
        )
        self.assertEqual("requires_user_decision", one_slot["claim_effect"])

        two_slots = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
                ],
                profiles=[shared_profile],
                must_cover=("group-1", "group-2"),
            )
        )
        self.assertEqual(2, len(two_slots["must_cover_diagnostics"]["matches"]))
        self.assertEqual(
            {"slot-01", "slot-02"},
            {
                item["slot_id"]
                for item in two_slots["must_cover_diagnostics"]["matches"]
            },
        )
        self.assertEqual(
            ["group-1", "group-2"],
            two_slots["must_cover_diagnostics"]["covered_group_ids"],
        )
        self.assertEqual(
            [],
            two_slots["must_cover_diagnostics"]["uncovered_group_ids"],
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            two_slots["claim_effect"],
        )

    def test_ineligible_profile_is_never_assigned_but_its_must_cover_gap_survives(
        self,
    ) -> None:
        plan = allocate_stage_profiles(
            request(
                must_cover=("blocked-group",),
                profiles=[
                    profile("eligible", "segment-a", "eligible-group", 1.0, 1.0),
                    profile(
                        "blocked",
                        "segment-a",
                        "blocked-group",
                        0.0,
                        0.0,
                        must_cover=("blocked-group",),
                        snapshot_hash=HASH_B,
                        eligible=False,
                    ),
                ],
            )
        )
        self.assertEqual({"eligible": 2}, assignments_by_profile(plan))
        self.assertEqual(
            ["blocked-group"],
            plan["must_cover_diagnostics"]["uncovered_group_ids"],
        )
        self.assertEqual("requires_user_decision", plan["claim_effect"])

    def test_allocator_never_increases_capacity_and_preserves_input_weights(self) -> None:
        payload = request(
            slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
            must_cover=("group-a", "group-b"),
            profiles=[
                profile(
                    "profile-a",
                    "segment-a",
                    "group-a",
                    0.6,
                    0.6,
                    must_cover=("group-a",),
                ),
                profile(
                    "profile-b",
                    "segment-a",
                    "group-b",
                    0.4,
                    0.4,
                    must_cover=("group-b",),
                    snapshot_hash=HASH_B,
                ),
            ],
        )
        before = copy.deepcopy(payload["profiles"])
        plan = allocate_stage_profiles(payload)
        self.assertEqual(1, len(plan["assignments"]))
        self.assertEqual(before, payload["profiles"])

    def test_deficit_balancing_keeps_prefixes_close_to_target(self) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": f"slot-{index:02d}", "reported_segment_id": "segment-a"}
                    for index in range(1, 6)
                ],
                profiles=[
                    profile("profile-a", "segment-a", "group-a", 0.6, 0.6),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-b",
                        0.4,
                        0.4,
                        snapshot_hash=HASH_B,
                    ),
                ],
            )
        )
        self.assertEqual(
            ["profile-a", "profile-b", "profile-a", "profile-b", "profile-a"],
            [item["grounded_profile_id"] for item in plan["assignments"]],
        )

    def test_structural_frame_accepts_the_exact_five_point_boundary(self) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
                profiles=[
                    profile(
                        "profile-a",
                        "segment-a",
                        "group-a",
                        0.95,
                        0.95,
                        must_cover=("group-a",),
                    ),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-b",
                        0.05,
                        0.05,
                        snapshot_hash=HASH_B,
                    ),
                ],
                must_cover=("group-a",),
                basis="structural_frame",
            )
        )
        self.assertEqual("frame_aligned", plan["fidelity"]["status"])
        self.assertLessEqual(
            plan["fidelity"]["observed_maximum_absolute_deviation"],
            0.05,
        )
        self.assertEqual("frame_aligned", plan["claim_effect"])

    def test_structural_frame_distorts_at_five_points_plus_one_ten_billionth(
        self,
    ) -> None:
        payload = request(
            slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
            profiles=[
                profile(
                    "profile-a",
                    "segment-a",
                    "group-a",
                    0.9499999999,
                    0.9499999999,
                    must_cover=("group-a",),
                ),
                profile(
                    "profile-b",
                    "segment-a",
                    "group-b",
                    0.0500000001,
                    0.0500000001,
                    snapshot_hash=HASH_B,
                ),
            ],
            must_cover=("group-a",),
            basis="structural_frame",
        )
        blocked = allocate_stage_profiles(payload)
        self.assertEqual("allocation_distorted", blocked["fidelity"]["status"])
        self.assertGreater(
            blocked["fidelity"]["observed_maximum_absolute_deviation"],
            0.05,
        )
        self.assertEqual("requires_user_decision", blocked["claim_effect"])

        payload["allow_directional_allocation"] = True
        continued = allocate_stage_profiles(payload)
        self.assertEqual(
            "directional_tier_1_for_this_run",
            continued["claim_effect"],
        )

    def test_seed_is_the_final_tie_break_for_equal_profiles(self) -> None:
        first = allocate_stage_profiles(
            request(
                slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
                seed="seed-a",
            )
        )
        second = allocate_stage_profiles(
            request(
                slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
                seed="seed-e",
            )
        )
        self.assertEqual("profile-a", first["assignments"][0]["grounded_profile_id"])
        self.assertEqual("profile-b", second["assignments"][0]["grounded_profile_id"])


class BruteForceAllocationOracleTest(unittest.TestCase):
    def test_small_allocations_match_independent_coverage_and_deviation_oracle(
        self,
    ) -> None:
        cases = [
            request(
                slots=[
                    {"slot_id": "a-1", "reported_segment_id": "segment-a"},
                    {"slot_id": "a-2", "reported_segment_id": "segment-a"},
                    {"slot_id": "b-1", "reported_segment_id": "segment-b"},
                ],
                profiles=[
                    profile(
                        "a-flex",
                        "segment-a",
                        "flex",
                        0.2,
                        0.4,
                        must_cover=("flex",),
                    ),
                    profile(
                        "b-flex",
                        "segment-b",
                        "flex",
                        0.3,
                        1.0,
                        must_cover=("flex",),
                        snapshot_hash=HASH_B,
                    ),
                    profile(
                        "a-only",
                        "segment-a",
                        "only",
                        0.3,
                        0.6,
                        must_cover=("only",),
                        snapshot_hash=HASH_C,
                    ),
                ],
                analysis_weights={"segment-a": 0.5, "segment-b": 0.5},
                must_cover=("flex", "only"),
            ),
            request(
                stage="finalist",
                slots=[
                    {"slot_id": f"f-{index}", "reported_segment_id": None}
                    for index in range(1, 9)
                ],
                profiles=[
                    profile("p1", "s1", "g1", 0.45, 1.0, must_cover=("g1",)),
                    profile(
                        "p2",
                        "s1",
                        "g2",
                        0.25,
                        1.0,
                        must_cover=("g2",),
                        snapshot_hash=HASH_B,
                    ),
                    profile(
                        "p3",
                        "s2",
                        "g3",
                        0.2,
                        1.0,
                        must_cover=("g3",),
                        snapshot_hash=HASH_C,
                    ),
                    profile("p4", "s2", "g4", 0.1, 1.0, snapshot_hash=HASH_D),
                ],
                analysis_weights={},
                must_cover=("g1", "g2", "g3"),
            ),
        ]
        for payload in cases:
            with self.subTest(stage=payload["stage"], slots=len(payload["slots"])):
                expected_weight, expected_coverage, expected_deviation = (
                    brute_force_objective(payload)
                )
                plan = allocate_stage_profiles(payload)
                group_diags = diagnostics_by_id(
                    plan["structural_group_diagnostics"],
                    "structural_group_id",
                )
                covered = plan["must_cover_diagnostics"]["covered_group_ids"]
                actual_weight = sum(
                    plan["must_cover_diagnostics"]["target_weights"][group_id]
                    for group_id in covered
                )
                profile_diags = diagnostics_by_id(
                    plan["profile_diagnostics"],
                    "grounded_profile_id",
                )
                actual_deviation = sum(
                    abs(
                        diagnostic["assigned_slots"]
                        - diagnostic["ideal_slot_count"]
                    )
                    for diagnostic in profile_diags.values()
                    if diagnostic["eligible"]
                )
                self.assertAlmostEqual(expected_weight, actual_weight)
                self.assertEqual(expected_coverage, len(covered))
                self.assertAlmostEqual(expected_deviation, actual_deviation)
                self.assertEqual(
                    len(payload["slots"]),
                    sum(item["assigned_slots"] for item in group_diags.values()),
                )

    def test_weighted_matching_and_stable_tie_break_match_exhaustive_oracle(
        self,
    ) -> None:
        cases = [
            request(
                slots=[
                    {"slot_id": "slot-a", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-b", "reported_segment_id": "segment-b"},
                ],
                profiles=[
                    profile(
                        "flex-a",
                        "segment-a",
                        "group-flex",
                        0.25,
                        0.5,
                        must_cover=("group-flex",),
                    ),
                    profile(
                        "flex-b",
                        "segment-b",
                        "group-flex",
                        0.25,
                        1.0,
                        must_cover=("group-flex",),
                        snapshot_hash=HASH_B,
                    ),
                    profile(
                        "only-a",
                        "segment-a",
                        "group-only",
                        0.5,
                        0.5,
                        must_cover=("group-only",),
                        snapshot_hash=HASH_C,
                    ),
                ],
                analysis_weights={"segment-a": 0.5, "segment-b": 0.5},
                must_cover=("group-flex", "group-only"),
            ),
            request(
                slots=[{"slot_id": "slot-01", "reported_segment_id": "segment-a"}],
                profiles=[
                    profile(
                        "profile-a",
                        "segment-a",
                        "group-a",
                        0.5,
                        0.5,
                        must_cover=("group-a",),
                    ),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-b",
                        0.5,
                        0.5,
                        must_cover=("group-b",),
                        snapshot_hash=HASH_B,
                    ),
                ],
                must_cover=("group-a", "group-b"),
                seed="equal-group-priority",
            ),
        ]
        for payload in cases:
            with self.subTest(slots=len(payload["slots"])):
                expected = brute_force_matching(payload)
                plan = allocate_stage_profiles(payload)
                actual = tuple(
                    sorted(
                        (
                            item["stable_order_sha256"].removeprefix("sha256:"),
                            item["must_cover_group_id"],
                            item["slot_id"],
                            item["grounded_profile_id"],
                        )
                        for item in plan["must_cover_diagnostics"]["matches"]
                    )
                )
                self.assertEqual(expected, actual)


class AllocationSubsetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directional_request = request(
            slots=[
                {"slot_id": f"slot-{index:02d}", "reported_segment_id": "segment-a"}
                for index in range(1, 5)
            ],
            profiles=[
                profile(
                    "profile-a",
                    "segment-a",
                    "group-a",
                    0.5,
                    0.5,
                    must_cover=("group-a",),
                ),
                profile(
                    "profile-b",
                    "segment-a",
                    "group-b",
                    0.5,
                    0.5,
                    must_cover=("group-b",),
                    snapshot_hash=HASH_B,
                ),
            ],
            must_cover=("group-a", "group-b"),
        )
        self.directional_plan = allocate_stage_profiles(self.directional_request)

    def test_exact_prefix_is_a_read_only_projection_bound_to_the_full_plan(self) -> None:
        original = copy.deepcopy(self.directional_plan)
        selected = [
            item["slot_id"] for item in self.directional_plan["assignments"][:2]
        ]
        subset = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=selected,
            allow_directional_allocation=False,
        )
        self.assertEqual(original, self.directional_plan)
        self.assertEqual(ALLOCATION_SUBSET_VERSION, subset["schema_version"])
        self.assertEqual(canonical_sha256(self.directional_plan), subset["full_plan_sha256"])
        self.assertEqual(selected, subset["selected_slot_ids"])
        self.assertEqual("directional_profile_allocation", subset["fidelity"]["status"])
        self.assertEqual(
            subset,
            validate_allocation_subset(subset, plan=self.directional_plan),
        )

    def test_subset_validation_requires_the_exact_frozen_plan_and_hash(self) -> None:
        selected = [
            item["slot_id"] for item in self.directional_plan["assignments"][:2]
        ]
        subset = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=selected,
            allow_directional_allocation=False,
        )
        self.assertEqual(
            subset,
            validate_allocation_subset(subset, plan=self.directional_plan),
        )
        with self.assertRaises(TypeError):
            validate_allocation_subset(subset)

        wrong_request = copy.deepcopy(self.directional_request)
        wrong_request["stable_seed"] = "wrong-plan-seed"
        wrong_plan = allocate_stage_profiles(wrong_request)
        with self.assertRaises(ValueError):
            validate_allocation_subset(subset, plan=wrong_plan)

        tampered_plan = copy.deepcopy(self.directional_plan)
        tampered_plan["assignments"][0]["profile_snapshot_sha256"] = HASH_C
        with self.assertRaises(ValueError):
            validate_allocation_subset(subset, plan=tampered_plan)

        mismatched_hash = copy.deepcopy(subset)
        mismatched_hash["full_plan_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            validate_allocation_subset(
                mismatched_hash,
                plan=self.directional_plan,
            )

    def test_plan_binding_rejects_resealed_and_altered_must_cover_matches(
        self,
    ) -> None:
        forge_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": f"slot-{index:02d}", "reported_segment_id": "segment-a"}
                    for index in range(1, 4)
                ],
                profiles=[
                    profile(
                        "profile-a",
                        "segment-a",
                        "group-a",
                        1.0,
                        1.0,
                        must_cover=("group-a",),
                    )
                ],
                must_cover=("group-a",),
                seed="forge-0",
            )
        )
        self.assertEqual(
            "slot-02",
            forge_plan["must_cover_diagnostics"]["matches"][0]["slot_id"],
        )
        honest_prefix = evaluate_allocation_subset(
            forge_plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=False,
        )
        forged = copy.deepcopy(honest_prefix)
        stable_hash = "sha256:" + hashlib.sha256(
            (
                "forge-0"
                + "\0roster-001"
                + "\0slot-01"
                + "\0profile-a"
            ).encode("utf-8")
        ).hexdigest()
        forged["must_cover_diagnostics"]["matches"] = [
            {
                "must_cover_group_id": "group-a",
                "slot_id": "slot-01",
                "grounded_profile_id": "profile-a",
                "target_weight": 1.0,
                "stable_order_sha256": stable_hash,
            }
        ]
        forged["must_cover_diagnostics"]["covered_group_ids"] = ["group-a"]
        forged["must_cover_diagnostics"]["uncovered_group_ids"] = []
        forged["profile_diagnostics"][0]["matching_floor"] = 1
        forged["fidelity"]["all_must_cover_groups_represented"] = True
        forged["claim_effect"] = "directional_tier_1_for_this_run"
        with self.assertRaises(ValueError):
            validate_allocation_subset(forged, plan=forge_plan)

        shared_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "shared-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "shared-02", "reported_segment_id": "segment-a"},
                ],
                profiles=[
                    profile(
                        "shared-profile",
                        "segment-a",
                        "shared-group",
                        1.0,
                        1.0,
                        must_cover=("group-1", "group-2"),
                    )
                ],
                must_cover=("group-1", "group-2"),
            )
        )
        shared_subset = evaluate_allocation_subset(
            shared_plan,
            selected_slot_ids=["shared-01", "shared-02"],
            allow_directional_allocation=False,
        )
        swapped_groups = copy.deepcopy(shared_subset)
        for match in swapped_groups["must_cover_diagnostics"]["matches"]:
            match["must_cover_group_id"] = (
                "group-2"
                if match["must_cover_group_id"] == "group-1"
                else "group-1"
            )
        swapped_groups["must_cover_diagnostics"]["matches"].sort(
            key=lambda item: item["must_cover_group_id"]
        )
        with self.assertRaises(ValueError):
            validate_allocation_subset(swapped_groups, plan=shared_plan)

        altered_slot = copy.deepcopy(shared_subset)
        first_match = altered_slot["must_cover_diagnostics"]["matches"][0]
        second_match = altered_slot["must_cover_diagnostics"]["matches"][1]
        first_match["slot_id"], second_match["slot_id"] = (
            second_match["slot_id"],
            first_match["slot_id"],
        )
        for match in altered_slot["must_cover_diagnostics"]["matches"]:
            match["stable_order_sha256"] = "sha256:" + hashlib.sha256(
                (
                    shared_plan["stable_seed"]
                    + "\0"
                    + shared_plan["stage_roster_id"]
                    + "\0"
                    + match["slot_id"]
                    + "\0"
                    + match["grounded_profile_id"]
                ).encode("utf-8")
            ).hexdigest()
        with self.assertRaises(ValueError):
            validate_allocation_subset(altered_slot, plan=shared_plan)

        altered_hash = copy.deepcopy(shared_subset)
        altered_hash["must_cover_diagnostics"]["matches"][0][
            "stable_order_sha256"
        ] = "sha256:" + "f" * 64
        with self.assertRaises(ValueError):
            validate_allocation_subset(altered_hash, plan=shared_plan)

        alternate_profile_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "profile-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "profile-02", "reported_segment_id": "segment-a"},
                ],
                profiles=[
                    profile(
                        "profile-a",
                        "segment-a",
                        "group-a",
                        0.5,
                        0.5,
                        must_cover=("group-a",),
                    ),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-a",
                        0.5,
                        0.5,
                        must_cover=("group-a",),
                        snapshot_hash=HASH_B,
                    ),
                ],
                must_cover=("group-a",),
            )
        )
        alternate_subset = evaluate_allocation_subset(
            alternate_profile_plan,
            selected_slot_ids=["profile-01", "profile-02"],
            allow_directional_allocation=False,
        )
        altered_profile = copy.deepcopy(alternate_subset)
        match = altered_profile["must_cover_diagnostics"]["matches"][0]
        original_profile_id = match["grounded_profile_id"]
        replacement_profile_id = (
            "profile-b" if original_profile_id == "profile-a" else "profile-a"
        )
        match["grounded_profile_id"] = replacement_profile_id
        match["stable_order_sha256"] = "sha256:" + hashlib.sha256(
            (
                alternate_profile_plan["stable_seed"]
                + "\0"
                + alternate_profile_plan["stage_roster_id"]
                + "\0"
                + match["slot_id"]
                + "\0"
                + replacement_profile_id
            ).encode("utf-8")
        ).hexdigest()
        profile_diagnostics = diagnostics_by_id(
            altered_profile["profile_diagnostics"],
            "grounded_profile_id",
        )
        profile_diagnostics[original_profile_id]["matching_floor"] = 0
        profile_diagnostics[replacement_profile_id]["matching_floor"] = 1
        with self.assertRaises(ValueError):
            validate_allocation_subset(
                altered_profile,
                plan=alternate_profile_plan,
            )

    def test_tier_one_subset_only_gates_when_must_cover_is_missing(self) -> None:
        selected = [self.directional_plan["assignments"][0]["slot_id"]]
        blocked = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=selected,
            allow_directional_allocation=False,
        )
        self.assertEqual("directional_profile_allocation", blocked["fidelity"]["status"])
        self.assertEqual("requires_user_decision", blocked["claim_effect"])
        self.assertEqual(2, len(blocked["must_cover_diagnostics"]["uncovered_group_ids"]))

        continued = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=selected,
            allow_directional_allocation=True,
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            continued["claim_effect"],
        )

    def test_subset_preserves_distinct_group_floor_bindings(self) -> None:
        shared_profile = profile(
            "shared-profile",
            "segment-a",
            "shared-structural-group",
            1.0,
            1.0,
            must_cover=("group-1", "group-2"),
        )
        one_slot_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"}
                ],
                profiles=[shared_profile],
                must_cover=("group-1", "group-2"),
            )
        )
        one_slot_subset = evaluate_allocation_subset(
            one_slot_plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=False,
        )
        self.assertEqual(
            1,
            len(one_slot_subset["must_cover_diagnostics"]["covered_group_ids"]),
        )
        self.assertEqual(
            1,
            len(one_slot_subset["must_cover_diagnostics"]["uncovered_group_ids"]),
        )
        self.assertEqual(
            "requires_user_decision",
            one_slot_subset["claim_effect"],
        )

        two_slot_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
                ],
                profiles=[shared_profile],
                must_cover=("group-1", "group-2"),
            )
        )
        prefix = evaluate_allocation_subset(
            two_slot_plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=False,
        )
        self.assertEqual(1, len(prefix["must_cover_diagnostics"]["matches"]))
        self.assertEqual(
            1,
            len(prefix["must_cover_diagnostics"]["covered_group_ids"]),
        )
        self.assertEqual(
            1,
            len(prefix["must_cover_diagnostics"]["uncovered_group_ids"]),
        )
        self.assertEqual("requires_user_decision", prefix["claim_effect"])

    def test_subset_recomputes_literal_ideals_from_selected_capacity(self) -> None:
        screening = allocate_stage_profiles(
            request(
                slots=[
                    {
                        "slot_id": f"screening-{index}",
                        "reported_segment_id": "segment-a",
                    }
                    for index in range(1, 5)
                ],
                profiles=[
                    profile("profile-a", "segment-a", "group-a", 0.75, 0.75),
                    profile(
                        "profile-b",
                        "segment-a",
                        "group-b",
                        0.25,
                        0.25,
                        snapshot_hash=HASH_B,
                    ),
                ],
            )
        )
        screening_subset = evaluate_allocation_subset(
            screening,
            selected_slot_ids=["screening-1", "screening-2"],
            allow_directional_allocation=False,
        )
        screening_diagnostics = diagnostics_by_id(
            screening_subset["profile_diagnostics"],
            "grounded_profile_id",
        )
        self.assertEqual(1.5, screening_diagnostics["profile-a"]["ideal_slot_count"])
        self.assertEqual(0.5, screening_diagnostics["profile-b"]["ideal_slot_count"])

        finalist = allocate_stage_profiles(
            request(
                stage="finalist",
                slots=[
                    {"slot_id": f"finalist-{index}", "reported_segment_id": None}
                    for index in range(1, 5)
                ],
                profiles=[
                    profile("profile-a", "segment-a", "group-a", 0.75, 1.0),
                    profile(
                        "profile-b",
                        "segment-b",
                        "group-b",
                        0.25,
                        1.0,
                        snapshot_hash=HASH_B,
                    ),
                ],
                analysis_weights={},
            )
        )
        finalist_subset = evaluate_allocation_subset(
            finalist,
            selected_slot_ids=["finalist-1", "finalist-2"],
            allow_directional_allocation=False,
        )
        finalist_diagnostics = diagnostics_by_id(
            finalist_subset["profile_diagnostics"],
            "grounded_profile_id",
        )
        self.assertEqual(1.5, finalist_diagnostics["profile-a"]["ideal_slot_count"])
        self.assertEqual(0.5, finalist_diagnostics["profile-b"]["ideal_slot_count"])

        tampered = copy.deepcopy(finalist_subset)
        tampered["profile_diagnostics"][0]["ideal_slot_count"] = 999
        with self.assertRaises(ValueError):
            validate_allocation_subset(tampered, plan=finalist)

    def test_plan_binding_rejects_zero_mass_ideal_redistribution_and_nonprefix(
        self,
    ) -> None:
        zero_mass_plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "zero-01", "reported_segment_id": "segment-zero"},
                    {"slot_id": "zero-02", "reported_segment_id": "segment-zero"},
                    {
                        "slot_id": "positive-01",
                        "reported_segment_id": "segment-positive",
                    },
                ],
                profiles=[
                    profile(
                        "zero-major",
                        "segment-zero",
                        "zero-major-group",
                        0.0,
                        0.75,
                    ),
                    profile(
                        "zero-minor",
                        "segment-zero",
                        "zero-minor-group",
                        0.0,
                        0.25,
                        snapshot_hash=HASH_B,
                    ),
                    profile(
                        "positive",
                        "segment-positive",
                        "positive-group",
                        1.0,
                        1.0,
                        snapshot_hash=HASH_C,
                    ),
                ],
                analysis_weights={
                    "segment-zero": 0.0,
                    "segment-positive": 1.0,
                },
            )
        )
        selected_ids = ["zero-01", "zero-02", "positive-01"]
        subset = evaluate_allocation_subset(
            zero_mass_plan,
            selected_slot_ids=selected_ids,
            allow_directional_allocation=False,
        )
        self.assertEqual(
            subset,
            validate_allocation_subset(subset, plan=zero_mass_plan),
        )
        redistributed = copy.deepcopy(subset)
        diagnostics = diagnostics_by_id(
            redistributed["profile_diagnostics"],
            "grounded_profile_id",
        )
        diagnostics["zero-major"]["ideal_slot_count"] = 1.0
        diagnostics["zero-minor"]["ideal_slot_count"] = 1.0
        self.assertEqual(
            2.0,
            diagnostics["zero-major"]["ideal_slot_count"]
            + diagnostics["zero-minor"]["ideal_slot_count"],
        )
        with self.assertRaises(ValueError):
            validate_allocation_subset(
                redistributed,
                plan=zero_mass_plan,
            )

        honest_prefix = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=False,
        )
        forged_nonprefix = copy.deepcopy(honest_prefix)
        forged_nonprefix["selected_slot_ids"] = ["slot-02"]
        with self.assertRaises(ValueError):
            validate_allocation_subset(
                forged_nonprefix,
                plan=self.directional_plan,
            )

    def test_cumulative_wave_prefix_succeeds_but_current_wave_only_fails(self) -> None:
        slot_ids = [
            item["slot_id"] for item in self.directional_plan["assignments"]
        ]
        cumulative_through_wave_two = slot_ids[:3]
        subset = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=cumulative_through_wave_two,
            allow_directional_allocation=False,
        )
        self.assertEqual(
            cumulative_through_wave_two,
            subset["selected_slot_ids"],
        )

        current_wave_only = [slot_ids[2]]
        with self.assertRaises(ValueError):
            evaluate_allocation_subset(
                self.directional_plan,
                selected_slot_ids=current_wave_only,
                allow_directional_allocation=False,
            )

    def test_structural_subset_uses_only_selected_slots_for_diagnostics_and_gate(
        self,
    ) -> None:
        plan = allocate_stage_profiles(
            request(
                slots=[
                    {"slot_id": "slot-01", "reported_segment_id": "segment-a"},
                    {"slot_id": "slot-02", "reported_segment_id": "segment-a"},
                ],
                basis="structural_frame",
            )
        )
        self.assertEqual("frame_aligned", plan["fidelity"]["status"])
        subset = evaluate_allocation_subset(
            plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=False,
        )
        self.assertEqual("allocation_distorted", subset["fidelity"]["status"])
        self.assertEqual("requires_user_decision", subset["claim_effect"])
        self.assertEqual(
            1,
            sum(
                item["assigned_slots"]
                for item in subset["structural_group_diagnostics"]
            ),
        )

        continued = evaluate_allocation_subset(
            plan,
            selected_slot_ids=["slot-01"],
            allow_directional_allocation=True,
        )
        self.assertEqual(
            "directional_tier_1_for_this_run",
            continued["claim_effect"],
        )

    def test_subset_rejects_empty_unknown_duplicate_reordered_and_skipped_slots(
        self,
    ) -> None:
        slot_ids = [
            item["slot_id"] for item in self.directional_plan["assignments"]
        ]
        invalid_selections = [
            [],
            ["unknown"],
            [slot_ids[0], slot_ids[0]],
            [slot_ids[1], slot_ids[0]],
            [slot_ids[0], slot_ids[2]],
            [slot_ids[1]],
        ]
        for selected in invalid_selections:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                evaluate_allocation_subset(
                    self.directional_plan,
                    selected_slot_ids=selected,
                    allow_directional_allocation=False,
                )

    def test_plan_and_subset_validators_reject_allowlist_or_binding_tampering(
        self,
    ) -> None:
        invalid_plan = copy.deepcopy(self.directional_plan)
        invalid_plan["extra"] = True
        with self.assertRaises(ValueError):
            validate_allocation_plan(invalid_plan)

        invalid_hash = copy.deepcopy(self.directional_plan)
        invalid_hash["assignments"][0]["profile_snapshot_sha256"] = HASH_C
        with self.assertRaises(ValueError):
            validate_allocation_plan(invalid_hash)

        subset = evaluate_allocation_subset(
            self.directional_plan,
            selected_slot_ids=[
                item["slot_id"] for item in self.directional_plan["assignments"][:2]
            ],
            allow_directional_allocation=False,
        )
        invalid_subset = copy.deepcopy(subset)
        invalid_subset["fidelity"]["status"] = "frame_aligned"
        with self.assertRaises(ValueError):
            validate_allocation_subset(
                invalid_subset,
                plan=self.directional_plan,
            )


class PublicContractTest(unittest.TestCase):
    def test_public_versions_and_fidelity_statuses_are_exact(self) -> None:
        self.assertEqual(
            {
                "directional_profile_allocation",
                "frame_aligned",
                "allocation_distorted",
            },
            ALLOCATION_FIDELITY_STATUSES,
        )
        self.assertEqual(
            "audience-profile-allocation-request-v1",
            ALLOCATION_REQUEST_VERSION,
        )
        self.assertEqual(
            "audience-profile-allocation-plan-v1",
            ALLOCATION_PLAN_VERSION,
        )
        self.assertEqual(
            "audience-profile-allocation-subset-v1",
            ALLOCATION_SUBSET_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
