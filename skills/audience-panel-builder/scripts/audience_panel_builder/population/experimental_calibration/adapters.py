"""Closed adapters for the four fictional synthetic platform exports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import hashlib
import json
import math

from ...common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    sha256_json,
)
from .contracts import (
    OUTCOME_OBSERVATION_VERSION,
    validate_creative_attribute_registry,
    validate_outcome_observation,
    validate_study_manifest,
)


SUPPORTED_PLATFORMS = frozenset({"meta", "google", "linkedin", "tiktok"})
_GROUPING_KEYS = [
    "batch_id",
    "block_id",
    "campaign_id",
    "creative_id",
    "date",
    "experiment_id",
    "segment_id",
]
_ROW_STATES = {"observed", "zero", "missing", "suppressed", "omitted-zero"}
_RAW_TOP_COMMON = {
    "schema_version",
    "evidence_origin",
    "source_binding",
    "reporting_context",
    "state_markers",
    "rows",
}
_COMMON_ROW_KEYS = {
    "batch_id",
    "block_id",
    "campaign_id",
    "creative_id",
    "date",
    "experiment_id",
    "impressions",
    "metric_reporting_state",
    "row_state",
    "segment_id",
}

_PLATFORM_TOP_KEYS = {
    "meta": _RAW_TOP_COMMON | {"reach_aggregation"},
    "google": _RAW_TOP_COMMON | {"conversion_date_semantics"},
    "linkedin": _RAW_TOP_COMMON | {"suppression_policy"},
    "tiktok": _RAW_TOP_COMMON | {"attribution_windows"},
}
_PLATFORM_ROW_KEYS = {
    "meta": _COMMON_ROW_KEYS
    | {
        "action_report_time",
        "action_values",
        "actions",
        "attribution_setting",
        "clicks",
        "other_clicks",
        "outbound_clicks",
        "reach",
        "spend",
        "video_p100",
        "video_p25",
        "video_p50",
        "video_p75",
    },
    "google": _COMMON_ROW_KEYS
    | {
        "all_conversions",
        "attribution_model",
        "clicks",
        "conversion_date",
        "conversion_value",
        "conversions",
        "cost_local",
        "cost_micros",
        "currency_code",
        "data_status",
        "interaction_date",
        "interactions",
    },
    "linkedin": _COMMON_ROW_KEYS
    | {
        "advertiser_conversion_value",
        "application_starts",
        "attribution_model",
        "chargeable_clicks",
        "cost_local",
        "cost_usd",
        "estimation_status",
        "job_applications",
        "job_views",
        "landing_page_clicks",
        "leads",
        "post_click_conversions",
        "post_view_conversions",
        "reporting_delay_days",
        "sends",
        "spend",
        "suppression_status",
        "total_conversions",
    },
    "tiktok": _COMMON_ROW_KEYS
    | {
        "clicks_all",
        "cta_conversions",
        "cvr_all_clicks",
        "cvr_all_clicks_denominator",
        "cvr_impressions",
        "cvr_impressions_denominator",
        "cvr_destination_clicks",
        "cvr_destination_clicks_denominator",
        "destination_clicks",
        "evta_conversions",
        "interaction_date",
        "spend",
        "third_party_event_date",
        "video_p100",
        "video_p25",
        "video_p50",
        "video_p75",
        "video_watched_2s",
        "video_watched_6s",
        "vta_conversions",
    },
}
_RAW_SCHEMA_VERSIONS = {
    "meta": "fictional-meta-daily-aggregates-v1",
    "google": "fictional-google-daily-aggregates-v1",
    "linkedin": "fictional-linkedin-daily-aggregates-v1",
    "tiktok": "fictional-tiktok-daily-aggregates-v1",
}
_APPROVED_PRIMARY_NATIVE_METRICS = {
    "meta": "outbound_clicks",
    "google": "conversions",
    "linkedin": "total_conversions",
    "tiktok": "cta_conversions",
}


def _closed(value: object, keys: set[str], path: str) -> dict[str, object]:
    checked = require_object(value, keys, path)
    return deepcopy(dict(checked))


def _number(
    value: object,
    path: str,
    *,
    nullable: bool = False,
    integer: bool = False,
) -> int | float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ContractError(f"{path} must be a finite non-negative number")
    if integer and type(value) is not int:
        raise ContractError(f"{path} must be an exact non-negative integer")
    return value


def _metric_id(value: object, path: str) -> str:
    text = require_string(value, path)
    if not all(part and part.replace("-", "").isalnum() for part in text.split("_")):
        raise ContractError(f"{path} must be a canonical metric name")
    return text


def _parse_verified_json_bytes(
    raw_export_bytes: bytes,
    source_sha256: str,
) -> dict[str, object]:
    if not isinstance(raw_export_bytes, bytes):
        raise ContractError("raw_export_bytes must be exact bytes")
    expected = "sha256:" + hashlib.sha256(raw_export_bytes).hexdigest()
    if source_sha256 != expected:
        raise ContractError("source_sha256 does not match exact raw_export_bytes")
    try:
        parsed = json.loads(raw_export_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("raw_export_bytes must contain one UTF-8 JSON object") from exc
    if not isinstance(parsed, Mapping):
        raise ContractError("raw export must be an object")
    return deepcopy(dict(parsed))


def _validate_source_binding(
    raw: Mapping[str, object],
    manifest: Mapping[str, object],
) -> str:
    binding = _closed(
        raw["source_binding"],
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
        "raw.source_binding",
    )
    if binding["study_id"] != manifest["study_id"]:
        raise ContractError("raw.source_binding.study_id does not match study manifest")
    if binding["study_manifest_sha256"] != manifest["manifest_sha256"]:
        raise ContractError("raw.source_binding.study_manifest_sha256 does not match study manifest")
    scenario_id = require_identifier(binding["scenario_id"], "raw.source_binding.scenario_id")
    family = next(
        (
            member
            for member in manifest["scenario_families"]
            if member["scenario_id"] == scenario_id
        ),
        None,
    )
    if family is None:
        raise ContractError("raw.source_binding.scenario_id is not in study manifest")
    expected = {
        "dgp_id": family["dgp_id"],
        "dgp_version": family["dgp_version"],
        "seed": family["seed"],
        "repetitions": family["repetitions"],
        "parameters_sha256": family["parameters"]["parameters_sha256"],
    }
    for key, expected_value in expected.items():
        if type(binding[key]) is not type(expected_value) or binding[key] != expected_value:
            raise ContractError(f"raw.source_binding.{key} does not match study manifest")
    return scenario_id


def _validate_raw_envelope(
    *,
    platform: str,
    raw: dict[str, object],
    manifest: Mapping[str, object],
) -> tuple[dict[str, object], list[object], str]:
    require_object(raw, _PLATFORM_TOP_KEYS[platform], "raw")
    require_enum(
        raw["schema_version"],
        {_RAW_SCHEMA_VERSIONS[platform]},
        "raw.schema_version",
    )
    require_enum(raw["evidence_origin"], {"synthetic_fixture_only"}, "raw.evidence_origin")
    scenario_id = _validate_source_binding(raw, manifest)
    context = _closed(
        raw["reporting_context"],
        {
            "account_id",
            "breakdown_overlap_permitted",
            "currency",
            "grouping_keys",
            "grouping_semantics",
            "omitted_zero_policy",
            "reporting_basis",
            "timezone",
        },
        "raw.reporting_context",
    )
    require_identifier(context["account_id"], "raw.reporting_context.account_id")
    if context["currency"] != "USD":
        raise ContractError("raw.reporting_context.currency must match the frozen USD study")
    if context["timezone"] != "UTC":
        raise ContractError("raw.reporting_context.timezone must match the frozen UTC study")
    if context["breakdown_overlap_permitted"] is not False:
        raise ContractError("raw reporting breakdown overlap is not permitted")
    require_enum(
        context["grouping_semantics"],
        {"mutually-exclusive-randomized-blocks"},
        "raw.reporting_context.grouping_semantics",
    )
    require_enum(
        context["omitted_zero_policy"],
        {"explicit-metric-state"},
        "raw.reporting_context.omitted_zero_policy",
    )
    require_enum(
        context["reporting_basis"],
        {"daily-finalized"},
        "raw.reporting_context.reporting_basis",
    )
    grouping_keys = require_string_array(
        context["grouping_keys"],
        "raw.reporting_context.grouping_keys",
        nonempty=True,
    )
    if grouping_keys != _GROUPING_KEYS:
        raise ContractError("raw.reporting_context.grouping_keys must match the frozen grouping identity")
    markers = require_array(raw["state_markers"], "raw.state_markers", nonempty=True)
    marker_states: list[str] = []
    for index, marker_raw in enumerate(markers):
        marker = _closed(
            marker_raw,
            {"marker_id", "meaning", "state"},
            f"raw.state_markers[{index}]",
        )
        require_identifier(marker["marker_id"], f"raw.state_markers[{index}].marker_id")
        require_string(marker["meaning"], f"raw.state_markers[{index}].meaning")
        marker_states.append(
            require_enum(marker["state"], _ROW_STATES, f"raw.state_markers[{index}].state")
        )
    if marker_states != ["zero", "missing", "suppressed", "omitted-zero"]:
        raise ContractError("raw.state_markers must preserve the four frozen non-observed states")
    rows = require_array(raw["rows"], "raw.rows", nonempty=True)
    return context, rows, scenario_id


def _validate_metric_state(
    row: Mapping[str, object],
    path: str,
    platform: str,
) -> tuple[str, str]:
    state = require_enum(row["row_state"], _ROW_STATES, f"{path}.row_state")
    metric_state = _closed(
        row["metric_reporting_state"],
        {"metric", "state", "value"},
        f"{path}.metric_reporting_state",
    )
    metric_id = _metric_id(metric_state["metric"], f"{path}.metric_reporting_state.metric")
    if metric_id != _APPROVED_PRIMARY_NATIVE_METRICS[platform]:
        raise ContractError(
            f"{path}.metric_reporting_state.metric must equal the approved "
            f"primary metric for {platform}"
        )
    reported_state = require_enum(
        metric_state["state"],
        _ROW_STATES,
        f"{path}.metric_reporting_state.state",
    )
    if state != reported_state:
        raise ContractError(f"{path}.row_state must match metric_reporting_state.state")
    value = metric_state["value"]
    if state == "zero" and value != 0:
        raise ContractError(f"{path}.metric_reporting_state.value must be zero for zero state")
    if state in {"missing", "suppressed", "omitted-zero"} and value is not None:
        raise ContractError(f"{path}.metric_reporting_state.value must be null for {state} state")
    if state == "observed":
        _number(value, f"{path}.metric_reporting_state.value")
    native_name = str(metric_state["metric"])
    if native_name not in row:
        raise ContractError(
            f"{path}.metric_reporting_state.metric has no native metric field"
        )
    if type(value) is not type(row[native_name]) or value != row[native_name]:
        raise ContractError(
            f"{path}.metric_reporting_state.value must equal the native metric"
        )
    return metric_id, state


def _base_row(
    *,
    platform: str,
    raw: Mapping[str, object],
    raw_row: object,
    row_index: int,
    context: Mapping[str, object],
    source_sha256: str,
    scenario_id: str,
    manifest: Mapping[str, object],
    registry: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], str]:
    del raw
    path = f"raw.rows[{row_index}]"
    row = _closed(raw_row, _PLATFORM_ROW_KEYS[platform], path)
    for key in _GROUPING_KEYS:
        if key == "date":
            require_string(row[key], f"{path}.{key}")
        else:
            require_identifier(row[key], f"{path}.{key}")
    _number(row["impressions"], f"{path}.impressions", integer=True)
    metric_id, state = _validate_metric_state(row, path, platform)
    creative_id = str(row["creative_id"])
    binding = next(
        (
            item
            for item in registry["creative_bindings"]
            if item["creative_id"] == creative_id
        ),
        None,
    )
    if binding is None:
        raise ContractError(f"{path}.creative_id is not bound in creative attribute registry")
    definitions = {
        str(definition["attribute_id"]): definition
        for definition in registry["attribute_definitions"]
    }
    creative_attributes = sorted(
        (
            {
                "attribute_id": attribute["attribute_id"],
                "attribute_version": attribute["attribute_version"],
                "method_id": attribute["method_id"],
                "value": deepcopy(attribute["value"]),
                "hypothesis_id": (
                    definitions[str(attribute["attribute_id"])][
                        "behavioral_hypothesis"
                    ]["hypothesis_id"]
                    if definitions[str(attribute["attribute_id"])][
                        "behavioral_hypothesis"
                    ]
                    is not None
                    else None
                ),
            }
            for attribute in registry["creative_attributes"]
            if attribute["creative_id"] == creative_id
        ),
        key=lambda item: str(item["attribute_id"]),
    )
    hypothesis_ids = sorted(
        str(attribute["hypothesis_id"])
        for attribute in creative_attributes
        if attribute["hypothesis_id"] is not None
    )
    grouping_text = "|".join(
        [
            str(manifest["manifest_sha256"]),
            scenario_id,
            source_sha256,
            platform,
        ]
        + [str(row[key]) for key in _GROUPING_KEYS]
    )
    grouping_digest = hashlib.sha256(grouping_text.encode("utf-8")).hexdigest()[:32]
    observation_id = f"{platform}-observation-{grouping_digest}"
    document: dict[str, object] = {
        "schema_version": OUTCOME_OBSERVATION_VERSION,
        "observation_id": observation_id,
        "evidence_origin": "synthetic_fixture_only",
        "synthetic_study_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "source": {
            "platform": platform,
            "source_sha256": source_sha256,
            "raw_schema_version": _RAW_SCHEMA_VERSIONS[platform],
            "source_scenario_id": scenario_id,
        },
        "reporting_context": {
            "account_id": context["account_id"],
            "timezone": context["timezone"],
            "currency": context["currency"],
            "date": row["date"],
            "grain": "daily",
            "report_time_basis": "platform-specific-explicit",
            "maturity": "finalized",
            "grouping_keys": sorted(_GROUPING_KEYS),
        },
        "entity_identity": {
            "account_id": context["account_id"],
            "campaign_id": row["campaign_id"],
            "ad_group_id": "not-applicable",
            "ad_id": "not-reported",
        },
        "experiment_binding": {
            "experiment_id": row["experiment_id"],
            "campaign_id": row["campaign_id"],
            "block_id": row["block_id"],
            "batch_id": row["batch_id"],
            "arm_id": creative_id,
            "reference_arm_id": "strategic-control",
        },
        "creative_binding": {
            "creative_id": creative_id,
            "asset_sha256": binding["asset_sha256"],
        },
        "creative_attribute_binding": {
            "registry_id": registry["registry_id"],
            "registry_sha256": registry["registry_sha256"],
            "hypothesis_ids": hypothesis_ids,
            "attributes": creative_attributes,
        },
        "audience_scope": {
            "segment_id": row["segment_id"],
            "objective": "lead-generation",
            "placement": f"{platform}-feed",
        },
        "delivery": {
            "impressions": row["impressions"],
            "spend": None,
            "spend_micros": None,
            "spend_local": None,
            "spend_usd": None,
            "reach": None,
            "reach_status": "not_reported",
            "sends": None,
            "sends_semantics": "not_applicable",
            "video_metrics": {
                "video_p25": None,
                "video_p50": None,
                "video_p75": None,
                "video_p100": None,
                "video_watched_2s": None,
                "video_watched_6s": None,
            },
        },
        "traffic": {
            "clicks_all": None,
            "outbound_clicks": None,
            "other_clicks": None,
            "interactions": None,
            "destination_clicks": None,
            "chargeable_clicks": None,
            "landing_page_clicks": None,
        },
        "outcome_events": [],
        "measurement_definition": {
            "primary_metric_id": metric_id,
            "data_status": "observed",
            "attribution_model": None,
            "click_window": None,
            "view_window": None,
            "engaged_view_window": None,
            "interaction_date": None,
            "conversion_date": None,
            "third_party_event_date": None,
            "action_report_time": None,
            "attribution_report_time": None,
            "reporting_delay_days": None,
            "rates": [],
        },
        "denominators": [
            {
                "metric_id": metric_id,
                "denominator_kind": "impressions",
                "denominator_value": row["impressions"],
            }
        ],
        "completeness": {
            "metric_state": state,
            "row_state": state,
            "suppression_status": "not-suppressed",
            "omitted_zero_policy": context["omitted_zero_policy"],
        },
        "design_quality": {
            "design": "randomized",
            "grouping_identity": f"{platform}-group-{grouping_digest}",
            "grouping_semantics": context["grouping_semantics"],
            "overlap_permitted": False,
        },
        "limitations": ["fictional-aggregate-only"],
        "observation_sha256": None,
    }
    return document, row, metric_id


def _event(
    metric_id: str,
    *,
    count: int | float | None = None,
    value: int | float | None = None,
    event_kind: str = "count",
    attribution_kind: str = "none",
    report_time_basis: str = "platform-specific",
    data_status: str = "observed",
) -> dict[str, object]:
    return {
        "metric_id": metric_id,
        "event_kind": event_kind,
        "count": count,
        "value": value,
        "attribution_kind": attribution_kind,
        "report_time_basis": report_time_basis,
        "data_status": data_status,
    }


def _validate_primary_projection(
    document: Mapping[str, object],
    raw_row: Mapping[str, object],
    path: str,
) -> None:
    """Prove the canonical primary event is the exact authenticated marker."""

    marker = raw_row["metric_reporting_state"]
    if not isinstance(marker, Mapping):
        raise ContractError(f"{path}.metric_reporting_state must be an object")
    metric_id = str(marker["metric"])
    primary_metric_id = document["measurement_definition"]["primary_metric_id"]
    if primary_metric_id != metric_id:
        raise ContractError(
            f"{path} emitted primary metric must equal the native marker"
        )
    matches = [
        event
        for event in document["outcome_events"]
        if event["metric_id"] == metric_id
    ]
    if len(matches) != 1:
        raise ContractError(
            f"{path} must emit exactly one native primary metric event"
        )
    event = matches[0]
    if (
        event["event_kind"] != "count"
        or event["value"] is not None
        or type(event["count"]) is not type(marker["value"])
        or event["count"] != marker["value"]
        or event["data_status"] != marker["state"]
    ):
        raise ContractError(
            f"{path} emitted primary event must exactly equal the native marker"
        )


def _normalize_meta(**kwargs: object) -> dict[str, object]:
    document, row, metric_id = _base_row(platform="meta", **kwargs)
    path = f"raw.rows[{kwargs['row_index']}]"
    if kwargs["raw"]["reach_aggregation"] != "non-additive":
        raise ContractError("raw.reach_aggregation must be non-additive")
    attribution = _closed(
        row["attribution_setting"],
        {"click_window", "view_window", "report_time"},
        f"{path}.attribution_setting",
    )
    action_report_time = require_string(
        row["action_report_time"],
        f"{path}.action_report_time",
    )
    attribution_report_time = require_string(
        attribution["report_time"],
        f"{path}.attribution_setting.report_time",
    )
    if attribution_report_time != action_report_time:
        raise ContractError(
            f"{path}.attribution_setting.report_time must match action_report_time"
        )
    actions = require_array(row["actions"], f"{path}.actions", nonempty=True)
    action_values = require_array(row["action_values"], f"{path}.action_values", nonempty=True)
    events: list[dict[str, object]] = []
    for name, values, event_kind in (
        ("actions", actions, "count"),
        ("action_values", action_values, "action_value"),
    ):
        for index, raw_action in enumerate(values):
            action = _closed(
                raw_action,
                {"action_type", "report_time", "value"},
                f"{path}.{name}[{index}]",
            )
            action_metric = _metric_id(
                action["action_type"],
                f"{path}.{name}[{index}].action_type",
            )
            if event_kind == "action_value":
                action_metric = f"{action_metric}-value"
            events.append(
                _event(
                    action_metric,
                    **({"count": _number(action["value"], f"{path}.{name}[{index}].value")} if event_kind == "count" else {"value": _number(action["value"], f"{path}.{name}[{index}].value")}),
                    event_kind=event_kind,
                    attribution_kind="aggregate",
                    report_time_basis=require_string(action["report_time"], f"{path}.{name}[{index}].report_time"),
                    data_status="observed",
                )
            )
    state = str(document["completeness"]["metric_state"])
    events.append(
        _event(
            metric_id,
            count=_number(
                row["outbound_clicks"],
                f"{path}.outbound_clicks",
                nullable=True,
                integer=True,
            ),
            data_status=state,
        )
    )
    document["delivery"].update(
        {
            "spend": _number(row["spend"], f"{path}.spend"),
            "spend_local": _number(row["spend"], f"{path}.spend"),
            "spend_usd": _number(row["spend"], f"{path}.spend"),
            "reach": _number(row["reach"], f"{path}.reach", integer=True),
            "reach_status": "non_additive_estimated",
            "video_metrics": {
                key: _number(row[key], f"{path}.{key}", integer=True)
                for key in ("video_p25", "video_p50", "video_p75", "video_p100")
            }
            | {"video_watched_2s": None, "video_watched_6s": None},
        }
    )
    document["traffic"].update(
        {
            "clicks_all": _number(row["clicks"], f"{path}.clicks", integer=True),
            "outbound_clicks": _number(row["outbound_clicks"], f"{path}.outbound_clicks", nullable=True, integer=True),
            "other_clicks": _number(row["other_clicks"], f"{path}.other_clicks", nullable=True, integer=True),
        }
    )
    document["outcome_events"] = events
    document["measurement_definition"].update(
        {
            "primary_metric_id": metric_id,
            "data_status": "observed",
            "attribution_model": None,
            "click_window": require_string(attribution["click_window"], f"{path}.attribution_setting.click_window"),
            "view_window": require_string(attribution["view_window"], f"{path}.attribution_setting.view_window"),
            "action_report_time": action_report_time,
            "attribution_report_time": attribution_report_time,
        }
    )
    return document


def _normalize_google(**kwargs: object) -> dict[str, object]:
    document, row, metric_id = _base_row(platform="google", **kwargs)
    path = f"raw.rows[{kwargs['row_index']}]"
    if kwargs["raw"]["conversion_date_semantics"] != "interaction-and-conversion-date-preserved":
        raise ContractError("raw.conversion_date_semantics is not supported")
    cost_micros = _number(row["cost_micros"], f"{path}.cost_micros", integer=True)
    cost_local = _number(row["cost_local"], f"{path}.cost_local")
    if cost_micros / 1_000_000 != cost_local:
        raise ContractError(f"{path}.cost_micros does not exactly match cost_local")
    if row["currency_code"] != kwargs["context"]["currency"]:
        raise ContractError(f"{path}.currency_code does not match reporting currency")
    state = str(document["completeness"]["metric_state"])
    document["delivery"].update(
        {
            "spend": cost_local,
            "spend_micros": cost_micros,
            "spend_local": cost_local,
            "spend_usd": cost_local,
        }
    )
    document["traffic"].update(
        {
            "clicks_all": _number(row["clicks"], f"{path}.clicks", integer=True),
            "interactions": _number(row["interactions"], f"{path}.interactions", integer=True),
        }
    )
    document["outcome_events"] = [
        _event("conversions", count=_number(row["conversions"], f"{path}.conversions", nullable=True), attribution_kind="aggregate", report_time_basis="conversion-date", data_status=state),
        _event("all_conversions", count=_number(row["all_conversions"], f"{path}.all_conversions", nullable=True), attribution_kind="aggregate", report_time_basis="conversion-date", data_status="observed"),
        _event("conversion_value", value=_number(row["conversion_value"], f"{path}.conversion_value", nullable=True), event_kind="action_value", attribution_kind="aggregate", report_time_basis="conversion-date", data_status="observed"),
    ]
    data_status = require_enum(
        row["data_status"],
        {"observed", "modeled_and_observed"},
        f"{path}.data_status",
    )
    document["measurement_definition"].update(
        {
            "primary_metric_id": metric_id,
            "data_status": data_status,
            "attribution_model": require_string(
                row["attribution_model"],
                f"{path}.attribution_model",
            ),
            "interaction_date": require_string(row["interaction_date"], f"{path}.interaction_date"),
            "conversion_date": require_string(row["conversion_date"], f"{path}.conversion_date"),
        }
    )
    return document


def _normalize_linkedin(**kwargs: object) -> dict[str, object]:
    document, row, metric_id = _base_row(platform="linkedin", **kwargs)
    path = f"raw.rows[{kwargs['row_index']}]"
    if kwargs["raw"]["suppression_policy"] != "preserve-without-imputation":
        raise ContractError("raw.suppression_policy is not supported")
    if row["cost_local"] != row["cost_usd"] or row["cost_local"] != row["spend"]:
        raise ContractError(f"{path} cost fields must agree in the frozen USD fixture")
    state = str(document["completeness"]["metric_state"])
    document["delivery"].update(
        {
            "spend": _number(row["spend"], f"{path}.spend"),
            "spend_local": _number(row["cost_local"], f"{path}.cost_local"),
            "spend_usd": _number(row["cost_usd"], f"{path}.cost_usd"),
            "sends": _number(row["sends"], f"{path}.sends", integer=True),
            "sends_semantics": "sponsored-messaging-delivery",
        }
    )
    document["traffic"].update(
        {
            "chargeable_clicks": _number(row["chargeable_clicks"], f"{path}.chargeable_clicks", integer=True),
            "landing_page_clicks": _number(row["landing_page_clicks"], f"{path}.landing_page_clicks", integer=True),
        }
    )
    count_metrics = (
        "total_conversions",
        "post_click_conversions",
        "post_view_conversions",
        "leads",
        "job_views",
        "job_applications",
        "application_starts",
    )
    document["outcome_events"] = [
        _event(
            name,
            count=_number(row[name], f"{path}.{name}", nullable=True),
            attribution_kind=(
                "post_click"
                if name == "post_click_conversions"
                else "post_view"
                if name == "post_view_conversions"
                else "aggregate"
            ),
            report_time_basis="conversion-date",
            data_status=(
                state
                if name == "total_conversions"
                else "estimated"
                if row["estimation_status"] == "estimated"
                else "observed"
            ),
        )
        for name in count_metrics
    ] + [
        _event(
            "advertiser-conversion-value",
            value=_number(row["advertiser_conversion_value"], f"{path}.advertiser_conversion_value", nullable=True),
            event_kind="action_value",
            attribution_kind="aggregate",
            report_time_basis="conversion-date",
            data_status=(
                "estimated"
                if row["estimation_status"] == "estimated"
                else "observed"
            ),
        )
    ]
    estimation = require_enum(
        row["estimation_status"],
        {"observed_not_estimated", "estimated"},
        f"{path}.estimation_status",
    )
    suppression = require_enum(
        row["suppression_status"],
        {"not-suppressed", "suppressed-low-volume"},
        f"{path}.suppression_status",
    )
    expected_suppression = (
        "suppressed-low-volume" if state == "suppressed" else "not-suppressed"
    )
    if suppression != expected_suppression:
        raise ContractError(
            f"{path}.suppression_status must match row_state"
        )
    document["completeness"]["suppression_status"] = suppression
    document["measurement_definition"].update(
        {
            "primary_metric_id": metric_id,
            "data_status": "estimated" if estimation == "estimated" else "observed",
            "attribution_model": require_string(
                row["attribution_model"],
                f"{path}.attribution_model",
            ),
            "reporting_delay_days": _number(
                row["reporting_delay_days"],
                f"{path}.reporting_delay_days",
                integer=True,
            ),
        }
    )
    return document


def _normalize_tiktok(**kwargs: object) -> dict[str, object]:
    document, row, metric_id = _base_row(platform="tiktok", **kwargs)
    path = f"raw.rows[{kwargs['row_index']}]"
    windows = _closed(
        kwargs["raw"]["attribution_windows"],
        {"cta", "vta", "evta"},
        "raw.attribution_windows",
    )
    state = str(document["completeness"]["metric_state"])
    document["delivery"].update(
        {
            "spend": _number(row["spend"], f"{path}.spend"),
            "spend_local": _number(row["spend"], f"{path}.spend"),
            "spend_usd": _number(row["spend"], f"{path}.spend"),
            "video_metrics": {
                key: _number(row[key], f"{path}.{key}", integer=True)
                for key in (
                    "video_p25",
                    "video_p50",
                    "video_p75",
                    "video_p100",
                    "video_watched_2s",
                    "video_watched_6s",
                )
            },
        }
    )
    clicks_all = _number(row["clicks_all"], f"{path}.clicks_all", integer=True)
    destination_clicks = _number(row["destination_clicks"], f"{path}.destination_clicks", integer=True)
    document["traffic"].update(
        {
            "clicks_all": clicks_all,
            "destination_clicks": destination_clicks,
        }
    )
    document["outcome_events"] = [
        _event(
            name,
            count=_number(row[name], f"{path}.{name}", nullable=True),
            attribution_kind=kind,
            report_time_basis="third-party-event-date",
            data_status=state if name == "cta_conversions" else "observed",
        )
        for name, kind in (
            ("cta_conversions", "cta"),
            ("vta_conversions", "vta"),
            ("evta_conversions", "evta"),
        )
    ]
    cvr_all_denominator = _number(
        row["cvr_all_clicks_denominator"],
        f"{path}.cvr_all_clicks_denominator",
        integer=True,
    )
    cvr_destination_denominator = _number(
        row["cvr_destination_clicks_denominator"],
        f"{path}.cvr_destination_clicks_denominator",
        integer=True,
    )
    if cvr_all_denominator != clicks_all:
        raise ContractError(f"{path}.cvr_all_clicks_denominator must equal clicks_all")
    if cvr_destination_denominator != destination_clicks:
        raise ContractError(f"{path}.cvr_destination_clicks_denominator must equal destination_clicks")
    cta_value = row["cta_conversions"]
    cvr_values = {
        "cvr-all-clicks": (
            row["cvr_all_clicks"],
            "clicks_all",
            cvr_all_denominator,
        ),
        "cvr-destination-click": (
            row["cvr_destination_clicks"],
            "destination_clicks",
            cvr_destination_denominator,
        ),
        "cvr-impression": (
            row["cvr_impressions"],
            "impressions",
            _number(
                row["cvr_impressions_denominator"],
                f"{path}.cvr_impressions_denominator",
                integer=True,
            ),
        ),
    }
    if cvr_values["cvr-impression"][2] != row["impressions"]:
        raise ContractError(
            f"{path}.cvr_impressions_denominator must equal impressions"
        )
    rates: list[dict[str, object]] = []
    for rate_id, (rate_value, denominator_kind, denominator_value) in sorted(
        cvr_values.items()
    ):
        if rate_value is not None:
            _number(rate_value, f"{path}.{rate_id}")
            expected_rate = (
                round(float(cta_value) / int(denominator_value), 8)
                if denominator_value and cta_value is not None
                else 0.0
            )
            if rate_value != expected_rate:
                raise ContractError(
                    f"{path}.{rate_id} must equal its native numerator and denominator"
                )
        rates.append(
            {
                "metric_id": rate_id,
                "rate_value": rate_value,
                "numerator_metric_id": "cta_conversions",
                "numerator_value": cta_value,
                "denominator_kind": denominator_kind,
                "denominator_value": denominator_value,
                "data_status": state,
            }
        )
    document["denominators"] = [
        {
            "metric_id": "cvr-all-clicks",
            "denominator_kind": "clicks_all",
            "denominator_value": cvr_all_denominator,
        },
        {
            "metric_id": "cvr-destination-click",
            "denominator_kind": "destination_clicks",
            "denominator_value": cvr_destination_denominator,
        },
        {
            "metric_id": "cvr-impression",
            "denominator_kind": "impressions",
            "denominator_value": row["impressions"],
        },
        {
            "metric_id": metric_id,
            "denominator_kind": "impressions",
            "denominator_value": row["impressions"],
        },
    ]
    document["measurement_definition"].update(
        {
            "primary_metric_id": metric_id,
            "data_status": "observed",
            "attribution_model": None,
            "click_window": require_string(windows["cta"], "raw.attribution_windows.cta"),
            "view_window": require_string(windows["vta"], "raw.attribution_windows.vta"),
            "engaged_view_window": require_string(windows["evta"], "raw.attribution_windows.evta"),
            "interaction_date": require_string(row["interaction_date"], f"{path}.interaction_date"),
            "third_party_event_date": require_string(row["third_party_event_date"], f"{path}.third_party_event_date"),
            "rates": rates,
        }
    )
    return document


_ADAPTERS: dict[str, Callable[..., dict[str, object]]] = {
    "meta": _normalize_meta,
    "google": _normalize_google,
    "linkedin": _normalize_linkedin,
    "tiktok": _normalize_tiktok,
}


def normalize_platform_export(
    *,
    platform: str,
    raw_export_bytes: bytes,
    source_sha256: str,
    study_manifest: dict[str, object],
    creative_attribute_registry: dict[str, object],
) -> list[dict[str, object]]:
    """Normalize one exact fictional export without collapsing native meanings."""

    platform = require_enum(platform, set(SUPPORTED_PLATFORMS), "platform")
    raw = _parse_verified_json_bytes(raw_export_bytes, source_sha256)
    manifest = validate_study_manifest(study_manifest)
    registry = validate_creative_attribute_registry(creative_attribute_registry)
    context, rows, scenario_id = _validate_raw_envelope(
        platform=platform,
        raw=raw,
        manifest=manifest,
    )
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for row_index, raw_row in enumerate(rows):
        document = _ADAPTERS[platform](
            raw=raw,
            raw_row=raw_row,
            row_index=row_index,
            context=context,
            source_sha256=source_sha256,
            scenario_id=scenario_id,
            manifest=manifest,
            registry=registry,
        )
        _validate_primary_projection(
            document,
            raw_row,
            f"raw.rows[{row_index}]",
        )
        observation_id = str(document["observation_id"])
        if observation_id in seen_ids:
            raise ContractError(
                f"duplicate observation grouping identity: {observation_id}"
            )
        seen_ids.add(observation_id)
        document["observation_sha256"] = sha256_json(document)
        normalized.append(validate_outcome_observation(document))
    return sorted(normalized, key=lambda row: str(row["observation_id"]))
