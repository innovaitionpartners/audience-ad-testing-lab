"""Strict declarative mappings for authorized aggregate audience sources."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Mapping

from .authorized_source import validate_source_profile
from .common import (
    ContractError,
    exact_object,
    require_boolean,
    require_enum,
    require_identifier,
    require_integer,
    require_string,
    require_string_list,
    require_timestamp,
    sha256_json,
)


AUTHORIZED_MAPPING_VERSION = "authorized-audience-mapping-v1"
SEMANTIC_ROUTES = frozenset(
    {
        "structural_frame",
        "overlay_evidence",
        "profile_seed",
        "outcome_feedback",
        "unsupported",
    }
)
ALLOWED_OPERATIONS = frozenset(
    {
        "select",
        "rename",
        "cast",
        "flatten",
        "wide_to_long",
        "pivot",
        "join",
        "category_map",
        "normalize_missing",
        "normalize_suppression",
        "derive_share",
        "normalize_weight",
        "aggregate",
        "filter",
        "sort",
    }
)

_TOP_LEVEL_KEYS = {
    "schema_version",
    "mapping_id",
    "mapping_version",
    "source_profile_sha256",
    "input_hashes",
    "selections",
    "operations",
    "field_routes",
    "expected_outputs",
    "ignored_fields",
    "privacy_requirements",
    "approval",
}
_SELECTION_KEYS = {
    "selection_id",
    "file",
    "file_sha256",
    "sheet",
    "record_path",
    "fields",
    "unit",
    "denominator",
    "aggregate_join_keys",
}
_FIELD_ROUTE_KEYS = {"dataset", "field", "route"}
_EXPECTED_OUTPUT_KEYS = {
    "dataset", "route", "filename", "schema_version", "metadata",
}
_IGNORED_FIELD_KEYS = {"file", "sheet", "record_path", "field", "reason"}
_PRIVACY_KEYS = {
    "permission_confirmed",
    "aggregate_only",
    "minimum_cell_size",
    "prohibited_routes",
    "resolved_clarifications",
}
_APPROVAL_KEYS = {"status", "approved_by", "approved_at", "mapping_sha256"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_AMBIGUOUS = {"unknown", "ambiguous", "unspecified", "tbd", "n/a", "na", "none"}
_CANONICAL_FILENAMES = re.compile(
    r"(?:frame-observations|structured-evidence|social-observations|"
    r"profile-seeds|outcome-feedback)-\d{4}\.json"
)
_EXECUTABLE = re.compile(
    r"(?:__import__|(?:^|[^a-z])(eval|exec|compile|subprocess)(?:[^a-z]|$)|"
    r"\bselect\s+.+\s+from\b|\$\(|`|<%|{{|lambda\s|os\.system|"
    r"(?:^|[;&|])\s*(?:sh|bash|zsh|python)\b)",
    re.IGNORECASE,
)
_STRUCTURAL_FIELDS = {
    "count",
    "respondent_count",
    "sample_size",
    "share",
    "rate",
    "percentage",
    "pct",
    "weight",
    "normalized_weight",
    "denominator",
    "population",
    "estimate",
}
_PERMITTED_CLARIFICATIONS = {
    "confirm_unit",
    "confirm_denominator",
    "confirm_join",
    "confirm_field_meaning",
    "confirm_unit_and_denominator",
}
CANONICAL_OUTPUT_REGISTRY = {
    "frame-observations": (
        "structural_frame",
        "audience-frame-observation-batch-v1",
    ),
    "structured-evidence": (
        "overlay_evidence",
        "audience-structured-evidence-batch-v1",
    ),
    "social-observations": (
        "overlay_evidence",
        "social-observation-batch-v1",
    ),
    "profile-seeds": (
        "profile_seed",
        "audience-profile-seed-batch-v1",
    ),
    "outcome-feedback": (
        "outcome_feedback",
        "panel-outcome-feedback-v1",
    ),
}
_FRAME_METADATA_KEYS = {
    "batch_id", "frame_request_id", "adapter_id", "source_family", "source",
    "raw_snapshot_sha256", "access", "geography", "unit", "denominator",
    "dimension_fields", "estimate_field", "cell_key_field", "cell_metadata",
    "selection_notes", "coverage_notes", "citations",
}
_FRAME_SOURCE_KEYS = {
    "publisher", "program", "edition", "vintage", "retrieved_at",
}
_FRAME_ACCESS_KEYS = {
    "access_type", "permission_confirmed", "permitted_uses",
}
_FRAME_CELL_METADATA_KEYS = {
    "cell_id", "uncertainty", "suppressed", "status", "relationship",
    "source_location",
}
_FRAME_UNCERTAINTY_KEYS = {"lower_field", "upper_field", "method"}
_STRUCTURED_METADATA_KEYS = {
    "batch_id", "created_at", "source_adapter", "source_schema_version",
    "input_sha256", "permission", "source_status", "item_id_field",
    "content_summary_field", "item_metadata",
}
_STRUCTURED_ITEM_METADATA_KEYS = {
    "source_url", "item_type", "text_fidelity", "content_sha256",
    "source_pointer", "upstream_source_ids", "use_constraints",
    "quality_flags",
}
_OUTCOME_METADATA_KEYS = {
    "record_match", "feedback_id", "panel_id", "study_id", "variant_id",
    "cohort_id", "metric", "metric_direction", "units", "windows",
    "aggregate_fields", "design", "source", "holdout", "missingness",
    "limitations", "source_sha256",
}
_OUTCOME_METRIC_KEYS = {"name", "definition"}
_OUTCOME_UNIT_KEYS = {"exposure", "outcome"}
_OUTCOME_WINDOW_KEYS = {"measurement", "attribution"}
_OUTCOME_AGGREGATE_FIELD_KEYS = {"numerator", "denominator", "value"}
_OUTCOME_SOURCE_KEYS = {"source_id", "permission_confirmed"}
_SOCIAL_METADATA_KEYS = {
    "batch_id", "created_at", "source_adapter", "source_schema_version",
    "input_sha256", "query", "window_start", "window_end", "source_status",
    "collection", "coverage_warnings", "observation_id_field",
    "text_excerpt_field", "observation_metadata",
}
_SOCIAL_COLLECTION_KEYS = {
    "provider", "collector", "collector_version", "run_or_dataset_id",
    "collection_method", "access_route", "permitted_use", "sort_mode",
    "item_limit", "pagination", "completeness", "deduplication_control",
    "bot_spam_control",
}
_SOCIAL_OBSERVATION_METADATA_KEYS = {
    "platform", "source_item_id", "source_url", "published_at",
    "collected_at", "unit_of_analysis", "title", "text_fidelity",
    "content_sha256", "engagement", "relevance_score", "cluster_id",
    "role_status", "author_group_token", "freshness_verdict", "json_pointer",
    "use_constraints", "quality_flags",
}

_OPERATION_KEYS: dict[str, set[str]] = {
    "select": {"operation_id", "op", "input", "output", "fields"},
    "rename": {"operation_id", "op", "input", "output", "fields"},
    "cast": {"operation_id", "op", "input", "output", "fields"},
    "flatten": {"operation_id", "op", "input", "output", "fields"},
    "wide_to_long": {
        "operation_id", "op", "input", "output", "id_fields", "value_fields",
        "name_field", "value_field",
    },
    "pivot": {
        "operation_id", "op", "input", "output", "index_fields",
        "column_field", "value_field", "columns",
    },
    "join": {
        "operation_id", "op", "left", "right", "output", "on", "cardinality",
    },
    "category_map": {
        "operation_id", "op", "input", "output", "field", "mapping", "unmapped",
    },
    "normalize_missing": {
        "operation_id", "op", "input", "output", "fields", "values",
    },
    "normalize_suppression": {
        "operation_id", "op", "input", "output", "field", "values", "status_field",
    },
    "derive_share": {
        "operation_id", "op", "input", "output", "count_field",
        "denominator_field", "output_field",
    },
    "normalize_weight": {
        "operation_id", "op", "input", "output", "field", "output_field",
        "group_by",
    },
    "aggregate": {
        "operation_id", "op", "input", "output", "group_by", "metrics",
    },
    "filter": {
        "operation_id", "op", "input", "output", "field", "predicate", "value",
    },
    "sort": {"operation_id", "op", "input", "output", "fields"},
}


def mapping_sha256(payload: object) -> str:
    """Hash a profile normally or a mapping with its approval digest nulled."""

    if isinstance(payload, Mapping) and "approval" in payload:
        candidate = deepcopy(dict(payload))
        approval = candidate.get("approval")
        if isinstance(approval, Mapping):
            candidate["approval"] = dict(approval)
            candidate["approval"]["mapping_sha256"] = None
        return sha256_json(candidate)
    return sha256_json(payload)


def _require_digest(value: object, path: str) -> str:
    digest = require_string(value, path)
    if not _DIGEST.fullmatch(digest):
        raise ContractError(f"{path} must be a SHA-256 digest")
    return digest


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    return value


def _string_mapping(value: object, path: str, *, nonempty: bool = False) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    result: dict[str, str] = {}
    for key, raw in value.items():
        source = require_string(key, f"{path} key")
        result[source] = require_string(raw, f"{path}.{source}")
    if nonempty and not result:
        raise ContractError(f"{path} must not be empty")
    return result


def _validate_output_metadata(
    value: object,
    *,
    family: str,
    dataset_fields: set[str],
    path: str,
) -> dict[str, object]:
    if family == "frame-observations":
        metadata = exact_object(value, _FRAME_METADATA_KEYS, path)
        for key in ("batch_id", "frame_request_id", "adapter_id", "source_family"):
            require_identifier(metadata[key], f"{path}.{key}")
        source = exact_object(metadata["source"], _FRAME_SOURCE_KEYS, f"{path}.source")
        for key in ("publisher", "program", "edition", "vintage"):
            require_string(source[key], f"{path}.source.{key}")
        require_timestamp(source["retrieved_at"], f"{path}.source.retrieved_at")
        _require_digest(metadata["raw_snapshot_sha256"], f"{path}.raw_snapshot_sha256")
        access = exact_object(metadata["access"], _FRAME_ACCESS_KEYS, f"{path}.access")
        require_enum(
            access["access_type"],
            {"public", "licensed", "authorized"},
            f"{path}.access.access_type",
        )
        require_boolean(
            access["permission_confirmed"],
            f"{path}.access.permission_confirmed",
        )
        require_string_list(
            access["permitted_uses"],
            f"{path}.access.permitted_uses",
            nonempty=True,
        )
        require_string_list(metadata["geography"], f"{path}.geography", nonempty=True)
        require_identifier(metadata["unit"], f"{path}.unit")
        require_identifier(metadata["denominator"], f"{path}.denominator")
        dimensions = _string_mapping(
            metadata["dimension_fields"],
            f"{path}.dimension_fields",
            nonempty=True,
        )
        for dimension, field in dimensions.items():
            require_identifier(dimension, f"{path}.dimension_fields key")
            if field not in dataset_fields:
                raise ContractError(
                    f"{path}.dimension_fields.{dimension} is unresolved: {field}"
                )
        for key in ("estimate_field", "cell_key_field"):
            field = require_string(metadata[key], f"{path}.{key}")
            if field not in dataset_fields:
                raise ContractError(f"{path}.{key} is unresolved")
        cell_metadata = metadata["cell_metadata"]
        if not isinstance(cell_metadata, Mapping) or not cell_metadata:
            raise ContractError(f"{path}.cell_metadata must be a nonempty object")
        for source_key, raw_cell in cell_metadata.items():
            require_string(source_key, f"{path}.cell_metadata key")
            cell = exact_object(
                raw_cell,
                _FRAME_CELL_METADATA_KEYS,
                f"{path}.cell_metadata.{source_key}",
            )
            require_identifier(
                cell["cell_id"],
                f"{path}.cell_metadata.{source_key}.cell_id",
            )
            uncertainty = exact_object(
                cell["uncertainty"],
                _FRAME_UNCERTAINTY_KEYS,
                f"{path}.cell_metadata.{source_key}.uncertainty",
            )
            for key in ("lower_field", "upper_field"):
                field = require_string(
                    uncertainty[key],
                    f"{path}.cell_metadata.{source_key}.uncertainty.{key}",
                )
                if field not in dataset_fields:
                    raise ContractError(
                        f"{path}.cell_metadata.{source_key}.uncertainty.{key} "
                        "is unresolved"
                    )
            require_string(
                uncertainty["method"],
                f"{path}.cell_metadata.{source_key}.uncertainty.method",
            )
            require_boolean(
                cell["suppressed"],
                f"{path}.cell_metadata.{source_key}.suppressed",
            )
            require_enum(
                cell["status"],
                {"observed", "derived", "modeled", "missing"},
                f"{path}.cell_metadata.{source_key}.status",
            )
            require_enum(
                cell["relationship"],
                {"marginal", "joint"},
                f"{path}.cell_metadata.{source_key}.relationship",
            )
            require_string(
                cell["source_location"],
                f"{path}.cell_metadata.{source_key}.source_location",
            )
        for key in ("selection_notes", "coverage_notes"):
            require_string(metadata[key], f"{path}.{key}")
        require_string_list(metadata["citations"], f"{path}.citations", nonempty=True)
        return metadata
    if family == "structured-evidence":
        metadata = exact_object(value, _STRUCTURED_METADATA_KEYS, path)
        require_identifier(metadata["batch_id"], f"{path}.batch_id")
        require_timestamp(metadata["created_at"], f"{path}.created_at")
        for key in ("source_adapter", "source_schema_version", "source_status"):
            require_string(metadata[key], f"{path}.{key}")
        _require_digest(metadata["input_sha256"], f"{path}.input_sha256")
        require_enum(metadata["permission"], {"allowed", "conditional"}, f"{path}.permission")
        for key in ("item_id_field", "content_summary_field"):
            field = require_string(metadata[key], f"{path}.{key}")
            if field not in dataset_fields:
                raise ContractError(f"{path}.{key} is unresolved")
        item_metadata = metadata["item_metadata"]
        if not isinstance(item_metadata, Mapping) or not item_metadata:
            raise ContractError(f"{path}.item_metadata must be a nonempty object")
        for item_id, raw_item in item_metadata.items():
            require_identifier(item_id, f"{path}.item_metadata key")
            item = exact_object(
                raw_item,
                _STRUCTURED_ITEM_METADATA_KEYS,
                f"{path}.item_metadata.{item_id}",
            )
            for key in (
                "source_url", "item_type", "text_fidelity", "source_pointer",
            ):
                require_string(item[key], f"{path}.item_metadata.{item_id}.{key}")
            _require_digest(
                item["content_sha256"],
                f"{path}.item_metadata.{item_id}.content_sha256",
            )
            for key in ("upstream_source_ids", "use_constraints", "quality_flags"):
                require_string_list(
                    item[key],
                    f"{path}.item_metadata.{item_id}.{key}",
                )
        return metadata
    if family == "outcome-feedback":
        metadata = exact_object(value, _OUTCOME_METADATA_KEYS, path)
        record_match = metadata["record_match"]
        if not isinstance(record_match, Mapping) or not record_match:
            raise ContractError(f"{path}.record_match must be a nonempty object")
        if not set(record_match) <= dataset_fields:
            raise ContractError(f"{path}.record_match contains an unresolved field")
        for key, item in record_match.items():
            require_string(key, f"{path}.record_match key")
            if item is not None and not isinstance(item, (str, int, float, bool)):
                raise ContractError(f"{path}.record_match.{key} must be scalar")
        for key in (
            "feedback_id", "panel_id", "study_id", "variant_id", "cohort_id",
        ):
            require_identifier(metadata[key], f"{path}.{key}")
        metric = exact_object(metadata["metric"], _OUTCOME_METRIC_KEYS, f"{path}.metric")
        require_identifier(metric["name"], f"{path}.metric.name")
        require_string(metric["definition"], f"{path}.metric.definition")
        require_enum(
            metadata["metric_direction"],
            {"higher_is_better", "lower_is_better", "descriptive"},
            f"{path}.metric_direction",
        )
        units = exact_object(metadata["units"], _OUTCOME_UNIT_KEYS, f"{path}.units")
        for key in _OUTCOME_UNIT_KEYS:
            require_identifier(units[key], f"{path}.units.{key}")
        windows = exact_object(
            metadata["windows"], _OUTCOME_WINDOW_KEYS, f"{path}.windows"
        )
        for key in _OUTCOME_WINDOW_KEYS:
            require_string(windows[key], f"{path}.windows.{key}")
        aggregate_fields = exact_object(
            metadata["aggregate_fields"],
            _OUTCOME_AGGREGATE_FIELD_KEYS,
            f"{path}.aggregate_fields",
        )
        if not any(field is not None for field in aggregate_fields.values()):
            raise ContractError(f"{path}.aggregate_fields requires one source field")
        for key, field in aggregate_fields.items():
            if field is not None:
                source_field = require_string(field, f"{path}.aggregate_fields.{key}")
                if source_field not in dataset_fields:
                    raise ContractError(
                        f"{path}.aggregate_fields.{key} is unresolved"
                    )
        require_enum(
            metadata["design"],
            {"experimental", "observational", "modeled"},
            f"{path}.design",
        )
        source = exact_object(metadata["source"], _OUTCOME_SOURCE_KEYS, f"{path}.source")
        require_identifier(source["source_id"], f"{path}.source.source_id")
        require_boolean(
            source["permission_confirmed"],
            f"{path}.source.permission_confirmed",
        )
        require_boolean(metadata["holdout"], f"{path}.holdout")
        require_string(metadata["missingness"], f"{path}.missingness")
        require_string_list(metadata["limitations"], f"{path}.limitations", nonempty=True)
        _require_digest(metadata["source_sha256"], f"{path}.source_sha256")
        return metadata
    if family == "social-observations":
        metadata = exact_object(value, _SOCIAL_METADATA_KEYS, path)
        require_identifier(metadata["batch_id"], f"{path}.batch_id")
        for key in (
            "created_at", "window_start", "window_end",
        ):
            require_timestamp(metadata[key], f"{path}.{key}")
        for key in (
            "source_adapter", "source_schema_version", "query",
        ):
            require_string(metadata[key], f"{path}.{key}")
        _require_digest(metadata["input_sha256"], f"{path}.input_sha256")
        source_status = metadata["source_status"]
        if (
            not isinstance(source_status, Mapping)
            or not source_status
            or not all(
                isinstance(key, str)
                and key
                and isinstance(status, str)
                and status
                for key, status in source_status.items()
            )
        ):
            raise ContractError(
                f"{path}.source_status must map source names to statuses"
            )
        collection = exact_object(
            metadata["collection"],
            _SOCIAL_COLLECTION_KEYS,
            f"{path}.collection",
        )
        for key in _SOCIAL_COLLECTION_KEYS - {"item_limit"}:
            require_string(collection[key], f"{path}.collection.{key}")
        require_integer(
            collection["item_limit"],
            f"{path}.collection.item_limit",
            minimum=0,
        )
        require_string_list(metadata["coverage_warnings"], f"{path}.coverage_warnings")
        for key in ("observation_id_field", "text_excerpt_field"):
            field = require_string(metadata[key], f"{path}.{key}")
            if field not in dataset_fields:
                raise ContractError(f"{path}.{key} is unresolved")
        observation_metadata = metadata["observation_metadata"]
        if not isinstance(observation_metadata, Mapping) or not observation_metadata:
            raise ContractError(
                f"{path}.observation_metadata must be a nonempty object"
            )
        for observation_id, raw_item in observation_metadata.items():
            require_identifier(observation_id, f"{path}.observation_metadata key")
            item_path = f"{path}.observation_metadata.{observation_id}"
            item = exact_object(
                raw_item,
                _SOCIAL_OBSERVATION_METADATA_KEYS,
                item_path,
            )
            for key in (
                "platform", "source_item_id", "unit_of_analysis", "title",
                "json_pointer",
            ):
                require_string(item[key], f"{item_path}.{key}")
            require_enum(
                item["text_fidelity"],
                {
                    "verbatim_public_text", "platform_caption", "transcript",
                    "provider_summary", "faithful_summary",
                },
                f"{item_path}.text_fidelity",
            )
            require_enum(
                item["role_status"],
                {"verified", "self_reported", "inferred", "unknown"},
                f"{item_path}.role_status",
            )
            require_string(item["source_url"], f"{item_path}.source_url")
            if item["published_at"] is not None:
                require_timestamp(item["published_at"], f"{item_path}.published_at")
            require_timestamp(item["collected_at"], f"{item_path}.collected_at")
            _require_digest(item["content_sha256"], f"{item_path}.content_sha256")
            engagement = item["engagement"]
            if (
                not isinstance(engagement, Mapping)
                or not all(
                    isinstance(key, str)
                    and key
                    and isinstance(metric, (int, float))
                    and not isinstance(metric, bool)
                    for key, metric in engagement.items()
                )
            ):
                raise ContractError(
                    f"{item_path}.engagement must map names to numeric values"
                )
            relevance = item["relevance_score"]
            if (
                relevance is not None
                and (
                    isinstance(relevance, bool)
                    or not isinstance(relevance, (int, float))
                    or not 0 <= relevance <= 1
                )
            ):
                raise ContractError(
                    f"{item_path}.relevance_score must be null or 0..1"
                )
            for key in (
                "cluster_id", "author_group_token", "freshness_verdict",
            ):
                if item[key] is not None:
                    require_string(item[key], f"{item_path}.{key}")
            for key in ("use_constraints", "quality_flags"):
                require_string_list(item[key], f"{item_path}.{key}")
        return metadata
    if family == "profile-seeds":
        if not isinstance(value, Mapping) or value:
            raise ContractError(f"{path} must be an empty object")
        return dict(value)
    raise ContractError(f"{path} uses an unsupported canonical output family")


def _reject_executable_values(value: object, path: str = "mapping") -> None:
    if isinstance(value, str):
        if _EXECUTABLE.search(value):
            raise ContractError(f"{path} contains an executable-looking value")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_executable_values(key, f"{path} key")
            _reject_executable_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_executable_values(item, f"{path}[{index}]")


def _validate_operation(
    raw: object,
    index: int,
    datasets: dict[str, set[str]],
    join_keys: dict[str, set[str]],
) -> tuple[dict[str, object], set[str]]:
    path = f"mapping.operations[{index}]"
    if not isinstance(raw, Mapping):
        raise ContractError(f"{path} must be an object")
    op = require_enum(raw.get("op"), set(ALLOWED_OPERATIONS), f"{path}.op")
    item = exact_object(raw, _OPERATION_KEYS[op], path)
    require_identifier(item["operation_id"], f"{path}.operation_id")
    output = require_identifier(item["output"], f"{path}.output")
    if output in datasets:
        raise ContractError(f"{path}.output must name a new dataset")

    def fields_for(name_key: str = "input") -> tuple[str, set[str]]:
        name = require_identifier(item[name_key], f"{path}.{name_key}")
        if name not in datasets:
            raise ContractError(f"{path}.{name_key} does not resolve to an earlier dataset")
        return name, set(datasets[name])

    if op == "join":
        left, left_fields = fields_for("left")
        right, right_fields = fields_for("right")
        on = require_string_list(item["on"], f"{path}.on", nonempty=True)
        require_enum(item["cardinality"], {"one_to_one", "many_to_one"}, f"{path}.cardinality")
        if not set(on) <= left_fields or not set(on) <= right_fields:
            raise ContractError(f"{path}.on contains an unresolved join key")
        if not set(on) <= join_keys[left] or not set(on) <= join_keys[right]:
            raise ContractError(f"{path}.on must use exact approved aggregate join keys")
        overlap = (left_fields & right_fields) - set(on)
        if overlap:
            raise ContractError(f"{path} has colliding non-key fields: {', '.join(sorted(overlap))}")
        output_fields = left_fields | right_fields
        join_keys[output] = (join_keys[left] | join_keys[right]) & output_fields
        return item, output_fields

    input_name, input_fields = fields_for()
    output_fields = set(input_fields)
    if op == "select":
        selected = require_string_list(item["fields"], f"{path}.fields", nonempty=True)
        if not set(selected) <= input_fields:
            raise ContractError(f"{path}.fields contains an unresolved field")
        output_fields = set(selected)
    elif op in {"rename", "cast", "flatten"}:
        field_map = _string_mapping(item["fields"], f"{path}.fields", nonempty=True)
        if op == "rename":
            if not set(field_map) <= input_fields:
                raise ContractError(f"{path}.fields contains an unresolved field")
            output_fields = (input_fields - set(field_map)) | set(field_map.values())
            if len(output_fields) != len(input_fields):
                raise ContractError(f"{path}.fields creates a duplicate field")
        elif op == "cast":
            if not set(field_map) <= input_fields:
                raise ContractError(f"{path}.fields contains an unresolved field")
            for field, cast_type in field_map.items():
                require_enum(
                    cast_type,
                    {"string", "integer", "number", "boolean", "date"},
                    f"{path}.fields.{field}",
                )
        else:
            source_fields = {
                source_path.split(".", 1)[0]
                for source_path in field_map.values()
            }
            if not source_fields <= input_fields:
                raise ContractError(
                    f"{path}.fields contains an unresolved nested source field"
                )
            output_fields = set(field_map)
    elif op == "wide_to_long":
        ids = require_string_list(item["id_fields"], f"{path}.id_fields")
        values = require_string_list(item["value_fields"], f"{path}.value_fields", nonempty=True)
        if not set(ids + values) <= input_fields or set(ids) & set(values):
            raise ContractError(f"{path} contains unresolved or overlapping fields")
        name_field = require_string(item["name_field"], f"{path}.name_field")
        value_field = require_string(item["value_field"], f"{path}.value_field")
        output_fields = set(ids) | {name_field, value_field}
    elif op == "pivot":
        indexes = require_string_list(item["index_fields"], f"{path}.index_fields", nonempty=True)
        column = require_string(item["column_field"], f"{path}.column_field")
        value = require_string(item["value_field"], f"{path}.value_field")
        columns = require_string_list(item["columns"], f"{path}.columns", nonempty=True)
        if not set(indexes + [column, value]) <= input_fields:
            raise ContractError(f"{path} contains an unresolved field")
        output_fields = set(indexes + columns)
    elif op == "category_map":
        field = require_string(item["field"], f"{path}.field")
        if field not in input_fields:
            raise ContractError(f"{path}.field is unresolved")
        _string_mapping(item["mapping"], f"{path}.mapping", nonempty=True)
        require_enum(item["unmapped"], {"error", "keep", "null"}, f"{path}.unmapped")
    elif op == "normalize_missing":
        fields = require_string_list(item["fields"], f"{path}.fields", nonempty=True)
        if not set(fields) <= input_fields:
            raise ContractError(f"{path}.fields contains an unresolved field")
        if not isinstance(item["values"], list) or not item["values"]:
            raise ContractError(f"{path}.values must be a nonempty array")
    elif op == "normalize_suppression":
        field = require_string(item["field"], f"{path}.field")
        status = require_string(item["status_field"], f"{path}.status_field")
        if field not in input_fields or status in input_fields:
            raise ContractError(f"{path} contains an unresolved or duplicate field")
        if not isinstance(item["values"], list) or not item["values"]:
            raise ContractError(f"{path}.values must be a nonempty array")
        output_fields.add(status)
    elif op == "derive_share":
        for key in ("count_field", "denominator_field"):
            if require_string(item[key], f"{path}.{key}") not in input_fields:
                raise ContractError(f"{path}.{key} is unresolved")
        output_field = require_string(item["output_field"], f"{path}.output_field")
        if output_field in input_fields:
            raise ContractError(f"{path}.output_field already exists")
        output_fields.add(output_field)
    elif op == "normalize_weight":
        field = require_string(item["field"], f"{path}.field")
        output_field = require_string(item["output_field"], f"{path}.output_field")
        groups = require_string_list(item["group_by"], f"{path}.group_by")
        if field not in input_fields or not set(groups) <= input_fields or output_field in input_fields:
            raise ContractError(f"{path} contains an unresolved or duplicate field")
        output_fields.add(output_field)
    elif op == "aggregate":
        groups = require_string_list(item["group_by"], f"{path}.group_by")
        if not set(groups) <= input_fields:
            raise ContractError(f"{path}.group_by contains an unresolved field")
        metrics = item["metrics"]
        if not isinstance(metrics, Mapping) or not metrics:
            raise ContractError(f"{path}.metrics must be a nonempty object")
        output_fields = set(groups)
        for output_field, raw_metric in metrics.items():
            require_string(output_field, f"{path}.metrics key")
            metric = exact_object(raw_metric, {"field", "function"}, f"{path}.metrics.{output_field}")
            if require_string(metric["field"], f"{path}.metrics.{output_field}.field") not in input_fields:
                raise ContractError(f"{path}.metrics.{output_field}.field is unresolved")
            require_enum(metric["function"], {"sum", "count", "min", "max", "mean"}, f"{path}.metrics.{output_field}.function")
            output_fields.add(output_field)
    elif op == "filter":
        field = require_string(item["field"], f"{path}.field")
        if field not in input_fields:
            raise ContractError(f"{path}.field is unresolved")
        predicate = require_enum(
            item["predicate"],
            {"equals", "not_equals", "in", "not_in", "is_null", "is_not_null"},
            f"{path}.predicate",
        )
        if predicate in {"in", "not_in"} and not isinstance(item["value"], list):
            raise ContractError(f"{path}.value must be an array for {predicate}")
        if predicate in {"is_null", "is_not_null"} and item["value"] is not None:
            raise ContractError(f"{path}.value must be null for {predicate}")
    elif op == "sort":
        fields = require_string_list(item["fields"], f"{path}.fields", nonempty=True)
        if not set(fields) <= input_fields:
            raise ContractError(f"{path}.fields contains an unresolved field")
    join_keys[output] = join_keys[input_name] & output_fields
    return item, output_fields


def _validate_route_provenance(
    *,
    selections: Mapping[str, dict[str, object]],
    operations: list[object],
    datasets: Mapping[str, set[str]],
    routes: Mapping[tuple[str, str], str],
) -> None:
    """Enforce semantic provenance through every selected and derived field."""

    expected = {
        (dataset, field)
        for dataset, fields in datasets.items()
        for field in fields
    }
    if set(routes) != expected:
        raise ContractError(
            "mapping.field_routes must cover every selected and derived field exactly once"
        )

    for selection_id, selection in selections.items():
        for field in selection["fields"]:
            route = routes[(selection_id, field)]
            if field.casefold() in _STRUCTURAL_FIELDS and route not in {
                "structural_frame",
                "unsupported",
            }:
                raise ContractError(
                    f"mapping field route for {selection_id}.{field} launders structural semantics"
                )

    def require_same(
        source_dataset: str,
        source_field: str,
        output_dataset: str,
        output_field: str,
    ) -> None:
        source_route = routes[(source_dataset, source_field)]
        output_route = routes[(output_dataset, output_field)]
        if source_route != output_route:
            raise ContractError(
                f"semantic route provenance changed from "
                f"{source_dataset}.{source_field} to {output_dataset}.{output_field}"
            )
        if source_route == "unsupported":
            raise ContractError("unsupported fields cannot feed a derived dataset")

    for raw in operations:
        operation = dict(raw)
        op = str(operation["op"])
        output = str(operation["output"])
        if op == "join":
            for source_dataset in (str(operation["left"]), str(operation["right"])):
                for field in datasets[source_dataset]:
                    require_same(source_dataset, field, output, field)
            continue
        source = str(operation["input"])
        if op == "select":
            selected = set(operation["fields"])
            for field in selected:
                require_same(source, field, output, field)
            for field in datasets[source] - selected:
                if routes[(source, field)] != "unsupported":
                    raise ContractError(
                        f"dropped field {source}.{field} must use the unsupported route"
                    )
        elif op == "rename":
            names = dict(operation["fields"])
            for field in datasets[source]:
                require_same(source, field, output, str(names.get(field, field)))
        elif op == "flatten":
            for field, source_path in dict(operation["fields"]).items():
                require_same(
                    source,
                    str(source_path).split(".", 1)[0],
                    output,
                    str(field),
                )
        elif op == "wide_to_long":
            for field in operation["id_fields"]:
                require_same(source, str(field), output, str(field))
            value_routes = {
                routes[(source, str(field))] for field in operation["value_fields"]
            }
            if len(value_routes) != 1 or "unsupported" in value_routes:
                raise ContractError("wide_to_long value fields require one supported semantic route")
            value_route = next(iter(value_routes))
            if routes[(output, str(operation["name_field"]))] != value_route:
                raise ContractError("wide_to_long name field breaks semantic provenance")
            if routes[(output, str(operation["value_field"]))] != value_route:
                raise ContractError("wide_to_long value field breaks semantic provenance")
        elif op == "pivot":
            for field in operation["index_fields"]:
                require_same(source, str(field), output, str(field))
            value_route = routes[(source, str(operation["value_field"]))]
            if value_route == "unsupported":
                raise ContractError("unsupported pivot values cannot feed an output")
            for field in operation["columns"]:
                if routes[(output, str(field))] != value_route:
                    raise ContractError("pivot column breaks semantic provenance")
        elif op == "normalize_suppression":
            for field in datasets[source]:
                require_same(source, field, output, field)
            if routes[(output, str(operation["status_field"]))] != routes[
                (source, str(operation["field"]))
            ]:
                raise ContractError("suppression status breaks semantic provenance")
        elif op in {"derive_share", "normalize_weight"}:
            for field in datasets[source]:
                require_same(source, field, output, field)
            if op == "derive_share":
                ancestors = (
                    str(operation["count_field"]),
                    str(operation["denominator_field"]),
                )
            else:
                ancestors = (str(operation["field"]),)
            if any(routes[(source, field)] != "structural_frame" for field in ancestors):
                raise ContractError(
                    "evidence ancestry cannot become structural weight or share"
                )
            if routes[(output, str(operation["output_field"]))] != "structural_frame":
                raise ContractError("derived weight or share must remain structural")
        elif op == "aggregate":
            for field in operation["group_by"]:
                require_same(source, str(field), output, str(field))
            for output_field, metric in dict(operation["metrics"]).items():
                require_same(
                    source,
                    str(dict(metric)["field"]),
                    output,
                    str(output_field),
                )
        else:
            for field in datasets[source]:
                require_same(source, field, output, field)


def validate_authorized_mapping(
    payload: object,
    *,
    source_profile: dict[str, object],
) -> dict[str, object]:
    """Validate an approved, non-executable mapping before source data is read."""

    profile = validate_source_profile(source_profile)
    mapping = exact_object(payload, _TOP_LEVEL_KEYS, "mapping")
    _reject_executable_values(mapping)
    if mapping["schema_version"] != AUTHORIZED_MAPPING_VERSION:
        raise ContractError("mapping.schema_version is not supported")
    require_identifier(mapping["mapping_id"], "mapping.mapping_id")
    require_string(mapping["mapping_version"], "mapping.mapping_version")
    if _require_digest(mapping["source_profile_sha256"], "mapping.source_profile_sha256") != mapping_sha256(profile):
        raise ContractError("mapping.source_profile_sha256 does not match the exact source profile")
    if profile["privacy_risk"]:
        raise ContractError(
            "source profile contains privacy risk and requires private aggregation"
        )
    if profile["unresolved"]:
        raise ContractError("source profile contains unresolved blocking issues")
    if not profile["tables"]:
        raise ContractError("source profile contains no aggregate tables")
    if profile["decision"]["status"] not in {"ready_for_mapping", "needs_clarification"}:
        raise ContractError(
            "source profile decision must allow aggregate_transform before approval"
        )

    expected_hashes = {
        item["display_name"]: item["sha256"] for item in profile["inputs"]
    }
    input_hashes = mapping["input_hashes"]
    if not isinstance(input_hashes, Mapping):
        raise ContractError("mapping.input_hashes must be an object")
    normalized_hashes = {
        require_string(key, "mapping.input_hashes key"): _require_digest(value, f"mapping.input_hashes.{key}")
        for key, value in input_hashes.items()
    }
    if normalized_hashes != expected_hashes:
        raise ContractError("mapping.input_hashes must exactly match the source profile inputs")

    approval = exact_object(mapping["approval"], _APPROVAL_KEYS, "mapping.approval")
    if require_enum(approval["status"], {"approved"}, "mapping.approval.status") != "approved":
        raise ContractError("mapping.approval.status must be approved")
    require_string(approval["approved_by"], "mapping.approval.approved_by")
    require_timestamp(approval["approved_at"], "mapping.approval.approved_at")
    supplied_digest = _require_digest(approval["mapping_sha256"], "mapping.approval.mapping_sha256")
    if supplied_digest != mapping_sha256(mapping):
        raise ContractError("mapping.approval.mapping_sha256 does not match the null-digest canonical mapping")

    privacy = exact_object(mapping["privacy_requirements"], _PRIVACY_KEYS, "mapping.privacy_requirements")
    if not require_boolean(privacy["permission_confirmed"], "mapping.privacy_requirements.permission_confirmed"):
        raise ContractError("mapping privacy permission must be confirmed")
    if not require_boolean(privacy["aggregate_only"], "mapping.privacy_requirements.aggregate_only"):
        raise ContractError("authorized direct transformation must be aggregate-only")
    require_integer(privacy["minimum_cell_size"], "mapping.privacy_requirements.minimum_cell_size", minimum=1)
    require_string_list(privacy["prohibited_routes"], "mapping.privacy_requirements.prohibited_routes", nonempty=True)
    resolved = require_string_list(
        privacy["resolved_clarifications"],
        "mapping.privacy_requirements.resolved_clarifications",
    )
    required_clarifications = list(profile["decision"]["reasons"])
    if profile["decision"]["status"] == "needs_clarification":
        if any(item not in _PERMITTED_CLARIFICATIONS for item in required_clarifications):
            raise ContractError("source profile contains a non-permitted clarification issue")
        if set(resolved) != set(required_clarifications):
            raise ContractError("mapping must explicitly cover every clarification")
    elif resolved:
        raise ContractError("ready source profiles cannot declare clarification resolutions")

    profile_tables = {
        (item["file"], item["sheet"], item["record_path"]): item
        for item in profile["tables"]
    }
    selections: dict[str, dict[str, object]] = {}
    datasets: dict[str, set[str]] = {}
    join_keys: dict[str, set[str]] = {}
    selected_table_keys: set[tuple[object, object, object]] = set()
    for index, raw in enumerate(_array(mapping["selections"], "mapping.selections")):
        path = f"mapping.selections[{index}]"
        item = exact_object(raw, _SELECTION_KEYS, path)
        selection_id = require_identifier(item["selection_id"], f"{path}.selection_id")
        if selection_id in selections:
            raise ContractError(f"{path}.selection_id is duplicated")
        file_name = require_string(item["file"], f"{path}.file")
        if file_name not in expected_hashes:
            raise ContractError(f"{path}.file is not in the source profile")
        if _require_digest(item["file_sha256"], f"{path}.file_sha256") != expected_hashes[file_name]:
            raise ContractError(f"{path}.file_sha256 does not match the source profile")
        sheet = item["sheet"]
        if sheet is not None:
            require_string(sheet, f"{path}.sheet")
        record_path = require_string(item["record_path"], f"{path}.record_path")
        table = profile_tables.get((file_name, sheet, record_path))
        if table is None:
            raise ContractError(f"{path} does not resolve to an exact profiled table")
        table_key = (file_name, sheet, record_path)
        if table_key in selected_table_keys:
            raise ContractError(f"{path} duplicates a selected profiled table")
        selected_table_keys.add(table_key)
        fields = require_string_list(item["fields"], f"{path}.fields", nonempty=True)
        if not set(fields) <= set(table["field_names"]):
            raise ContractError(f"{path}.fields contains a field absent from the profile")
        unit = require_string(item["unit"], f"{path}.unit")
        denominator = require_string(item["denominator"], f"{path}.denominator")
        if unit.strip().casefold() in _AMBIGUOUS:
            raise ContractError(f"{path}.unit must be explicitly resolved")
        if denominator.strip().casefold() in _AMBIGUOUS:
            raise ContractError(f"{path}.denominator must be explicitly resolved")
        if not unit.casefold().startswith("aggregate_"):
            raise ContractError(f"{path}.unit must be an explicit aggregate unit")
        keys = require_string_list(item["aggregate_join_keys"], f"{path}.aggregate_join_keys")
        if not set(keys) <= set(fields):
            raise ContractError(f"{path}.aggregate_join_keys contains an unresolved field")
        selections[selection_id] = item
        datasets[selection_id] = set(fields)
        join_keys[selection_id] = set(keys)
    if not selections:
        raise ContractError("mapping.selections must not be empty")

    operation_ids: set[str] = set()
    for index, raw in enumerate(_array(mapping["operations"], "mapping.operations")):
        operation, output_fields = _validate_operation(raw, index, datasets, join_keys)
        operation_id = str(operation["operation_id"])
        if operation_id in operation_ids:
            raise ContractError(f"mapping.operations[{index}].operation_id is duplicated")
        operation_ids.add(operation_id)
        datasets[str(operation["output"])] = output_fields
    routes: dict[tuple[str, str], str] = {}
    for index, raw in enumerate(_array(mapping["field_routes"], "mapping.field_routes")):
        path = f"mapping.field_routes[{index}]"
        item = exact_object(raw, _FIELD_ROUTE_KEYS, path)
        dataset = require_identifier(item["dataset"], f"{path}.dataset")
        field = require_string(item["field"], f"{path}.field")
        route = require_enum(item["route"], set(SEMANTIC_ROUTES), f"{path}.route")
        key = (dataset, field)
        if dataset not in datasets or field not in datasets[dataset]:
            raise ContractError(f"{path} does not resolve to a mapped output field")
        if key in routes:
            raise ContractError(f"{path}: every output field must have exactly one semantic route")
        routes[key] = route
    _validate_route_provenance(
        selections=selections,
        operations=_array(mapping["operations"], "mapping.operations"),
        datasets=datasets,
        routes=routes,
    )

    output_datasets: set[str] = set()
    filenames: set[str] = set()
    output_sequences: dict[str, list[int]] = {}
    outcome_record_matches: set[str] = set()
    outcome_feedback_ids: set[str] = set()
    for index, raw in enumerate(_array(mapping["expected_outputs"], "mapping.expected_outputs")):
        path = f"mapping.expected_outputs[{index}]"
        item = exact_object(raw, _EXPECTED_OUTPUT_KEYS, path)
        dataset = require_identifier(item["dataset"], f"{path}.dataset")
        route = require_enum(item["route"], set(SEMANTIC_ROUTES) - {"unsupported"}, f"{path}.route")
        filename = require_string(item["filename"], f"{path}.filename")
        schema_version = require_string(item["schema_version"], f"{path}.schema_version")
        if dataset not in datasets:
            raise ContractError(f"{path}.dataset is unresolved")
        if not _CANONICAL_FILENAMES.fullmatch(filename):
            raise ContractError(f"{path}.filename is not a canonical output name")
        family = filename.removesuffix(".json").rsplit("-", 1)[0]
        if filename in filenames:
            raise ContractError(f"{path} duplicates an output filename")
        if dataset in output_datasets and family != "outcome-feedback":
            raise ContractError(f"{path} duplicates an output dataset")
        expected_route, expected_schema = CANONICAL_OUTPUT_REGISTRY[family]
        if (route, schema_version) != (expected_route, expected_schema):
            raise ContractError(f"{path} has an invalid route/schema registry pair")
        for field in datasets[dataset]:
            if routes.get((dataset, field)) != route:
                raise ContractError(
                    f"{path}: route laundering is forbidden; every output field must have exactly one semantic route matching the output"
                )
        _validate_output_metadata(
            item["metadata"],
            family=family,
            dataset_fields=datasets[dataset],
            path=f"{path}.metadata",
        )
        if family == "outcome-feedback":
            metadata = item["metadata"]
            match_key = sha256_json(metadata["record_match"])
            if match_key in outcome_record_matches:
                raise ContractError(
                    f"{path}.metadata.record_match duplicates another outcome output"
                )
            feedback_id = str(metadata["feedback_id"])
            if feedback_id in outcome_feedback_ids:
                raise ContractError(
                    f"{path}.metadata.feedback_id duplicates another outcome output"
                )
            outcome_record_matches.add(match_key)
            outcome_feedback_ids.add(feedback_id)
        output_datasets.add(dataset)
        filenames.add(filename)
        family, sequence_text = filename.removesuffix(".json").rsplit("-", 1)
        output_sequences.setdefault(family, []).append(int(sequence_text))
    if not output_datasets:
        raise ContractError("mapping.expected_outputs must not be empty")
    for family, sequences in output_sequences.items():
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise ContractError(
                f"mapping.expected_outputs {family} sequence must be contiguous from 0001"
            )
    ignored: set[tuple[object, object, object, str]] = set()
    for index, raw in enumerate(_array(mapping["ignored_fields"], "mapping.ignored_fields")):
        path = f"mapping.ignored_fields[{index}]"
        item = exact_object(raw, _IGNORED_FIELD_KEYS, path)
        file_name = require_string(item["file"], f"{path}.file")
        sheet = item["sheet"]
        if sheet is not None:
            require_string(sheet, f"{path}.sheet")
        record_path = require_string(item["record_path"], f"{path}.record_path")
        field = require_string(item["field"], f"{path}.field")
        require_string(item["reason"], f"{path}.reason")
        table_key = (file_name, sheet, record_path)
        profile_table = profile_tables.get(table_key)
        if profile_table is None or field not in profile_table["field_names"]:
            raise ContractError(f"{path}.field must resolve to a profiled table field")
        if table_key in selected_table_keys:
            selection = next(
                value for value in selections.values()
                if (value["file"], value["sheet"], value["record_path"]) == table_key
            )
            if field in selection["fields"]:
                raise ContractError(f"{path}.field is already selected")
        ignored_key = (*table_key, field)
        if ignored_key in ignored:
            raise ContractError(f"{path} is duplicated")
        ignored.add(ignored_key)
    for table_key, table in profile_tables.items():
        selected = set()
        if table_key in selected_table_keys:
            selection = next(
                value for value in selections.values()
                if (value["file"], value["sheet"], value["record_path"]) == table_key
            )
            selected = set(selection["fields"])
        covered_ignored = {
            field for file_name, sheet, record_path, field in ignored
            if (file_name, sheet, record_path) == table_key
        }
        if selected | covered_ignored != set(table["field_names"]):
            raise ContractError(
                "mapping must provide selected or ignored coverage for every profiled table field"
            )

    return mapping
