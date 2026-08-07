from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

try:
    import numpy as np
    from scipy import stats
except ModuleNotFoundError:
    np = None
    stats = None

from audience_panel_builder.population.validation.statistics import (  # noqa: E402
    _complete_block_resample_indices,
    bca_block_interval,
    kendall_tau_b,
)


def deterministic_rank_cases() -> tuple[tuple[list[float], list[float]], ...]:
    return (
        ([1.0, 2.5, 2.5, 4.0], [1.0, 2.0, 3.0, 4.0]),
        ([3.0, 1.0, 4.0, 2.0], [2.0, 1.0, 4.0, 3.0]),
        ([1.0, 1.0, 2.0, 3.0], [3.0, 2.0, 1.0, 1.0]),
    )


@unittest.skipIf(stats is None, "SciPy oracle is optional outside conformance")
class Tier4SciPyOracleTests(unittest.TestCase):
    def test_tau_matches_scipy(self):
        assert stats is not None
        for synthetic, observed in deterministic_rank_cases():
            with self.subTest(synthetic=synthetic, observed=observed):
                self.assertAlmostEqual(
                    stats.kendalltau(synthetic, observed, variant="b").statistic,
                    kendall_tau_b(synthetic, observed),
                    places=12,
                )

    def test_resample_matrix_matches_independent_numpy_oracle(self):
        assert np is not None
        values = np.array([0.2, 0.4, 0.3, 0.8, 0.6, 0.5], dtype=float)
        indices = _complete_block_resample_indices(6, seed=1729, resamples=257)
        oracle_resampled = np.array([np.mean(values[list(row)]) for row in indices])
        production_resampled = np.array([
            sum(float(values[index]) for index in row) / len(row) for row in indices
        ])
        oracle_jackknife = np.array([
            np.mean(np.delete(values, index)) for index in range(len(values))
        ])
        production_jackknife = np.array([
            sum(float(value) for position, value in enumerate(values) if position != index) / (len(values) - 1)
            for index in range(len(values))
        ])
        self.assertLessEqual(float(np.max(np.abs(oracle_resampled - production_resampled))), 1e-12)
        self.assertLessEqual(float(np.max(np.abs(oracle_jackknife - production_jackknife))), 1e-12)

    def test_bca_bounds_are_a_stochastic_scipy_sanity_check(self):
        assert np is not None and stats is not None
        values = np.array([0.2, 0.4, 0.3, 0.8, 0.6, 0.5, 0.1, 0.7, 0.9, 0.4, 0.6, 0.3])
        production = bca_block_interval(values.tolist(), seed=1729, resamples=10_000)
        oracle = stats.bootstrap(
            (values,), np.mean, method="BCa", confidence_level=0.95,
            n_resamples=10_000, rng=np.random.default_rng(1729),
        ).confidence_interval
        self.assertAlmostEqual(float(oracle.low), production.two_sided_lower, delta=0.02)
        self.assertAlmostEqual(float(oracle.high), production.two_sided_upper, delta=0.02)


if __name__ == "__main__":
    unittest.main()
