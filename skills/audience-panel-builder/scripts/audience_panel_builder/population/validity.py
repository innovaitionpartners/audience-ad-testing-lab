"""Pure dimensional validity assessment for provisional population frames."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

from ..common import ContractError, sha256_json
from ..evidence import build_evidence_ledger


SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    VALIDITY_PROFILE_VERSION,
    validate_composition_plan,
    validate_frame_request,
    validate_outcome_feedback,
    validate_population_frame,
    validate_validity_profile,
)


def _axis(
    status: str,
    coverage: float | None,
    limitations: Sequence[str],
) -> dict[str, object]:
    return {
        "status": status,
        "coverage": coverage,
        "limitations": list(limitations),
    }


def assess_population_validity(
    *,
    frame_request: dict[str, object],
    population_frame: dict[str, object] | None,
    overlay_evidence: Sequence[dict[str, object]],
    outcome_feedback: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Assess five independent axes without creating panel-stage identities."""

    try:
        request = validate_frame_request(frame_request)
        frame = (
            None
            if population_frame is None
            else validate_population_frame(population_frame)
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if frame is not None and frame["frame_request_id"] != request["request_id"]:
        raise ContractError("population frame must bind the exact frame request")
    if frame is not None and frame["frame_request_sha256"] != sha256_json(request):
        raise ContractError("population frame request hash does not match")
    if not isinstance(overlay_evidence, Sequence) or isinstance(
        overlay_evidence, (str, bytes, bytearray)
    ):
        raise ContractError("overlay_evidence must be a sequence")
    if overlay_evidence:
        build_evidence_ledger(
            str(request["request_id"]),
            overlay_evidence,
            created_at="1970-01-01T00:00:00Z",
        )
    else:
        # Reject non-JSON values consistently without inventing an overlay score.
        json.dumps([], allow_nan=False)
    if not isinstance(outcome_feedback, Sequence) or isinstance(
        outcome_feedback, (str, bytes, bytearray)
    ):
        raise ContractError("outcome_feedback must be a sequence")
    heldout = []
    for feedback in outcome_feedback:
        try:
            validated = validate_outcome_feedback(feedback)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        canonical = validated["canonical_copy"]
        if canonical["holdout"] and canonical["source"]["permission_confirmed"]:
            heldout.append(validated["source_digest"])
    heldout = sorted(set(heldout))

    no_frame = frame is None or frame["eligibility"] == "no_defensible_frame"
    experimental = (
        frame is not None and frame["eligibility"] == "experimental"
    )
    if no_frame:
        structural = _axis(
            "insufficient",
            0.0,
            [
                (
                    "No defensible population frame exists."
                    if frame is None
                    else frame["downgrade_reason"]
                )
            ],
        )
    elif experimental:
        structural = _axis(
            "directional",
            1.0,
            [frame["downgrade_reason"]],
        )
    else:
        structural = _axis(
            "supported",
            1.0,
            list(frame["coverage_assessment"]["known_gaps"]),
        )
    axes = {
        "structural_frame": structural,
        "overlay_evidence": (
            _axis(
                "directional",
                None,
                [
                    "Overlay evidence informs hypotheses but does not "
                    "establish structural prevalence."
                ],
            )
            if overlay_evidence
            else _axis(
                "not_available",
                None,
                ["No validated overlay evidence was supplied."],
            )
        ),
        "allocation_fidelity": _axis(
            "not_available",
            None,
            ["B1 does not create or assess run allocations."],
        ),
        "outcome_calibration": (
            _axis(
                "directional",
                None,
                [
                    "Held-out aggregate outcome feedback is bound read-only; "
                    "it does not mutate the frame or establish Tier 4."
                ],
            )
            if heldout
            else _axis(
                "not_available",
                None,
                ["No valid held-out outcome feedback was supplied."],
            )
        ),
        "external_validation": _axis(
            "not_available",
            None,
            [
                "No predeclared panel-stage external validation design is "
                "available at the frame-provisional stage."
            ],
        ),
    }
    frame_result = (
        sha256_json(frame)
        if frame is not None
        else sha256_json({
            "frame_request_sha256": sha256_json(request),
            "result": "not_built",
        })
    )
    payload = {
        "schema_version": VALIDITY_PROFILE_VERSION,
        "validity_id": f"{request['request_id']}-validity",
        "binding_state": "frame_provisional",
        "panel_id": None,
        "panel_tier": None,
        "evidence_basis": None,
        "axes": axes,
        "predeclared_validation_design": None,
        "held_out_outcome_evidence": heldout,
        "source_bindings": {
            "brief_sha256": None,
            "panel_sha256": None,
            "frame_result_sha256": frame_result,
            "frame_sha256": None if no_frame else frame_result,
            "composition_sha256": None,
        },
    }
    try:
        return validate_validity_profile(payload)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def finalize_validity_profile(
    *,
    provisional_validity: dict[str, object],
    population_frame: dict[str, object],
    composition_plan: dict[str, object],
    panel_id: str,
    panel_tier: str,
    evidence_basis: str,
    brief_sha256: str,
    panel_projection_sha256: str,
) -> dict[str, object]:
    """Bind a provisional profile to real panel-stage identities and digests.

    The caller supplies every final identity. This helper retains the exact
    provisional frame-result and usable-frame bindings, copies the provisional
    axes without alteration, and never derives or invents panel evidence.
    """

    try:
        provisional = validate_validity_profile(provisional_validity)
        frame = validate_population_frame(population_frame)
        composition = validate_composition_plan(
            composition_plan,
            frame=frame,
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if provisional["binding_state"] != "frame_provisional":
        raise ContractError(
            "provisional_validity must have binding_state frame_provisional"
        )
    frame_result_sha256 = sha256_json(frame)
    if (
        provisional["source_bindings"]["frame_result_sha256"]
        != frame_result_sha256
    ):
        raise ContractError(
            "provisional_validity frame_result_sha256 must bind the exact "
            "canonical population-frame result"
        )
    if composition["achieved_tier"] != panel_tier:
        raise ContractError(
            "panel_tier must equal the composition achieved_tier"
        )
    if composition["evidence_basis"] != evidence_basis:
        raise ContractError(
            "evidence_basis must equal the composition evidence_basis"
        )
    final = deepcopy(provisional)
    final.update({
        "binding_state": "panel_final",
        "panel_id": panel_id,
        "panel_tier": panel_tier,
        "evidence_basis": evidence_basis,
    })
    final["source_bindings"] = {
        "brief_sha256": brief_sha256,
        "panel_sha256": panel_projection_sha256,
        "frame_result_sha256": frame_result_sha256,
        "frame_sha256": composition["frame_binding"]["frame_sha256"],
        "composition_sha256": sha256_json(composition),
    }
    try:
        return validate_validity_profile(final)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
