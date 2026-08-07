"""Oracle-only deterministic synthetic study generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import stat
import errno

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.contracts import (
    STUDY_MANIFEST_VERSION,
    SYNTHETIC_SCENARIO_REGISTRY,
    SYNTHETIC_STUDY_GENERATOR_VERSION,
    _validate_trusted_generator_observation_v1,
    validate_study_manifest,
)
from audience_panel_builder.population.experimental_calibration import (
    synthetic_response_adapter as _adapter,
)

from .contracts import ORACLE_VERSION, validate_oracle


GENERATOR_VERSION = SYNTHETIC_STUDY_GENERATOR_VERSION
SYNTHETIC_RESPONSE_ADAPTER_SOURCE = Path(_adapter.__file__).resolve(strict=True)
_EXPECTED_DGPS = {
    scenario_id: str(binding["dgp_id"])
    for scenario_id, binding in SYNTHETIC_SCENARIO_REGISTRY.items()
}
_PLATFORMS = ("meta", "google", "linkedin", "tiktok")
_CREATIVES = (
    {
        "creative_id": "quantified-payback",
        "attributes": ["implementation-risk", "quantified-payback"],
    },
    {
        "creative_id": "strategic-control",
        "attributes": ["governance", "strategic-control"],
    },
    {
        "creative_id": "peer-validation",
        "attributes": ["peer-validation", "social-proof"],
    },
    {
        "creative_id": "ease-of-use",
        "attributes": ["ease-of-use", "fast-setup"],
    },
)
_GROUPING_KEYS = [
    "batch_id",
    "block_id",
    "campaign_id",
    "creative_id",
    "date",
    "experiment_id",
    "segment_id",
]
_EXPECTED_PROFILE_OPERATION = {
    "operation_type": "profile_snapshot_update",
    "target_persona_id": "finance-pricing-archetype",
    "target_field": "proof_needs",
    "expected_value": [
        "Quantified payback and implementation-risk evidence",
    ],
    "value_direction_rule": "exact_array_equality",
}
_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FILE_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_PROTECTED_SYSTEM_ALIASES = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


@dataclass(frozen=True)
class _ScenarioMaterial:
    public_files: Mapping[str, bytes]
    oracle_files: Mapping[str, bytes]
    public_manifest_sha256: str
    oracle_manifest_sha256: str


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _rng(seed: int) -> random.Random:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContractError("scenario seed must be a non-negative integer")
    return random.Random(seed)


def _copy_json(value: object, path: str) -> object:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ContractError(f"{path} must be finite JSON-shaped data") from exc


def _adapter_source_digest() -> str:
    path = SYNTHETIC_RESPONSE_ADAPTER_SOURCE
    try:
        value = os.lstat(path)
        raw = path.read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError("frozen synthetic response adapter source is unavailable") from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise ContractError("frozen synthetic response adapter source must be a real file")
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise ContractError("frozen synthetic response adapter source is not canonical UTF-8/LF")
    return _digest_bytes(raw)


def build_study_manifest(
    *,
    study_id: str,
    created_at: str,
    generator_version: str,
    scenario_specs: Sequence[Mapping[str, object]],
    estimands: Sequence[Mapping[str, object]],
    parameter_grid: Mapping[str, object],
    seeds: Sequence[int],
    repetitions: int,
    monte_carlo_error_targets: Mapping[str, object],
    diagnosis_method: Mapping[str, object],
    synthetic_response_adapter: Mapping[str, object],
    stopping_rule: Mapping[str, object],
    performance_measures: Sequence[str],
) -> dict[str, object]:
    """Freeze the complete verification-only study before outcomes exist."""

    scenarios = _copy_json(list(scenario_specs), "scenario_specs")
    if not isinstance(scenarios, list):
        raise ContractError("scenario_specs must be an array")
    ids = [
        row.get("scenario_id")
        for row in scenarios
        if isinstance(row, dict)
    ]
    if set(ids) != set(_EXPECTED_DGPS) or len(ids) != len(_EXPECTED_DGPS):
        raise ContractError(
            "scenario_specs must contain the complete closed scenario registry"
        )
    if generator_version != GENERATOR_VERSION:
        raise ContractError(
            f"generator_version must be exactly {GENERATOR_VERSION}"
        )
    for row in scenarios:
        if not isinstance(row, dict):
            raise ContractError("scenario_specs entries must be objects")
        scenario_id = row.get("scenario_id")
        expected = SYNTHETIC_SCENARIO_REGISTRY.get(str(scenario_id))
        if expected is None or any(
            row.get(key) != value for key, value in expected.items()
        ):
            raise ContractError(
                f"{scenario_id!r} must bind its closed DGP/version/partition"
            )
    expected_seeds = list(
        dict.fromkeys(int(row["seed"]) for row in scenarios)
    )
    if list(seeds) != expected_seeds:
        raise ContractError(
            "seeds must equal the ordered unique scenario-family seeds"
        )
    if any(row.get("repetitions") != repetitions for row in scenarios):
        raise ContractError(
            "scenario repetitions must equal the top-level repetitions"
        )

    adapter = _copy_json(
        dict(synthetic_response_adapter),
        "synthetic_response_adapter",
    )
    if not isinstance(adapter, dict):
        raise ContractError("synthetic_response_adapter must be an object")
    expected_adapter = {
        "adapter_id": _adapter.ADAPTER_ID,
        "version": _adapter.ADAPTER_VERSION,
        "source_sha256": _adapter_source_digest(),
        "feature_allowlist": list(_adapter.FEATURE_ALLOWLIST),
        "deterministic_tie_rule": _adapter.DETERMINISTIC_TIE_RULE,
    }
    for key, expected in expected_adapter.items():
        if adapter.get(key) != expected:
            raise ContractError(
                f"synthetic_response_adapter.{key} does not match frozen source"
            )

    document: dict[str, object] = {
        "schema_version": STUDY_MANIFEST_VERSION,
        "study_id": study_id,
        "created_at": created_at,
        "purpose": "verification_only",
        "generator_version": generator_version,
        "scenario_families": scenarios,
        "estimands": _copy_json(list(estimands), "estimands"),
        "parameter_grid": _copy_json(dict(parameter_grid), "parameter_grid"),
        "seeds": _copy_json(list(seeds), "seeds"),
        "repetitions": repetitions,
        "monte_carlo_error_targets": _copy_json(
            dict(monte_carlo_error_targets),
            "monte_carlo_error_targets",
        ),
        "diagnosis_method": _copy_json(
            dict(diagnosis_method),
            "diagnosis_method",
        ),
        "synthetic_response_adapter": adapter,
        "stopping_rule": _copy_json(dict(stopping_rule), "stopping_rule"),
        "performance_measures": _copy_json(
            list(performance_measures),
            "performance_measures",
        ),
        "manifest_sha256": None,
    }
    document["manifest_sha256"] = sha256_json(document)
    return validate_study_manifest(document)


def _parameter_values(family: Mapping[str, object]) -> dict[str, object]:
    parameter_set = family["parameters"]
    if not isinstance(parameter_set, Mapping):
        raise ContractError("scenario parameters must be an object")
    raw_values = parameter_set.get("parameter_values")
    if not isinstance(raw_values, list):
        raise ContractError("scenario parameter_values must be an array")
    values = {
        row["name"]: row["value"]
        for row in raw_values
        if isinstance(row, Mapping)
        and isinstance(row.get("name"), str)
        and "value" in row
    }
    if set(values) != {
        "baseline-rate",
        "blocks",
        "enabled",
        "noise-family",
        "visible-effect-rate",
    }:
        raise ContractError("scenario parameters do not match the closed DGP parameter set")
    if (
        type(values["baseline-rate"]) is not float
        or not 0.0 <= values["baseline-rate"] <= 1.0
        or type(values["blocks"]) is not int
        or values["blocks"] < 96
        or values["blocks"] % 16 != 0
        or values["enabled"] is not True
        or values["noise-family"] not in {
            "binomial",
            "delayed-censored",
            "heavy-tailed",
            "nonlinear-saturation",
            "zero-inflated",
        }
        or type(values["visible-effect-rate"]) is not float
        or not 0.0 <= values["visible-effect-rate"] <= 1.0
    ):
        raise ContractError("scenario parameters are outside the closed DGP bounds")
    return values


def _binomial(rng: random.Random, count: int, probability: float) -> int:
    bounded = min(1.0, max(0.0, probability))
    return sum(rng.random() < bounded for _ in range(count))


def _public_scenario_key(scenario_id: str) -> str:
    if scenario_id.startswith("non-identifiable-twin-"):
        return "non-identifiable-twin"
    return scenario_id


def _scenario_rows(
    *,
    manifest: Mapping[str, object],
    family: Mapping[str, object],
    visible_kind: str,
) -> list[dict[str, object]]:
    values = _parameter_values(family)
    rng = _rng(int(family["seed"]))
    rows: list[dict[str, object]] = []
    block_count = int(values["blocks"])
    blocks_per_cell = block_count // (2 * 2 * len(_PLATFORMS))
    block_index = 0
    for experiment_number in (1, 2):
        for segment_id in ("cfo", "operations"):
            for platform in _PLATFORMS:
                for cell_block in range(blocks_per_cell):
                    experiment_id = f"fictional-experiment-{experiment_number}"
                    batch_id = f"fictional-batch-{1 + (cell_block % 3)}"
                    block_id = (
                        f"block-e{experiment_number:02d}-{segment_id}-"
                        f"{platform}-{cell_block + 1:02d}"
                    )
                    date = f"2026-07-{1 + (block_index % 28):02d}"
                    block_index += 1
                    for creative in _CREATIVES:
                        creative_id = str(creative["creative_id"])
                        impressions = 900 + rng.randrange(0, 201)
                        rate = float(values["baseline-rate"])
                        if (
                            visible_kind == "known-proof-need-miss"
                            and segment_id == "cfo"
                            and creative_id == "quantified-payback"
                        ):
                            rate += float(values["visible-effect-rate"])
                        elif (
                            visible_kind == "non-identifiable-twin"
                            and segment_id == "cfo"
                            and creative_id == "quantified-payback"
                        ):
                            rate += float(values["visible-effect-rate"])
                        noise_family = str(values["noise-family"])
                        if noise_family == "heavy-tailed":
                            conversions = (
                                0
                                if rng.random() < 0.8
                                else _binomial(
                                    rng,
                                    impressions,
                                    min(1.0, rate * 5.0),
                                )
                            )
                        elif noise_family == "zero-inflated":
                            conversions = (
                                0
                                if rng.random() < 0.5
                                else _binomial(
                                    rng,
                                    impressions,
                                    min(1.0, rate * 2.0),
                                )
                            )
                        elif noise_family == "nonlinear-saturation":
                            saturation = 1.0 / (
                                1.0 + max(0, impressions - 900) / 400.0
                            )
                            conversions = _binomial(
                                rng,
                                impressions,
                                rate * saturation,
                            )
                        elif (
                            noise_family == "delayed-censored"
                            and cell_block >= blocks_per_cell // 2
                        ):
                            conversions = 0
                        else:
                            conversions = _binomial(rng, impressions, rate)
                        clicks = min(
                            impressions,
                            conversions
                            + _binomial(
                                rng,
                                impressions - conversions,
                                0.08,
                            ),
                        )
                        rows.append(
                            {
                                "date": date,
                                "experiment_id": experiment_id,
                                "campaign_id": (
                                    f"{experiment_id}-{platform}-"
                                    f"{segment_id}-campaign"
                                ),
                                "batch_id": batch_id,
                                "block_id": block_id,
                                "segment_id": segment_id,
                                "platform": platform,
                                "creative_id": creative_id,
                                "impressions": impressions,
                                "clicks": clicks,
                                "conversions": conversions,
                                "spend_minor": round(
                                    impressions
                                    * (0.011 + rng.random() * 0.004),
                                    2,
                                ),
                                "video_p25": min(
                                    impressions, int(impressions * 0.58)
                                ),
                                "video_p50": min(
                                    impressions, int(impressions * 0.39)
                                ),
                                "video_p75": min(
                                    impressions, int(impressions * 0.24)
                                ),
                                "video_p100": min(
                                    impressions, int(impressions * 0.13)
                                ),
                            }
                        )
    return rows


def _source_binding(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    parameters = family["parameters"]
    assert isinstance(parameters, Mapping)
    return {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
        "scenario_id": family["scenario_id"],
        "dgp_id": family["dgp_id"],
        "dgp_version": family["dgp_version"],
        "seed": family["seed"],
        "repetitions": family["repetitions"],
        "parameters_sha256": parameters["parameters_sha256"],
    }


def _state_markers(platform: str) -> list[dict[str, object]]:
    markers = [
        {
            "marker_id": f"{platform}-explicit-zero",
            "state": "zero",
            "meaning": "reported numeric zero",
        },
        {
            "marker_id": f"{platform}-missing",
            "state": "missing",
            "meaning": "field present without an available value",
        },
        {
            "marker_id": f"{platform}-suppressed",
            "state": "suppressed",
            "meaning": "platform withheld a low-volume value",
        },
        {
            "marker_id": f"{platform}-omitted-zero",
            "state": "omitted-zero",
            "meaning": "absent grouping row means zero only under the declared policy",
        },
    ]
    return markers


def _attribution(platform: str) -> dict[str, str]:
    return {
        "meta": {
            "click_window": "7-day",
            "view_window": "1-day",
            "engaged_view_window": "not-applicable",
            "model": "last-touch",
        },
        "google": {
            "click_window": "30-day",
            "view_window": "not-applicable",
            "engaged_view_window": "not-applicable",
            "model": "data-driven",
        },
        "linkedin": {
            "click_window": "30-day",
            "view_window": "7-day",
            "engaged_view_window": "not-applicable",
            "model": "last-touch",
        },
        "tiktok": {
            "click_window": "7-day",
            "view_window": "1-day",
            "engaged_view_window": "1-day-engaged-view",
            "model": "last-touch",
        },
    }[platform]


def _report_time_basis(platform: str) -> str:
    return (
        "interaction-and-conversion-date"
        if platform == "google"
        else "conversion-date"
    )


def _registered_attribution(platform: str) -> dict[str, str]:
    result = {
        key: ("not-reported" if value is None else value)
        for key, value in {
            "model": {
                "meta": None,
                "google": "data-driven",
                "linkedin": "last-touch",
                "tiktok": None,
            }[platform],
            "click_window": {
                "meta": "7-day",
                "google": None,
                "linkedin": None,
                "tiktok": "7-day",
            }[platform],
            "view_window": {
                "meta": "1-day",
                "google": None,
                "linkedin": None,
                "tiktok": "1-day",
            }[platform],
            "engaged_view_window": {
                "meta": None,
                "google": None,
                "linkedin": None,
                "tiktok": "1-day-engaged-view",
            }[platform],
        }.items()
    }
    return result


def _registered_numerator_event(platform: str) -> dict[str, str]:
    return {
        "meta": {
            "metric_id": "lead",
            "event_kind": "count",
            "attribution_kind": "aggregate",
            "report_time_basis": "conversion",
        },
        "google": {
            "metric_id": "conversions",
            "event_kind": "count",
            "attribution_kind": "aggregate",
            "report_time_basis": "conversion-date",
        },
        "linkedin": {
            "metric_id": "total_conversions",
            "event_kind": "count",
            "attribution_kind": "aggregate",
            "report_time_basis": "conversion-date",
        },
        "tiktok": {
            "metric_id": "cta_conversions",
            "event_kind": "count",
            "attribution_kind": "cta",
            "report_time_basis": "third-party-event-date",
        },
    }[platform]


def _next_day(value: object) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ContractError("fictional daily row has an invalid date")
    return f"{value[:8]}{int(value[8:]) + 1:02d}"


def _raw_header(
    *,
    platform: str,
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": f"fictional-{platform}-daily-aggregates-v1",
        "evidence_origin": "synthetic_fixture_only",
        "source_binding": _source_binding(manifest, family),
        "reporting_context": {
            "account_id": f"fictional-{platform}-account",
            "currency": "USD",
            "timezone": "UTC",
            "reporting_basis": "daily-finalized",
            "grouping_keys": list(_GROUPING_KEYS),
            "grouping_semantics": "mutually-exclusive-randomized-blocks",
            "breakdown_overlap_permitted": False,
            "omitted_zero_policy": "explicit-metric-state",
        },
        "state_markers": _state_markers(platform),
    }


def _metric_reporting_state(
    *,
    metric: str,
    observed_value: int | float,
    row_index: int,
) -> dict[str, object]:
    state = _metric_state(row_index)
    return {
        "metric": metric,
        "state": state,
        "value": _reported_metric_value(observed_value, row_index),
    }


def _metric_state(row_index: int) -> str:
    return (
        "observed",
        "zero",
        "missing",
        "suppressed",
        "omitted-zero",
    )[row_index % 5]


def _reported_metric_value(
    observed_value: int | float,
    row_index: int,
) -> int | float | None:
    state = _metric_state(row_index)
    return observed_value if state == "observed" else 0 if state == "zero" else None


def _meta_document(
    rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    document = _raw_header(platform="meta", manifest=manifest, family=family)
    document["reach_aggregation"] = "non-additive"
    platform_rows = [row for row in rows if row["platform"] == "meta"]
    document["rows"] = [
        {
            "date": row["date"],
            "experiment_id": row["experiment_id"],
            "campaign_id": row["campaign_id"],
            "batch_id": row["batch_id"],
            "block_id": row["block_id"],
            "segment_id": row["segment_id"],
            "creative_id": row["creative_id"],
            "impressions": row["impressions"],
            "spend": row["spend_minor"],
            "reach": max(0, int(row["impressions"]) - 37),
            "clicks": row["clicks"],
            "outbound_clicks": _reported_metric_value(
                max(0, int(row["clicks"]) - 3),
                index,
            ),
            "other_clicks": min(3, int(row["clicks"])),
            "actions": [
                {
                    "action_type": "lead",
                    "report_time": "conversion",
                    "value": row["conversions"],
                },
                {
                    "action_type": "landing_page_view",
                    "report_time": "interaction",
                    "value": row["clicks"],
                },
            ],
            "action_values": [
                {
                    "action_type": "lead",
                    "report_time": "conversion",
                    "value": round(float(row["conversions"]) * 125.5, 2),
                }
            ],
            "action_report_time": "conversion",
            "attribution_setting": {
                "click_window": "7-day",
                "report_time": "conversion",
                "view_window": "1-day",
            },
            "video_p25": row["video_p25"],
            "video_p50": row["video_p50"],
            "video_p75": row["video_p75"],
            "video_p100": row["video_p100"],
            "row_state": _metric_state(index),
            "metric_reporting_state": _metric_reporting_state(
                metric="outbound_clicks",
                observed_value=int(row["clicks"]) - min(3, int(row["clicks"])),
                row_index=index,
            ),
        }
        for index, row in enumerate(platform_rows)
    ]
    return document


def _google_document(
    rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    document = _raw_header(platform="google", manifest=manifest, family=family)
    document["conversion_date_semantics"] = "interaction-and-conversion-date-preserved"
    platform_rows = [row for row in rows if row["platform"] == "google"]
    document["rows"] = [
        {
            "date": row["date"],
            "interaction_date": row["date"],
            "conversion_date": _next_day(row["date"]),
            "experiment_id": row["experiment_id"],
            "campaign_id": row["campaign_id"],
            "batch_id": row["batch_id"],
            "block_id": row["block_id"],
            "segment_id": row["segment_id"],
            "creative_id": row["creative_id"],
            "impressions": row["impressions"],
            "currency_code": "USD",
            "cost_local": row["spend_minor"],
            "cost_micros": int(round(float(row["spend_minor"]) * 1_000_000)),
            "clicks": row["clicks"],
            "interactions": int(row["clicks"]) + 2,
            "conversions": _reported_metric_value(
                float(row["conversions"]) + 0.25,
                index,
            ),
            "all_conversions": float(row["conversions"]) + 0.75,
            "conversion_value": round(float(row["conversions"]) * 125.5, 2),
            "attribution_model": "data-driven",
            "data_status": "modeled_and_observed",
            "row_state": _metric_state(index),
            "metric_reporting_state": _metric_reporting_state(
                metric="conversions",
                observed_value=float(row["conversions"]) + 0.25,
                row_index=index,
            ),
        }
        for index, row in enumerate(platform_rows)
    ]
    return document


def _linkedin_document(
    rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    document = _raw_header(platform="linkedin", manifest=manifest, family=family)
    document["suppression_policy"] = "preserve-without-imputation"
    platform_rows = [row for row in rows if row["platform"] == "linkedin"]
    document["rows"] = [
        {
            "date": row["date"],
            "experiment_id": row["experiment_id"],
            "campaign_id": row["campaign_id"],
            "batch_id": row["batch_id"],
            "block_id": row["block_id"],
            "segment_id": row["segment_id"],
            "creative_id": row["creative_id"],
            "impressions": row["impressions"],
            "spend": row["spend_minor"],
            "cost_local": row["spend_minor"],
            "cost_usd": row["spend_minor"],
            "chargeable_clicks": row["clicks"],
            "landing_page_clicks": max(0, int(row["clicks"]) - 2),
            "sends": int(row["impressions"]) + 11,
            "post_click_conversions": row["conversions"],
            "post_view_conversions": round(float(row["conversions"]) * 0.2, 2),
            "total_conversions": _reported_metric_value(
                round(float(row["conversions"]) * 1.2, 2),
                index,
            ),
            "advertiser_conversion_value": round(
                float(row["conversions"]) * 125.5,
                2,
            ),
            "leads": row["conversions"],
            "job_views": max(0, int(row["clicks"]) - 1),
            "job_applications": max(0, int(row["conversions"]) - 1),
            "application_starts": row["conversions"],
            "attribution_model": "last-touch",
            "estimation_status": (
                "estimated" if index % 2 else "observed_not_estimated"
            ),
            "reporting_delay_days": 2,
            "suppression_status": (
                "suppressed-low-volume"
                if _metric_state(index) == "suppressed"
                else "not-suppressed"
            ),
            "row_state": _metric_state(index),
            "metric_reporting_state": _metric_reporting_state(
                metric="total_conversions",
                observed_value=round(float(row["conversions"]) * 1.2, 2),
                row_index=index,
            ),
        }
        for index, row in enumerate(platform_rows)
    ]
    return document


def _tiktok_document(
    rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> dict[str, object]:
    document = _raw_header(platform="tiktok", manifest=manifest, family=family)
    document["attribution_windows"] = {
        "cta": "7-day",
        "vta": "1-day",
        "evta": "1-day-engaged-view",
    }
    platform_rows = [row for row in rows if row["platform"] == "tiktok"]
    document["rows"] = [
        {
            "date": row["date"],
            "interaction_date": row["date"],
            "third_party_event_date": _next_day(row["date"]),
            "experiment_id": row["experiment_id"],
            "campaign_id": row["campaign_id"],
            "batch_id": row["batch_id"],
            "block_id": row["block_id"],
            "segment_id": row["segment_id"],
            "creative_id": row["creative_id"],
            "impressions": row["impressions"],
            "spend": row["spend_minor"],
            "clicks_all": row["clicks"],
            "destination_clicks": max(0, int(row["clicks"]) - 4),
            "cta_conversions": _reported_metric_value(
                row["conversions"],
                index,
            ),
            "vta_conversions": round(float(row["conversions"]) * 0.15, 2),
            "evta_conversions": round(float(row["conversions"]) * 0.05, 2),
            "cvr_all_clicks_denominator": row["clicks"],
            "cvr_all_clicks": (
                _reported_metric_value(
                    round(float(row["conversions"]) / int(row["clicks"]), 8),
                    index,
                )
                if int(row["clicks"])
                else 0.0
            ),
            "cvr_impressions_denominator": row["impressions"],
            "cvr_impressions": _reported_metric_value(
                round(
                    float(row["conversions"]) / int(row["impressions"]),
                    8,
                ),
                index,
            ),
            "cvr_destination_clicks_denominator": max(
                0,
                int(row["clicks"]) - 4,
            ),
            "cvr_destination_clicks": (
                _reported_metric_value(
                    round(
                        float(row["conversions"])
                        / max(0, int(row["clicks"]) - 4),
                        8,
                    ),
                    index,
                )
                if max(0, int(row["clicks"]) - 4)
                else 0.0
            ),
            "video_watched_2s": max(int(row["video_p25"]), int(row["video_p50"])),
            "video_watched_6s": row["video_p50"],
            "video_p25": row["video_p25"],
            "video_p50": row["video_p50"],
            "video_p75": row["video_p75"],
            "video_p100": row["video_p100"],
            "row_state": _metric_state(index),
            "metric_reporting_state": _metric_reporting_state(
                metric="cta_conversions",
                observed_value=row["conversions"],
                row_index=index,
            ),
        }
        for index, row in enumerate(platform_rows)
    ]
    return document


def _observation(
    *,
    manifest: Mapping[str, object],
    visible_key: str,
    row: Mapping[str, object],
    index: int,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "persona-behavior-outcome-observation-v1",
        "observation_id": f"{visible_key}-observation-{index + 1:03d}",
        "evidence_origin": "synthetic_fixture_only",
        "synthetic_study_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "source": {"platform": row["platform"]},
        "reporting_context": {
            "timezone": "UTC",
            "currency": "USD",
            "report_time_basis": _report_time_basis(str(row["platform"])),
            "maturity": "finalized",
        },
        "entity_identity": {"account": f"fictional-{row['platform']}-account"},
        "experiment_binding": {
            "experiment": row["experiment_id"],
            "campaign": row["campaign_id"],
            "block": row["block_id"],
            "batch": row["batch_id"],
            "arm": row["creative_id"],
            "reference_arm": "strategic-control",
        },
        "creative_binding": {"creative": row["creative_id"]},
        "creative_attribute_binding": {
            "registry": "fictional-creative-attribute-registry",
            "hypothesis": "quantified-payback-proof-need",
        },
        "audience_scope": {
            "segment": row["segment_id"],
            "objective": "lead-generation",
            "placement": f"{row['platform']}-feed",
        },
        "delivery": {"impressions": row["impressions"]},
        "traffic": {"clicks": row["clicks"]},
        "outcome_events": {"conversions": row["conversions"]},
        "measurement_definition": {
            "metric": "finalized-lead-rate",
            "registered_numerator": "finalized-leads",
            "registered_denominator": "impressions",
            "attribution_click_window": _attribution(str(row["platform"]))[
                "click_window"
            ],
            "attribution_view_window": _attribution(str(row["platform"]))[
                "view_window"
            ],
            "attribution_engaged_view_window": _attribution(
                str(row["platform"])
            )["engaged_view_window"],
            "attribution_model": _attribution(str(row["platform"]))["model"],
        },
        "denominators": {"kind": "impressions"},
        "completeness": {"status": "finalized"},
        "design_quality": {"design": "randomized"},
        "limitations": ["fictional-aggregate-only"],
        "observation_sha256": None,
    }
    document["observation_sha256"] = sha256_json(document)
    return _validate_trusted_generator_observation_v1(document)


def _closed(value: object, keys: set[str], path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    if set(value) != keys:
        raise ContractError(f"{path} does not match its closed schema")
    return value


def _validate_experiment_design(value: object) -> dict[str, object]:
    document = dict(
        _closed(
            value,
            {
                "schema_version",
                "design_id",
                "study_manifest_binding",
                "scenario_binding",
                "creative_attribute_registry_binding",
                "behavioral_hypothesis",
                "analytical_cells",
                "design_sha256",
            },
            "experiment_design",
        )
    )
    if document["schema_version"] != "fictional-randomized-block-design-v1":
        raise ContractError("experiment_design.schema_version is invalid")
    _closed(
        document["study_manifest_binding"],
        {"study_id", "study_manifest_sha256"},
        "experiment_design.study_manifest_binding",
    )
    _closed(
        document["scenario_binding"],
        {
            "study_id",
            "study_manifest_sha256",
            "scenario_id",
            "dgp_id",
            "dgp_version",
            "seed",
            "repetitions",
            "parameters_sha256",
        },
        "experiment_design.scenario_binding",
    )
    _closed(
        document["creative_attribute_registry_binding"],
        {"registry_id", "registry_version"},
        "experiment_design.creative_attribute_registry_binding",
    )
    _closed(
        document["behavioral_hypothesis"],
        {
            "hypothesis_id",
            "target_persona_id",
            "target_field",
            "informative_attribute_id",
            "informative_attribute_value",
            "contrast_direction",
            "predeclared",
        },
        "experiment_design.behavioral_hypothesis",
    )
    if document["behavioral_hypothesis"]["informative_attribute_value"] is not True:
        raise ContractError(
            "experiment_design informative_attribute_value must be true"
        )
    cells = document["analytical_cells"]
    if not isinstance(cells, list) or len(cells) != 16:
        raise ContractError("experiment_design must contain exactly 16 analytical cells")
    identities: list[tuple[str, str, str]] = []
    for index, raw_cell in enumerate(cells):
        path = f"experiment_design.analytical_cells[{index}]"
        cell = _closed(
            raw_cell,
            {
                "experiment_id",
                "campaign_id",
                "platform",
                "segment_id",
                "objective",
                "placement",
                "attribution",
                "reporting",
                "arms",
                "randomization",
                "estimand",
            },
            path,
        )
        _closed(
            cell["attribution"],
            {"click_window", "view_window", "engaged_view_window", "model"},
            f"{path}.attribution",
        )
        _closed(
            cell["reporting"],
            {"report_time_basis", "maturity", "timezone", "currency"},
            f"{path}.reporting",
        )
        arms = cell["arms"]
        if not isinstance(arms, list) or len(arms) != 4:
            raise ContractError(f"{path}.arms must preserve the four creative arms")
        for arm_index, arm in enumerate(arms):
            _closed(
                arm,
                {"arm_id", "creative_id", "role"},
                f"{path}.arms[{arm_index}]",
            )
        randomization = _closed(
            cell["randomization"],
            {"mechanism", "block_ids", "batch_ids"},
            f"{path}.randomization",
        )
        if (
            not isinstance(randomization["block_ids"], list)
            or len(randomization["block_ids"]) < 6
            or not isinstance(randomization["batch_ids"], list)
            or len(randomization["batch_ids"]) < 3
        ):
            raise ContractError(
                f"{path}.randomization must bind six blocks and three batches"
            )
        estimand = _closed(
            cell["estimand"],
            {
                "estimand_id",
                "registered_numerator",
                "registered_numerator_event",
                "registered_denominator",
                "registered_direction",
                "reference_arm_id",
                "treatment_arm_ids",
                "contrast_direction",
            },
            f"{path}.estimand",
        )
        _closed(
            estimand["registered_numerator_event"],
            {
                "metric_id",
                "event_kind",
                "attribution_kind",
                "report_time_basis",
            },
            f"{path}.estimand.registered_numerator_event",
        )
        if estimand["registered_direction"] != "higher_is_better":
            raise ContractError(f"{path}.estimand.registered_direction is invalid")
        identities.append(
            (
                str(cell["experiment_id"]),
                str(cell["platform"]),
                str(cell["segment_id"]),
            )
        )
    if len(identities) != len(set(identities)):
        raise ContractError("experiment_design analytical identities must be unique")
    supplied = document["design_sha256"]
    candidate = deepcopy(document)
    candidate["design_sha256"] = None
    if supplied != sha256_json(candidate):
        raise ContractError("experiment_design.design_sha256 is stale")
    return document


def _experiment_design(
    *,
    manifest: Mapping[str, object],
    family: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    cells = []
    for experiment_number in (1, 2):
        experiment_id = f"fictional-experiment-{experiment_number}"
        for platform in _PLATFORMS:
            for segment_id in ("cfo", "operations"):
                selected = [
                    row
                    for row in rows
                    if row["experiment_id"] == experiment_id
                    and row["platform"] == platform
                    and row["segment_id"] == segment_id
                ]
                block_ids = sorted({str(row["block_id"]) for row in selected})
                batch_ids = sorted({str(row["batch_id"]) for row in selected})
                campaign_ids = {str(row["campaign_id"]) for row in selected}
                if len(campaign_ids) != 1:
                    raise ContractError("one analytical cell must bind one campaign")
                cells.append(
                    {
                        "experiment_id": experiment_id,
                        "campaign_id": campaign_ids.pop(),
                        "platform": platform,
                        "segment_id": segment_id,
                        "objective": "lead-generation",
                        "placement": f"{platform}-feed",
                        "attribution": _registered_attribution(platform),
                        "reporting": {
                            "report_time_basis": "platform-specific-explicit",
                            "maturity": "finalized",
                            "timezone": "UTC",
                            "currency": "USD",
                        },
                        "arms": [
                            {
                                "arm_id": creative["creative_id"],
                                "creative_id": creative["creative_id"],
                                "role": (
                                    "reference"
                                    if creative["creative_id"] == "strategic-control"
                                    else "treatment"
                                ),
                            }
                            for creative in _CREATIVES
                        ],
                        "randomization": {
                            "mechanism": "seeded-complete-randomization-within-block",
                            "block_ids": block_ids,
                            "batch_ids": batch_ids,
                        },
                        "estimand": {
                            "estimand_id": (
                                "cfo-quantified-payback-rate-contrast"
                                if segment_id == "cfo"
                                else "operations-creative-rate-contrast"
                            ),
                            "registered_numerator": "finalized-leads",
                            "registered_numerator_event":
                                _registered_numerator_event(platform),
                            "registered_denominator": "impressions",
                            "registered_direction": "higher_is_better",
                            "reference_arm_id": "strategic-control",
                            "treatment_arm_ids": [
                                "ease-of-use",
                                "peer-validation",
                                "quantified-payback",
                            ],
                            "contrast_direction": (
                                "treatment_minus_reference_positive"
                            ),
                        },
                    }
                )
    document: dict[str, object] = {
        "schema_version": "fictional-randomized-block-design-v1",
        "design_id": f"{family['scenario_id']}-design",
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "scenario_binding": _source_binding(manifest, family),
        "creative_attribute_registry_binding": {
            "registry_id": "fictional-creative-attribute-registry",
            "registry_version": "1.0.0",
        },
        "behavioral_hypothesis": {
            "hypothesis_id": "quantified-payback-proof-need",
            "target_persona_id": "finance-pricing-archetype",
            "target_field": "proof_needs",
            "informative_attribute_id": "quantified-payback-proof",
            "informative_attribute_value": True,
            "contrast_direction": "treatment_minus_reference_positive",
            "predeclared": True,
        },
        "analytical_cells": cells,
        "design_sha256": None,
    }
    document["design_sha256"] = sha256_json(document)
    return _validate_experiment_design(document)


def _oracle(
    *,
    manifest: Mapping[str, object],
    family: Mapping[str, object],
    physical_safe_actions: list[str],
    mechanism: str,
    effect: float,
    true_miss: bool,
    identification_status: str,
    expected_engine_action: str,
) -> dict[str, object]:
    physical_operation = (
        deepcopy(_EXPECTED_PROFILE_OPERATION) if true_miss else None
    )
    expected_operation = (
        deepcopy(_EXPECTED_PROFILE_OPERATION)
        if expected_engine_action == "profile_snapshot_update"
        else None
    )
    document: dict[str, object] = {
        "schema_version": ORACLE_VERSION,
        "oracle_id": f"{family['scenario_id']}-oracle",
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "scenario_id": family["scenario_id"],
        "repetition": 0,
        "physical_truth": {
            "true_behavioral_miss": (
                {
                    "target_persona_id": "finance-pricing-archetype",
                    "target_field": "proof_needs",
                }
                if true_miss
                else None
            ),
            "safe_action_set": physical_safe_actions,
            "true_operation": physical_operation,
        },
        "epistemic_truth": {
            "identification_status": identification_status,
            "expected_engine_action": expected_engine_action,
            "expected_operation": expected_operation,
        },
        "failure_mechanism": {"kind": mechanism},
        "counterfactual_values": {"effect": effect},
        "oracle_sha256": None,
    }
    document["oracle_sha256"] = sha256_json(document)
    return validate_oracle(document)


def _build_null_effect(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = _scenario_rows(
        manifest=manifest,
        family=family,
        visible_kind="null-effect",
    )
    return rows, _oracle(
        manifest=manifest,
        family=family,
        physical_safe_actions=["no_change"],
        mechanism="null-effect",
        effect=0.0,
        true_miss=False,
        identification_status="no_miss",
        expected_engine_action="no_change",
    )


def _build_known_proof_need_miss(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = _scenario_rows(
        manifest=manifest,
        family=family,
        visible_kind="known-proof-need-miss",
    )
    return rows, _oracle(
        manifest=manifest,
        family=family,
        physical_safe_actions=["profile_snapshot_update"],
        mechanism="cfo-proof-need-omission",
        effect=float(_parameter_values(family)["visible-effect-rate"]),
        true_miss=True,
        identification_status="identified",
        expected_engine_action="profile_snapshot_update",
    )


def _build_non_identifiable_twin_a(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = _scenario_rows(
        manifest=manifest,
        family=family,
        visible_kind="non-identifiable-twin",
    )
    return rows, _oracle(
        manifest=manifest,
        family=family,
        physical_safe_actions=["profile_snapshot_update"],
        mechanism="latent-cfo-proof-need",
        effect=float(_parameter_values(family)["visible-effect-rate"]),
        true_miss=True,
        identification_status="non_identifiable",
        expected_engine_action="abstain",
    )


def _build_non_identifiable_twin_b(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = _scenario_rows(
        manifest=manifest,
        family=family,
        visible_kind="non-identifiable-twin",
    )
    return rows, _oracle(
        manifest=manifest,
        family=family,
        physical_safe_actions=["no_change"],
        mechanism="unobserved-delivery-confounding",
        effect=0.0,
        true_miss=False,
        identification_status="non_identifiable",
        expected_engine_action="abstain",
    )


def _build_adversarial_abstention(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build one distinct stress-family fixture with no executable conclusion."""

    scenario_id = str(family["scenario_id"])
    rows = _scenario_rows(
        manifest=manifest,
        family=family,
        visible_kind="null-effect",
    )
    return rows, _oracle(
        manifest=manifest,
        family=family,
        physical_safe_actions=["no_change"],
        mechanism=scenario_id,
        effect=0.0,
        true_miss=False,
        identification_status="non_identifiable",
        expected_engine_action="abstain",
    )


SCENARIO_BUILDERS = {
    "null-effect": _build_null_effect,
    "known-proof-need-miss": _build_known_proof_need_miss,
    "non-identifiable-twin-a": _build_non_identifiable_twin_a,
    "non-identifiable-twin-b": _build_non_identifiable_twin_b,
    **{
        scenario_id: _build_adversarial_abstention
        for scenario_id in SYNTHETIC_SCENARIO_REGISTRY
        if scenario_id
        not in {
            "null-effect",
            "known-proof-need-miss",
            "non-identifiable-twin-a",
            "non-identifiable-twin-b",
        }
    },
}


def _file_binding(path: str, raw: bytes) -> dict[str, object]:
    return {
        "path": path,
        "byte_count": len(raw),
        "raw_bytes_sha256": _digest_bytes(raw),
    }


def _materialize(
    manifest: Mapping[str, object],
    family: Mapping[str, object],
) -> _ScenarioMaterial:
    scenario_id = str(family["scenario_id"])
    builder = SCENARIO_BUILDERS.get(scenario_id)
    if builder is None:
        raise ContractError("scenario_id is not registered")
    rows, oracle = builder(manifest, family)
    visible_key = _public_scenario_key(scenario_id)
    observations = [
        _observation(
            manifest=manifest,
            visible_key=visible_key,
            row=row,
            index=index,
        )
        for index, row in enumerate(rows)
    ]
    experiment_design = _experiment_design(
        manifest=manifest,
        family=family,
        rows=rows,
    )
    raw_documents = {
        "raw/meta/daily-aggregates.json": _meta_document(rows, manifest, family),
        "raw/google/daily-aggregates.json": _google_document(rows, manifest, family),
        "raw/linkedin/daily-aggregates.json": _linkedin_document(rows, manifest, family),
        "raw/tiktok/daily-aggregates.json": _tiktok_document(rows, manifest, family),
    }
    public_files: dict[str, bytes] = {
        "experiment-design.json": canonical_json_bytes(experiment_design),
        "canonical-observations.json": canonical_json_bytes(observations),
        **{
            path: canonical_json_bytes(document)
            for path, document in raw_documents.items()
        },
    }
    scenario_manifest: dict[str, object] = {
        "schema_version": "synthetic-persona-behavior-scenario-manifest-v1",
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "scenario_binding": _source_binding(manifest, family),
        "partition": family["partition"],
        "public_file_bindings": [
            _file_binding(path, public_files[path])
            for path in sorted(public_files)
        ],
        "manifest_sha256": None,
    }
    scenario_manifest["manifest_sha256"] = sha256_json(scenario_manifest)
    public_files["scenario-manifest.json"] = canonical_json_bytes(scenario_manifest)

    hidden_raw = canonical_json_bytes(oracle)
    oracle_manifest: dict[str, object] = {
        "schema_version": "synthetic-persona-behavior-oracle-manifest-v1",
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "scenario_id": scenario_id,
        "oracle_file_binding": _file_binding("hidden-oracle.json", hidden_raw),
        "manifest_sha256": None,
    }
    oracle_manifest["manifest_sha256"] = sha256_json(oracle_manifest)
    oracle_files = {
        "hidden-oracle.json": hidden_raw,
        "oracle-manifest.json": canonical_json_bytes(oracle_manifest),
    }
    return _ScenarioMaterial(
        public_files=public_files,
        oracle_files=oracle_files,
        public_manifest_sha256=str(scenario_manifest["manifest_sha256"]),
        oracle_manifest_sha256=str(oracle_manifest["manifest_sha256"]),
    )


def _canonical_absolute(path: Path) -> Path:
    absolute = path.absolute()
    for alias, target in _PROTECTED_SYSTEM_ALIASES.items():
        if alias.is_symlink() and (absolute == alias or alias in absolute.parents):
            return target.joinpath(*absolute.relative_to(alias).parts)
    return absolute


def _assert_no_symlink_ancestors(path: Path, label: str) -> None:
    absolute = _canonical_absolute(path)
    if ".." in absolute.parts:
        raise ContractError(f"{label} contains an unsafe parent traversal")
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            value = os.lstat(cursor)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ContractError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(value.st_mode):
            raise ContractError(f"{label} must not contain a symlink ancestor")
        if cursor != absolute and not stat.S_ISDIR(value.st_mode):
            raise ContractError(f"{label} has a non-directory ancestor")


def _open_directory_chain(
    path: Path,
    *,
    label: str,
    final_must_be_new: bool,
) -> int:
    absolute = _canonical_absolute(path)
    _assert_no_symlink_ancestors(absolute, label)
    descriptor = os.open(absolute.anchor, _DIR_FLAGS)
    try:
        for index, part in enumerate(absolute.parts[1:]):
            final = index == len(absolute.parts[1:]) - 1
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno not in {errno.ENOENT}:
                    raise ContractError(
                        f"{label} changed or contains an unsafe alias"
                    ) from exc
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                except OSError as create_exc:
                    raise ContractError(f"{label} could not be created safely") from create_exc
            else:
                if final and final_must_be_new:
                    os.close(child)
                    raise ContractError(f"{label} already exists: {absolute}")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_fd_bytes(
    parent_fd: int,
    name: str,
    raw: bytes,
    label: str,
) -> dict[str, object]:
    try:
        descriptor = os.open(
            name,
            _CREATE_FILE_FLAGS,
            0o600,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise ContractError(f"{label} could not be created safely") from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ContractError(f"{label} could not be written completely")
            view = view[written:]
        os.fsync(descriptor)
        written_identity = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        copied = bytearray()
        while len(copied) < len(raw):
            chunk = os.read(
                descriptor,
                min(1024 * 1024, len(raw) - len(copied)),
            )
            if not chunk:
                break
            copied.extend(chunk)
        final_identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final_identity.st_mode)
            or (written_identity.st_dev, written_identity.st_ino)
            != (final_identity.st_dev, final_identity.st_ino)
            or final_identity.st_size != len(raw)
            or bytes(copied) != raw
        ):
            raise ContractError(f"{label} changed during publication")
        return {
            "device": final_identity.st_dev,
            "inode": final_identity.st_ino,
            "byte_count": len(raw),
            "raw_bytes_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
    finally:
        os.close(descriptor)


def _assert_directory_path_identity(
    path: Path,
    expected: os.stat_result,
    label: str,
) -> None:
    try:
        lexical = os.lstat(path)
        resolved = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ContractError(f"{label} changed during publication") from exc
    if (
        stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISDIR(resolved.st_mode)
        or (resolved.st_dev, resolved.st_ino)
        != (expected.st_dev, expected.st_ino)
    ):
        raise ContractError(f"{label} changed during publication")


def _assert_file_path_identity(
    path: Path,
    receipt: Mapping[str, object],
    label: str,
) -> None:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise ContractError(f"{label} changed during publication") from exc
    if (
        stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or value.st_dev != receipt["device"]
        or value.st_ino != receipt["inode"]
        or value.st_size != receipt["byte_count"]
    ):
        raise ContractError(f"{label} changed during publication")


def publish_new_file_no_follow(
    path: Path,
    raw: bytes,
    label: str,
) -> dict[str, object]:
    target = _canonical_absolute(Path(path))
    _assert_no_symlink_ancestors(target, label)
    parent_fd = _open_directory_chain(
        target.parent,
        label=f"{label} parent",
        final_must_be_new=False,
    )
    try:
        parent_identity = os.fstat(parent_fd)
        receipt = _write_fd_bytes(parent_fd, target.name, raw, label)
        _assert_directory_path_identity(
            target.parent,
            parent_identity,
            f"{label} parent",
        )
        _assert_file_path_identity(target, receipt, label)
        _assert_directory_path_identity(
            target.parent,
            parent_identity,
            f"{label} parent",
        )
    finally:
        os.close(parent_fd)
    return {
        **receipt,
        "path": str(target),
    }


def preflight_new_path_no_follow(path: Path, label: str) -> Path:
    target = _canonical_absolute(Path(path))
    _assert_no_symlink_ancestors(target, label)
    if target.exists() or target.is_symlink():
        raise ContractError(f"{label} already exists: {target}")
    return target


def _preflight_roots(public: Path, oracle: Path) -> tuple[Path, Path]:
    roots = []
    for label, raw in (("public", public), ("oracle", oracle)):
        path = preflight_new_path_no_follow(
            raw,
            f"{label} output directory",
        )
        roots.append(path)
    public_path, oracle_path = roots
    public_resolved = public_path.resolve(strict=False)
    oracle_resolved = oracle_path.resolve(strict=False)
    if (
        public_resolved == oracle_resolved
        or public_resolved in oracle_resolved.parents
        or oracle_resolved in public_resolved.parents
    ):
        raise ContractError("public and oracle output roots must be disjoint")
    return public_path, oracle_path


def _publish_files(
    root_fd: int,
    files: Mapping[str, bytes],
    label: str,
) -> dict[str, dict[str, object]]:
    receipts: dict[str, dict[str, object]] = {}
    for relative in sorted(files):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ContractError(f"{label} contains an unsafe relative path")
        descriptor = os.dup(root_fd)
        try:
            for part in path.parts[:-1]:
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    if exc.errno != errno.ENOENT:
                        raise ContractError(
                            f"{label} directory changed or is unsafe"
                        ) from exc
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                        child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    except OSError as create_exc:
                        raise ContractError(
                            f"{label} directory could not be created safely"
                        ) from create_exc
                os.close(descriptor)
                descriptor = child
            receipts[relative] = _write_fd_bytes(
                descriptor,
                path.name,
                files[relative],
                f"{label} {relative}",
            )
        finally:
            os.close(descriptor)
    return receipts


def generate_and_publish_synthetic_scenario(
    *,
    manifest: dict[str, object],
    scenario_id: str,
    public_output_dir: Path,
    oracle_output_dir: Path,
) -> dict[str, object]:
    """Generate privately and publish disjoint public and hidden roots."""

    validated = validate_study_manifest(manifest)
    if validated["synthetic_response_adapter"]["source_sha256"] != _adapter_source_digest():
        raise ContractError("study manifest adapter source binding is stale")
    family = next(
        (
            item
            for item in validated["scenario_families"]
            if item["scenario_id"] == scenario_id
        ),
        None,
    )
    if family is None or scenario_id not in SCENARIO_BUILDERS:
        raise ContractError("scenario_id is not frozen in the study manifest")
    if family["dgp_id"] != _EXPECTED_DGPS[scenario_id]:
        raise ContractError("scenario DGP binding does not match the closed registry")
    public_root, oracle_root = _preflight_roots(
        Path(public_output_dir),
        Path(oracle_output_dir),
    )
    material = _materialize(validated, family)
    public_fd = _open_directory_chain(
        public_root,
        label="public output directory",
        final_must_be_new=True,
    )
    try:
        oracle_fd = _open_directory_chain(
            oracle_root,
            label="oracle output directory",
            final_must_be_new=True,
        )
    except BaseException:
        os.close(public_fd)
        raise
    try:
        public_identity = os.fstat(public_fd)
        oracle_identity = os.fstat(oracle_fd)
        if (
            public_identity.st_dev,
            public_identity.st_ino,
        ) == (
            oracle_identity.st_dev,
            oracle_identity.st_ino,
        ):
            raise ContractError("created public and oracle roots alias one inode")
        _assert_directory_path_identity(
            public_root,
            public_identity,
            "public output directory",
        )
        _assert_directory_path_identity(
            oracle_root,
            oracle_identity,
            "oracle output directory",
        )
        public_after = public_root.resolve(strict=True)
        oracle_after = oracle_root.resolve(strict=True)
        if (
            public_after == oracle_after
            or public_after in oracle_after.parents
            or oracle_after in public_after.parents
        ):
            raise ContractError(
                "created public and oracle output roots are not disjoint"
            )
        _publish_files(public_fd, material.public_files, "public scenario output")
        _publish_files(oracle_fd, material.oracle_files, "oracle scenario output")
        _assert_directory_path_identity(
            public_root,
            public_identity,
            "public output directory",
        )
        _assert_directory_path_identity(
            oracle_root,
            oracle_identity,
            "oracle output directory",
        )
        _assert_directory_path_identity(
            public_root,
            public_identity,
            "public output directory",
        )
        _assert_directory_path_identity(
            oracle_root,
            oracle_identity,
            "oracle output directory",
        )
    finally:
        os.close(public_fd)
        os.close(oracle_fd)
    return {
        "public_manifest_sha256": material.public_manifest_sha256,
        "public_output_dir": str(public_root),
        "public_output_identity_sha256": "sha256:"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "device": public_identity.st_dev,
                    "inode": public_identity.st_ino,
                }
            )
        ).hexdigest(),
        "oracle_manifest_sha256": material.oracle_manifest_sha256,
        "oracle_output_dir": str(oracle_root),
        "oracle_output_identity_sha256": "sha256:"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "device": oracle_identity.st_dev,
                    "inode": oracle_identity.st_ino,
                }
            )
        ).hexdigest(),
    }


__all__ = [
    "GENERATOR_VERSION",
    "SCENARIO_BUILDERS",
    "SYNTHETIC_RESPONSE_ADAPTER_SOURCE",
    "build_study_manifest",
    "generate_and_publish_synthetic_scenario",
    "preflight_new_path_no_follow",
    "publish_new_file_no_follow",
]
