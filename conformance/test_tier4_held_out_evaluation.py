from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    project_shared_outcome_evidence,
    seal_preregistration as _seal_preregistration,
)
from audience_panel_builder.population.validation.evaluation import (  # noqa: E402
    _is_material_segment,
    _material_segment_gate,
    build_claim_family as _build_claim_family,
    evaluate_held_out_ordering as _evaluate_held_out_ordering,
    issue_tier4_claim as _issue_tier4_claim,
)
from audience_panel_builder.population.validation.statistics import (  # noqa: E402
    InsufficientUncertaintyError,
    Interval,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    approved_design,
    digest,
    observation_fixture,
    preregistration_fixture,
    shared_outcome_evidence_fixture,
)


_DESIGN_APPROVALS: dict[str, object] = {}
_AUTHORITY_REGISTRIES: dict[str, object] = {}


def seal_preregistration(payload: object) -> dict[str, object]:
    from audience_panel_builder.population.validation.contracts import (
        approve_preregistration_design,
    )
    from conformance.test_tier4_validation_contracts import (
        authority_registry_capability,
    )

    registry = authority_registry_capability(payload)
    approved, capability = approve_preregistration_design(
        payload,
        authority_registry=registry,
        authority_id=str(payload["approval"]["approved_by"]),
    )
    sealed = _seal_preregistration(approved, design_approval=capability)
    _DESIGN_APPROVALS[str(sealed["registration_sha256"])] = capability
    _AUTHORITY_REGISTRIES[str(sealed["registration_id"])] = registry
    return sealed


def build_claim_family(**kwargs: object) -> dict[str, object]:
    return _build_claim_family(
        **kwargs,
        authority_registry=_AUTHORITY_REGISTRIES,
    )


def evaluate_held_out_ordering(**kwargs: object) -> dict[str, object]:
    registration = kwargs["registration"]
    assert isinstance(registration, dict)
    return _evaluate_held_out_ordering(
        **kwargs,
        design_approval=_DESIGN_APPROVALS[
            str(registration["registration_sha256"])
        ],
        authority_registry=_AUTHORITY_REGISTRIES,
    )


def issue_tier4_claim(**kwargs: object) -> dict[str, object]:
    evaluation = kwargs["evaluation"]
    assert isinstance(evaluation, dict)
    preregistration = evaluation["preregistration"]
    assert isinstance(preregistration, dict)
    return _issue_tier4_claim(
        **kwargs,
        design_approval=_DESIGN_APPROVALS[
            str(preregistration["registration_sha256"])
        ],
        authority_registry=_AUTHORITY_REGISTRIES,
    )


def sealed_registration(
    blocks: int = 12, *, registration_id: str = "validation-q3",
    member_ids: list[str] | None = None,
    documented_power: float = 0.80,
    minimum_blocks: int | None = None,
    additional_segments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    registration = preregistration_fixture()
    registration["registration_id"] = registration_id
    registration["multiplicity_rules"]["member_registration_ids"] = member_ids or [registration_id]
    registration["synthetic_surface"]["eligible_creatives"].append({
        "creative_id": "creative-c", "creative_sha256": digest("b"),
    })
    registration["validation_blocks"] = [{
        "block_id": f"block-{index:02d}",
        "study_id": f"batch-{index % 3}",
        "planned_arm_ids": [f"arm-{index:02d}-a", f"arm-{index:02d}-b", f"arm-{index:02d}-c"],
        "planned_effective_sample": 300.0,
        "planned_segment_membership": [],
    } for index in range(blocks)]
    registration["holdout_partition"] = {
        "partition_unit": "block",
        "held_out_ids": [item["block_id"] for item in registration["validation_blocks"]],
    }
    registration["analysis_rules"]["bootstrap_resamples"] = 20_000
    if minimum_blocks is not None:
        registration["eligibility_thresholds"]["minimum_blocks"] = (
            minimum_blocks
        )
    registration["study_design_power"]["documented_power"] = documented_power
    registration["segment_inventory"][0]["planned_block_ids"] = [
        item["block_id"] for item in registration["validation_blocks"]
    ]
    if additional_segments:
        for segment in additional_segments:
            registration["segment_inventory"].append({
                "planned_block_ids": [
                    item["block_id"]
                    for item in registration["validation_blocks"]
                ],
                "evidence_sha256": digest("f"),
                "approval_sha256": digest("6"),
                **segment,
            })
        registration["segment_inventory"].sort(
            key=lambda item: item["segment_id"],
        )
    for block in registration["validation_blocks"]:
        planned_segment_ids = [
            item["segment_id"] for item in registration["segment_inventory"]
            if block["block_id"] in item["planned_block_ids"]
        ]
        block["planned_segment_membership"] = [{
            "arm_id": arm_id,
            "segment_ids": planned_segment_ids,
        } for arm_id in sorted(block["planned_arm_ids"])]
    registration["registration_sha256"] = None
    return seal_preregistration(registration)


def comparison(
    registration: dict[str, object], index: int, *, reverse: bool = False,
    middle_swap: bool = False,
) -> dict[str, object]:
    block = registration["validation_blocks"][index]
    observed = (
        [["creative-c"], ["creative-b"], ["creative-a"]]
        if reverse
        else [["creative-a"], ["creative-c"], ["creative-b"]]
        if middle_swap
        else [["creative-a"], ["creative-b"], ["creative-c"]]
    )
    observations = []
    for suffix, creative_id, creative_digest in (
        ("a", "creative-a", "9"),
        ("b", "creative-b", "a"),
        ("c", "creative-c", "b"),
    ):
        shared = shared_outcome_evidence_fixture()
        shared["shared_evidence_id"] = f"{block['block_id']}-arm-{suffix}"
        shared["study_id"] = block["study_id"]
        shared["block_id"] = block["block_id"]
        shared["arm_id"] = f"arm-{index:02d}-{suffix}"
        shared["creative_binding"] = {
            "creative_id": creative_id,
            "creative_sha256": digest(creative_digest),
        }
        shared["segment_ids"] = next(
            item["segment_ids"]
            for item in block["planned_segment_membership"]
            if item["arm_id"] == shared["arm_id"]
        )
        shared["shared_evidence_sha256"] = sha256_json({
            **shared, "shared_evidence_sha256": None,
        })
        observation = observation_fixture(registration)
        observation["observation_id"] = (
            f"observation-{index:02d}-{suffix}"
        )
        observation["panel_binding"] = deepcopy(
            registration["panel_binding"],
        )
        observation["synthetic_binding"] = deepcopy(
            registration["claim_scope"]["synthetic_binding"],
        )
        observation["claim_scope"] = deepcopy(registration["claim_scope"])
        observation["shared_outcome_evidence_binding"] = {
            "shared_evidence_id": shared["shared_evidence_id"],
            "study_id": shared["study_id"],
            "shared_evidence_sha256": shared["shared_evidence_sha256"],
        }
        for field in (
            "block_id", "arm_id", "creative_binding", "outcome_scope",
            "metric", "metric_family", "units", "assignment", "windows",
            "aggregate", "precision", "sample", "missingness", "segment_ids",
            "exclusions", "source", "outcome_accessed_at", "limitations",
        ):
            observation[field] = deepcopy(shared[field])
        observation["holdout_status"] = (
            "eligible_held_out"
            if block["block_id"]
            in registration["holdout_partition"]["held_out_ids"]
            else "in_sample"
        )
        observation["observation_sha256"] = sha256_json({
            **observation, "observation_sha256": None,
        })
        observations.append(observation)
    observation_by_arm = {
        item["arm_id"]: item for item in observations
    }
    arm_mappings = [
        {
            "arm_id": f"arm-{index:02d}-{suffix}",
            "creative_binding": {
                "creative_id": creative_id,
                "creative_sha256": digest(creative_digest),
            },
            "observation_sha256": observation_by_arm[
                f"arm-{index:02d}-{suffix}"
            ]["observation_sha256"],
        }
        for suffix, creative_id, creative_digest in (
            ("a", "creative-a", "9"),
            ("b", "creative-b", "a"),
            ("c", "creative-c", "b"),
        )
    ]
    document: dict[str, object] = {
        "schema_version": "panel-synthetic-outcome-comparison-v1",
        "comparison_id": f"comparison-{index:02d}",
        "registration_binding": {"registration_id": registration["registration_id"], "registration_sha256": registration["registration_sha256"]},
        "panel_binding": registration["panel_binding"],
        "synthetic_result_binding": registration["claim_scope"]["synthetic_binding"],
        "block_binding": {"block_id": block["block_id"], "study_id": block["study_id"]},
        "metric_binding": registration["primary_metric"],
        "observations": observations,
        "arm_mappings": arm_mappings,
        "mapping_coverage": {"expected_arms": 3, "mapped_arms": 3},
        "observed_ordering": observed,
        "synthetic_ordering": [["creative-a"], ["creative-b"], ["creative-c"]],
        "pairwise_comparisons": [
            {"creative_a": "creative-a", "creative_b": "creative-b", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse else "observed_a_above_b"},
            {"creative_a": "creative-a", "creative_b": "creative-c", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse else "observed_a_above_b"},
            {"creative_a": "creative-b", "creative_b": "creative-c", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse or middle_swap else "observed_a_above_b"},
        ],
        "block_evidence": {
            "observation_sha256": [
                observation_by_arm[arm_id]["observation_sha256"]
                for arm_id in sorted(observation_by_arm)
            ],
            "eligible_exposure_count": 300,
            "missing_outcome_count": 0,
            "planned_effective_sample": 300.0,
            "achieved_effective_sample": 300.0,
        },
        "segment_evidence": [{
            "segment_id": segment["segment_id"],
            "observation_sha256": [
                observation_by_arm[arm_id]["observation_sha256"]
                for arm_id in sorted(observation_by_arm)
            ],
            "arm_ids": sorted(observation_by_arm),
            "observed_ordering": observed,
            "synthetic_ordering": [
                ["creative-a"], ["creative-b"], ["creative-c"],
            ],
            "pairwise_comparisons": [
                {"creative_a": "creative-a", "creative_b": "creative-b", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse else "observed_a_above_b"},
                {"creative_a": "creative-a", "creative_b": "creative-c", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse else "observed_a_above_b"},
                {"creative_a": "creative-b", "creative_b": "creative-c", "synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a" if reverse or middle_swap else "observed_a_above_b"},
            ],
        } for segment in registration["segment_inventory"]
        if segment["segment_id"] in observations[0]["segment_ids"]],
        "comparison_sha256": None,
    }
    document["comparison_sha256"] = sha256_json(document)
    return document


def reseal_comparison_evidence(document: dict[str, object]) -> dict[str, object]:
    """Recompute every observation-derived comparison binding after a test edit."""
    for observation in document["observations"]:
        projected = project_shared_outcome_evidence(observation)
        observation["shared_outcome_evidence_binding"]["shared_evidence_sha256"] = (
            projected["shared_evidence_sha256"]
        )
        observation["observation_sha256"] = sha256_json({
            **observation, "observation_sha256": None,
        })
    observations_by_arm = {
        item["arm_id"]: item for item in document["observations"]
    }
    for mapping in document["arm_mappings"]:
        mapping["observation_sha256"] = observations_by_arm[
            mapping["arm_id"]
        ]["observation_sha256"]
    ordered_arms = sorted(observations_by_arm)
    block = document["block_evidence"]
    block["observation_sha256"] = [
        observations_by_arm[arm_id]["observation_sha256"]
        for arm_id in ordered_arms
    ]
    block["eligible_exposure_count"] = sum(
        item["missingness"]["eligible_exposure_count"]
        for item in document["observations"]
    )
    block["missing_outcome_count"] = sum(
        item["missingness"]["missing_outcome_count"]
        for item in document["observations"]
    )
    block["achieved_effective_sample"] = sum(
        item["sample"]["effective_sample_size"]
        for item in document["observations"]
    )

    def filtered(groups: list[list[str]], selected: set[str]) -> list[list[str]]:
        return [
            [creative_id for creative_id in group if creative_id in selected]
            for group in groups
            if any(creative_id in selected for creative_id in group)
        ]

    segment_ids = sorted({
        segment_id
        for observation in document["observations"]
        for segment_id in observation["segment_ids"]
    })
    rows = []
    for segment_id in segment_ids:
        segment_observations = [
            observations_by_arm[arm_id] for arm_id in ordered_arms
            if segment_id in observations_by_arm[arm_id]["segment_ids"]
        ]
        selected = {
            item["creative_binding"]["creative_id"]
            for item in segment_observations
        }
        rows.append({
            "segment_id": segment_id,
            "observation_sha256": [
                item["observation_sha256"] for item in segment_observations
            ],
            "arm_ids": [item["arm_id"] for item in segment_observations],
            "observed_ordering": filtered(
                document["observed_ordering"], selected,
            ),
            "synthetic_ordering": filtered(
                document["synthetic_ordering"], selected,
            ),
            "pairwise_comparisons": [
                pair for pair in document["pairwise_comparisons"]
                if pair["creative_a"] in selected
                and pair["creative_b"] in selected
            ],
        })
    document["segment_evidence"] = rows
    document["comparison_sha256"] = sha256_json({
        **document, "comparison_sha256": None,
    })
    return document


class Tier4HeldOutEvaluationTests(unittest.TestCase):
    def test_claim_family_rejects_leaked_and_in_sample_comparisons(self) -> None:
        registration = sealed_registration()
        leaked = [comparison(registration, index) for index in range(12)]
        for document in leaked:
            for observation in document["observations"]:
                observation["assignment"]["leakage_detected"] = True
                observation["holdout_status"] = "leaked"
            reseal_comparison_evidence(document)
        with self.assertRaisesRegex(
            ContractError, "valid bound comparisons",
        ):
            build_claim_family(
                registrations=[registration],
                comparisons_by_registration={
                    registration["registration_id"]: leaked,
                },
                built_at="2026-09-01T00:00:00Z",
            )

        draft = preregistration_fixture()
        draft["synthetic_surface"]["eligible_creatives"].append({
            "creative_id": "creative-c", "creative_sha256": digest("b"),
        })
        complete_blocks = sealed_registration()["validation_blocks"]
        draft["validation_blocks"] = deepcopy(complete_blocks)
        for index, block in enumerate(draft["validation_blocks"]):
            block["study_id"] = f"mixed-batch-{index:02d}"
        draft["holdout_partition"] = {
            "partition_unit": "block",
            "held_out_ids": [draft["validation_blocks"][0]["block_id"]],
        }
        draft["analysis_rules"]["bootstrap_resamples"] = 20_000
        draft["segment_inventory"][0]["planned_block_ids"] = [
            block["block_id"] for block in draft["validation_blocks"]
        ]
        draft["registration_sha256"] = None
        mixed_registration = seal_preregistration(draft)
        mixed = [
            comparison(mixed_registration, index) for index in range(12)
        ]
        self.assertEqual(
            11,
            sum(
                document["observations"][0]["holdout_status"] == "in_sample"
                for document in mixed
            ),
        )
        with self.assertRaisesRegex(
            ContractError, "valid bound comparisons",
        ):
            build_claim_family(
                registrations=[mixed_registration],
                comparisons_by_registration={
                    mixed_registration["registration_id"]: mixed,
                },
                built_at="2026-09-01T00:00:00Z",
            )

    def _evaluate(
        self,
        registration: dict[str, object],
        comparisons: list[dict[str, object]],
    ) -> dict[str, object]:
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        return evaluate_held_out_ordering(
            registration=registration,
            comparisons=comparisons,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )

    def test_positive_twelve_block_case_issues_narrow_claim(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={registration["registration_id"]: comparisons},
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration, comparisons=comparisons, claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual("tier4_supported", evaluation["decision"]["status"])
        claim = issue_tier4_claim(
            evaluation=evaluation, issued_at="2026-09-01T01:00:00Z",
            expires_at="2027-03-01T00:00:00Z",
        )
        self.assertEqual("active", claim["status"])
        self.assertIn("supports using the panel to prioritize", claim["claim_text"])

    def test_one_campaign_with_many_pairs_cannot_pass(self):
        registration = sealed_registration(blocks=1)
        comparisons = [comparison(registration, 0)]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={registration["registration_id"]: comparisons},
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration, comparisons=comparisons, claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual("evaluated_with_limitations", evaluation["decision"]["status"])
        self.assertIn("reason-code:minimum-independent-blocks", evaluation["limitations"])

    def test_material_segment_reversal_blocks_broad_claim(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index, reverse=True) for index in range(12)]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={registration["registration_id"]: comparisons},
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration, comparisons=comparisons, claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual("tier4_not_supported", evaluation["decision"]["status"])
        self.assertIn("reason-code:material-segment-reversal", evaluation["limitations"])

    def test_family_recomputes_and_rejects_wrong_member_hash(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(
            registrations=[registration], comparisons_by_registration={registration["registration_id"]: comparisons}, built_at="2026-09-01T00:00:00Z",
        )
        tampered = deepcopy(family)
        tampered["member_comparison_sha256"] = [digest("f")]
        tampered["family_sha256"] = sha256_json({**tampered, "family_sha256": None})
        evaluation = evaluate_held_out_ordering(registration=registration, comparisons=comparisons, claim_family=tampered, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("invalid", evaluation["decision"]["status"])
        self.assertIn("reason-code:untrusted-or-invalid-claim-family", evaluation["limitations"])
        self.assertIn("reason-code:invalid-claim-family", evaluation["limitations"])

    def test_positive_fixture_meets_representable_exact_boundaries(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        self.assertEqual(12, len(comparisons))
        self.assertEqual(36, len({(item["block_binding"]["block_id"], row["arm_id"]) for item in comparisons for row in item["arm_mappings"]}))
        self.assertEqual(3, len({item["block_binding"]["study_id"] for item in comparisons}))
        self.assertEqual(20_000, registration["analysis_rules"]["bootstrap_resamples"])
        self.assertEqual("equal", registration["analysis_rules"]["block_weighting"])
        self.assertEqual(1 / 12, max(1 / len(comparisons), 0))
        self.assertEqual(1.0, len(comparisons) / len(registration["validation_blocks"]))
        self.assertEqual(1.0, 36 / 36)

    def test_missingness_exact_boundary_passes_and_next_count_fails(self):
        registration = sealed_registration()
        exact = [comparison(registration, index) for index in range(12)]
        for item in exact:
            for observation in item["observations"]:
                observation["missingness"]["missing_outcome_count"] = 10
                observation["missingness"]["rate"] = 0.10
                observation["missingness"]["status"] = "present"
                observation["sample"]["effective_sample_size"] = 90.0
                observation["aggregate"]["eligible_exposure_count"] = 90
            reseal_comparison_evidence(item)
        evaluated = self._evaluate(registration, exact)
        self.assertEqual("tier4_supported", evaluated["decision"]["status"])
        self.assertEqual(0.10, evaluated["missingness"]["rate"])

        above = deepcopy(exact)
        observation = above[0]["observations"][0]
        observation["missingness"]["missing_outcome_count"] = 11
        observation["missingness"]["rate"] = 0.11
        observation["sample"]["effective_sample_size"] = 89.0
        observation["aggregate"]["eligible_exposure_count"] = 89
        reseal_comparison_evidence(above[0])
        evaluated = self._evaluate(registration, above)
        self.assertEqual(
            "evaluated_with_limitations", evaluated["decision"]["status"],
        )
        self.assertGreater(evaluated["missingness"]["rate"], 0.10)
        self.assertIn(
            "reason-code:missingness-threshold", evaluated["limitations"],
        )

    def test_each_block_sample_exact_boundary_passes_and_adjacent_below_fails(self):
        registration = sealed_registration()
        exact = [comparison(registration, index) for index in range(12)]
        for item in exact:
            for observation in item["observations"]:
                observation["sample"]["effective_sample_size"] = 90.0
            reseal_comparison_evidence(item)
        evaluated = self._evaluate(registration, exact)
        self.assertEqual("tier4_supported", evaluated["decision"]["status"])
        self.assertEqual(
            0.90, evaluated["sample_sufficiency"]["minimum_achieved_ratio"],
        )

        below = deepcopy(exact)
        below[0]["observations"][0]["sample"]["effective_sample_size"] = (
            89.999999999
        )
        reseal_comparison_evidence(below[0])
        evaluated = self._evaluate(registration, below)
        self.assertEqual(
            "evaluated_with_limitations", evaluated["decision"]["status"],
        )
        self.assertLess(
            evaluated["sample_sufficiency"]["minimum_achieved_ratio"], 0.90,
        )

    def test_power_exact_boundary_passes_and_adjacent_below_fails(self):
        exact_registration = sealed_registration(documented_power=0.80)
        exact = [
            comparison(exact_registration, index) for index in range(12)
        ]
        evaluated = self._evaluate(exact_registration, exact)
        self.assertEqual("tier4_supported", evaluated["decision"]["status"])
        self.assertEqual(0.80, evaluated["power"]["documented_power"])

        below_registration = sealed_registration(
            documented_power=math.nextafter(0.80, 0.0),
        )
        below = [
            comparison(below_registration, index) for index in range(12)
        ]
        evaluated = self._evaluate(below_registration, below)
        self.assertEqual(
            "evaluated_with_limitations", evaluated["decision"]["status"],
        )
        self.assertIn("reason-code:power-threshold", evaluated["limitations"])

    def test_materiality_agreement_and_clear_reversal_exact_boundaries(self):
        self.assertTrue(_is_material_segment({
            "must_cover": False, "effective_panel_weight": 0.10,
        }))
        self.assertFalse(_is_material_segment({
            "must_cover": False,
            "effective_panel_weight": math.nextafter(0.10, 0.0),
        }))
        self.assertTrue(_is_material_segment({
            "must_cover": True, "effective_panel_weight": 0.0,
        }))
        tau = SimpleNamespace(point=0.01, two_sided_upper=0.1)
        agreement = SimpleNamespace(point=0.55, two_sided_upper=0.7)
        sparse, reversal, passes = _material_segment_gate(
            eligible_blocks=6,
            creative_arms=18,
            block_coverage=0.80,
            tau_interval=tau,
            agreement_interval=agreement,
        )
        self.assertEqual((False, False, True), (sparse, reversal, passes))
        agreement.point = math.nextafter(0.55, 0.0)
        self.assertEqual(
            (False, False, False),
            _material_segment_gate(
                eligible_blocks=6,
                creative_arms=18,
                block_coverage=0.80,
                tau_interval=tau,
                agreement_interval=agreement,
            ),
        )
        negative_crossing = SimpleNamespace(
            point=-0.01, two_sided_upper=0.01,
        )
        self.assertEqual(
            (False, False, False),
            _material_segment_gate(
                eligible_blocks=6,
                creative_arms=18,
                block_coverage=0.80,
                tau_interval=negative_crossing,
                agreement_interval=SimpleNamespace(
                    point=0.60, two_sided_upper=0.70,
                ),
            ),
        )
        clear_negative = SimpleNamespace(
            point=-0.01,
            two_sided_upper=math.nextafter(0.0, -1.0),
        )
        self.assertEqual(
            (False, True, False),
            _material_segment_gate(
                eligible_blocks=6,
                creative_arms=18,
                block_coverage=0.80,
                tau_interval=clear_negative,
                agreement_interval=SimpleNamespace(
                    point=0.60, two_sided_upper=0.70,
                ),
            ),
        )
        self.assertEqual(
            (True, True, False),
            _material_segment_gate(
                eligible_blocks=5,
                creative_arms=15,
                block_coverage=0.79,
                tau_interval=clear_negative,
                agreement_interval=None,
            ),
        )

    def test_all_zero_agreement_only_interval_is_available_reversal(self):
        registration = sealed_registration()
        comparisons = [
            comparison(registration, index) for index in range(12)
        ]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        positive = Interval(
            1.0, 1.0, 1.0, 1.0, "test", 20_000, 17,
        )
        zero = Interval(
            0.0, 0.0, 0.0, 0.0, "test", 20_000, 17,
        )
        with patch(
            "audience_panel_builder.population.validation.evaluation."
            "bca_block_interval",
            side_effect=[
                positive,
                positive,
                InsufficientUncertaintyError("tau unavailable"),
                zero,
            ],
        ):
            evaluated = evaluate_held_out_ordering(
                registration=registration,
                comparisons=comparisons,
                claim_family=family,
                evaluated_at="2026-09-01T00:00:00Z",
            )
        segment = evaluated["segment_diagnostics"][0]
        self.assertFalse(segment["tau"]["available"])
        self.assertTrue(segment["agreement"]["available"])
        self.assertEqual(0.0, segment["agreement"]["point"])
        self.assertTrue(segment["clear_reversal"])
        self.assertEqual("tier4_not_supported", evaluated["decision"]["status"])
        agreement_reversal = SimpleNamespace(
            point=0.49, two_sided_upper=0.49,
        )
        self.assertEqual(
            (True, True, False),
            _material_segment_gate(
                eligible_blocks=5,
                creative_arms=15,
                block_coverage=0.79,
                tau_interval=None,
                agreement_interval=agreement_reversal,
            ),
        )

    def test_post_outcome_segment_removal_is_invalid(self):
        registration = sealed_registration()
        comparisons = [
            comparison(registration, index) for index in range(12)
        ]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        changed = deepcopy(comparisons)
        changed[0]["segment_evidence"] = []
        changed[0]["comparison_sha256"] = sha256_json({
            **changed[0], "comparison_sha256": None,
        })
        evaluated = evaluate_held_out_ordering(
            registration=registration,
            comparisons=changed,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual("invalid", evaluated["decision"]["status"])
        self.assertIn("reason-code:invalid-comparison", evaluated["limitations"])

    def test_rehashed_adverse_arm_segment_membership_removal_is_invalid(self):
        registration = sealed_registration()
        comparisons = [
            comparison(registration, index) for index in range(12)
        ]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        changed = deepcopy(comparisons)
        changed[0]["observations"][0]["segment_ids"].remove("enterprise")
        reseal_comparison_evidence(changed[0])
        evaluated = evaluate_held_out_ordering(
            registration=registration,
            comparisons=changed,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(12, len(changed))
        self.assertEqual(36, sum(
            len(item["arm_mappings"]) for item in changed
        ))
        self.assertEqual("invalid", evaluated["decision"]["status"])
        self.assertIn("reason-code:invalid-comparison", evaluated["limitations"])

    def test_rehashed_status_passing_numeric_failure_cannot_issue_claim(self):
        registration = sealed_registration()
        comparisons = [
            comparison(registration, index) for index in range(12)
        ]
        evaluation = self._evaluate(registration, comparisons)
        forged = deepcopy(evaluation)
        forged["missingness"] = {
            "status": "within_threshold",
            "eligible_exposure_count": 100,
            "missing_outcome_count": 50,
            "rate": 0.50,
        }
        forged["sample_sufficiency"]["status"] = "sufficient"
        forged["sample_sufficiency"]["minimum_achieved_ratio"] = 0.0
        forged["sample_sufficiency"]["blocks"][0][
            "achieved_effective_sample"
        ] = 0.0
        forged["sample_sufficiency"]["blocks"][0]["achieved_ratio"] = 0.0
        forged["power"]["status"] = "sufficient"
        forged["power"]["documented_power"] = 0.0
        forged["segment_diagnostics"][0]["status"] = "pass"
        forged["segment_diagnostics"][0]["tau"] = {
            "available": True,
            "point": -1.0, "two_sided_lower": -1.0,
            "two_sided_upper": -0.5, "one_sided_lower": -1.0,
        }
        forged["segment_diagnostics"][0]["clear_reversal"] = False
        forged["gate_results"]["all_required_gates_passed"] = True
        forged["decision"]["status"] = "tier4_supported"
        forged["evaluation_sha256"] = sha256_json({
            **forged, "evaluation_sha256": None,
        })
        with self.assertRaises(ContractError):
            issue_tier4_claim(
                evaluation=forged,
                issued_at="2026-09-01T01:00:00Z",
                expires_at="2027-03-01T00:00:00Z",
            )

    def test_positive_pooled_result_cannot_hide_sparse_material_segment(self):
        registration = sealed_registration(additional_segments=[{
            "segment_id": "sparse-risk",
            "must_cover": True,
            "effective_panel_weight": 0.0,
            "planned_block_ids": [
                f"block-{index:02d}" for index in range(5)
            ],
        }])
        comparisons = [
            comparison(registration, index) for index in range(12)
        ]
        evaluated = self._evaluate(registration, comparisons)
        self.assertEqual(
            "evaluated_with_limitations", evaluated["decision"]["status"],
        )
        self.assertEqual(
            "limitations",
            next(
                row for row in evaluated["segment_diagnostics"]
                if row["segment_id"] == "sparse-risk"
            )["status"],
        )
        self.assertEqual("pass", evaluated["overall_diagnostics"]["status"])

    def test_positive_pooled_result_cannot_hide_material_segment_reversal(self):
        planned_risk_blocks = [f"block-{index:02d}" for index in range(6)]
        registration = sealed_registration(
            blocks=24,
            additional_segments=[{
                "segment_id": "reversal-risk",
                "must_cover": True,
                "effective_panel_weight": 0.0,
                "planned_block_ids": planned_risk_blocks,
            }],
        )
        comparisons = [
            comparison(registration, index, reverse=index < 6)
            for index in range(24)
        ]
        evaluated = self._evaluate(registration, comparisons)
        self.assertEqual(
            "tier4_not_supported", evaluated["decision"]["status"],
        )
        self.assertEqual("pass", evaluated["overall_diagnostics"]["status"])
        reversal = next(
            row for row in evaluated["segment_diagnostics"]
            if row["segment_id"] == "reversal-risk"
        )
        self.assertTrue(reversal["clear_reversal"])
        self.assertEqual("fail", reversal["status"])
        self.assertEqual(1.0, 36 / 36)  # complete comparisons are the closed missingness/sample projection.
        self.assertEqual(1.0, 36 / 36)

    def test_current_member_p_value_chronology_and_binding_tampering_fail_closed(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(registrations=[registration], comparisons_by_registration={registration["registration_id"]: comparisons}, built_at="2026-09-01T00:00:00Z")
        p_tampered = deepcopy(family)
        p_tampered["member_one_sided_p_values"] = [0.0]
        p_tampered["adjusted_p_values"] = [0.0]
        p_tampered["family_sha256"] = sha256_json({**p_tampered, "family_sha256": None})
        limited = evaluate_held_out_ordering(registration=registration, comparisons=comparisons, claim_family=p_tampered, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("evaluated_with_limitations", limited["decision"]["status"])
        self.assertIn("reason-code:family-p-value-mismatch", limited["limitations"])
        early = evaluate_held_out_ordering(registration=registration, comparisons=comparisons, claim_family=family, evaluated_at="2026-07-01T00:00:00Z")
        self.assertEqual("invalid", early["decision"]["status"])
        changed = deepcopy(comparisons)
        changed[0]["metric_binding"] = deepcopy(registration["secondary_metrics"])  # malformed metric binding is a closed-contract failure.
        changed[0]["comparison_sha256"] = sha256_json({**changed[0], "comparison_sha256": None})
        invalid = evaluate_held_out_ordering(registration=registration, comparisons=changed, claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("invalid", invalid["decision"]["status"])

    def test_pair_integrity_resample_policy_and_claim_chronology(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        broken = deepcopy(comparisons)
        broken[0]["pairwise_comparisons"].pop()
        broken[0]["comparison_sha256"] = sha256_json({**broken[0], "comparison_sha256": None})
        family = build_claim_family(registrations=[registration], comparisons_by_registration={registration["registration_id"]: comparisons}, built_at="2026-09-01T00:00:00Z")
        invalid = evaluate_held_out_ordering(registration=registration, comparisons=broken, claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("invalid", invalid["decision"]["status"])
        short = deepcopy(registration)
        short["analysis_rules"]["bootstrap_resamples"] = 1_000
        short["registration_sha256"] = None
        short = seal_preregistration(short)
        short_comparisons = [comparison(short, index) for index in range(12)]
        short_family = build_claim_family(registrations=[short], comparisons_by_registration={short["registration_id"]: short_comparisons}, built_at="2026-09-01T00:00:00Z")
        limited = evaluate_held_out_ordering(registration=short, comparisons=short_comparisons, claim_family=short_family, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("evaluated_with_limitations", limited["decision"]["status"])
        supported = evaluate_held_out_ordering(registration=registration, comparisons=comparisons, claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        with self.assertRaises(ValueError):
            issue_tier4_claim(evaluation=supported, issued_at="2026-08-31T00:00:00Z", expires_at="2027-03-01T00:00:00Z")

    def test_cli_never_clobbers_or_reuses_an_input_path(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family_input = {"registrations": [registration], "comparisons_by_registration": {registration["registration_id"]: comparisons}, "built_at": "2026-09-01T00:00:00Z"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "family-input.json"
            path.write_text(json.dumps(family_input), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(ROOT / "skills" / "audience-panel-builder" / "scripts" / "build-panel-claim-family.py"),
                "--family-input",
                str(path),
                "--output",
                str(path),
                "--authority-registry",
                str(Path(temporary) / "authority.json"),
                "--authority-secret-file",
                str(Path(temporary) / "authority.key"),
            ], capture_output=True, text=True)
            self.assertEqual(3, result.returncode)
            self.assertEqual(json.dumps(family_input), path.read_text(encoding="utf-8"))

    def test_multi_member_family_authenticates_every_sibling(self):
        ids = ["validation-a", "validation-b", "validation-c"]
        registrations = [sealed_registration(registration_id=item, member_ids=ids) for item in ids]
        comparisons_by_registration = {
            registration["registration_id"]: [comparison(registration, index) for index in range(12)]
            for registration in registrations
        }
        family = build_claim_family(registrations=registrations, comparisons_by_registration=comparisons_by_registration, built_at="2026-09-01T00:00:00Z")
        current = registrations[0]
        supported = evaluate_held_out_ordering(registration=current, comparisons=comparisons_by_registration[current["registration_id"]], claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("tier4_supported", supported["decision"]["status"])
        # A sealed but forged sibling p-value is rejected because evaluation
        # recomputes every sibling from embedded authenticated comparisons.
        forged = deepcopy(family)
        forged["member_one_sided_p_values"][1] = 0.0
        from audience_panel_builder.population.validation.statistics import holm_adjust  # noqa: E402
        forged["adjusted_p_values"] = holm_adjust(forged["member_one_sided_p_values"])
        forged["family_sha256"] = sha256_json({**forged, "family_sha256": None})
        rejected = evaluate_held_out_ordering(registration=current, comparisons=comparisons_by_registration[current["registration_id"]], claim_family=forged, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("evaluated_with_limitations", rejected["decision"]["status"])
        self.assertIn("reason-code:family-p-value-mismatch", rejected["limitations"])

    def test_raw_p_pass_but_exact_three_member_holm_adjusted_value_fails(self):
        ids = ["validation-a", "validation-b", "validation-c"]
        registrations = [sealed_registration(registration_id=item, member_ids=ids) for item in ids]
        comparisons_by_registration = {
            registration["registration_id"]: [comparison(registration, index) for index in range(12)]
            for registration in registrations
        }
        family = build_claim_family(registrations=registrations, comparisons_by_registration=comparisons_by_registration, built_at="2026-09-01T00:00:00Z")
        family["member_one_sided_p_values"] = [0.02, 0.02, 0.02]
        family["adjusted_p_values"] = [0.06, 0.06, 0.06]
        family["family_sha256"] = sha256_json({**family, "family_sha256": None})
        current = registrations[0]
        with patch("audience_panel_builder.population.validation.evaluation.complete_block_sign_permutation_p", return_value=0.02):
            evaluation = evaluate_held_out_ordering(registration=current, comparisons=comparisons_by_registration[current["registration_id"]], claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        self.assertEqual("tier4_not_supported", evaluation["decision"]["status"])
        self.assertIn("reason-code:holm-adjusted-failure", evaluation["limitations"])

    def test_evaluation_cli_rejects_input_alias_and_duplicate_invalid_exit(self):
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(registrations=[registration], comparisons_by_registration={registration["registration_id"]: comparisons}, built_at="2026-09-01T00:00:00Z")
        script = ROOT / "skills" / "audience-panel-builder" / "scripts" / "evaluate-panel-outcomes.py"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            registration_path, family_path, comparison_path = base / "registration.json", base / "family.json", base / "comparison.json"
            registration_path.write_text(json.dumps(registration), encoding="utf-8")
            family_path.write_text(json.dumps(family), encoding="utf-8")
            comparison_path.write_text(json.dumps(comparisons[0]), encoding="utf-8")
            authority_root = base / "authority-root.json"
            authority_index = base / "authority-index.json"
            authority_root.write_text("{}", encoding="utf-8")
            authority_index.write_text("{}", encoding="utf-8")
            authority_registry = base / "authority-registry.json"
            authority_secret = base / "authority-secret.key"
            authority_registry.write_text("{}", encoding="utf-8")
            authority_secret.write_bytes(
                b"fictional-tier4-test-authority-secret",
            )
            authority_args = [
                "--authority-root", str(authority_root),
                "--authority-index", str(authority_index),
                "--authority-registry", str(authority_registry),
                "--authority-secret-file", str(authority_secret),
            ]
            alias = subprocess.run([sys.executable, str(script), "--registration", str(registration_path), "--comparison", str(comparison_path), "--claim-family", str(family_path), "--evaluated-at", "2026-09-01T00:00:00Z", "--evaluation-output", str(registration_path), *authority_args], capture_output=True, text=True)
            self.assertEqual(3, alias.returncode)
            duplicate = subprocess.run([sys.executable, str(script), "--registration", str(registration_path), "--comparison", str(comparison_path), "--comparison", str(comparison_path), "--claim-family", str(family_path), "--evaluated-at", "2026-09-01T00:00:00Z", "--evaluation-output", str(base / "invalid.json"), *authority_args], capture_output=True, text=True)
            self.assertEqual(7, duplicate.returncode)


if __name__ == "__main__":
    unittest.main()
