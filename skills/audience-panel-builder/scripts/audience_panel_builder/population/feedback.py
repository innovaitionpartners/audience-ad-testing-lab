"""Pure, deterministic binding of aggregate outcome feedback to a saved panel."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from pathlib import Path
import sys
from typing import Any

from ..common import (
    ContractError,
    require_identifier,
    require_string,
    require_timestamp,
    sha256_json,
)


SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_outcome_feedback,
    validate_saved_panel_v3,
)


FEEDBACK_BINDING_VERSION = "panel-outcome-feedback-binding-v1"
CALIBRATION_PROPOSAL_VERSION = "panel-calibration-refresh-proposal-v1"
_PREFIXED_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BINDING_KEYS = {
    "schema_version",
    "binding_id",
    "bound_at",
    "panel_binding",
    "study_id",
    "variant_ids",
    "cohort_identities",
    "metric_identities",
    "source_identities",
    "feedback_records",
    "limitations",
    "binding_sha256",
}


def _sequence(value: object, path: str) -> list[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise ContractError(f"{path} must be a sequence")
    result = list(value)
    if not result:
        raise ContractError(f"{path} must not be empty")
    return result


def _canonical_panel(value: object) -> dict[str, object]:
    """Validate the exact standalone saved-panel-v3 identity and surface."""

    try:
        panel = validate_saved_panel_v3(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if panel != value:
        raise ContractError("panel must be the canonical saved-audience-panel-v3")
    return panel


def _canonical_feedback(value: object, *, index: int) -> dict[str, object]:
    try:
        result = validate_outcome_feedback(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    feedback = result["canonical_copy"]
    if not isinstance(feedback, dict):
        raise ContractError(
            f"feedback_documents[{index}] did not produce a canonical object"
        )
    if feedback["source"]["permission_confirmed"] is not True:
        raise ContractError(
            f"feedback_documents[{index}].source.permission_confirmed must be true"
        )
    aggregate = feedback["aggregate"]
    if (
        aggregate["value"] is None
        and (
            aggregate["numerator"] is None
            or aggregate["denominator"] is None
        )
    ):
        raise ContractError(
            f"feedback_documents[{index}].aggregate numerator and denominator "
            "are both required when aggregate value is absent"
        )
    return feedback


def _panel_binding(panel: Mapping[str, object]) -> dict[str, object]:
    return {
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "panel_sha256": sha256_json(panel),
        "panel_tier": panel["panel_tier"],
        "evidence_basis": panel["evidence_basis"],
        "population_frame_result_sha256":
            panel["population_frame_result_sha256"],
        "population_frame_sha256": panel["population_frame_sha256"],
        "composition_plan_sha256": panel["composition_plan_sha256"],
        "validity_profile_sha256": panel["validity_profile_sha256"],
        "authorized_handoff_sha256": panel["authorized_handoff_sha256"],
        "claim_boundary": panel["claim_boundary"],
        "package_status": panel["package_status"],
    }


def _metric_identity(feedback: Mapping[str, object]) -> dict[str, object]:
    return {
        "name": feedback["metric"]["name"],
        "definition": feedback["metric"]["definition"],
        "direction": feedback["metric_direction"],
        "exposure_unit": feedback["units"]["exposure"],
        "outcome_unit": feedback["units"]["outcome"],
        "measurement_window": feedback["windows"]["measurement"],
        "attribution_window": feedback["windows"]["attribution"],
    }


def _sort_key(value: Mapping[str, object]) -> tuple[str, ...]:
    return (
        str(value["feedback_id"]),
        str(value["study_id"]),
        str(value["variant_id"]),
        str(value["cohort_id"]),
        str(value["metric"]["name"]),
        str(value["source"]["source_id"]),
    )


def _unique_objects(
    values: Sequence[dict[str, object]],
    *,
    key,
) -> list[dict[str, object]]:
    by_canonical: dict[str, dict[str, object]] = {}
    for value in values:
        by_canonical[sha256_json(value)] = value
    return sorted(by_canonical.values(), key=key)


def bind_outcome_feedback(
    *,
    panel: dict[str, object],
    feedback_documents: Sequence[dict[str, object]],
    binding_id: str,
    bound_at: str,
) -> dict[str, object]:
    """Bind aggregate feedback as immutable metadata, never as a panel update."""

    canonical_panel = _canonical_panel(panel)
    require_identifier(binding_id, "binding_id")
    require_timestamp(bound_at, "bound_at")
    canonical_feedback = [
        _canonical_feedback(value, index=index)
        for index, value in enumerate(
            _sequence(feedback_documents, "feedback_documents")
        )
    ]
    panel_id = str(canonical_panel["panel_id"])
    for index, feedback in enumerate(canonical_feedback):
        if feedback["panel_id"] != panel_id:
            raise ContractError(
                f"feedback_documents[{index}].panel_id must match panel.panel_id"
            )

    study_ids = {str(feedback["study_id"]) for feedback in canonical_feedback}
    if len(study_ids) != 1:
        raise ContractError(
            "one feedback binding must contain exactly one study_id"
        )
    study_id = next(iter(study_ids))

    seen_feedback_ids: set[str] = set()
    cohort_identities: dict[str, dict[str, object]] = {}
    source_hashes: dict[str, str] = {}
    for index, feedback in enumerate(canonical_feedback):
        feedback_id = str(feedback["feedback_id"])
        if feedback_id in seen_feedback_ids:
            raise ContractError(
                f"feedback_documents[{index}].feedback_id is duplicated"
            )
        seen_feedback_ids.add(feedback_id)

        cohort_id = str(feedback["cohort_id"])
        cohort_identity = {
            "cohort_id": cohort_id,
            "exposure_unit": feedback["units"]["exposure"],
            "measurement_window": feedback["windows"]["measurement"],
        }
        previous_cohort = cohort_identities.get(cohort_id)
        if previous_cohort is not None and previous_cohort != cohort_identity:
            raise ContractError(
                f"feedback_documents[{index}] reuses {cohort_id!r} with an "
                "incompatible cohort identity"
            )
        cohort_identities[cohort_id] = cohort_identity

        source_id = str(feedback["source"]["source_id"])
        source_hash = str(feedback["source_sha256"])
        previous_hash = source_hashes.get(source_id)
        if previous_hash is not None and previous_hash != source_hash:
            raise ContractError(
                f"feedback_documents[{index}] gives source_id {source_id!r} "
                "conflicting source hashes"
            )
        source_hashes[source_id] = source_hash

    canonical_feedback.sort(key=_sort_key)
    metric_identities = _unique_objects(
        [_metric_identity(feedback) for feedback in canonical_feedback],
        key=lambda value: (
            str(value["name"]),
            str(value["definition"]),
            str(value["direction"]),
            str(value["exposure_unit"]),
            str(value["outcome_unit"]),
            str(value["measurement_window"]),
            str(value["attribution_window"]),
        ),
    )
    source_identities = [
        {
            "source_id": source_id,
            "source_sha256": source_hashes[source_id],
        }
        for source_id in sorted(source_hashes)
    ]
    limitations = sorted({
        limitation
        for feedback in canonical_feedback
        for limitation in feedback["limitations"]
    })
    binding = {
        "schema_version": FEEDBACK_BINDING_VERSION,
        "binding_id": binding_id,
        "bound_at": bound_at,
        "panel_binding": _panel_binding(canonical_panel),
        "study_id": study_id,
        "variant_ids": sorted({
            str(feedback["variant_id"]) for feedback in canonical_feedback
        }),
        "cohort_identities": [
            cohort_identities[cohort_id]
            for cohort_id in sorted(cohort_identities)
        ],
        "metric_identities": metric_identities,
        "source_identities": source_identities,
        "feedback_records": [
            {
                "feedback_id": feedback["feedback_id"],
                "feedback_sha256": sha256_json(feedback),
                "study_id": feedback["study_id"],
                "variant_id": feedback["variant_id"],
                "cohort_id": feedback["cohort_id"],
                "metric": deepcopy(feedback["metric"]),
                "metric_direction": feedback["metric_direction"],
                "units": deepcopy(feedback["units"]),
                "windows": deepcopy(feedback["windows"]),
                "aggregate": deepcopy(feedback["aggregate"]),
                "design": feedback["design"],
                "source": {
                    **deepcopy(feedback["source"]),
                    "source_sha256": feedback["source_sha256"],
                },
                "holdout": feedback["holdout"],
                "evaluation_set": (
                    "held_out" if feedback["holdout"] else "in_sample"
                ),
                "missingness": feedback["missingness"],
                "limitations": deepcopy(feedback["limitations"]),
                "canonical_feedback": deepcopy(feedback),
            }
            for feedback in canonical_feedback
        ],
        "limitations": limitations,
        "binding_sha256": None,
    }
    binding["binding_sha256"] = sha256_json(binding)
    return binding


def _validate_feedback_binding(
    value: object,
    *,
    panel: dict[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError("feedback_binding must be an object")
    unknown = sorted(set(value) - _BINDING_KEYS)
    missing = sorted(_BINDING_KEYS - set(value))
    if unknown:
        raise ContractError(
            "feedback_binding has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise ContractError(
            "feedback_binding is missing fields: " + ", ".join(missing)
        )
    supplied = deepcopy(dict(value))
    binding_sha256 = require_string(
        supplied["binding_sha256"],
        "feedback_binding.binding_sha256",
    )
    if not _PREFIXED_DIGEST.fullmatch(binding_sha256):
        raise ContractError(
            "feedback_binding.binding_sha256 must be a prefixed SHA-256 digest"
        )
    unhashed = deepcopy(supplied)
    unhashed["binding_sha256"] = None
    if sha256_json(unhashed) != binding_sha256:
        raise ContractError(
            "feedback_binding.binding_sha256 does not match the canonical binding"
        )
    panel_binding = supplied["panel_binding"]
    if (
        not isinstance(panel_binding, Mapping)
        or panel_binding.get("panel_sha256") != sha256_json(panel)
    ):
        raise ContractError(
            "feedback_binding panel does not match the supplied canonical panel"
        )
    feedback_records = supplied["feedback_records"]
    if not isinstance(feedback_records, list) or not feedback_records:
        raise ContractError(
            "feedback_binding.feedback_records must be a nonempty array"
        )
    documents = []
    for index, record in enumerate(feedback_records):
        if not isinstance(record, Mapping) or "canonical_feedback" not in record:
            raise ContractError(
                f"feedback_binding.feedback_records[{index}] is invalid"
            )
        documents.append(record["canonical_feedback"])
    rebuilt = bind_outcome_feedback(
        panel=panel,
        feedback_documents=documents,
        binding_id=require_identifier(
            supplied["binding_id"],
            "feedback_binding.binding_id",
        ),
        bound_at=require_string(
            supplied["bound_at"],
            "feedback_binding.bound_at",
        ),
    )
    if rebuilt != supplied:
        raise ContractError(
            "feedback_binding does not match its canonical feedback documents"
        )
    return supplied


def propose_calibration_refresh(
    *,
    panel: dict[str, object],
    feedback_binding: dict[str, object],
    proposal_id: str,
    proposed_at: str,
) -> dict[str, object]:
    """Emit an approval-required, non-executable read-only refresh proposal."""

    canonical_panel = _canonical_panel(panel)
    canonical_binding = _validate_feedback_binding(
        feedback_binding,
        panel=canonical_panel,
    )
    require_identifier(proposal_id, "proposal_id")
    require_timestamp(proposed_at, "proposed_at")
    if (
        canonical_binding["panel_binding"]["panel_sha256"]
        != sha256_json(canonical_panel)
    ):
        raise ContractError(
            "feedback_binding panel does not match the supplied canonical panel"
        )
    proposal = {
        "schema_version": CALIBRATION_PROPOSAL_VERSION,
        "proposal_id": proposal_id,
        "proposed_at": proposed_at,
        "status": "requires_calibration_approval",
        "executable": False,
        "panel_binding": deepcopy(canonical_binding["panel_binding"]),
        "feedback_binding": {
            "binding_id": canonical_binding["binding_id"],
            "binding_sha256": canonical_binding["binding_sha256"],
            "study_id": canonical_binding["study_id"],
        },
        "evaluation_scope": {
            "variant_ids": deepcopy(canonical_binding["variant_ids"]),
            "cohort_identities":
                deepcopy(canonical_binding["cohort_identities"]),
            "metric_identities":
                deepcopy(canonical_binding["metric_identities"]),
            "source_identities":
                deepcopy(canonical_binding["source_identities"]),
        },
        "diff": {
            "base_panel_sha256":
                canonical_binding["panel_binding"]["panel_sha256"],
            "proposed_panel_sha256": None,
            "operations": [],
        },
        "review_items": [
            "Review metric, cohort, source, window, and holdout coverage.",
            "Approve a separately versioned refresh before specifying changes.",
        ],
        "limitations": sorted(set(
            list(canonical_binding["limitations"])
            + [
                "Outcome feedback does not make synthetic results observed "
                "customer behavior.",
                "This proposal performs no calibration or panel update.",
            ]
        )),
        "proposal_sha256": None,
    }
    proposal["proposal_sha256"] = sha256_json(proposal)
    return proposal


__all__ = [
    "CALIBRATION_PROPOSAL_VERSION",
    "FEEDBACK_BINDING_VERSION",
    "bind_outcome_feedback",
    "propose_calibration_refresh",
]
