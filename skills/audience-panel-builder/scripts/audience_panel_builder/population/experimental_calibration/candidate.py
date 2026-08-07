"""Materialize a complete synthetic-only persona panel candidate.

The materializer deliberately validates only the standalone saved-panel-v3
surface.  It does not call production workflow, audit, package, registration,
activation, or active-library paths.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Mapping

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_identifier,
    require_timestamp,
    sha256_json,
)
from .contracts import (
    ALLOWED_PERSONA_FIELDS,
    AUTHORING_PROJECTION_VERSION,
    CANDIDATE_VERSION,
    validate_diagnosis,
    validate_experimental_proposal,
    validate_persona_authoring_projection,
    validate_sandbox_candidate_binding,
)
from .proposal import build_experimental_proposal


SKILLS_ROOT = Path(__file__).resolve().parents[5]
AD_TESTING_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(AD_TESTING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AD_TESTING_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_saved_panel_v3,
)


_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_BEHAVIOR_FIELDS = (
    "anxieties",
    "decision_context",
    "motivations",
    "proof_needs",
    "role_context",
)
_BUNDLE_VERSION = "experimental-persona-candidate-bundle-manifest-v1"
_DIFF_VERSION = "experimental-persona-behavior-diff-v1"
_VALIDATION_VERSION = "experimental-standalone-panel-validation-v1"
_SYSTEM_PATH_ALIASES = {Path("/etc"), Path("/tmp"), Path("/var")}
_STRUCTURAL_VALIDATION = {
    "standalone_saved_panel_v3": "passed",
    "production_workflow_state": "not_run_sandbox_only",
    "production_construction_audit": "not_run_sandbox_only",
    "production_package_approval": "not_run_sandbox_only",
    "production_library_registration": "not_run_sandbox_only",
}
_LIMITATIONS = [
    "Only fictional synthetic fixtures were used.",
    "This candidate is not a reusable audience-panel package.",
    "No real-world improvement, calibration, or activation is established.",
]
_README = (
    "EXPERIMENTAL SYNTHETIC-ONLY SANDBOX CANDIDATE\n"
    "This is not a reusable audience-panel package. It cannot be registered or\n"
    "activated and does not prove real-world improvement.\n"
    "\n"
    "Only fictional synthetic fixtures were used. The complete standalone panel\n"
    "is provided solely for the isolated synthetic evaluation harness.\n"
)


class CandidateNotMaterializable(ContractError):
    """The valid experimental result contains no permitted update."""


class UnsafeCandidateOutput(ContractError):
    """A candidate bundle cannot be published without aliasing or clobbering."""


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise ContractError("candidate_version must be MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in value.split("."))


def _canonical_panel(value: object) -> dict[str, object]:
    try:
        result = validate_saved_panel_v3(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if not isinstance(result, dict):
        raise ContractError("saved-panel-v3 validator returned an invalid result")
    return result


def _behavior_snapshot(persona: Mapping[str, object]) -> dict[str, object]:
    return {field: deepcopy(persona[field]) for field in _BEHAVIOR_FIELDS}


def _persona_rows(panel: Mapping[str, object]) -> list[dict[str, object]]:
    rows = panel["persona_archetypes"]
    if not isinstance(rows, list):
        raise ContractError("validated panel persona_archetypes must be an array")
    return rows


def _profile_rows(panel: Mapping[str, object]) -> list[dict[str, object]]:
    rows = panel["grounded_context_profiles"]
    if not isinstance(rows, list):
        raise ContractError(
            "validated panel grounded_context_profiles must be an array"
        )
    return rows


def _find_persona(
    panel: Mapping[str, object], persona_id: str
) -> dict[str, object]:
    matches = [
        row
        for row in _persona_rows(panel)
        if row["persona_archetype_id"] == persona_id
    ]
    if len(matches) != 1:
        raise ContractError(
            "target persona must identify exactly one existing persona archetype"
        )
    return matches[0]


def _panel_binding(
    panel: Mapping[str, object],
    *,
    persona_id: str,
) -> dict[str, object]:
    persona = _find_persona(panel, persona_id)
    result = {
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "panel_sha256": sha256_json(panel),
        "persona_id": persona_id,
        "persona_snapshot_sha256": sha256_json(_behavior_snapshot(persona)),
    }
    return result


def build_persona_authoring_projection(
    *,
    validated_panel: dict[str, object],
) -> dict[str, object]:
    """Project persona behavior only from an already validated panel copy."""

    # Revalidate at this public boundary so callers cannot label arbitrary input
    # as ``validated_panel``. Materialization itself passes the canonical copy
    # returned by the first authoritative validation.
    panel = _canonical_panel(validated_panel)
    personas = []
    persona_ids: list[str] = []
    for row in _persona_rows(panel):
        persona_id = str(row["persona_archetype_id"])
        persona_ids.append(persona_id)
        personas.append(
            {
                "persona_archetype_id": persona_id,
                **_behavior_snapshot(row),
            }
        )
    if len(persona_ids) != len(set(persona_ids)):
        raise ContractError("validated panel has duplicate persona archetype IDs")
    if not persona_ids:
        raise ContractError("validated panel must contain persona archetypes")

    bindings = []
    profile_ids: list[str] = []
    for row in _profile_rows(panel):
        profile_id = str(row["grounded_profile_id"])
        profile_ids.append(profile_id)
        snapshot = deepcopy(row["profile_snapshot"])
        bindings.append(
            {
                "profile_id": profile_id,
                "persona_archetype_id": row["persona_archetype_id"],
                "profile_snapshot": snapshot,
                "profile_snapshot_sha256": sha256_json(snapshot),
            }
        )
    if len(profile_ids) != len(set(profile_ids)):
        raise ContractError("validated panel has duplicate grounded-profile IDs")

    first_persona = personas[0]
    projection = {
        "schema_version": AUTHORING_PROJECTION_VERSION,
        "projection_id": (
            f"{panel['panel_id']}-{str(panel['version']).replace('.', '-')}"
            "-persona-authoring-projection"
        ),
        "created_at": panel["created_at"],
        "source_role": "saved-audience-panel-v3.persona_archetypes",
        "provenance_status": "canonical_panel_projection_only",
        "panel_binding": _panel_binding(
            panel, persona_id=str(first_persona["persona_archetype_id"])
        ),
        "persona_archetypes": personas,
        "grounded_profile_snapshot_bindings": bindings,
        "projection_sha256": None,
    }
    projection["projection_sha256"] = sha256_json(projection)
    return validate_persona_authoring_projection(projection)


def _matching_profiles(
    panel: Mapping[str, object],
    *,
    persona_id: str,
    authoritative_snapshot: Mapping[str, object],
) -> list[dict[str, object]]:
    profile_ids: list[str] = []
    matches = []
    for profile in _profile_rows(panel):
        profile_id = str(profile["grounded_profile_id"])
        profile_ids.append(profile_id)
        if profile["persona_archetype_id"] == persona_id:
            if profile["profile_snapshot"] != authoritative_snapshot:
                raise ContractError(
                    "every matching grounded profile before value must exactly "
                    "match the authoritative persona snapshot"
                )
            matches.append(profile)
    if len(profile_ids) != len(set(profile_ids)):
        raise ContractError("validated panel has duplicate grounded-profile IDs")
    if not matches:
        raise ContractError(
            "target persona must have at least one matching grounded profile"
        )
    return matches


def _changed_path_rows(
    *,
    base_panel: Mapping[str, object],
    candidate_panel: Mapping[str, object],
    persona_id: str,
    field: str,
    profile_ids: list[str],
) -> list[dict[str, object]]:
    base_persona = _find_persona(base_panel, persona_id)
    candidate_persona = _find_persona(candidate_panel, persona_id)
    rows = [
        {
            "path": "$.version",
            "before": base_panel["version"],
            "after": candidate_panel["version"],
        },
        {
            "path": "$.created_at",
            "before": base_panel["created_at"],
            "after": candidate_panel["created_at"],
        },
        {
            "path": "$.updated_at",
            "before": base_panel["updated_at"],
            "after": candidate_panel["updated_at"],
        },
        {
            "path": f"$.persona_archetypes[{persona_id}].{field}",
            "before": deepcopy(base_persona[field]),
            "after": deepcopy(candidate_persona[field]),
        },
    ]
    base_profiles = {
        row["grounded_profile_id"]: row for row in _profile_rows(base_panel)
    }
    candidate_profiles = {
        row["grounded_profile_id"]: row for row in _profile_rows(candidate_panel)
    }
    for profile_id in profile_ids:
        rows.append(
            {
                "path": (
                    "$.grounded_context_profiles"
                    f"[{profile_id}].profile_snapshot.{field}"
                ),
                "before": deepcopy(
                    base_profiles[profile_id]["profile_snapshot"][field]
                ),
                "after": deepcopy(
                    candidate_profiles[profile_id]["profile_snapshot"][field]
                ),
            }
        )
    return sorted(rows, key=lambda row: row["path"])


def _assert_exact_candidate_diff(
    *,
    base_panel: Mapping[str, object],
    candidate_panel: Mapping[str, object],
    persona_id: str,
    field: str,
    profile_ids: list[str],
) -> None:
    expected = deepcopy(base_panel)
    expected["version"] = candidate_panel["version"]
    expected["created_at"] = candidate_panel["created_at"]
    expected["updated_at"] = candidate_panel["updated_at"]
    _find_persona(expected, persona_id)[field] = deepcopy(
        _find_persona(candidate_panel, persona_id)[field]
    )
    expected_profiles = {
        row["grounded_profile_id"]: row for row in _profile_rows(expected)
    }
    candidate_profiles = {
        row["grounded_profile_id"]: row for row in _profile_rows(candidate_panel)
    }
    for profile_id in profile_ids:
        expected_profiles[profile_id]["profile_snapshot"][field] = deepcopy(
            candidate_profiles[profile_id]["profile_snapshot"][field]
        )
    if expected != candidate_panel:
        raise ContractError(
            "candidate contains a change outside the exact behavioral allowlist"
        )


def _build_candidate_binding(
    *,
    candidate_id: str,
    created_at: str,
    base_panel: Mapping[str, object],
    candidate_panel: Mapping[str, object],
    proposal: Mapping[str, object],
    base_projection: Mapping[str, object],
    candidate_projection: Mapping[str, object],
    applied_operation: Mapping[str, object],
    persona_id: str,
    changed_paths: list[str],
) -> dict[str, object]:
    binding = {
        "schema_version": CANDIDATE_VERSION,
        "candidate_id": candidate_id,
        "created_at": created_at,
        "status": "sandbox_only",
        "evidence_origin": "synthetic_fixture_only",
        "real_world_validation_status": "not_evaluated",
        "registration_permitted": False,
        "activation_permitted": False,
        "active_panel_mutation_permitted": False,
        "base_panel_binding": _panel_binding(
            base_panel,
            persona_id=persona_id,
        ),
        "proposal_binding": {
            "proposal_id": proposal["proposal_id"],
            "proposal_sha256": proposal["proposal_sha256"],
        },
        "candidate_panel_binding": _panel_binding(
            candidate_panel,
            persona_id=persona_id,
        ),
        "base_authoring_projection_binding": {
            "projection_id": base_projection["projection_id"],
            "projection_sha256": base_projection["projection_sha256"],
        },
        "candidate_authoring_projection_binding": {
            "projection_id": candidate_projection["projection_id"],
            "projection_sha256": candidate_projection["projection_sha256"],
        },
        "applied_operation": deepcopy(dict(applied_operation)),
        "allowed_diff": {"changed_paths": list(changed_paths)},
        "forbidden_diff_check": {"passed": True, "forbidden_paths": []},
        "structural_validation": deepcopy(_STRUCTURAL_VALIDATION),
        "synthetic_evaluation_requirement": {"required": True},
        "limitations": list(_LIMITATIONS),
        "candidate_binding_sha256": None,
    }
    binding["candidate_binding_sha256"] = sha256_json(binding)
    return validate_sandbox_candidate_binding(binding)


def materialize_sandbox_candidate(
    *,
    base_panel: dict[str, object],
    proposal: dict[str, object],
    study_manifest: dict[str, object],
    scenario_manifests: list[dict[str, object]],
    experiment_designs: list[dict[str, object]],
    diagnosis: dict[str, object],
    attribute_registry: dict[str, object],
    evidence_library_snapshot: dict[str, object],
    evidence_head_receipt: dict[str, object],
    alternative_causes: dict[str, dict[str, object]],
    candidate_id: str,
    candidate_version: str,
    created_at: str,
) -> dict[str, object]:
    """Return a complete standalone candidate without publishing or registering."""

    require_identifier(candidate_id, "candidate_id")
    require_timestamp(created_at, "created_at")
    base_input_bytes = canonical_json_bytes(base_panel)
    supplied_proposal = validate_experimental_proposal(proposal)

    # This is the authority boundary: everything below is derived from the
    # exact canonical copy returned by the existing standalone v3 validator.
    canonical_base = _canonical_panel(base_panel)
    if canonical_json_bytes(base_panel) != base_input_bytes:
        raise ContractError("base panel input changed during validation")
    base_projection = build_persona_authoring_projection(
        validated_panel=canonical_base
    )
    checked_diagnosis = validate_diagnosis(diagnosis)
    selected = checked_diagnosis["selected_hypothesis"]
    if isinstance(selected, Mapping):
        persona_id = str(selected["target_persona_id"])
    else:
        persona_id = str(
            checked_diagnosis["base_panel_binding"]["persona_id"]
        )
    authoritative_binding = _panel_binding(
        canonical_base, persona_id=persona_id
    )
    recomputed_proposal = build_experimental_proposal(
        base_panel_binding=authoritative_binding,
        study_manifest=study_manifest,
        scenario_manifests=scenario_manifests,
        experiment_designs=experiment_designs,
        diagnosis=checked_diagnosis,
        attribute_registry=attribute_registry,
        evidence_library_snapshot=evidence_library_snapshot,
        evidence_head_receipt=evidence_head_receipt,
        alternative_causes=alternative_causes,
        proposal_id=str(supplied_proposal["proposal_id"]),
        proposed_at=str(supplied_proposal["proposed_at"]),
    )
    if canonical_json_bytes(recomputed_proposal) != canonical_json_bytes(
        supplied_proposal
    ):
        raise ContractError(
            "proposal does not byte-match the recomputed frozen Task 5 proposal"
        )
    checked_proposal = recomputed_proposal
    if checked_proposal["proposal_type"] != "profile_snapshot_update":
        raise CandidateNotMaterializable(
            "proposal has no materializable persona behavior update"
        )

    operation_intent = checked_proposal["operation"]
    if not isinstance(operation_intent, Mapping):
        raise CandidateNotMaterializable("proposal operation is unavailable")
    if str(operation_intent["target_persona_id"]) != persona_id:
        raise ContractError(
            "proposal operation does not target the diagnosis-selected persona"
        )
    field = str(operation_intent["changed_fields"][0])
    if field not in ALLOWED_PERSONA_FIELDS:
        raise ContractError("proposal targets a forbidden persona field")
    proposed_value = deepcopy(operation_intent["proposed_after"][field])
    base_persona = _find_persona(canonical_base, persona_id)
    authoritative_snapshot = _behavior_snapshot(base_persona)
    if proposed_value == authoritative_snapshot[field]:
        raise CandidateNotMaterializable(
            "proposal does not change the authoritative persona behavior value"
        )
    matching = _matching_profiles(
        canonical_base,
        persona_id=persona_id,
        authoritative_snapshot=authoritative_snapshot,
    )

    if checked_proposal["base_panel_binding"] != authoritative_binding:
        raise ContractError(
            "proposal base panel binding does not match the authoritative "
            "validated base panel binding"
        )
    if _version_tuple(candidate_version) <= _version_tuple(
        str(canonical_base["version"])
    ):
        raise ContractError(
            "candidate_version must be strictly newer than the base panel version"
        )
    candidate_timestamp = require_timestamp(created_at, "created_at")
    if any(
        candidate_timestamp <= require_timestamp(
            str(canonical_base[field]), f"base_panel.{field}"
        )
        for field in ("created_at", "updated_at")
    ):
        raise ContractError(
            "candidate created_at must be strictly later than both base panel "
            "timestamps"
        )
    if candidate_timestamp < require_timestamp(
        str(checked_proposal["proposed_at"]), "proposal.proposed_at"
    ):
        raise ContractError("candidate created_at must not precede the proposal")

    candidate = deepcopy(canonical_base)
    candidate["version"] = candidate_version
    candidate["created_at"] = created_at
    candidate["updated_at"] = created_at
    candidate_persona = _find_persona(candidate, persona_id)
    candidate_persona[field] = deepcopy(proposed_value)
    profile_ids = [str(row["grounded_profile_id"]) for row in matching]
    candidate_profiles = {
        row["grounded_profile_id"]: row for row in _profile_rows(candidate)
    }
    for profile_id in profile_ids:
        candidate_profiles[profile_id]["profile_snapshot"][field] = deepcopy(
            proposed_value
        )

    _assert_exact_candidate_diff(
        base_panel=canonical_base,
        candidate_panel=candidate,
        persona_id=persona_id,
        field=field,
        profile_ids=profile_ids,
    )
    canonical_candidate = _canonical_panel(candidate)
    candidate_projection = build_persona_authoring_projection(
        validated_panel=canonical_candidate
    )
    candidate_persona_snapshot = _behavior_snapshot(
        _find_persona(canonical_candidate, persona_id)
    )

    applied_operation = {
        **deepcopy(dict(operation_intent)),
        "target_persona_snapshot_sha256": sha256_json(authoritative_snapshot),
        "before": {field: deepcopy(authoritative_snapshot[field])},
    }
    # Reorder through canonical JSON rather than relying on insertion order.
    applied_operation = json.loads(canonical_json_bytes(applied_operation))
    changes = _changed_path_rows(
        base_panel=canonical_base,
        candidate_panel=canonical_candidate,
        persona_id=persona_id,
        field=field,
        profile_ids=profile_ids,
    )
    diff = {
        "schema_version": _DIFF_VERSION,
        "base_panel_sha256": sha256_json(canonical_base),
        "candidate_panel_sha256": sha256_json(canonical_candidate),
        "changes": changes,
        "diff_sha256": None,
    }
    diff["diff_sha256"] = sha256_json(diff)
    changed_paths = sorted(row["path"] for row in changes)
    binding = _build_candidate_binding(
        candidate_id=candidate_id,
        created_at=created_at,
        base_panel=canonical_base,
        candidate_panel=canonical_candidate,
        proposal=checked_proposal,
        base_projection=base_projection,
        candidate_projection=candidate_projection,
        applied_operation=applied_operation,
        persona_id=persona_id,
        changed_paths=changed_paths,
    )

    validation = {
        "schema_version": _VALIDATION_VERSION,
        "status": "passed",
        "validator": (
            "audience_lab.audience_research_v3.validate_saved_panel_v3"
        ),
        "panel_sha256": sha256_json(canonical_candidate),
        "production_workflow_state": "not_run_sandbox_only",
        "production_construction_audit": "not_run_sandbox_only",
        "production_package_approval": "not_run_sandbox_only",
        "production_library_registration": "not_run_sandbox_only",
    }
    return {
        "candidate_binding": binding,
        "base_authoring_projection": base_projection,
        "candidate_authoring_projection": candidate_projection,
        "base_persona_snapshot": authoritative_snapshot,
        "candidate_persona_snapshot": candidate_persona_snapshot,
        "candidate_panel": canonical_candidate,
        "persona_behavior_diff": diff,
        "experimental_proposal": deepcopy(checked_proposal),
        "standalone_panel_validation": validation,
    }


def _ensure_safe_new_directory(path: Path) -> Path:
    absolute = path.absolute()
    if absolute.exists() or absolute.is_symlink():
        raise UnsafeCandidateOutput(
            "candidate output already exists or is a symlink"
        )
    for parent in absolute.parents:
        if (
            parent not in _SYSTEM_PATH_ALIASES
            and parent.exists()
            and parent.is_symlink()
        ):
            raise UnsafeCandidateOutput(
                "candidate output has a symlinked ancestor"
            )
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return absolute


def _write_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | (
        os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise UnsafeCandidateOutput(
                    "candidate bundle write made no progress"
                )
            remaining = remaining[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _authenticate_materialized(
    materialized: dict[str, object],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    required = {
        "candidate_binding",
        "base_authoring_projection",
        "candidate_authoring_projection",
        "base_persona_snapshot",
        "candidate_persona_snapshot",
        "candidate_panel",
        "persona_behavior_diff",
        "experimental_proposal",
        "standalone_panel_validation",
    }
    if not isinstance(materialized, dict) or set(materialized) != required:
        raise ContractError(
            "materialized candidate must contain the exact closed result"
        )
    binding = validate_sandbox_candidate_binding(materialized["candidate_binding"])
    proposal = validate_experimental_proposal(materialized["experimental_proposal"])
    candidate_panel = _canonical_panel(materialized["candidate_panel"])
    operation = binding["applied_operation"]
    persona_id = str(operation["target_persona_id"])
    field = str(operation["changed_fields"][0])
    target_profile_ids = sorted(
        str(profile["grounded_profile_id"])
        for profile in _profile_rows(candidate_panel)
        if profile["persona_archetype_id"] == persona_id
    )
    if not target_profile_ids:
        raise ContractError("candidate must retain matching grounded profiles")
    expected_paths = {
        "$.version",
        "$.created_at",
        "$.updated_at",
        f"$.persona_archetypes[{persona_id}].{field}",
        *(
            "$.grounded_context_profiles"
            f"[{profile_id}].profile_snapshot.{field}"
            for profile_id in target_profile_ids
        ),
    }
    if set(binding["allowed_diff"]["changed_paths"]) != expected_paths:
        raise ContractError(
            "candidate binding must name every and only allowlisted diff"
        )
    if (
        candidate_panel["created_at"] != binding["created_at"]
        or candidate_panel["updated_at"] != binding["created_at"]
    ):
        raise ContractError(
            "candidate panel timestamps must equal the candidate binding time"
        )

    diff = materialized["persona_behavior_diff"]
    if not isinstance(diff, dict) or set(diff) != {
        "schema_version",
        "base_panel_sha256",
        "candidate_panel_sha256",
        "changes",
        "diff_sha256",
    }:
        raise ContractError("persona behavior diff must have the closed shape")
    if diff["schema_version"] != _DIFF_VERSION:
        raise ContractError("persona behavior diff version is invalid")
    supplied_diff_hash = diff["diff_sha256"]
    unhashed_diff = deepcopy(diff)
    unhashed_diff["diff_sha256"] = None
    if supplied_diff_hash != sha256_json(unhashed_diff):
        raise ContractError("persona behavior diff hash does not match")
    if diff["candidate_panel_sha256"] != sha256_json(candidate_panel):
        raise ContractError(
            "persona behavior diff does not authenticate candidate panel"
        )
    if diff["base_panel_sha256"] != binding["base_panel_binding"]["panel_sha256"]:
        raise ContractError(
            "persona behavior diff does not authenticate the base panel"
        )
    changes = diff["changes"]
    if (
        not isinstance(changes, list)
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"path", "before", "after"}
            for row in changes
        )
        or len({row["path"] for row in changes}) != len(changes)
        or sorted(row["path"] for row in changes)
        != binding["allowed_diff"]["changed_paths"]
    ):
        raise ContractError(
            "persona behavior diff paths do not match the candidate binding"
        )
    change_by_path = {str(row["path"]): row for row in changes}
    candidate_profiles = {
        str(row["grounded_profile_id"]): row
        for row in _profile_rows(candidate_panel)
    }
    actual_after = {
        "$.version": candidate_panel["version"],
        "$.created_at": candidate_panel["created_at"],
        "$.updated_at": candidate_panel["updated_at"],
        f"$.persona_archetypes[{persona_id}].{field}": _find_persona(
            candidate_panel, persona_id
        )[field],
        **{
            (
                "$.grounded_context_profiles"
                f"[{profile_id}].profile_snapshot.{field}"
            ): candidate_profiles[profile_id]["profile_snapshot"][field]
            for profile_id in target_profile_ids
        },
    }
    if any(
        change_by_path[path]["after"] != value
        for path, value in actual_after.items()
    ):
        raise ContractError("persona behavior diff after values are not exact")

    # Publication begins from the candidate bytes and reverses only the closed
    # allowlist. The reconstructed full panel must authenticate as the claimed
    # base; unrelated candidate mutations therefore survive reversal and fail
    # the base hash.
    reconstructed_base = deepcopy(candidate_panel)
    reconstructed_base["version"] = change_by_path["$.version"]["before"]
    reconstructed_base["created_at"] = change_by_path["$.created_at"]["before"]
    reconstructed_base["updated_at"] = change_by_path["$.updated_at"]["before"]
    _find_persona(reconstructed_base, persona_id)[field] = deepcopy(
        change_by_path[
            f"$.persona_archetypes[{persona_id}].{field}"
        ]["before"]
    )
    reconstructed_profiles = {
        str(row["grounded_profile_id"]): row
        for row in _profile_rows(reconstructed_base)
    }
    for profile_id in target_profile_ids:
        path = (
            "$.grounded_context_profiles"
            f"[{profile_id}].profile_snapshot.{field}"
        )
        reconstructed_profiles[profile_id]["profile_snapshot"][field] = deepcopy(
            change_by_path[path]["before"]
        )
    canonical_base = _canonical_panel(reconstructed_base)
    candidate_timestamp = require_timestamp(
        str(candidate_panel["created_at"]),
        "candidate_panel.created_at",
    )
    if any(
        candidate_timestamp <= require_timestamp(
            str(canonical_base[timestamp_field]),
            f"reconstructed_base.{timestamp_field}",
        )
        for timestamp_field in ("created_at", "updated_at")
    ):
        raise ContractError(
            "candidate timestamp must be strictly later than both "
            "reconstructed base timestamps"
        )
    if candidate_timestamp < require_timestamp(
        str(proposal["proposed_at"]),
        "experimental_proposal.proposed_at",
    ):
        raise ContractError(
            "candidate timestamp must not precede the proposal timestamp"
        )
    expected_base_binding = _panel_binding(
        canonical_base,
        persona_id=persona_id,
    )
    if binding["base_panel_binding"] != expected_base_binding:
        raise ContractError(
            "reconstructed base panel does not authenticate the base binding"
        )
    proposal_base = deepcopy(proposal["base_panel_binding"])
    expected_proposal_base = deepcopy(expected_base_binding)
    if proposal_base != expected_proposal_base:
        raise ContractError(
            "reconstructed base panel does not authenticate the proposal"
        )
    if binding["candidate_panel_binding"] != _panel_binding(
        candidate_panel, persona_id=persona_id
    ):
        raise ContractError("candidate panel binding is stale")
    if binding["proposal_binding"] != {
        "proposal_id": proposal["proposal_id"],
        "proposal_sha256": proposal["proposal_sha256"],
    }:
        raise ContractError("candidate proposal binding is stale")

    base_projection = build_persona_authoring_projection(
        validated_panel=canonical_base
    )
    candidate_projection = build_persona_authoring_projection(
        validated_panel=candidate_panel
    )
    if materialized["base_authoring_projection"] != base_projection:
        raise ContractError("reconstructed base projection does not match")
    if materialized["candidate_authoring_projection"] != candidate_projection:
        raise ContractError("rebuilt candidate projection does not match")
    for key, projection in (
        ("base_authoring_projection_binding", base_projection),
        ("candidate_authoring_projection_binding", candidate_projection),
    ):
        if binding[key] != {
            "projection_id": projection["projection_id"],
            "projection_sha256": projection["projection_sha256"],
        }:
            raise ContractError(f"candidate {key} is stale")

    base_snapshot = _behavior_snapshot(_find_persona(canonical_base, persona_id))
    candidate_snapshot = _behavior_snapshot(
        _find_persona(candidate_panel, persona_id)
    )
    if materialized["base_persona_snapshot"] != base_snapshot:
        raise ContractError("reconstructed base persona snapshot does not match")
    if materialized["candidate_persona_snapshot"] != candidate_snapshot:
        raise ContractError("rebuilt candidate persona snapshot does not match")
    proposal_operation = proposal["operation"]
    if not isinstance(proposal_operation, Mapping):
        raise ContractError(
            "candidate proposal must contain one materializable operation"
        )
    expected_operation = {
        **deepcopy(dict(proposal_operation)),
        "target_persona_snapshot_sha256": sha256_json(base_snapshot),
        "before": {field: deepcopy(base_snapshot[field])},
    }
    expected_operation = json.loads(canonical_json_bytes(expected_operation))
    if canonical_json_bytes(operation) != canonical_json_bytes(
        expected_operation
    ):
        raise ContractError(
            "candidate applied operation does not exactly match the proposal "
            "and reconstructed base"
        )
    if operation["proposed_after"] != {field: candidate_snapshot[field]}:
        raise ContractError("applied operation proposed value is not exact")

    recomputed_changes = _changed_path_rows(
        base_panel=canonical_base,
        candidate_panel=candidate_panel,
        persona_id=persona_id,
        field=field,
        profile_ids=target_profile_ids,
    )
    recomputed_diff = {
        "schema_version": _DIFF_VERSION,
        "base_panel_sha256": sha256_json(canonical_base),
        "candidate_panel_sha256": sha256_json(candidate_panel),
        "changes": recomputed_changes,
        "diff_sha256": None,
    }
    recomputed_diff["diff_sha256"] = sha256_json(recomputed_diff)
    if diff != recomputed_diff:
        raise ContractError("persona behavior diff does not exactly replay")
    expected_binding = _build_candidate_binding(
        candidate_id=str(binding["candidate_id"]),
        created_at=str(candidate_panel["created_at"]),
        base_panel=canonical_base,
        candidate_panel=candidate_panel,
        proposal=proposal,
        base_projection=base_projection,
        candidate_projection=candidate_projection,
        applied_operation=expected_operation,
        persona_id=persona_id,
        changed_paths=[
            str(row["path"]) for row in recomputed_changes
        ],
    )
    if canonical_json_bytes(binding) != canonical_json_bytes(expected_binding):
        raise ContractError(
            "candidate binding does not exactly replay from authenticated "
            "candidate documents"
        )

    reapplied = deepcopy(canonical_base)
    reapplied["version"] = candidate_panel["version"]
    reapplied["created_at"] = candidate_panel["created_at"]
    reapplied["updated_at"] = candidate_panel["updated_at"]
    _find_persona(reapplied, persona_id)[field] = deepcopy(
        operation["proposed_after"][field]
    )
    reapplied_profiles = {
        str(row["grounded_profile_id"]): row for row in _profile_rows(reapplied)
    }
    for profile_id in target_profile_ids:
        reapplied_profiles[profile_id]["profile_snapshot"][field] = deepcopy(
            operation["proposed_after"][field]
        )
    if reapplied != candidate_panel:
        raise ContractError(
            "candidate contains a mutation outside the exact allowlist"
        )

    validation = materialized["standalone_panel_validation"]
    if (
        not isinstance(validation, dict)
        or set(validation) != {
            "schema_version",
            "status",
            "validator",
            "panel_sha256",
            "production_workflow_state",
            "production_construction_audit",
            "production_package_approval",
            "production_library_registration",
        }
        or validation.get("schema_version") != _VALIDATION_VERSION
        or validation.get("status") != "passed"
        or validation.get("panel_sha256") != sha256_json(candidate_panel)
        or validation.get("validator")
        != "audience_lab.audience_research_v3.validate_saved_panel_v3"
        or any(
            validation.get(field) != "not_run_sandbox_only"
            for field in (
                "production_workflow_state",
                "production_construction_audit",
                "production_package_approval",
                "production_library_registration",
            )
        )
    ):
        raise ContractError(
            "standalone validation does not authenticate candidate panel"
        )
    return binding, candidate_panel, canonical_base, proposal


def publish_sandbox_candidate_bundle(
    *,
    materialized: dict[str, object],
    study_manifest: dict[str, object],
    scenario_manifests: list[dict[str, object]],
    experiment_designs: list[dict[str, object]],
    diagnosis: dict[str, object],
    attribute_registry: dict[str, object],
    evidence_library_snapshot: dict[str, object],
    evidence_head_receipt: dict[str, object],
    alternative_causes: dict[str, dict[str, object]],
    output_dir: Path,
) -> Path:
    """Atomically publish the distinct, non-ZIP experimental bundle."""

    (
        binding,
        candidate_panel,
        reconstructed_base,
        proposal,
    ) = _authenticate_materialized(materialized)
    replayed = materialize_sandbox_candidate(
        base_panel=reconstructed_base,
        proposal=proposal,
        study_manifest=study_manifest,
        scenario_manifests=scenario_manifests,
        experiment_designs=experiment_designs,
        diagnosis=diagnosis,
        attribute_registry=attribute_registry,
        evidence_library_snapshot=evidence_library_snapshot,
        evidence_head_receipt=evidence_head_receipt,
        alternative_causes=alternative_causes,
        candidate_id=str(binding["candidate_id"]),
        candidate_version=str(candidate_panel["version"]),
        created_at=str(binding["created_at"]),
    )
    if canonical_json_bytes(replayed) != canonical_json_bytes(materialized):
        raise ContractError(
            "materialized candidate graph does not byte-match the complete "
            "frozen Task 5 replay"
        )
    target = _ensure_safe_new_directory(Path(output_dir))
    files = {
        "experimental-candidate-binding.json": canonical_json_bytes(binding),
        "base-persona-authoring-projection.json": canonical_json_bytes(
            validate_persona_authoring_projection(
                materialized["base_authoring_projection"]
            )
        ),
        "candidate-persona-authoring-projection.json": canonical_json_bytes(
            validate_persona_authoring_projection(
                materialized["candidate_authoring_projection"]
            )
        ),
        "base-persona-snapshot.json": canonical_json_bytes(
            materialized["base_persona_snapshot"]
        ),
        "candidate-persona-snapshot.json": canonical_json_bytes(
            materialized["candidate_persona_snapshot"]
        ),
        "candidate-audience-panel.json": canonical_json_bytes(
            candidate_panel
        ),
        "persona-behavior-diff.json": canonical_json_bytes(
            materialized["persona_behavior_diff"]
        ),
        "experimental-proposal.json": canonical_json_bytes(
            validate_experimental_proposal(
                materialized["experimental_proposal"]
            )
        ),
        "standalone-panel-validation.json": canonical_json_bytes(
            materialized["standalone_panel_validation"]
        ),
        "README.txt": _README.encode("utf-8"),
    }
    manifest = {
        "schema_version": _BUNDLE_VERSION,
        "candidate_id": binding["candidate_id"],
        "registration_permitted": False,
        "production_package_manifest_present": False,
        "production_package_graph_present": False,
        "files": [
            {
                "path": name,
                "sha256": (
                    "sha256:" + hashlib.sha256(payload).hexdigest()
                ),
                "byte_count": len(payload),
            }
            for name, payload in sorted(files.items())
        ],
        "bundle_manifest_sha256": None,
    }
    manifest["bundle_manifest_sha256"] = sha256_json(manifest)
    files["bundle-manifest.json"] = canonical_json_bytes(manifest)

    stage = Path(
        tempfile.mkdtemp(prefix=".candidate-", dir=str(target.parent))
    )
    try:
        os.chmod(stage, 0o700)
        for name, payload in files.items():
            _write_new(stage / name, payload)
        try:
            os.replace(stage, target)
        except OSError as exc:
            raise UnsafeCandidateOutput(
                "candidate output could not be published atomically"
            ) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return target


__all__ = [
    "CandidateNotMaterializable",
    "UnsafeCandidateOutput",
    "build_persona_authoring_projection",
    "materialize_sandbox_candidate",
    "publish_sandbox_candidate_bundle",
]
