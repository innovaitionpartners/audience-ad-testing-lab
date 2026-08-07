#!/usr/bin/env python3
"""Freeze and publish the fictional persona-behavior scenario matrix."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    SYNTHETIC_SCENARIO_REGISTRY,
    SYNTHETIC_SCENARIO_SEED,
)
from experimental_persona_calibration_oracle.simulation import (  # noqa: E402
    SYNTHETIC_RESPONSE_ADAPTER_SOURCE,
    build_study_manifest,
    generate_and_publish_synthetic_scenario,
    preflight_new_path_no_follow,
    publish_new_file_no_follow,
)

_GOLDEN_BEHAVIORS = (
    "null-effect",
    "known-proof-need-miss",
    "non-identifiable-twins",
    "one-campaign-only",
    "observational-confounding",
    "platform-interaction",
    "denominator-mismatch",
    "attribution-mismatch",
    "late-maturation",
    "modeled-fractional",
    "suppressed-missing",
    "breakdown-double-count",
    "block-reversal",
    "creative-attribute-ambiguity",
    "duplicate-evidence",
    "hidden-oracle-leak",
    "structural-change-request",
    "multiple-hypotheses",
    "candidate-extra-diff",
    "candidate-registration",
    "existing-output",
    "reversed-row-order",
    "sealed-holdout-reuse",
    "base-panel-package-bytes",
    "ad-testing-output-bytes",
    "nonlinear-saturation",
    "delayed-censored-outcomes",
    "zero-inflated-value",
    "production-library-snapshot",
)
_DGP_GENERALIZED_BEHAVIORS = frozenset(
    {
        "zero-inflated-value",
    }
)
_DGP_COVERAGE_FAMILIES = (
    {
        "scenario_family_id": "nonlinear-saturation",
        "dgp_id": "nonlinear-saturation",
        "dgp_class": "nonlinear_saturation",
        "signature": {
            "observable": "conversion_rate_by_impression_band",
            "assertion": "high_impression_rate_lt_low_impression_rate",
            "low_impression_max": 950,
            "high_impression_min": 1050,
        },
    },
    {
        "scenario_family_id": "delayed-censored-outcomes",
        "dgp_id": "delayed-censored-outcomes",
        "dgp_class": "delayed_censored",
        "signature": {
            "observable": "late_block_zero_share",
            "assertion": "late_blocks_all_zero_and_early_blocks_not_all_zero",
            "late_block_min": 4,
        },
    },
    {
        "scenario_family_id": "heavy-tailed-revenue",
        "dgp_id": "heavy-tailed-revenue",
        "dgp_class": "heavy_tailed",
        "signature": {
            "observable": "conversion_zero_share_and_max_rate",
            "assertion": "zero_share_gt_and_max_rate_gt",
            "zero_share_min_exclusive": 0.75,
            "max_rate_min_exclusive": 0.30,
        },
    },
    {
        "scenario_family_id": "zero-inflated-value",
        "dgp_id": "zero-inflated-value",
        "dgp_class": "zero_inflated",
        "signature": {
            "observable": "conversion_zero_share_and_max_rate",
            "assertion": "zero_share_between_and_max_rate_between",
            "zero_share_min_inclusive": 0.40,
            "zero_share_max_inclusive": 0.60,
            "max_rate_min_inclusive": 0.12,
            "max_rate_max_inclusive": 0.25,
        },
    },
)
_COVERAGE_EXCLUSIONS = {
    "null-effect": "The no-change result is executed by one randomized-block null DGP, not three incompatible DGP classes.",
    "known-proof-need-miss": "The positive proposal is executed by one heterogeneous proof-need DGP; no cross-family proposal claim is made.",
    "non-identifiable-twins": "The sealed twins are an identification construction with two matched fixtures, not three incompatible DGP classes.",
    "one-campaign-only": "This is a sample-eligibility gate exercised by one deliberately incomplete evidence history.",
    "observational-confounding": "This is a design-quality gate exercised by one observational fixture.",
    "platform-interaction": "This is a platform-pooling gate exercised by one interaction fixture.",
    "denominator-mismatch": "This is a measurement-compatibility gate exercised by one denominator fixture.",
    "attribution-mismatch": "This is a measurement-compatibility gate exercised by one attribution fixture.",
    "late-maturation": "This is a maturity-eligibility gate exercised by one recent-evidence fixture.",
    "modeled-fractional": "This is a normalization preservation check, not a DGP-generalized decision claim.",
    "suppressed-missing": "This is a missing-state normalization check, not a DGP-generalized decision claim.",
    "breakdown-double-count": "This is an evidence identity/deduplication check, not a DGP-generalized decision claim.",
    "block-reversal": "This is a blocked-design analysis check exercised by one reversal fixture.",
    "creative-attribute-ambiguity": "This is a registry binding validation check, not a DGP-generalized decision claim.",
    "duplicate-evidence": "This is an append-only evidence-library hard-failure check.",
    "hidden-oracle-leak": "This is an oracle-authority boundary check, not a statistical DGP claim.",
    "structural-change-request": "This is a proposal-scope hard-failure check.",
    "multiple-hypotheses": "This is an identification/ambiguity gate exercised by one multi-hypothesis fixture.",
    "candidate-extra-diff": "This is a candidate diff allowlist check.",
    "candidate-registration": "This is a production-authority boundary check.",
    "existing-output": "This is a filesystem no-clobber check.",
    "reversed-row-order": "This is a deterministic ordering check.",
    "sealed-holdout-reuse": "This is a sealed-partition reuse check.",
    "base-panel-package-bytes": "This is a byte-preservation check.",
    "ad-testing-output-bytes": "This is a byte-preservation check.",
    "nonlinear-saturation": (
        "The sensitivity-reporting semantic is exercised by one nonlinear "
        "fixture; only the broader honest-abstention behavior is generalized."
    ),
    "delayed-censored-outcomes": (
        "The ineligible-until-final semantic is exercised by one delayed/"
        "censored fixture; only honest abstention is generalized."
    ),
    "production-library-snapshot": "This is a production-library non-mutation check.",
}


def _parameter_set(scenario_id: str) -> dict[str, object]:
    visible_effect_rate = (
        0.04
        if scenario_id == "known-proof-need-miss"
        else 0.025
        if scenario_id.startswith("non-identifiable-twin-")
        else 0.0
    )
    noise_family = {
        "delayed-censored-outcomes": "delayed-censored",
        "heavy-tailed-revenue": "heavy-tailed",
        "nonlinear-saturation": "nonlinear-saturation",
        "zero-inflated-value": "zero-inflated",
    }.get(scenario_id, "binomial")
    document: dict[str, object] = {
        "parameter_set_id": f"{scenario_id}-parameters",
        "parameter_version": "1.0.0",
        "parameter_values": [
            {"name": "baseline-rate", "value_type": "number", "value": 0.08},
            {"name": "blocks", "value_type": "integer", "value": 96},
            {"name": "enabled", "value_type": "boolean", "value": True},
            {"name": "noise-family", "value_type": "string", "value": noise_family},
            {
                "name": "visible-effect-rate",
                "value_type": "number",
                "value": visible_effect_rate,
            },
        ],
        "parameters_sha256": None,
    }
    document["parameters_sha256"] = sha256_json(document)
    return document


def _manifest(created_at: str) -> dict[str, object]:
    source_digest = "sha256:" + hashlib.sha256(
        SYNTHETIC_RESPONSE_ADAPTER_SOURCE.read_bytes()
    ).hexdigest()
    scenario_order = [
        "null-effect",
        "known-proof-need-miss",
        "non-identifiable-twin-a",
        "non-identifiable-twin-b",
        *sorted(
            set(SYNTHETIC_SCENARIO_REGISTRY)
            - {
                "null-effect",
                "known-proof-need-miss",
                "non-identifiable-twin-a",
                "non-identifiable-twin-b",
            }
        ),
    ]
    families = [
        (
            scenario_id,
            SYNTHETIC_SCENARIO_REGISTRY[scenario_id]["dgp_id"],
            SYNTHETIC_SCENARIO_SEED[scenario_id],
            SYNTHETIC_SCENARIO_REGISTRY[scenario_id]["partition"],
        )
        for scenario_id in scenario_order
    ]
    return build_study_manifest(
        study_id="fictional-persona-behavior-study",
        created_at=created_at,
        generator_version="1.0.0",
        scenario_specs=[
            {
                "scenario_id": scenario_id,
                "dgp_id": dgp_id,
                "dgp_version": "1.0.0",
                "seed": seed,
                "repetitions": 1,
                "parameters": _parameter_set(scenario_id),
                "partition": partition,
            }
            for scenario_id, dgp_id, seed, partition in families
        ],
        estimands=[
            {"estimand_id": "cfo-quantified-payback-rate-contrast"}
        ],
        parameter_grid={"rate": [0.0, 0.025, 0.04]},
        seeds=list(
            dict.fromkeys(seed for _id, _dgp, seed, _partition in families)
        ),
        repetitions=1,
        monte_carlo_error_targets={
            "maximum": 0.01,
            "method_version": "deterministic-batch-quantile-mcse-v1",
            "batch_count": 10,
            "batch_partition_policy": "equal_contiguous_replicate_batches",
            "quantile_interpolation": "linear",
            "reported_measures": [
                "bootstrap_mean",
                "interval_lower",
                "interval_upper",
            ],
        },
        diagnosis_method={
            "method_version": "blocked-contrast-bootstrap-v1",
            "contrast_source": "registered_numerator_denominator",
            "block_weighting": "equal",
            "experiment_weighting": "equal",
            "minimum_complete_blocks_per_experiment": 6,
            "minimum_independent_experiments": 2,
            "interval_method": "deterministic_percentile_block_bootstrap",
            "interval_level": 0.95,
            "bootstrap_repetitions": 500,
            "bootstrap_seed": 73021,
            "minimum_practical_effect": 0.02,
            "minimum_practical_effect_rule": (
                "directional_point_estimate_strictly_exceeds_threshold"
            ),
            "missingness_policy": "incomplete_block_ineligible",
            "maturity_policy": "finalized_only",
            "observational_policy": "descriptive_only",
            "early_stopping_permitted": False,
        },
        synthetic_response_adapter={
            "adapter_id": "frozen-synthetic-panelist-response",
            "version": "1.0.0",
            "source_sha256": source_digest,
            "feature_allowlist": [
                "creative_attributes",
                "experiment_design",
                "persona_snapshot",
                "study_manifest",
            ],
            "deterministic_tie_rule": (
                "score-descending-creative-id-ascending"
            ),
            "seed": 73021,
        },
        stopping_rule={"rule": "none"},
        performance_measures=[
            "correct-abstention",
            "false-proposal",
            "target-field-accuracy",
        ],
    )


def _coverage_matrix(manifest: dict[str, object]) -> dict[str, object]:
    scenario_rows = list(manifest["scenario_families"])
    inventory = [
        "study-manifest.json",
        *[
            f"{row['partition']}/{row['scenario_id']}/{relative}"
            for row in scenario_rows
            for relative in (
                "canonical-observations.json",
                "experiment-design.json",
                "raw/google/daily-aggregates.json",
                "raw/linkedin/daily-aggregates.json",
                "raw/meta/daily-aggregates.json",
                "raw/tiktok/daily-aggregates.json",
                "scenario-manifest.json",
            )
        ],
        *[
            f"oracle/{row['partition']}/{row['scenario_id']}/{relative}"
            for row in scenario_rows
            for relative in (
                "hidden-oracle.json",
                "oracle-manifest.json",
            )
        ],
    ]
    matrix: dict[str, object] = {
        "schema_version": "synthetic-persona-behavior-coverage-matrix-v2",
        "study_manifest_sha256": manifest["manifest_sha256"],
        "fixture_inventory": sorted(inventory),
        "declared_probe_ids": [
            "fixture-dgp-signature-and-evaluation-v1"
        ],
        "rows": [
            {
                "behavior_id": behavior_id,
                "coverage_status": (
                    "dgp_generalized"
                    if behavior_id in _DGP_GENERALIZED_BEHAVIORS
                    else "excluded"
                ),
                **(
                    {
                        "claim": (
                            "The engine abstains without creating a candidate "
                            "when distributional stress does not identify a "
                            "persona-behavior update."
                        ),
                        "cells": [
                            {
                                "behavior_id": behavior_id,
                                "scenario_family_id": family[
                                    "scenario_family_id"
                                ],
                                "dgp_id": family["dgp_id"],
                                "dgp_class": family["dgp_class"],
                                "probe_id": (
                                    "fixture-dgp-signature-and-evaluation-v1"
                                ),
                                "observable": {
                                    "fixture": (
                                        "canonical-observations.json"
                                    ),
                                    "runtime": (
                                        "scenario_family_results[]."
                                        "scenario_results[0]"
                                    ),
                                },
                                "signature": family["signature"],
                                "expected_observation": {
                                    "fixture_dgp_signature": True,
                                    "oracle_failure_mechanism": family[
                                        "scenario_family_id"
                                    ],
                                    "expected_action": "abstain",
                                    "actual_action": "abstain",
                                    "evaluation_result": (
                                        "correct_abstention"
                                    ),
                                    "candidate_count": 0,
                                },
                                "rationale": (
                                    f"{family['dgp_class']} is structurally "
                                    "incompatible with the other declared "
                                    "classes; fixture statistics and the real "
                                    "evaluation output must independently "
                                    "support the abstention."
                                ),
                            }
                            for family in _DGP_COVERAGE_FAMILIES
                        ],
                    }
                    if behavior_id in _DGP_GENERALIZED_BEHAVIORS
                    else {
                        "exclusion_reason": _COVERAGE_EXCLUSIONS[
                            behavior_id
                        ],
                        "cells": [],
                    }
                ),
            }
            for behavior_id in _GOLDEN_BEHAVIORS
        ],
        "coverage_matrix_sha256": None,
    }
    matrix["coverage_matrix_sha256"] = sha256_json(matrix)
    return matrix


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--public-fixtures-root", required=True)
    parser.add_argument("--oracle-fixtures-root", required=True)
    parser.add_argument("--created-at", required=True)
    return parser


def _unsafe(error: ContractError) -> bool:
    text = str(error).casefold()
    return any(
        token in text
        for token in (
            "already exists",
            "symlink",
            "path alias",
            "must be disjoint",
            "unsafe",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path = Path(args.manifest_output).absolute()
    public_root = Path(args.public_fixtures_root).absolute()
    oracle_root = Path(args.oracle_fixtures_root).absolute()
    try:
        preflight_new_path_no_follow(manifest_path, "study manifest")
        manifest = _manifest(args.created_at)
        coverage_path = manifest_path.parent / "coverage-matrix.json"
        preflight_new_path_no_follow(coverage_path, "coverage matrix")
        targets = [
            (
                row["scenario_id"],
                row["partition"],
                public_root / str(row["partition"]) / str(row["scenario_id"]),
                oracle_root / str(row["partition"]) / str(row["scenario_id"]),
            )
            for row in manifest["scenario_families"]
        ]
        for _scenario_id, _partition, public, oracle in targets:
            preflight_new_path_no_follow(public, "public fixture")
            preflight_new_path_no_follow(oracle, "oracle fixture")
        publish_new_file_no_follow(
            manifest_path,
            canonical_json_bytes(manifest),
            "synthetic study manifest",
        )
        publish_new_file_no_follow(
            coverage_path,
            canonical_json_bytes(_coverage_matrix(manifest)),
            "synthetic coverage matrix",
        )
        for scenario_id, _partition, public, oracle in targets:
            generate_and_publish_synthetic_scenario(
                manifest=manifest,
                scenario_id=str(scenario_id),
                public_output_dir=public,
                oracle_output_dir=oracle,
            )
        return 0
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 3 if _unsafe(exc) else 2


if __name__ == "__main__":
    raise SystemExit(main())
