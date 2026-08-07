from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population import validation as validation_api  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    project_shared_outcome_evidence,
)
from audience_panel_builder.population.validation.metrics import (  # noqa: E402
    _student_t_quantile,
    _welch_components,
    DifferenceInterval,
    NormalizedArm,
    classify_observed_pair,
    garwood_interval,
    normalize_observation,
    wilson_score_interval,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    approved_seal as seal_preregistration,
    metric,
    observation_fixture,
)


def _reseal_observation(document: dict[str, object]) -> dict[str, object]:
    shared = project_shared_outcome_evidence(document)
    binding = document["shared_outcome_evidence_binding"]
    assert isinstance(binding, dict)
    binding["shared_evidence_sha256"] = shared["shared_evidence_sha256"]
    document["observation_sha256"] = sha256_json(
        {**document, "observation_sha256": None}
    )
    return document


def observation_fixture_for(
    *,
    metric_family: str = "binary_proportion",
    aggregate: dict[str, object] | None = None,
    direction: str = "higher_is_better",
    arm_id: str = "arm-a",
    block_id: str = "campaign-q3",
    study_id: str = "campaign-study",
) -> dict[str, object]:
    document = observation_fixture()
    outcome_metric = metric()
    outcome_metric["direction"] = direction
    if metric_family == "binary_proportion":
        outcome_metric.update({
            "exposure_unit": "eligible-exposure",
            "outcome_unit": "qualified-response",
        })
        aggregate = aggregate or {"success_count": 20, "eligible_exposure_count": 100}
    elif metric_family == "continuous_mean":
        outcome_metric.update({"exposure_unit": "observation", "outcome_unit": "score"})
        aggregate = aggregate or {"sample_count": 10, "mean": 4.0, "standard_deviation": 2.0}
    elif metric_family == "event_rate":
        outcome_metric.update({"exposure_unit": "day", "outcome_unit": "event"})
        aggregate = aggregate or {"event_count": 2, "exposure_time": 100.0}
    else:
        aggregate = aggregate or {}
    document["metric"] = outcome_metric
    document["metric_family"] = metric_family
    document["units"] = {
        "exposure": outcome_metric["exposure_unit"],
        "outcome": outcome_metric["outcome_unit"],
    }
    document["windows"] = {
        "measurement": outcome_metric["measurement_window"],
        "attribution": outcome_metric["attribution_window"],
    }
    document["aggregate"] = aggregate
    if metric_family == "binary_proportion":
        evidence_count = int(aggregate["eligible_exposure_count"])
    elif metric_family == "continuous_mean":
        evidence_count = int(aggregate["sample_count"])
    elif metric_family == "event_rate":
        evidence_count = max(0, int(float(aggregate["exposure_time"])))
    else:
        evidence_count = 100
    document["sample"] = {
        "eligible_exposure_count": evidence_count,
        "effective_sample_size": float(evidence_count),
    }
    document["missingness"] = {
        "status": "none",
        "eligible_exposure_count": evidence_count,
        "missing_outcome_count": 0,
        "rate": 0.0,
    }
    document["observation_id"] = f"observation-{block_id}-{arm_id}"
    document["block_id"] = block_id
    document["arm_id"] = arm_id
    document["shared_outcome_evidence_binding"]["study_id"] = study_id  # type: ignore[index]
    registration = document["registration_binding"]
    assert isinstance(registration, dict)
    preregistration = registration["preregistration"]
    assert isinstance(preregistration, dict)
    preregistration["primary_metric"] = deepcopy(outcome_metric)
    preregistration["validation_blocks"] = [{
        "block_id": block_id,
        "study_id": study_id,
        "planned_arm_ids": [arm_id],
        "planned_effective_sample": float(max(evidence_count, 1)),
        "planned_segment_membership": [{
            "arm_id": arm_id, "segment_ids": ["enterprise"],
        }],
    }]
    preregistration["segment_inventory"][0]["planned_block_ids"] = [block_id]
    preregistration["holdout_partition"] = {
        "partition_unit": "block", "held_out_ids": [block_id],
    }
    preregistration["registration_sha256"] = None
    sealed = seal_preregistration(preregistration)
    registration.update({
        "registration_id": sealed["registration_id"],
        "registration_sha256": sealed["registration_sha256"],
        "registered_at": sealed["registered_at"],
        "status": sealed["status"],
        "holdout_partition": sealed["holdout_partition"],
        "claim_scope": sealed["claim_scope"],
        "multiplicity_rules": sealed["multiplicity_rules"],
        "preregistration": sealed,
    })
    return _reseal_observation(document)


def binary_fixture(*, successes: int, exposures: int) -> dict[str, object]:
    return observation_fixture_for(
        aggregate={"success_count": successes, "eligible_exposure_count": exposures}
    )


def continuous_fixture(*, count: int, sd: float) -> dict[str, object]:
    return observation_fixture_for(
        metric_family="continuous_mean",
        aggregate={"sample_count": count, "mean": 4.0, "standard_deviation": sd},
    )


def _bind_pair_registration(*documents: dict[str, object]) -> tuple[dict[str, object], ...]:
    registration = documents[0]["registration_binding"]
    assert isinstance(registration, dict)
    preregistration = deepcopy(registration["preregistration"])
    assert isinstance(preregistration, dict)
    preregistration["validation_blocks"][0]["planned_arm_ids"] = [  # type: ignore[index]
        document["arm_id"] for document in documents
    ]
    preregistration["validation_blocks"][0]["planned_segment_membership"] = [  # type: ignore[index]
        {"arm_id": document["arm_id"], "segment_ids": ["enterprise"]}
        for document in sorted(documents, key=lambda item: str(item["arm_id"]))
    ]
    preregistration["registration_sha256"] = None
    sealed = seal_preregistration(preregistration)
    for document in documents:
        binding = document["registration_binding"]
        assert isinstance(binding, dict)
        binding.update({
            "registration_id": sealed["registration_id"],
            "registration_sha256": sealed["registration_sha256"],
            "registered_at": sealed["registered_at"],
            "status": sealed["status"],
            "holdout_partition": sealed["holdout_partition"],
            "claim_scope": sealed["claim_scope"],
            "multiplicity_rules": sealed["multiplicity_rules"],
            "preregistration": sealed,
        })
        _reseal_observation(document)
    return documents


def pair_fixtures(
    *,
    metric_family: str = "binary_proportion",
    left_aggregate: dict[str, object],
    right_aggregate: dict[str, object],
    direction: str = "higher_is_better",
) -> tuple[dict[str, object], dict[str, object]]:
    left = observation_fixture_for(
        metric_family=metric_family, aggregate=left_aggregate,
        direction=direction, arm_id="arm-a",
    )
    right = observation_fixture_for(
        metric_family=metric_family, aggregate=right_aggregate,
        direction=direction, arm_id="arm-b",
    )
    return _bind_pair_registration(left, right)  # type: ignore[return-value]


def rate_fixture(*, events: int, exposure_time: float) -> dict[str, object]:
    return observation_fixture_for(
        metric_family="event_rate",
        aggregate={"event_count": events, "exposure_time": exposure_time},
    )


class Tier4MetricAdapterTests(unittest.TestCase):
    def test_binary_proportion_uses_eligible_exposures(self) -> None:
        arm = normalize_observation(observation_fixture_for(
            aggregate={"success_count": 40, "eligible_exposure_count": 200}
        ))
        self.assertAlmostEqual(0.20, arm.point)
        self.assertEqual("wilson-score", arm.interval_method)
        self.assertLess(arm.lower, arm.point)
        self.assertGreater(arm.upper, arm.point)

    def test_lower_is_better_reverses_only_the_comparison_direction(self) -> None:
        arm = normalize_observation(observation_fixture_for(
            metric_family="continuous_mean",
            aggregate={"sample_count": 100, "mean": 4.0, "standard_deviation": 2.0},
            direction="lower_is_better",
        ))
        self.assertEqual(4.0, arm.point)
        self.assertEqual(-4.0, arm.direction_normalized_point)

    def test_total_revenue_without_denominator_is_rejected(self) -> None:
        document = observation_fixture_for()
        document["metric_family"] = "continuous_mean"
        document["aggregate"] = {"total": 10000}
        document = _reseal_observation(document)
        with self.assertRaisesRegex(ContractError, "sample_count.*mean.*standard_deviation"):
            normalize_observation(document)

    def test_zero_and_all_successes_keep_nonzero_uncertainty(self) -> None:
        zero = normalize_observation(binary_fixture(successes=0, exposures=30))
        all_success = normalize_observation(binary_fixture(successes=30, exposures=30))
        self.assertGreater(zero.upper, 0.0)
        self.assertLess(all_success.lower, 1.0)

    def test_one_continuous_observation_is_limited_not_certain(self) -> None:
        arm = normalize_observation(continuous_fixture(count=1, sd=0.0))
        self.assertEqual("limited", arm.support_status)
        self.assertIn("continuous-sample-too-small", arm.limitation_codes)
        self.assertIsNone(arm.lower)
        self.assertIsNone(arm.upper)

    def test_zero_event_rate_keeps_positive_upper_bound(self) -> None:
        arm = normalize_observation(rate_fixture(events=0, exposure_time=100))
        self.assertEqual(0.0, arm.point)
        self.assertGreater(arm.upper, 0.0)

    def test_event_rate_accepts_nonintegral_person_time_denominator(self) -> None:
        document = rate_fixture(events=2, exposure_time=123.5)
        self.assertEqual("day", document["units"]["exposure"])
        arm = normalize_observation(document)
        self.assertAlmostEqual(2 / 123.5, arm.point)
        self.assertEqual(123.5, arm.effective_sample)
        self.assertEqual(
            123, document["sample"]["eligible_exposure_count"],
        )

    def test_rejects_invalid_denominators_counts_and_nonfinite_values(self) -> None:
        cases = [
            binary_fixture(successes=0, exposures=0),
            binary_fixture(successes=-1, exposures=1),
            binary_fixture(successes=2, exposures=1),
            rate_fixture(events=0, exposure_time=0),
        ]
        for document in cases:
            with self.subTest(document=document["aggregate"]), self.assertRaises(ContractError):
                normalize_observation(document)
        document = continuous_fixture(count=2, sd=1.0)
        document["aggregate"]["standard_deviation"] = math.nan  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "finite"):
            normalize_observation(document)

    def test_rejects_missing_statistics_and_unsupported_families(self) -> None:
        document = continuous_fixture(count=2, sd=1.0)
        del document["aggregate"]["standard_deviation"]  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "sample_count.*mean.*standard_deviation"):
            normalize_observation(document)
        document = observation_fixture_for(metric_family="unsupported")
        with self.assertRaisesRegex(ContractError, "metric_family"):
            normalize_observation(document)

    def test_requires_metric_units_windows_and_registered_direction_to_match(self) -> None:
        for path, value, message in [
            ("units", {"exposure": "wrong", "outcome": "qualified-response"}, "exposure unit"),
            ("windows", {"measurement": "2026-q3", "attribution": "wrong"}, "attribution window"),
        ]:
            document = observation_fixture_for()
            document[path] = value
            document = _reseal_observation(document)
            with self.subTest(path=path), self.assertRaisesRegex(ContractError, message):
                normalize_observation(document)
        document = observation_fixture_for()
        document["metric"]["direction"] = "lower_is_better"  # type: ignore[index]
        document = _reseal_observation(document)
        with self.assertRaisesRegex(ContractError, "direction"):
            normalize_observation(document)

    def test_pair_classification_uses_intervals_and_never_decides_limited_arms(self) -> None:
        left_document, right_document = pair_fixtures(
            left_aggregate={"success_count": 90, "eligible_exposure_count": 100},
            right_aggregate={"success_count": 10, "eligible_exposure_count": 100},
        )
        left, right = normalize_observation(left_document), normalize_observation(right_document)
        status, interval = classify_observed_pair(left, right, equivalence_margin=0.02)
        self.assertEqual("observed_a_above_b", status)
        self.assertGreater(interval.lower, 0.02)
        limited_left, limited_right = pair_fixtures(
            metric_family="continuous_mean",
            left_aggregate={"sample_count": 1, "mean": 4.0, "standard_deviation": 0.0},
            right_aggregate={"sample_count": 1, "mean": 4.0, "standard_deviation": 0.0},
        )
        limited, limited_other = normalize_observation(limited_left), normalize_observation(limited_right)
        status, interval = classify_observed_pair(limited, limited_other, equivalence_margin=0.02)
        self.assertEqual("observed_indeterminate", status)
        self.assertIsNone(interval.lower)
        self.assertIsNone(interval.upper)
        self.assertIsNone(interval.confidence_level)
        zero_left, zero_right = pair_fixtures(
            metric_family="continuous_mean",
            left_aggregate={"sample_count": 2, "mean": 4.0, "standard_deviation": 0.0},
            right_aggregate={"sample_count": 2, "mean": 4.0, "standard_deviation": 0.0},
        )
        status, interval = classify_observed_pair(
            normalize_observation(zero_left), normalize_observation(zero_right),
            equivalence_margin=0.02,
        )
        self.assertEqual("observed_indeterminate", status)
        self.assertIsNone(interval.lower)

    def test_pair_statuses_and_direction_normalized_interval_coverage(self) -> None:
        above_left, above_right = pair_fixtures(
            left_aggregate={"success_count": 10, "eligible_exposure_count": 100},
            right_aggregate={"success_count": 90, "eligible_exposure_count": 100},
        )
        status, _ = classify_observed_pair(
            normalize_observation(above_left), normalize_observation(above_right),
            equivalence_margin=0.02,
        )
        self.assertEqual("observed_b_above_a", status)
        equal_left, equal_right = pair_fixtures(
            left_aggregate={"success_count": 5000, "eligible_exposure_count": 10000},
            right_aggregate={"success_count": 5001, "eligible_exposure_count": 10000},
        )
        status, _ = classify_observed_pair(
            normalize_observation(equal_left), normalize_observation(equal_right),
            equivalence_margin=0.03,
        )
        self.assertEqual("observed_equivalent", status)
        lower_left, lower_right = pair_fixtures(
            metric_family="continuous_mean", direction="lower_is_better",
            left_aggregate={"sample_count": 50, "mean": 2.0, "standard_deviation": 1.0},
            right_aggregate={"sample_count": 50, "mean": 4.0, "standard_deviation": 1.0},
        )
        status, interval = classify_observed_pair(
            normalize_observation(lower_left), normalize_observation(lower_right),
            equivalence_margin=0.02,
        )
        self.assertEqual("observed_a_above_b", status)
        self.assertAlmostEqual(2.0, interval.point)
        self.assertGreater(interval.lower, 0.0)
        rate_left, rate_right = pair_fixtures(
            metric_family="event_rate",
            left_aggregate={"event_count": 20, "exposure_time": 100.0},
            right_aggregate={"event_count": 1, "exposure_time": 100.0},
        )
        status, interval = classify_observed_pair(
            normalize_observation(rate_left), normalize_observation(rate_right),
            equivalence_margin=0.02,
        )
        self.assertEqual("observed_a_above_b", status)
        self.assertEqual("bonferroni-garwood", interval.method)

    def test_pair_rejects_self_cross_block_and_cross_study_comparisons(self) -> None:
        left, right = pair_fixtures(
            left_aggregate={"success_count": 50, "eligible_exposure_count": 100},
            right_aggregate={"success_count": 40, "eligible_exposure_count": 100},
        )
        normalized = normalize_observation(left)
        with self.assertRaisesRegex(ContractError, "distinct"):
            classify_observed_pair(normalized, normalized, equivalence_margin=0.02)
        cross_block = observation_fixture_for(
            aggregate={"success_count": 40, "eligible_exposure_count": 100},
            arm_id="arm-b", block_id="campaign-q4",
        )
        with self.assertRaisesRegex(ContractError, "same registered block"):
            classify_observed_pair(normalize_observation(left), normalize_observation(cross_block), equivalence_margin=0.02)
        cross_study = observation_fixture_for(
            aggregate={"success_count": 40, "eligible_exposure_count": 100},
            arm_id="arm-b", study_id="other-study",
        )
        with self.assertRaisesRegex(ContractError, "same registered block"):
            classify_observed_pair(normalize_observation(left), normalize_observation(cross_study), equivalence_margin=0.02)

    def test_public_dataclasses_are_compact_and_forged_arms_are_rejected(self) -> None:
        left, right = pair_fixtures(
            left_aggregate={"success_count": 80, "eligible_exposure_count": 100},
            right_aggregate={"success_count": 20, "eligible_exposure_count": 100},
        )
        normalized_left, normalized_right = normalize_observation(left), normalize_observation(right)
        arm_fields = {
            "arm_id", "block_id", "creative_id", "point",
            "direction_normalized_point", "effective_sample", "lower", "upper",
            "interval_method", "support_status", "limitation_codes",
        }
        difference_fields = {"point", "lower", "upper", "confidence_level", "method"}
        self.assertEqual(arm_fields, set(NormalizedArm.__dataclass_fields__))
        self.assertEqual(arm_fields, set(asdict(normalized_left)))
        self.assertNotIn("MetricCalculationContext", validation_api.__all__)
        first = classify_observed_pair(normalized_left, normalized_right, equivalence_margin=0.02)[1]
        second = classify_observed_pair(normalized_left, normalized_right, equivalence_margin=0.02)[1]
        self.assertEqual(first, second)
        self.assertEqual(difference_fields, set(DifferenceInterval.__dataclass_fields__))
        self.assertEqual(difference_fields, set(asdict(first)))
        forged_left = NormalizedArm(**asdict(normalized_left))
        forged_right = NormalizedArm(**asdict(normalized_right))
        with self.assertRaisesRegex(ContractError, "normalize_observation"):
            classify_observed_pair(forged_left, forged_right, equivalence_margin=0.02)

    def test_sparse_event_rate_uses_exact_garwood_bounds(self) -> None:
        arm = normalize_observation(rate_fixture(events=1, exposure_time=100))
        self.assertEqual("garwood-poisson", arm.interval_method)
        self.assertGreater(arm.lower, 0.0)
        self.assertGreater(arm.upper, arm.point)

    def test_offline_scipy_oracle_rows(self) -> None:
        """Reference values were generated offline with scipy.stats (not runtime)."""
        # Wilson: scipy.stats.norm.ppf with the score-interval formula.
        for successes, exposures, expected in [
            (0, 30, (0.0, 0.1434417117633984)),
            (30, 30, (0.8565582882366016, 1.0)),
            (40, 200, (0.14430622925150888, 0.2703961147945647)),
        ]:
            with self.subTest(kind="wilson", successes=successes):
                self.assertAlmostEqual(expected[0], wilson_score_interval(successes, exposures, confidence_level=0.975)[0], places=12)
                self.assertAlmostEqual(expected[1], wilson_score_interval(successes, exposures, confidence_level=0.975)[1], places=12)
        # Garwood: scipy.stats.chi2.ppf at zero, one-event, sparse, and interior counts.
        for events, exposure_time, expected in [
            (0, 100, (0.0, 0.043820266346738815)),
            (1, 100, (0.00012578782206860127, 0.06380925698871577)),
            (2, 100, (0.0016710573863239486, 0.08122259946376378)),
            (10, 200, (0.02139074246924653, 0.09864438410279082)),
        ]:
            with self.subTest(kind="garwood", events=events):
                self.assertAlmostEqual(expected[0], garwood_interval(events, exposure_time, confidence_level=0.975)[0], places=12)
                self.assertAlmostEqual(expected[1], garwood_interval(events, exposure_time, confidence_level=0.975)[1], places=12)
        # Student-t quantiles used for single-arm and Welch intervals.
        for degrees_of_freedom, expected_95, expected_975 in [
            (1, 12.706204736174659, 25.45169957935704),
            (10, 2.228138851986274, 2.6337669157115977),
            (50, 2.008559112100757, 2.3109139355649013),
        ]:
            with self.subTest(kind="student-t", degrees_of_freedom=degrees_of_freedom):
                self.assertAlmostEqual(expected_95, _student_t_quantile(0.975, degrees_of_freedom), places=11)
                self.assertAlmostEqual(expected_975, _student_t_quantile(0.9875, degrees_of_freedom), places=11)
        # Welch: scipy.stats.ttest_ind_from_stats components and t.ppf endpoint,
        # including the lower-is-better transform for left minus right.
        left, right = pair_fixtures(
            metric_family="continuous_mean", direction="lower_is_better",
            left_aggregate={"sample_count": 20, "mean": 3.0, "standard_deviation": 2.0},
            right_aggregate={"sample_count": 25, "mean": 5.0, "standard_deviation": 3.0},
        )
        normalized_left, normalized_right = normalize_observation(left), normalize_observation(right)
        components = _welch_components(normalized_left, normalized_right, 0.95)
        assert components is not None
        point, lower, upper, variance_combination, degrees_of_freedom = components
        self.assertAlmostEqual(0.56, variance_combination, places=14)
        self.assertAlmostEqual(41.784011220196355, degrees_of_freedom, places=12)
        self.assertAlmostEqual(2.0, point, places=12)
        self.assertAlmostEqual(0.48957462730259493, lower, places=11)
        self.assertAlmostEqual(3.510425372697405, upper, places=11)
        interval = classify_observed_pair(
            normalized_left, normalized_right, equivalence_margin=0.01,
        )[1]
        self.assertAlmostEqual(2.0, interval.point, places=12)
        self.assertAlmostEqual(0.48957462730259493, interval.lower, places=11)
        self.assertAlmostEqual(3.510425372697405, interval.upper, places=11)

    def test_input_is_not_mutated(self) -> None:
        document = observation_fixture_for()
        original = deepcopy(document)
        normalize_observation(document)
        self.assertEqual(original, document)


if __name__ == "__main__":
    unittest.main()
