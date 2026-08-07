"""Deterministic study routing and synthetic-replicate capacity planning."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audience_library import (
    ImmutableVersionConflict,
    LibrarySafetyError,
    _parse_timestamp,
    validate_audience_intake,
)
from .audience_package_v3 import (
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
    read_v3_archive_members,
)
from .audience_resolution_v3 import resolve_audience_v3
from .contracts import SUPPORTED_CREATIVE_FORMATS


CONTEXT_PROVENANCE_STATUSES = frozenset({"observed", "estimated", "experimental"})
V3_RUN_ENVELOPE_VERSION = "audience-run-envelope-v3"
V3_RUN_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "resolved_at",
        "resolution_status",
        "resolution_reasons",
        "audience_package",
        "audience_lock",
        "context_strata",
        "grounded_context_profiles",
        "profile_weights",
        "allocation_constraints",
        "allocation_basis",
        "claim_boundary",
        "snapshot",
    }
)
V3_AUDIENCE_PACKAGE_KEYS = frozenset(
    {
        "schema_version",
        "generator_version",
        "package_manifest_sha256",
        "package_zip_sha256",
        "panel_id",
        "panel_version",
        "tier",
        "evidence_basis",
    }
)
_V3_ALLOCATION_BASES = frozenset({"directional_planning", "structural_frame"})


@dataclass(frozen=True)
class ContextDimension:
    """One supplied planning dimension and its evidence boundary."""

    name: str
    value: str
    status: str
    source_evidence: tuple[str, ...]
    finding_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "source_evidence": list(self.source_evidence),
        }
        if self.finding_ids:
            payload["finding_ids"] = list(self.finding_ids)
        return payload


@dataclass(frozen=True)
class ContextStratum:
    """A grounded context profile used only to plan replicate assignments."""

    context_stratum_id: str
    segment_id: str
    planned_weight: float
    weighting_rule: str
    dimensions: tuple[ContextDimension, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_stratum_id": self.context_stratum_id,
            "segment_id": self.segment_id,
            "planned_weight": self.planned_weight,
            "weighting_rule": self.weighting_rule,
            "dimensions": [dimension.as_dict() for dimension in self.dimensions],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ContextStratum":
        if not isinstance(payload, Mapping):
            raise ValueError("context strata must be objects")
        context_stratum_id = _non_empty_string(
            "context_stratum_id", payload.get("context_stratum_id")
        )
        segment_id = _non_empty_string("segment_id", payload.get("segment_id"))
        weighting_rule = _non_empty_string(
            "weighting_rule", payload.get("weighting_rule")
        )
        planned_weight = payload.get("planned_weight")
        if (
            not isinstance(planned_weight, (int, float))
            or isinstance(planned_weight, bool)
            or not math.isfinite(planned_weight)
            or planned_weight <= 0
        ):
            raise ValueError("planned_weight must be a finite positive number")

        raw_dimensions = payload.get("dimensions")
        if (
            not isinstance(raw_dimensions, Sequence)
            or isinstance(raw_dimensions, (str, bytes))
            or not raw_dimensions
        ):
            raise ValueError("context stratum dimensions must be a non-empty array")
        dimensions: list[ContextDimension] = []
        for raw_dimension in raw_dimensions:
            if not isinstance(raw_dimension, Mapping):
                raise ValueError("context dimensions must be objects")
            name = _non_empty_string("context dimension name", raw_dimension.get("name"))
            value = _non_empty_string("context dimension value", raw_dimension.get("value"))
            status = raw_dimension.get("status")
            if status not in CONTEXT_PROVENANCE_STATUSES:
                raise ValueError(
                    "context dimension status must be observed, estimated, or experimental"
                )
            raw_evidence = raw_dimension.get("source_evidence")
            if (
                not isinstance(raw_evidence, Sequence)
                or isinstance(raw_evidence, (str, bytes))
                or (not raw_evidence and status != "experimental")
                or not all(
                    isinstance(evidence, str) and evidence.strip()
                    for evidence in raw_evidence
                )
            ):
                raise ValueError(
                    "context dimension source_evidence must be an array of strings and may be empty only for experimental dimensions"
                )
            dimensions.append(
                ContextDimension(
                    name=name,
                    value=value,
                    status=status,
                    source_evidence=tuple(raw_evidence),
                    finding_ids=tuple(
                        _string_array(
                            "context dimension finding_ids",
                            raw_dimension.get("finding_ids", ()),
                            allow_empty=True,
                        )
                    ),
                )
            )
        dimension_names = [dimension.name for dimension in dimensions]
        if len(set(dimension_names)) != len(dimension_names):
            raise ValueError("context dimension names must be unique within a stratum")
        return cls(
            context_stratum_id=context_stratum_id,
            segment_id=segment_id,
            planned_weight=float(planned_weight),
            weighting_rule=weighting_rule,
            dimensions=tuple(dimensions),
        )


@dataclass(frozen=True)
class StudyRequest:
    """The planner inputs loaded from a public study-request document."""

    study_id: str
    creative_ids: tuple[str, ...]
    creative_format: str
    requested_shortlist_size: int
    maximum_synthetic_panelists: int
    context_strata: tuple[ContextStratum, ...] = ()
    audience_intake: Mapping[str, Any] | None = None

    @property
    def audience_route(self) -> str | None:
        return self.audience_intake.get("route") if self.audience_intake else None

    @property
    def creative_count(self) -> int:
        return len(self.creative_ids)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StudyRequest":
        """Validate and normalize a JSON study request."""

        study_id = payload.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("study_id must be a non-empty string")

        creative_ids = payload.get("creative_ids")
        if not isinstance(creative_ids, Sequence) or isinstance(creative_ids, (str, bytes)):
            raise ValueError("creative_ids must be an array of strings")
        if not all(isinstance(creative_id, str) and creative_id.strip() for creative_id in creative_ids):
            raise ValueError("creative_ids must contain only non-empty strings")
        normalized_ids = tuple(creative_ids)
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("creative_ids must be unique")
        if not 2 <= len(normalized_ids) <= 100:
            raise ValueError("creative_ids must contain between 2 and 100 creatives")

        creative_format = payload.get("creative_format")
        if not isinstance(creative_format, str) or creative_format not in SUPPORTED_CREATIVE_FORMATS:
            raise ValueError("creative_format must name exactly one supported format")

        requested_shortlist_size = _require_int(
            "requested_shortlist_size", payload.get("requested_shortlist_size")
        )
        # Complete-exposure studies may legitimately narrow three to six
        # creatives to two finalists. Larger partial-exposure libraries retain
        # the three-finalist minimum used by the calibrated recovery bands.
        minimum_shortlist_size = 2 if len(normalized_ids) <= 6 else 3
        if not minimum_shortlist_size <= requested_shortlist_size <= 6:
            raise ValueError(
                "requested_shortlist_size must be between "
                f"{minimum_shortlist_size} and 6"
            )
        if requested_shortlist_size > len(normalized_ids):
            raise ValueError("requested_shortlist_size cannot exceed creative count")

        maximum_synthetic_panelists = _require_int(
            "maximum_synthetic_panelists", payload.get("maximum_synthetic_panelists")
        )
        if maximum_synthetic_panelists < 0:
            raise ValueError("maximum_synthetic_panelists must be non-negative")

        audience_keys = {
            key for key in ("target_audience", "audience_panel", "provisional_audience")
            if key in payload
        }
        audience_intake = (
            validate_audience_intake({key: payload[key] for key in audience_keys})
            if audience_keys else None
        )
        raw_context_strata = payload.get("context_strata", ())
        if audience_intake is not None and raw_context_strata:
            raise ValueError(
                "v2 audience intake must use context strata from a resolved audience snapshot"
            )
        if not isinstance(raw_context_strata, Sequence) or isinstance(
            raw_context_strata, (str, bytes)
        ):
            raise ValueError("context_strata must be an array")
        context_strata = tuple(
            ContextStratum.from_mapping(raw_stratum)
            for raw_stratum in raw_context_strata
        )
        stratum_keys = [
            (stratum.segment_id, stratum.context_stratum_id)
            for stratum in context_strata
        ]
        if len(set(stratum_keys)) != len(stratum_keys):
            raise ValueError("context_stratum_id values must be unique within each segment")

        return cls(
            study_id=study_id,
            creative_ids=normalized_ids,
            creative_format=creative_format,
            requested_shortlist_size=requested_shortlist_size,
            maximum_synthetic_panelists=maximum_synthetic_panelists,
            context_strata=context_strata,
            audience_intake=audience_intake,
        )


def validate_v3_run_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the strict Task 2 envelope fields consumed by planning."""

    if not isinstance(payload, Mapping):
        raise ValueError("v3 audience resolution must be an object")
    if set(payload) != V3_RUN_ENVELOPE_KEYS:
        raise ValueError("v3 audience resolution keys do not match the allowlist")
    if payload.get("schema_version") != V3_RUN_ENVELOPE_VERSION:
        raise ValueError("v3 audience resolution schema_version is unsupported")
    resolved_at = payload.get("resolved_at")
    parsed_resolved_at = _parse_timestamp(resolved_at, "v3 resolved_at")
    if resolved_at != parsed_resolved_at.isoformat().replace("+00:00", "Z"):
        raise ValueError("v3 resolved_at must be canonical UTC")
    if payload.get("resolution_status") != "ready":
        raise ValueError("v3 audience resolution must be ready before planning")
    reasons = payload.get("resolution_reasons")
    if not isinstance(reasons, list) or reasons:
        raise ValueError("ready v3 audience resolution must have no resolution reasons")
    package = payload.get("audience_package")
    if not isinstance(package, Mapping) or set(package) != V3_AUDIENCE_PACKAGE_KEYS:
        raise ValueError("v3 audience_package keys do not match the allowlist")
    if package.get("schema_version") != "audience-panel-package-v3":
        raise ValueError("v3 audience_package schema_version is unsupported")
    for key in ("package_manifest_sha256", "package_zip_sha256"):
        value = package.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"v3 audience_package.{key} must be a SHA-256 digest")
    for key in ("panel_id", "panel_version", "tier", "evidence_basis"):
        _non_empty_string(f"v3 audience_package.{key}", package.get(key))

    audience_lock = payload.get("audience_lock")
    if not isinstance(audience_lock, Mapping):
        raise ValueError("v3 audience_lock must be an object")
    segment_weights = audience_lock.get("segment_weights")
    if not isinstance(segment_weights, Mapping) or not segment_weights:
        raise ValueError("v3 audience_lock.segment_weights must be a non-empty object")
    normalized_weight_total = 0.0
    for segment_id, weight in segment_weights.items():
        _non_empty_string("v3 segment ID", segment_id)
        if (
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError("v3 segment weights must be finite positive numbers")
        normalized_weight_total += float(weight)
    if not math.isclose(normalized_weight_total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("v3 segment weights must sum to one")

    raw_strata = payload.get("context_strata")
    if (
        not isinstance(raw_strata, Sequence)
        or isinstance(raw_strata, (str, bytes))
        or not raw_strata
    ):
        raise ValueError("v3 context_strata must be a non-empty array")
    strata = tuple(ContextStratum.from_mapping(item) for item in raw_strata)
    if {stratum.segment_id for stratum in strata} != set(segment_weights):
        raise ValueError("v3 context strata must exactly cover locked segments")

    raw_profiles = payload.get("grounded_context_profiles")
    if (
        not isinstance(raw_profiles, Sequence)
        or isinstance(raw_profiles, (str, bytes))
        or not raw_profiles
    ):
        raise ValueError("v3 grounded_context_profiles must be a non-empty array")
    profile_ids: list[str] = []
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, Mapping):
            raise ValueError(f"v3 grounded_context_profiles[{index}] must be an object")
        profile_id = _non_empty_string(
            "v3 grounded_profile_id", profile.get("grounded_profile_id")
        )
        profile_ids.append(profile_id)
        segment_id = _non_empty_string(
            "v3 reported_segment_id", profile.get("reported_segment_id")
        )
        if segment_id not in segment_weights:
            raise ValueError("v3 profile references an unknown reported segment")
        _non_empty_string(
            "v3 structural_group_id", profile.get("structural_group_id")
        )
        for key in ("effective_weight", "conditional_overlay_allocation"):
            value = profile.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"v3 profile {key} must be finite and nonnegative")
        must_cover = profile.get("must_cover_group_ids")
        if (
            not isinstance(must_cover, Sequence)
            or isinstance(must_cover, (str, bytes))
            or not all(
                isinstance(group_id, str) and group_id.strip()
                for group_id in must_cover
            )
            or len(set(must_cover)) != len(must_cover)
        ):
            raise ValueError("v3 profile must-cover groups must be a unique array")
        snapshot_hash = profile.get("profile_snapshot_sha256")
        if (
            not isinstance(snapshot_hash, str)
            or not snapshot_hash.startswith("sha256:")
            or len(snapshot_hash) != 71
            or any(
                character not in "0123456789abcdef"
                for character in snapshot_hash[7:]
            )
        ):
            raise ValueError("v3 profile snapshot hash is invalid")
        if not isinstance(profile.get("eligible"), bool):
            raise ValueError("v3 profile eligible must be a boolean")
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("v3 grounded profile IDs must be unique")

    raw_weights = payload.get("profile_weights")
    if (
        not isinstance(raw_weights, Sequence)
        or isinstance(raw_weights, (str, bytes))
        or not all(isinstance(item, Mapping) for item in raw_weights)
    ):
        raise ValueError("v3 profile_weights must be an array of objects")
    weight_ids = [
        item.get("grounded_profile_id")
        for item in raw_weights
    ]
    if sorted(weight_ids) != sorted(profile_ids):
        raise ValueError("v3 profile weights must exactly cover grounded profiles")
    basis = payload.get("allocation_basis")
    if basis not in _V3_ALLOCATION_BASES:
        raise ValueError("v3 allocation_basis is unsupported")
    constraints = payload.get("allocation_constraints")
    if not isinstance(constraints, list):
        raise ValueError("v3 allocation_constraints must be an array")
    _non_empty_string("v3 claim_boundary", payload.get("claim_boundary"))
    snapshot = payload.get("snapshot")
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("package_sha256") != package.get("package_zip_sha256")
    ):
        raise ValueError("v3 snapshot must bind the audience package")
    return copy.deepcopy(dict(payload))


def load_reusable_v3_audience_resolution(
    path: Path | str,
) -> tuple[dict[str, Any], bytes]:
    """Revalidate one canonical v3 envelope against its immutable run unit."""

    resolution_path = Path(path).expanduser()
    if not resolution_path.is_absolute():
        raise ValueError("v3 audience resolution path must be absolute")
    if (
        resolution_path.name != "resolution.json"
        or resolution_path.parent.name != "audience"
        or resolution_path.is_symlink()
        or not resolution_path.is_file()
    ):
        raise ValueError(
            "v3 audience resolution must be a real canonical audience/resolution.json file"
        )
    snapshot = resolution_path.parent / "snapshot"
    package_path = snapshot / "audience-panel-package.zip"
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or package_path.is_symlink()
        or not package_path.is_file()
    ):
        raise ValueError("v3 audience snapshot is missing or unsafe")
    try:
        _raw, manifest_bytes = read_v3_archive_manifest(package_path)
        members = read_v3_archive_members(
            package_path,
            allowed_files=archive_files_v3_for_manifest(
                json.loads(manifest_bytes.decode("utf-8"))
            ),
        )
        panel = json.loads(
            members["saved-audience-panel.json"].decode("utf-8")
        )
    except (KeyError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("v3 audience snapshot package is invalid") from exc
    if not isinstance(panel, Mapping):
        raise ValueError("v3 audience snapshot panel must be an object")
    audience_scope = panel.get("audience_scope")
    scope_keys = {
        "audience",
        "market",
        "geography",
        "category",
        "buying_context",
        "exclusions",
    }
    if not isinstance(audience_scope, Mapping) or not scope_keys.issubset(
        audience_scope
    ):
        raise ValueError("v3 audience snapshot scope is invalid")
    study_scope = {
        key: copy.deepcopy(audience_scope[key])
        for key in scope_keys
    }
    try:
        envelope = resolve_audience_v3(
            package_path=package_path,
            study_scope=study_scope,
            run_directory=resolution_path.parent.parent,
        )
    except (ImmutableVersionConflict, LibrarySafetyError) as exc:
        raise ValueError(
            "v3 audience resolution does not match its immutable snapshot"
        ) from exc
    validated = validate_v3_run_envelope(envelope)
    return validated, resolution_path.read_bytes()


def v3_allocation_profiles(
    envelope: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Project Task 2 joined profiles into Task 3's exact request shape."""

    validated = validate_v3_run_envelope(envelope)
    return [
        {
            "grounded_profile_id": profile["grounded_profile_id"],
            "reported_segment_id": profile["reported_segment_id"],
            "structural_group_id": profile["structural_group_id"],
            "effective_weight": profile["effective_weight"],
            "conditional_effective_weight": profile["effective_weight"],
            "must_cover_group_ids": copy.deepcopy(
                profile["must_cover_group_ids"]
            ),
            "profile_snapshot_sha256": profile["profile_snapshot_sha256"],
            "eligible": profile["eligible"],
        }
        for profile in validated["grounded_context_profiles"]
    ]


def _string_array(field: str, value: Any, *, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or (not value and not allow_empty)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        qualifier = "" if allow_empty else " non-empty"
        raise ValueError(f"{field} must be a{qualifier} array of strings")
    return list(value)


@dataclass(frozen=True)
class CapacityPlan:
    """Separate binding reserves under the user-controlled replicate ceiling."""

    screening_planned: int
    boundary_reserved: int
    finalist_reserved: int
    required_total: int
    ceiling: int
    ceiling_satisfied: bool

    @property
    def shortfall(self) -> int:
        return max(0, self.required_total - self.ceiling)


def choose_method(creative_count: int, burden_pilot_passed: bool) -> str:
    """Route a supported library without weakening a failed burden pilot."""

    creative_count = _require_int("creative_count", creative_count)
    if not 2 <= creative_count <= 100:
        raise ValueError("creative_count must be between 2 and 100")
    if not isinstance(burden_pilot_passed, bool):
        raise ValueError("burden_pilot_passed must be a boolean")
    if not burden_pilot_passed:
        return "split_required"
    return "complete_exposure" if creative_count <= 6 else "partial_exposure_maxdiff"


def minimum_screening_jobs(creative_count: int, reported_segments: int = 1) -> int:
    """Plan nine participations per creative within every reported segment."""

    creative_count = _require_int("creative_count", creative_count)
    reported_segments = _require_int("reported_segments", reported_segments)
    if not 2 <= creative_count <= 100:
        raise ValueError("creative_count must be between 2 and 100")
    if reported_segments < 1:
        raise ValueError("reported_segments must be at least 1")
    return math.ceil(9 * creative_count / 4) * reported_segments


def resolve_reported_segment_ids(
    context_strata: Sequence[ContextStratum], reported_segments: int
) -> tuple[str, ...]:
    """Resolve the exact named segment roster used by deterministic planning.

    Grounded strata are authoritative when supplied.  The legacy ``segment-N``
    fallback exists only for requests that contain no strata at all.
    """

    reported_segments = _require_int("reported_segments", reported_segments)
    if reported_segments < 1:
        raise ValueError("reported_segments must be at least 1")
    if context_strata:
        segment_ids = tuple(sorted({stratum.segment_id for stratum in context_strata}))
        if len(segment_ids) != reported_segments:
            raise ValueError(
                "reported_segments must exactly match the named segment coverage "
                f"in context_strata (reported={reported_segments}, "
                f"covered={len(segment_ids)}: {', '.join(segment_ids)})"
            )
        return segment_ids
    return tuple(f"segment-{index}" for index in range(1, reported_segments + 1))


def reserve_capacity(
    ceiling: int,
    screening_planned: int,
    boundary_jobs_per_wave: int,
    boundary_waves_max: int,
    finalist_reserved: int,
) -> CapacityPlan:
    """Reserve screening, boundary, and finalist capacity without substitution."""

    named_values = {
        "ceiling": ceiling,
        "screening_planned": screening_planned,
        "boundary_jobs_per_wave": boundary_jobs_per_wave,
        "boundary_waves_max": boundary_waves_max,
        "finalist_reserved": finalist_reserved,
    }
    for name, value in named_values.items():
        normalized = _require_int(name, value)
        if normalized < 0:
            raise ValueError("capacity inputs must be non-negative")

    boundary_reserved = boundary_jobs_per_wave * boundary_waves_max
    required_total = screening_planned + boundary_reserved + finalist_reserved
    return CapacityPlan(
        screening_planned=screening_planned,
        boundary_reserved=boundary_reserved,
        finalist_reserved=finalist_reserved,
        required_total=required_total,
        ceiling=ceiling,
        ceiling_satisfied=required_total <= ceiling,
    )


def _require_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _non_empty_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


__all__ = [
    "CapacityPlan",
    "ContextDimension",
    "ContextStratum",
    "StudyRequest",
    "choose_method",
    "load_reusable_v3_audience_resolution",
    "minimum_screening_jobs",
    "resolve_reported_segment_ids",
    "reserve_capacity",
    "validate_v3_run_envelope",
    "v3_allocation_profiles",
]
