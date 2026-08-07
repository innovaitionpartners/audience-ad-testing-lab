"""Pure, deterministic construction of explicit reusable profile composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
import sys
from typing import Any

from ..common import (
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


SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    COMPOSITION_PLAN_VERSION,
    EVIDENCE_BASES,
    PANEL_TIERS,
    validate_composition_plan,
    validate_population_frame,
)


_STRUCTURAL_KEYS = {
    "structural_group_id",
    "cell_ids",
    "structural_finding_ids",
    "evidence_ids",
    "must_cover",
    "planning_allocation",
}
_OVERLAY_KEYS = {
    "overlay_id",
    "description",
    "allocation_basis",
    "finding_ids",
    "evidence_ids",
    "topic_bindings",
    "decision_relevance",
}
_SUPPORTED_PROFILE_KEYS = {
    "status",
    "profile_id",
    "structural_group_id",
    "overlay_ids",
    "support_finding_ids",
    "support_evidence_ids",
    "conditional_overlay_allocation",
}
_UNSUPPORTED_PROFILE_KEYS = {
    "status",
    "structural_group_id",
    "overlay_ids",
    "reason_code",
    "reason",
}
_TOLERANCE = 1e-9


def _sequence(value: object, path: str, *, nonempty: bool = True) -> list[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise ContractError(f"{path} must be a sequence")
    result = list(value)
    if nonempty and not result:
        raise ContractError(f"{path} must not be empty")
    return result


def _identifiers(value: object, path: str, *, nonempty: bool = True) -> list[str]:
    values = require_string_array(value, path, nonempty=nonempty)
    return sorted(
        require_identifier(item, f"{path}[{index}]")
        for index, item in enumerate(values)
    )


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{path} must be a finite number")
    if result < 0.0 or result > 1.0:
        raise ContractError(f"{path} must be between 0 and 1")
    return result


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{path} must be a boolean")
    return value


def _reconcile(weights: Sequence[float], path: str) -> None:
    if abs(math.fsum(weights) - 1.0) > _TOLERANCE:
        raise ContractError(f"{path} must reconcile to 1.0")


def _canonical_structural_findings(
    values: Sequence[dict[str, object]],
    *,
    usable_frame: bool,
    provisional: bool,
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    seen: set[str] = set()
    assigned_cells: set[str] = set()
    for index, value in enumerate(_sequence(values, "structural_findings")):
        path = f"structural_findings[{index}]"
        item = require_object(value, _STRUCTURAL_KEYS, path)
        group_id = require_identifier(
            item["structural_group_id"],
            f"{path}.structural_group_id",
        )
        if group_id in seen:
            raise ContractError(f"{path}.structural_group_id is duplicated")
        seen.add(group_id)
        cell_ids = _identifiers(
            item["cell_ids"],
            f"{path}.cell_ids",
            nonempty=usable_frame,
        )
        if not usable_frame and cell_ids:
            raise ContractError(
                f"{path}.cell_ids must be empty for Tier 1 evidence groups"
            )
        overlap = assigned_cells.intersection(cell_ids)
        if overlap:
            raise ContractError(
                f"{path}.cell_ids assigns cells more than once: "
                + ", ".join(sorted(overlap))
            )
        assigned_cells.update(cell_ids)
        planning = item["planning_allocation"]
        if usable_frame and planning is not None:
            raise ContractError(
                f"{path}.planning_allocation must be null when frame weights apply"
            )
        if not usable_frame and planning is None:
            raise ContractError(
                f"{path}.planning_allocation is required for Tier 1 evidence groups"
            )
        structural_finding_ids = _identifiers(
            item["structural_finding_ids"],
            f"{path}.structural_finding_ids",
            nonempty=not provisional,
        )
        evidence_ids = _identifiers(
            item["evidence_ids"],
            f"{path}.evidence_ids",
            nonempty=not provisional,
        )
        if provisional and (structural_finding_ids or evidence_ids):
            raise ContractError(
                f"{path} must have empty structural support when "
                "evidence_basis is none"
            )
        groups.append({
            "structural_group_id": group_id,
            "cell_ids": cell_ids,
            "structural_finding_ids": structural_finding_ids,
            "evidence_ids": evidence_ids,
            "must_cover": _boolean(item["must_cover"], f"{path}.must_cover"),
            "planning_allocation": (
                None
                if planning is None
                else _number(planning, f"{path}.planning_allocation")
            ),
        })
    groups.sort(key=lambda item: str(item["structural_group_id"]))
    if not usable_frame:
        _reconcile(
            [float(item["planning_allocation"]) for item in groups],
            "structural_findings.planning_allocation",
        )
    return groups


def _select_collection(
    frame: Mapping[str, object],
    groups: Sequence[dict[str, object]],
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    frame_cells = {
        str(cell["cell_id"]): cell
        for cell in frame["cells"]
        if isinstance(cell, Mapping)
    }
    supplied_cells = {
        str(cell_id)
        for group in groups
        for cell_id in group["cell_ids"]
    }
    candidates: list[tuple[dict[str, object], dict[str, dict[str, object]]]] = []
    for relationship, collection_name in (
        ("marginal", "margins"),
        ("joint", "joints"),
    ):
        for record in frame[collection_name]:
            available = {
                str(cell_id): frame_cells[str(cell_id)]
                for cell_id in record["cell_ids"]
                if (
                    frame_cells[str(cell_id)]["status"] != "missing"
                    and not frame_cells[str(cell_id)]["suppressed"]
                    and frame_cells[str(cell_id)]["structural_weight"] is not None
                )
            }
            if available and set(available) == supplied_cells:
                candidates.append(({
                    "partition_id": record["partition_id"],
                    "relationship": relationship,
                    "dimensions": sorted(record["dimensions"]),
                }, available))
    if len(candidates) != 1:
        raise ContractError(
            "structural_findings must select exactly one eligible "
            "partition and margin/joint collection"
        )
    return candidates[0]


def _materialize_groups(
    groups: Sequence[dict[str, object]],
    *,
    usable_frame: bool,
    provisional: bool,
    selected_cells: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    result: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    for group in groups:
        cell_ids = list(group["cell_ids"])
        if usable_frame:
            if not set(cell_ids).issubset(selected_cells):
                raise ContractError(
                    "structural group cells must belong to the selected collection"
                )
            semantics = {
                str(selected_cells[cell_id]["weight_semantic"])
                for cell_id in cell_ids
            }
            if len(semantics) != 1:
                raise ContractError(
                    "one structural group cannot combine different weight semantics"
                )
            weight = math.fsum(
                float(selected_cells[cell_id]["structural_weight"])
                for cell_id in cell_ids
            )
            semantic = next(iter(semantics))
            origin = "frame_cells"
        else:
            weight = float(group["planning_allocation"])
            semantic = "planning_allocation"
            origin = (
                "tier_1_provisional" if provisional else "tier_1_evidence"
            )
        materialized = {
            "structural_group_id": group["structural_group_id"],
            "origin": origin,
            "cell_ids": cell_ids,
            "structural_finding_ids": list(group["structural_finding_ids"]),
            "evidence_ids": list(group["evidence_ids"]),
            "structural_weight": weight,
            "weight_semantic": semantic,
            "must_cover": group["must_cover"],
        }
        result.append(materialized)
        by_id[str(group["structural_group_id"])] = materialized
    return result, by_id


def _canonical_overlays(
    values: Sequence[dict[str, object]],
    *,
    provisional: bool,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    overlays: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    for index, value in enumerate(_sequence(values, "overlay_findings")):
        path = f"overlay_findings[{index}]"
        item = require_object(value, _OVERLAY_KEYS, path)
        overlay_id = require_identifier(item["overlay_id"], f"{path}.overlay_id")
        if overlay_id in by_id:
            raise ContractError(f"{path}.overlay_id is duplicated")
        relevance = require_enum(
            item["decision_relevance"],
            {"topic_bound", "unrelated_affinity"},
            f"{path}.decision_relevance",
        )
        if relevance == "unrelated_affinity":
            raise ContractError(
                f"{path} is unrelated affinity evidence and cannot become a profile overlay"
            )
        evidence_ids = _identifiers(
            item["evidence_ids"],
            f"{path}.evidence_ids",
            nonempty=not provisional,
        )
        topic_bindings = []
        topic_ids: set[str] = set()
        for topic_index, raw_topic in enumerate(
            require_array(
                item["topic_bindings"],
                f"{path}.topic_bindings",
                nonempty=not provisional,
            )
        ):
            topic_path = f"{path}.topic_bindings[{topic_index}]"
            topic = require_object(
                raw_topic,
                {"topic_id", "evidence_ids"},
                topic_path,
            )
            topic_id = require_identifier(
                topic["topic_id"],
                f"{topic_path}.topic_id",
            )
            if topic_id in topic_ids:
                raise ContractError(f"{topic_path}.topic_id is duplicated")
            topic_ids.add(topic_id)
            topic_evidence = _identifiers(
                topic["evidence_ids"],
                f"{topic_path}.evidence_ids",
            )
            if not set(topic_evidence).issubset(evidence_ids):
                raise ContractError(
                    f"{topic_path}.evidence_ids must be a subset of overlay evidence_ids"
                )
            topic_bindings.append({
                "topic_id": topic_id,
                "evidence_ids": topic_evidence,
            })
        topic_bindings.sort(key=lambda item: str(item["topic_id"]))
        finding_ids = _identifiers(
            item["finding_ids"],
            f"{path}.finding_ids",
            nonempty=not provisional,
        )
        allocation_basis = require_enum(
            item["allocation_basis"],
            {"observed", "estimated", "experimental"},
            f"{path}.allocation_basis",
        )
        if provisional:
            if finding_ids or evidence_ids or topic_bindings:
                raise ContractError(
                    f"{path} must have empty overlay support when "
                    "evidence_basis is none"
                )
            if allocation_basis != "experimental":
                raise ContractError(
                    f"{path}.allocation_basis must be experimental when "
                    "evidence_basis is none"
                )
        overlay = {
            "overlay_id": overlay_id,
            "description": require_string(
                item["description"],
                f"{path}.description",
            ),
            "allocation_basis": allocation_basis,
            "finding_ids": finding_ids,
            "evidence_ids": evidence_ids,
            "topic_bindings": topic_bindings,
        }
        overlays.append(overlay)
        by_id[overlay_id] = overlay
    overlays.sort(key=lambda item: str(item["overlay_id"]))
    return overlays, by_id


def _materialize_profiles(
    values: Sequence[dict[str, object]],
    *,
    groups: Mapping[str, dict[str, object]],
    overlays: Mapping[str, dict[str, object]],
    provisional: bool,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    set[str],
]:
    profiles: list[dict[str, object]] = []
    unsupported: list[dict[str, object]] = []
    supported_signatures: set[tuple[str, tuple[str, ...]]] = set()
    unsupported_signatures: set[tuple[str, tuple[str, ...]]] = set()
    profile_ids: set[str] = set()
    conditional_by_group: dict[str, list[float]] = {
        group_id: [] for group_id in groups
    }
    used_overlay_ids: set[str] = set()
    for index, value in enumerate(_sequence(values, "supported_profile_specs")):
        path = f"supported_profile_specs[{index}]"
        if not isinstance(value, Mapping):
            raise ContractError(f"{path} must be an object")
        status = require_enum(
            value.get("status"),
            {"provisional", "supported", "unsupported"},
            f"{path}.status",
        )
        keys = (
            _SUPPORTED_PROFILE_KEYS
            if status != "unsupported"
            else _UNSUPPORTED_PROFILE_KEYS
        )
        item = require_object(value, keys, path)
        group_id = require_identifier(
            item["structural_group_id"],
            f"{path}.structural_group_id",
        )
        if group_id not in groups:
            raise ContractError(f"{path}.structural_group_id does not resolve")
        overlay_ids = _identifiers(
            item["overlay_ids"],
            f"{path}.overlay_ids",
        )
        if not set(overlay_ids).issubset(overlays):
            raise ContractError(f"{path}.overlay_ids contains an undeclared overlay")
        signature = (group_id, tuple(overlay_ids))
        if status == "unsupported":
            if signature in unsupported_signatures:
                raise ContractError(f"{path} duplicates an unsupported signature")
            unsupported_signatures.add(signature)
            unsupported.append({
                "structural_group_id": group_id,
                "overlay_ids": overlay_ids,
                "reason_code": require_identifier(
                    item["reason_code"],
                    f"{path}.reason_code",
                ),
                "reason": require_string(item["reason"], f"{path}.reason"),
            })
            continue
        expected_status = "provisional" if provisional else "supported"
        if status != expected_status:
            raise ContractError(
                f"{path}.status must be {expected_status} for this "
                "evidence basis"
            )
        if signature in supported_signatures:
            raise ContractError(f"{path} duplicates a supported signature")
        if signature in unsupported_signatures:
            raise ContractError(
                f"{path} cannot be both supported and unsupported"
            )
        supported_signatures.add(signature)
        profile_id = require_identifier(item["profile_id"], f"{path}.profile_id")
        if profile_id in profile_ids:
            raise ContractError(f"{path}.profile_id is duplicated")
        profile_ids.add(profile_id)
        conditional = _number(
            item["conditional_overlay_allocation"],
            f"{path}.conditional_overlay_allocation",
        )
        group = groups[group_id]
        allowed_findings = set(group["structural_finding_ids"])
        allowed_evidence = set(group["evidence_ids"])
        for overlay_id in overlay_ids:
            allowed_findings.update(overlays[overlay_id]["finding_ids"])
            allowed_evidence.update(overlays[overlay_id]["evidence_ids"])
        support_findings = _identifiers(
            item["support_finding_ids"],
            f"{path}.support_finding_ids",
            nonempty=not provisional,
        )
        support_evidence = _identifiers(
            item["support_evidence_ids"],
            f"{path}.support_evidence_ids",
            nonempty=not provisional,
        )
        if provisional and (support_findings or support_evidence):
            raise ContractError(
                f"{path} provisional profiles require empty support bindings"
            )
        if set(support_findings) != allowed_findings:
            raise ContractError(
                f"{path}.support_finding_ids must exactly resolve the structural "
                "group and selected overlays"
            )
        if set(support_evidence) != allowed_evidence:
            raise ContractError(
                f"{path}.support_evidence_ids must exactly resolve the structural "
                "group and selected overlays"
            )
        used_overlay_ids.update(overlay_ids)
        conditional_by_group[group_id].append(conditional)
        profiles.append({
            "profile_id": profile_id,
            "structural_group_id": group_id,
            "overlay_ids": overlay_ids,
            "support_status": expected_status,
            "support_finding_ids": support_findings,
            "support_evidence_ids": support_evidence,
            "conditional_overlay_allocation": conditional,
            "overlay_weight_semantic": "planning_allocation",
            "effective_profile_allocation": (
                float(group["structural_weight"]) * conditional
            ),
            "effective_weight_semantic": group["weight_semantic"],
            "source_cell_ids": list(group["cell_ids"]),
        })
    collision = supported_signatures.intersection(unsupported_signatures)
    if collision:
        raise ContractError(
            "an explicit profile signature cannot be both supported and unsupported"
        )
    for group_id, weights in conditional_by_group.items():
        if not weights:
            raise ContractError(
                f"supported_profile_specs must include {group_id}"
            )
        _reconcile(
            weights,
            f"supported_profile_specs[{group_id}] conditional allocations",
        )
    if len(groups) > 1 and len(overlays) > 1:
        complete_singleton_product = {
            (group_id, (overlay_id,))
            for group_id in groups
            for overlay_id in overlays
        }
        if complete_singleton_product.issubset(supported_signatures):
            raise ContractError(
                "a complete structural-group × overlay Cartesian product is forbidden"
            )
    profiles.sort(key=lambda item: str(item["profile_id"]))
    unsupported.sort(
        key=lambda item: (
            str(item["structural_group_id"]),
            tuple(item["overlay_ids"]),
        )
    )
    return profiles, unsupported, used_overlay_ids


def _tier_outcome(
    *,
    frame_eligibility: str,
    evidence_basis: str,
    requested_tier: str,
    experimental_overlay_used: bool,
) -> tuple[str, list[str], list[str]]:
    requested_rank = int(requested_tier[-1])
    usable_frame = frame_eligibility in {"eligible_tier_2", "eligible_tier_3"}
    if not usable_frame:
        supported_rank = 1
    elif (
        frame_eligibility == "eligible_tier_3"
        and evidence_basis in {"first_party_aggregate", "hybrid"}
    ):
        supported_rank = 3
    else:
        supported_rank = 2
    if experimental_overlay_used:
        supported_rank = 1
    achieved = f"tier_{min(requested_rank, supported_rank, 3)}"
    if achieved == requested_tier:
        return achieved, [], []

    reasons: list[str] = []
    claims: list[str] = []
    if requested_rank == 4:
        reasons.append("tier-4-requires-separate-outcome-validation")
        claims.append(
            "Tier 4 outcome calibration is not constructed from composition inputs."
        )
    if not usable_frame and requested_rank > 1:
        reasons.append("no-eligible-population-frame")
        claims.append(
            "No population-shape or representativeness claim is supported."
        )
    elif supported_rank <= 2 and requested_rank > 2 and not experimental_overlay_used:
        reasons.append("public-or-incompatible-frame-caps-tier-2")
        claims.append(
            "The composition cannot claim an authorized audience-calibrated frame."
        )
    if experimental_overlay_used and requested_rank > 1:
        reasons.append("experimental-overlay-support")
        claims.append(
            "Experimental overlay support permits directional testing only."
        )
    return achieved, sorted(set(reasons)), sorted(set(claims))


def build_composition_plan(
    *,
    population_frame: dict[str, object] | None,
    structural_findings: Sequence[dict[str, object]],
    overlay_findings: Sequence[dict[str, object]],
    supported_profile_specs: Sequence[dict[str, object]],
    requested_tier: str,
    evidence_basis: str,
    plan_id: str,
    plan_version: str,
    built_at: str,
) -> dict[str, object]:
    """Build the smallest caller-declared reusable profile set.

    The function selects one exact frame collection, derives every weight,
    and never creates a study quota, panelist assignment, or implicit profile.
    """

    if population_frame is None:
        raise ContractError(
            "population_frame must contain the canonical population-frame "
            "result, including a no-frame result for Tier 1"
        )
    try:
        frame = validate_population_frame(population_frame)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    usable_frame = frame["eligibility"] in {
        "eligible_tier_2",
        "eligible_tier_3",
    }
    evidence_basis = require_enum(
        evidence_basis,
        set(EVIDENCE_BASES),
        "evidence_basis",
    )
    requested_tier = require_enum(
        requested_tier,
        set(PANEL_TIERS),
        "requested_tier",
    )
    provisional = evidence_basis == "none"
    if provisional and usable_frame:
        raise ContractError(
            "evidence_basis none requires a no-frame Tier 1 result"
        )
    canonical_groups = _canonical_structural_findings(
        structural_findings,
        usable_frame=usable_frame,
        provisional=provisional,
    )
    if usable_frame:
        selection, selected_cells = _select_collection(frame, canonical_groups)
    else:
        selection, selected_cells = None, {}
    groups, group_by_id = _materialize_groups(
        canonical_groups,
        usable_frame=usable_frame,
        provisional=provisional,
        selected_cells=selected_cells,
    )
    overlays, overlay_by_id = _canonical_overlays(
        overlay_findings,
        provisional=provisional,
    )
    profiles, unsupported, used_overlay_ids = _materialize_profiles(
        supported_profile_specs,
        groups=group_by_id,
        overlays=overlay_by_id,
        provisional=provisional,
    )
    achieved_tier, reason_codes, lost_claims = _tier_outcome(
        frame_eligibility=str(frame["eligibility"]),
        evidence_basis=evidence_basis,
        requested_tier=requested_tier,
        experimental_overlay_used=any(
            overlay_by_id[overlay_id]["allocation_basis"] == "experimental"
            for overlay_id in used_overlay_ids
        ),
    )
    modeled_share = (
        math.fsum(
            float(profile["conditional_overlay_allocation"])
            * math.fsum(
                float(selected_cells[cell_id]["structural_weight"])
                for cell_id in profile["source_cell_ids"]
                if selected_cells[cell_id]["status"] == "modeled"
            )
            for profile in profiles
        )
        if usable_frame
        else 0.0
    )
    frame_digest = sha256_json(frame)
    payload = {
        "schema_version": COMPOSITION_PLAN_VERSION,
        "composition_id": require_identifier(plan_id, "plan_id"),
        "plan_version": require_string(plan_version, "plan_version"),
        "built_at": require_string(built_at, "built_at"),
        "evidence_basis": evidence_basis,
        "requested_tier": requested_tier,
        "achieved_tier": achieved_tier,
        "tier_reason_codes": reason_codes,
        "lost_claims": lost_claims,
        "frame_binding": {
            "frame_result_sha256": frame_digest,
            "frame_sha256": frame_digest if usable_frame else None,
            "frame_id": frame["frame_id"] if usable_frame else None,
            "selection": selection,
        },
        "structural_groups": groups,
        "overlay_hypotheses": overlays,
        "profiles": profiles,
        "unsupported_combinations": unsupported,
        "allocation_constraints": [
            "Preserve every explicit materialized profile signature.",
            "Represent every must-cover structural group before discretionary allocation.",
            "Do not allocate an unsupported structural-group and overlay combination.",
        ],
        "run_allocation_rules": {
            "reserve_strategy": "largest-remainder",
            "min_one_for_must_cover": True,
        },
        "required_diagnostics": [
            "effective-allocation-drift",
            "must-cover-coverage",
            "structural-group-deviation",
        ],
        "modeled_cell_share": modeled_share,
    }
    require_timestamp(payload["built_at"], "built_at")
    try:
        return validate_composition_plan(payload, frame=frame)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
