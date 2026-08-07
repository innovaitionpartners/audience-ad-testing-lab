from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from audience_lab.maxdiff import (  # noqa: E402
    IndexedObservation,
    MaxDiffConfig,
    classify_top_k_frequency,
    compute_analysis_weights,
    fit_maxdiff,
    maxdiff_loss_and_gradient,
    screen_shortlist,
    usable_participation_counts,
)
from audience_lab.audience_allocation import (  # noqa: E402
    ALLOCATION_REQUEST_VERSION,
    allocate_stage_profiles,
    validate_allocation_plan,
)
from audience_lab.assignments import (  # noqa: E402
    build_boundary_reserve_slots,
    build_finalist_reserve_slots,
)
from audience_lab.audience_library import resolve_audience_panel  # noqa: E402
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.contracts import validate_manifest  # noqa: E402
from audience_lab.planning import v3_allocation_profiles  # noqa: E402
from conformance import test_v3_profile_rosters as roster_contract  # noqa: E402


PROTECTED_MAXIMUM_CAPACITY = {
    "screening_planned": 225,
    "boundary_reserved": 16,
    "finalist_reserved": 8,
    "required_total": 249,
    "ceiling": 249,
    "ceiling_satisfied": True,
    "boundary_jobs_per_wave": 8,
    "boundary_waves_max": 2,
    "shortfall": 0,
}
PROTECTED_MAXIMUM_SHA256 = {
    "capacity": (
        "8b6ea9fb9c35df0dbe9a7aa5d68ff608e5c660de287dd3a28b312f6b78e2b88c"
    ),
    "screening_slot_ids": (
        "b03291bd162ac5efc7a7a5760c011b22f4c7271d4560feac41bf3fe9e1bfbcef"
    ),
    "boundary_slot_ids": (
        "2de384d76a3acb9b31923b57aab406386e11ca0917a13d5987a7dcb64466777c"
    ),
    "finalist_slot_ids": (
        "3943073c977770359f632ce609be97a64f8af352d569d75243d3be6acd51b938"
    ),
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


def fixture(name: str):
    return json.loads(
        (ROOT / "conformance" / "fixtures" / name).read_text(encoding="utf-8")
    )


def recovery_config() -> dict:
    return json.loads(
        (SKILL_ROOT / "references" / "screening-recovery-config.json").read_text(
            encoding="utf-8"
        )
    )


def compact_observation(
    response_id: str,
    block: list[str],
    best: str,
    worst: str,
    *,
    segment_id: str = "S1",
    archetype_id: str = "A1",
    usable: bool = True,
) -> dict:
    return {
        "response_id": response_id,
        "segment_id": segment_id,
        "persona_archetype_id": archetype_id,
        "assigned_variation_ids": block,
        "comparative_choice": {
            "status": "best_worst",
            "best_variation_id": best,
            "weakest_variation_id": worst,
        },
        "usable_maxdiff_block": usable,
    }


def full_response_for_block(
    block: list[str], response_number: int, *, study_id: str = "study-maxdiff-cli"
) -> dict:
    source = json.loads(
        (ROOT / "conformance" / "fixtures" / "screening-responses-valid.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    response = deepcopy(source)
    old_block = list(response["assigned_variation_ids"])
    replacements = dict(zip(old_block, block, strict=True))
    suffix = str(response_number)
    response.update(
        {
            "study_id": study_id,
            "response_id": f"response-{suffix}",
            "synthetic_replicate_id": f"replicate-{suffix}",
            "reviewer_dispatch_id": f"dispatch-{suffix}",
            "persona_archetype_id": f"archetype-{suffix}",
            "segment_id": "S1",
            "assigned_variation_ids": list(block),
            "blind_labels": {
                replacements[creative_id]: label
                for creative_id, label in response["blind_labels"].items()
            },
            "shown_order": [replacements[item] for item in response["shown_order"]],
        }
    )
    for reaction in response["per_creative_reactions"]:
        reaction["variation_id"] = replacements[reaction["variation_id"]]
        reaction["reaction_id"] = f"{reaction['reaction_id']}-{suffix}"
    choice = response["comparative_choice"]
    choice["best_variation_id"] = replacements[choice["best_variation_id"]]
    choice["weakest_variation_id"] = replacements[choice["weakest_variation_id"]]
    choice["frozen_reaction_ids"] = [
        reaction["reaction_id"] for reaction in response["per_creative_reactions"]
    ]
    return response


def full_job_for_response(response: dict) -> dict:
    return {
        "study_id": response.get("study_id"),
        "response_id": response.get("response_id"),
        "record_type": response.get("record_type"),
        "method": response.get("method"),
        "synthetic_replicate_id": response.get("synthetic_replicate_id"),
        "dispatch_id": response.get("reviewer_dispatch_id"),
        "persona_archetype_id": response.get("persona_archetype_id"),
        "segment_id": response.get("segment_id"),
        "profile_snapshot": deepcopy(response.get("profile_snapshot")),
        "context_attribute_provenance": deepcopy(
            response.get("context_attribute_provenance")
        ),
        "worker_context_isolation": response.get("worker_context_isolation"),
        "human_sample_independence": response.get("human_sample_independence"),
        "variation_ids": deepcopy(response.get("assigned_variation_ids")),
        "blind_labels": deepcopy(response.get("blind_labels")),
        "shown_order": deepcopy(response.get("shown_order")),
        "reaction_protocol": response.get("reaction_protocol"),
        "reaction_prompts": ["Review this blind creative."]
        * len(response.get("shown_order", ())),
        "comparison_prompt": "Choose strongest and weakest.",
    }


def matching_manifest(
    *,
    study_id: str = "study-maxdiff-cli",
    creative_ids: tuple[str, ...] = ("V1", "V2", "V3", "V4"),
) -> dict:
    manifest = fixture("manifest-valid.json")
    manifest["study_id"] = study_id
    manifest["requested_shortlist_size"] = 2
    manifest["audience_lock"]["segment_weights"] = {"S1": 1.0}
    manifest["audience_lock"]["unique_archetypes"] = 2
    manifest["outputs"]["creative_asset_hashes"] = {
        creative_id: f"sha256:test-{index:03d}"
        for index, creative_id in enumerate(creative_ids, 1)
    }
    manifest["assignment"]["usable_participations_per_creative"] = {
        creative_id: 9 for creative_id in creative_ids
    }
    return manifest


class MaxDiffLikelihoodTests(unittest.TestCase):
    def test_joint_best_worst_gradient_matches_finite_difference(self):
        observations = (
            IndexedObservation((0, 1, 2, 3), 0, 3),
            IndexedObservation((0, 1, 2, 3), 1, 2),
        )
        weights = np.asarray([1.25, 0.75])
        utilities = np.asarray([0.4, 0.1, -0.2, -0.3])
        loss, gradient = maxdiff_loss_and_gradient(
            utilities, observations, weights, penalty_lambda=0.1
        )

        epsilon = 1e-6
        numerical = np.empty_like(utilities)
        for index in range(len(utilities)):
            step = np.zeros_like(utilities)
            step[index] = epsilon
            right = maxdiff_loss_and_gradient(
                utilities + step, observations, weights, 0.1
            )[0]
            left = maxdiff_loss_and_gradient(
                utilities - step, observations, weights, 0.1
            )[0]
            numerical[index] = (right - left) / (2 * epsilon)

        self.assertTrue(math.isfinite(loss))
        np.testing.assert_allclose(gradient, numerical, atol=2e-6, rtol=2e-6)

    def test_recovers_known_order_and_sum_to_zero_identification(self):
        observations = fixture("maxdiff-recovery.json")["observations"]
        fit = fit_maxdiff(observations, MaxDiffConfig(penalty_lambda=0.1))

        order = sorted(fit.utilities, key=fit.utilities.get, reverse=True)
        self.assertTrue(fit.success)
        self.assertTrue(fit.connected)
        self.assertTrue(fit.identified)
        self.assertEqual(["V1", "V2", "V3", "V4"], order)
        self.assertAlmostEqual(0.0, sum(fit.utilities.values()), places=9)

    def test_locked_segment_weights_override_oversampling(self):
        observations = fixture("maxdiff-segment-weighted.json")["observations"]
        weights = compute_analysis_weights(observations, {"S1": 0.8, "S2": 0.2})
        segment_ids = [item["segment_id"] for item in observations]

        self.assertAlmostEqual(
            len(observations) * 0.8,
            sum(weight for weight, segment in zip(weights, segment_ids) if segment == "S1"),
        )
        self.assertAlmostEqual(
            len(observations) * 0.2,
            sum(weight for weight, segment in zip(weights, segment_ids) if segment == "S2"),
        )
        result = screen_shortlist(
            observations,
            {"S1": 0.8, "S2": 0.2},
            top_k=2,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=30, seed=8),
        )
        unweighted = fit_maxdiff(observations, MaxDiffConfig(penalty_lambda=0.1))

        self.assertEqual("V1", result.ranked_ids[0])
        self.assertNotEqual("V1", unweighted.ranked_ids[0])

    def test_disconnected_graph_is_not_fit_or_reported(self):
        observations = fixture("maxdiff-disconnected.json")["observations"]
        fit = fit_maxdiff(observations, MaxDiffConfig(penalty_lambda=0.1))
        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=2,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=20),
        )

        self.assertFalse(fit.success)
        self.assertFalse(fit.connected)
        self.assertEqual({}, fit.utilities)
        self.assertEqual("invalid", result.validity_status)
        self.assertEqual({}, result.utilities)
        self.assertEqual(
            {"unresolved"}, set(result.classifications.values())
        )
        self.assertIn("comparison_graph_disconnected", result.validity_reasons)


class MaxDiffGateTests(unittest.TestCase):
    def test_usable_coverage_counts_only_accepted_usable_blocks(self):
        observations = fixture("maxdiff-recovery.json")["observations"][:2]
        unusable = deepcopy(observations[0])
        unusable["response_id"] = "not-usable"
        unusable["usable_maxdiff_block"] = False
        observations.append(unusable)

        counts = usable_participation_counts(observations)

        self.assertEqual({"V1": 2, "V2": 2, "V3": 2, "V4": 2}, counts)

    def test_bootstrap_resamples_whole_records_and_enforces_fit_floor(self):
        observations = [
            compact_observation("bridge-left", ["A", "B", "C", "D"], "A", "C"),
            compact_observation("bridge-right", ["D", "E", "F", "G"], "D", "G"),
        ]
        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=2,
            config=MaxDiffConfig(
                penalty_lambda=0.1,
                bootstrap_count=80,
                successful_fit_floor=0.95,
                seed=19,
            ),
        )

        self.assertTrue(result.diagnostics["connected"])
        self.assertLess(result.diagnostics["bootstrap"]["successful_fit_rate"], 0.95)
        self.assertFalse(result.diagnostics["gates"]["stability"])
        self.assertEqual("exploratory", result.validity_status)

    def test_threshold_boundaries_are_explicit_product_rules(self):
        self.assertEqual("clear_finalist", classify_top_k_frequency(0.90, 0.90, 0.10))
        self.assertEqual("clear_non_finalist", classify_top_k_frequency(0.10, 0.90, 0.10))
        self.assertEqual(
            "boundary_candidate", classify_top_k_frequency(0.100001, 0.90, 0.10)
        )
        self.assertEqual(
            "boundary_candidate", classify_top_k_frequency(0.899999, 0.90, 0.10)
        )

    def test_leave_one_archetype_out_ignores_rank_only_swaps_within_top_k(self):
        block = ["V1", "V2", "V3", "V4"]
        observations = [
            compact_observation("A-1", block, "V1", "V4", archetype_id="A"),
            compact_observation("B-1", block, "V2", "V4", archetype_id="B"),
            compact_observation("C-1", block, "V1", "V3", archetype_id="C"),
        ]

        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=2,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=1, seed=5),
        )

        sensitivity = result.archetype_sensitivity
        omitted_a = next(
            item
            for item in sensitivity["results"]
            if item["omitted_archetype_id"] == "A"
        )
        self.assertEqual(
            set(result.ranked_ids[:2]), set(omitted_a["ranked_ids"][:2])
        )
        self.assertFalse(omitted_a["top_k_changed"])
        self.assertTrue(sensitivity["top_k_consistent"])
        self.assertEqual([], sensitivity["top_k_changed_for"])

    def test_leave_one_archetype_out_detects_top_k_membership_change(self):
        block = ["V1", "V2", "V3", "V4"]
        observations = [
            compact_observation(f"A-{index}", block, "V1", "V4", archetype_id="A")
            for index in range(4)
        ]
        observations.extend(
            compact_observation(f"B-{index}", block, "V2", "V1", archetype_id="B")
            for index in range(3)
        )
        observations.extend(
            compact_observation(f"C-{index}", block, "V3", "V4", archetype_id="C")
            for index in range(2)
        )

        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=1,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=25, seed=5),
        )

        sensitivity = result.archetype_sensitivity
        omitted_b = next(
            item
            for item in sensitivity["results"]
            if item["omitted_archetype_id"] == "B"
        )
        self.assertEqual(3, sensitivity["unique_archetypes"])
        self.assertNotEqual(
            set(result.ranked_ids[:1]), set(omitted_b["ranked_ids"][:1])
        )
        self.assertTrue(omitted_b["top_k_changed"])
        self.assertIn("B", sensitivity["top_k_changed_for"])
        self.assertFalse(sensitivity["top_k_consistent"])

    def test_fixed_seed_reproduces_bootstrap_and_sensitivity_outputs(self):
        observations = fixture("maxdiff-recovery.json")["observations"]
        config = MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=40, seed=37)

        first = screen_shortlist(observations, {"S1": 1.0}, top_k=2, config=config)
        second = screen_shortlist(observations, {"S1": 1.0}, top_k=2, config=config)

        self.assertEqual(first.as_dict(), second.as_dict())

    def test_exploratory_recovery_config_cannot_emit_valid(self):
        observations = fixture("maxdiff-recovery.json")["observations"]
        config_payload = recovery_config()
        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=2,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=30, seed=4),
            recovery_config=config_payload,
        )

        self.assertNotEqual("valid", result.validity_status)
        self.assertIn("recovery_configuration_exploratory_only", result.validity_reasons)
        self.assertNotIn("clear_finalist", set(result.classifications.values()))
        self.assertNotIn("clear_non_finalist", set(result.classifications.values()))

    def test_public_recovery_config_has_all_versioned_gate_dimensions(self):
        payload = recovery_config()
        required = {
            "version",
            "calibration_status",
            "library_size_bands",
            "shortlist_size_bands",
            "segment_count",
            "tie_inability_band",
            "utility_separation_band",
            "planned_participation_floor",
            "usable_participation_floor",
            "bootstrap_count",
            "successful_fit_floor",
            "shortlist_thresholds",
        }

        self.assertEqual(required, set(payload))
        self.assertEqual("exploratory_only", payload["calibration_status"])
        self.assertEqual(2000, payload["bootstrap_count"])

    def test_calibrated_configuration_rejects_weakened_protocol_constants_and_empty_bands(
        self,
    ):
        observations = fixture("maxdiff-recovery.json")["observations"]
        mutations = {
            "too_few_bootstraps": lambda payload: payload.update(bootstrap_count=1999),
            "weak_fit_floor": lambda payload: payload.update(
                successful_fit_floor=0.9499999999
            ),
            "mutable_finalist_threshold": lambda payload: payload[
                "shortlist_thresholds"
            ].update(clear_finalist=0.8999999999),
            "mutable_non_finalist_threshold": lambda payload: payload[
                "shortlist_thresholds"
            ].update(clear_non_finalist=0.1000000001),
            "empty_library_bands": lambda payload: payload.update(library_size_bands=[]),
            "empty_shortlist_bands": lambda payload: payload.update(shortlist_size_bands=[]),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                config_payload = recovery_config()
                config_payload["calibration_status"] = "calibrated"
                mutate(config_payload)
                with self.assertRaises(ValueError):
                    screen_shortlist(
                        observations,
                        {"S1": 1.0},
                        top_k=2,
                        config=MaxDiffConfig(penalty_lambda=0.1),
                        recovery_config=config_payload,
                        planned_participations_per_creative=9,
                    )

    def test_compliant_calibrated_configuration_emits_valid_after_2000_resamples(self):
        block = ["V1", "V2", "V3", "V4"]
        observations = [
            compact_observation(
                f"A{index % 4}-{index}",
                block,
                "V1",
                "V4",
                archetype_id=f"A{index % 4}",
            )
            for index in range(8)
        ]
        config_payload = recovery_config()
        config_payload["calibration_status"] = "calibrated"
        config_payload["library_size_bands"] = [
            {"name": "small_calibrated_library", "minimum": 4, "maximum": 10}
        ]
        config_payload["shortlist_size_bands"] = [
            {"name": "small_calibrated_shortlist", "minimum": 2, "maximum": 3}
        ]
        config_payload["utility_separation_band"]["maximum_log_utility_gap"] = 100.0

        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=2,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=2000, seed=42),
            recovery_config=config_payload,
            planned_participations_per_creative=9,
        )

        self.assertEqual("valid", result.validity_status)
        self.assertEqual((), result.validity_reasons)
        self.assertTrue(all(result.diagnostics["gates"].values()))
        self.assertEqual(2000, result.diagnostics["bootstrap"]["requested_fits"])
        self.assertIn("clear_finalist", set(result.classifications.values()))

        cached_bootstrap = (
            dict(result.top_k_inclusion_frequencies),
            dict(result.diagnostics["bootstrap"]),
        )
        weakened_models = {
            "near_equal_fit_floor": MaxDiffConfig(
                penalty_lambda=0.1,
                bootstrap_count=2000,
                successful_fit_floor=0.9499999999,
                seed=42,
            ),
            "near_equal_finalist_threshold": MaxDiffConfig(
                penalty_lambda=0.1,
                bootstrap_count=2000,
                clear_finalist_threshold=0.8999999999,
                seed=42,
            ),
            "near_equal_non_finalist_threshold": MaxDiffConfig(
                penalty_lambda=0.1,
                bootstrap_count=2000,
                clear_non_finalist_threshold=0.1000000001,
                seed=42,
            ),
        }
        with patch(
            "audience_lab.maxdiff._bootstrap_stability",
            return_value=cached_bootstrap,
        ):
            for name, weakened_model in weakened_models.items():
                with self.subTest(name=name):
                    weakened_result = screen_shortlist(
                        observations,
                        {"S1": 1.0},
                        top_k=2,
                        config=weakened_model,
                        recovery_config=config_payload,
                        planned_participations_per_creative=9,
                    )

                    self.assertNotEqual("valid", weakened_result.validity_status)
                    self.assertFalse(
                        weakened_result.diagnostics["gates"][
                            "model_matches_recovery_config"
                        ]
                    )
                    self.assertIn(
                        "manifest_model_recovery_configuration_mismatch",
                        weakened_result.validity_reasons,
                    )


class MaxDiffCliTests(unittest.TestCase):
    def run_cli(self, manifest: Path, responses: Path, output: Path) -> subprocess.CompletedProcess:
        manifest_for_run = output.with_name("screening-manifest.json")
        jobs_path = output.with_name("screening-jobs.json")
        try:
            records = [
                json.loads(line)
                for line in responses.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            records = [record for record in records if isinstance(record, dict)]
        except (OSError, json.JSONDecodeError):
            records = []
        if not records:
            records = [full_response_for_block(["V1", "V2", "V3", "V4"], 999)]
        jobs = [full_job_for_response(record) for record in records]
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        capacity = manifest_payload["synthetic_replicate_capacity"]
        capacity["screening_planned"] = len(jobs)
        required = (
            capacity["screening_planned"]
            + capacity["boundary_reserved"]
            + capacity["finalist_reserved"]
        )
        manifest_payload["maximum_synthetic_panelists"] = max(
            manifest_payload["maximum_synthetic_panelists"], required
        )
        capacity["ceiling_satisfied"] = True
        manifest_for_run.write_text(json.dumps(manifest_payload), encoding="utf-8")
        jobs_path.write_text(
            json.dumps(
                {
                    "study_id": manifest_payload["study_id"],
                    "method": "partial_exposure_maxdiff",
                    "record_type": "screening_response",
                    "synthetic_replicate_jobs": jobs,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "aggregate-screening.py"),
                "screening",
                "--manifest",
                str(manifest_for_run),
                "--jobs",
                str(jobs_path),
                "--responses",
                str(responses),
                "--recovery-config",
                str(SKILL_ROOT / "references" / "screening-recovery-config.json"),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_corrupt_jsonl_writes_useful_invalid_result_before_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest.write_text(json.dumps(matching_manifest()), encoding="utf-8")
            responses.write_text("{not-json}\n", encoding="utf-8")

            completed = self.run_cli(manifest, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertEqual({}, payload["utilities"])
        self.assertTrue(payload["validity_reasons"])

    def test_disconnected_cli_input_writes_invalid_result(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest.write_text(
                json.dumps(
                    matching_manifest(
                        creative_ids=("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")
                    )
                ),
                encoding="utf-8",
            )
            records = [
                full_response_for_block(["V1", "V2", "V3", "V4"], 1),
                full_response_for_block(["V5", "V6", "V7", "V8"], 2),
            ]
            responses.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            completed = self.run_cli(manifest, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertEqual({}, payload["utilities"])
        self.assertIn("comparison_graph_disconnected", payload["validity_reasons"])

    def test_manifest_roster_creative_with_no_observations_is_retained_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest_path.write_text(
                json.dumps(
                    matching_manifest(creative_ids=("V1", "V2", "V3", "V4", "V5"))
                ),
                encoding="utf-8",
            )
            response = full_response_for_block(["V1", "V2", "V3", "V4"], 1)
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")

            completed = self.run_cli(manifest_path, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertEqual(5, payload["model_diagnostics"]["fit"]["creative_count"])
        self.assertEqual(
            0,
            payload["model_diagnostics"]["usable_participations_per_creative"]["V5"],
        )
        self.assertIn("comparison_graph_disconnected", payload["validity_reasons"])
        self.assertIn("usable_participation_floor_not_met", payload["validity_reasons"])

    def test_response_creative_outside_manifest_roster_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest_path.write_text(json.dumps(matching_manifest()), encoding="utf-8")
            response = full_response_for_block(["V1", "V2", "V3", "V5"], 1)
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")

            completed = self.run_cli(manifest_path, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertIn("response_creative_out_of_roster", payload["validity_reasons"])

    def test_valid_json_malformed_scalar_ids_stay_inside_invalid_envelope(self):
        malformed_values = (["not", "a", "scalar"], {"not": "a scalar"})
        for field in ("response_id", "synthetic_replicate_id"):
            for malformed in malformed_values:
                with self.subTest(field=field, malformed=type(malformed).__name__):
                    with tempfile.TemporaryDirectory() as directory:
                        temp = Path(directory)
                        manifest_path = temp / "manifest.json"
                        responses = temp / "responses.jsonl"
                        output = temp / "result.json"
                        manifest_path.write_text(
                            json.dumps(matching_manifest()), encoding="utf-8"
                        )
                        response = full_response_for_block(
                            ["V1", "V2", "V3", "V4"], 1
                        )
                        response[field] = malformed
                        responses.write_text(
                            json.dumps(response) + "\n", encoding="utf-8"
                        )

                        completed = self.run_cli(manifest_path, responses, output)
                        payload = json.loads(output.read_text(encoding="utf-8"))

                    self.assertNotEqual(0, completed.returncode)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertEqual("invalid", payload["validity_status"])
                    self.assertIn("response_contract_invalid", payload["validity_reasons"])

    def test_duplicate_ids_write_invalid_envelope_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest_path.write_text(json.dumps(matching_manifest()), encoding="utf-8")
            first = full_response_for_block(["V1", "V2", "V3", "V4"], 1)
            second = full_response_for_block(["V1", "V2", "V3", "V4"], 2)
            second["response_id"] = first["response_id"]
            second["synthetic_replicate_id"] = first["synthetic_replicate_id"]
            responses.write_text(
                json.dumps(first) + "\n" + json.dumps(second) + "\n",
                encoding="utf-8",
            )

            completed = self.run_cli(manifest_path, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertIn("duplicate_response_id", payload["validity_reasons"])
        self.assertIn("duplicate_synthetic_replicate_id", payload["validity_reasons"])

    def test_unsupported_manifest_model_writes_invalid_result(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest = matching_manifest()
            manifest["model"]["penalty_type"] = "l1"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            response = full_response_for_block(["V1", "V2", "V3", "V4"], 1)
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")

            completed = self.run_cli(manifest_path, responses, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertIn(
            "corrupt_aggregation_configuration", payload["validity_reasons"]
        )

    def test_exact_smoke_inputs_still_write_json_when_contracts_do_not_match(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "screening-results.json"
            completed = self.run_cli(
                ROOT / "conformance" / "fixtures" / "manifest-valid.json",
                ROOT / "conformance" / "fixtures" / "screening-responses-valid.jsonl",
                output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("invalid", payload["validity_status"])
        self.assertIn("response_study_id_mismatch", payload["validity_reasons"])

    def test_output_avoids_banned_inference_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest = temp / "manifest.json"
            responses = temp / "responses.jsonl"
            output = temp / "result.json"
            manifest.write_text(json.dumps(matching_manifest()), encoding="utf-8")
            response = full_response_for_block(["V1", "V2", "V3", "V4"], 1)
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")

            completed = self.run_cli(manifest, responses, output)
            rendered = output.read_text(encoding="utf-8").lower()
            payload = json.loads(rendered)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("exploratory", payload["validity_status"])
        self.assertEqual({"unresolved"}, set(payload["classifications"].values()))
        for banned in (
            "population confidence",
            "population_confidence",
            "preference percentage",
            "preference_percentage",
            "market share",
            "market_share",
        ):
            self.assertNotIn(banned, rendered)


@unittest.skipUnless(
    os.environ.get("AUDIENCE_LAB_RUN_MAX_DESIGN_BENCHMARK") == "1",
    "set AUDIENCE_LAB_RUN_MAX_DESIGN_BENCHMARK=1 for the 100-creative benchmark",
)
class MaxDiffMaximumDesignBenchmark(unittest.TestCase):
    def test_100_creative_design_runs_all_2000_protocol_resamples(self):
        creative_ids = tuple(f"V{index:03d}" for index in range(1, 101))
        rng = np.random.default_rng(20260722)
        observations = []
        for round_index in range(10):
            shuffled = list(rng.permutation(creative_ids))
            for block_index in range(25):
                block = shuffled[block_index * 4 : (block_index + 1) * 4]
                observations.append(
                    compact_observation(
                        f"R{round_index:02d}-{block_index:02d}",
                        block,
                        min(block),
                        max(block),
                        archetype_id=f"A{round_index:02d}",
                    )
                )
        started = time.perf_counter()
        result = screen_shortlist(
            observations,
            {"S1": 1.0},
            top_k=6,
            config=MaxDiffConfig(penalty_lambda=0.1, bootstrap_count=2000, seed=20260722),
            recovery_config=recovery_config(),
            creative_ids=creative_ids,
            planned_participations_per_creative=10,
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(100, result.diagnostics["fit"]["creative_count"])
        self.assertEqual(2000, result.diagnostics["bootstrap"]["requested_fits"])
        print(f"maximum_design_100_creatives_2000_resamples_seconds={elapsed:.3f}")

    def _run_production_maximum_plan(
        self,
        *,
        root: Path,
        package_path: Path,
        resolution_path: Path,
        output_name: str,
    ) -> dict[str, object]:
        request = {
            "study_id": "maximum-design",
            "creative_ids": [
                f"creative-{index:03d}" for index in range(1, 101)
            ],
            "creative_format": "static_image",
            "requested_shortlist_size": 6,
            "maximum_synthetic_panelists": 249,
            "audience_panel": {
                "source": "file",
                "package_path": str(package_path),
            },
        }
        request_path = root / f"{output_name}-request.json"
        output_path = resolution_path.parents[1] / output_name
        request_path.write_bytes(canonical_bytes(request))
        completed = subprocess.run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "plan-large-library.py"),
                str(request_path),
                str(output_path),
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
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stderr + completed.stdout,
        )
        return json.loads(output_path.read_text(encoding="utf-8"))

    def _production_maximum_plans(
        self,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v2_root = root / "v2"
            fixtures = (
                ROOT / "conformance" / "fixtures" / "audience-research"
            )
            brief = json.loads(
                (fixtures / "approved-brief.json").read_text(
                    encoding="utf-8"
                )
            )
            panel = json.loads(
                (fixtures / "approved-panel.json").read_text(
                    encoding="utf-8"
                )
            )
            v2_package = build_audience_package(
                brief,
                panel,
                v2_root / "package",
            ).package_zip_path
            scope = {
                key: deepcopy(panel["audience_scope"][key])
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
                {"source": "file", "package_path": str(v2_package)},
                scope,
                run_dir=v2_root,
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            v2_plan = self._run_production_maximum_plan(
                root=v2_root,
                package_path=v2_package,
                resolution_path=v2_root / "audience" / "resolution.json",
                output_name="v2-plan.json",
            )

            harness = roster_contract.V3ProfileRosterTests()
            harness.setUp()
            v3_package, _run, v3_resolution = harness._resolved_run(
                root / "v3"
            )
            v3_first = self._run_production_maximum_plan(
                root=root / "v3",
                package_path=v3_package,
                resolution_path=v3_resolution,
                output_name="v3-plan-a.json",
            )
            v3_second = self._run_production_maximum_plan(
                root=root / "v3",
                package_path=v3_package,
                resolution_path=v3_resolution,
                output_name="v3-plan-b.json",
            )
            v3_envelope = json.loads(
                v3_resolution.read_text(encoding="utf-8")
            )
            return v2_plan, v3_first, v3_second, v3_envelope

    def _measure_production_allocation_memory(
        self,
        production_plan: dict[str, object],
        v3_envelope: dict[str, object],
    ) -> tuple[dict[str, object], float, int]:
        """Measure allocation itself from the production planner boundary."""

        tracemalloc.start()
        started = time.perf_counter()
        try:
            segment_ids = production_plan["reported_segment_ids"]
            capacity = production_plan["synthetic_replicate_capacity"]
            screening_slots = [
                {
                    "slot_id": job["synthetic_replicate_id"],
                    "reported_segment_id": job["segment_id"],
                }
                for job in production_plan["assignment"][
                    "synthetic_replicate_jobs"
                ]
            ]
            boundary_slots = build_boundary_reserve_slots(
                segment_ids,
                jobs_per_wave=capacity["boundary_jobs_per_wave"],
                waves_max=capacity["boundary_waves_max"],
            )
            finalist_slots = build_finalist_reserve_slots(
                capacity["finalist_reserved"]
            )
            profiles = v3_allocation_profiles(v3_envelope)
            must_cover_group_ids = sorted(
                {
                    group_id
                    for profile in profiles
                    for group_id in profile["must_cover_group_ids"]
                }
            )
            analysis_weights = {
                str(segment_id): float(weight)
                for segment_id, weight in v3_envelope["audience_lock"][
                    "segment_weights"
                ].items()
            }
            stable_seed = (
                "maximum-design:29:audience-profile-allocation-v1"
            )

            def allocate(
                stage: str,
                roster_name: str,
                slots: list[dict[str, object]],
            ) -> dict[str, object]:
                return allocate_stage_profiles(
                    {
                        "schema_version": ALLOCATION_REQUEST_VERSION,
                        "stage": stage,
                        "stage_roster_id": (
                            f"maximum-design:{roster_name}"
                        ),
                        "stable_seed": stable_seed,
                        "allocation_basis": v3_envelope[
                            "allocation_basis"
                        ],
                        "slots": slots,
                        "profiles": profiles,
                        "analysis_weights": (
                            {} if stage == "finalist" else analysis_weights
                        ),
                        "must_cover_group_ids": must_cover_group_ids,
                        "maximum_absolute_deviation": 0.05,
                        "allow_directional_allocation": False,
                    }
                )

            roster_core = {
                "schema_version": "audience-profile-rosters-v1",
                "envelope_sha256": production_plan[
                    "audience_profile_rosters"
                ]["envelope_sha256"],
                "screening": allocate(
                    "screening",
                    "screening",
                    screening_slots,
                ),
                "boundary_reserve": allocate(
                    "boundary",
                    "boundary-reserve",
                    boundary_slots,
                ),
                "finalist_reserve": allocate(
                    "finalist",
                    "finalist-reserve",
                    finalist_slots,
                ),
            }
            combined_input = {
                "schema_version": roster_core["schema_version"],
                "study_id": production_plan["study_id"],
                "method": production_plan["method"],
                "maximum_synthetic_panelists": production_plan[
                    "maximum_synthetic_panelists"
                ],
                "synthetic_replicate_capacity": production_plan[
                    "synthetic_replicate_capacity"
                ],
                "assignment_sha256": (
                    "sha256:"
                    + hashlib.sha256(
                        canonical_bytes(production_plan["assignment"])
                    ).hexdigest()
                ),
                "envelope_sha256": roster_core["envelope_sha256"],
                "screening": roster_core["screening"],
                "boundary_reserve": roster_core["boundary_reserve"],
                "finalist_reserve": roster_core["finalist_reserve"],
            }
            measured_rosters = {
                **roster_core,
                "combined_sha256": (
                    "sha256:"
                    + hashlib.sha256(
                        canonical_bytes(combined_input)
                    ).hexdigest()
                ),
            }
            elapsed = time.perf_counter() - started
            _current, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return measured_rosters, elapsed, peak_bytes

    def test_v3_maximum_capacity_rosters_are_bounded_and_deterministic(self):
        started = time.perf_counter()
        v2_plan, v3_first, v3_second, v3_envelope = (
            self._production_maximum_plans()
        )
        planning_elapsed = time.perf_counter() - started

        segment_ids = v2_plan["reported_segment_ids"]
        protected_slots = {
            "screening": [
                job["synthetic_replicate_id"]
                for job in v2_plan["assignment"][
                    "synthetic_replicate_jobs"
                ]
            ],
            "boundary_reserve": [
                item["slot_id"]
                for item in build_boundary_reserve_slots(
                    segment_ids,
                    jobs_per_wave=8,
                    waves_max=2,
                )
            ],
            "finalist_reserve": [
                item["slot_id"]
                for item in build_finalist_reserve_slots(8)
            ],
        }
        self.assertEqual(
            PROTECTED_MAXIMUM_CAPACITY,
            v2_plan["synthetic_replicate_capacity"],
        )
        self.assertEqual(
            PROTECTED_MAXIMUM_SHA256,
            {
                "capacity": hashlib.sha256(
                    canonical_bytes(
                        v2_plan["synthetic_replicate_capacity"]
                    )
                ).hexdigest(),
                "screening_slot_ids": hashlib.sha256(
                    canonical_bytes(protected_slots["screening"])
                ).hexdigest(),
                "boundary_slot_ids": hashlib.sha256(
                    canonical_bytes(
                        protected_slots["boundary_reserve"]
                    )
                ).hexdigest(),
                "finalist_slot_ids": hashlib.sha256(
                    canonical_bytes(
                        protected_slots["finalist_reserve"]
                    )
                ).hexdigest(),
            },
        )
        self.assertEqual(
            v2_plan["synthetic_replicate_capacity"],
            v3_first["synthetic_replicate_capacity"],
        )
        self.assertEqual(
            v2_plan["assignment"],
            v3_first["assignment"],
        )
        self.assertEqual(
            v3_first["audience_profile_rosters"],
            v3_second["audience_profile_rosters"],
        )
        self.assertEqual(
            protected_slots,
            {
                stage: [
                    item["slot_id"]
                    for item in v3_first["audience_profile_rosters"][
                        stage
                    ]["assignments"]
                ]
                for stage in protected_slots
            },
        )
        (
            measured_rosters,
            allocation_elapsed,
            allocation_peak_bytes,
        ) = self._measure_production_allocation_memory(
            v3_first,
            v3_envelope,
        )
        self.assertEqual(
            v3_first["audience_profile_rosters"],
            measured_rosters,
        )
        self.assertLess(allocation_peak_bytes, 64 * 1024 * 1024)

        harness = roster_contract.V3ProfileRosterTests()
        manifest = harness._manifest_from_plan(v3_first)
        manifest["outputs"]["creative_asset_hashes"] = {
            creative_id: f"sha256:{index:064x}"
            for index, creative_id in enumerate(
                [
                    f"creative-{creative_index:03d}"
                    for creative_index in range(1, 101)
                ],
                1,
            )
        }
        self.assertEqual([], validate_manifest(manifest))

        tracemalloc.start()
        validation_started = time.perf_counter()
        for _iteration in range(2):
            for stage in (
                "screening",
                "boundary_reserve",
                "finalist_reserve",
            ):
                validate_allocation_plan(
                    deepcopy(
                        v3_first["audience_profile_rosters"][stage]
                    )
                )
            self.assertEqual([], validate_manifest(deepcopy(manifest)))
        validation_elapsed = time.perf_counter() - validation_started
        _current, validation_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertEqual(
            {
                "screening": 225,
                "boundary_reserve": 16,
                "finalist_reserve": 8,
            },
            {
                stage: len(
                    v3_first["audience_profile_rosters"][stage][
                        "assignments"
                    ]
                )
                for stage in (
                    "screening",
                    "boundary_reserve",
                    "finalist_reserve",
                )
            },
        )
        self.assertLess(validation_peak_bytes, 64 * 1024 * 1024)
        print(
            "maximum_design_v3_production_planning_seconds="
            f"{planning_elapsed:.3f} "
            "production_roster_allocation_seconds="
            f"{allocation_elapsed:.3f} "
            "allocation_peak_bytes="
            f"{allocation_peak_bytes} "
            "production_roster_validation_seconds="
            f"{validation_elapsed:.3f} "
            "validation_peak_bytes="
            f"{validation_peak_bytes} "
            "screening_workers=225 boundary_workers=16 "
            "finalist_workers=8 total_workers=249 deterministic=true"
        )


if __name__ == "__main__":
    unittest.main()
