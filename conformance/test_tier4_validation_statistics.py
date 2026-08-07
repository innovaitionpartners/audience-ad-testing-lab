from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.validation.statistics import (  # noqa: E402
    InsufficientUncertaintyError,
    bca_block_interval,
    block_pairwise_agreement,
    complete_block_sign_permutation_p,
    holm_adjust,
    kendall_tau_b,
)


def comparison_with_six_pairs() -> dict[str, object]:
    return {
        "pairwise_comparisons": [
            {"synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_a_above_b"},
            {"synthetic_direction": "synthetic_b_above_a", "observed_direction": "observed_b_above_a"},
            {"synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_a_above_b"},
            {"synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_b_above_a"},
            {"synthetic_direction": "synthetic_b_above_a", "observed_direction": "observed_b_above_a"},
            {"synthetic_direction": "synthetic_a_above_b", "observed_direction": "observed_indeterminate"},
        ],
        "block_diagnostics": {"independent_block_count": 1},
    }


class Tier4StatisticsTests(unittest.TestCase):
    def test_tau_b_handles_ties(self):
        result = kendall_tau_b(
            synthetic_ranks=[1.0, 2.5, 2.5, 4.0],
            observed_ranks=[1.0, 2.0, 3.0, 4.0],
        )
        self.assertAlmostEqual(0.9128709291752769, result, places=12)

    def test_tau_exact_concordance_reversal_and_ties(self):
        self.assertEqual(1.0, kendall_tau_b([1, 2, 3], [1, 2, 3]))
        self.assertEqual(-1.0, kendall_tau_b([1, 2, 3], [3, 2, 1]))
        self.assertAlmostEqual(math.sqrt(2 / 3), kendall_tau_b([1, 1, 2], [1, 2, 3]))
        with self.assertRaises(ContractError):
            kendall_tau_b([1, 1], [2, 2])

    def test_tau_rejects_nonfinite_or_mismatched_input(self):
        for synthetic, observed in (([1, math.nan], [1, 2]), ([1], [1, 2])):
            with self.subTest(synthetic=synthetic, observed=observed), self.assertRaises(ContractError):
                kendall_tau_b(synthetic, observed)

    def test_pairs_do_not_become_independent_blocks(self):
        comparison = comparison_with_six_pairs()
        agreement, coverage = block_pairwise_agreement(comparison)
        self.assertEqual(1, comparison["block_diagnostics"]["independent_block_count"])
        self.assertAlmostEqual(4 / 5, agreement)
        self.assertAlmostEqual(5 / 6, coverage)

    def test_synthetic_tie_with_non_directional_observed_outcome_is_excluded(self):
        equivalent = comparison_with_six_pairs()
        equivalent_pairs = equivalent["pairwise_comparisons"]
        assert isinstance(equivalent_pairs, list)
        equivalent_pairs[5] = {
            "synthetic_direction": "synthetic_tie",
            "observed_direction": "observed_equivalent",
        }
        agreement, coverage = block_pairwise_agreement(equivalent)
        self.assertAlmostEqual(4 / 5, agreement)
        self.assertAlmostEqual(1.0, coverage)

        indeterminate = comparison_with_six_pairs()
        indeterminate_pairs = indeterminate["pairwise_comparisons"]
        assert isinstance(indeterminate_pairs, list)
        indeterminate_pairs[5]["synthetic_direction"] = "synthetic_tie"
        agreement, coverage = block_pairwise_agreement(indeterminate)
        self.assertAlmostEqual(4 / 5, agreement)
        self.assertAlmostEqual(5 / 6, coverage)

    def test_synthetic_tie_with_strict_observed_direction_is_a_non_match(self):
        comparison = comparison_with_six_pairs()
        pairs = comparison["pairwise_comparisons"]
        assert isinstance(pairs, list)
        pairs[2] = {
            "synthetic_direction": "synthetic_tie",
            "observed_direction": "observed_a_above_b",
        }
        agreement, coverage = block_pairwise_agreement(comparison)
        self.assertAlmostEqual(3 / 5, agreement)
        self.assertAlmostEqual(5 / 6, coverage)

    def test_pairwise_agreement_rejects_no_determinate_pairs(self):
        comparison = comparison_with_six_pairs()
        comparison["pairwise_comparisons"] = [{
            "synthetic_direction": "synthetic_a_above_b",
            "observed_direction": "observed_indeterminate",
        }]
        with self.assertRaises(ContractError):
            block_pairwise_agreement(comparison)

    def test_bca_is_deterministic_and_does_not_mutate_input(self):
        values = [0.2, 0.4, 0.3, 0.8, 0.6, 0.5, 0.1, 0.7, 0.9, 0.4, 0.6, 0.3]
        original = deepcopy(values)
        first = bca_block_interval(values, seed=1729)
        second = bca_block_interval(values, seed=1729)
        self.assertEqual(first, second)
        self.assertEqual(20_000, first.resamples)
        self.assertEqual(original, values)
        self.assertAlmostEqual(0.35, first.two_sided_lower)
        self.assertEqual(0.6166666666666667, first.two_sided_upper)
        self.assertEqual(0.3666666666666667, first.one_sided_lower)
        self.assertLessEqual(first.two_sided_lower, first.point)
        self.assertGreaterEqual(first.two_sided_upper, first.point)
        self.assertLessEqual(first.one_sided_lower, first.point)

    def test_bca_constant_values_return_a_constant_interval(self):
        interval = bca_block_interval([0.4, 0.4, 0.4], seed=1)
        self.assertEqual(0.4, interval.point)
        self.assertEqual(0.4, interval.two_sided_lower)
        self.assertEqual(0.4, interval.two_sided_upper)
        self.assertEqual(0.4, interval.one_sided_lower)

    def test_bca_rejects_undefined_or_nonfinite_input(self):
        for values in ([1.0], [1.0, math.inf]):
            with self.subTest(values=values), self.assertRaises((ContractError, InsufficientUncertaintyError)):
                bca_block_interval(values, seed=1)
        with self.assertRaises(InsufficientUncertaintyError):
            bca_block_interval([1.7e308, 1.6e308], seed=1)

    def test_exact_sign_permutation_and_deterministic_monte_carlo(self):
        self.assertEqual(1 / 8, complete_block_sign_permutation_p([1.0, 1.0, 1.0], seed=2))
        self.assertEqual(
            1 / (2 ** 20),
            complete_block_sign_permutation_p([1.0] * 20, seed=2),
        )
        first = complete_block_sign_permutation_p(
            [1.0] * 21, seed=1729, monte_carlo_resamples=1_000,
        )
        second = complete_block_sign_permutation_p(
            [1.0] * 21, seed=1729, monte_carlo_resamples=1_000,
        )
        self.assertEqual(first, second)
        self.assertEqual(1 / 1001, first)

    def test_holm_adjustment_has_original_order_and_is_monotone(self):
        values = [0.04, 0.01, 0.03, 0.8]
        original = deepcopy(values)
        self.assertEqual([0.09, 0.04, 0.09, 0.8], holm_adjust(values))
        self.assertEqual(original, values)
        with self.assertRaises(ContractError):
            holm_adjust([0.2, 1.1])


if __name__ == "__main__":
    unittest.main()
