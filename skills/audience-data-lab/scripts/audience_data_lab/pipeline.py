"""Strict private-data audit, aggregation, modeling, and handoff validation."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
import html
import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from .common import (
    ContractError,
    canonical_json_bytes,
    create_new_directory,
    exact_object,
    require_boolean,
    require_enum,
    require_identifier,
    require_integer,
    require_nullable_string,
    require_number,
    require_string,
    require_string_list,
    require_timestamp,
    sha256_file,
    sha256_json,
)
from .modeling import build_feature_matrix, performance_model, segment_candidates
from .tabular import as_float, as_text, is_missing, load_rows


INTAKE_VERSION = "audience-private-data-intake-v1"
AUDIT_VERSION = "audience-private-data-audit-v1"
FIRST_PARTY_VERSION = "audience-first-party-evidence-v1"
PERFORMANCE_VERSION = "audience-performance-evidence-v1"

_INTAKE_KEYS = {
    "schema_version", "project_id", "created_at", "data_kind", "purpose",
    "covered_population", "time_window", "permission", "columns", "privacy",
    "analysis", "allowed_uses", "prohibited_uses", "retention",
}
_TIME_KEYS = {"start", "end", "timezone"}
_PERMISSION_KEYS = {
    "confirmed", "confirmed_by", "confirmed_at", "data_owner",
    "legal_or_contract_basis", "note",
}
_COLUMN_KEYS = {
    "entity_id", "direct_identifiers", "quasi_identifiers", "sensitive",
    "dimensions", "metrics", "outcome", "event_date", "ignored",
}
_PRIVACY_KEYS = {
    "minimum_cell_size", "release_mode", "privacy_budget_epsilon",
    "suppress_rare_values", "allow_synthetic_release",
}
_ANALYSIS_KEYS = {
    "generate_cross_tabs", "max_cross_tab_dimensions", "modeling_mode",
    "feature_columns", "cluster_counts", "model_seed", "minimum_model_rows",
    "temporal_holdout_fraction",
}
_RETENTION_KEYS = {
    "raw_input_action", "working_copy_action", "deadline", "approved_by",
}
_DATA_KINDS = {"crm", "customer", "sales", "product_usage", "performance"}
_RELEASE_MODES = {
    "aggregate_only", "k_anonymous", "differential_privacy", "synthetic_tabular",
}
_SUPPORTED_RELEASE_MODES = {"aggregate_only", "k_anonymous"}
_MODELING_MODES = {"none", "segment_candidates", "performance_prediction"}
_RETENTION_ACTIONS = {"retain_in_place", "return_to_owner", "delete_working_copy"}
_APPROVAL_KEYS = {
    "approved_for_downstream_use", "approved_by", "approved_at", "approval_note",
}
_APPROVAL_RECORD_KEYS = _APPROVAL_KEYS | {"schema_version"}
_AUDIT_KEYS = {
    "schema_version", "project_id", "generated_at", "input_sha256", "data_kind",
    "row_count", "entity_count", "column_inventory", "missingness", "privacy_risk",
    "analysis_readiness", "release_readiness", "retention", "decision", "reasons",
}
_FIRST_PARTY_KEYS = {
    "schema_version", "package_id", "created_at", "status", "source_audit_sha256",
    "input_sha256", "purpose", "covered_population", "time_window",
    "evidence_basis", "data_quality", "distributions", "cross_tabs",
    "segment_candidates", "privacy_assessment", "allowed_uses",
    "prohibited_uses", "limitations", "approval",
}
_PERFORMANCE_KEYS = {
    "schema_version", "package_id", "created_at", "status", "source_audit_sha256",
    "input_sha256", "purpose", "covered_population", "time_window",
    "evidence_basis", "outcome_definition", "data_quality", "cohort_results",
    "temporal_split", "model_results", "calibration_scope",
    "privacy_assessment", "allowed_uses", "prohibited_uses", "limitations",
    "approval",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_intake(payload: Any) -> dict[str, Any]:
    intake = exact_object(payload, _INTAKE_KEYS, "$")
    if intake["schema_version"] != INTAKE_VERSION:
        raise ContractError(f"$.schema_version must equal {INTAKE_VERSION}")
    require_identifier(intake["project_id"], "$.project_id")
    require_timestamp(intake["created_at"], "$.created_at")
    require_enum(intake["data_kind"], _DATA_KINDS, "$.data_kind")
    require_string(intake["purpose"], "$.purpose")
    require_string(intake["covered_population"], "$.covered_population")

    time_window = exact_object(intake["time_window"], _TIME_KEYS, "$.time_window")
    require_nullable_string(time_window["start"], "$.time_window.start")
    require_nullable_string(time_window["end"], "$.time_window.end")
    require_string(time_window["timezone"], "$.time_window.timezone")

    permission = exact_object(intake["permission"], _PERMISSION_KEYS, "$.permission")
    if not require_boolean(permission["confirmed"], "$.permission.confirmed"):
        raise ContractError("permission must be confirmed before private rows are read")
    require_string(permission["confirmed_by"], "$.permission.confirmed_by")
    require_timestamp(permission["confirmed_at"], "$.permission.confirmed_at")
    require_string(permission["data_owner"], "$.permission.data_owner")
    require_string(
        permission["legal_or_contract_basis"], "$.permission.legal_or_contract_basis"
    )
    require_string(permission["note"], "$.permission.note", allow_empty=True)

    columns = exact_object(intake["columns"], _COLUMN_KEYS, "$.columns")
    require_string(columns["entity_id"], "$.columns.entity_id")
    list_keys = {
        "direct_identifiers", "quasi_identifiers", "sensitive", "dimensions",
        "metrics", "ignored",
    }
    for key in list_keys:
        require_string_list(columns[key], f"$.columns.{key}")
    require_nullable_string(columns["outcome"], "$.columns.outcome")
    require_nullable_string(columns["event_date"], "$.columns.event_date")

    classified: list[str] = [columns["entity_id"]]
    for key in sorted(list_keys):
        classified.extend(columns[key])
    if columns["outcome"] is not None:
        classified.append(columns["outcome"])
    if columns["event_date"] is not None:
        classified.append(columns["event_date"])
    duplicates = sorted(
        value for value, count in Counter(classified).items() if count > 1
    )
    if duplicates:
        raise ContractError(
            "$.columns classifies fields more than once: " + ", ".join(duplicates)
        )

    privacy = exact_object(intake["privacy"], _PRIVACY_KEYS, "$.privacy")
    require_integer(
        privacy["minimum_cell_size"],
        "$.privacy.minimum_cell_size",
        minimum=3,
        maximum=1000,
    )
    release_mode = require_enum(
        privacy["release_mode"], _RELEASE_MODES, "$.privacy.release_mode"
    )
    if release_mode not in _SUPPORTED_RELEASE_MODES:
        raise ContractError(
            f"{release_mode} requires a separately configured and approved privacy engine"
        )
    epsilon = privacy["privacy_budget_epsilon"]
    if epsilon is not None:
        require_number(
            epsilon,
            "$.privacy.privacy_budget_epsilon",
            minimum=0.000001,
        )
    require_boolean(
        privacy["suppress_rare_values"], "$.privacy.suppress_rare_values"
    )
    require_boolean(
        privacy["allow_synthetic_release"], "$.privacy.allow_synthetic_release"
    )
    if privacy["allow_synthetic_release"]:
        raise ContractError(
            "synthetic release requires a separately approved synthesizer and privacy assessment"
        )

    analysis = exact_object(intake["analysis"], _ANALYSIS_KEYS, "$.analysis")
    require_boolean(
        analysis["generate_cross_tabs"], "$.analysis.generate_cross_tabs"
    )
    require_integer(
        analysis["max_cross_tab_dimensions"],
        "$.analysis.max_cross_tab_dimensions",
        minimum=1,
        maximum=3,
    )
    modeling_mode = require_enum(
        analysis["modeling_mode"], _MODELING_MODES, "$.analysis.modeling_mode"
    )
    feature_columns = require_string_list(
        analysis["feature_columns"], "$.analysis.feature_columns"
    )
    allowed_features = set(columns["dimensions"]) | set(columns["metrics"])
    if not set(feature_columns).issubset(allowed_features):
        raise ContractError(
            "$.analysis.feature_columns may use only approved dimensions and metrics"
        )
    cluster_counts = analysis["cluster_counts"]
    if not isinstance(cluster_counts, list):
        raise ContractError("$.analysis.cluster_counts must be an array")
    validated_counts = [
        require_integer(value, f"$.analysis.cluster_counts[{index}]", minimum=2, maximum=20)
        for index, value in enumerate(cluster_counts)
    ]
    if len(validated_counts) != len(set(validated_counts)):
        raise ContractError("$.analysis.cluster_counts must not contain duplicates")
    require_integer(analysis["model_seed"], "$.analysis.model_seed", minimum=0)
    require_integer(
        analysis["minimum_model_rows"],
        "$.analysis.minimum_model_rows",
        minimum=20,
    )
    require_number(
        analysis["temporal_holdout_fraction"],
        "$.analysis.temporal_holdout_fraction",
        minimum=0.1,
        maximum=0.5,
    )
    if modeling_mode == "segment_candidates":
        if not feature_columns or not validated_counts:
            raise ContractError(
                "segment_candidates requires feature_columns and cluster_counts"
            )
    if modeling_mode == "performance_prediction":
        if intake["data_kind"] != "performance":
            raise ContractError("performance_prediction requires data_kind performance")
        if not feature_columns or columns["outcome"] is None or columns["event_date"] is None:
            raise ContractError(
                "performance_prediction requires features, outcome, and event_date"
            )

    require_string_list(intake["allowed_uses"], "$.allowed_uses", nonempty=True)
    require_string_list(
        intake["prohibited_uses"], "$.prohibited_uses", nonempty=True
    )
    retention = exact_object(intake["retention"], _RETENTION_KEYS, "$.retention")
    require_enum(
        retention["raw_input_action"],
        _RETENTION_ACTIONS,
        "$.retention.raw_input_action",
    )
    require_enum(
        retention["working_copy_action"],
        _RETENTION_ACTIONS,
        "$.retention.working_copy_action",
    )
    require_timestamp(retention["deadline"], "$.retention.deadline")
    require_string(retention["approved_by"], "$.retention.approved_by")
    return intake


def _reconcile_columns(input_columns: Sequence[str], intake: Mapping[str, Any]) -> None:
    columns = intake["columns"]
    declared = {columns["entity_id"]}
    for key in (
        "direct_identifiers", "quasi_identifiers", "sensitive", "dimensions",
        "metrics", "ignored",
    ):
        declared.update(columns[key])
    if columns["outcome"] is not None:
        declared.add(columns["outcome"])
    if columns["event_date"] is not None:
        declared.add(columns["event_date"])
    actual = set(input_columns)
    if actual != declared:
        unclassified = sorted(actual - declared)
        absent = sorted(declared - actual)
        parts = []
        if unclassified:
            parts.append("unclassified input columns: " + ", ".join(unclassified))
        if absent:
            parts.append("declared columns absent from input: " + ", ".join(absent))
        raise ContractError("; ".join(parts))


def _missingness(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    total = len(rows)
    return [
        {
            "column": column,
            "missing_count": sum(1 for row in rows if is_missing(row.get(column))),
            "missing_share": round(
                sum(1 for row in rows if is_missing(row.get(column))) / total,
                6,
            ),
        }
        for column in columns
    ]


def _entity_count(rows: Sequence[Mapping[str, Any]], entity_column: str) -> int:
    return len(
        {
            as_text(row.get(entity_column))
            for row in rows
            if as_text(row.get(entity_column))
        }
    )


def _privacy_risk(
    rows: Sequence[Mapping[str, Any]],
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    columns = intake["columns"]
    minimum = intake["privacy"]["minimum_cell_size"]
    quasi = columns["quasi_identifiers"]
    entity = columns["entity_id"]
    direct_present = [
        column
        for column in columns["direct_identifiers"]
        if any(not is_missing(row.get(column)) for row in rows)
    ]
    risky_combinations = 0
    risky_entities: set[str] = set()
    if quasi:
        combination_entities: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for row in rows:
            entity_value = as_text(row.get(entity))
            if not entity_value:
                continue
            key = tuple(as_text(row.get(column)) or "[missing]" for column in quasi)
            combination_entities[key].add(entity_value)
        for entities in combination_entities.values():
            if len(entities) < minimum:
                risky_combinations += 1
                risky_entities.update(entities)
    duplicate_contributions = len(rows) - _entity_count(rows, entity)
    return {
        "minimum_cell_size": minimum,
        "direct_identifier_columns_present": len(direct_present),
        "quasi_identifier_column_count": len(quasi),
        "rare_quasi_combination_count": risky_combinations,
        "entities_in_rare_quasi_combinations": len(risky_entities),
        "duplicate_entity_contributions": max(0, duplicate_contributions),
        "release_mode": intake["privacy"]["release_mode"],
        "raw_rows_released": False,
    }


def _numeric_summaries(
    rows: Sequence[Mapping[str, Any]],
    metric_columns: Sequence[str],
    minimum_count: int,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for column in metric_columns:
        values = [as_float(row.get(column)) for row in rows]
        numeric = sorted(float(value) for value in values if value is not None)
        if len(numeric) < minimum_count:
            summaries.append(
                {
                    "column": column,
                    "count": None,
                    "mean": None,
                    "median": None,
                    "p25": None,
                    "p75": None,
                    "suppressed": True,
                }
            )
            continue
        array = __import__("numpy").array(numeric, dtype=float)
        summaries.append(
            {
                "column": column,
                "count": len(numeric),
                "mean": round(mean(numeric), 6),
                "median": round(median(numeric), 6),
                "p25": round(float(__import__("numpy").quantile(array, 0.25)), 6),
                "p75": round(float(__import__("numpy").quantile(array, 0.75)), 6),
                "suppressed": False,
            }
        )
    return summaries


def _group_entities(
    rows: Sequence[Mapping[str, Any]],
    entity_column: str,
    dimension_columns: Sequence[str],
) -> dict[tuple[str, ...], set[str]]:
    groups: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for row in rows:
        entity = as_text(row.get(entity_column))
        if not entity:
            continue
        key = tuple(as_text(row.get(column)) or "[missing]" for column in dimension_columns)
        groups[key].add(entity)
    return groups


def _safe_cells(
    rows: Sequence[Mapping[str, Any]],
    entity_column: str,
    dimension_columns: Sequence[str],
    minimum_count: int,
    entity_total: int,
) -> list[dict[str, Any]]:
    groups = _group_entities(rows, entity_column, dimension_columns)
    safe: list[dict[str, Any]] = []
    suppressed_entities: set[str] = set()
    for key, entities in sorted(groups.items()):
        if len(entities) < minimum_count:
            suppressed_entities.update(entities)
            continue
        safe.append(
            {
                "dimensions": dict(zip(dimension_columns, key)),
                "count": len(entities),
                "share": round(len(entities) / max(entity_total, 1), 6),
                "suppressed": False,
            }
        )
    if suppressed_entities:
        safe.append(
            {
                "dimensions": {
                    column: "[suppressed]" for column in dimension_columns
                },
                "count": None,
                "share": None,
                "suppressed": True,
            }
        )
    return safe


def _aggregate_first_party(
    rows: Sequence[Mapping[str, Any]],
    intake: Mapping[str, Any],
    audit: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    columns = intake["columns"]
    minimum = intake["privacy"]["minimum_cell_size"]
    entity_total = audit["entity_count"]
    distributions = []
    for dimension in columns["dimensions"]:
        distributions.extend(
            _safe_cells(
                rows, columns["entity_id"], [dimension], minimum, entity_total
            )
        )
    cross_tabs: list[dict[str, Any]] = []
    if intake["analysis"]["generate_cross_tabs"]:
        upper = min(
            intake["analysis"]["max_cross_tab_dimensions"],
            len(columns["dimensions"]),
        )
        for width in range(2, upper + 1):
            for selected in combinations(columns["dimensions"], width):
                cross_tabs.extend(
                    _safe_cells(
                        rows,
                        columns["entity_id"],
                        list(selected),
                        minimum,
                        entity_total,
                    )
                )
    segment_output: dict[str, Any] = {
        "status": "not_run",
        "recommended_cluster_count": None,
        "candidate_tests": [],
        "candidate_profiles": [],
    }
    if intake["analysis"]["modeling_mode"] == "segment_candidates":
        if entity_total < intake["analysis"]["minimum_model_rows"]:
            segment_output["status"] = "insufficient_data"
        else:
            bundle = build_feature_matrix(
                rows,
                intake["analysis"]["feature_columns"],
                set(columns["metrics"]),
                minimum,
            )
            segment_output = segment_candidates(
                bundle,
                intake["analysis"]["cluster_counts"],
                seed=intake["analysis"]["model_seed"],
                minimum_cluster_size=minimum,
            )
    audit_hash = sha256_json(audit)
    return {
        "schema_version": FIRST_PARTY_VERSION,
        "package_id": f"{intake['project_id']}-first-party-evidence",
        "created_at": created_at,
        "status": "draft",
        "source_audit_sha256": audit_hash,
        "input_sha256": audit["input_sha256"],
        "purpose": intake["purpose"],
        "covered_population": intake["covered_population"],
        "time_window": dict(intake["time_window"]),
        "evidence_basis": "permissioned_first_party_aggregate",
        "data_quality": {
            "row_count": audit["row_count"],
            "entity_count": audit["entity_count"],
            "missingness": audit["missingness"],
            "numeric_summaries": _numeric_summaries(
                rows, columns["metrics"], minimum
            ),
        },
        "distributions": distributions,
        "cross_tabs": cross_tabs,
        "segment_candidates": segment_output,
        "privacy_assessment": dict(audit["privacy_risk"]),
        "allowed_uses": list(intake["allowed_uses"]),
        "prohibited_uses": list(intake["prohibited_uses"]),
        "limitations": [
            "First-party coverage reflects the supplied corpus and may not represent the total market.",
            "Exploratory clusters require research interpretation and approval before panel use.",
        ],
        "approval": {
            "approved_for_downstream_use": False,
            "approved_by": None,
            "approved_at": None,
            "approval_note": None,
        },
    }


def _cohort_results(
    rows: Sequence[Mapping[str, Any]],
    intake: Mapping[str, Any],
) -> list[dict[str, Any]]:
    columns = intake["columns"]
    outcome = columns["outcome"]
    if outcome is None:
        return []
    minimum = intake["privacy"]["minimum_cell_size"]
    results: list[dict[str, Any]] = []
    for dimension in columns["dimensions"]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[as_text(row.get(dimension)) or "[missing]"].append(row)
        suppressed = False
        for value, group_rows in sorted(groups.items()):
            entities = {
                as_text(row.get(columns["entity_id"]))
                for row in group_rows
                if as_text(row.get(columns["entity_id"]))
            }
            outcome_values = [
                as_float(row.get(outcome))
                for row in group_rows
                if as_float(row.get(outcome)) is not None
            ]
            if len(entities) < minimum or len(outcome_values) < minimum:
                suppressed = True
                continue
            results.append(
                {
                    "dimensions": {dimension: value},
                    "entity_count": len(entities),
                    "observation_count": len(outcome_values),
                    "outcome_mean": round(
                        mean(float(item) for item in outcome_values), 6
                    ),
                    "suppressed": False,
                }
            )
        if suppressed:
            results.append(
                {
                    "dimensions": {dimension: "[suppressed]"},
                    "entity_count": None,
                    "observation_count": None,
                    "outcome_mean": None,
                    "suppressed": True,
                }
            )
    return results


def _aggregate_performance(
    rows: Sequence[Mapping[str, Any]],
    intake: Mapping[str, Any],
    audit: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    columns = intake["columns"]
    outcome = columns["outcome"]
    if outcome is None:
        raise ContractError("performance evidence requires an outcome column")
    model_results: dict[str, Any] = {
        "validation_state": "not_run",
        "model_type": None,
        "usable_rows": 0,
        "baseline_metrics": {},
        "model_metrics": {},
    }
    temporal_split: dict[str, Any] = {
        "strategy": "not_run",
        "train_start": None,
        "train_end": None,
        "holdout_start": None,
        "holdout_end": None,
        "train_rows": 0,
        "holdout_rows": 0,
    }
    if intake["analysis"]["modeling_mode"] == "performance_prediction":
        bundle = build_feature_matrix(
            rows,
            intake["analysis"]["feature_columns"],
            set(columns["metrics"]),
            intake["privacy"]["minimum_cell_size"],
        )
        model_results, temporal_split = performance_model(
            rows,
            bundle,
            outcome,
            columns["event_date"],
            holdout_fraction=intake["analysis"]["temporal_holdout_fraction"],
            minimum_model_rows=intake["analysis"]["minimum_model_rows"],
        )
    audit_hash = sha256_json(audit)
    return {
        "schema_version": PERFORMANCE_VERSION,
        "package_id": f"{intake['project_id']}-performance-evidence",
        "created_at": created_at,
        "status": "draft",
        "source_audit_sha256": audit_hash,
        "input_sha256": audit["input_sha256"],
        "purpose": intake["purpose"],
        "covered_population": intake["covered_population"],
        "time_window": dict(intake["time_window"]),
        "evidence_basis": "permissioned_historical_performance",
        "outcome_definition": {
            "column": outcome,
            "unit": "input_observation",
            "objective": intake["purpose"],
        },
        "data_quality": {
            "row_count": audit["row_count"],
            "entity_count": audit["entity_count"],
            "missingness": audit["missingness"],
            "numeric_summaries": _numeric_summaries(
                rows,
                list(columns["metrics"]) + [outcome],
                intake["privacy"]["minimum_cell_size"],
            ),
        },
        "cohort_results": _cohort_results(rows, intake),
        "temporal_split": temporal_split,
        "model_results": model_results,
        "calibration_scope": {
            "state": model_results["validation_state"],
            "outcome": outcome,
            "audience": intake["covered_population"],
            "time_window": dict(intake["time_window"]),
            "claim": (
                "Retrospective model evaluation for the named outcome and period."
                if model_results["validation_state"] == "retrospectively_evaluated"
                else "No performance-validation claim."
            ),
        },
        "privacy_assessment": dict(audit["privacy_risk"]),
        "allowed_uses": list(intake["allowed_uses"]),
        "prohibited_uses": list(intake["prohibited_uses"]),
        "limitations": [
            "Historical performance may reflect targeting, delivery, attribution, and campaign-selection effects.",
            "Retrospective evaluation does not establish prospective predictive validity.",
        ],
        "approval": {
            "approved_for_downstream_use": False,
            "approved_by": None,
            "approved_at": None,
            "approval_note": None,
        },
    }


def _build_audit(
    rows: Sequence[Mapping[str, Any]],
    input_columns: Sequence[str],
    intake: Mapping[str, Any],
    input_sha256: str,
    created_at: str,
) -> dict[str, Any]:
    entity_column = intake["columns"]["entity_id"]
    entity_count = _entity_count(rows, entity_column)
    privacy_risk = _privacy_risk(rows, intake)
    analysis_mode = intake["analysis"]["modeling_mode"]
    minimum_rows = intake["analysis"]["minimum_model_rows"]
    reasons: list[str] = []
    if entity_count < intake["privacy"]["minimum_cell_size"]:
        reasons.append("entity count is below the minimum release cell size")
    if analysis_mode != "none" and len(rows) < minimum_rows:
        reasons.append("row count is below the requested modeling minimum")
    decision = "ready" if not reasons else "aggregate_only_with_modeling_blocked"
    return {
        "schema_version": AUDIT_VERSION,
        "project_id": intake["project_id"],
        "generated_at": created_at,
        "input_sha256": input_sha256,
        "data_kind": intake["data_kind"],
        "row_count": len(rows),
        "entity_count": entity_count,
        "column_inventory": {
            "input_column_count": len(input_columns),
            "classified_column_count": len(input_columns),
            "direct_identifier_count": len(
                intake["columns"]["direct_identifiers"]
            ),
            "quasi_identifier_count": len(
                intake["columns"]["quasi_identifiers"]
            ),
            "sensitive_column_count": len(intake["columns"]["sensitive"]),
            "analysis_dimension_count": len(intake["columns"]["dimensions"]),
            "numeric_metric_count": len(intake["columns"]["metrics"]),
        },
        "missingness": _missingness(
            rows,
            list(intake["columns"]["dimensions"])
            + list(intake["columns"]["metrics"])
            + (
                [intake["columns"]["outcome"]]
                if intake["columns"]["outcome"] is not None
                else []
            )
            + (
                [intake["columns"]["event_date"]]
                if intake["columns"]["event_date"] is not None
                else []
            ),
        ),
        "privacy_risk": privacy_risk,
        "analysis_readiness": {
            "requested_modeling_mode": analysis_mode,
            "minimum_model_rows": minimum_rows,
            "row_minimum_met": len(rows) >= minimum_rows,
            "feature_count": len(intake["analysis"]["feature_columns"]),
        },
        "release_readiness": {
            "release_mode": intake["privacy"]["release_mode"],
            "minimum_cell_size": intake["privacy"]["minimum_cell_size"],
            "raw_rows_allowed": False,
            "aggregate_handoff_allowed": entity_count
            >= intake["privacy"]["minimum_cell_size"],
        },
        "retention": dict(intake["retention"]),
        "decision": decision,
        "reasons": reasons,
    }


def validate_handoff(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError("handoff must be an object")
    version = payload.get("schema_version")
    if version == FIRST_PARTY_VERSION:
        handoff = exact_object(payload, _FIRST_PARTY_KEYS, "$")
    elif version == PERFORMANCE_VERSION:
        handoff = exact_object(payload, _PERFORMANCE_KEYS, "$")
    else:
        raise ContractError("unsupported handoff schema_version")
    require_identifier(handoff["package_id"], "$.package_id")
    require_timestamp(handoff["created_at"], "$.created_at")
    require_enum(handoff["status"], {"draft", "approved"}, "$.status")
    approval = exact_object(handoff["approval"], _APPROVAL_KEYS, "$.approval")
    approved = require_boolean(
        approval["approved_for_downstream_use"],
        "$.approval.approved_for_downstream_use",
    )
    if approved:
        require_string(approval["approved_by"], "$.approval.approved_by")
        require_timestamp(approval["approved_at"], "$.approval.approved_at")
        require_string(
            approval["approval_note"],
            "$.approval.approval_note",
            allow_empty=True,
        )
        if handoff["status"] != "approved":
            raise ContractError("approved handoff must use status approved")
    else:
        if any(
            approval[key] is not None
            for key in ("approved_by", "approved_at", "approval_note")
        ):
            raise ContractError("draft approval identity fields must be null")
        if handoff["status"] != "draft":
            raise ContractError("unapproved handoff must use status draft")
    def contains_prohibited_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            if set(value) & {"raw_rows", "row_samples"}:
                return True
            return any(contains_prohibited_key(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_prohibited_key(item) for item in value)
        return False

    if contains_prohibited_key(handoff):
        raise ContractError("handoff contains prohibited row-level output fields")
    return handoff


def approve_handoff(
    draft_payload: Any,
    approval_payload: Any,
) -> dict[str, Any]:
    draft = validate_handoff(draft_payload)
    if draft["status"] != "draft":
        raise ContractError("only a draft handoff may be approved")
    approval_record = exact_object(
        approval_payload, _APPROVAL_RECORD_KEYS, "$.approval"
    )
    if approval_record["schema_version"] != "audience-evidence-approval-v1":
        raise ContractError(
            "$.approval.schema_version must equal audience-evidence-approval-v1"
        )
    if not require_boolean(
        approval_record["approved_for_downstream_use"],
        "$.approval.approved_for_downstream_use",
    ):
        raise ContractError("approval must explicitly authorize downstream use")
    require_string(approval_record["approved_by"], "$.approval.approved_by")
    require_timestamp(approval_record["approved_at"], "$.approval.approved_at")
    require_string(
        approval_record["approval_note"],
        "$.approval.approval_note",
        allow_empty=True,
    )
    approved = json.loads(json.dumps(draft))
    frozen = {
        key: value for key, value in approved.items() if key not in {"status", "approval"}
    }
    approved["status"] = "approved"
    approved["approval"] = {
        key: approval_record[key]
        for key in (
            "approved_for_downstream_use", "approved_by", "approved_at",
            "approval_note",
        )
    }
    if frozen != {
        key: value
        for key, value in approved.items()
        if key not in {"status", "approval"}
    }:
        raise ContractError("approval changed a frozen handoff field")
    return validate_handoff(approved)


def prepare_private_evidence(
    input_path: str | Path,
    intake_payload: Any,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    intake = validate_intake(intake_payload)
    input_columns, rows = load_rows(input_path)
    _reconcile_columns(input_columns, intake)
    created_at = _utc_now()
    audit = _build_audit(
        rows,
        input_columns,
        intake,
        sha256_file(str(input_path)),
        created_at,
    )
    if not audit["release_readiness"]["aggregate_handoff_allowed"]:
        raise ContractError(
            "aggregate handoff is blocked because entity count is below the minimum cell size"
        )
    if intake["data_kind"] == "performance":
        handoff = _aggregate_performance(rows, intake, audit, created_at)
    else:
        handoff = _aggregate_first_party(rows, intake, audit, created_at)
    validate_handoff(handoff)
    report = render_methodology_report(intake, audit, handoff)
    return audit, handoff, report


def render_methodology_report(
    intake: Mapping[str, Any],
    audit: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> str:
    modeling = (
        handoff.get("segment_candidates")
        if handoff["schema_version"] == FIRST_PARTY_VERSION
        else handoff.get("model_results")
    )
    allowed = "".join(
        f"<li>{html.escape(item)}</li>" for item in handoff["allowed_uses"]
    )
    limitations = "".join(
        f"<li>{html.escape(item)}</li>" for item in handoff["limitations"]
    )
    reasons = "".join(
        f"<li>{html.escape(item)}</li>" for item in audit["reasons"]
    ) or "<li>No blocking audit reason.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audience Data Lab Methodology Report</title>
<style>
body{{font-family:Inter,system-ui,sans-serif;max-width:980px;margin:0 auto;padding:40px;color:#17202a;background:#f7f5ef}}
main{{background:white;border:1px solid #d8d4c8;border-radius:18px;padding:32px}}
h1,h2{{line-height:1.1}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{border:1px solid #ded9cd;border-radius:12px;padding:16px;background:#fbfaf6}} code{{word-break:break-all}}
.status{{font-weight:700;color:#315b4c}} table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px;border-bottom:1px solid #e7e2d7}}
</style>
</head>
<body><main>
<p>Audience Data Lab</p>
<h1>Private-data methodology report</h1>
<p>{html.escape(intake['purpose'])}</p>
<div class="grid">
<div class="card"><strong>Covered population</strong><br>{html.escape(intake['covered_population'])}</div>
<div class="card"><strong>Rows examined locally</strong><br>{audit['row_count']}</div>
<div class="card"><strong>Distinct entities</strong><br>{audit['entity_count']}</div>
<div class="card"><strong>Release mode</strong><br>{html.escape(audit['privacy_risk']['release_mode'])}</div>
</div>
<h2>Privacy boundary</h2>
<p>No raw rows were released or provided to an LLM. Direct identifiers, sensitive fields, and ignored fields were excluded from analysis outputs. Cells below the approved minimum of {audit['privacy_risk']['minimum_cell_size']} were suppressed.</p>
<table>
<tr><th>Direct-identifier columns detected</th><td>{audit['privacy_risk']['direct_identifier_columns_present']}</td></tr>
<tr><th>Quasi-identifier columns reviewed</th><td>{audit['privacy_risk']['quasi_identifier_column_count']}</td></tr>
<tr><th>Entities in rare quasi-identifier combinations</th><td>{audit['privacy_risk']['entities_in_rare_quasi_combinations']}</td></tr>
<tr><th>Audit decision</th><td class="status">{html.escape(audit['decision'])}</td></tr>
</table>
<h2>Analysis</h2>
<pre>{html.escape(json.dumps(modeling, indent=2, sort_keys=True))}</pre>
<h2>Allowed uses</h2><ul>{allowed}</ul>
<h2>Limitations</h2><ul>{limitations}</ul>
<h2>Audit notes</h2><ul>{reasons}</ul>
<h2>Approval</h2>
<p>This handoff is a draft. Downstream use begins only after the user approves the exact JSON package.</p>
<p><code>{html.escape(handoff['source_audit_sha256'])}</code></p>
</main></body></html>
"""


def write_outputs(
    output_dir: str | Path,
    audit: Mapping[str, Any],
    handoff: Mapping[str, Any],
    report: str,
) -> dict[str, str]:
    directory = create_new_directory(output_dir, "private evidence output directory")
    audit_path = directory / "private-data-audit.json"
    handoff_name = (
        "audience-first-party-evidence.json"
        if handoff["schema_version"] == FIRST_PARTY_VERSION
        else "audience-performance-evidence.json"
    )
    handoff_path = directory / handoff_name
    report_path = directory / "data-methodology-report.html"
    audit_path.write_bytes(canonical_json_bytes(audit))
    handoff_path.write_bytes(canonical_json_bytes(handoff))
    report_path.write_text(report, encoding="utf-8")
    return {
        "audit": str(audit_path),
        "handoff": str(handoff_path),
        "report": str(report_path),
    }
