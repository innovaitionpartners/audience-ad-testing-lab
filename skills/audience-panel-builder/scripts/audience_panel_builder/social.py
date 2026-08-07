"""Normalize versioned Last30Days and mapped social exports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Mapping

from .common import (
    ContractError,
    canonical_json_bytes,
    get_path,
    numeric_mapping,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_timestamp,
    require_url,
    sanitize_excerpt,
    sha256_json,
    sha256_text,
)


SOCIAL_BATCH_SCHEMA_VERSION = "social-observation-batch-v1"
MAPPING_SCHEMA_VERSION = "social-export-mapping-v1"

# Stable, collision-resistant identifiers are shortened only for human-readable
# run outputs; full content SHA-256 values remain in every evidence record.
OBSERVATION_ID_HEX_LENGTH = 20
AUTHOR_TOKEN_HEX_LENGTH = 20
BATCH_ID_HEX_LENGTH = 16
FALLBACK_SOURCE_ID_HEX_LENGTH = 24
TITLE_MAXIMUM_CHARACTERS = 240
EXCERPT_MAXIMUM_CHARACTERS = 500
RELEVANCE_MINIMUM = 0.0
RELEVANCE_MAXIMUM = 1.0
LAST30DAYS_MAJOR_VERSION = 1
LAST30DAYS_MINIMUM_MINOR_VERSION = 2

_LAST30DAYS_REQUIRED = {
    "schema_version", "query", "generated_at", "window_days", "source_status",
    "freshness_verdicts", "clusters", "results",
}
_LAST30DAYS_RESULT_REQUIRED = {
    "candidate_id", "title", "source", "url", "summary", "engagement", "relevance_score",
}
_MAPPING_KEYS = {"schema_version", "batch", "records_path", "fields", "constants"}
_BATCH_KEYS = {
    "batch_id", "created_at", "provider", "collector", "collector_version",
    "run_or_dataset_id", "query", "window_start", "window_end", "collection_method",
    "access_route", "permitted_use", "sort_mode", "item_limit", "pagination",
    "completeness", "deduplication_control", "bot_spam_control",
}
_FIELD_KEYS = {
    "source_item_id", "platform", "source_url", "published_at", "unit_of_analysis",
    "title", "text", "relevance_score", "cluster_id", "role_status", "author_id",
    "engagement",
}
_CONSTANT_KEYS = {"platform", "unit_of_analysis", "role_status", "text_fidelity"}
_ROLE_STATUS = {"verified", "self_reported", "inferred", "unknown"}
_TEXT_FIDELITY = {
    "verbatim_public_text", "platform_caption", "transcript", "provider_summary",
}
_FAILURE_STATES = {
    "partial", "rate-limited", "auth-failed", "unreachable", "timeout",
    "schema-drift", "skipped-unconfigured", "error",
}


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_timestamp(value: Any, path: str) -> str | None:
    if value in (None, ""):
        return None
    text = require_string(value, path)
    if len(text) == 10:
        try:
            parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ContractError(f"{path} must be an ISO 8601 date or timestamp") from exc
        return _rfc3339(parsed)
    return _rfc3339(require_timestamp(text, path))


def _observation_id(platform: str, source_item_id: str, content_hash: str) -> str:
    digest = hashlib.sha256(
        f"{platform}\0{source_item_id}\0{content_hash}".encode("utf-8")
    ).hexdigest()[:OBSERVATION_ID_HEX_LENGTH]
    return f"observation-{digest}"


def _author_group_token(
    *,
    batch_id: str,
    input_sha256: str,
    platform: str,
    author_id: Any,
) -> str | None:
    if author_id in (None, ""):
        return None
    author_text = require_string(str(author_id), "$input.author_id")
    digest = hashlib.sha256(
        f"{batch_id}\0{input_sha256}\0{platform}\0{author_text}".encode("utf-8")
    ).hexdigest()[:AUTHOR_TOKEN_HEX_LENGTH]
    return f"author-group-{digest}"


def _freshness_by_candidate(values: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, raw in enumerate(require_array(values, "$.freshness_verdicts")):
        if not isinstance(raw, Mapping):
            raise ContractError(f"$.freshness_verdicts[{index}] must be an object")
        candidate_id = raw.get("candidate_id")
        verdict = raw.get("verdict")
        if isinstance(candidate_id, str) and isinstance(verdict, str):
            result[candidate_id] = verdict
    return result


def normalize_last30days(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError("$ must be an object")
    if payload.get("comparison") is True or payload.get("kind") == "discovery":
        raise ContractError("comparison and discovery exports require separate adapters")
    missing = sorted(_LAST30DAYS_REQUIRED - set(payload))
    if missing:
        raise ContractError(f"$ is missing fields: {', '.join(missing)}")
    version = require_string(payload["schema_version"], "$.schema_version")
    try:
        major_text, minor_text = version.split(".", 1)
        major, minor = int(major_text), int(minor_text)
    except (ValueError, TypeError) as exc:
        raise ContractError("$.schema_version must use major.minor numbering") from exc
    if (
        major != LAST30DAYS_MAJOR_VERSION
        or minor < LAST30DAYS_MINIMUM_MINOR_VERSION
    ):
        raise ContractError("$.schema_version must be a compatible Last30Days 1.2+ agent export")
    query = require_string(payload["query"], "$.query")
    generated = require_timestamp(payload["generated_at"], "$.generated_at")
    window_days = payload["window_days"]
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
        raise ContractError("$.window_days must be a positive integer")
    source_status = payload["source_status"]
    if not isinstance(source_status, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in source_status.items()
    ):
        raise ContractError("$.source_status must map source names to status strings")
    clusters = require_array(payload["clusters"], "$.clusters")
    freshness = _freshness_by_candidate(payload["freshness_verdicts"])
    input_sha256 = sha256_json(payload)
    batch_token = hashlib.sha256(
        f"{query}\0{_rfc3339(generated)}\0{input_sha256}".encode("utf-8")
    ).hexdigest()[:BATCH_ID_HEX_LENGTH]
    warnings = [
        f"{source}: {state}"
        for source, state in sorted(source_status.items())
        if state in _FAILURE_STATES
    ]
    observations: list[dict[str, Any]] = []
    for index, raw in enumerate(require_array(payload["results"], "$.results")):
        path = f"$.results[{index}]"
        if not isinstance(raw, Mapping):
            raise ContractError(f"{path} must be an object")
        missing_result = sorted(_LAST30DAYS_RESULT_REQUIRED - set(raw))
        if missing_result:
            raise ContractError(f"{path} is missing fields: {', '.join(missing_result)}")
        candidate_id = require_string(raw["candidate_id"], f"{path}.candidate_id")
        platform = require_string(raw["source"], f"{path}.source").casefold().replace(" ", "_")
        source_url = require_url(raw["url"], f"{path}.url", allow_empty=True)
        title = sanitize_excerpt(
            raw["title"],
            maximum=TITLE_MAXIMUM_CHARACTERS,
        )
        excerpt = sanitize_excerpt(
            raw["summary"],
            maximum=EXCERPT_MAXIMUM_CHARACTERS,
        )
        content_hash = sha256_text(
            "\0".join((platform, source_url, title, excerpt))
        )
        published = _optional_timestamp(raw.get("published_at"), f"{path}.published_at")
        relevance = raw["relevance_score"]
        if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
            raise ContractError(f"{path}.relevance_score must be numeric")
        relevance = max(
            RELEVANCE_MINIMUM,
            min(RELEVANCE_MAXIMUM, float(relevance)),
        )
        cluster = raw.get("cluster")
        if cluster is not None:
            if isinstance(cluster, bool) or not isinstance(cluster, int) or not 0 <= cluster < len(clusters):
                raise ContractError(f"{path}.cluster must resolve to $.clusters")
            cluster_id = f"cluster-{cluster + 1}"
        else:
            cluster_id = None
        engagement = numeric_mapping(raw["engagement"])
        engagement["prevalence_weight"] = 0
        quality_flags = ["provider_summary_not_verbatim"]
        if not source_url:
            quality_flags.append("missing_source_url")
        if published is None:
            quality_flags.append("missing_publication_time")
        verdict = freshness.get(candidate_id)
        if verdict is None:
            quality_flags.append("freshness_not_verified")
        observations.append(
            {
                "observation_id": _observation_id(platform, candidate_id, content_hash),
                "platform": platform,
                "source_item_id": candidate_id,
                "source_url": source_url,
                "published_at": published,
                "collected_at": _rfc3339(generated),
                "unit_of_analysis": "discovery_result",
                "title": title,
                "text_excerpt": excerpt,
                "text_fidelity": "provider_summary",
                "content_sha256": content_hash,
                "engagement": engagement,
                "relevance_score": relevance,
                "cluster_id": cluster_id,
                "role_status": "unknown",
                "author_group_token": None,
                "freshness_verdict": verdict,
                "json_pointer": f"/results/{index}",
                "use_constraints": [
                    "evidence_lead_or_qualitative_signal",
                    "verify_original_source_before_quote",
                    "no_prevalence_or_weighting",
                    "no_title_or_industry_inference",
                ],
                "quality_flags": quality_flags,
            }
        )
    observations.sort(key=lambda item: item["observation_id"])
    warnings.append(
        "author concentration could not be assessed from this export"
    )
    return {
        "schema_version": SOCIAL_BATCH_SCHEMA_VERSION,
        "batch_id": f"last30days-{batch_token}",
        "created_at": _rfc3339(generated),
        "source_adapter": "last30days-agent-json",
        "source_schema_version": version,
        "input_sha256": input_sha256,
        "query": query,
        "window_start": _rfc3339(generated - timedelta(days=window_days)),
        "window_end": _rfc3339(generated),
        "source_status": dict(sorted(source_status.items())),
        "collection": {
            "provider": "Last30Days",
            "collector": "last30days-agent-json",
            "collector_version": version,
            "run_or_dataset_id": f"last30days-{batch_token}",
            "collection_method": "multi_platform_recent_public_discovery",
            "access_route": "versioned_local_agent_export",
            "permitted_use": "requires_original_source_verification",
            "sort_mode": "provider_relevance_ranking",
            "item_limit": len(observations),
            "pagination": "provider_managed",
            "completeness": "source_status_bound_discovery_sample",
            "deduplication_control": "provider clustering plus downstream content hash",
            "bot_spam_control": "provider controls; downstream review required",
        },
        "observations": observations,
        "coverage_warnings": warnings,
    }


def _validate_mapping(mapping_payload: Any) -> dict[str, Any]:
    mapping = require_object(mapping_payload, _MAPPING_KEYS, "$mapping")
    if mapping["schema_version"] != MAPPING_SCHEMA_VERSION:
        raise ContractError(f"$mapping.schema_version must equal {MAPPING_SCHEMA_VERSION}")
    batch = require_object(mapping["batch"], _BATCH_KEYS, "$mapping.batch")
    require_identifier(batch["batch_id"], "$mapping.batch.batch_id")
    require_timestamp(batch["created_at"], "$mapping.batch.created_at")
    for key in _BATCH_KEYS - {"item_limit"}:
        require_string(batch[key], f"$mapping.batch.{key}")
    if isinstance(batch["item_limit"], bool) or not isinstance(batch["item_limit"], int) or batch["item_limit"] < 0:
        raise ContractError("$mapping.batch.item_limit must be a non-negative integer")
    require_string(mapping["records_path"], "$mapping.records_path", allow_empty=True)
    fields = require_object(mapping["fields"], _FIELD_KEYS, "$mapping.fields")
    for key in _FIELD_KEYS - {"engagement"}:
        if fields[key] is not None:
            require_string(fields[key], f"$mapping.fields.{key}", allow_empty=True)
    if not isinstance(fields["engagement"], Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in fields["engagement"].items()
    ):
        raise ContractError("$mapping.fields.engagement must map names to non-empty field paths")
    constants = require_object(mapping["constants"], _CONSTANT_KEYS, "$mapping.constants")
    for key in _CONSTANT_KEYS:
        if constants[key] is not None:
            require_string(constants[key], f"$mapping.constants.{key}")
    if constants["role_status"] is not None:
        require_enum(constants["role_status"], _ROLE_STATUS, "$mapping.constants.role_status")
    require_enum(constants["text_fidelity"], _TEXT_FIDELITY, "$mapping.constants.text_fidelity")
    return dict(mapping)


def normalize_mapped_export(payload: Any, mapping_payload: Any) -> dict[str, Any]:
    mapping = _validate_mapping(mapping_payload)
    records = get_path(payload, mapping["records_path"])
    records = require_array(records, "$input.records")
    batch = mapping["batch"]
    fields = mapping["fields"]
    constants = mapping["constants"]
    input_sha256 = sha256_json(payload)
    created = require_timestamp(batch["created_at"], "$mapping.batch.created_at")
    observations: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ContractError(f"$input.records[{index}] must be an object")
        platform_value = get_path(raw, fields["platform"])
        platform = platform_value if platform_value not in (None, "") else constants["platform"]
        platform = require_string(platform, f"$input.records[{index}].platform").casefold().replace(" ", "_")
        unit_value = get_path(raw, fields["unit_of_analysis"])
        unit = unit_value if unit_value not in (None, "") else constants["unit_of_analysis"]
        unit = require_string(unit, f"$input.records[{index}].unit_of_analysis")
        role_value = get_path(raw, fields["role_status"])
        role = role_value if role_value not in (None, "") else constants["role_status"]
        role = require_enum(role, _ROLE_STATUS, f"$input.records[{index}].role_status")
        text_raw = get_path(raw, fields["text"])
        excerpt = sanitize_excerpt(
            text_raw,
            maximum=EXCERPT_MAXIMUM_CHARACTERS,
        )
        title_raw = get_path(raw, fields["title"])
        title = sanitize_excerpt(
            title_raw or "",
            maximum=TITLE_MAXIMUM_CHARACTERS,
        )
        url_raw = get_path(raw, fields["source_url"])
        source_url = require_url(
            "" if url_raw is None else url_raw,
            f"$input.records[{index}].source_url",
            allow_empty=True,
        )
        published = _optional_timestamp(
            get_path(raw, fields["published_at"]),
            f"$input.records[{index}].published_at",
        )
        native_id = get_path(raw, fields["source_item_id"])
        if native_id in (None, ""):
            native_id = hashlib.sha256(
                f"{platform}\0{source_url}\0{published or ''}\0{excerpt}".encode("utf-8")
            ).hexdigest()[:FALLBACK_SOURCE_ID_HEX_LENGTH]
        native_id = require_string(str(native_id), f"$input.records[{index}].source_item_id")
        author_group_token = _author_group_token(
            batch_id=batch["batch_id"],
            input_sha256=input_sha256,
            platform=platform,
            author_id=get_path(raw, fields["author_id"]),
        )
        content_hash = sha256_text(
            "\0".join((platform, source_url, published or "", title, excerpt))
        )
        engagement = {
            name: value
            for name, path in fields["engagement"].items()
            if (
                (value := get_path(raw, path)) is not None
                and not isinstance(value, bool)
                and isinstance(value, (int, float))
            )
        }
        engagement["prevalence_weight"] = 0
        relevance = get_path(raw, fields["relevance_score"])
        if relevance is not None:
            if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
                raise ContractError(f"$input.records[{index}].relevance_score must be numeric")
            relevance = max(
                RELEVANCE_MINIMUM,
                min(RELEVANCE_MAXIMUM, float(relevance)),
            )
        cluster = get_path(raw, fields["cluster_id"])
        if cluster is not None:
            cluster = require_string(str(cluster), f"$input.records[{index}].cluster_id")
        quality_flags: list[str] = []
        if not source_url:
            quality_flags.append("missing_source_url")
        if published is None:
            quality_flags.append("missing_publication_time")
        if constants["text_fidelity"] == "provider_summary":
            quality_flags.append("provider_summary_not_verbatim")
        observations.append(
            {
                "observation_id": _observation_id(platform, native_id, content_hash),
                "platform": platform,
                "source_item_id": native_id,
                "source_url": source_url,
                "published_at": published,
                "collected_at": _rfc3339(created),
                "unit_of_analysis": unit,
                "title": title,
                "text_excerpt": excerpt,
                "text_fidelity": constants["text_fidelity"],
                "content_sha256": content_hash,
                "engagement": dict(sorted(engagement.items())),
                "relevance_score": relevance,
                "cluster_id": cluster,
                "role_status": role,
                "author_group_token": author_group_token,
                "freshness_verdict": None,
                "json_pointer": f"/{index}" if mapping["records_path"] == "" else f"/{mapping['records_path'].replace('.', '/')}/{index}",
                "use_constraints": [
                    "qualitative_context_only",
                    "no_prevalence_or_weighting",
                    "no_title_or_industry_inference_without_separate_evidence",
                ],
                "quality_flags": quality_flags,
            }
        )
    observations.sort(key=lambda item: item["observation_id"])
    coverage_warnings = []
    if observations and all(
        item["author_group_token"] is None for item in observations
    ):
        coverage_warnings.append(
            "author concentration could not be assessed from this export"
        )
    return {
        "schema_version": SOCIAL_BATCH_SCHEMA_VERSION,
        "batch_id": batch["batch_id"],
        "created_at": _rfc3339(created),
        "source_adapter": "mapped-social-export",
        "source_schema_version": mapping["schema_version"],
        "input_sha256": input_sha256,
        "query": batch["query"],
        "window_start": _rfc3339(require_timestamp(batch["window_start"], "$mapping.batch.window_start")),
        "window_end": _rfc3339(require_timestamp(batch["window_end"], "$mapping.batch.window_end")),
        "source_status": {constants["platform"] or "mapped_export": "ok"},
        "collection": {
            "provider": batch["provider"],
            "collector": batch["collector"],
            "collector_version": batch["collector_version"],
            "run_or_dataset_id": batch["run_or_dataset_id"],
            "collection_method": batch["collection_method"],
            "access_route": batch["access_route"],
            "permitted_use": batch["permitted_use"],
            "sort_mode": batch["sort_mode"],
            "item_limit": batch["item_limit"],
            "pagination": batch["pagination"],
            "completeness": batch["completeness"],
            "deduplication_control": batch["deduplication_control"],
            "bot_spam_control": batch["bot_spam_control"],
        },
        "observations": observations,
        "coverage_warnings": coverage_warnings,
    }
