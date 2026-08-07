"""Frozen pre-outcome deterministic response adapter.

This module is deliberately pure.  It accepts only explicit JSON-shaped
arguments and has no filesystem, environment, network, outcome, or oracle
surface.
"""

from __future__ import annotations


ADAPTER_ID = "frozen-synthetic-panelist-response"
ADAPTER_VERSION = "1.0.0"
DETERMINISTIC_TIE_RULE = "score-descending-creative-id-ascending"
FEATURE_ALLOWLIST = (
    "creative_attributes",
    "experiment_design",
    "persona_snapshot",
    "study_manifest",
)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("adapter feature values must be arrays of strings")
    return tuple(value)


def _stable_jitter(*values: str, seed: int) -> int:
    state = seed
    for value in values:
        for byte in value.encode("utf-8"):
            state = ((state * 131) + byte) % 1_000_003
    return state % 997


def synthetic_panelist_response(
    *,
    frozen_adapter_binding: dict[str, object],
    panelist_job: dict[str, object],
    persona_snapshot: dict[str, object],
    creative_attributes: list[dict[str, object]],
) -> dict[str, object]:
    """Map explicit pre-outcome features to one deterministic ranking."""

    if set(frozen_adapter_binding) != {
        "adapter_id",
        "version",
        "source_sha256",
        "feature_allowlist",
        "deterministic_tie_rule",
        "seed",
    }:
        raise ValueError("frozen adapter binding is not closed")
    if (
        frozen_adapter_binding["adapter_id"] != ADAPTER_ID
        or frozen_adapter_binding["version"] != ADAPTER_VERSION
        or frozen_adapter_binding["deterministic_tie_rule"]
        != DETERMINISTIC_TIE_RULE
        or tuple(frozen_adapter_binding["feature_allowlist"]) != FEATURE_ALLOWLIST
        or type(frozen_adapter_binding["seed"]) is not int
    ):
        raise ValueError("frozen adapter binding does not match this source")
    if set(panelist_job) != {
        "dispatch_id",
        "experiment_design",
        "study_manifest",
    }:
        raise ValueError("panelist job is not closed")
    if not isinstance(panelist_job["dispatch_id"], str):
        raise ValueError("dispatch_id must be a string")
    if not isinstance(panelist_job["experiment_design"], dict):
        raise ValueError("experiment_design must be an object")
    if not isinstance(panelist_job["study_manifest"], dict):
        raise ValueError("study_manifest must be an object")
    allowed_persona_keys = {
        "anxieties",
        "decision_context",
        "motivations",
        "proof_needs",
        "role_context",
    }
    if set(persona_snapshot) != allowed_persona_keys:
        raise ValueError("persona snapshot is not closed")

    persona_terms = set()
    for key in ("anxieties", "motivations", "proof_needs"):
        persona_terms.update(item.casefold() for item in _strings(persona_snapshot[key]))
    for key in ("decision_context", "role_context"):
        value = persona_snapshot[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        persona_terms.add(value.casefold())

    seed = frozen_adapter_binding["seed"]
    rows: list[dict[str, object]] = []
    for index, raw in enumerate(creative_attributes):
        if set(raw) != {"creative_id", "attributes"}:
            raise ValueError(f"creative_attributes[{index}] is not closed")
        creative_id = raw["creative_id"]
        if not isinstance(creative_id, str):
            raise ValueError(f"creative_attributes[{index}].creative_id must be a string")
        attributes = _strings(raw["attributes"])
        overlap = sum(
            1
            for attribute in attributes
            if any(
                attribute.casefold() in term or term in attribute.casefold()
                for term in persona_terms
            )
        )
        score = overlap * 1000 + _stable_jitter(
            panelist_job["dispatch_id"],
            creative_id,
            seed=seed,
        )
        rows.append({"creative_id": creative_id, "score": score})

    rows.sort(key=lambda row: (-row["score"], row["creative_id"]))
    return {
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "dispatch_id": panelist_job["dispatch_id"],
        "tie_rule": DETERMINISTIC_TIE_RULE,
        "ranking": [
            {
                "position": position,
                "creative_id": row["creative_id"],
                "score": row["score"],
            }
            for position, row in enumerate(rows, start=1)
        ],
    }


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DETERMINISTIC_TIE_RULE",
    "FEATURE_ALLOWLIST",
    "synthetic_panelist_response",
]
