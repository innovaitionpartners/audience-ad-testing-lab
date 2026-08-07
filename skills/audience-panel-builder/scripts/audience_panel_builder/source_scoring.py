"""Deterministic source-candidate scoring and hard gates."""

from __future__ import annotations

from typing import Any, Mapping

from .common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    require_url,
)


CANDIDATES_SCHEMA_VERSION = "audience-source-candidates-v1"
SCORED_SCHEMA_VERSION = "audience-scored-sources-v1"

# The maximum score is 21. Sixteen requires strong audience/decision fit and
# credible methods; twelve permits bounded use while rejecting weak evidence.
STRONG_SOURCE_SCORE = 16
USABLE_SOURCE_SCORE = 12

_TOP_KEYS = {"schema_version", "plan_id", "created_at", "candidates"}
_CANDIDATE_KEYS = {
    "candidate_id", "source_family_id", "lane", "title", "publisher", "source_url",
    "methodology_url", "publication_date", "field_dates", "population", "geography",
    "sample_size", "collection_method", "access_route", "reuse_status", "assessments",
    "social_collection", "upstream_source_ids", "evidence_item_ids", "notes",
}
_ASSESSMENT_KEYS = {
    "audience_match", "decision_match", "methodology_transparency",
    "collection_quality", "recency", "geography_match", "subgroup_usefulness",
    "permitted_use",
}
_SOCIAL_KEYS = {
    "platform", "query", "window_start", "window_end", "timezone", "unit_of_analysis",
    "sort_mode", "item_limit", "pagination", "returned_item_count", "completeness",
    "collector", "collector_version", "run_or_dataset_id", "deduplication_control",
    "bot_spam_control", "engagement_available",
}
_LANES = {"structural", "survey", "social_community", "first_party", "performance"}
_REUSE = {"allowed", "conditional", "prohibited", "unknown"}
# Three-point dimensions are the primary fit/method signals; two-point
# dimensions refine scope and usefulness without letting geography or subgroup
# detail overpower direct audience and decision relevance. Permitted use is
# scored for transparency but remains an independent hard gate.
_ASSESSMENT_SCORES = {
    "audience_match": {"exact": 3, "partial": 2, "adjacent": 1, "unknown": 0},
    "decision_match": {"exact": 3, "partial": 2, "indirect": 1, "none": 0},
    "methodology_transparency": {"transparent": 3, "documented": 2, "limited": 1, "opaque": 0},
    "collection_quality": {
        "probability_or_census": 3,
        "robust_documented": 3,
        "documented": 2,
        "qualitative_curated": 2,
        "unstructured": 1,
        "unknown": 0,
    },
    "recency": {"current": 3, "acceptable": 2, "legacy": 1, "stale": 0},
    "geography_match": {"exact": 2, "partial": 1, "not_applicable": 1, "mismatch": 0},
    "subgroup_usefulness": {"direct": 2, "limited": 1, "none": 0},
    "permitted_use": {"allowed": 2, "conditional": 1, "prohibited": 0, "unknown": 0},
}


def _integer_or_null(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{path} must be null or a non-negative integer")
    return value


def _validate_social(value: Any, path: str) -> Mapping[str, Any]:
    record = require_object(value, _SOCIAL_KEYS, path)
    for key in _SOCIAL_KEYS - {
        "item_limit", "returned_item_count", "engagement_available",
    }:
        require_string(record[key], f"{path}.{key}")
    _integer_or_null(record["item_limit"], f"{path}.item_limit")
    _integer_or_null(record["returned_item_count"], f"{path}.returned_item_count")
    if not isinstance(record["engagement_available"], bool):
        raise ContractError(f"{path}.engagement_available must be boolean")
    require_timestamp(record["window_start"], f"{path}.window_start")
    require_timestamp(record["window_end"], f"{path}.window_end")
    return record


def _validate_candidates(payload: Any) -> dict[str, Any]:
    top = require_object(payload, _TOP_KEYS, "$")
    if top["schema_version"] != CANDIDATES_SCHEMA_VERSION:
        raise ContractError(f"$.schema_version must equal {CANDIDATES_SCHEMA_VERSION}")
    require_identifier(top["plan_id"], "$.plan_id")
    require_timestamp(top["created_at"], "$.created_at")
    seen: set[str] = set()
    for index, raw in enumerate(require_array(top["candidates"], "$.candidates", nonempty=True)):
        path = f"$.candidates[{index}]"
        candidate = require_object(raw, _CANDIDATE_KEYS, path)
        candidate_id = require_identifier(candidate["candidate_id"], f"{path}.candidate_id")
        if candidate_id in seen:
            raise ContractError(f"{path}.candidate_id is duplicated")
        seen.add(candidate_id)
        require_identifier(candidate["source_family_id"], f"{path}.source_family_id")
        lane = require_enum(candidate["lane"], _LANES, f"{path}.lane")
        for key in (
            "title", "publisher", "publication_date", "field_dates", "population",
            "geography", "collection_method", "access_route", "notes",
        ):
            require_string(candidate[key], f"{path}.{key}")
        require_url(candidate["source_url"], f"{path}.source_url")
        require_url(candidate["methodology_url"], f"{path}.methodology_url")
        _integer_or_null(candidate["sample_size"], f"{path}.sample_size")
        require_enum(candidate["reuse_status"], _REUSE, f"{path}.reuse_status")
        assessments = require_object(candidate["assessments"], _ASSESSMENT_KEYS, f"{path}.assessments")
        for dimension, score_map in _ASSESSMENT_SCORES.items():
            require_enum(
                assessments[dimension], set(score_map), f"{path}.assessments.{dimension}"
            )
        upstream = require_string_array(
            candidate["upstream_source_ids"], f"{path}.upstream_source_ids"
        )
        for upstream_index, source_id in enumerate(upstream):
            require_identifier(
                source_id, f"{path}.upstream_source_ids[{upstream_index}]"
            )
        item_ids = require_string_array(
            candidate["evidence_item_ids"],
            f"{path}.evidence_item_ids",
            nonempty=True,
        )
        for item_index, item_id in enumerate(item_ids):
            require_identifier(
                item_id, f"{path}.evidence_item_ids[{item_index}]"
            )
        if lane == "social_community":
            if candidate["social_collection"] is None:
                raise ContractError(f"{path}.social_collection is required for social evidence")
            _validate_social(candidate["social_collection"], f"{path}.social_collection")
        elif candidate["social_collection"] is not None:
            raise ContractError(f"{path}.social_collection must be null outside social evidence")
    return dict(top)


def _score(candidate: Mapping[str, Any]) -> int:
    return sum(
        _ASSESSMENT_SCORES[dimension][candidate["assessments"][dimension]]
        for dimension in _ASSESSMENT_SCORES
    )


def _base_decision(candidate: Mapping[str, Any], score: int) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if (
        candidate["reuse_status"] in {"prohibited", "unknown"}
        or candidate["assessments"]["permitted_use"] in {"prohibited", "unknown"}
    ):
        return "reject", ["Permitted use is prohibited or unresolved."]
    if candidate["assessments"]["audience_match"] == "unknown":
        reasons.append("Audience match is unresolved.")
    if candidate["assessments"]["decision_match"] in {"none", "indirect"}:
        reasons.append("Decision relevance is weak.")
    if candidate["assessments"]["methodology_transparency"] == "opaque":
        reasons.append("Methodology is opaque.")
    if candidate["lane"] == "social_community":
        reasons.append("Use only as qualitative context or an evidence lead; prevalence weight is zero.")
        if score >= USABLE_SOURCE_SCORE:
            return "accept_as_qualitative", reasons
        return "lead_only", reasons + ["The source does not clear the qualitative acceptance score."]
    if score >= STRONG_SOURCE_SCORE:
        return "accept", reasons
    if score >= USABLE_SOURCE_SCORE:
        return "accept_with_limits", reasons + ["Use only within the documented limits."]
    return "reject", reasons + ["The source does not clear the minimum acceptance score."]


def score_source_candidates(payload: Any) -> dict[str, Any]:
    top = _validate_candidates(payload)
    working: list[dict[str, Any]] = []
    for candidate in top["candidates"]:
        score = _score(candidate)
        tier = (
            "strong"
            if score >= STRONG_SOURCE_SCORE
            else "usable"
            if score >= USABLE_SOURCE_SCORE
            else "weak"
        )
        decision, reasons = _base_decision(candidate, score)
        working.append(
            {
                **candidate,
                "score": score,
                "tier": tier,
                "decision": decision,
                "decision_reasons": reasons,
            }
        )

    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in working:
        keys = {f"url:{candidate['source_url']}"}
        keys.update(f"upstream:{source_id}" for source_id in candidate["upstream_source_ids"])
        for key in keys:
            groups.setdefault(key, []).append(candidate)
    duplicate_ids: set[str] = set()
    for group in groups.values():
        active = [
            candidate for candidate in group
            if candidate["decision"] not in {"reject", "lead_only"}
        ]
        if len(active) < 2:
            continue
        active.sort(key=lambda item: (-item["score"], item["candidate_id"]))
        for duplicate in active[1:]:
            duplicate_ids.add(duplicate["candidate_id"])
    for candidate in working:
        if candidate["candidate_id"] in duplicate_ids:
            candidate["decision"] = "reject"
            candidate["decision_reasons"] = [
                *candidate["decision_reasons"],
                "Duplicate or dependent evidence family; keep the highest-scoring source.",
            ]
    working.sort(key=lambda item: (-item["score"], item["candidate_id"]))
    return {
        "schema_version": SCORED_SCHEMA_VERSION,
        "plan_id": top["plan_id"],
        "created_at": top["created_at"],
        "candidates": working,
        "summary": {
            "total": len(working),
            "accepted": sum(
                candidate["decision"]
                in {"accept", "accept_with_limits", "accept_as_qualitative"}
                for candidate in working
            ),
            "lead_only": sum(candidate["decision"] == "lead_only" for candidate in working),
            "rejected": sum(candidate["decision"] == "reject" for candidate in working),
        },
    }
