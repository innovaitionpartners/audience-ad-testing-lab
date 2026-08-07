"""Validation for staged synthetic-replicate jobs and response records."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Callable, Mapping, Sequence


RECORD_TYPES = {
    "screening_response",
    "boundary_response",
    "finalist_response",
}
METHODS = {"complete_exposure", "partial_exposure_maxdiff"}
REACTION_PROTOCOLS = {"progressive_reveal", "reflective_reaction_caveat"}
REACTION_LABELS = {"immediate", "reflective"}
JUDGMENT_STATUSES = {"judged", "unable_to_judge"}
PROVENANCE_STATUSES = {"observed", "estimated", "experimental"}
FINALIST_RUBRIC_KEYS = (
    "comprehension",
    "relevance",
    "credibility",
    "offer_appeal",
    "motivation",
    "friction",
    "attention_potential",
    "overall",
)


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_string(
    payload: Mapping[str, Any], key: str, errors: list[str], *, prefix: str = ""
) -> None:
    if not _non_empty_string(payload.get(key)):
        errors.append(f"{prefix}{key} must be a non-empty string")


def _validate_id_array(value: Any, field: str) -> list[str]:
    if not _is_array(value) or not value:
        return [f"{field} must be a non-empty array"]
    if not all(_non_empty_string(item) for item in value):
        return [f"{field} values must be non-empty strings"]
    if len(set(value)) != len(value):
        return [f"{field} values must be unique"]
    return []


def _validate_context_provenance(value: Any) -> list[str]:
    errors: list[str] = []
    if not _is_array(value) or not value:
        return ["context_attribute_provenance must be a non-empty array"]
    for index, item in enumerate(value):
        prefix = f"context_attribute_provenance[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        attribute = item.get("attribute", item.get("name"))
        if not _non_empty_string(attribute):
            errors.append(f"{prefix}.attribute must be a non-empty string")
        if not _non_empty_string(item.get("value")):
            errors.append(f"{prefix}.value must be a non-empty string")
        if item.get("status") not in PROVENANCE_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        evidence = item.get("source_evidence", item.get("evidence_ids"))
        if (
            not _is_array(evidence)
            or not all(_non_empty_string(source) for source in evidence)
            or (not evidence and item.get("status") != "experimental")
        ):
            errors.append(
                f"{prefix}.source_evidence must contain stable source IDs unless status is experimental"
            )
    return errors


def _validate_reaction_protocol(response: Mapping[str, Any]) -> list[str]:
    protocol = response.get("reaction_protocol")
    errors: list[str] = []
    if protocol not in REACTION_PROTOCOLS:
        errors.append("reaction_protocol is invalid")
    reaction_records: list[Any] = []
    for field in ("per_creative_reactions", "finalist_reviews"):
        value = response.get(field)
        if _is_array(value):
            reaction_records.extend(value)
    if protocol != "progressive_reveal" and any(
        isinstance(item, Mapping) and item.get("reaction_label") == "immediate"
        for item in reaction_records
    ):
        errors.append("immediate reactions require progressive_reveal")
    return errors


def _validate_assignment_fields(
    variation_ids: Any,
    shown_order: Any,
    blind_labels: Any,
    *,
    variation_field: str,
) -> list[str]:
    errors = _validate_id_array(variation_ids, variation_field)
    errors.extend(_validate_id_array(shown_order, "shown_order"))
    if _is_array(variation_ids) and _is_array(shown_order):
        if len(variation_ids) != len(shown_order) or set(variation_ids) != set(shown_order):
            errors.append(f"shown_order must be an exact permutation of {variation_field}")
    if not isinstance(blind_labels, Mapping):
        errors.append("blind_labels must be an object")
    elif _is_array(variation_ids):
        if set(blind_labels) != set(variation_ids):
            errors.append(f"blind_labels keys must exactly match {variation_field}")
        labels = list(blind_labels.values())
        if not all(_non_empty_string(label) for label in labels):
            errors.append("blind_labels values must be non-empty strings")
        elif len(set(labels)) != len(labels):
            errors.append("blind_labels values must be unique")
    return errors


def validate_job(job: Mapping[str, Any]) -> list[str]:
    """Validate Task 3's assignment core plus Task 4 dispatch enrichment."""

    if not isinstance(job, Mapping):
        return ["job must be an object"]
    errors: list[str] = []
    for key in (
        "study_id",
        "response_id",
        "method",
        "synthetic_replicate_id",
        "dispatch_id",
        "persona_archetype_id",
        "segment_id",
    ):
        _require_string(job, key, errors)
    if "context_stratum_id" in job and not _non_empty_string(
        job.get("context_stratum_id")
    ):
        errors.append("context_stratum_id must be a non-empty string when supplied")
    v3_identity_fields = (
        "audience_slot_id",
        "grounded_profile_id",
        "profile_snapshot_sha256",
    )
    present_v3_fields = [
        field for field in v3_identity_fields if field in job
    ]
    has_v3_marker = any(
        field in job
        for field in ("audience_slot_id", "profile_snapshot_sha256")
    )
    if has_v3_marker and len(present_v3_fields) != len(
        v3_identity_fields
    ):
        errors.append("v3 audience identity fields must be supplied together")
    elif has_v3_marker:
        if (
            not _non_empty_string(job.get("audience_slot_id"))
            or job.get("audience_slot_id")
            != job.get("synthetic_replicate_id")
        ):
            errors.append(
                "audience_slot_id must exactly match synthetic_replicate_id"
            )
        if not _non_empty_string(job.get("grounded_profile_id")):
            errors.append("grounded_profile_id must be a non-empty string")
        snapshot_hash = job.get("profile_snapshot_sha256")
        if (
            not isinstance(snapshot_hash, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
        ):
            errors.append(
                "profile_snapshot_sha256 must be a prefixed SHA-256"
            )

    record_type = job.get("record_type")
    if record_type not in RECORD_TYPES:
        errors.append(f"unsupported record_type: {record_type}")
    method = job.get("method")
    if method not in METHODS:
        errors.append("method must be complete_exposure or partial_exposure_maxdiff")
    if not isinstance(job.get("profile_snapshot"), Mapping) or not job.get(
        "profile_snapshot"
    ):
        errors.append("profile_snapshot must be a non-empty object")
    errors.extend(_validate_context_provenance(job.get("context_attribute_provenance")))
    if job.get("worker_context_isolation") not in {
        "isolated",
        "shared_context_fallback",
    }:
        errors.append("worker_context_isolation is invalid")
    if job.get("human_sample_independence") is not False:
        errors.append("human_sample_independence must be false")

    variation_ids = job.get("variation_ids")
    shown_order = job.get("shown_order")
    errors.extend(
        _validate_assignment_fields(
            variation_ids,
            shown_order,
            job.get("blind_labels"),
            variation_field="variation_ids",
        )
    )
    if record_type == "screening_response":
        if method == "partial_exposure_maxdiff" and (
            not _is_array(variation_ids) or len(variation_ids) != 4
        ):
            errors.append(
                "partial_exposure_maxdiff screening jobs must assign exactly four variations"
            )
        if method == "complete_exposure" and (
            not _is_array(variation_ids) or not 2 <= len(variation_ids) <= 6
        ):
            errors.append(
                "complete_exposure screening jobs must assign between two and six variations"
            )
    if record_type == "boundary_response" and (
        not _is_array(variation_ids) or len(variation_ids) != 2
    ):
        errors.append("boundary jobs must assign exactly two variations")
    if record_type == "boundary_response" and method != "partial_exposure_maxdiff":
        errors.append("boundary jobs require partial_exposure_maxdiff")
    if record_type == "finalist_response" and (
        not _is_array(variation_ids) or not 2 <= len(variation_ids) <= 6
    ):
        errors.append("finalist jobs must assign between two and six variations")

    reaction_protocol = job.get("reaction_protocol")
    if reaction_protocol not in REACTION_PROTOCOLS:
        errors.append("reaction_protocol is invalid")
    prompts = job.get("reaction_prompts")
    if not _is_array(prompts) or not all(_non_empty_string(prompt) for prompt in prompts):
        errors.append("reaction_prompts must be an array of non-empty rendered prompts")
    elif not _is_array(shown_order) or len(prompts) != len(shown_order):
        errors.append("reaction_prompts must match shown_order length")
    if not _non_empty_string(job.get("comparison_prompt")):
        errors.append("comparison_prompt must be a non-empty rendered prompt")
    if "panelist_reviews" in job or "prompt" in job:
        errors.append("obsolete one-shot review fields are not allowed in enriched jobs")
    return errors


def _attempt_value(attempt: Mapping[str, Any], primary: str, alias: str) -> Any:
    return attempt.get(primary, attempt.get(alias))


def _attempt_group(attempt: Mapping[str, Any]) -> tuple[str, int | None] | None:
    stage = attempt.get("stage")
    if stage == "reaction":
        position = attempt.get("position_seen")
        if isinstance(position, int) and not isinstance(position, bool):
            return (stage, position)
        return None
    if stage == "comparison":
        return (stage, None)
    return None


def _runtime_attempt_state(
    response: Mapping[str, Any],
) -> tuple[list[str], dict[tuple[str, int | None], str]]:
    errors: list[str] = []
    attempts = response.get("runtime_attempts")
    if not _is_array(attempts) or not attempts:
        return ["runtime_attempts must be a non-empty array"], {}

    seen_attempt_ids: set[str] = set()
    seen_provider_ids: set[str] = set()
    grouped: dict[tuple[str, int | None], list[tuple[int, str, str]]] = defaultdict(list)
    for index, attempt in enumerate(attempts):
        prefix = f"runtime_attempts[{index}]"
        if not isinstance(attempt, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        attempt_id = attempt.get("attempt_id")
        if not _non_empty_string(attempt_id):
            errors.append(f"{prefix}.attempt_id must be a stable non-empty string")
        elif attempt_id in seen_attempt_ids:
            errors.append(f"duplicate runtime attempt_id: {attempt_id}")
        else:
            seen_attempt_ids.add(attempt_id)
        provider_return_id = _attempt_value(
            attempt, "provider_return_id", "raw_return_id"
        )
        if not _non_empty_string(provider_return_id):
            errors.append(f"{prefix}.provider_return_id must be a stable non-empty string")
        elif provider_return_id in seen_provider_ids:
            errors.append(f"duplicate provider_return_id: {provider_return_id}")
        else:
            seen_provider_ids.add(provider_return_id)

        group = _attempt_group(attempt)
        if group is None:
            errors.append(f"{prefix} must identify reaction position or comparison stage")
            continue
        attempt_number = _attempt_value(attempt, "attempt_number", "attempt")
        if (
            not isinstance(attempt_number, int)
            or isinstance(attempt_number, bool)
            or attempt_number not in {1, 2}
        ):
            errors.append("runtime attempts permit exactly one retry")
            continue
        outcome = _attempt_value(attempt, "outcome", "status")
        if outcome not in {"accepted", "rejected"}:
            errors.append(f"{prefix}.outcome must be accepted or rejected")
            continue
        validation_errors = attempt.get("validation_errors")
        if outcome == "rejected":
            if (
                not _is_array(validation_errors)
                or not validation_errors
                or not all(_non_empty_string(error) for error in validation_errors)
            ):
                errors.append(
                    f"{prefix}.validation_errors must be a non-empty array for rejected attempts"
                )
        elif validation_errors != []:
            errors.append(
                f"{prefix}.validation_errors must be an empty array for accepted attempts"
            )
        grouped[group].append((attempt_number, outcome, str(provider_return_id)))

    accepted: dict[tuple[str, int | None], str] = {}
    for group, entries in grouped.items():
        entries.sort(key=lambda entry: entry[0])
        numbers = [entry[0] for entry in entries]
        if len(numbers) != len(set(numbers)):
            errors.append(f"duplicate runtime attempt number for {group}")
            continue
        if numbers not in ([1], [1, 2]):
            errors.append("runtime attempts must start at one and permit exactly one retry")
        if len(entries) == 2 and entries[0][1] != "rejected":
            errors.append("a retry is allowed only after a rejected first attempt")
        if entries[-1][1] != "accepted":
            errors.append(f"runtime attempt history has no accepted return for {group}")
        else:
            accepted[group] = entries[-1][2]

    shown_order = response.get("shown_order")
    if _is_array(shown_order):
        expected_groups = {
            *(('reaction', position) for position in range(1, len(shown_order) + 1)),
            ("comparison", None),
        }
        missing = expected_groups - set(grouped)
        unexpected = set(grouped) - expected_groups
        if missing:
            errors.append("runtime_attempts are missing required reaction or comparison calls")
        if unexpected:
            errors.append("runtime_attempts contain unassigned reaction positions")
    return errors, accepted


def _validate_source_provenance(
    value: Any,
    field: str,
    expected_provider_return_id: str | None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{field} must be an object"]
    errors: list[str] = []
    provider_return_id = value.get("provider_return_id")
    if not _non_empty_string(provider_return_id):
        errors.append(f"{field}.provider_return_id must be a stable non-empty string")
    elif (
        expected_provider_return_id is not None
        and provider_return_id != expected_provider_return_id
    ):
        errors.append(f"{field}.provider_return_id must identify the accepted return")
    if not _non_empty_string(value.get("capture")):
        errors.append(f"{field}.capture must describe source provenance")
    return errors


def validate_base_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate stable identity, assignment, provenance, and attempt history."""

    if not isinstance(response, Mapping):
        return ["response must be an object"]
    errors: list[str] = []
    for key in (
        "study_id",
        "response_id",
        "method",
        "synthetic_replicate_id",
        "reviewer_dispatch_id",
        "persona_archetype_id",
        "segment_id",
    ):
        _require_string(response, key, errors)
    if "context_stratum_id" in response and not _non_empty_string(
        response.get("context_stratum_id")
    ):
        errors.append("context_stratum_id must be a non-empty string when supplied")
    v3_identity_fields = (
        "audience_slot_id",
        "grounded_profile_id",
        "profile_snapshot_sha256",
    )
    present_v3_fields = [
        field for field in v3_identity_fields if field in response
    ]
    has_v3_marker = any(
        field in response
        for field in ("audience_slot_id", "profile_snapshot_sha256")
    )
    if has_v3_marker and len(present_v3_fields) != len(
        v3_identity_fields
    ):
        errors.append("v3 audience identity fields must be supplied together")
    if not isinstance(response.get("profile_snapshot"), Mapping) or not response.get(
        "profile_snapshot"
    ):
        errors.append("profile_snapshot must be a non-empty object")
    errors.extend(
        _validate_context_provenance(response.get("context_attribute_provenance"))
    )
    if response.get("worker_context_isolation") not in {
        "isolated",
        "shared_context_fallback",
    }:
        errors.append("worker_context_isolation is invalid")
    if response.get("human_sample_independence") is not False:
        errors.append("human_sample_independence must be false")
    if response.get("method") not in METHODS:
        errors.append("method must be complete_exposure or partial_exposure_maxdiff")
    errors.extend(
        _validate_assignment_fields(
            response.get("assigned_variation_ids"),
            response.get("shown_order"),
            response.get("blind_labels"),
            variation_field="assigned_variation_ids",
        )
    )
    errors.extend(_validate_reaction_protocol(response))
    attempt_errors, _ = _runtime_attempt_state(response)
    errors.extend(attempt_errors)

    validation = response.get("validation")
    if not isinstance(validation, Mapping):
        errors.append("validation must be an object")
    else:
        for flag in ("schema_valid", "assignment_valid", "reaction_order_valid"):
            if validation.get(flag) is not True:
                errors.append(f"validation.{flag} must be true")
    if "panelist_reviews" in response:
        errors.append("panelist_reviews is an obsolete one-shot response field")

    if job is not None:
        if not isinstance(job, Mapping):
            errors.append("job must be an object")
            return errors
        expected_values = {
            "study_id": job.get("study_id"),
            "response_id": job.get("response_id"),
            "record_type": job.get("record_type"),
            "method": job.get("method"),
            "synthetic_replicate_id": job.get("synthetic_replicate_id"),
            "reviewer_dispatch_id": job.get("dispatch_id"),
            "persona_archetype_id": job.get("persona_archetype_id"),
            "segment_id": job.get("segment_id"),
            "context_stratum_id": job.get("context_stratum_id"),
            "profile_snapshot": job.get("profile_snapshot"),
            "context_attribute_provenance": job.get("context_attribute_provenance"),
            "worker_context_isolation": job.get("worker_context_isolation"),
            "human_sample_independence": job.get("human_sample_independence"),
            "assigned_variation_ids": job.get("variation_ids"),
            "blind_labels": job.get("blind_labels"),
            "shown_order": job.get("shown_order"),
            "reaction_protocol": job.get("reaction_protocol"),
        }
        job_has_v3_identity = all(
            field in job for field in v3_identity_fields
        )
        response_has_v3_identity = all(
            field in response for field in v3_identity_fields
        )
        if job_has_v3_identity != response_has_v3_identity:
            errors.append(
                "response v3 audience identity presence must exactly match the job"
            )
        if job_has_v3_identity:
            expected_values.update(
                {
                    field: job.get(field)
                    for field in v3_identity_fields
                }
            )
        for field, expected in expected_values.items():
            if response.get(field) != expected:
                if field == "assigned_variation_ids":
                    errors.append("assigned_variation_ids must exactly match the job")
                elif field == "shown_order":
                    errors.append("shown_order must exactly match the job")
                else:
                    errors.append(f"{field} must exactly match the job")
    return errors


def _validate_reactions(
    response: Mapping[str, Any],
    records: Any,
    *,
    field: str,
    screening_fields: bool,
    finalist_fields: bool,
) -> tuple[list[str], list[Mapping[str, Any]], bool]:
    errors: list[str] = []
    assigned = response.get("assigned_variation_ids")
    shown_order = response.get("shown_order")
    blind_labels = response.get("blind_labels")
    expected_count = len(shown_order) if _is_array(shown_order) else 0
    if not _is_array(records):
        return [f"{field} must be an array"], [], False
    if len(records) != expected_count:
        errors.append(f"{field} must contain one record per shown creative")
    _, accepted_returns = _runtime_attempt_state(response)
    normalized: list[Mapping[str, Any]] = []
    seen_variations: set[str] = set()
    seen_reaction_ids: set[str] = set()
    all_judged = len(records) == expected_count
    for index, item in enumerate(records):
        prefix = f"{field}[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object")
            all_judged = False
            continue
        normalized.append(item)
        reaction_id = item.get("reaction_id")
        if not _non_empty_string(reaction_id):
            errors.append(f"{prefix}.reaction_id must be a stable non-empty string")
        elif reaction_id in seen_reaction_ids:
            errors.append(f"{prefix}.reaction_id is duplicated")
        else:
            seen_reaction_ids.add(reaction_id)
        variation_id = item.get("variation_id")
        if not _is_array(assigned) or variation_id not in assigned:
            errors.append(f"{prefix}.variation_id is not assigned")
        elif variation_id in seen_variations:
            errors.append(f"{prefix}.variation_id is duplicated")
        else:
            seen_variations.add(variation_id)
        expected_variation = (
            shown_order[index] if _is_array(shown_order) and index < len(shown_order) else None
        )
        if variation_id != expected_variation:
            errors.append(f"{prefix}.variation_id does not match shown_order position")
        expected_position = index + 1
        if item.get("position_seen") != expected_position:
            errors.append(f"{prefix}.position_seen does not match shown_order")
        expected_label = (
            blind_labels.get(variation_id)
            if isinstance(blind_labels, Mapping) and variation_id in blind_labels
            else None
        )
        if item.get("display_label_seen") != expected_label:
            errors.append(f"{prefix}.display_label_seen does not match blind_labels")
        if item.get("reaction_label") not in REACTION_LABELS:
            errors.append(f"{prefix}.reaction_label is invalid")
        if not _non_empty_string(item.get("immediate_reaction")):
            errors.append(f"{prefix}.immediate_reaction is required")
        judgment = item.get("judgment_status")
        if judgment not in JUDGMENT_STATUSES:
            errors.append(f"{prefix}.judgment_status is invalid")
            all_judged = False
        elif judgment != "judged":
            all_judged = False
        expected_provider = accepted_returns.get(("reaction", expected_position))
        errors.extend(
            _validate_source_provenance(
                item.get("source_provenance"),
                f"{prefix}.source_provenance",
                expected_provider,
            )
        )
        if screening_fields:
            for key in (
                "noticed_or_understood_first",
                "strongest_positive_signal",
                "strongest_negative_signal",
            ):
                if not _non_empty_string(item.get(key)):
                    errors.append(f"{prefix}.{key} is required")
        if finalist_fields:
            scores = item.get("rubric_scores")
            if not isinstance(scores, Mapping):
                errors.append(f"{prefix}.rubric_scores must be an object")
            else:
                for key in FINALIST_RUBRIC_KEYS:
                    score = scores.get(key)
                    if (
                        not isinstance(score, int)
                        or isinstance(score, bool)
                        or not 1 <= score <= 5
                    ):
                        errors.append(
                            f"{prefix}.rubric_scores.{key} must be a whole number from 1-5"
                        )
            feedback = item.get("feedback")
            if not _is_array(feedback) or not all(
                _non_empty_string(entry) for entry in feedback
            ):
                errors.append(f"{prefix}.feedback must be an array of strings")
            errors.extend(
                _validate_source_provenance(
                    item.get("rubric_source_provenance"),
                    f"{prefix}.rubric_source_provenance",
                    accepted_returns.get(("comparison", None)),
                )
            )
    if _is_array(assigned) and seen_variations != set(assigned):
        errors.append(f"{field} creative coverage is incomplete")
        all_judged = False
    return errors, normalized, all_judged


def _validate_frozen_ids(
    choice: Mapping[str, Any], reactions: Sequence[Mapping[str, Any]], field: str
) -> list[str]:
    expected = [reaction.get("reaction_id") for reaction in reactions]
    if choice.get("frozen_reaction_ids") != expected:
        return [f"{field}.frozen_reaction_ids must match validated reaction order"]
    return []


def validate_screening_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate the method-specific first-round screening record."""

    method = response.get("method")
    if method == "complete_exposure":
        return _validate_complete_exposure_response(response, job)
    return _validate_partial_exposure_response(response, job)


def _validate_partial_exposure_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate a four-creative partial-exposure MaxDiff collection record."""

    errors: list[str] = []
    if response.get("method") != "partial_exposure_maxdiff":
        errors.append(
            "partial-exposure screening_response requires partial_exposure_maxdiff"
        )
    for forbidden in (
        "pairwise_choice",
        "usable_pairwise_observation",
        "finalist_reviews",
        "final_preference_ranking",
        "complete_set_evaluation",
        "usable_complete_exposure_observation",
    ):
        if forbidden in response:
            errors.append(f"screening_response cannot contain {forbidden}")
    assigned = response.get("assigned_variation_ids")
    if not _is_array(assigned) or len(assigned) != 4:
        errors.append("screening_response must contain exactly four assigned variations")
    reaction_errors, reactions, all_judged = _validate_reactions(
        response,
        response.get("per_creative_reactions"),
        field="per_creative_reactions",
        screening_fields=True,
        finalist_fields=False,
    )
    errors.extend(reaction_errors)

    choice = response.get("comparative_choice")
    usable_choice = False
    frozen_ids_valid = False
    if not isinstance(choice, Mapping):
        errors.append("comparative_choice must be an object")
    else:
        status = choice.get("status")
        if status not in {
            "best_worst",
            "no_meaningful_difference",
            "unable_to_judge",
        }:
            errors.append("comparative_choice.status is invalid")
        if status == "best_worst":
            best = choice.get("best_variation_id")
            weakest = choice.get("weakest_variation_id")
            if not _is_array(assigned) or best not in assigned:
                errors.append("comparative_choice.best_variation_id is not assigned")
            if not _is_array(assigned) or weakest not in assigned:
                errors.append("comparative_choice.weakest_variation_id is not assigned")
            if best == weakest:
                errors.append("best and weakest variation IDs must differ")
            if not _non_empty_string(choice.get("best_reason")):
                errors.append("comparative_choice.best_reason is required")
            if not _non_empty_string(choice.get("weakest_reason")):
                errors.append("comparative_choice.weakest_reason is required")
            usable_choice = (
                best in (assigned or ())
                and weakest in (assigned or ())
                and best != weakest
            )
        else:
            for field in ("best_variation_id", "weakest_variation_id"):
                if choice.get(field) not in {None, ""}:
                    errors.append(f"comparative_choice.{field} must be empty for {status}")
        frozen_id_errors = _validate_frozen_ids(
            choice, reactions, "comparative_choice"
        )
        errors.extend(frozen_id_errors)
        frozen_ids_valid = not frozen_id_errors
        _, accepted_returns = _runtime_attempt_state(response)
        errors.extend(
            _validate_source_provenance(
                choice.get("source_provenance"),
                "comparative_choice.source_provenance",
                accepted_returns.get(("comparison", None)),
            )
        )

    expected_usable = (
        not reaction_errors
        and not _validate_reaction_protocol(response)
        and all_judged
        and usable_choice
        and frozen_ids_valid
    )
    usable = response.get("usable_maxdiff_block")
    if not isinstance(usable, bool):
        errors.append("usable_maxdiff_block must be a boolean")
    elif usable != expected_usable:
        errors.append(f"usable_maxdiff_block must be {str(expected_usable).lower()}")
    return errors


def _validate_complete_exposure_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate a 2-6 creative complete-set first-round observation."""

    errors: list[str] = []
    if response.get("method") != "complete_exposure":
        errors.append("complete-exposure screening_response requires complete_exposure")
    for forbidden in (
        "comparative_choice",
        "usable_maxdiff_block",
        "pairwise_choice",
        "usable_pairwise_observation",
        "finalist_reviews",
        "final_preference_ranking",
    ):
        if forbidden in response:
            errors.append(f"complete_exposure screening_response cannot contain {forbidden}")

    assigned = response.get("assigned_variation_ids")
    if not _is_array(assigned) or not 2 <= len(assigned) <= 6:
        errors.append(
            "complete_exposure screening_response must contain between two and six assigned variations"
        )
    reaction_errors, reactions, all_judged = _validate_reactions(
        response,
        response.get("per_creative_reactions"),
        field="per_creative_reactions",
        screening_fields=True,
        finalist_fields=False,
    )
    errors.extend(reaction_errors)

    evaluation = response.get("complete_set_evaluation")
    usable_evaluation = False
    frozen_ids_valid = False
    if not isinstance(evaluation, Mapping):
        errors.append("complete_set_evaluation must be an object")
    else:
        status = evaluation.get("status")
        if status not in {"ranked", "unable_to_judge"}:
            errors.append("complete_set_evaluation.status is invalid")
        ranking = evaluation.get("preference_ranking")
        if status == "ranked":
            if (
                not _is_array(ranking)
                or not _is_array(assigned)
                or len(ranking) != len(assigned)
                or len(set(ranking)) != len(ranking)
                or set(ranking) != set(assigned)
            ):
                errors.append(
                    "complete_set_evaluation.preference_ranking must be an exact permutation of assigned variations"
                )
            else:
                usable_evaluation = True
        elif ranking is not None and ranking != "" and ranking != []:
            errors.append(
                "complete_set_evaluation.preference_ranking must be empty when unable to judge"
            )
        frozen_id_errors = _validate_frozen_ids(
            evaluation, reactions, "complete_set_evaluation"
        )
        errors.extend(frozen_id_errors)
        frozen_ids_valid = not frozen_id_errors
        _, accepted_returns = _runtime_attempt_state(response)
        errors.extend(
            _validate_source_provenance(
                evaluation.get("source_provenance"),
                "complete_set_evaluation.source_provenance",
                accepted_returns.get(("comparison", None)),
            )
        )

    expected_usable = (
        not reaction_errors
        and not _validate_reaction_protocol(response)
        and all_judged
        and usable_evaluation
        and frozen_ids_valid
    )
    usable = response.get("usable_complete_exposure_observation")
    if not isinstance(usable, bool):
        errors.append("usable_complete_exposure_observation must be a boolean")
    elif usable != expected_usable:
        errors.append(
            "usable_complete_exposure_observation must be "
            f"{str(expected_usable).lower()}"
        )
    return errors


def validate_boundary_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate one predeclared pairwise boundary observation only."""

    errors: list[str] = []
    if response.get("method") != "partial_exposure_maxdiff":
        errors.append("boundary_response requires partial_exposure_maxdiff")
    for forbidden in (
        "comparative_choice",
        "usable_maxdiff_block",
        "finalist_reviews",
        "final_preference_ranking",
    ):
        if forbidden in response:
            errors.append(f"boundary_response cannot contain {forbidden}")
    assigned = response.get("assigned_variation_ids")
    shown_order = response.get("shown_order")
    if not _is_array(assigned) or len(assigned) != 2:
        errors.append("boundary_response must contain exactly two assigned variations")

    reaction_errors, reactions, all_judged = _validate_reactions(
        response,
        response.get("per_creative_reactions"),
        field="per_creative_reactions",
        screening_fields=True,
        finalist_fields=False,
    )
    errors.extend(reaction_errors)

    choice = response.get("pairwise_choice")
    usable_choice = False
    frozen_ids_valid = False
    if not isinstance(choice, Mapping):
        errors.append("pairwise_choice must be an object")
    else:
        status = choice.get("status")
        statuses = {"first_preferred", "second_preferred", "tie", "unable_to_judge"}
        if status not in statuses:
            errors.append("pairwise_choice.status is invalid")
        preferred = choice.get("preferred_variation_id")
        if status == "first_preferred":
            expected = shown_order[0] if _is_array(shown_order) and shown_order else None
            if preferred != expected:
                errors.append("preferred_variation_id must match the first shown creative")
            usable_choice = preferred == expected
        elif status == "second_preferred":
            expected = shown_order[1] if _is_array(shown_order) and len(shown_order) > 1 else None
            if preferred != expected:
                errors.append("preferred_variation_id must match the second shown creative")
            usable_choice = preferred == expected
        elif status == "tie":
            if preferred not in {None, ""}:
                errors.append("preferred_variation_id must be empty for a tie")
            usable_choice = True
        elif status == "unable_to_judge":
            if preferred not in {None, ""}:
                errors.append("preferred_variation_id must be empty when unable to judge")
        if not _non_empty_string(choice.get("reason")):
            errors.append("pairwise_choice.reason is required")
        frozen_id_errors = _validate_frozen_ids(
            choice, reactions, "pairwise_choice"
        )
        errors.extend(frozen_id_errors)
        frozen_ids_valid = not frozen_id_errors
        _, accepted_returns = _runtime_attempt_state(response)
        errors.extend(
            _validate_source_provenance(
                choice.get("source_provenance"),
                "pairwise_choice.source_provenance",
                accepted_returns.get(("comparison", None)),
            )
        )

    expected_usable = (
        not reaction_errors
        and all_judged
        and usable_choice
        and frozen_ids_valid
    )
    usable = response.get("usable_pairwise_observation")
    if not isinstance(usable, bool):
        errors.append("usable_pairwise_observation must be a boolean")
    elif usable != expected_usable:
        errors.append(
            f"usable_pairwise_observation must be {str(expected_usable).lower()}"
        )
    return errors


def validate_finalist_response(
    response: Mapping[str, Any], job: Mapping[str, Any] | None = None
) -> list[str]:
    """Validate progressively exposed finalist rubrics and final ranking only."""

    errors: list[str] = []
    if response.get("method") not in METHODS:
        errors.append("finalist_response method is invalid")
    for forbidden in (
        "per_creative_reactions",
        "comparative_choice",
        "usable_maxdiff_block",
        "pairwise_choice",
        "usable_pairwise_observation",
    ):
        if forbidden in response:
            errors.append(f"finalist_response cannot contain {forbidden}")
    assigned = response.get("assigned_variation_ids")
    if not _is_array(assigned) or not 2 <= len(assigned) <= 6:
        errors.append("finalist_response must contain between two and six variations")
    reaction_errors, _, all_judged = _validate_reactions(
        response,
        response.get("finalist_reviews"),
        field="finalist_reviews",
        screening_fields=False,
        finalist_fields=True,
    )
    errors.extend(reaction_errors)
    if not all_judged:
        errors.append("finalist reviews must be judged before final ranking")
    ranking = response.get("final_preference_ranking")
    if (
        not _is_array(ranking)
        or not _is_array(assigned)
        or len(ranking) != len(assigned)
        or len(set(ranking)) != len(ranking)
        or set(ranking) != set(assigned)
    ):
        errors.append(
            "final_preference_ranking must be an exact permutation of assigned variations"
        )
    return errors


ResponseValidator = Callable[[Mapping[str, Any], Mapping[str, Any] | None], list[str]]
RESPONSE_VALIDATORS: dict[str, ResponseValidator] = {
    "screening_response": validate_screening_response,
    "boundary_response": validate_boundary_response,
    "finalist_response": validate_finalist_response,
}


def validate_response(
    response: Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
) -> list[str]:
    """Dispatch exactly one response-stage validator after base validation."""

    errors = validate_base_response(response, job)
    record_type = response.get("record_type") if isinstance(response, Mapping) else None
    validator = RESPONSE_VALIDATORS.get(record_type)
    if validator is None:
        return errors + [f"unsupported record_type: {record_type}"]
    return errors + validator(response, job)


def validate_response_job_bindings(
    jobs: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
    *,
    require_exact_set: bool = True,
) -> list[str]:
    """Validate one canonical exact response-to-dispatch-job binding set.

    Aggregators and standalone validators must use this function instead of
    maintaining partial field lists.  ``validate_response(response, job)`` is
    the sole field-level authority; this wrapper additionally proves unique
    identities and exact set coverage.
    """

    errors: list[str] = []
    jobs_by_replicate: dict[str, Mapping[str, Any]] = {}
    job_response_ids: set[str] = set()
    job_dispatch_ids: set[str] = set()
    for index, job in enumerate(jobs):
        prefix = f"jobs[{index}]"
        if not isinstance(job, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(f"{prefix}: {error}" for error in validate_job(job))
        replicate_id = job.get("synthetic_replicate_id")
        if not _non_empty_string(replicate_id):
            continue
        if replicate_id in jobs_by_replicate:
            errors.append(f"duplicate planned synthetic_replicate_id: {replicate_id}")
        else:
            jobs_by_replicate[str(replicate_id)] = job
        for field, seen in (
            ("response_id", job_response_ids),
            ("dispatch_id", job_dispatch_ids),
        ):
            value = job.get(field)
            if _non_empty_string(value):
                if value in seen:
                    errors.append(f"duplicate planned {field}: {value}")
                seen.add(str(value))

    response_replicates: set[str] = set()
    response_ids: set[str] = set()
    response_dispatch_ids: set[str] = set()
    for index, response in enumerate(responses):
        prefix = f"responses[{index}]"
        if not isinstance(response, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        replicate_id = response.get("synthetic_replicate_id")
        if _non_empty_string(replicate_id):
            if replicate_id in response_replicates:
                errors.append(f"duplicate response synthetic_replicate_id: {replicate_id}")
            response_replicates.add(str(replicate_id))
        for field, seen in (
            ("response_id", response_ids),
            ("reviewer_dispatch_id", response_dispatch_ids),
        ):
            value = response.get(field)
            if _non_empty_string(value):
                if value in seen:
                    errors.append(f"duplicate response {field}: {value}")
                seen.add(str(value))
        job = (
            jobs_by_replicate.get(str(replicate_id))
            if _non_empty_string(replicate_id)
            else None
        )
        if job is None:
            errors.append(
                f"{prefix}.synthetic_replicate_id is outside the frozen job set: "
                f"{replicate_id}"
            )
        errors.extend(
            f"{prefix}: {error}" for error in validate_response(response, job)
        )

    if require_exact_set:
        missing = sorted(set(jobs_by_replicate) - response_replicates)
        unexpected = sorted(response_replicates - set(jobs_by_replicate))
        if missing:
            errors.append(
                "missing responses for frozen synthetic_replicate_ids: "
                + ", ".join(missing)
            )
        if unexpected:
            errors.append(
                "responses outside frozen synthetic_replicate_ids: "
                + ", ".join(unexpected)
            )
        if len(responses) != len(jobs):
            errors.append(
                f"exact job binding expected {len(jobs)} responses, found {len(responses)}"
            )
    return errors


def validate_dispatch_audit_job_bindings(
    jobs: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
    dispatch_audit: Sequence[Mapping[str, Any]],
    *,
    retry_limit_per_return: int,
) -> list[str]:
    """Bind dispatch outcomes to the frozen jobs and accepted response set.

    This validator owns the accepted-versus-exhausted slot relationship.  Raw
    provider-call lineage remains subject to the stricter lineage validator;
    aggregation uses this frozen audit view to decide whether a planned missing
    response is an authorized incomplete slot.
    """

    errors: list[str] = []
    if (
        isinstance(retry_limit_per_return, bool)
        or not isinstance(retry_limit_per_return, int)
        or retry_limit_per_return != 1
    ):
        return ["retry_limit_per_return must equal the supported policy of 1"]

    jobs_by_dispatch: dict[str, Mapping[str, Any]] = {}
    jobs_by_replicate: dict[str, Mapping[str, Any]] = {}
    for index, job in enumerate(jobs):
        if not isinstance(job, Mapping):
            errors.append(f"jobs[{index}] must be an object")
            continue
        dispatch_id = job.get("dispatch_id")
        replicate_id = job.get("synthetic_replicate_id")
        if _non_empty_string(dispatch_id):
            if dispatch_id in jobs_by_dispatch:
                errors.append(f"duplicate planned dispatch_id: {dispatch_id}")
            jobs_by_dispatch[str(dispatch_id)] = job
        if _non_empty_string(replicate_id):
            if replicate_id in jobs_by_replicate:
                errors.append(
                    f"duplicate planned synthetic_replicate_id: {replicate_id}"
                )
            jobs_by_replicate[str(replicate_id)] = job

    responses_by_dispatch: dict[str, Mapping[str, Any]] = {}
    for index, response in enumerate(responses):
        if not isinstance(response, Mapping):
            errors.append(f"responses[{index}] must be an object")
            continue
        dispatch_id = response.get("reviewer_dispatch_id")
        if not _non_empty_string(dispatch_id):
            continue
        if dispatch_id in responses_by_dispatch:
            errors.append(f"duplicate accepted reviewer_dispatch_id: {dispatch_id}")
        responses_by_dispatch[str(dispatch_id)] = response

    audited_dispatches: set[str] = set()
    audited_replicates: set[str] = set()
    accepted_audit_dispatches: set[str] = set()
    for index, audit in enumerate(dispatch_audit):
        prefix = f"dispatch_audit[{index}]"
        if not isinstance(audit, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        dispatch_id = audit.get("reviewer_dispatch_id")
        replicate_id = audit.get("synthetic_replicate_id")
        if not _non_empty_string(dispatch_id):
            errors.append(f"{prefix}.reviewer_dispatch_id must be a non-empty string")
            continue
        if not _non_empty_string(replicate_id):
            errors.append(f"{prefix}.synthetic_replicate_id must be a non-empty string")
            continue
        dispatch_id = str(dispatch_id)
        replicate_id = str(replicate_id)
        if dispatch_id in audited_dispatches or replicate_id in audited_replicates:
            errors.append("dispatch_audit job-slot IDs must be unique")
        audited_dispatches.add(dispatch_id)
        audited_replicates.add(replicate_id)
        job = jobs_by_dispatch.get(dispatch_id)
        if job is None:
            errors.append(f"{prefix} is outside the frozen job set")
            continue
        if replicate_id != job.get("synthetic_replicate_id"):
            errors.append(f"{prefix}.synthetic_replicate_id must exactly match the job")
        if audit.get("record_type") != job.get("record_type"):
            errors.append(f"{prefix}.record_type must exactly match the job")

        accepted = audit.get("accepted")
        if not isinstance(accepted, bool):
            errors.append(f"{prefix}.accepted must be a boolean")
            continue
        response = responses_by_dispatch.get(dispatch_id)
        if accepted != (response is not None):
            errors.append(
                f"{prefix}.accepted must exactly match the accepted response set"
            )
        if accepted:
            accepted_audit_dispatches.add(dispatch_id)

        contract = audit.get("attempt_contract")
        shown_order = job.get("shown_order")
        expected_positions = (
            list(range(1, len(shown_order) + 1))
            if _is_array(shown_order)
            else []
        )
        if not isinstance(contract, Mapping):
            errors.append(f"{prefix}.attempt_contract must be an object")
            continue
        if contract.get("retry_limit_per_return") != retry_limit_per_return:
            errors.append(f"{prefix}.attempt_contract retry policy must match the manifest")
        if contract.get("reaction_positions") != expected_positions:
            errors.append(
                f"{prefix}.attempt_contract.reaction_positions must match the job"
            )
        if contract.get("comparison_required") is not True:
            errors.append(
                f"{prefix}.attempt_contract.comparison_required must be true"
            )
        reaction_attempts = audit.get("reaction_attempts")
        if (
            not _is_array(reaction_attempts)
            or len(reaction_attempts) != len(expected_positions)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= retry_limit_per_return + 1
                for value in reaction_attempts
            )
        ):
            errors.append(
                f"{prefix}.reaction_attempts must contain one valid count per position"
            )
            continue
        comparison_attempts = audit.get("comparison_attempts")
        if (
            isinstance(comparison_attempts, bool)
            or not isinstance(comparison_attempts, int)
            or not 0 <= comparison_attempts <= retry_limit_per_return + 1
        ):
            errors.append(f"{prefix}.comparison_attempts is invalid")
            continue

        if response is not None:
            actual_reactions = [0 for _ in expected_positions]
            actual_comparison = 0
            attempts = response.get("runtime_attempts")
            if _is_array(attempts):
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        continue
                    if attempt.get("stage") == "reaction":
                        position = attempt.get("position_seen")
                        if (
                            isinstance(position, int)
                            and not isinstance(position, bool)
                            and position in expected_positions
                        ):
                            actual_reactions[position - 1] += 1
                    elif attempt.get("stage") == "comparison":
                        actual_comparison += 1
            if list(reaction_attempts) != actual_reactions:
                errors.append(
                    f"{prefix}.reaction_attempts must match the accepted response lineage"
                )
            if comparison_attempts != actual_comparison:
                errors.append(
                    f"{prefix}.comparison_attempts must match the accepted response lineage"
                )
        else:
            reaction_exhausted = (
                retry_limit_per_return + 1 in reaction_attempts
                and comparison_attempts == 0
            )
            comparison_exhausted = (
                all(value >= 1 for value in reaction_attempts)
                and comparison_attempts == retry_limit_per_return + 1
            )
            if not reaction_exhausted and not comparison_exhausted:
                errors.append(
                    f"{prefix} incomplete slot must record reaction or comparison exhaustion"
                )

    if audited_dispatches != set(jobs_by_dispatch):
        missing = sorted(set(jobs_by_dispatch) - audited_dispatches)
        unexpected = sorted(audited_dispatches - set(jobs_by_dispatch))
        errors.append(
            "dispatch_audit must exactly cover the frozen job set "
            f"(missing={missing}, unexpected={unexpected})"
        )
    if audited_replicates != set(jobs_by_replicate):
        errors.append("dispatch_audit replicate IDs must exactly cover the frozen job set")
    if accepted_audit_dispatches != set(responses_by_dispatch):
        errors.append("dispatch_audit accepted set must exactly match accepted responses")
    return errors


__all__ = [
    "RESPONSE_VALIDATORS",
    "validate_base_response",
    "validate_boundary_response",
    "validate_finalist_response",
    "validate_job",
    "validate_response",
    "validate_dispatch_audit_job_bindings",
    "validate_response_job_bindings",
    "validate_screening_response",
]
