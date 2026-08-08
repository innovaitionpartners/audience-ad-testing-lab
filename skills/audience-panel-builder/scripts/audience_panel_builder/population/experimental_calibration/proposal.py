"""Seal a synthetic-only, non-executable persona-behavior update intent."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_identifier,
    require_timestamp,
    sha256_json,
)
from .contracts import (
    PROPOSAL_VERSION,
    validate_creative_attribute_registry,
    validate_diagnosis,
    validate_evidence_library,
    validate_evidence_receipt,
    validate_experimental_proposal,
    validate_study_manifest,
)
from .diagnosis import diagnose_persona_behavior, registered_behavior_hypotheses


class ProposalNotPermitted(ContractError):
    """The diagnosis is an abstention/invalid state and seals no proposal."""


def build_bounded_profile_snapshot_operation(
    *,
    target_persona_id: str,
    target_persona_field: str,
    proposed_value: object,
) -> dict[str, object]:
    """Build the one-persona, one-behavior operation shared by sandbox and C2."""

    persona_id = require_identifier(target_persona_id, "target_persona_id")
    if target_persona_field not in {
        "anxieties", "decision_context", "motivations", "proof_needs",
        "role_context",
    }:
        raise ContractError("target_persona_field is not a behavioral field")
    if isinstance(proposed_value, str):
        if not proposed_value:
            raise ContractError("proposed_value must not be empty")
    elif (
        not isinstance(proposed_value, list)
        or not proposed_value
        or any(not isinstance(item, str) or not item for item in proposed_value)
    ):
        raise ContractError(
            "proposed_value must be a non-empty string or string array"
        )
    return {
        "operation_type": "profile_snapshot_update",
        "target_persona_id": persona_id,
        "changed_fields": [target_persona_field],
        "proposed_after": {
            target_persona_field: deepcopy(proposed_value),
        },
    }


def _closed_panel_binding(value: Mapping[str, object]) -> dict[str, object]:
    keys = {
        "panel_id",
        "panel_version",
        "panel_sha256",
        "persona_id",
        "persona_snapshot_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ContractError("base_panel_binding must be a closed panel binding")
    return deepcopy(dict(value))


def _projection_preimage(snapshot: Mapping[str, object]) -> dict[str, object]:
    result = deepcopy(dict(snapshot))
    result["library_sha256"] = None
    receipt = result.get("head_receipt")
    if isinstance(receipt, Mapping):
        receipt_copy = deepcopy(dict(receipt))
        receipt_copy["projection_sha256"] = None
        receipt_copy["receipt_sha256"] = None
        result["head_receipt"] = receipt_copy
    return result


def build_experimental_proposal(
    *,
    base_panel_binding: Mapping[str, object],
    study_manifest: dict[str, object],
    scenario_manifests: list[dict[str, object]],
    experiment_designs: list[dict[str, object]],
    diagnosis: dict[str, object],
    attribute_registry: dict[str, object],
    evidence_library_snapshot: dict[str, object],
    evidence_head_receipt: dict[str, object],
    alternative_causes: Mapping[str, Mapping[str, object]],
    proposal_id: str,
    proposed_at: str,
) -> dict[str, object]:
    """Return a no-change record or one exact non-executable update intent."""

    panel = _closed_panel_binding(base_panel_binding)
    manifest = validate_study_manifest(study_manifest)
    checked_diagnosis = validate_diagnosis(diagnosis)
    registry = validate_creative_attribute_registry(attribute_registry)
    snapshot = validate_evidence_library(evidence_library_snapshot)
    receipt = validate_evidence_receipt(evidence_head_receipt)
    proposal_identifier = require_identifier(proposal_id, "proposal_id")
    proposal_timestamp = (
        require_timestamp(proposed_at, "proposed_at")
        .isoformat()
        .replace("+00:00", "Z")
    )
    recomputed_diagnosis = diagnose_persona_behavior(
        base_panel_binding=panel,
        study_manifest=manifest,
        scenario_manifests=scenario_manifests,
        experiment_designs=experiment_designs,
        evidence_library_snapshot=snapshot,
        evidence_head_receipt=receipt,
        attribute_registry=registry,
        alternative_causes=alternative_causes,
        diagnosis_id=str(checked_diagnosis["diagnosis_id"]),
        diagnosed_at=str(checked_diagnosis["diagnosed_at"]),
    )
    if canonical_json_bytes(recomputed_diagnosis) != canonical_json_bytes(
        checked_diagnosis
    ):
        raise ContractError(
            "proposal diagnosis does not byte-match the recomputed frozen diagnosis"
        )
    if checked_diagnosis["decision"] not in {
        "repeatable_behavioral_miss",
        "no_repeatable_miss",
    }:
        raise ProposalNotPermitted(
            "this diagnosis is an abstention or invalid result and seals no proposal"
        )
    if checked_diagnosis["base_panel_binding"] != panel:
        raise ContractError("diagnosis base-panel binding is stale")
    expected_study = {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
    }
    if checked_diagnosis["synthetic_study_binding"] != expected_study:
        raise ContractError("diagnosis study binding is stale")
    if (
        snapshot["head_receipt"] != receipt
        or receipt["library_id"] != snapshot["library_id"]
        or receipt["projection_sha256"] != sha256_json(_projection_preimage(snapshot))
    ):
        raise ContractError(
            "proposal evidence projection does not authenticate to its receipt"
        )
    projection_binding = {
        "library_id": snapshot["library_id"],
        "as_of": snapshot["as_of"],
        "library_sha256": snapshot["library_sha256"],
        "projection_sha256": receipt["projection_sha256"],
    }
    receipt_binding = {
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "event_count": receipt["event_count"],
        "event_sha256": receipt["event_sha256"],
        "projection_sha256": receipt["projection_sha256"],
    }
    if (
        checked_diagnosis["evidence_library_projection_binding"]
        != projection_binding
        or checked_diagnosis["evidence_head_receipt_binding"]
        != receipt_binding
    ):
        raise ContractError("diagnosis evidence binding is stale")
    frozen = checked_diagnosis["frozen_analysis_bindings"]
    if (
        frozen["diagnosis_method_sha256"]
        != sha256_json(manifest["diagnosis_method"])
        or frozen["monte_carlo_error_method_sha256"]
        != sha256_json(manifest["monte_carlo_error_targets"])
        or frozen["stopping_rule_sha256"]
        != sha256_json(manifest["stopping_rule"])
        or frozen["creative_attribute_registry_sha256"]
        != registry["registry_sha256"]
    ):
        raise ContractError("diagnosis frozen analysis binding is stale")

    operation = None
    proposal_type = "no_change"
    effect_direction = "none"
    effect_boundary = "no_change_supported_in_fixture"
    mcse = None
    if checked_diagnosis["decision"] == "repeatable_behavioral_miss":
        selected = checked_diagnosis["selected_hypothesis"]
        assert isinstance(selected, Mapping)
        definitions = [
            definition
            for definition in registered_behavior_hypotheses(registry)
            if definition["hypothesis_id"] == selected["hypothesis_id"]
        ]
        if len(definitions) != 1:
            raise ContractError(
                "selected diagnosis hypothesis is not uniquely preregistered"
            )
        definition = definitions[0]
        registered = definition
        if (
            definition["attribute_id"] != selected["attribute_id"]
            or registered["target_persona_id"]
            != selected["target_persona_id"]
            or registered["target_persona_field"]
            != selected["target_persona_field"]
            or registered["proposed_value"] != selected["proposed_value"]
            or registered["rationale_template"]
            != selected["rationale_template"]
            or selected["target_persona_id"] != panel["persona_id"]
        ):
            raise ContractError(
                "selected diagnosis hypothesis does not equal the frozen registry"
            )
        field = str(selected["target_persona_field"])
        evidence_ids = list(selected["evidence_entry_ids"])
        operation = {
            **build_bounded_profile_snapshot_operation(
                target_persona_id=str(selected["target_persona_id"]),
                target_persona_field=field,
                proposed_value=selected["proposed_value"],
            ),
            "hypothesis_id": selected["hypothesis_id"],
            "evidence_sha256": list(selected["evidence_sha256"]),
            "creative_attribute_registry_sha256": registry["registry_sha256"],
            "rationale": (
                f"{registered['rationale_template']} "
                f"Associated synthetic evidence entries: {', '.join(evidence_ids)}."
            ),
            "constraints": [
                "One existing persona behavior field only.",
                "Synthetic hypothesis to test; no production authority.",
            ],
            "reversibility": "sandbox_reversible",
        }
        proposal_type = "profile_snapshot_update"
        effect_direction = "positive"
        effect_boundary = "synthetic_hypothesis_to_test"
        mcse = deepcopy(
            checked_diagnosis["analysis"]["combined"][
                "monte_carlo_standard_error"
            ]
        )

    document: dict[str, object] = {
        "schema_version": PROPOSAL_VERSION,
        "proposal_id": proposal_identifier,
        "proposed_at": proposal_timestamp,
        "status": "experimental_only",
        "evidence_origin": "synthetic_fixture_only",
        "real_world_validation_status": "not_evaluated",
        "production_executable": False,
        "sandbox_candidate_materialization_permitted": True,
        "production_candidate_materialization_permitted": False,
        "activation_permitted": False,
        "active_panel_mutation_permitted": False,
        "base_panel_binding": panel,
        "base_panel_authority_status": "unverified_proposal_context",
        "synthetic_study_binding": expected_study,
        "evidence_library_projection_binding": projection_binding,
        "evidence_head_receipt_binding": receipt_binding,
        "frozen_analysis_bindings": deepcopy(frozen),
        "diagnosis": {
            "diagnosis_id": checked_diagnosis["diagnosis_id"],
            "diagnosis_sha256": checked_diagnosis["diagnosis_sha256"],
            "decision": checked_diagnosis["decision"],
        },
        "proposal_type": proposal_type,
        "operation": operation,
        "expected_effect": {
            "direction": effect_direction,
            "claim_boundary": effect_boundary,
        },
        "alternative_explanations": deepcopy(
            checked_diagnosis["alternative_causes"]
        ),
        "assumptions": {
            "synthetic_fixture_only": True,
            "panel_validation_deferred_to_candidate_materialization": True,
        },
        "uncertainty": {
            "status": "experimental",
            "monte_carlo_standard_error": mcse,
        },
        "known_risks": [
            "Synthetic results may not transfer to real campaign behavior.",
            "The proposed behavioral explanation may be wrong.",
        ],
        "required_review": {
            "status": "required",
            "real_world_evidence_required_for_activation": True,
        },
        "reversibility": {
            "status": "sandbox_candidate_can_be_discarded",
        },
        "limitations": [
            "Synthetic fixtures prove mechanics only.",
            "No real-world validation, calibration, improvement, causation, "
            "preference, production registration, or activation is claimed.",
        ],
        "proposal_sha256": None,
    }
    document["proposal_sha256"] = sha256_json(document)
    return validate_experimental_proposal(document)
