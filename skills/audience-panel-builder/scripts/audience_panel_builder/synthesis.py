"""Validate the auditable bridge from evidence items to research findings."""

from __future__ import annotations

from typing import Any

from .common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    sha256_json,
)
from .evidence import validate_evidence_ledger, validate_finding_support


SYNTHESIS_SCHEMA_VERSION = "audience-synthesis-matrix-v1"

_TOP_KEYS = {
    "schema_version", "plan_id", "created_at", "ledger_sha256", "questions",
}
_QUESTION_KEYS = {"question_id", "research_question", "findings"}
_FINDING_KEYS = {
    "finding_id", "statement", "category", "evidence_item_ids",
    "supporting_item_ids", "qualifying_item_ids", "contradicting_item_ids",
    "integration_state", "methodological_limitations", "relevance",
    "coherence", "adequacy", "confidence", "confidence_reason",
    "inference_boundary", "marketer_implication", "creative_implications",
    "segment_decision",
}
_FINDING_CATEGORIES = {
    "pain_points_challenges", "motivations_goals", "questions_being_asked",
    "information_sources_influences", "decision_criteria", "buying_triggers",
    "current_approaches", "fears_objections", "emerging_trends_awareness",
    "proof_needs", "media_behaviors",
}
_INTEGRATION_STATES = {
    "convergent", "complementary", "mixed", "discordant", "single_source",
}
_CONCERN_LEVELS = {
    "no_serious_concerns", "minor_concerns", "major_concerns", "unknown",
}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_SEGMENT_DECISIONS = {
    "candidate", "emerging_hypothesis", "gap_only", "not_segment_relevant",
}


def validate_synthesis_matrix(
    payload: Any,
    ledger_payload: Any,
    support_payload: Any,
) -> dict[str, Any]:
    """Validate exact item lineage while leaving synthesis judgment to reviewers."""

    ledger = validate_evidence_ledger(ledger_payload)
    support = validate_finding_support(support_payload, ledger)
    matrix = require_object(payload, _TOP_KEYS, "$")
    if matrix["schema_version"] != SYNTHESIS_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {SYNTHESIS_SCHEMA_VERSION}"
        )
    require_identifier(matrix["plan_id"], "$.plan_id")
    require_timestamp(matrix["created_at"], "$.created_at")
    if matrix["ledger_sha256"] != sha256_json(ledger):
        raise ContractError("$.ledger_sha256 does not match the exact ledger")

    ledger_items = {
        item["evidence_item_id"]: item
        for item in ledger["evidence_items"]
    }
    role_items: dict[str, dict[str, set[str]]] = {}
    for row in support["findings"]:
        finding_roles = role_items.setdefault(
            row["finding_id"],
            {"supports": set(), "qualifies": set(), "contradicts": set()},
        )
        finding_roles[row["support_role"]].update(row["evidence_item_ids"])

    matrix_finding_ids: set[str] = set()
    for question_index, raw_question in enumerate(
        require_array(matrix["questions"], "$.questions", nonempty=True)
    ):
        question_path = f"$.questions[{question_index}]"
        question = require_object(raw_question, _QUESTION_KEYS, question_path)
        require_identifier(question["question_id"], f"{question_path}.question_id")
        require_string(
            question["research_question"],
            f"{question_path}.research_question",
        )
        for finding_index, raw_finding in enumerate(
            require_array(
                question["findings"],
                f"{question_path}.findings",
                nonempty=True,
            )
        ):
            path = f"{question_path}.findings[{finding_index}]"
            finding = require_object(raw_finding, _FINDING_KEYS, path)
            finding_id = require_identifier(
                finding["finding_id"],
                f"{path}.finding_id",
            )
            if finding_id in matrix_finding_ids:
                raise ContractError(f"{path}.finding_id is duplicated")
            matrix_finding_ids.add(finding_id)
            if finding_id not in role_items:
                raise ContractError(
                    f"{path}.finding_id has no exact finding-support record"
                )
            require_string(finding["statement"], f"{path}.statement")
            require_enum(
                finding["category"],
                _FINDING_CATEGORIES,
                f"{path}.category",
            )
            evidence_ids = set(
                require_string_array(
                    finding["evidence_item_ids"],
                    f"{path}.evidence_item_ids",
                    nonempty=True,
                )
            )
            lists_by_role = {
                "supports": set(
                    require_string_array(
                        finding["supporting_item_ids"],
                        f"{path}.supporting_item_ids",
                    )
                ),
                "qualifies": set(
                    require_string_array(
                        finding["qualifying_item_ids"],
                        f"{path}.qualifying_item_ids",
                    )
                ),
                "contradicts": set(
                    require_string_array(
                        finding["contradicting_item_ids"],
                        f"{path}.contradicting_item_ids",
                    )
                ),
            }
            unresolved = sorted(evidence_ids - set(ledger_items))
            if unresolved:
                raise ContractError(
                    f"{path}.evidence_item_ids do not resolve: {', '.join(unresolved)}"
                )
            expected_roles = role_items[finding_id]
            if lists_by_role != expected_roles:
                raise ContractError(
                    f"{path} support, qualification, and contradiction item lists must match audience-finding-support-v1"
                )
            if evidence_ids != set().union(*lists_by_role.values()):
                raise ContractError(
                    f"{path}.evidence_item_ids must equal the union of all support-role item lists"
                )
            integration = require_enum(
                finding["integration_state"],
                _INTEGRATION_STATES,
                f"{path}.integration_state",
            )
            if lists_by_role["contradicts"] and integration == "convergent":
                raise ContractError(
                    f"{path}.integration_state cannot be convergent when contradicting items are recorded"
                )
            if integration == "discordant" and not lists_by_role["contradicts"]:
                raise ContractError(
                    f"{path}.integration_state discordant requires a contradicting item"
                )
            concern_values = []
            for key in (
                "methodological_limitations", "relevance", "coherence", "adequacy"
            ):
                concern_values.append(
                    require_enum(
                        finding[key],
                        _CONCERN_LEVELS,
                        f"{path}.{key}",
                    )
                )
            confidence = require_enum(
                finding["confidence"],
                _CONFIDENCE_LEVELS,
                f"{path}.confidence",
            )
            if confidence == "high" and any(
                value in {"major_concerns", "unknown"}
                for value in concern_values
            ):
                raise ContractError(
                    f"{path}.confidence cannot be high with major or unknown confidence-component concerns"
                )
            if confidence == "high" and all(
                ledger_items[item_id]["item_type"].startswith("social_")
                for item_id in evidence_ids
            ):
                raise ContractError(
                    f"{path}.confidence cannot be high when support is social-only"
                )
            for key in (
                "confidence_reason", "inference_boundary",
                "marketer_implication",
            ):
                require_string(finding[key], f"{path}.{key}")
            require_string_array(
                finding["creative_implications"],
                f"{path}.creative_implications",
                nonempty=True,
            )
            require_enum(
                finding["segment_decision"],
                _SEGMENT_DECISIONS,
                f"{path}.segment_decision",
            )

    missing = sorted(set(role_items) - matrix_finding_ids)
    if missing:
        raise ContractError(
            "synthesis matrix omits supported findings: " + ", ".join(missing)
        )
    return dict(matrix)
