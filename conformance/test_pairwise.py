from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from audience_lab.pairwise import (
    IndexedPairwiseObservation,
    PairwiseConfig,
    classify_inclusion_frequency,
    davidson_loss_and_gradient,
    davidson_probabilities,
    fit_davidson,
    resolve_boundary,
    symmetric_cutoff_inclusion,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"


def boundary_fixture(name: str = "boundary-responses.jsonl") -> list[dict]:
    path = ROOT / "conformance" / "fixtures" / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


CONFIG = PairwiseConfig(
    tie_parameter=0.4,
    penalty_lambda=0.1,
    optimizer_tolerance=1e-8,
    bootstrap_count=200,
    successful_fit_floor=0.95,
    seed=17,
)


def compact_response(
    response_id: str,
    first: str,
    second: str,
    outcome: str,
    *,
    wave: int = 1,
    segment_id: str = "S1",
) -> dict:
    preferred = first if outcome == "first_preferred" else second
    if outcome == "tie":
        preferred = ""
    return {
        "response_id": response_id,
        "record_type": "boundary_response",
        "synthetic_replicate_id": f"replicate-{response_id}",
        "persona_archetype_id": f"archetype-{int(response_id.rsplit('-', 1)[-1]) % 4}",
        "segment_id": segment_id,
        "assigned_variation_ids": [first, second],
        "shown_order": [first, second],
        "pairwise_choice": {
            "status": outcome,
            "preferred_variation_id": preferred,
        },
        "usable_pairwise_observation": True,
        "pair_assignment_id": response_id,
        "boundary_wave": wave,
    }


def assignments_for(records: list[dict]) -> list[dict]:
    return [
        {
            "pair_assignment_id": record["pair_assignment_id"],
            "wave": record["boundary_wave"],
            "variation_ids": list(record["assigned_variation_ids"]),
        }
        for record in records
    ]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def matching_manifest(
    *,
    records: list[dict],
    creative_ids: tuple[str, ...] = ("F1", "V4", "V5", "V6", "N1"),
    shortlist_size: int = 3,
) -> dict:
    payload = json.loads(
        (ROOT / "conformance" / "fixtures" / "manifest-valid.json").read_text(
            encoding="utf-8"
        )
    )
    payload["study_id"] = records[0]["study_id"]
    payload["requested_shortlist_size"] = shortlist_size
    payload["maximum_synthetic_panelists"] = 60
    payload["synthetic_replicate_capacity"] = {
        "screening_planned": 31,
        "boundary_reserved": len(records),
        "boundary_jobs_per_wave": len(records),
        "boundary_waves_max": 1,
        "finalist_reserved": 20,
        "ceiling_satisfied": True,
    }
    payload["audience_lock"]["segment_weights"] = {
        segment_id: 1 / len({item["segment_id"] for item in records})
        for segment_id in sorted({item["segment_id"] for item in records})
    }
    payload["outputs"]["creative_asset_hashes"] = {
        creative_id: f"sha256:test-{index:03d}"
        for index, creative_id in enumerate(creative_ids, 1)
    }
    payload["assignment"]["usable_participations_per_creative"] = {
        creative_id: 9 for creative_id in creative_ids
    }
    payload["model"].update(
        {
            "bootstrap_count": 2000,
            "pairwise_tie_parameter": 0.4,
            "pairwise_penalty_lambda": 0.1,
            "pairwise_optimizer_tolerance": 1e-8,
        }
    )
    return payload


def matching_screening_result(
    records: list[dict],
    *,
    classifications: dict[str, str] | None = None,
) -> dict:
    classes = classifications or {
        "F1": "clear_finalist",
        "V4": "boundary_candidate",
        "V5": "boundary_candidate",
        "V6": "boundary_candidate",
        "N1": "clear_non_finalist",
    }
    return {
        "study_id": records[0]["study_id"],
        "method": "partial_exposure_maxdiff",
        "requested_top_k": 3,
        "validity_status": "valid",
        "validity_reasons": [],
        "classifications": classes,
        "utilities": {creative_id: 999.0 - index for index, creative_id in enumerate(classes)},
        "top_k_inclusion_frequencies": {
            creative_id: 0.5 for creative_id in classes
        },
        "boundary_plan": {
            "plan_version": "predeclared-boundary-v1",
            "frozen_before_dispatch": True,
            "available_boundary_reserve": len(records),
            "predeclared_pair_assignments": assignments_for(records),
        },
    }


class DavidsonLikelihoodTests(unittest.TestCase):
    def test_probabilities_include_fixed_tie_mass_and_sum_to_one(self):
        first, second, tie = davidson_probabilities(0.0, 0.0, tie_parameter=0.4)

        self.assertAlmostEqual(1 / 2.4, first)
        self.assertAlmostEqual(1 / 2.4, second)
        self.assertAlmostEqual(0.4 / 2.4, tie)
        self.assertAlmostEqual(1.0, first + second + tie)

    def test_davidson_gradient_matches_finite_difference_with_ties(self):
        observations = (
            IndexedPairwiseObservation(0, 1, "first"),
            IndexedPairwiseObservation(0, 2, "tie"),
            IndexedPairwiseObservation(1, 2, "second"),
        )
        weights = np.asarray([1.2, 0.7, 1.1])
        utilities = np.asarray([0.3, -0.1, -0.2])
        _, gradient = davidson_loss_and_gradient(
            utilities,
            observations,
            weights,
            tie_parameter=0.4,
            penalty_lambda=0.1,
        )

        epsilon = 1e-6
        numerical = np.empty_like(utilities)
        for index in range(len(utilities)):
            step = np.zeros_like(utilities)
            step[index] = epsilon
            right = davidson_loss_and_gradient(
                utilities + step, observations, weights, 0.4, 0.1
            )[0]
            left = davidson_loss_and_gradient(
                utilities - step, observations, weights, 0.4, 0.1
            )[0]
            numerical[index] = (right - left) / (2 * epsilon)

        np.testing.assert_allclose(gradient, numerical, atol=2e-6, rtol=2e-6)

    def test_fit_is_connected_identified_centered_and_tie_aware(self):
        fit = fit_davidson(boundary_fixture(), CONFIG, candidate_ids=("V4", "V5", "V6"))

        self.assertTrue(fit.success)
        self.assertTrue(fit.connected)
        self.assertTrue(fit.identified)
        self.assertTrue(fit.converged)
        self.assertEqual(["V4", "V5", "V6"], list(fit.ranked_ids))
        self.assertAlmostEqual(0.0, sum(fit.utilities.values()), places=9)
        self.assertGreater(fit.outcome_counts["tie"], 0)

    def test_disconnected_graph_is_rejected_before_regularization(self):
        fit = fit_davidson(
            boundary_fixture("boundary-responses-disconnected.jsonl"),
            CONFIG,
            candidate_ids=("V4", "V5", "V6", "V7"),
        )

        self.assertFalse(fit.success)
        self.assertFalse(fit.connected)
        self.assertFalse(fit.identified)
        self.assertEqual({}, fit.utilities)
        self.assertIn("disconnected", fit.message)

    def test_fixed_zero_tie_parameter_refuses_observed_ties(self):
        fit = fit_davidson(
            boundary_fixture(),
            PairwiseConfig(
                tie_parameter=0.0,
                penalty_lambda=0.1,
                bootstrap_count=20,
                seed=4,
            ),
            candidate_ids=("V4", "V5", "V6"),
        )

        self.assertFalse(fit.success)
        self.assertFalse(fit.identified)
        self.assertEqual({}, fit.utilities)
        self.assertIn("tie_parameter is zero", fit.message)

    def test_negative_resampling_seed_is_rejected_at_configuration_time(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            PairwiseConfig(tie_parameter=0.4, penalty_lambda=0.1, seed=-1)

    def test_zero_and_negative_protocol_penalties_are_rejected(self):
        for penalty in (0.0, -0.1):
            with self.subTest(penalty=penalty):
                with self.assertRaisesRegex(ValueError, "positive"):
                    PairwiseConfig(tie_parameter=0.4, penalty_lambda=penalty)

        with self.assertRaisesRegex(ValueError, "optimizer_tolerance.*positive"):
            PairwiseConfig(
                tie_parameter=0.4,
                penalty_lambda=0.1,
                optimizer_tolerance=0.0,
            )

    def test_complete_separation_is_finite_under_positive_regularization(self):
        records = [
            compact_response(
                f"separated-{index}", "V4", "V5", "first_preferred"
            )
            for index in range(30)
        ]
        config = PairwiseConfig(
            tie_parameter=0.4,
            penalty_lambda=0.1,
            optimizer_tolerance=1e-10,
            bootstrap_count=20,
            seed=7,
        )

        fit = fit_davidson(records, config, candidate_ids=("V4", "V5"))

        self.assertTrue(fit.success, fit.message)
        self.assertTrue(fit.converged)
        self.assertTrue(all(np.isfinite(value) for value in fit.utilities.values()))
        self.assertLess(max(abs(value) for value in fit.utilities.values()), 10.0)
        self.assertIsNotNone(fit.projected_gradient_norm)
        self.assertLessEqual(fit.projected_gradient_norm, 5e-5)


class PairwiseResolutionTests(unittest.TestCase):
    def test_davidson_model_resolves_connected_boundary(self):
        records = boundary_fixture()
        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            predeclared_pair_assignments=assignments_for(records),
        )

        self.assertEqual("resolved", result.status)
        self.assertEqual(["V4", "V5"], result.selected_ids)

    def test_disconnected_boundary_is_unresolved_without_utilities(self):
        records = boundary_fixture("boundary-responses-disconnected.jsonl")
        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            predeclared_pair_assignments=assignments_for(records),
        )

        self.assertEqual("unresolved", result.status)
        self.assertEqual({}, result.utilities)
        self.assertEqual([], result.selected_ids)
        self.assertIn("comparison_graph_disconnected", result.status_reasons)

    def test_insufficient_boundary_is_unresolved_without_utilities(self):
        records = [compact_response("job-1", "V4", "V5", "first_preferred")]
        result = resolve_boundary(
            records,
            slots=3,
            config=CONFIG,
            candidate_ids=("V4", "V5"),
            predeclared_pair_assignments=assignments_for(records),
        )

        self.assertEqual("unresolved", result.status)
        self.assertEqual({}, result.utilities)
        self.assertIn("insufficient_boundary_candidates", result.status_reasons)

    def test_deterministic_resampling_reproduces_complete_output(self):
        records = boundary_fixture()
        plan = assignments_for(records)
        first = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            predeclared_pair_assignments=plan,
        )
        second = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            predeclared_pair_assignments=plan,
        )

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_inclusive_product_threshold_boundaries(self):
        self.assertEqual("clear_finalist", classify_inclusion_frequency(0.90))
        self.assertEqual("clear_non_finalist", classify_inclusion_frequency(0.10))
        self.assertEqual("boundary_candidate", classify_inclusion_frequency(0.899999))
        self.assertEqual("boundary_candidate", classify_inclusion_frequency(0.100001))

    def test_cutoff_ties_receive_symmetric_fractional_inclusion(self):
        inclusion = symmetric_cutoff_inclusion(
            {"leader": 1.0, "tie-a": 0.0, "tie-b": 0.0, "trailer": -1.0},
            slots=2,
            tolerance=1e-8,
        )

        self.assertEqual(1.0, inclusion["leader"])
        self.assertEqual(0.5, inclusion["tie-a"])
        self.assertEqual(0.5, inclusion["tie-b"])
        self.assertEqual(0.0, inclusion["trailer"])
        self.assertEqual(
            "boundary_candidate", classify_inclusion_frequency(inclusion["tie-a"])
        )

    def test_all_tie_boundary_is_unresolved_without_lexical_certainty(self):
        records: list[dict] = []
        pairs = (("V4", "V5"), ("V4", "V6"), ("V5", "V6"))
        for index in range(30):
            first, second = pairs[index % len(pairs)]
            records.append(
                compact_response(f"all-tie-{index}", first, second, "tie")
            )

        result = resolve_boundary(
            records,
            slots=1,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=assignments_for(records),
        )

        self.assertEqual("unresolved", result.status)
        frequencies = result.decision_audit["waves"][0][
            "conditional_inclusion_frequencies"
        ]
        for candidate_id in ("V4", "V5", "V6"):
            self.assertAlmostEqual(1 / 3, frequencies[candidate_id], places=12)
        bootstrap = result.decision_audit["waves"][0]["bootstrap"]
        self.assertEqual(
            bootstrap["successful_fits"], bootstrap["cutoff_tied_fits"]
        )
        self.assertEqual([], result.selected_ids)
        self.assertEqual(
            "symmetric_fractional_inclusion",
            result.decision_audit["inclusion_policy"]["cutoff_tie_policy"],
        )

    def test_result_contains_only_pairwise_candidate_utilities_and_no_pooling(self):
        records = boundary_fixture()
        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=assignments_for(records),
            clear_finalist_ids=("F1",),
            clear_non_finalist_ids=("N1",),
        )
        payload = result.to_dict()

        self.assertEqual({"V4", "V5", "V6"}, set(payload["utilities"]))
        self.assertNotIn("combined_utility", payload)
        self.assertNotIn("maxdiff_utilities", payload)
        self.assertFalse(payload["decision_audit"]["maxdiff_utilities_pooled"])
        self.assertEqual(["F1"], payload["frozen_clear_finalist_ids"])
        self.assertEqual(["N1"], payload["frozen_clear_non_finalist_ids"])

    def test_malformed_choice_returns_invalid_envelope(self):
        records = boundary_fixture()
        plan = assignments_for(records)
        records[0]["pairwise_choice"]["status"] = "invented_outcome"

        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            predeclared_pair_assignments=plan,
        )

        self.assertEqual("invalid", result.status)
        self.assertEqual({}, result.utilities)
        self.assertIn("malformed_boundary_response", result.status_reasons)

    def test_clear_group_comparison_is_out_of_scope_and_invalid(self):
        records = boundary_fixture()
        plan = assignments_for(records)
        records[0]["assigned_variation_ids"] = ["F1", "V6"]
        records[0]["shown_order"] = ["F1", "V6"]
        records[0]["pairwise_choice"].update(
            status="first_preferred", preferred_variation_id="F1"
        )

        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=plan,
            clear_finalist_ids=("F1",),
        )

        self.assertEqual("invalid", result.status)
        self.assertEqual({}, result.utilities)
        self.assertIn("out_of_scope_pairwise_response", result.status_reasons)

    def test_core_resolver_requires_explicit_frozen_assignments(self):
        records = boundary_fixture()

        result = resolve_boundary(records, slots=2, config=CONFIG)

        self.assertEqual("invalid", result.status)
        self.assertIn("predeclared_pair_assignments_required", result.status_reasons)
        self.assertEqual(
            len(records),
            result.decision_audit["reserve"]["boundary_jobs_observed"],
        )

    def test_trivial_slot_resolution_rejects_assignment_pair_and_wave_mismatches(self):
        base = compact_response("trivial-0", "V4", "V5", "first_preferred")
        plan = assignments_for([base])
        cases: list[tuple[str, dict, str]] = []

        unknown = dict(base)
        unknown["pair_assignment_id"] = "unknown-job"
        cases.append(("assignment", unknown, "response_not_predeclared"))

        pair = compact_response("trivial-0", "V4", "V6", "first_preferred")
        cases.append(("pair", pair, "response_pair_mismatch"))

        wave = dict(base)
        wave["boundary_wave"] = 2
        cases.append(("wave", wave, "response_wave_mismatch"))

        for name, response, reason in cases:
            with self.subTest(name=name):
                result = resolve_boundary(
                    [response],
                    slots=0,
                    config=CONFIG,
                    candidate_ids=("V4", "V5", "V6"),
                    predeclared_pair_assignments=plan,
                )
                self.assertEqual("invalid", result.status)
                self.assertIn(reason, result.status_reasons)
                self.assertEqual(
                    1,
                    result.decision_audit["reserve"]["boundary_jobs_consumed"],
                )

    def test_trivial_slot_resolution_accounts_valid_supplied_calls(self):
        records = [
            compact_response("trivial-1", "V4", "V5", "first_preferred"),
            compact_response("trivial-2", "V4", "V5", "second_preferred"),
        ]
        plan = assignments_for(records)

        for slots in (0, 2):
            with self.subTest(slots=slots):
                result = resolve_boundary(
                    records,
                    slots=slots,
                    config=CONFIG,
                    candidate_ids=("V4", "V5"),
                    predeclared_pair_assignments=plan,
                    boundary_jobs_per_wave=2,
                    boundary_waves_max=1,
                    boundary_reserved=2,
                    available_boundary_reserve=2,
                )
                self.assertEqual("resolved", result.status)
                reserve = result.decision_audit["reserve"]
                self.assertEqual(2, reserve["boundary_jobs_observed"])
                self.assertEqual(2, reserve["boundary_jobs_consumed"])
                self.assertEqual(0, reserve["boundary_jobs_remaining"])


class PairwisePolicyTests(unittest.TestCase):
    def test_stops_after_stable_completed_wave_and_preserves_future_jobs(self):
        wave_one: list[dict] = []
        for index in range(10):
            wave_one.extend(
                [
                    compact_response(
                        f"wave1-v4-v6-{index}", "V4", "V6", "first_preferred"
                    ),
                    compact_response(
                        f"wave1-v5-v6-{index}", "V5", "V6", "first_preferred"
                    ),
                    compact_response(f"wave1-v4-v5-{index}", "V4", "V5", "tie"),
                ]
            )
        future = [
            {
                "pair_assignment_id": f"wave2-job-{index}",
                "wave": 2,
                "variation_ids": ["V4", "V5"],
            }
            for index in range(30)
        ]
        result = resolve_boundary(
            wave_one,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=assignments_for(wave_one) + future,
            boundary_jobs_per_wave=30,
            boundary_waves_max=2,
            boundary_reserved=60,
            available_boundary_reserve=60,
            finalist_reserved=20,
        )

        audit = result.decision_audit
        self.assertEqual("resolved", result.status)
        self.assertEqual("inclusion_rule_satisfied", audit["stopping_decision"]["reason"])
        self.assertEqual(30, audit["reserve"]["boundary_jobs_consumed"])
        self.assertEqual(30, audit["reserve"]["boundary_jobs_remaining"])
        self.assertEqual(20, audit["reserve"]["finalist_reserved_before"])
        self.assertEqual(20, audit["reserve"]["finalist_reserved_after"])
        self.assertEqual([], audit["next_wave_job_ids"])

    def test_later_wave_overdispatch_counts_every_realized_call(self):
        wave_one: list[dict] = []
        for index in range(10):
            wave_one.extend(
                [
                    compact_response(
                        f"overdispatch-v4-v6-{index}",
                        "V4",
                        "V6",
                        "first_preferred",
                    ),
                    compact_response(
                        f"overdispatch-v5-v6-{index}",
                        "V5",
                        "V6",
                        "first_preferred",
                    ),
                    compact_response(
                        f"overdispatch-v4-v5-{index}", "V4", "V5", "tie"
                    ),
                ]
            )
        later = compact_response(
            "future-job-0", "V4", "V5", "tie", wave=2
        )
        future = [
            {
                "pair_assignment_id": f"future-job-{index}",
                "wave": 2,
                "variation_ids": ["V4", "V5"],
            }
            for index in range(30)
        ]

        result = resolve_boundary(
            wave_one + [later],
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=assignments_for(wave_one) + future,
            boundary_jobs_per_wave=30,
            boundary_waves_max=2,
            boundary_reserved=60,
            available_boundary_reserve=60,
            finalist_reserved=20,
        )

        self.assertEqual("invalid", result.status)
        self.assertIn("responses_after_inclusion_stop", result.status_reasons)
        reserve = result.decision_audit["reserve"]
        self.assertEqual(31, reserve["boundary_jobs_observed"])
        self.assertEqual(31, reserve["boundary_jobs_consumed"])
        self.assertEqual(29, reserve["boundary_jobs_remaining"])
        self.assertEqual(0, reserve["boundary_jobs_over_reserve"])
        self.assertEqual(20, reserve["finalist_reserved_after"])

    def test_maximum_wave_stop_is_recorded_when_threshold_not_met(self):
        records: list[dict] = []
        for index in range(20):
            records.append(
                compact_response(
                    f"balanced-a-{index}",
                    "V4",
                    "V5",
                    "first_preferred",
                )
            )
            records.append(
                compact_response(
                    f"balanced-b-{index}",
                    "V4",
                    "V5",
                    "second_preferred",
                )
            )
        result = resolve_boundary(
            records,
            slots=1,
            config=CONFIG,
            candidate_ids=("V4", "V5"),
            predeclared_pair_assignments=assignments_for(records),
            boundary_jobs_per_wave=40,
            boundary_waves_max=1,
            boundary_reserved=40,
            available_boundary_reserve=40,
            finalist_reserved=12,
        )

        self.assertEqual("unresolved", result.status)
        self.assertEqual({}, result.utilities)
        self.assertEqual(
            "maximum_waves_reached",
            result.decision_audit["stopping_decision"]["reason"],
        )
        self.assertEqual(12, result.decision_audit["reserve"]["finalist_reserved_after"])

    def test_unknown_or_over_cap_predeclared_job_is_invalid(self):
        records = boundary_fixture()
        plan = assignments_for(records)
        plan.pop()

        unknown = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=plan,
            boundary_jobs_per_wave=9,
            boundary_waves_max=1,
            boundary_reserved=9,
            available_boundary_reserve=9,
        )
        over_cap = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            predeclared_pair_assignments=assignments_for(records),
            boundary_jobs_per_wave=8,
            boundary_waves_max=1,
            boundary_reserved=8,
            available_boundary_reserve=8,
        )

        self.assertEqual("invalid", unknown.status)
        self.assertIn("response_not_predeclared", unknown.status_reasons)
        self.assertEqual("invalid", over_cap.status)
        self.assertIn("predeclared_wave_exceeds_job_cap", over_cap.status_reasons)
        reserve = over_cap.decision_audit["reserve"]
        self.assertEqual(9, reserve["boundary_jobs_consumed"])
        self.assertEqual(0, reserve["boundary_jobs_remaining"])
        self.assertEqual(1, reserve["boundary_jobs_over_reserve"])

    def test_boundary_reserve_exhaustion_is_distinct_from_maximum_wave_stop(self):
        records: list[dict] = []
        for index in range(10):
            records.append(
                compact_response(
                    f"reserve-a-{index}", "V4", "V5", "first_preferred"
                )
            )
            records.append(
                compact_response(
                    f"reserve-b-{index}", "V4", "V5", "second_preferred"
                )
            )
        result = resolve_boundary(
            records,
            slots=1,
            config=CONFIG,
            candidate_ids=("V4", "V5"),
            predeclared_pair_assignments=assignments_for(records),
            boundary_jobs_per_wave=20,
            boundary_waves_max=2,
            boundary_reserved=40,
            available_boundary_reserve=20,
            finalist_reserved=14,
        )

        self.assertEqual("unresolved", result.status)
        self.assertEqual(
            "boundary_reserve_exhausted",
            result.decision_audit["stopping_decision"]["reason"],
        )
        self.assertEqual(20, result.decision_audit["reserve"]["boundary_jobs_consumed"])
        self.assertEqual(0, result.decision_audit["reserve"]["boundary_jobs_remaining"])
        self.assertEqual(14, result.decision_audit["reserve"]["finalist_reserved_after"])

    def test_partial_wave_consumes_realized_calls_but_does_not_fit(self):
        received = compact_response(
            "partial-wave-job-1", "V4", "V5", "first_preferred"
        )
        plan = assignments_for([received]) + [
            {
                "pair_assignment_id": "partial-wave-job-2",
                "wave": 1,
                "variation_ids": ["V4", "V5"],
            }
        ]
        result = resolve_boundary(
            [received],
            slots=1,
            config=CONFIG,
            candidate_ids=("V4", "V5"),
            predeclared_pair_assignments=plan,
            boundary_jobs_per_wave=2,
            boundary_waves_max=1,
            boundary_reserved=2,
            available_boundary_reserve=2,
            finalist_reserved=8,
        )

        self.assertEqual("unresolved", result.status)
        self.assertEqual(
            "awaiting_predeclared_wave_responses",
            result.decision_audit["stopping_decision"]["reason"],
        )
        self.assertEqual(1, result.decision_audit["reserve"]["boundary_jobs_observed"])
        self.assertEqual(1, result.decision_audit["reserve"]["boundary_jobs_consumed"])
        self.assertEqual(
            ["partial-wave-job-2"], result.decision_audit["next_wave_job_ids"]
        )
        self.assertIsNone(result.model_diagnostics["fit"])

    def test_locked_segment_coverage_can_complete_in_a_later_predeclared_wave(self):
        records: list[dict] = []
        counter = 0
        for wave, segment in ((1, "S1"), (2, "S2")):
            for _ in range(2):
                for first, second, outcome in (
                    ("V4", "V6", "first_preferred"),
                    ("V5", "V6", "first_preferred"),
                    ("V4", "V5", "tie"),
                ):
                    counter += 1
                    records.append(
                        compact_response(
                            f"segment-wave-{counter}",
                            first,
                            second,
                            outcome,
                            wave=wave,
                            segment_id=segment,
                        )
                    )
        result = resolve_boundary(
            records,
            slots=2,
            config=CONFIG,
            candidate_ids=("V4", "V5", "V6"),
            segment_weights={"S1": 0.5, "S2": 0.5},
            predeclared_pair_assignments=assignments_for(records),
            boundary_jobs_per_wave=6,
            boundary_waves_max=2,
            boundary_reserved=12,
            available_boundary_reserve=12,
            finalist_reserved=20,
        )

        self.assertEqual("resolved", result.status)
        self.assertEqual(
            ["locked_segment_coverage_incomplete"],
            result.decision_audit["waves"][0]["decision_reasons"],
        )
        self.assertEqual("stop_resolved", result.decision_audit["waves"][1]["decision"])


class PairwiseCliTests(unittest.TestCase):
    def run_cli(
        self,
        manifest: Path,
        screening_results: Path,
        responses: Path,
        output: Path,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "aggregate-screening.py"),
                "boundary",
                "--manifest",
                str(manifest),
                "--screening-results",
                str(screening_results),
                "--responses",
                str(responses),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_success_cli_resolves_frozen_boundary_without_maxdiff_pooling(self):
        records = boundary_fixture()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(
                json.dumps(matching_manifest(records=records)), encoding="utf-8"
            )
            screening.write_text(
                json.dumps(matching_screening_result(records)), encoding="utf-8"
            )

            completed = self.run_cli(
                manifest,
                screening,
                ROOT / "conformance" / "fixtures" / "boundary-responses.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("resolved", payload["status"])
        self.assertEqual(["V4", "V5"], payload["selected_boundary_ids"])
        self.assertEqual(["F1", "V4", "V5"], payload["proposed_finalist_ids"])
        self.assertEqual({"V4", "V5", "V6"}, set(payload["utilities"]))
        self.assertNotIn("combined_utility", json.dumps(payload))
        self.assertFalse(payload["decision_audit"]["maxdiff_utilities_pooled"])
        self.assertEqual(
            2000, payload["model_diagnostics"]["bootstrap"]["requested_fits"]
        )

    def test_cli_rejects_non_protocol_bootstrap_count(self):
        records = boundary_fixture()
        manifest_payload = matching_manifest(records=records)
        manifest_payload["model"]["bootstrap_count"] = 200
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            screening.write_text(
                json.dumps(matching_screening_result(records)), encoding="utf-8"
            )

            completed = self.run_cli(
                manifest,
                screening,
                ROOT / "conformance" / "fixtures" / "boundary-responses.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["status"])
        self.assertIn("corrupt_boundary_configuration", payload["status_reasons"])
        self.assertIn(
            "exactly 2000",
            " ".join(payload["model_diagnostics"]["input_errors"]),
        )

    def test_cli_rejects_unregularized_pairwise_model(self):
        records = boundary_fixture()
        manifest_payload = matching_manifest(records=records)
        manifest_payload["model"]["pairwise_penalty_lambda"] = 0.0
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            screening.write_text(
                json.dumps(matching_screening_result(records)), encoding="utf-8"
            )

            completed = self.run_cli(
                manifest,
                screening,
                ROOT / "conformance" / "fixtures" / "boundary-responses.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["status"])
        self.assertIn("corrupt_boundary_configuration", payload["status_reasons"])
        self.assertIn(
            "penalty_lambda must be a finite positive number",
            " ".join(payload["model_diagnostics"]["input_errors"]),
        )

    def test_invalid_screening_prerequisite_writes_invalid_json_and_nonzero_exit(self):
        records = boundary_fixture()
        screening_payload = matching_screening_result(records)
        screening_payload["validity_status"] = "invalid"
        screening_payload["classifications"] = {}
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(
                json.dumps(matching_manifest(records=records)), encoding="utf-8"
            )
            screening.write_text(json.dumps(screening_payload), encoding="utf-8")

            completed = self.run_cli(
                manifest,
                screening,
                ROOT / "conformance" / "fixtures" / "boundary-responses.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual("invalid", payload["status"])
        self.assertEqual({}, payload["utilities"])
        self.assertIn("screening_result_not_valid", payload["status_reasons"])
        reserve = payload["decision_audit"]["reserve"]
        self.assertEqual(len(records), reserve["boundary_jobs_observed"])
        self.assertEqual(len(records), reserve["boundary_jobs_consumed"])
        self.assertEqual(0, reserve["boundary_jobs_remaining"])

    def test_disconnected_cli_returns_unresolved_without_utilities(self):
        records = boundary_fixture("boundary-responses-disconnected.jsonl")
        classifications = {
            creative_id: "boundary_candidate" for creative_id in ("V4", "V5", "V6", "V7")
        }
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(
                json.dumps(
                    matching_manifest(
                        records=records,
                        creative_ids=("V4", "V5", "V6", "V7"),
                        shortlist_size=2,
                    )
                ),
                encoding="utf-8",
            )
            screening_payload = matching_screening_result(
                records, classifications=classifications
            )
            screening_payload["requested_top_k"] = 2
            screening.write_text(json.dumps(screening_payload), encoding="utf-8")

            completed = self.run_cli(
                manifest,
                screening,
                ROOT
                / "conformance"
                / "fixtures"
                / "boundary-responses-disconnected.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("unresolved", payload["status"])
        self.assertEqual({}, payload["utilities"])
        self.assertIn("comparison_graph_disconnected", payload["status_reasons"])

    def test_missing_boundary_plan_is_invalid_instead_of_inventing_jobs(self):
        records = boundary_fixture()
        screening_payload = matching_screening_result(records)
        screening_payload.pop("boundary_plan")
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            output = temp / "boundary.json"
            manifest.write_text(
                json.dumps(matching_manifest(records=records)), encoding="utf-8"
            )
            screening.write_text(json.dumps(screening_payload), encoding="utf-8")

            completed = self.run_cli(
                manifest,
                screening,
                ROOT / "conformance" / "fixtures" / "boundary-responses.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["status"])
        self.assertIn("predeclared_boundary_plan_missing", payload["status_reasons"])

    def test_known_segment_subset_is_accepted_while_next_wave_is_pending(self):
        records = boundary_fixture()
        for record in records:
            record["boundary_wave"] = (
                1 if record["segment_id"] == "growth_leader" else 2
            )
        wave_one = [
            record for record in records if record["segment_id"] == "growth_leader"
        ]
        manifest_payload = matching_manifest(records=records)
        manifest_payload["maximum_synthetic_panelists"] = 63
        manifest_payload["synthetic_replicate_capacity"].update(
            boundary_reserved=12,
            boundary_jobs_per_wave=6,
            boundary_waves_max=2,
        )
        screening_payload = matching_screening_result(records)

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            responses = temp / "wave-one.jsonl"
            output = temp / "boundary.json"
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            screening.write_text(json.dumps(screening_payload), encoding="utf-8")
            write_jsonl(responses, wave_one)

            completed = self.run_cli(manifest, screening, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("unresolved", payload["status"])
        self.assertNotIn("response_segment_lock_mismatch", payload["status_reasons"])
        self.assertEqual(
            "awaiting_predeclared_wave_responses",
            payload["decision_audit"]["stopping_decision"]["reason"],
        )
        self.assertEqual(
            ["locked_segment_coverage_incomplete"],
            payload["decision_audit"]["waves"][0]["decision_reasons"],
        )
        self.assertEqual(
            ["boundary-07", "boundary-08", "boundary-09"],
            payload["decision_audit"]["next_wave_job_ids"],
        )

    def test_unknown_segment_is_rejected_by_cli(self):
        records = boundary_fixture()
        manifest_payload = matching_manifest(records=records)
        screening_payload = matching_screening_result(records)
        records[0]["segment_id"] = "unknown_segment"

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            screening = temp / "screening.json"
            responses = temp / "unknown-segment.jsonl"
            output = temp / "boundary.json"
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            screening.write_text(json.dumps(screening_payload), encoding="utf-8")
            write_jsonl(responses, records)

            completed = self.run_cli(manifest, screening, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(4, completed.returncode)
        self.assertEqual("invalid", payload["status"])
        self.assertIn("response_segment_lock_mismatch", payload["status_reasons"])
        self.assertIn(
            "unknown_segment",
            " ".join(payload["model_diagnostics"]["input_errors"]),
        )


if __name__ == "__main__":
    unittest.main()
