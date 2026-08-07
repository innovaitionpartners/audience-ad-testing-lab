"""Strict source registry v2 and deterministic property-based routing."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import importlib
from itertools import combinations
from pathlib import Path
import re
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
)
from .adapters.base import PopulationAdapter

SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    EVIDENCE_BASES,
    validate_frame_request,
)


SOURCE_REGISTRY_VERSION = "audience-source-registry-v2"
PLAN_VERSION = "population-source-plan-v1"

_REGISTRY_KEYS = {"schema_version", "updated_at", "sources"}
_SOURCE_KEYS = {
    "adapter_id",
    "programs",
    "units",
    "dimensions",
    "joints",
    "geographies",
    "access",
    "authentication",
    "freshness",
    "implementation",
}
_ACCESS_KEYS = {"access_type", "evidence_basis", "required_capability"}
_AUTH_KEYS = {"mode", "required"}
_FRESHNESS_KEYS = {"edition", "vintage", "published_at"}
_IMPORT_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$"
)


def _date(value: object, path: str) -> str:
    text = require_string(value, path)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"{path} must be an ISO 8601 date") from exc
    return text


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{path} must be a boolean")
    return value


def validate_source_registry(payload: object) -> dict[str, object]:
    registry = require_object(payload, _REGISTRY_KEYS, "$")
    if registry["schema_version"] != SOURCE_REGISTRY_VERSION:
        raise ContractError(
            f"$.schema_version must equal {SOURCE_REGISTRY_VERSION}"
        )
    _date(registry["updated_at"], "$.updated_at")
    sources = require_array(registry["sources"], "$.sources", nonempty=True)
    seen: set[str] = set()
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(sources):
        path = f"$.sources[{index}]"
        item = require_object(raw, _SOURCE_KEYS, path)
        adapter_id = require_identifier(item["adapter_id"], f"{path}.adapter_id")
        if adapter_id in seen:
            raise ContractError(f"{path}.adapter_id is duplicated")
        seen.add(adapter_id)
        programs = require_string_array(
            item["programs"], f"{path}.programs", nonempty=True
        )
        units = require_string_array(item["units"], f"{path}.units", nonempty=True)
        dimensions = require_string_array(
            item["dimensions"], f"{path}.dimensions", nonempty=True
        )
        joints: list[list[str]] = []
        for joint_index, raw_joint in enumerate(
            require_array(item["joints"], f"{path}.joints")
        ):
            joint_path = f"{path}.joints[{joint_index}]"
            joint = require_string_array(raw_joint, joint_path, nonempty=True)
            if len(joint) < 2:
                raise ContractError(f"{joint_path} must contain at least two dimensions")
            if not set(joint).issubset(dimensions):
                raise ContractError(f"{joint_path} contains an undeclared dimension")
            if joint != sorted(joint):
                raise ContractError(f"{joint_path} must use canonical sorted order")
            joints.append(joint)
        geographies = require_string_array(
            item["geographies"], f"{path}.geographies", nonempty=True
        )
        access = require_object(item["access"], _ACCESS_KEYS, f"{path}.access")
        access_type = require_string(
            access["access_type"], f"{path}.access.access_type"
        )
        if access_type not in {"public", "authorized"}:
            raise ContractError(f"{path}.access.access_type is unsupported")
        evidence_basis = require_enum(
            access["evidence_basis"],
            EVIDENCE_BASES,
            f"{path}.access.evidence_basis",
        )
        required_capability = require_identifier(
            access["required_capability"],
            f"{path}.access.required_capability",
        )
        authentication = require_object(
            item["authentication"], _AUTH_KEYS, f"{path}.authentication"
        )
        auth_mode = require_identifier(
            authentication["mode"], f"{path}.authentication.mode"
        )
        auth_required = _boolean(
            authentication["required"], f"{path}.authentication.required"
        )
        freshness = require_object(
            item["freshness"], _FRESHNESS_KEYS, f"{path}.freshness"
        )
        edition = require_string(
            freshness["edition"], f"{path}.freshness.edition"
        )
        vintage = _date(freshness["vintage"], f"{path}.freshness.vintage")
        published_at = _date(
            freshness["published_at"], f"{path}.freshness.published_at"
        )
        implementation = require_string(
            item["implementation"], f"{path}.implementation"
        )
        if not _IMPORT_PATH.fullmatch(implementation):
            raise ContractError(
                f"{path}.implementation must be module.path:ClassName"
            )
        normalized.append(
            {
                "adapter_id": adapter_id,
                "programs": programs,
                "units": units,
                "dimensions": dimensions,
                "joints": joints,
                "geographies": geographies,
                "access": {
                    "access_type": access_type,
                    "evidence_basis": evidence_basis,
                    "required_capability": required_capability,
                },
                "authentication": {
                    "mode": auth_mode,
                    "required": auth_required,
                },
                "freshness": {
                    "edition": edition,
                    "vintage": vintage,
                    "published_at": published_at,
                },
                "implementation": implementation,
            }
        )
    return {
        "schema_version": registry["schema_version"],
        "updated_at": registry["updated_at"],
        "sources": normalized,
    }


def _capability_values(
    capabilities: dict[str, object],
    key: str,
) -> set[str]:
    values = capabilities.get(key)
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise ContractError(f"capabilities.{key} must be an array of strings")
    return set(values)


def _joint_key(joint: list[str]) -> tuple[str, ...]:
    return tuple(sorted(joint))


def route_population_sources(
    *,
    frame_request: dict[str, object],
    registry: dict[str, object],
    capabilities: dict[str, object],
) -> dict[str, object]:
    try:
        frame_request = validate_frame_request(frame_request)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    validated = validate_source_registry(registry)
    required_units = [frame_request["target_unit"]]
    for proxy in frame_request["proxy_universes"]:
        assert isinstance(proxy, dict)
        if proxy["unit"] not in required_units:
            required_units.append(proxy["unit"])
    required_geographies = list(frame_request["geography"])
    required_dimensions = set(frame_request["required_dimensions"])
    required_joints = {
        _joint_key(joint) for joint in frame_request["required_joints"]
    }
    evidence_bases = set(frame_request["authorized_evidence_bases"])
    declared_capabilities = set(frame_request["available_capabilities"])
    available_capabilities = _capability_values(
        capabilities, "available_capabilities"
    )
    available_authentication = _capability_values(
        capabilities, "available_authentication"
    )
    as_of = date.fromisoformat(str(frame_request["time_basis"]["as_of"]))
    sources = list(validated["sources"])

    unit_candidates = [
        item
        for item in sources
        if set(item["units"]) & set(required_units)
    ]
    if not unit_candidates:
        raise ContractError("incompatible unit: " + ", ".join(required_units))
    for unit in required_units:
        if not any(unit in item["units"] for item in unit_candidates):
            raise ContractError(f"incompatible unit: {unit}")

    geography_candidates = [
        item
        for item in unit_candidates
        if set(required_geographies).issubset(item["geographies"])
    ]
    if not geography_candidates:
        raise ContractError(
            "unsupported geography: " + ", ".join(required_geographies)
        )

    access_candidates = [
        item
        for item in geography_candidates
        if item["access"]["evidence_basis"] in evidence_bases
    ]
    if not access_candidates:
        raise ContractError(
            "unavailable access basis: " + ", ".join(sorted(evidence_bases))
        )

    fresh_candidates = [
        item
        for item in access_candidates
        if date.fromisoformat(item["freshness"]["published_at"]) <= as_of
    ]
    if not fresh_candidates:
        raise ContractError(f"no source was published by {as_of.isoformat()}")

    capability_candidates = [
        item
        for item in fresh_candidates
        if item["access"]["required_capability"] in available_capabilities
        and item["access"]["required_capability"] in declared_capabilities
    ]
    if not capability_candidates:
        missing = sorted(
            {
                item["access"]["required_capability"]
                for item in fresh_candidates
            }
        )
        raise ContractError("missing capability: " + ", ".join(missing))

    auth_candidates = [
        item
        for item in capability_candidates
        if not item["authentication"]["required"]
        or item["authentication"]["mode"] in available_authentication
    ]
    if not auth_candidates:
        missing = sorted(
            {
                item["authentication"]["mode"]
                for item in capability_candidates
                if item["authentication"]["required"]
            }
        )
        raise ContractError("unavailable authentication: " + ", ".join(missing))

    for unit in required_units:
        if not any(unit in item["units"] for item in auth_candidates):
            raise ContractError(f"incompatible unit: {unit}")

    ordered_candidates = sorted(
        auth_candidates,
        key=lambda item: (
            -len(
                required_joints
                & {_joint_key(joint) for joint in item["joints"]}
            ),
            -len(required_dimensions & set(item["dimensions"])),
            -date.fromisoformat(
                item["freshness"]["published_at"]
            ).toordinal(),
            item["adapter_id"],
        ),
    )
    selected: list[dict[str, Any]] | None = None
    required_unit_set = set(required_units)
    for size in range(1, len(ordered_candidates) + 1):
        for candidate_group in combinations(ordered_candidates, size):
            covered_units = set().union(
                *(set(item["units"]) for item in candidate_group)
            )
            if not required_unit_set.issubset(covered_units):
                continue
            covered_dimensions = set().union(
                *(set(item["dimensions"]) for item in candidate_group)
            )
            if not required_dimensions.issubset(covered_dimensions):
                continue
            covered_joints = set().union(
                *(
                    {_joint_key(joint) for joint in item["joints"]}
                    for item in candidate_group
                )
            )
            if required_joints.issubset(covered_joints):
                selected = list(candidate_group)
                break
        if selected is not None:
            break

    if selected is None:
        covered_dimensions = set().union(
            *(set(item["dimensions"]) for item in ordered_candidates)
        )
        missing_dimensions = sorted(required_dimensions - covered_dimensions)
        if missing_dimensions:
            raise ContractError(
                "missing required dimension: " + ", ".join(missing_dimensions)
            )
        covered_joints = set().union(
            *(
                {_joint_key(joint) for joint in item["joints"]}
                for item in ordered_candidates
            )
        )
        missing_joints = sorted(required_joints - covered_joints)
        if missing_joints:
            rendered = "; ".join(
                " + ".join(joint) for joint in missing_joints
            )
            raise ContractError("missing critical joint: " + rendered)
        raise ContractError(
            "no compatible source combination covers all requested units"
        )

    selections = []
    for item in selected:
        selections.append(
            {
                "adapter_id": item["adapter_id"],
                "implementation": item["implementation"],
                "programs": deepcopy(item["programs"]),
                "units": sorted(set(item["units"]) & set(required_units)),
                "matched_dimensions": sorted(
                    required_dimensions & set(item["dimensions"])
                ),
                "matched_joints": [
                    list(joint)
                    for joint in sorted(
                        required_joints
                        & {_joint_key(joint) for joint in item["joints"]}
                    )
                ],
                "matched_geographies": deepcopy(required_geographies),
                "access": deepcopy(item["access"]),
                "authentication": deepcopy(item["authentication"]),
                "freshness": deepcopy(item["freshness"]),
            }
        )
    return {
        "schema_version": PLAN_VERSION,
        "registry_version": SOURCE_REGISTRY_VERSION,
        "frame_request_id": frame_request["request_id"],
        "as_of": as_of.isoformat(),
        "selections": selections,
    }


def load_population_adapter(
    descriptor: dict[str, object],
) -> PopulationAdapter:
    validated = validate_source_registry(
        {
            "schema_version": SOURCE_REGISTRY_VERSION,
            "updated_at": "2000-01-01",
            "sources": [descriptor],
        }
    )
    expected_descriptor = validated["sources"][0]
    implementation = expected_descriptor["implementation"]
    module_name, class_name = implementation.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        adapter_class = getattr(module, class_name)
        adapter = adapter_class()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ContractError(
            f"population adapter implementation is unavailable: {implementation}"
        ) from exc
    if not isinstance(adapter, PopulationAdapter):
        raise ContractError(
            "population adapter implementation does not implement "
            f"PopulationAdapter: {implementation}"
        )
    try:
        actual_descriptor = adapter.descriptor()
    except Exception as exc:
        raise ContractError(
            f"population adapter descriptor is unavailable: {implementation}"
        ) from exc
    if actual_descriptor != expected_descriptor:
        raise ContractError(
            "population adapter descriptor does not exactly match registry: "
            f"{implementation}"
        )
    return adapter
