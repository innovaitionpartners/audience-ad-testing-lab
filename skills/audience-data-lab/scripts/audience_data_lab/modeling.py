"""Interpretable, dependency-light exploratory modeling with NumPy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Mapping, Sequence

import numpy as np

from .common import ContractError
from .tabular import as_float, as_text


# These bounded defaults favor deterministic, auditable exploratory models over
# expensive optimization. They are not claims of universal statistical optimality.
KMEANS_MAX_ITERATIONS = 100
SILHOUETTE_SAMPLE_LIMIT = 300
STABILITY_COMPARISON_SEEDS = 4
MINIMUM_SILHOUETTE = 0.10
MINIMUM_COASSIGNMENT_STABILITY = 0.70
PROFILE_FEATURE_LIMIT = 8
FLOAT_COMPARISON_EPSILON = 1e-12
LOGISTIC_MAX_ITERATIONS = 1000
LOGISTIC_LEARNING_RATE = 0.1
LOGISTIC_L2_PENALTY = 0.01
LOGIT_CLIP_BOUND = 30.0
RIDGE_L2_PENALTY = 0.1


@dataclass(frozen=True)
class MatrixBundle:
    matrix: np.ndarray
    feature_names: tuple[str, ...]
    raw_numeric: Mapping[str, np.ndarray]
    category_counts: Mapping[str, Mapping[str, int]]


def _grouped_categories(
    rows: Sequence[Mapping[str, Any]],
    column: str,
    minimum_count: int,
) -> tuple[list[str], dict[str, int]]:
    raw_values = [as_text(row.get(column)) or "[missing]" for row in rows]
    counts: dict[str, int] = {}
    for value in raw_values:
        counts[value] = counts.get(value, 0) + 1
    grouped = [
        value if counts[value] >= minimum_count else "[suppressed-other]"
        for value in raw_values
    ]
    grouped_counts: dict[str, int] = {}
    for value in grouped:
        grouped_counts[value] = grouped_counts.get(value, 0) + 1
    return grouped, grouped_counts


def build_feature_matrix(
    rows: Sequence[Mapping[str, Any]],
    feature_columns: Sequence[str],
    numeric_columns: set[str],
    minimum_count: int,
) -> MatrixBundle:
    arrays: list[np.ndarray] = []
    names: list[str] = []
    raw_numeric: dict[str, np.ndarray] = {}
    category_counts: dict[str, dict[str, int]] = {}
    for column in feature_columns:
        if column in numeric_columns:
            values = np.array(
                [
                    np.nan if as_float(row.get(column)) is None else as_float(row.get(column))
                    for row in rows
                ],
                dtype=float,
            )
            finite = values[np.isfinite(values)]
            if finite.size == 0:
                raise ContractError(f"feature column {column} has no numeric values")
            median = float(np.median(finite))
            values = np.where(np.isfinite(values), values, median)
            raw_numeric[column] = values.copy()
            std = float(np.std(values))
            standardized = (values - float(np.mean(values))) / (std if std > 0 else 1.0)
            arrays.append(standardized[:, None])
            names.append(column)
        else:
            grouped, counts = _grouped_categories(rows, column, minimum_count)
            category_counts[column] = counts
            categories = sorted(counts, key=lambda value: (value.casefold(), value))
            for category in categories:
                arrays.append(
                    np.array([1.0 if value == category else 0.0 for value in grouped])[:, None]
                )
                names.append(f"{column}={category}")
    if not arrays:
        raise ContractError("modeling requires at least one feature")
    return MatrixBundle(
        matrix=np.hstack(arrays).astype(float),
        feature_names=tuple(names),
        raw_numeric=raw_numeric,
        category_counts=category_counts,
    )


def _kmeans_plus_plus(matrix: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    centers = [matrix[int(rng.integers(0, matrix.shape[0]))]]
    while len(centers) < k:
        squared = np.min(
            np.stack([np.sum((matrix - center) ** 2, axis=1) for center in centers]),
            axis=0,
        )
        total = float(np.sum(squared))
        if total <= 0:
            candidates = [index for index in range(matrix.shape[0])]
            centers.append(matrix[int(rng.choice(candidates))])
        else:
            centers.append(matrix[int(rng.choice(matrix.shape[0], p=squared / total))])
    return np.stack(centers)


def _fit_kmeans(matrix: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centers = _kmeans_plus_plus(matrix, k, rng)
    labels = np.full(matrix.shape[0], -1, dtype=int)
    for _ in range(KMEANS_MAX_ITERATIONS):
        distances = np.stack(
            [np.sum((matrix - center) ** 2, axis=1) for center in centers],
            axis=1,
        )
        next_labels = np.argmin(distances, axis=1)
        if np.array_equal(labels, next_labels):
            labels = next_labels
            break
        labels = next_labels
        for cluster in range(k):
            members = matrix[labels == cluster]
            if members.size == 0:
                centers[cluster] = matrix[int(rng.integers(0, matrix.shape[0]))]
            else:
                centers[cluster] = np.mean(members, axis=0)
    return labels, centers


def _sample_indices(
    size: int,
    seed: int,
    limit: int = SILHOUETTE_SAMPLE_LIMIT,
) -> np.ndarray:
    if size <= limit:
        return np.arange(size)
    return np.sort(np.random.default_rng(seed).choice(size, size=limit, replace=False))


def _silhouette(matrix: np.ndarray, labels: np.ndarray, seed: int) -> float:
    sample = _sample_indices(matrix.shape[0], seed)
    data = matrix[sample]
    sampled_labels = labels[sample]
    distances = np.sqrt(np.sum((data[:, None, :] - data[None, :, :]) ** 2, axis=2))
    scores: list[float] = []
    for index, label in enumerate(sampled_labels):
        same = np.where(sampled_labels == label)[0]
        same = same[same != index]
        if same.size == 0:
            scores.append(0.0)
            continue
        a = float(np.mean(distances[index, same]))
        other_means = [
            float(np.mean(distances[index, np.where(sampled_labels == other)[0]]))
            for other in sorted(set(sampled_labels))
            if other != label
        ]
        b = min(other_means)
        scores.append((b - a) / max(a, b, FLOAT_COMPARISON_EPSILON))
    return float(np.mean(scores)) if scores else 0.0


def _coassignment_agreement(
    first: np.ndarray,
    second: np.ndarray,
    seed: int,
) -> float:
    sample = _sample_indices(first.shape[0], seed)
    left = first[sample]
    right = second[sample]
    first_matrix = left[:, None] == left[None, :]
    second_matrix = right[:, None] == right[None, :]
    upper = np.triu_indices(sample.size, k=1)
    if upper[0].size == 0:
        return 1.0
    return float(np.mean(first_matrix[upper] == second_matrix[upper]))


def segment_candidates(
    bundle: MatrixBundle,
    cluster_counts: Sequence[int],
    *,
    seed: int,
    minimum_cluster_size: int,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    accepted: list[tuple[float, float, int, np.ndarray, np.ndarray]] = []
    for k in cluster_counts:
        if k < 2 or k >= bundle.matrix.shape[0]:
            continue
        labels, centers = _fit_kmeans(bundle.matrix, k, seed)
        sizes = [int(np.sum(labels == cluster)) for cluster in range(k)]
        silhouette = _silhouette(bundle.matrix, labels, seed)
        comparison_labels = [
            _fit_kmeans(bundle.matrix, k, seed + offset)[0]
            for offset in range(1, STABILITY_COMPARISON_SEEDS + 1)
        ]
        stability = float(
            np.mean(
                [
                    _coassignment_agreement(labels, alternate, seed)
                    for alternate in comparison_labels
                ]
            )
        )
        minimum_separation_met = silhouette >= MINIMUM_SILHOUETTE
        eligible = (
            min(sizes) >= minimum_cluster_size
            and minimum_separation_met
            and stability >= MINIMUM_COASSIGNMENT_STABILITY
        )
        candidates.append(
            {
                "cluster_count": k,
                "cluster_sizes": sizes,
                "silhouette": round(silhouette, 6),
                "coassignment_stability": round(stability, 6),
                "minimum_cluster_size_met": min(sizes) >= minimum_cluster_size,
                "minimum_separation_met": minimum_separation_met,
                "eligible": eligible,
            }
        )
        if eligible:
            accepted.append((silhouette, stability, k, labels, centers))
    if not accepted:
        return {
            "status": "no_stable_candidate",
            "recommended_cluster_count": None,
            "candidate_tests": candidates,
            "candidate_profiles": [],
        }
    accepted.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    _, _, best_k, best_labels, best_centers = accepted[0]
    profiles: list[dict[str, Any]] = []
    for cluster in range(best_k):
        center = best_centers[cluster]
        ranked = sorted(
            zip(bundle.feature_names, center),
            key=lambda item: abs(float(item[1])),
            reverse=True,
        )[:PROFILE_FEATURE_LIMIT]
        profiles.append(
            {
                "candidate_id": f"cluster-{cluster + 1}",
                "row_count": int(np.sum(best_labels == cluster)),
                "distinguishing_features": [
                    {"feature": name, "standardized_signal": round(float(value), 6)}
                    for name, value in ranked
                ],
                "status": "exploratory_segment_candidate",
            }
        )
    return {
        "status": "exploratory_candidate_available",
        "recommended_cluster_count": best_k,
        "candidate_tests": candidates,
        "candidate_profiles": profiles,
    }


def _parse_date(value: Any) -> datetime:
    text = as_text(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("event-date values must be ISO-8601") from exc


def _auc(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    positives = np.where(y_true == 1)[0]
    negatives = np.where(y_true == 0)[0]
    if positives.size == 0 or negatives.size == 0:
        return None
    wins = 0.0
    for positive in positives:
        comparison = scores[positive] - scores[negatives]
        wins += float(np.sum(comparison > 0)) + 0.5 * float(np.sum(comparison == 0))
    return wins / float(positives.size * negatives.size)


def performance_model(
    rows: Sequence[Mapping[str, Any]],
    bundle: MatrixBundle,
    outcome_column: str,
    event_date_column: str,
    *,
    holdout_fraction: float,
    minimum_model_rows: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    order = np.argsort(
        np.array([_parse_date(row.get(event_date_column)).timestamp() for row in rows])
    )
    matrix = bundle.matrix[order]
    outcome_values = np.array(
        [
            np.nan if as_float(rows[index].get(outcome_column)) is None
            else float(as_float(rows[index].get(outcome_column)))
            for index in order
        ]
    )
    valid = np.isfinite(outcome_values)
    matrix = matrix[valid]
    outcome_values = outcome_values[valid]
    sorted_dates = [_parse_date(rows[index].get(event_date_column)) for index in order]
    sorted_dates = [date for date, keep in zip(sorted_dates, valid) if keep]
    if outcome_values.size < minimum_model_rows:
        return (
            {
                "validation_state": "insufficient_data",
                "model_type": None,
                "usable_rows": int(outcome_values.size),
                "baseline_metrics": {},
                "model_metrics": {},
            },
            {
                "strategy": "chronological",
                "train_start": None,
                "train_end": None,
                "holdout_start": None,
                "holdout_end": None,
                "train_rows": 0,
                "holdout_rows": 0,
            },
        )
    split = int(math.floor(outcome_values.size * (1.0 - holdout_fraction)))
    split = max(1, min(split, outcome_values.size - 1))
    train_x, holdout_x = matrix[:split], matrix[split:]
    train_y, holdout_y = outcome_values[:split], outcome_values[split:]
    temporal = {
        "strategy": "chronological",
        "train_start": sorted_dates[0].isoformat(),
        "train_end": sorted_dates[split - 1].isoformat(),
        "holdout_start": sorted_dates[split].isoformat(),
        "holdout_end": sorted_dates[-1].isoformat(),
        "train_rows": int(train_y.size),
        "holdout_rows": int(holdout_y.size),
    }
    design_train = np.hstack([np.ones((train_x.shape[0], 1)), train_x])
    design_holdout = np.hstack([np.ones((holdout_x.shape[0], 1)), holdout_x])
    unique = set(float(value) for value in outcome_values)
    if unique.issubset({0.0, 1.0}) and len(unique) == 2:
        weights = np.zeros(design_train.shape[1], dtype=float)
        for _ in range(LOGISTIC_MAX_ITERATIONS):
            logits = np.clip(
                design_train @ weights,
                -LOGIT_CLIP_BOUND,
                LOGIT_CLIP_BOUND,
            )
            probabilities = 1.0 / (1.0 + np.exp(-logits))
            gradient = (design_train.T @ (probabilities - train_y)) / train_y.size
            gradient[1:] += LOGISTIC_L2_PENALTY * weights[1:]
            weights -= LOGISTIC_LEARNING_RATE * gradient
        holdout_scores = 1.0 / (
            1.0
            + np.exp(
                -np.clip(
                    design_holdout @ weights,
                    -LOGIT_CLIP_BOUND,
                    LOGIT_CLIP_BOUND,
                )
            )
        )
        baseline_probability = float(np.mean(train_y))
        baseline_scores = np.full(holdout_y.size, baseline_probability)
        baseline_auc = _auc(holdout_y, baseline_scores)
        model_auc = _auc(holdout_y, holdout_scores)
        result = {
            "validation_state": "retrospectively_evaluated",
            "model_type": "l2_logistic_regression",
            "usable_rows": int(outcome_values.size),
            "baseline_metrics": {
                "brier": round(float(np.mean((holdout_y - baseline_scores) ** 2)), 6),
                "auc": None if baseline_auc is None else round(float(baseline_auc), 6),
            },
            "model_metrics": {
                "brier": round(float(np.mean((holdout_y - holdout_scores) ** 2)), 6),
                "auc": None if model_auc is None else round(float(model_auc), 6),
            },
        }
    else:
        penalty = np.eye(design_train.shape[1]) * RIDGE_L2_PENALTY
        penalty[0, 0] = 0.0
        weights = np.linalg.pinv(design_train.T @ design_train + penalty) @ (
            design_train.T @ train_y
        )
        predictions = design_holdout @ weights
        baseline = np.full(holdout_y.size, float(np.mean(train_y)))
        total_variance = float(np.sum((holdout_y - np.mean(holdout_y)) ** 2))
        residual = float(np.sum((holdout_y - predictions) ** 2))
        result = {
            "validation_state": "retrospectively_evaluated",
            "model_type": "ridge_regression",
            "usable_rows": int(outcome_values.size),
            "baseline_metrics": {
                "mae": round(float(np.mean(np.abs(holdout_y - baseline))), 6),
                "rmse": round(float(np.sqrt(np.mean((holdout_y - baseline) ** 2))), 6),
            },
            "model_metrics": {
                "mae": round(float(np.mean(np.abs(holdout_y - predictions))), 6),
                "rmse": round(float(np.sqrt(np.mean((holdout_y - predictions) ** 2))), 6),
                "r2": None if total_variance <= 0 else round(1.0 - residual / total_variance, 6),
            },
        }
    return result, temporal
