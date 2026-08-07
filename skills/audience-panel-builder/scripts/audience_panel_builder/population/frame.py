"""Pure, deterministic construction of source-neutral population frames."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from ..common import ContractError, sha256_json


SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    POPULATION_FRAME_VERSION,
    validate_frame_request,
    validate_observation_batch,
    validate_population_frame,
)


_TOLERANCE = 1e-9


def _validated_request(value: object) -> dict[str, object]:
    try:
        return validate_frame_request(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _validated_batch(value: object) -> dict[str, object]:
    try:
        return validate_observation_batch(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _validated_frame(value: object) -> dict[str, object]:
    try:
        return validate_population_frame(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _partition_id(unit: str, denominator: str) -> str:
    """Use the canonical denominator unless two units happen to share it."""

    if denominator == unit or denominator.startswith(f"{unit}-"):
        return denominator
    return f"{unit}-{denominator}"


def _no_frame(
    request: dict[str, object],
    *,
    built_at: str,
    reasons: Sequence[str],
) -> dict[str, object]:
    exact_reasons = sorted(set(reasons)) or ["no-compatible-observation-partition"]
    frame = {
        "schema_version": POPULATION_FRAME_VERSION,
        "frame_id": f"{request['request_id']}-no-defensible-frame",
        "frame_version": "1.0.0",
        "built_at": built_at,
        "frame_request_id": request["request_id"],
        "frame_request_sha256": sha256_json(request),
        "target_universe": request["target_audience"],
        "proxy_universes": sorted(
            proxy["universe_id"] for proxy in request["proxy_universes"]
        ),
        "claim_boundary": (
            "No population claim is supported. Preserve Tier 1 with a null "
            "population-frame reference; this result records the failed frame attempt."
        ),
        "units": [],
        "structural_dimensions": sorted(request["required_dimensions"]),
        "cells": [],
        "margins": [],
        "joints": [],
        "source_bindings": [],
        "coverage_assessment": {
            "selection_statement": (
                "No compatible structural observation partition was available."
            ),
            "coverage_statement": "No defensible population coverage.",
            "known_gaps": exact_reasons,
        },
        "modeled_weight_by_dimension": [],
        "modeled_weight_share": 0.0,
        "eligibility": "no_defensible_frame",
        "downgrade_reason": ";".join(exact_reasons),
    }
    return _validated_frame(frame)


def _compatible_batches(
    request: dict[str, object],
    batches: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    proxy_pairs = {
        (proxy["unit"], proxy["denominator"])
        for proxy in request["proxy_universes"]
    }
    request_geography = set(request["geography"])
    allowed_bases = set(request["authorized_evidence_bases"])
    bases_by_access = {
        "public": {"public", "hybrid"},
        "licensed": {"licensed_aggregate", "hybrid"},
        "authorized": {"first_party_aggregate", "hybrid"},
    }
    as_of = date.fromisoformat(request["time_basis"]["as_of"])
    earliest = as_of - timedelta(days=request["time_basis"]["lookback_days"])
    accepted: list[dict[str, object]] = []
    reasons: list[str] = []
    for batch in batches:
        label = str(batch["batch_id"])
        pair = (batch["unit"], batch["denominator"])
        vintage = date.fromisoformat(batch["source"]["vintage"])
        reason = None
        if batch["frame_request_id"] != request["request_id"]:
            reason = "frame-request-mismatch"
        elif pair not in proxy_pairs:
            reason = "unit-denominator-mismatch"
        elif not set(batch["geography"]).issubset(request_geography):
            reason = "geography-mismatch"
        elif not allowed_bases.intersection(
            bases_by_access[batch["access"]["access_type"]]
        ):
            reason = "evidence-basis-mismatch"
        elif not batch["access"]["permission_confirmed"]:
            reason = "permission-not-confirmed"
        elif vintage > as_of or vintage < earliest:
            reason = "vintage-outside-request-window"
        if reason is None:
            accepted.append(batch)
        else:
            reasons.append(f"{reason}:{label}")
    return accepted, reasons


def _calibration_factor(
    request: dict[str, object],
    *,
    unit: str,
    denominator: str,
    dimension_values: Mapping[str, str],
) -> float:
    matching = [
        rule
        for rule in request["calibration_rules"]
        if rule["unit"] == unit
        and rule["denominator"] == denominator
        and all(
            dimension_values.get(name) == value
            for name, value in rule["dimension_values"].items()
        )
    ]
    if len(matching) > 1:
        raise ContractError(
            "multiple calibration rules match one structural cell: "
            + ", ".join(sorted(rule["rule_id"] for rule in matching))
        )
    return 1.0 if not matching else float(matching[0]["calibration_factor"])


def _source_binding(
    batch: dict[str, object],
    partition_id: str,
) -> dict[str, object]:
    return {
        "batch_id": batch["batch_id"],
        "normalized_batch_sha256": batch["normalized_batch_sha256"],
        "raw_snapshot_sha256": batch["raw_snapshot_sha256"],
        "partition_id": partition_id,
        "source": deepcopy(batch["source"]),
        "geography": sorted(batch["geography"]),
        "access": deepcopy(batch["access"]),
        "selection_notes": batch["selection_notes"],
        "coverage_notes": batch["coverage_notes"],
    }


def _coordinate_key(
    *,
    partition_id: str,
    relationship: str,
    dimension_values: Mapping[str, str],
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    return (
        partition_id,
        relationship,
        tuple(sorted(dimension_values.items())),
    )


def build_population_frame(
    *,
    frame_request: dict[str, object],
    observation_batches: Sequence[dict[str, object]],
    built_at: str,
) -> dict[str, object]:
    """Build one canonical population frame without fetching or writing files."""

    request = _validated_request(frame_request)
    if not isinstance(observation_batches, Sequence) or isinstance(
        observation_batches, (str, bytes, bytearray)
    ):
        raise ContractError("observation_batches must be a sequence")
    batches = [_validated_batch(batch) for batch in observation_batches]
    accepted, rejected_reasons = _compatible_batches(request, batches)
    if not accepted:
        return _no_frame(
            request,
            built_at=built_at,
            reasons=rejected_reasons or ["no-compatible-observation-partition"],
        )

    proxy_by_pair = {
        (proxy["unit"], proxy["denominator"]): proxy
        for proxy in request["proxy_universes"]
    }
    partition_by_pair = {
        pair: _partition_id(str(pair[0]), str(pair[1]))
        for pair in sorted(
            {(batch["unit"], batch["denominator"]) for batch in accepted}
        )
        if pair in proxy_by_pair
    }
    accepted = sorted(accepted, key=lambda batch: str(batch["batch_id"]))
    collection_cells: dict[
        tuple[str, str, tuple[str, ...]], list[dict[str, Any]]
    ] = defaultdict(list)
    used_frame_cell_ids: set[str] = set()
    used_coordinates: set[
        tuple[str, str, tuple[tuple[str, str], ...]]
    ] = set()

    for batch in accepted:
        pair = (batch["unit"], batch["denominator"])
        partition_id = partition_by_pair[pair]
        for source_cell in sorted(batch["cells"], key=lambda cell: cell["cell_id"]):
            coordinate = _coordinate_key(
                partition_id=partition_id,
                relationship=str(source_cell["relationship"]),
                dimension_values=source_cell["dimension_values"],
            )
            if coordinate in used_coordinates:
                raise ContractError(
                    "duplicate structural coordinate across accepted "
                    f"observation batches: {coordinate}"
                )
            used_coordinates.add(coordinate)
            base_id = str(source_cell["cell_id"])
            cell_id = base_id
            if cell_id in used_frame_cell_ids:
                cell_id = f"{batch['batch_id']}-{base_id}"
            if cell_id in used_frame_cell_ids:
                raise ContractError(f"frame cell ID collision: {cell_id}")
            used_frame_cell_ids.add(cell_id)
            dimensions = tuple(sorted(source_cell["dimension_values"]))
            key = (partition_id, source_cell["relationship"], dimensions)
            unavailable = (
                source_cell["status"] == "missing" or source_cell["suppressed"]
            )
            collection_cells[key].append({
                "cell_id": cell_id,
                "partition_id": partition_id,
                "dimension_values": dict(sorted(source_cell["dimension_values"].items())),
                "relationship": source_cell["relationship"],
                "origin": "source_observation",
                "modeled_rule_id": None,
                "status": source_cell["status"],
                "estimate": source_cell["estimate"],
                "source_uncertainty": deepcopy(source_cell["uncertainty"]),
                "structural_weight": None,
                "weight_semantic": None,
                "uncertainty": {"lower": None, "upper": None},
                "suppressed": source_cell["suppressed"],
                "source_observations": [{
                    "batch_id": batch["batch_id"],
                    "cell_id": source_cell["cell_id"],
                }],
                "calibration_factor": (
                    None
                    if unavailable
                    else _calibration_factor(
                        request,
                        unit=str(batch["unit"]),
                        denominator=str(batch["denominator"]),
                        dimension_values=source_cell["dimension_values"],
                    )
                ),
                "access_type": batch["access"]["access_type"],
            })

    for rule in sorted(request["modeled_cell_rules"], key=lambda item: item["rule_id"]):
        pair = (rule["unit"], rule["denominator"])
        partition_id = partition_by_pair.get(pair)
        if partition_id is None:
            continue
        dimensions = tuple(sorted(rule["dimension_values"]))
        relationship = "marginal" if len(dimensions) == 1 else "joint"
        coordinate = _coordinate_key(
            partition_id=partition_id,
            relationship=relationship,
            dimension_values=rule["dimension_values"],
        )
        if coordinate in used_coordinates:
            raise ContractError(
                "duplicate structural coordinate between a source observation "
                f"and modeled declaration: {coordinate}"
            )
        used_coordinates.add(coordinate)
        cell_id = f"modeled-{rule['rule_id']}"
        if cell_id in used_frame_cell_ids:
            raise ContractError(f"frame cell ID collision: {cell_id}")
        used_frame_cell_ids.add(cell_id)
        collection_cells[(partition_id, relationship, dimensions)].append({
            "cell_id": cell_id,
            "partition_id": partition_id,
            "dimension_values": dict(sorted(rule["dimension_values"].items())),
            "relationship": relationship,
            "origin": "modeled_rule",
            "modeled_rule_id": rule["rule_id"],
            "status": "modeled",
            "estimate": None,
            "source_uncertainty": None,
            "structural_weight": float(rule["structural_weight"]),
            "weight_semantic": "experimental_modeled_weight",
            "uncertainty": deepcopy(rule["uncertainty"]),
            "suppressed": False,
            "source_observations": [],
            "calibration_factor": _calibration_factor(
                request,
                unit=str(rule["unit"]),
                denominator=str(rule["denominator"]),
                dimension_values=rule["dimension_values"],
            ),
            "access_type": "authorized",
        })

    has_available_weight_basis = any(
        (
            cell["origin"] == "source_observation"
            and cell["status"] != "missing"
            and not cell["suppressed"]
            and float(cell["estimate"]) > 0
        )
        or (
            cell["origin"] == "modeled_rule"
            and float(cell["structural_weight"]) > 0
        )
        for members in collection_cells.values()
        for cell in members
    )
    if not has_available_weight_basis:
        return _no_frame(
            request,
            built_at=built_at,
            reasons=[*rejected_reasons, "no-available-weighted-cells"],
        )

    cells: list[dict[str, object]] = []
    margins: list[dict[str, object]] = []
    joints: list[dict[str, object]] = []
    known_gaps = list(rejected_reasons)
    usable_authorized_partitions: set[str] = set()
    for key in sorted(collection_cells):
        partition_id, relationship, dimensions = key
        members = collection_cells[key]
        modeled_rules = [
            cell for cell in members if cell["origin"] == "modeled_rule"
        ]
        declared_modeled = math.fsum(
            float(cell["structural_weight"]) for cell in modeled_rules
        )
        if declared_modeled > 1.0 + _TOLERANCE:
            raise ContractError(
                "predeclared modeled weights exceed 1.0 for "
                f"{partition_id}/{relationship}/{'-'.join(dimensions)}"
            )
        available_source = [
            cell
            for cell in members
            if cell["origin"] == "source_observation"
            and cell["status"] != "missing"
            and not cell["suppressed"]
        ]
        source_total = math.fsum(float(cell["estimate"]) for cell in available_source)
        remaining = max(0.0, 1.0 - declared_modeled)
        if source_total <= 0 and remaining > _TOLERANCE and (
            available_source or modeled_rules
        ):
            known_gaps.append(
                "nonpositive-collection-total:"
                f"{partition_id}:{relationship}:{'-'.join(dimensions)}"
            )
            continue
        if source_total <= 0:
            # A fully modeled collection can be valid, but observed zero-count
            # cells provide no defensible relative-weight basis and stay only
            # in the immutable source binding rather than being relabeled.
            members = [
                cell for cell in members if cell not in available_source
            ]
            available_source = []
        for cell in available_source:
            weight = remaining * float(cell["estimate"]) / source_total
            cell["structural_weight"] = weight
            cell["weight_semantic"] = (
                "experimental_modeled_weight"
                if cell["status"] == "modeled"
                else (
                    "authorized_cohort_weight"
                    if cell["access_type"] == "authorized"
                    else "population_weight"
                )
            )
            lower = float(cell["source_uncertainty"]["lower"])
            upper = float(cell["source_uncertainty"]["upper"])
            cell["uncertainty"] = {
                "lower": max(0.0, remaining * lower / source_total),
                "upper": min(1.0, remaining * upper / source_total),
            }
            if (
                cell["origin"] == "source_observation"
                and cell["access_type"] == "authorized"
                and weight > _TOLERANCE
            ):
                usable_authorized_partitions.add(partition_id)
        canonical_members = []
        for cell in sorted(members, key=lambda item: item["cell_id"]):
            canonical_members.append({
                key_name: cell[key_name]
                for key_name in (
                    "cell_id",
                    "partition_id",
                    "dimension_values",
                    "relationship",
                    "origin",
                    "modeled_rule_id",
                    "status",
                    "structural_weight",
                    "weight_semantic",
                    "uncertainty",
                    "suppressed",
                    "source_observations",
                    "calibration_factor",
                )
            })
        cells.extend(canonical_members)
        unavailable_only = not any(
            cell["status"] != "missing" and not cell["suppressed"]
            for cell in canonical_members
        )
        record = {
            "partition_id": partition_id,
            "dimensions": list(dimensions),
            "cell_ids": [cell["cell_id"] for cell in canonical_members],
            "missing_reason": (
                "All source cells in this collection are missing or suppressed."
                if unavailable_only
                else None
            ),
        }
        (margins if relationship == "marginal" else joints).append(record)
        if unavailable_only:
            known_gaps.append(
                f"unavailable-collection:{partition_id}:{'-'.join(dimensions)}"
            )

    if not cells:
        return _no_frame(
            request,
            built_at=built_at,
            reasons=known_gaps or ["no-available-weighted-cells"],
        )

    target_partitions = {
        partition_by_pair[pair]
        for pair in partition_by_pair
        if pair[0] == request["target_unit"]
    }
    joint_by_signature = {
        (record["partition_id"], tuple(sorted(record["dimensions"]))): record
        for record in joints
    }
    missing_critical = []
    for partition_id in sorted(target_partitions):
        for required_joint in sorted(
            (tuple(sorted(joint)) for joint in request["required_joints"])
        ):
            signature = (partition_id, required_joint)
            existing = joint_by_signature.get(signature)
            if existing is None:
                joints.append({
                    "partition_id": partition_id,
                    "dimensions": list(required_joint),
                    "cell_ids": [],
                    "missing_reason": (
                        "The required critical joint is not available from "
                        "the selected source observations."
                    ),
                })
            elif existing["missing_reason"] is not None:
                existing["missing_reason"] = (
                    "The required critical joint is not available from "
                    "the selected source observations."
                )
            if existing is None or existing["missing_reason"] is not None:
                missing_critical.append(
                    f"missing-critical-joint:{partition_id}:{'-'.join(required_joint)}"
                )
    known_gaps.extend(missing_critical)

    expected_modeled: dict[tuple[str, str], float] = {}
    for (partition_id, _relationship, dimensions), members in collection_cells.items():
        if not any(cell["structural_weight"] is not None for cell in members):
            continue
        share = math.fsum(
            float(cell["structural_weight"])
            for cell in members
            if cell["status"] == "modeled"
            and cell["structural_weight"] is not None
        )
        for dimension in dimensions:
            key = (partition_id, dimension)
            expected_modeled[key] = max(expected_modeled.get(key, 0.0), share)
    modeled_by_dimension = [
        {
            "partition_id": partition_id,
            "dimension": dimension,
            "share": share,
            "status": "experimental" if share > 0.30 else "supported",
        }
        for (partition_id, dimension), share in sorted(expected_modeled.items())
    ]
    modeled_share = max(expected_modeled.values(), default=0.0)
    source_bindings = [
        _source_binding(
            batch,
            partition_by_pair[(batch["unit"], batch["denominator"])],
        )
        for batch in accepted
    ]
    usable_authorized_target_pairs = {
        pair
        for pair, partition_id in partition_by_pair.items()
        if pair[0] == request["target_unit"]
        and partition_id in usable_authorized_partitions
    }
    exact_authorized_target_support = any(
        bool(proxy_by_pair[pair]["exact"])
        for pair in usable_authorized_target_pairs
    )
    downgrade_reasons = []
    if modeled_share > 0.30:
        downgrade_reasons.append("modeled-share-above-threshold")
    downgrade_reasons.extend(missing_critical)
    if (
        usable_authorized_target_pairs
        and not exact_authorized_target_support
    ):
        downgrade_reasons.append("authorized-denominator-not-exact")
    eligibility = (
        "experimental"
        if downgrade_reasons
        else (
            "eligible_tier_3"
            if exact_authorized_target_support
            else "eligible_tier_2"
        )
    )
    descriptions = "; ".join(
        f"{proxy['universe_id']}: {proxy['description']}"
        for proxy in sorted(
            request["proxy_universes"], key=lambda item: item["universe_id"]
        )
    )
    claim_boundary = (
        "Authorized cohort composition only; it does not represent people "
        "outside the exact permissioned cohort."
        if exact_authorized_target_support
        else (
            "Public proxy frame only. "
            + descriptions
            + " These proxies do not represent the full commercial target audience."
        )
    )
    frame = {
        "schema_version": POPULATION_FRAME_VERSION,
        "frame_id": f"{request['request_id']}-population-frame",
        "frame_version": "1.0.0",
        "built_at": built_at,
        "frame_request_id": request["request_id"],
        "frame_request_sha256": sha256_json(request),
        "target_universe": request["target_audience"],
        "proxy_universes": sorted(
            proxy["universe_id"] for proxy in request["proxy_universes"]
        ),
        "claim_boundary": claim_boundary,
        "units": [
            {
                "partition_id": partition_id,
                "unit": pair[0],
                "denominator": pair[1],
                "exact": bool(proxy_by_pair[pair]["exact"]),
            }
            for pair, partition_id in sorted(
                partition_by_pair.items(), key=lambda item: item[1]
            )
        ],
        "structural_dimensions": sorted(request["required_dimensions"]),
        "cells": sorted(cells, key=lambda cell: cell["cell_id"]),
        "margins": sorted(
            margins,
            key=lambda row: (row["partition_id"], row["dimensions"]),
        ),
        "joints": sorted(
            joints,
            key=lambda row: (row["partition_id"], row["dimensions"]),
        ),
        "source_bindings": sorted(
            source_bindings, key=lambda binding: binding["batch_id"]
        ),
        "coverage_assessment": {
            "selection_statement": (
                f"Selected {len(source_bindings)} canonical observation "
                "batch(es) by exact request, unit, denominator, geography, "
                "vintage, and permission bindings."
            ),
            "coverage_statement": (
                "Every retained observation remains inside its own explicit "
                "unit and denominator partition."
            ),
            "known_gaps": sorted(set(known_gaps)),
        },
        "modeled_weight_by_dimension": modeled_by_dimension,
        "modeled_weight_share": modeled_share,
        "eligibility": eligibility,
        "downgrade_reason": ";".join(sorted(set(downgrade_reasons))),
    }
    return _validated_frame(frame)
