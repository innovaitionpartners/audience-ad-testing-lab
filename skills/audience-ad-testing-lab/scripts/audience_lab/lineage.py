"""Materialize and bind progressive-workflow provider-return lineage."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import validate_manifest
from .responses import validate_response


CANONICAL_LINEAGE_FILES = {
    "accepted_responses": "panelist-responses.jsonl",
    "raw_provider_returns": "raw-provider-returns.jsonl",
    "rejected_attempts": "rejected-attempts.jsonl",
    "dispatch_audit": "dispatch-audit.jsonl",
}


def _records(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field} entries must be objects")
    return [dict(item) for item in value]


def _stable_id(record: Mapping[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} must be a stable non-empty string")
    return value


def _jsonl_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
        for record in records
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _attempt_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("provider_return_id"),
        record.get("stage"),
        record.get("position_seen") if record.get("stage") == "reaction" else None,
        record.get("attempt_number"),
    )


def _retry_limit(manifest: Mapping[str, Any] | None) -> int:
    if not isinstance(manifest, Mapping):
        raise ValueError("lineage validation requires a manifest-bound retry policy")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("lineage-bound manifest runtime must be an object")
    value = runtime.get("retry_limit_per_return")
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ValueError(
            "manifest.runtime.retry_limit_per_return must equal the supported policy of 1"
        )
    return value


def _attempt_contract(
    audit: Mapping[str, Any], context: str, retry_limit: int
) -> tuple[set[int], bool]:
    contract = audit.get("attempt_contract")
    if not isinstance(contract, Mapping):
        raise ValueError(f"{context}.attempt_contract must be an object")
    if contract.get("retry_limit_per_return") != retry_limit:
        raise ValueError(
            f"{context}.attempt_contract retry policy must match the manifest"
        )
    positions = contract.get("reaction_positions")
    if (
        not isinstance(positions, Sequence)
        or isinstance(positions, (str, bytes))
        or not positions
        or any(
            isinstance(position, bool)
            or not isinstance(position, int)
            or position < 1
            for position in positions
        )
        or len(set(positions)) != len(positions)
    ):
        raise ValueError(
            f"{context}.attempt_contract.reaction_positions must contain unique positive integers"
        )
    expected = list(range(1, len(positions) + 1))
    if list(positions) != expected:
        raise ValueError(
            f"{context}.attempt_contract.reaction_positions must be the exact ordered positions"
        )
    comparison_required = contract.get("comparison_required")
    if comparison_required is not True:
        raise ValueError(
            f"{context}.attempt_contract.comparison_required must be true"
        )
    return set(positions), comparison_required


def _audit_attempt_counts(
    audit: Mapping[str, Any],
    context: str,
    reaction_positions: set[int],
    comparison_required: bool,
    retry_limit: int,
) -> dict[tuple[str, int | None], int]:
    reaction_attempts = audit.get("reaction_attempts")
    ordered_positions = sorted(reaction_positions)
    if (
        not isinstance(reaction_attempts, Sequence)
        or isinstance(reaction_attempts, (str, bytes))
        or len(reaction_attempts) != len(ordered_positions)
        or any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= retry_limit + 1
            for count in reaction_attempts
        )
    ):
        raise ValueError(
            f"{context}.reaction_attempts must contain one valid call count "
            "per authorized reaction position"
        )
    comparison_attempts = audit.get("comparison_attempts")
    if (
        isinstance(comparison_attempts, bool)
        or not isinstance(comparison_attempts, int)
        or not 0 <= comparison_attempts <= retry_limit + 1
    ):
        raise ValueError(
            f"{context}.comparison_attempts must be a valid provider-call count"
        )
    if not comparison_required and comparison_attempts != 0:
        raise ValueError(
            f"{context}.comparison_attempts must be zero when comparison is not authorized"
        )
    counts = {
        ("reaction", position): int(reaction_attempts[index])
        for index, position in enumerate(ordered_positions)
    }
    counts[("comparison", None)] = comparison_attempts
    return counts


def validate_lineage_records(
    responses: Sequence[Mapping[str, Any]],
    raw_returns: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    dispatch_audit: Sequence[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one authoritative lineage graph, including exhausted dispatches."""

    responses = _records(responses, "accepted_responses")
    raw_returns = _records(raw_returns, "raw_provider_returns")
    rejected = _records(rejected, "rejected_attempts")
    dispatch_audit = _records(dispatch_audit, "dispatch_audit")
    if not raw_returns:
        raise ValueError("raw_provider_returns must contain every provider/model call")
    if not dispatch_audit:
        raise ValueError("dispatch_audit must contain every dispatched job slot")

    retry_limit = _retry_limit(manifest)
    audit_by_dispatch: dict[str, dict[str, Any]] = {}
    audit_contracts: dict[str, tuple[set[int], bool]] = {}
    audit_attempt_counts: dict[str, dict[tuple[str, int | None], int]] = {}
    audit_replicates: set[str] = set()
    allowed_stages = {
        "screening_response",
        "boundary_response",
        "finalist_response",
    }
    for index, audit in enumerate(dispatch_audit):
        context = f"dispatch_audit[{index}]"
        dispatch_id = _stable_id(audit, "reviewer_dispatch_id", context)
        replicate_id = _stable_id(audit, "synthetic_replicate_id", context)
        if dispatch_id in audit_by_dispatch or replicate_id in audit_replicates:
            raise ValueError("dispatch_audit job-slot IDs must be unique")
        if audit.get("record_type") not in allowed_stages:
            raise ValueError(f"{context}.record_type is invalid")
        if not isinstance(audit.get("accepted"), bool):
            raise ValueError(f"{context}.accepted must be a boolean")
        contract = _attempt_contract(
            audit, context, retry_limit
        )
        audit_contracts[dispatch_id] = contract
        audit_attempt_counts[dispatch_id] = _audit_attempt_counts(
            audit,
            context,
            contract[0],
            contract[1],
            retry_limit,
        )
        audit_by_dispatch[dispatch_id] = audit
        audit_replicates.add(replicate_id)

    raw_by_id: dict[str, dict[str, Any]] = {}
    raw_by_dispatch: dict[str, list[dict[str, Any]]] = {}
    raw_keys: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(raw_returns):
        context = f"raw_provider_returns[{index}]"
        provider_id = _stable_id(raw, "provider_return_id", context)
        replicate_id = _stable_id(raw, "synthetic_replicate_id", context)
        dispatch_id = _stable_id(raw, "reviewer_dispatch_id", context)
        audit = audit_by_dispatch.get(dispatch_id)
        if audit is None:
            raise ValueError(f"{context} belongs to a dispatch outside dispatch_audit")
        if replicate_id != audit.get("synthetic_replicate_id"):
            raise ValueError(f"{context}.synthetic_replicate_id disagrees with dispatch_audit")
        if provider_id in raw_by_id:
            raise ValueError(f"duplicate provider_return_id: {provider_id}")
        stage = raw.get("stage")
        if stage not in {"reaction", "comparison"}:
            raise ValueError(f"{context}.stage is invalid")
        position = raw.get("position_seen")
        reaction_positions, comparison_required = audit_contracts[dispatch_id]
        if stage == "reaction" and (
            not isinstance(position, int)
            or isinstance(position, bool)
            or position not in reaction_positions
        ):
            raise ValueError(
                f"{context}.position_seen is outside the authorized attempt contract"
            )
        if stage == "comparison" and position is not None:
            raise ValueError(f"{context}.position_seen must be absent for comparison")
        if stage == "comparison" and not comparison_required:
            raise ValueError(f"{context}.comparison is outside the attempt contract")
        attempt_number = raw.get("attempt_number")
        if (
            not isinstance(attempt_number, int)
            or isinstance(attempt_number, bool)
            or not 1 <= attempt_number <= retry_limit + 1
        ):
            raise ValueError(
                f"{context}.attempt_number is outside the configured retry policy"
            )
        if not isinstance(raw.get("accepted"), bool):
            raise ValueError(f"{context}.accepted must be a boolean")
        validation_errors = raw.get("validation_errors")
        if not isinstance(validation_errors, list):
            raise ValueError(f"{context}.validation_errors must be an array")
        if raw["accepted"] == bool(validation_errors):
            raise ValueError(f"{context} acceptance and validation errors disagree")
        if "raw_return" not in raw:
            raise ValueError(f"{context}.raw_return is required")
        key = (
            dispatch_id,
            replicate_id,
            stage,
            position if stage == "reaction" else None,
            attempt_number,
        )
        if key in raw_keys:
            raise ValueError(f"duplicate raw attempt identity: {key}")
        raw_keys.add(key)
        raw_by_id[provider_id] = raw
        raw_by_dispatch.setdefault(dispatch_id, []).append(raw)

    if set(raw_by_dispatch) != set(audit_by_dispatch):
        raise ValueError("every dispatched job must have provider-return lineage")

    rejected_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(rejected):
        context = f"rejected_attempts[{index}]"
        provider_id = _stable_id(item, "provider_return_id", context)
        if provider_id in rejected_by_id:
            raise ValueError(f"duplicate rejected provider_return_id: {provider_id}")
        raw = raw_by_id.get(provider_id)
        if raw is None or raw.get("accepted") is not False:
            raise ValueError(f"{context} must identify a rejected raw provider return")
        for field in (
            "synthetic_replicate_id",
            "reviewer_dispatch_id",
            "stage",
            "position_seen",
            "attempt_number",
            "validation_errors",
        ):
            if item.get(field) != raw.get(field):
                raise ValueError(f"{context}.{field} must match its raw return")
        rejected_by_id[provider_id] = item
    expected_rejected = {
        provider_id for provider_id, raw in raw_by_id.items() if not raw["accepted"]
    }
    if set(rejected_by_id) != expected_rejected:
        raise ValueError("rejected_attempts must exactly cover rejected raw returns")

    response_by_dispatch: dict[str, dict[str, Any]] = {}
    response_ids: set[str] = set()
    response_replicates: set[str] = set()
    for index, response in enumerate(responses):
        errors = validate_response(response)
        if errors:
            raise ValueError(f"responses[{index}] is invalid: " + "; ".join(errors))
        response_id = _stable_id(response, "response_id", f"responses[{index}]")
        dispatch_id = _stable_id(
            response, "reviewer_dispatch_id", f"responses[{index}]"
        )
        replicate_id = _stable_id(
            response, "synthetic_replicate_id", f"responses[{index}]"
        )
        if response_id in response_ids or replicate_id in response_replicates:
            raise ValueError("accepted response IDs and replicate IDs must be unique")
        if dispatch_id in response_by_dispatch:
            raise ValueError("accepted response dispatch IDs must be unique")
        audit = audit_by_dispatch.get(dispatch_id)
        if audit is None or audit.get("accepted") is not True:
            raise ValueError("accepted response must map to an accepted dispatch audit")
        if replicate_id != audit.get("synthetic_replicate_id"):
            raise ValueError("accepted response replicate ID disagrees with dispatch_audit")
        if response.get("record_type") != audit.get("record_type"):
            raise ValueError("accepted response record_type disagrees with dispatch_audit")
        expected_positions, comparison_required = audit_contracts[dispatch_id]
        response_positions = list(range(1, len(response.get("shown_order", ())) + 1))
        if expected_positions != set(response_positions):
            raise ValueError(
                "accepted response shown_order disagrees with dispatch attempt contract"
            )
        if comparison_required is not True:
            raise ValueError(
                "accepted response requires a comparison in its dispatch attempt contract"
            )
        response_ids.add(response_id)
        response_replicates.add(replicate_id)
        response_by_dispatch[dispatch_id] = response
        referenced: set[str] = set()
        for attempt_index, attempt in enumerate(response["runtime_attempts"]):
            provider_id = _stable_id(
                attempt,
                "provider_return_id",
                f"responses[{index}].runtime_attempts[{attempt_index}]",
            )
            raw = raw_by_id.get(provider_id)
            expected_position = (
                attempt.get("position_seen") if attempt.get("stage") == "reaction" else None
            )
            expected_outcome = (
                "accepted" if isinstance(raw, Mapping) and raw.get("accepted") else "rejected"
            )
            if (
                raw is None
                or raw.get("synthetic_replicate_id") != replicate_id
                or raw.get("reviewer_dispatch_id") != dispatch_id
                or raw.get("stage") != attempt.get("stage")
                or raw.get("position_seen") != expected_position
                or raw.get("attempt_number") != attempt.get("attempt_number")
                or raw.get("validation_errors") != attempt.get("validation_errors")
                or attempt.get("outcome") != expected_outcome
            ):
                raise ValueError(
                    f"runtime attempt {provider_id} identity/outcome disagrees with raw lineage"
                )
            referenced.add(provider_id)
        expected = {
            str(item["provider_return_id"]) for item in raw_by_dispatch[dispatch_id]
        }
        if referenced != expected:
            raise ValueError(
                "accepted response runtime_attempts must exactly cover its dispatch calls"
            )

    for dispatch_id, audit in audit_by_dispatch.items():
        response = response_by_dispatch.get(dispatch_id)
        calls = raw_by_dispatch[dispatch_id]
        grouped_calls: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
        for call in calls:
            key = (
                str(call["stage"]),
                call.get("position_seen")
                if call.get("stage") == "reaction"
                else None,
            )
            grouped_calls.setdefault(key, []).append(call)

        expected_counts = audit_attempt_counts[dispatch_id]
        actual_counts = {key: len(group) for key, group in grouped_calls.items()}
        expected_present_counts = {
            key: count for key, count in expected_counts.items() if count > 0
        }
        if actual_counts != expected_present_counts:
            raise ValueError(
                "raw provider calls must exactly reconcile to dispatch_audit "
                "reaction_attempts and comparison_attempts"
            )

        exhausted_groups: set[tuple[str, int | None]] = set()
        for key, group in grouped_calls.items():
            ordered = sorted(group, key=lambda item: int(item["attempt_number"]))
            attempts = [int(item["attempt_number"]) for item in ordered]
            expected_prefix = list(range(1, attempts[-1] + 1))
            if attempts != expected_prefix:
                raise ValueError(
                    "provider-return attempt numbering must start at 1 and be contiguous"
                )
            accepted_attempts = [
                item for item in ordered if item.get("accepted") is True
            ]
            if len(accepted_attempts) > 1 or (
                accepted_attempts and accepted_attempts[0] is not ordered[-1]
            ):
                raise ValueError(
                    "provider-return sequence must stop after its first accepted component call"
                )
            if not accepted_attempts:
                expected_attempts = list(range(1, retry_limit + 2))
                if attempts != expected_attempts:
                    raise ValueError(
                        "rejected component sequence must contain the exact full retry sequence"
                    )
                exhausted_groups.add(key)

        exhausted_reaction_positions = {
            position
            for stage, position in exhausted_groups
            if stage == "reaction" and isinstance(position, int)
        }
        comparison_key = ("comparison", None)
        comparison_count = expected_counts[comparison_key]
        comparison_required = audit_contracts[dispatch_id][1]
        if exhausted_reaction_positions and comparison_count != 0:
            raise ValueError(
                "comparison calls are unexpected after an exhausted reaction sequence"
            )
        if (
            not exhausted_reaction_positions
            and comparison_required
            and comparison_count == 0
        ):
            raise ValueError(
                "comparison calls are required after every reaction position succeeds"
            )

        if audit["accepted"]:
            if response is None or not any(call["accepted"] for call in calls):
                raise ValueError(
                    "accepted composite dispatch must have one accepted response and component call"
                )
            if exhausted_groups:
                raise ValueError(
                    "accepted composite dispatch cannot retain an exhausted provider-call sequence"
                )
        elif response is not None:
            raise ValueError(
                "incomplete dispatch must have zero accepted composite responses"
            )
        elif not exhausted_groups:
            raise ValueError(
                "incomplete dispatch must contain an exact exhausted retry sequence "
                "for at least one required provider-call position"
            )

    stage_counts = Counter(str(item["record_type"]) for item in dispatch_audit)
    if manifest is not None:
        capacity = manifest.get("synthetic_replicate_capacity")
        if not isinstance(capacity, Mapping):
            raise ValueError("lineage-bound manifest requires capacity reserves")
        reserve_fields = {
            "screening_response": "screening_planned",
            "boundary_response": "boundary_reserved",
            "finalist_response": "finalist_reserved",
        }
        for record_type, reserve_field in reserve_fields.items():
            reserve = capacity.get(reserve_field)
            if (
                isinstance(reserve, bool)
                or not isinstance(reserve, int)
                or stage_counts[record_type] > reserve
            ):
                raise ValueError(
                    f"dispatched {record_type} slots exceed {reserve_field} reserve"
                )
    return {
        "unique_job_slots_dispatched": len(dispatch_audit),
        "accepted_response_records": len(responses),
        "accepted_unique_replicates": len(response_replicates),
        "total_model_calls": len(raw_returns),
        "rejected_attempts": len(rejected),
        "dispatched_job_slots_by_stage": dict(sorted(stage_counts.items())),
    }


def validate_bound_lineage(
    manifest: Mapping[str, Any],
    records_by_filename: Mapping[str, Sequence[Mapping[str, Any]]],
    content_by_filename: Mapping[str, bytes],
) -> dict[str, Any]:
    """Validate canonical bindings and then the shared lineage graph."""

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("lineage-bound manifest outputs must be an object")
    present = {name for name in CANONICAL_LINEAGE_FILES if name in outputs}
    if present != set(CANONICAL_LINEAGE_FILES):
        raise ValueError("manifest must bind all four canonical lineage files together")
    named_records: dict[str, Sequence[Mapping[str, Any]]] = {}
    for name, filename in CANONICAL_LINEAGE_FILES.items():
        binding = outputs.get(name)
        records = records_by_filename.get(filename)
        content = content_by_filename.get(filename)
        if not isinstance(binding, Mapping) or binding.get("path") != filename:
            raise ValueError(f"outputs.{name}.path must be {filename}")
        if records is None or content is None:
            raise ValueError(f"missing bound lineage file: {filename}")
        if binding.get("content_hash") != _hash_bytes(content):
            raise ValueError(f"outputs.{name}.content_hash does not match {filename}")
        if binding.get("record_count") != len(records):
            raise ValueError(f"outputs.{name}.record_count does not match {filename}")
        named_records[name] = records
    summary = validate_lineage_records(
        named_records["accepted_responses"],
        named_records["raw_provider_returns"],
        named_records["rejected_attempts"],
        named_records["dispatch_audit"],
        manifest=manifest,
    )
    usage = manifest.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("lineage-bound manifest requires usage accounting")
    for field in (
        "unique_job_slots_dispatched",
        "accepted_response_records",
        "accepted_unique_replicates",
        "total_model_calls",
        "rejected_attempts",
    ):
        if usage.get(field) != summary[field]:
            raise ValueError(f"manifest usage.{field} disagrees with bound lineage")
    planned = usage.get("unique_job_slots_planned")
    if (
        isinstance(planned, bool)
        or not isinstance(planned, int)
        or planned < summary["unique_job_slots_dispatched"]
    ):
        raise ValueError("planned slots must cover dispatched job slots")
    return summary


def _validate_lineage(
    workflow: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    responses = _records(workflow.get("responses"), "responses")
    raw_returns = _records(
        workflow.get("raw_provider_returns"), "raw_provider_returns"
    )
    rejected = _records(workflow.get("rejected_attempts"), "rejected_attempts")
    dispatch_audit = _records(workflow.get("dispatch_audit"), "dispatch_audit")
    if not raw_returns:
        raise ValueError("raw_provider_returns must contain every provider/model call")

    raw_by_id: dict[str, dict[str, Any]] = {}
    raw_keys: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(raw_returns):
        context = f"raw_provider_returns[{index}]"
        provider_id = _stable_id(raw, "provider_return_id", context)
        _stable_id(raw, "synthetic_replicate_id", context)
        _stable_id(raw, "reviewer_dispatch_id", context)
        if provider_id in raw_by_id:
            raise ValueError(f"duplicate provider_return_id: {provider_id}")
        if raw.get("stage") not in {"reaction", "comparison"}:
            raise ValueError(f"{context}.stage is invalid")
        if not isinstance(raw.get("attempt_number"), int) or isinstance(
            raw.get("attempt_number"), bool
        ):
            raise ValueError(f"{context}.attempt_number must be an integer")
        if not isinstance(raw.get("accepted"), bool):
            raise ValueError(f"{context}.accepted must be a boolean")
        validation_errors = raw.get("validation_errors")
        if not isinstance(validation_errors, list):
            raise ValueError(f"{context}.validation_errors must be an array")
        if raw.get("accepted") and validation_errors:
            raise ValueError(f"{context} accepted return cannot retain validation errors")
        if not raw.get("accepted") and not validation_errors:
            raise ValueError(f"{context} rejected return requires validation errors")
        if "raw_return" not in raw:
            raise ValueError(f"{context}.raw_return is required")
        key = _attempt_key(raw)
        if key in raw_keys:
            raise ValueError(f"duplicate raw attempt identity: {key}")
        raw_keys.add(key)
        raw_by_id[provider_id] = raw

    rejected_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(rejected):
        context = f"rejected_attempts[{index}]"
        provider_id = _stable_id(item, "provider_return_id", context)
        if provider_id in rejected_by_id:
            raise ValueError(f"duplicate rejected provider_return_id: {provider_id}")
        raw = raw_by_id.get(provider_id)
        if raw is None or raw.get("accepted") is not False:
            raise ValueError(f"{context} must identify a rejected raw provider return")
        for field in (
            "synthetic_replicate_id",
            "reviewer_dispatch_id",
            "stage",
            "attempt_number",
        ):
            if item.get(field) != raw.get(field):
                raise ValueError(f"{context}.{field} must match its raw return")
        if item.get("stage") == "reaction" and item.get("position_seen") != raw.get(
            "position_seen"
        ):
            raise ValueError(f"{context}.position_seen must match its raw return")
        if item.get("validation_errors") != raw.get("validation_errors"):
            raise ValueError(f"{context}.validation_errors must match its raw return")
        rejected_by_id[provider_id] = item
    expected_rejected = {
        provider_id for provider_id, raw in raw_by_id.items() if not raw["accepted"]
    }
    if set(rejected_by_id) != expected_rejected:
        raise ValueError("rejected_attempts must exactly cover rejected raw returns")

    seen_responses: set[str] = set()
    seen_replicates: set[str] = set()
    for index, response in enumerate(responses):
        errors = validate_response(response)
        if errors:
            raise ValueError(
                f"responses[{index}] is invalid: " + "; ".join(errors)
            )
        response_id = _stable_id(response, "response_id", f"responses[{index}]")
        replicate_id = _stable_id(
            response, "synthetic_replicate_id", f"responses[{index}]"
        )
        dispatch_id = _stable_id(
            response, "reviewer_dispatch_id", f"responses[{index}]"
        )
        if response_id in seen_responses:
            raise ValueError(f"duplicate accepted response_id: {response_id}")
        if replicate_id in seen_replicates:
            raise ValueError(
                f"duplicate accepted synthetic_replicate_id: {replicate_id}"
            )
        seen_responses.add(response_id)
        seen_replicates.add(replicate_id)
        for attempt_index, attempt in enumerate(response["runtime_attempts"]):
            provider_id = _stable_id(
                attempt,
                "provider_return_id",
                f"responses[{index}].runtime_attempts[{attempt_index}]",
            )
            raw = raw_by_id.get(provider_id)
            if raw is None:
                raise ValueError(
                    f"accepted response attempt {provider_id} has no raw provider return"
                )
            expected_outcome = "accepted" if raw["accepted"] else "rejected"
            if attempt.get("outcome") != expected_outcome:
                raise ValueError(f"runtime attempt {provider_id} outcome disagrees with raw")
            if attempt.get("validation_errors") != raw.get("validation_errors"):
                raise ValueError(
                    f"runtime attempt {provider_id} validation errors disagree with raw"
                )
            if attempt.get("stage") != raw.get("stage"):
                raise ValueError(f"runtime attempt {provider_id} stage disagrees with raw")
            if attempt.get("attempt_number") != raw.get("attempt_number"):
                raise ValueError(
                    f"runtime attempt {provider_id} attempt number disagrees with raw"
                )
            if dispatch_id != raw.get("reviewer_dispatch_id"):
                raise ValueError(
                    f"runtime attempt {provider_id} dispatch ID disagrees with raw"
                )

    audit_dispatches: set[str] = set()
    audit_replicates: set[str] = set()
    for index, item in enumerate(dispatch_audit):
        dispatch_id = _stable_id(
            item, "reviewer_dispatch_id", f"dispatch_audit[{index}]"
        )
        replicate_id = _stable_id(
            item, "synthetic_replicate_id", f"dispatch_audit[{index}]"
        )
        if dispatch_id in audit_dispatches or replicate_id in audit_replicates:
            raise ValueError("dispatch_audit job-slot IDs must be unique")
        audit_dispatches.add(dispatch_id)
        audit_replicates.add(replicate_id)
    raw_dispatches = {str(item["reviewer_dispatch_id"]) for item in raw_returns}
    if dispatch_audit and not raw_dispatches.issubset(audit_dispatches):
        raise ValueError("raw returns contain a dispatch outside dispatch_audit")
    dispatched_slots = len(audit_dispatches or raw_dispatches)
    requested = workflow.get("requested_replicates")
    if (
        not isinstance(requested, int)
        or isinstance(requested, bool)
        or requested != dispatched_slots
    ):
        raise ValueError(
            "requested_replicates must equal unique dispatched job slots"
        )
    completed = workflow.get("completed_replicates")
    if completed != len(seen_replicates):
        raise ValueError(
            "completed_replicates must equal accepted unique replicate records"
        )
    if dispatched_slots > manifest.get("maximum_synthetic_panelists", -1):
        raise ValueError("dispatched job slots exceed the manifest ceiling")
    return responses, raw_returns, rejected, dispatched_slots


def materialize_workflow_lineage(
    workflow: Mapping[str, Any], manifest: Mapping[str, Any], run_dir: Path
) -> dict[str, Any]:
    """Write canonical lineage files and return the bound manifest payload."""

    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        raise ValueError("manifest is invalid: " + "; ".join(manifest_errors))
    responses = _records(workflow.get("responses"), "responses")
    raw_returns = _records(
        workflow.get("raw_provider_returns"), "raw_provider_returns"
    )
    rejected = _records(workflow.get("rejected_attempts"), "rejected_attempts")
    dispatch_audit = _records(workflow.get("dispatch_audit"), "dispatch_audit")
    summary = validate_lineage_records(
        responses,
        raw_returns,
        rejected,
        dispatch_audit,
        manifest=manifest,
    )
    dispatched_slots = summary["unique_job_slots_dispatched"]
    expected_status = (
        "complete"
        if summary["accepted_response_records"] == dispatched_slots
        else "incomplete"
    )
    if workflow.get("status") != expected_status:
        raise ValueError(
            f"workflow.status must be {expected_status} for its composite response coverage"
        )
    requested = workflow.get("requested_replicates")
    if requested != dispatched_slots:
        raise ValueError("requested_replicates must equal dispatched audit slots")
    if workflow.get("completed_replicates") != len(responses):
        raise ValueError("completed_replicates must equal accepted responses")
    run_dir = run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "accepted_responses": (
            "panelist-responses.jsonl",
            _jsonl_bytes(responses),
        ),
        "raw_provider_returns": (
            "raw-provider-returns.jsonl",
            _jsonl_bytes(raw_returns),
        ),
        "rejected_attempts": (
            "rejected-attempts.jsonl",
            _jsonl_bytes(rejected),
        ),
        "dispatch_audit": (
            "dispatch-audit.jsonl",
            _jsonl_bytes(dispatch_audit),
        ),
    }
    bindings: dict[str, dict[str, Any]] = {}
    for name, (filename, content) in files.items():
        (run_dir / filename).write_bytes(content)
        bindings[name] = {
            "path": filename,
            "content_hash": _hash_bytes(content),
            "record_count": content.count(b"\n"),
        }

    payload = json.loads(json.dumps(manifest))
    outputs = payload.setdefault("outputs", {})
    outputs.update(bindings)
    stage_counts = Counter(record["record_type"] for record in responses)
    stage_replicates: dict[str, set[str]] = {}
    for record in responses:
        stage_replicates.setdefault(record["record_type"], set()).add(
            record["synthetic_replicate_id"]
        )
    capacity = payload.get("synthetic_replicate_capacity", {})
    planned_slots = capacity.get("required_total") if isinstance(capacity, Mapping) else None
    if planned_slots is None and isinstance(capacity, Mapping):
        components = (
            capacity.get("screening_planned"),
            capacity.get("boundary_reserved"),
            capacity.get("finalist_reserved"),
        )
        if all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in components
        ):
            planned_slots = sum(components)
    payload["usage"] = {
        "unique_job_slots_planned": planned_slots,
        "unique_job_slots_dispatched": dispatched_slots,
        "accepted_response_records": len(responses),
        "accepted_unique_replicates": len(
            {record["synthetic_replicate_id"] for record in responses}
        ),
        "total_model_calls": len(raw_returns),
        "rejected_attempts": len(rejected),
        "dispatched_job_slots_by_stage": summary["dispatched_job_slots_by_stage"],
        "accepted_response_records_by_stage": dict(sorted(stage_counts.items())),
        "accepted_unique_replicates_by_stage": {
            stage: len(replicates)
            for stage, replicates in sorted(stage_replicates.items())
        },
    }
    records_by_filename = {
        "panelist-responses.jsonl": responses,
        "raw-provider-returns.jsonl": raw_returns,
        "rejected-attempts.jsonl": rejected,
        "dispatch-audit.jsonl": dispatch_audit,
    }
    content_by_filename = {
        filename: content for filename, content in files.values()
    }
    validate_bound_lineage(payload, records_by_filename, content_by_filename)
    (run_dir / "study-manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "CANONICAL_LINEAGE_FILES",
    "materialize_workflow_lineage",
    "validate_bound_lineage",
    "validate_lineage_records",
]
