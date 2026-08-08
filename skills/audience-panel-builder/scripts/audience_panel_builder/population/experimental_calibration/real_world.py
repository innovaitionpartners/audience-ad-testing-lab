"""Experimental C2 calibration from authenticated real-world validation.

This module composes the existing C1 package authenticator, the existing
pre-outcome creative-attribute registry, the persona candidate helpers, the
workflow approval contract, and the immutable audience library.  It does not
create a second outcome evaluator or a second registration implementation.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Sequence

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_array,
    require_identifier,
    require_object,
    require_string,
    require_timestamp,
    sha256_json,
)
from ...workflow_state import (
    require_approved_scope,
    validate_workflow_state,
)
from ..validation.package import (
    read_authenticated_panel_snapshot,
    validate_validation_package,
)
from .candidate import (
    _behavior_snapshot,
    _canonical_panel,
    _changed_path_rows,
    _ensure_safe_new_directory,
    _find_persona,
    _matching_profiles,
    _panel_binding,
    _profile_rows,
    _version_tuple,
    _write_new,
    UnsafeCandidateOutput,
    build_persona_authoring_projection,
)
from .contracts import (
    ALLOWED_PERSONA_FIELDS,
    validate_creative_attribute_registry,
)
from .diagnosis import registered_behavior_hypotheses
from .evidence_library import (
    AUTHENTICATED_C1_PROJECTION_VERSION,
    build_authenticated_c1_evidence_projection,
    validate_authenticated_c1_evidence_projection,
)
from .proposal import build_bounded_profile_snapshot_operation


DIAGNOSIS_VERSION = "experimental-real-world-persona-behavior-diagnosis-v1"
EVIDENCE_PROJECTION_VERSION = AUTHENTICATED_C1_PROJECTION_VERSION
PROPOSAL_VERSION = "experimental-real-world-persona-behavior-proposal-v1"
CANDIDATE_VERSION = "experimental-real-world-persona-panel-candidate-v1"
DIFF_VERSION = "experimental-real-world-persona-diff-v1"
BUNDLE_VERSION = "experimental-real-world-persona-candidate-bundle-v1"
REGISTRATION_PROPOSAL_VERSION = (
    "experimental-real-world-panel-registration-proposal-v1"
)
ALTERNATIVE_CAUSES_VERSION = "real-world-persona-alternative-causes-v1"
CALIBRATION_HISTORY_ACTION = (
    "experimental_c2_candidate_requires_gated_registration"
)

REQUIRED_ALTERNATIVE_CAUSES = frozenset({
    "attribution",
    "delivery",
    "landing-page",
    "offer",
    "targeting",
    "timing",
    "tracking",
})

_DIAGNOSTIC_DECISION = "repeatable_behavioral_miss"
_EXPERIMENTAL_DISCLAIMER = (
    "Experimental only. Authenticated real-world outcome evidence supports "
    "a bounded persona-behavior hypothesis within the cited scopes; it does "
    "not prove causality, universal panel accuracy, or absolute performance."
)


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _self_hash(document: dict[str, object], field: str) -> dict[str, object]:
    document[field] = None
    document[field] = sha256_json(document)
    return document


def _prefixed_digest(value: object, path: str) -> str:
    text = require_string(value, path)
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise ContractError(f"{path} must be a prefixed SHA-256")
    return text


def _unprefixed_digest(value: object, path: str) -> str:
    text = require_string(value, path)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ContractError(f"{path} must be an unprefixed SHA-256")
    return text


def _validate_alternative_causes(payload: object) -> dict[str, object]:
    document = require_object(
        payload,
        {"schema_version", "reviewed_at", "reviewed_by", "causes"},
        "alternative_causes",
    )
    if document["schema_version"] != ALTERNATIVE_CAUSES_VERSION:
        raise ContractError("alternative_causes.schema_version is unknown")
    require_timestamp(document["reviewed_at"], "alternative_causes.reviewed_at")
    require_string(document["reviewed_by"], "alternative_causes.reviewed_by")
    rows: list[dict[str, object]] = []
    names: list[str] = []
    for index, raw in enumerate(
        require_array(document["causes"], "alternative_causes.causes", nonempty=True)
    ):
        path = f"alternative_causes.causes[{index}]"
        row = require_object(raw, {"cause", "status", "evidence"}, path)
        cause = require_identifier(row["cause"], f"{path}.cause")
        status = require_string(row["status"], f"{path}.status")
        if status not in {"cleared", "not_cleared"}:
            raise ContractError(f"{path}.status must be cleared or not_cleared")
        evidence = require_string(row["evidence"], f"{path}.evidence")
        names.append(cause)
        rows.append({"cause": cause, "status": status, "evidence": evidence})
    if names != sorted(names) or len(names) != len(set(names)):
        raise ContractError(
            "alternative_causes.causes must be unique and canonically sorted"
        )
    if set(names) != REQUIRED_ALTERNATIVE_CAUSES:
        raise ContractError(
            "alternative_causes.causes must exactly cover the required review"
        )
    return {
        "schema_version": ALTERNATIVE_CAUSES_VERSION,
        "reviewed_at": document["reviewed_at"],
        "reviewed_by": document["reviewed_by"],
        "causes": rows,
    }


def authenticate_c1_validation_packages(
    paths: Sequence[Path], *, authority_registry: object,
) -> list[dict[str, object]]:
    """Authenticate exact C1 archives through the existing package validator."""

    if len(paths) < 2:
        raise ContractError(
            "C2 diagnosis requires at least two C1 validation packages"
        )
    resolved = [Path(path).resolve(strict=True) for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ContractError("C1 validation package paths must be unique")
    return [
        validate_validation_package(path, authority_registry=authority_registry)
        for path in resolved
    ]


def _quality_gate(evaluation: Mapping[str, object], path: str) -> None:
    expected = (
        ("coverage", "status", "complete"),
        ("sample_sufficiency", "status", "sufficient"),
        ("independence", "status", "independent"),
        ("leakage", "status", "clear"),
        ("multiplicity", "status", "complete"),
        ("repeated_looks", "status", "none"),
        ("power", "status", "sufficient"),
    )
    for section, field, required in expected:
        value = evaluation.get(section)
        if not isinstance(value, Mapping) or value.get(field) != required:
            raise ContractError(
                f"{path}.{section}.{field} must equal {required}"
            )
    missingness = evaluation.get("missingness")
    if (
        not isinstance(missingness, Mapping)
        or missingness.get("status") not in {"none", "within_threshold"}
    ):
        raise ContractError(
            f"{path}.missingness.status must be none or within_threshold"
        )
    if evaluation.get("decision") != {"status": "tier4_not_supported"}:
        raise ContractError(
            f"{path} must be an otherwise eligible C1 negative evaluation"
        )
    overall = evaluation.get("overall_diagnostics")
    if not isinstance(overall, Mapping) or overall.get("status") != "fail":
        raise ContractError(f"{path}.overall_diagnostics must record the miss")


def _attribute_values(
    registry: Mapping[str, object], *, attribute_id: str,
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for row in registry["creative_attributes"]:
        if not isinstance(row, Mapping) or row.get("attribute_id") != attribute_id:
            continue
        value = row.get("value")
        if not isinstance(value, bool):
            raise ContractError(
                "C2 behavior diagnosis requires a pre-outcome Boolean attribute"
            )
        creative_id = str(row["creative_id"])
        if creative_id in result:
            raise ContractError("creative attribute registry contains duplicates")
        result[creative_id] = value
    if not result or set(result.values()) != {False, True}:
        raise ContractError(
            "C2 behavior diagnosis requires both informative and reference creatives"
        )
    return result


def _favored_creative(row: Mapping[str, object], *, observed: bool) -> str | None:
    prefix = "observed" if observed else "synthetic"
    direction = row.get(f"{prefix}_direction")
    creative_a = str(row.get("creative_a"))
    creative_b = str(row.get("creative_b"))
    if direction == f"{prefix}_a_above_b":
        return creative_a
    if direction == f"{prefix}_b_above_a":
        return creative_b
    if direction == f"{prefix}_tie":
        return None
    raise ContractError(f"pairwise comparison has an invalid {prefix} direction")


def _pair_evidence(
    row: Mapping[str, object], *, values: Mapping[str, bool], path: str,
) -> str:
    creative_a = str(row.get("creative_a"))
    creative_b = str(row.get("creative_b"))
    if creative_a not in values or creative_b not in values:
        return "not_applicable"
    if values[creative_a] == values[creative_b]:
        return "not_applicable"
    synthetic = _favored_creative(row, observed=False)
    observed = _favored_creative(row, observed=True)
    if synthetic is None or observed is None:
        return "ambiguous"
    if not values[synthetic] and values[observed]:
        return "supports"
    if values[synthetic] and not values[observed]:
        return "contrary"
    if synthetic == observed:
        return "aligned"
    raise ContractError(f"{path} cannot be reconciled to the registered attribute")


def _segment_row(
    comparison: Mapping[str, object], *, target_segment_id: str,
) -> Mapping[str, object] | None:
    rows = comparison.get("segment_evidence")
    if not isinstance(rows, list):
        raise ContractError("C1 comparison segment_evidence must be an array")
    matches = [
        row for row in rows
        if isinstance(row, Mapping) and row.get("segment_id") == target_segment_id
    ]
    if len(matches) > 1:
        raise ContractError("C1 comparison duplicates the target segment")
    return matches[0] if matches else None


def _source_hashes(evaluation: Mapping[str, object]) -> list[str]:
    values: set[str] = set()
    for comparison in evaluation.get("comparisons", []):
        if not isinstance(comparison, Mapping):
            continue
        for observation in comparison.get("observations", []):
            if not isinstance(observation, Mapping):
                continue
            source = observation.get("source")
            if isinstance(source, Mapping):
                for key, value in source.items():
                    if "sha256" in str(key) and isinstance(value, str):
                        values.add(_prefixed_digest(value, "evaluation.source hash"))
    return sorted(values)


def _outcome_access_times(evaluation: Mapping[str, object]) -> list[str]:
    values: set[str] = set()
    for comparison in evaluation.get("comparisons", []):
        if not isinstance(comparison, Mapping):
            continue
        for observation in comparison.get("observations", []):
            if isinstance(observation, Mapping):
                value = require_string(
                    observation.get("outcome_accessed_at"),
                    "evaluation.observation.outcome_accessed_at",
                )
                require_timestamp(value, "evaluation.observation.outcome_accessed_at")
                values.add(value)
    if not values:
        raise ContractError("C1 evaluation contains no outcome access evidence")
    return sorted(values, key=lambda value: require_timestamp(value, "outcome time"))


def build_authenticated_evidence_projection(
    *,
    base_panel_binding: Mapping[str, object],
    validated_packages: Sequence[Mapping[str, object]],
    attribute_registry: Mapping[str, object],
    target_persona_id: str,
    target_segment_id: str,
    as_of: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Derive the complete C2 diagnostic projection from authenticated C1 outputs."""

    require_timestamp(as_of, "as_of")
    require_identifier(target_persona_id, "target_persona_id")
    require_identifier(target_segment_id, "target_segment_id")
    registry = validate_creative_attribute_registry(attribute_registry)
    hypotheses = registered_behavior_hypotheses(
        registry, target_persona_id=target_persona_id
    )
    if not hypotheses:
        raise ContractError(
            "no pre-outcome behavioral hypothesis targets the requested persona"
        )
    if len(validated_packages) < 2:
        raise ContractError(
            "C2 diagnosis requires at least two independent C1 validations"
        )

    package_ids: set[str] = set()
    study_ids: set[str] = set()
    source_hashes: set[str] = set()
    entries: list[dict[str, object]] = []
    hypothesis_results: dict[str, dict[str, int]] = {
        str(row["hypothesis_id"]): {
            "supporting_packages": 0,
            "supporting_pairs": 0,
            "contrary_pairs": 0,
        }
        for row in hypotheses
    }

    earliest_declared = require_timestamp(
        registry["outcome_access_boundary"]["earliest_outcome_accessed_at"],
        "attribute_registry.outcome_access_boundary.earliest_outcome_accessed_at",
    )
    for package_index, package in enumerate(validated_packages):
        path = f"validated_packages[{package_index}]"
        if package.get("schema_version") != "audience-panel-validation-package-v1" or package.get("status") != "valid":
            raise ContractError(f"{path} is not authenticated C1 validation output")
        if package.get("claim_kind") != "negative":
            raise ContractError(f"{path} must preserve an honest C1 negative result")
        package_sha = _unprefixed_digest(
            package.get("package_zip_sha256"), f"{path}.package_zip_sha256"
        )
        if package_sha in package_ids:
            raise ContractError("diagnostic C1 package bytes must be unique")
        package_ids.add(package_sha)
        panel_binding = package.get("panel_binding")
        if not isinstance(panel_binding, Mapping) or dict(panel_binding) != dict(base_panel_binding):
            raise ContractError(
                f"{path} must bind the exact authenticated base panel package"
            )
        evaluation = package.get("evaluation")
        if not isinstance(evaluation, Mapping):
            raise ContractError(f"{path}.evaluation must be present")
        _quality_gate(evaluation, f"{path}.evaluation")
        access_times = _outcome_access_times(evaluation)
        if any(
            require_timestamp(value, f"{path}.outcome_accessed_at")
            < earliest_declared
            for value in access_times
        ):
            raise ContractError(
                "C1 outcomes precede the registry's pre-outcome access boundary"
            )

        package_studies: set[str] = set()
        block_ids: set[str] = set()
        comparison_hashes: list[str] = []
        package_hypothesis_counts: dict[str, dict[str, int]] = {}
        package_creative_ids: set[str] = set()
        target_segment_seen = False
        for comparison_index, comparison in enumerate(
            evaluation.get("comparisons", [])
        ):
            if not isinstance(comparison, Mapping):
                raise ContractError(f"{path}.evaluation.comparisons contains a non-object")
            binding = comparison.get("block_binding")
            if not isinstance(binding, Mapping):
                raise ContractError("C1 comparison is missing its block binding")
            package_studies.add(
                require_identifier(binding.get("study_id"), "comparison.study_id")
            )
            block_ids.add(
                require_identifier(binding.get("block_id"), "comparison.block_id")
            )
            comparison_hashes.append(
                _prefixed_digest(
                    comparison.get("comparison_sha256"),
                    "comparison.comparison_sha256",
                )
            )
            segment = _segment_row(
                comparison, target_segment_id=target_segment_id
            )
            if segment is None:
                continue
            target_segment_seen = True
            for hypothesis in hypotheses:
                hypothesis_id = str(hypothesis["hypothesis_id"])
                values = _attribute_values(
                    registry, attribute_id=str(hypothesis["attribute_id"])
                )
                counts = package_hypothesis_counts.setdefault(
                    hypothesis_id,
                    {"supports": 0, "contrary": 0, "ambiguous": 0, "aligned": 0},
                )
                for pair_index, pair in enumerate(
                    require_array(
                        segment.get("pairwise_comparisons"),
                        "segment.pairwise_comparisons",
                    )
                ):
                    if not isinstance(pair, Mapping):
                        raise ContractError("segment pairwise comparison must be an object")
                    result = _pair_evidence(
                        pair,
                        values=values,
                        path=(
                            f"{path}.comparisons[{comparison_index}]"
                            f".pairs[{pair_index}]"
                        ),
                    )
                    if result != "not_applicable":
                        counts[result] += 1
                        package_creative_ids.update(
                            (str(pair["creative_a"]), str(pair["creative_b"]))
                        )
        if not target_segment_seen:
            raise ContractError(
                f"{path} contains no evidence for the requested target segment"
            )
        if study_ids.intersection(package_studies):
            raise ContractError(
                "diagnostic validation packages must use disjoint study IDs"
            )
        study_ids.update(package_studies)
        package_source_hashes = _source_hashes(evaluation)
        if not package_source_hashes:
            raise ContractError(
                f"{path} contains no authenticated outcome source hash"
            )
        if source_hashes.intersection(package_source_hashes):
            raise ContractError(
                "diagnostic validation packages must use disjoint outcome source bytes"
            )
        source_hashes.update(package_source_hashes)

        for hypothesis in hypotheses:
            hypothesis_id = str(hypothesis["hypothesis_id"])
            counts = package_hypothesis_counts.get(
                hypothesis_id,
                {"supports": 0, "contrary": 0, "ambiguous": 0, "aligned": 0},
            )
            summary = hypothesis_results[hypothesis_id]
            summary["supporting_pairs"] += counts["supports"]
            summary["contrary_pairs"] += counts["contrary"]
            if counts["supports"] > 0 and counts["contrary"] == 0:
                summary["supporting_packages"] += 1

        entries.append({
            "package_sha256": "sha256:" + package_sha,
            "package_manifest_sha256": "sha256:" + _unprefixed_digest(
                package.get("package_manifest_sha256"),
                f"{path}.package_manifest_sha256",
            ),
            "evaluation_id": evaluation["evaluation_id"],
            "evaluation_sha256": _prefixed_digest(
                evaluation.get("evaluation_sha256"),
                f"{path}.evaluation.evaluation_sha256",
            ),
            "study_ids": sorted(package_studies),
            "block_ids": sorted(block_ids),
            "comparison_sha256": sorted(comparison_hashes),
            "creative_ids": sorted(package_creative_ids),
            "source_sha256": package_source_hashes,
            "outcome_accessed_at": access_times,
        })

    eligible_hypotheses = [
        row for row in hypotheses
        if hypothesis_results[str(row["hypothesis_id"])]["supporting_packages"]
        == len(validated_packages)
        and hypothesis_results[str(row["hypothesis_id"])]["supporting_pairs"]
        >= len(validated_packages)
        and hypothesis_results[str(row["hypothesis_id"])]["contrary_pairs"] == 0
    ]
    projection = build_authenticated_c1_evidence_projection(
        as_of=as_of,
        base_panel_binding=base_panel_binding,
        target_persona_id=target_persona_id,
        target_segment_id=target_segment_id,
        attribute_registry_binding={
            "registry_id": registry["registry_id"],
            "registry_sha256": registry["registry_sha256"],
            "registered_at": registry["registered_at"],
        },
        entries=entries,
        hypothesis_results=[
            {
                "hypothesis_id": hypothesis_id,
                **hypothesis_results[hypothesis_id],
            }
            for hypothesis_id in sorted(hypothesis_results)
        ],
    )
    return projection, eligible_hypotheses


def diagnose_real_world_persona_behavior(
    *,
    base_panel: Mapping[str, object],
    base_panel_binding: Mapping[str, object],
    validated_packages: Sequence[Mapping[str, object]],
    attribute_registry: Mapping[str, object],
    alternative_causes: Mapping[str, object],
    target_persona_id: str,
    target_segment_id: str,
    diagnosis_id: str,
    diagnosed_at: str,
) -> dict[str, object]:
    """Diagnose one repeated, evidence-supported persona-behavior miss."""

    require_identifier(diagnosis_id, "diagnosis_id")
    diagnosed_time = require_timestamp(diagnosed_at, "diagnosed_at")
    canonical_base = _canonical_panel(base_panel)
    persona = _find_persona(canonical_base, target_persona_id)
    authoritative = {
        **_panel_binding(canonical_base, persona_id=target_persona_id),
        "package_sha256": base_panel_binding["package_sha256"],
    }
    if authoritative != dict(base_panel_binding):
        raise ContractError(
            "base panel JSON does not match the authenticated package binding"
        )
    review = _validate_alternative_causes(alternative_causes)
    projection, eligible = build_authenticated_evidence_projection(
        base_panel_binding=base_panel_binding,
        validated_packages=validated_packages,
        attribute_registry=attribute_registry,
        target_persona_id=target_persona_id,
        target_segment_id=target_segment_id,
        as_of=diagnosed_at,
    )
    latest_access = max(
        require_timestamp(value, "evidence.outcome_accessed_at")
        for entry in projection["entries"]
        for value in entry["outcome_accessed_at"]
    )
    if diagnosed_time < latest_access:
        raise ContractError("diagnosis cannot precede outcome access")
    reviewed_time = require_timestamp(
        review["reviewed_at"], "alternative_causes.reviewed_at"
    )
    if reviewed_time < latest_access or reviewed_time > diagnosed_time:
        raise ContractError(
            "alternative-cause review must follow outcome access and not follow diagnosis"
        )
    uncleared = [
        row["cause"] for row in review["causes"]
        if row["status"] != "cleared"
    ]
    if uncleared:
        decision = "alternative_cause_not_cleared"
        selected: dict[str, object] | None = None
    elif len(eligible) > 1:
        decision = "non_identifiable"
        selected = None
    elif not eligible:
        decision = "no_repeatable_miss"
        selected = None
    else:
        decision = _DIAGNOSTIC_DECISION
        selected = deepcopy(eligible[0])
        field = str(selected["target_persona_field"])
        selected["before"] = deepcopy(persona[field])
        if selected["proposed_value"] == selected["before"]:
            raise ContractError("eligible hypothesis would not change the persona")

    document = {
        "schema_version": DIAGNOSIS_VERSION,
        "diagnosis_id": diagnosis_id,
        "diagnosed_at": diagnosed_at,
        "experimental_status": "experimental",
        "decision": decision,
        "base_panel_binding": deepcopy(dict(base_panel_binding)),
        "target_segment_id": target_segment_id,
        "evidence_projection": projection,
        "alternative_cause_review": review,
        "selected_hypothesis": selected,
        "claim_boundary": _EXPERIMENTAL_DISCLAIMER,
        "diagnosis_sha256": None,
    }
    return _self_hash(document, "diagnosis_sha256")


def build_real_world_persona_behavior_proposal(
    *,
    base_panel: Mapping[str, object],
    diagnosis: Mapping[str, object],
    proposal_id: str,
    proposed_at: str,
) -> dict[str, object]:
    """Seal one bounded C2 proposal from the authenticated diagnosis."""

    require_identifier(proposal_id, "proposal_id")
    proposed_time = require_timestamp(proposed_at, "proposed_at")
    if diagnosis.get("schema_version") != DIAGNOSIS_VERSION:
        raise ContractError("diagnosis schema is not C2")
    supplied = deepcopy(dict(diagnosis))
    digest = supplied.pop("diagnosis_sha256", None)
    supplied["diagnosis_sha256"] = None
    if digest != sha256_json(supplied):
        raise ContractError("diagnosis self-hash is invalid")
    if diagnosis.get("decision") != _DIAGNOSTIC_DECISION:
        raise ContractError("only a repeatable behavioral miss may create a proposal")
    if proposed_time < require_timestamp(diagnosis["diagnosed_at"], "diagnosed_at"):
        raise ContractError("proposal cannot precede diagnosis")
    canonical_base = _canonical_panel(base_panel)
    selected = diagnosis.get("selected_hypothesis")
    if not isinstance(selected, Mapping):
        raise ContractError("diagnosis selected hypothesis is missing")
    persona_id = require_identifier(
        selected.get("target_persona_id"), "selected_hypothesis.target_persona_id"
    )
    field = str(selected.get("target_persona_field"))
    if field not in ALLOWED_PERSONA_FIELDS:
        raise ContractError("proposal targets a forbidden persona field")
    persona = _find_persona(canonical_base, persona_id)
    if diagnosis["base_panel_binding"] != {
        **_panel_binding(canonical_base, persona_id=persona_id),
        "package_sha256": diagnosis["base_panel_binding"]["package_sha256"],
    }:
        raise ContractError("diagnosis base panel binding is stale")
    operation = build_bounded_profile_snapshot_operation(
        target_persona_id=persona_id,
        target_persona_field=field,
        proposed_value=selected["proposed_value"],
    )
    document = {
        "schema_version": PROPOSAL_VERSION,
        "proposal_id": proposal_id,
        "proposed_at": proposed_at,
        "experimental_status": "experimental",
        "proposal_type": "profile_snapshot_update",
        "base_panel_binding": deepcopy(diagnosis["base_panel_binding"]),
        "diagnosis_binding": {
            "diagnosis_id": diagnosis["diagnosis_id"],
            "diagnosis_sha256": diagnosis["diagnosis_sha256"],
        },
        "operation": operation,
        "before": {field: deepcopy(persona[field])},
        "diagnosis": deepcopy(dict(diagnosis)),
        "fresh_held_out_evaluation_required": True,
        "human_approval_required": True,
        "claim_boundary": _EXPERIMENTAL_DISCLAIMER,
        "proposal_sha256": None,
    }
    return _self_hash(document, "proposal_sha256")


def materialize_real_world_candidate(
    *,
    base_panel: Mapping[str, object],
    proposal: Mapping[str, object],
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    """Materialize one complete newer panel while preserving the base bytes."""

    require_identifier(candidate_id, "candidate_id")
    created_time = require_timestamp(created_at, "created_at")
    canonical_base = _canonical_panel(base_panel)
    base_bytes = canonical_json_bytes(canonical_base)
    if proposal.get("schema_version") != PROPOSAL_VERSION:
        raise ContractError("proposal schema is not C2")
    proposed = deepcopy(dict(proposal))
    proposal_digest = proposed.pop("proposal_sha256", None)
    proposed["proposal_sha256"] = None
    if proposal_digest != sha256_json(proposed):
        raise ContractError("proposal self-hash is invalid")
    if proposal.get("proposal_type") != "profile_snapshot_update":
        raise ContractError("proposal is not materializable")
    operation = proposal.get("operation")
    if not isinstance(operation, Mapping):
        raise ContractError("proposal operation is missing")
    persona_id = require_identifier(
        operation.get("target_persona_id"), "operation.target_persona_id"
    )
    fields = operation.get("changed_fields")
    if not isinstance(fields, list) or len(fields) != 1 or fields[0] not in ALLOWED_PERSONA_FIELDS:
        raise ContractError("proposal must change exactly one allowed persona field")
    field = str(fields[0])
    proposed_after = operation.get("proposed_after")
    if not isinstance(proposed_after, Mapping) or set(proposed_after) != {field}:
        raise ContractError("proposal after value must contain the one target field")
    proposed_value = deepcopy(proposed_after[field])
    authoritative_binding = {
        **_panel_binding(canonical_base, persona_id=persona_id),
        "package_sha256": proposal["base_panel_binding"]["package_sha256"],
    }
    if proposal.get("base_panel_binding") != authoritative_binding:
        raise ContractError("proposal does not bind the authoritative base panel")
    if _version_tuple(candidate_version) <= _version_tuple(str(canonical_base["version"])):
        raise ContractError("candidate_version must be newer than the base version")
    if any(
        created_time <= require_timestamp(canonical_base[key], f"base_panel.{key}")
        for key in ("created_at", "updated_at")
    ):
        raise ContractError("candidate timestamp must follow the base panel")
    if created_time < require_timestamp(proposal["proposed_at"], "proposed_at"):
        raise ContractError("candidate cannot precede the proposal")

    persona = _find_persona(canonical_base, persona_id)
    before_snapshot = _behavior_snapshot(persona)
    if proposed_value == before_snapshot[field]:
        raise ContractError("proposal would not change the authoritative persona")
    matching = _matching_profiles(
        canonical_base,
        persona_id=persona_id,
        authoritative_snapshot=before_snapshot,
    )
    profile_ids = sorted(str(row["grounded_profile_id"]) for row in matching)
    candidate = deepcopy(canonical_base)
    candidate["version"] = candidate_version
    candidate["created_at"] = created_at
    candidate["updated_at"] = created_at
    _find_persona(candidate, persona_id)[field] = proposed_value
    profiles = {
        str(row["grounded_profile_id"]): row for row in _profile_rows(candidate)
    }
    for profile_id in profile_ids:
        profiles[profile_id]["profile_snapshot"][field] = deepcopy(proposed_value)
    evidence = proposal["diagnosis_binding"]
    diagnosis = proposal.get("diagnosis")
    if not isinstance(diagnosis, Mapping):
        raise ContractError("proposal must retain its complete diagnosis")
    evidence_projection = diagnosis.get("evidence_projection")
    if not isinstance(evidence_projection, Mapping):
        raise ContractError("proposal diagnosis must retain its evidence projection")
    history_row = {
        "date": created_at,
        "source_type": "authenticated_real_world_outcome_validation",
        "mapped_run_id": proposal["proposal_id"],
        "mapped_variants": sorted({
            str(creative_id)
            for entry in evidence_projection.get("entries", [])
            for creative_id in entry.get("creative_ids", [])
        }),
        "mapped_segments": [str(diagnosis["target_segment_id"])],
        "objective": "Experimental bounded persona-behavior calibration",
        "time_window": str(proposal["proposed_at"]),
        "data_quality": "Authenticated C1 evidence; exact provenance retained outside the panel.",
        "directional_alignment": "Repeated held-out misses support an experimental hypothesis only.",
        "action": CALIBRATION_HISTORY_ACTION,
        "what_was_learned": (
            f"Experimental hypothesis {evidence['diagnosis_id']} proposes one "
            f"bounded {field} update."
        ),
        "next_run_guidance": (
            "Require fresh non-overlapping held-out C1 validation and exact "
            "human approval before registration."
        ),
    }
    candidate["calibration_history"] = [
        *deepcopy(canonical_base["calibration_history"]),
        history_row,
    ]

    expected = deepcopy(canonical_base)
    expected["version"] = candidate_version
    expected["created_at"] = created_at
    expected["updated_at"] = created_at
    _find_persona(expected, persona_id)[field] = deepcopy(proposed_value)
    expected_profiles = {
        str(row["grounded_profile_id"]): row for row in _profile_rows(expected)
    }
    for profile_id in profile_ids:
        expected_profiles[profile_id]["profile_snapshot"][field] = deepcopy(
            proposed_value
        )
    expected["calibration_history"] = [
        *deepcopy(canonical_base["calibration_history"]), history_row
    ]
    if expected != candidate:
        raise ContractError("candidate contains a change outside the C2 allowlist")
    canonical_candidate = _canonical_panel(candidate)
    if canonical_json_bytes(canonical_base) != base_bytes:
        raise ContractError("base panel changed during candidate materialization")

    base_projection = build_persona_authoring_projection(
        validated_panel=canonical_base
    )
    candidate_projection = build_persona_authoring_projection(
        validated_panel=canonical_candidate
    )
    changes = _changed_path_rows(
        base_panel=canonical_base,
        candidate_panel=canonical_candidate,
        persona_id=persona_id,
        field=field,
        profile_ids=profile_ids,
    )
    changes.append({
        "path": f"$.calibration_history[{len(canonical_base['calibration_history'])}]",
        "before": None,
        "after": deepcopy(history_row),
    })
    changes.sort(key=lambda row: str(row["path"]))
    diff = _self_hash({
        "schema_version": DIFF_VERSION,
        "base_panel_sha256": sha256_json(canonical_base),
        "candidate_panel_sha256": sha256_json(canonical_candidate),
        "changes": changes,
        "diff_sha256": None,
    }, "diff_sha256")
    binding = _self_hash({
        "schema_version": CANDIDATE_VERSION,
        "candidate_id": candidate_id,
        "created_at": created_at,
        "experimental_status": "experimental",
        "registration_route": "gated_c2_only",
        "registration_permitted": False,
        "active_panel_mutation_permitted": False,
        "base_panel_binding": authoritative_binding,
        "proposal_binding": {
            "proposal_id": proposal["proposal_id"],
            "proposal_sha256": proposal["proposal_sha256"],
        },
        "candidate_panel_binding": _panel_binding(
            canonical_candidate, persona_id=persona_id
        ),
        "diff_binding": {
            "diff_sha256": diff["diff_sha256"],
            "changed_paths": [row["path"] for row in changes],
        },
        "fresh_held_out_evaluation_required": True,
        "human_approval_required": True,
        "claim_boundary": _EXPERIMENTAL_DISCLAIMER,
        "candidate_binding_sha256": None,
    }, "candidate_binding_sha256")
    return {
        "base_panel": canonical_base,
        "candidate_panel": canonical_candidate,
        "base_authoring_projection": base_projection,
        "candidate_authoring_projection": candidate_projection,
        "proposal": deepcopy(dict(proposal)),
        "persona_behavior_diff": diff,
        "candidate_binding": binding,
    }


def publish_real_world_candidate_bundle(
    *,
    materialized: Mapping[str, object],
    diagnosis: Mapping[str, object],
    attribute_registry: Mapping[str, object],
    alternative_causes: Mapping[str, object],
    output_dir: Path,
) -> Path:
    """Publish a deterministic no-clobber C2 candidate directory."""

    target = _ensure_safe_new_directory(Path(output_dir))
    files = {
        "base-audience-panel.json": canonical_json_bytes(materialized["base_panel"]),
        "candidate-audience-panel.json": canonical_json_bytes(materialized["candidate_panel"]),
        "base-persona-authoring-projection.json": canonical_json_bytes(materialized["base_authoring_projection"]),
        "candidate-persona-authoring-projection.json": canonical_json_bytes(materialized["candidate_authoring_projection"]),
        "real-world-calibration-diagnosis.json": canonical_json_bytes(diagnosis),
        "authenticated-outcome-evidence-library.json": canonical_json_bytes(
            diagnosis["evidence_projection"]
        ),
        "real-world-calibration-proposal.json": canonical_json_bytes(materialized["proposal"]),
        "real-world-candidate-binding.json": canonical_json_bytes(materialized["candidate_binding"]),
        "persona-behavior-diff.json": canonical_json_bytes(materialized["persona_behavior_diff"]),
        "creative-attribute-registry.json": canonical_json_bytes(attribute_registry),
        "alternative-causes.json": canonical_json_bytes(alternative_causes),
        "README.txt": (
            "EXPERIMENTAL REAL-WORLD PANEL CALIBRATION CANDIDATE\n\n"
            + _EXPERIMENTAL_DISCLAIMER
            + "\nRegistration requires fresh held-out C1 evidence and explicit human approval.\n"
        ).encode("utf-8"),
    }
    manifest = _self_hash({
        "schema_version": BUNDLE_VERSION,
        "candidate_id": materialized["candidate_binding"]["candidate_id"],
        "experimental_status": "experimental",
        "registration_permitted": False,
        "files": [
            {
                "path": name,
                "sha256": _digest_bytes(payload),
                "byte_count": len(payload),
            }
            for name, payload in sorted(files.items())
        ],
        "bundle_manifest_sha256": None,
    }, "bundle_manifest_sha256")
    files["bundle-manifest.json"] = canonical_json_bytes(manifest)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.c2-", dir=target.parent))
    try:
        os.chmod(stage, 0o700)
        for name, payload in files.items():
            _write_new(stage / name, payload)
        try:
            os.replace(stage, target)
        except OSError as exc:
            raise UnsafeCandidateOutput(
                "C2 candidate output could not be published atomically"
            ) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return target


_BUNDLE_PAYLOAD_FILES = frozenset({
    "README.txt",
    "alternative-causes.json",
    "authenticated-outcome-evidence-library.json",
    "base-audience-panel.json",
    "base-persona-authoring-projection.json",
    "candidate-audience-panel.json",
    "candidate-persona-authoring-projection.json",
    "creative-attribute-registry.json",
    "persona-behavior-diff.json",
    "real-world-calibration-diagnosis.json",
    "real-world-calibration-proposal.json",
    "real-world-candidate-binding.json",
})


def _read_bundle(bundle_dir: Path) -> dict[str, object]:
    root = Path(bundle_dir)
    if root.is_symlink() or not root.is_dir():
        raise ContractError("candidate bundle must be one real directory")
    children = list(root.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in children):
        raise ContractError("candidate bundle may contain only regular files")
    expected = _BUNDLE_PAYLOAD_FILES | {"bundle-manifest.json"}
    if {path.name for path in children} != expected:
        raise ContractError("candidate bundle file allowlist is invalid")
    payload_bytes = {
        name: (root / name).read_bytes() for name in _BUNDLE_PAYLOAD_FILES
    }
    try:
        manifest = json.loads((root / "bundle-manifest.json").read_text("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("candidate bundle manifest is invalid JSON") from exc
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema_version", "candidate_id", "experimental_status",
        "registration_permitted", "files", "bundle_manifest_sha256",
    }:
        raise ContractError("candidate bundle manifest keys are invalid")
    if manifest["schema_version"] != BUNDLE_VERSION:
        raise ContractError("candidate bundle schema is unknown")
    unhashed = deepcopy(dict(manifest))
    supplied_hash = unhashed["bundle_manifest_sha256"]
    unhashed["bundle_manifest_sha256"] = None
    if supplied_hash != sha256_json(unhashed):
        raise ContractError("candidate bundle manifest self-hash is invalid")
    records = manifest["files"]
    if not isinstance(records, list):
        raise ContractError("candidate bundle manifest files must be an array")
    expected_records = [
        {
            "path": name,
            "sha256": _digest_bytes(payload),
            "byte_count": len(payload),
        }
        for name, payload in sorted(payload_bytes.items())
    ]
    if records != expected_records:
        raise ContractError("candidate bundle file hashes do not match")
    documents: dict[str, object] = {"manifest": dict(manifest)}
    for name in _BUNDLE_PAYLOAD_FILES - {"README.txt"}:
        try:
            document = json.loads(payload_bytes[name].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"candidate bundle {name} is invalid JSON") from exc
        if not isinstance(document, dict):
            raise ContractError(f"candidate bundle {name} must contain an object")
        if payload_bytes[name] != canonical_json_bytes(document):
            raise ContractError(f"candidate bundle {name} is not canonical JSON")
        documents[name] = document
    readme = payload_bytes["README.txt"].decode("utf-8")
    if not readme.startswith(
        "EXPERIMENTAL REAL-WORLD PANEL CALIBRATION CANDIDATE\n"
    ):
        raise ContractError("candidate bundle README loses the experimental label")
    return documents


def replay_real_world_candidate_bundle(
    *,
    bundle_dir: Path,
    base_panel_package: Path,
    diagnostic_validation_packages: Sequence[Path],
    authority_registry: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Reauthenticate and byte-replay every C2 candidate derivation."""

    documents = _read_bundle(bundle_dir)
    base_binding, _validation, base_panel = read_authenticated_panel_snapshot(
        Path(base_panel_package)
    )
    if documents["base-audience-panel.json"] != base_panel:
        raise ContractError("candidate bundle base panel differs from its package")
    validated_packages = authenticate_c1_validation_packages(
        diagnostic_validation_packages,
        authority_registry=authority_registry,
    )
    stored_diagnosis = documents["real-world-calibration-diagnosis.json"]
    stored_projection = validate_authenticated_c1_evidence_projection(
        documents["authenticated-outcome-evidence-library.json"]
    )
    if stored_diagnosis.get("evidence_projection") != stored_projection:
        raise ContractError(
            "candidate diagnosis does not bind its Outcome Evidence Library projection"
        )
    diagnosis = diagnose_real_world_persona_behavior(
        base_panel=base_panel,
        base_panel_binding=base_binding,
        validated_packages=validated_packages,
        attribute_registry=documents["creative-attribute-registry.json"],
        alternative_causes=documents["alternative-causes.json"],
        target_persona_id=str(
            stored_diagnosis["base_panel_binding"]["persona_id"]
        ),
        target_segment_id=str(stored_diagnosis["target_segment_id"]),
        diagnosis_id=str(stored_diagnosis["diagnosis_id"]),
        diagnosed_at=str(stored_diagnosis["diagnosed_at"]),
    )
    if diagnosis != stored_diagnosis:
        raise ContractError("candidate diagnosis does not byte-match C1 replay")
    stored_proposal = documents["real-world-calibration-proposal.json"]
    proposal = build_real_world_persona_behavior_proposal(
        base_panel=base_panel,
        diagnosis=diagnosis,
        proposal_id=str(stored_proposal["proposal_id"]),
        proposed_at=str(stored_proposal["proposed_at"]),
    )
    if proposal != stored_proposal:
        raise ContractError("candidate proposal does not byte-match diagnosis replay")
    stored_binding = documents["real-world-candidate-binding.json"]
    stored_panel = documents["candidate-audience-panel.json"]
    materialized = materialize_real_world_candidate(
        base_panel=base_panel,
        proposal=proposal,
        candidate_id=str(stored_binding["candidate_id"]),
        candidate_version=str(stored_panel["version"]),
        created_at=str(stored_binding["created_at"]),
    )
    comparisons = {
        "base_panel": "base-audience-panel.json",
        "candidate_panel": "candidate-audience-panel.json",
        "base_authoring_projection": "base-persona-authoring-projection.json",
        "candidate_authoring_projection": "candidate-persona-authoring-projection.json",
        "proposal": "real-world-calibration-proposal.json",
        "persona_behavior_diff": "persona-behavior-diff.json",
        "candidate_binding": "real-world-candidate-binding.json",
    }
    for key, name in comparisons.items():
        if materialized[key] != documents[name]:
            raise ContractError(f"candidate bundle {name} does not match replay")
    if documents["manifest"]["candidate_id"] != stored_binding["candidate_id"]:
        raise ContractError("candidate bundle manifest identity is stale")
    return materialized, diagnosis


def build_registration_proposal(
    *,
    candidate: Mapping[str, object],
    candidate_package_binding: Mapping[str, object],
    fresh_validation: Mapping[str, object],
    registered_at: str,
) -> dict[str, object]:
    """Gate a candidate with a fresh, non-overlapping C1 supported result."""

    registration_time = require_timestamp(registered_at, "registered_at")
    binding = candidate.get("candidate_binding")
    if not isinstance(binding, Mapping) or binding.get("schema_version") != CANDIDATE_VERSION:
        raise ContractError("candidate binding is invalid")
    candidate_panel = candidate.get("candidate_panel")
    if not isinstance(candidate_panel, Mapping):
        raise ContractError("candidate panel is missing")
    expected_panel_binding = _panel_binding(
        _canonical_panel(candidate_panel),
        persona_id=str(binding["candidate_panel_binding"]["persona_id"]),
    )
    if binding.get("candidate_panel_binding") != expected_panel_binding:
        raise ContractError("candidate binding does not match candidate panel bytes")
    expected_package_binding = {
        "panel_id": expected_panel_binding["panel_id"],
        "panel_version": expected_panel_binding["panel_version"],
        "panel_sha256": expected_panel_binding["panel_sha256"],
        "package_sha256": candidate_package_binding["package_sha256"],
    }
    if dict(candidate_package_binding) != expected_package_binding:
        raise ContractError("candidate package does not bind the materialized panel")
    if fresh_validation.get("schema_version") != "audience-panel-validation-package-v1" or fresh_validation.get("status") != "valid":
        raise ContractError("fresh evidence is not an authenticated C1 package")
    if fresh_validation.get("claim_kind") != "claim":
        raise ContractError("fresh candidate evidence must contain a supported C1 claim")
    if fresh_validation.get("panel_binding") != candidate_package_binding:
        raise ContractError("fresh C1 evidence must bind the exact candidate package")
    evaluation = fresh_validation.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ContractError("fresh C1 evaluation is missing")
    if evaluation.get("decision") != {"status": "tier4_supported"} or evaluation.get("gate_results") != {"all_required_gates_passed": True}:
        raise ContractError("fresh C1 evaluation must pass every evidence gate")
    claim = fresh_validation.get("claim")
    if not isinstance(claim, Mapping) or claim.get("status") != "active":
        raise ContractError("fresh C1 package must contain its active exact-scope claim")
    created_time = require_timestamp(binding["created_at"], "candidate.created_at")
    if require_timestamp(evaluation["evaluated_at"], "evaluation.evaluated_at") <= created_time:
        raise ContractError("fresh evaluation must follow candidate materialization")
    if registration_time < require_timestamp(evaluation["evaluated_at"], "evaluation.evaluated_at"):
        raise ContractError("registration cannot precede fresh evaluation")
    if registration_time >= require_timestamp(claim["expires_at"], "claim.expires_at"):
        raise ContractError("fresh C1 claim must remain active at registration")
    fresh_studies = {
        str(comparison["block_binding"]["study_id"])
        for comparison in evaluation.get("comparisons", [])
    }
    diagnostic_projection = candidate["proposal"].get("diagnosis")
    if not isinstance(diagnostic_projection, Mapping):
        raise ContractError("candidate proposal must retain the exact diagnosis")
    evidence = diagnostic_projection.get("evidence_projection")
    if not isinstance(evidence, Mapping):
        raise ContractError("candidate diagnosis evidence projection is missing")
    diagnostic_studies = {
        str(study)
        for entry in evidence.get("entries", [])
        for study in entry.get("study_ids", [])
    }
    if not fresh_studies or fresh_studies.intersection(diagnostic_studies):
        raise ContractError("fresh held-out studies must not overlap diagnosis evidence")
    diagnostic_sources = {
        str(source)
        for entry in evidence.get("entries", [])
        for source in entry.get("source_sha256", [])
    }
    fresh_sources = set(_source_hashes(evaluation))
    if diagnostic_sources.intersection(fresh_sources):
        raise ContractError("fresh outcome source bytes must not overlap diagnosis evidence")
    if any(
        require_timestamp(value, "fresh outcome access") <= created_time
        for value in _outcome_access_times(evaluation)
    ):
        raise ContractError("fresh outcomes must be accessed after candidate creation")

    return _self_hash({
        "schema_version": REGISTRATION_PROPOSAL_VERSION,
        "experimental_status": "experimental",
        "registered_at": registered_at,
        "candidate_binding": {
            "candidate_id": binding["candidate_id"],
            "candidate_binding_sha256": binding["candidate_binding_sha256"],
        },
        "candidate_package_binding": deepcopy(dict(candidate_package_binding)),
        "diagnostic_evidence_projection_sha256": evidence["projection_sha256"],
        "fresh_validation_binding": {
            "package_sha256": "sha256:" + fresh_validation["package_zip_sha256"],
            "package_manifest_sha256": "sha256:" + fresh_validation["package_manifest_sha256"],
            "evaluation_id": evaluation["evaluation_id"],
            "evaluation_sha256": evaluation["evaluation_sha256"],
            "evaluated_at": evaluation["evaluated_at"],
            "claim_id": claim["claim_id"],
            "claim_sha256": claim["claim_sha256"],
        },
        "fresh_evidence_disjoint": True,
        "all_evidence_gates_passed": True,
        "explicit_human_approval_required": True,
        "claim_boundary": _EXPERIMENTAL_DISCLAIMER,
        "registration_proposal_sha256": None,
    }, "registration_proposal_sha256")


def _validate_registration_proposal(
    payload: Mapping[str, object],
) -> dict[str, object]:
    keys = {
        "schema_version", "experimental_status", "registered_at",
        "candidate_binding", "candidate_package_binding",
        "diagnostic_evidence_projection_sha256", "fresh_validation_binding",
        "fresh_evidence_disjoint", "all_evidence_gates_passed",
        "explicit_human_approval_required", "claim_boundary",
        "registration_proposal_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != keys:
        raise ContractError("C2 registration proposal keys are not closed")
    document = deepcopy(dict(payload))
    if document["schema_version"] != REGISTRATION_PROPOSAL_VERSION:
        raise ContractError("C2 registration proposal schema is unknown")
    if document["experimental_status"] != "experimental":
        raise ContractError("C2 registration proposal must remain experimental")
    if document["claim_boundary"] != _EXPERIMENTAL_DISCLAIMER:
        raise ContractError("C2 registration proposal claim boundary changed")
    for field in (
        "fresh_evidence_disjoint", "all_evidence_gates_passed",
        "explicit_human_approval_required",
    ):
        if document[field] is not True:
            raise ContractError(f"C2 registration proposal {field} must be true")
    require_timestamp(document["registered_at"], "registered_at")
    candidate = document["candidate_binding"]
    if not isinstance(candidate, Mapping) or set(candidate) != {
        "candidate_id", "candidate_binding_sha256",
    }:
        raise ContractError("C2 candidate binding is not closed")
    require_identifier(candidate["candidate_id"], "candidate_binding.candidate_id")
    _prefixed_digest(
        candidate["candidate_binding_sha256"],
        "candidate_binding.candidate_binding_sha256",
    )
    package = document["candidate_package_binding"]
    if not isinstance(package, Mapping) or set(package) != {
        "panel_id", "panel_version", "panel_sha256", "package_sha256",
    }:
        raise ContractError("C2 candidate package binding is not closed")
    require_identifier(package["panel_id"], "candidate_package_binding.panel_id")
    _version_tuple(str(package["panel_version"]))
    _prefixed_digest(
        package["panel_sha256"], "candidate_package_binding.panel_sha256"
    )
    _prefixed_digest(
        package["package_sha256"], "candidate_package_binding.package_sha256"
    )
    _prefixed_digest(
        document["diagnostic_evidence_projection_sha256"],
        "diagnostic_evidence_projection_sha256",
    )
    fresh = document["fresh_validation_binding"]
    if not isinstance(fresh, Mapping) or set(fresh) != {
        "package_sha256", "package_manifest_sha256", "evaluation_id",
        "evaluation_sha256", "evaluated_at", "claim_id", "claim_sha256",
    }:
        raise ContractError("C2 fresh validation binding is not closed")
    for field in (
        "package_sha256", "package_manifest_sha256", "evaluation_sha256",
        "claim_sha256",
    ):
        _prefixed_digest(fresh[field], f"fresh_validation_binding.{field}")
    for field in ("evaluation_id", "claim_id"):
        require_identifier(fresh[field], f"fresh_validation_binding.{field}")
    require_timestamp(fresh["evaluated_at"], "fresh_validation_binding.evaluated_at")
    supplied = document["registration_proposal_sha256"]
    document["registration_proposal_sha256"] = None
    if supplied != sha256_json(document):
        raise ContractError("C2 registration proposal self-hash is invalid")
    document["registration_proposal_sha256"] = supplied
    return document


def require_registration_approval(
    *,
    workflow_state: Mapping[str, object],
    registration_proposal: Mapping[str, object],
) -> dict[str, object]:
    """Require exact C2 calibration and package approvals after evaluation."""

    proposal = _validate_registration_proposal(registration_proposal)
    state = validate_workflow_state(workflow_state)
    package = proposal["candidate_package_binding"]
    if state["state"] != "approved":
        raise ContractError("calibration workflow state must be approved")
    if state["panel_id"] != package["panel_id"] or state["panel_version"] != package["panel_version"]:
        raise ContractError("calibration approval targets a different panel version")
    panel_sha = str(package["panel_sha256"]).removeprefix("sha256:")
    package_sha = str(package["package_sha256"]).removeprefix("sha256:")
    if state["bindings"]["panel_sha256"] != panel_sha or state["bindings"]["package_sha256"] != package_sha:
        raise ContractError("calibration workflow bindings are stale")
    calibration = require_approved_scope(
        state,
        scope="calibration",
        target_sha256=str(
            proposal["registration_proposal_sha256"]
        ).removeprefix("sha256:"),
    )
    package_approval = require_approved_scope(
        state,
        scope="package_registration",
        target_sha256=package_sha,
    )
    evaluated_at = require_timestamp(
        proposal["fresh_validation_binding"]["evaluated_at"],
        "fresh_validation_binding.evaluated_at",
    )
    registration_time = require_timestamp(
        proposal["registered_at"], "registered_at"
    )
    for label, approval in (
        ("calibration", calibration),
        ("package_registration", package_approval),
    ):
        approved_at = require_timestamp(
            approval["approved_at"], f"{label}.approved_at"
        )
        if approved_at < evaluated_at:
            raise ContractError(f"{label} approval must follow fresh evaluation")
        if approved_at > registration_time:
            raise ContractError(f"{label} approval cannot follow registration")
    return {
        "calibration": calibration,
        "package_registration": package_approval,
    }


def register_real_world_calibrated_package(
    source: Path,
    *,
    library_root: Path,
    registration_proposal: Mapping[str, object],
    workflow_state: Mapping[str, object],
) -> dict[str, object]:
    """Publish through the existing immutable audience-library transaction."""

    require_registration_approval(
        workflow_state=workflow_state,
        registration_proposal=registration_proposal,
    )
    from audience_lab.audience_library import _register_experimental_c2_package

    return _register_experimental_c2_package(
        source,
        library_root=library_root,
        expected_binding=registration_proposal["candidate_package_binding"],
    )


__all__ = [
    "ALTERNATIVE_CAUSES_VERSION",
    "BUNDLE_VERSION",
    "CALIBRATION_HISTORY_ACTION",
    "CANDIDATE_VERSION",
    "DIAGNOSIS_VERSION",
    "EVIDENCE_PROJECTION_VERSION",
    "PROPOSAL_VERSION",
    "REGISTRATION_PROPOSAL_VERSION",
    "authenticate_c1_validation_packages",
    "build_authenticated_evidence_projection",
    "build_real_world_persona_behavior_proposal",
    "build_registration_proposal",
    "diagnose_real_world_persona_behavior",
    "materialize_real_world_candidate",
    "publish_real_world_candidate_bundle",
    "register_real_world_calibrated_package",
    "replay_real_world_candidate_bundle",
    "require_registration_approval",
]
