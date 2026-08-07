"""Deterministic finalist aggregation from validated complete-set responses."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
from typing import Any, Mapping, Sequence

from .responses import FINALIST_RUBRIC_KEYS, validate_response


APPROVED_STATES = {"approved", "approved_with_override"}


def _aware_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return value


def _require_ids(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must contain unique non-empty IDs")
    return list(value)


def _deterministic_proposal(
    method: str,
    screening: Mapping[str, Any],
    boundary: Mapping[str, Any] | None,
    requested_size: int,
    *,
    allow_complete_exploratory_override: bool = False,
) -> list[str]:
    if method == "complete_exposure":
        if boundary is not None:
            raise ValueError("complete_exposure does not use a boundary result")
        validity = screening.get("validity_status")
        selection = screening.get("selection_status")
        if validity == "exploratory" and selection == "unresolved":
            if allow_complete_exploratory_override:
                return []
            raise ValueError(
                "complete exploratory screening requires an audited approval override"
            )
        if validity != "valid":
            raise ValueError(
                f"complete screening validity {validity!r} prohibits finalist collection"
            )
        if screening.get("selection_status") != "resolved":
            return []
        proposal = _require_ids(
            screening.get("proposed_finalist_ids"),
            "screening proposed_finalist_ids",
        )
    elif method == "partial_exposure_maxdiff":
        if screening.get("validity_status") != "valid":
            raise ValueError("partial finalist collection requires valid screening")
        classifications = screening.get("classifications")
        if not isinstance(classifications, Mapping):
            raise ValueError("screening classifications must be an object")
        boundary_ids = sorted(
            creative_id
            for creative_id, classification in classifications.items()
            if classification == "boundary_candidate"
        )
        if boundary_ids:
            if boundary is None:
                raise ValueError("a frozen boundary group requires boundary results")
            if boundary.get("status") != "resolved":
                raise ValueError(
                    "finalist collection stops when the boundary result is invalid or unresolved"
                )
            proposal = _require_ids(
                boundary.get("proposed_finalist_ids"),
                "boundary proposed_finalist_ids",
            )
        else:
            declared = screening.get("proposed_finalist_ids")
            if declared:
                proposal = _require_ids(declared, "screening proposed_finalist_ids")
            else:
                proposal = sorted(
                    creative_id
                    for creative_id, classification in classifications.items()
                    if classification == "clear_finalist"
                )
    else:
        raise ValueError("manifest method is unsupported")
    if proposal and len(proposal) != requested_size:
        raise ValueError("deterministic proposal does not match requested shortlist size")
    return proposal


def validate_roster_approval(
    manifest: Mapping[str, Any], approval: Mapping[str, Any]
) -> tuple[list[str], Mapping[str, Any]]:
    """Validate the manifest-bound human roster decision before any dispatch.

    This structural gate is intentionally shared by dispatch and aggregation.
    Proposal agreement remains an aggregation concern because dispatch does not
    receive the screening/boundary result.
    """

    study_id = manifest.get("study_id")
    if not isinstance(study_id, str) or not study_id.strip():
        raise ValueError("manifest study_id is required")
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    if approval.get("study_id") != study_id:
        raise ValueError("approval study_id must exactly match the manifest")
    approval_method = approval.get("method")
    if approval_method is not None and approval_method != manifest.get("method"):
        raise ValueError("approval method must exactly match the manifest")

    requested_size = manifest.get("requested_shortlist_size")
    if (
        not isinstance(requested_size, int)
        or isinstance(requested_size, bool)
        or requested_size < 1
    ):
        raise ValueError("manifest requested_shortlist_size must be positive")
    approved_ids = _require_ids(
        approval.get("approved_finalist_ids"), "approval approved_finalist_ids"
    )
    if len(approved_ids) != requested_size:
        raise ValueError("approved finalist count must match requested shortlist size")
    outputs = manifest.get("outputs")
    creative_hashes = (
        outputs.get("creative_asset_hashes") if isinstance(outputs, Mapping) else None
    )
    if not isinstance(creative_hashes, Mapping) or not creative_hashes:
        raise ValueError("manifest outputs must lock a non-empty creative roster")
    if not set(approved_ids).issubset(creative_hashes):
        raise ValueError("approved finalist roster must be inside the manifest roster")

    decision = approval.get("roster_decision")
    if not isinstance(decision, Mapping):
        raise ValueError("approval roster_decision must be an object")
    status = decision.get("status")
    override = decision.get("override")
    if status not in APPROVED_STATES or not isinstance(override, bool):
        raise ValueError("approval roster_decision status/override is invalid")
    if (status == "approved_with_override") != override:
        raise ValueError("approved_with_override must match override=true")
    _aware_timestamp(decision.get("approved_at"), "roster_decision.approved_at")
    if not isinstance(decision.get("approved_by"), str) or not decision.get(
        "approved_by"
    ).strip():
        raise ValueError("roster_decision.approved_by is required")
    if override and (
        not isinstance(decision.get("override_reason"), str)
        or not decision.get("override_reason").strip()
    ):
        raise ValueError("an approval override requires override_reason")
    return approved_ids, decision


def aggregate_finalists(
    manifest: Mapping[str, Any],
    screening: Mapping[str, Any],
    approval: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile first-choice and exact rubric summaries from valid finalist records."""

    study_id = manifest.get("study_id")
    if not isinstance(study_id, str) or not study_id:
        raise ValueError("manifest study_id is required")
    for name, payload in (
        ("screening", screening),
        ("approval", approval),
        ("boundary", boundary),
    ):
        if payload is not None and payload.get("study_id") != study_id:
            raise ValueError(f"{name} study_id must match the manifest")

    method = manifest.get("method")
    requested_size = manifest.get("requested_shortlist_size")
    approved_ids, decision = validate_roster_approval(manifest, approval)
    override = bool(decision["override"])
    proposal = _deterministic_proposal(
        str(method),
        screening,
        boundary,
        requested_size,
        allow_complete_exploratory_override=(
            override and method == "complete_exposure"
        ),
    )
    if not override and approved_ids != proposal:
        raise ValueError(
            "non-override approval must exactly match the deterministic proposed roster"
        )
    if not proposal and not override:
        raise ValueError("an unresolved proposal requires an explicit human override")

    if not records:
        raise ValueError("finalist aggregation requires accepted finalist responses")
    seen_response_ids: set[str] = set()
    seen_replicate_ids: set[str] = set()
    for index, record in enumerate(records):
        errors = validate_response(record)
        if errors:
            raise ValueError(
                f"finalist response[{index}] is invalid: " + "; ".join(errors)
            )
        if record.get("record_type") != "finalist_response":
            raise ValueError(f"response[{index}] must be finalist_response")
        if record.get("method") != method:
            raise ValueError(f"response[{index}] method must match the manifest")
        if record.get("study_id") != study_id:
            raise ValueError(f"response[{index}] study_id must match the manifest")
        for field, seen in (
            ("response_id", seen_response_ids),
            ("synthetic_replicate_id", seen_replicate_ids),
        ):
            value = str(record.get(field))
            if value in seen:
                raise ValueError(f"duplicate finalist {field}: {value}")
            seen.add(value)
        for field in (
            "assigned_variation_ids",
            "shown_order",
            "final_preference_ranking",
        ):
            values = _require_ids(record.get(field), f"response[{index}].{field}")
            if len(values) != len(approved_ids) or set(values) != set(approved_ids):
                raise ValueError(
                    f"response[{index}].{field} must exactly match approved finalists"
                )
        reviews = record.get("finalist_reviews")
        if not isinstance(reviews, Sequence) or isinstance(reviews, (str, bytes)):
            raise ValueError(f"response[{index}].finalist_reviews must be an array")
        review_ids = [review.get("variation_id") for review in reviews]
        if len(review_ids) != len(approved_ids) or set(review_ids) != set(approved_ids):
            raise ValueError(
                f"response[{index}].finalist_reviews must exactly match approved finalists"
            )

    first_choice_counts = Counter({creative_id: 0 for creative_id in approved_ids})
    score_values: dict[str, dict[str, list[int]]] = {
        creative_id: {key: [] for key in FINALIST_RUBRIC_KEYS}
        for creative_id in approved_ids
    }
    for record in records:
        first_choice_counts[record["final_preference_ranking"][0]] += 1
        for review in record["finalist_reviews"]:
            creative_id = review["variation_id"]
            for key in FINALIST_RUBRIC_KEYS:
                score_values[creative_id][key].append(review["rubric_scores"][key])

    accepted_records = len(records)
    total_model_calls = sum(len(record["runtime_attempts"]) for record in records)
    rubric_summary: dict[str, dict[str, Any]] = {}
    for creative_id in approved_ids:
        rubric_summary[creative_id] = {}
        for key in FINALIST_RUBRIC_KEYS:
            values = score_values[creative_id][key]
            distribution = Counter(values)
            rubric_summary[creative_id][key] = {
                "mean": sum(values) / len(values),
                "distribution": {
                    str(score): distribution[score] for score in sorted(distribution)
                },
                "accepted_base": len(values),
            }

    shares = {
        creative_id: first_choice_counts[creative_id] / accepted_records
        for creative_id in approved_ids
    }
    if not math.isclose(sum(shares.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("conditional finalist shares did not normalize")
    return {
        "study_id": study_id,
        "method": method,
        "status": "valid",
        "approved_finalist_ids": approved_ids,
        "roster_decision": dict(decision),
        "deterministic_proposed_finalist_ids": proposal,
        "accepted_response_records": accepted_records,
        "accepted_unique_replicates": len(seen_replicate_ids),
        "unique_job_slots_consumed": len(seen_replicate_ids),
        "total_model_calls": total_model_calls,
        "first_choice_counts": dict(first_choice_counts),
        "conditional_first_choice_share": shares,
        "rubric_summary": rubric_summary,
        "interpretation_limits": [
            "First-choice shares are conditional only on the approved finalist set.",
            "Rubric summaries describe accepted synthetic response records, not people.",
            "Results do not estimate population preference or campaign performance.",
        ],
    }


__all__ = ["aggregate_finalists", "validate_roster_approval"]
