"""Honest document-only migration from validated v2 packages to Tier 1 v3."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Mapping, Sequence

from ..common import (
    ContractError,
    canonical_json_bytes,
    require_string,
    require_timestamp,
    sha256_json,
)
from .composition import build_composition_plan


SKILLS_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_package import (  # noqa: E402
    read_validated_package_archive,
)
from audience_lab.audience_research import (  # noqa: E402
    require_valid_audience_research_pair,
)
from audience_lab.audience_research_v3 import (  # noqa: E402
    COMPOSITION_PLAN_VERSION,
    POPULATION_FRAME_VERSION,
    RESEARCH_BRIEF_V3,
    SAVED_PANEL_V3,
    VALIDITY_PROFILE_VERSION,
    validate_audience_research_v3,
    validate_population_frame,
    validate_research_brief_v3,
    validate_saved_panel_v3,
    validate_validity_profile,
)


MIGRATION_PROVENANCE_VERSION = "audience-panel-v2-to-v3-migration-v1"
OUTPUT_FILENAMES = (
    "audience-research-brief-v3.json",
    "saved-audience-panel-v3.json",
    "panel-composition-plan.json",
    "panel-validity-profile.json",
    "migration-provenance.json",
)
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_CLAIM_BOUNDARY = (
    "Legacy v2 migration only; no population structure, prevalence, "
    "representativeness, calibration, or outcome claim is supported."
)
_NO_FRAME_LIMITATION = (
    "The validated v2 package contains no v3 population frame, source "
    "observations, denominator, or structural prevalence evidence."
)
_EVIDENCE_BASIS_BY_RESEARCH_MODE = {
    "public_research": "public",
    "crm_first_party": "first_party_aggregate",
    "hybrid_research": "hybrid",
    "provisional_no_research": "none",
}
_AUDIT_UNAVAILABLE_REASON = (
    "The legacy v2 package contains no Release B1 construction audit."
)


def _version_tuple(value: object, path: str) -> tuple[int, int, int]:
    text = require_string(value, path)
    if not _SEMVER.fullmatch(text):
        raise ContractError(
            f"{path} must be a semantic version in MAJOR.MINOR.PATCH form"
        )
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def _json_member(files: Mapping[str, bytes], name: str) -> dict[str, object]:
    try:
        value = json.loads(files[name].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"validated v2 package member {name} is unreadable") from exc
    if not isinstance(value, dict):
        raise ContractError(
            f"validated v2 package member {name} must contain an object"
        )
    return value


def _string_ids(values: object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        return []
    return sorted({
        value
        for value in values
        if isinstance(value, str) and value
    })


def _evidence_basis(
    brief: Mapping[str, object],
    panel: Mapping[str, object],
) -> str:
    research_mode = str(brief["research_mode"])
    persona_research = panel.get("persona_research")
    if not isinstance(persona_research, Mapping):
        raise ContractError(
            "validated v2 panel persona_research must be an object"
        )
    panel_mode = str(persona_research["mode"])
    if panel_mode != research_mode:
        raise ContractError(
            "validated v2 research modes do not match; migration cannot "
            "infer one evidence basis"
        )
    try:
        return _EVIDENCE_BASIS_BY_RESEARCH_MODE[research_mode]
    except KeyError as exc:
        raise ContractError(
            "cannot infer a v3 evidence basis from ambiguous v2 research "
            f"mode {research_mode}; rebuild with an explicit evidence route"
        ) from exc


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ContractError(f"{path} must be a finite positive number")
    return result


def _normalized(
    weighted: Sequence[tuple[str, float]],
    path: str,
) -> dict[str, float]:
    total = math.fsum(weight for _identifier, weight in weighted)
    if not math.isfinite(total) or total <= 0.0:
        raise ContractError(f"{path} must have a positive finite total")
    return {
        identifier: weight / total
        for identifier, weight in weighted
    }


def _support_ids(
    *values: object,
) -> list[str]:
    result: set[str] = set()
    for value in values:
        result.update(_string_ids(value))
    return sorted(result)


def _no_frame_result(
    *,
    panel: Mapping[str, object],
    new_panel_version: str,
    migrated_at: str,
    source_package_sha256: str,
) -> dict[str, object]:
    panel_id = str(panel["panel_id"])
    request_binding = {
        "schema_version": "audience-panel-v2-to-v3-no-frame-request-v1",
        "source_package_sha256": source_package_sha256,
        "panel_id": panel_id,
        "new_panel_version": new_panel_version,
        "population_frame": None,
    }
    result = {
        "schema_version": POPULATION_FRAME_VERSION,
        "frame_id": f"{panel_id}-migration-no-frame",
        "frame_version": new_panel_version,
        "built_at": migrated_at,
        "frame_request_id": f"{panel_id}-migration-request",
        "frame_request_sha256": sha256_json(request_binding),
        "target_universe": panel["audience_scope"]["audience"],
        "proxy_universes": [],
        "claim_boundary": _CLAIM_BOUNDARY,
        "units": [],
        "structural_dimensions": [
            "legacy-segment",
            "legacy-context",
        ],
        "cells": [],
        "margins": [],
        "joints": [],
        "source_bindings": [],
        "coverage_assessment": {
            "selection_statement": (
                "Migration preserves explicit v2 profiles without selecting "
                "a population-frame universe."
            ),
            "coverage_statement": (
                "No defensible population coverage is available from the "
                "v2 package."
            ),
            "known_gaps": [_NO_FRAME_LIMITATION],
        },
        "modeled_weight_by_dimension": [],
        "modeled_weight_share": 0.0,
        "eligibility": "no_defensible_frame",
        "downgrade_reason": "legacy-v2-package-has-no-population-frame",
    }
    try:
        return validate_population_frame(result)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _composition_inputs(
    panel: Mapping[str, object],
    *,
    evidence_basis: str,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    raw_segments = panel["segments"]
    raw_strata = panel["context_strata"]
    raw_profiles = panel["grounded_context_profiles"]
    raw_archetypes = panel["persona_archetypes"]
    if not all(
        isinstance(value, list)
        for value in (raw_segments, raw_strata, raw_profiles, raw_archetypes)
    ):
        raise ContractError("validated v2 panel collections must be arrays")

    segments = {
        str(segment["segment_id"]): segment
        for segment in raw_segments
        if isinstance(segment, Mapping)
    }
    strata = {
        str(stratum["context_stratum_id"]): stratum
        for stratum in raw_strata
        if isinstance(stratum, Mapping)
    }
    archetypes = {
        str(archetype["persona_archetype_id"]): archetype
        for archetype in raw_archetypes
        if isinstance(archetype, Mapping)
    }
    profiles = [
        profile
        for profile in raw_profiles
        if isinstance(profile, Mapping)
    ]
    profiles_by_segment: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    profiles_by_stratum: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for profile in profiles:
        profiles_by_segment[str(profile["segment_id"])].append(profile)
        profiles_by_stratum[str(profile["context_stratum_id"])].append(profile)
    missing_segments = sorted(set(segments) - set(profiles_by_segment))
    if missing_segments:
        raise ContractError(
            "every migrated segment must have at least one explicit grounded "
            "profile: " + ", ".join(missing_segments)
        )
    unused_strata = sorted(set(strata) - set(profiles_by_stratum))
    if unused_strata:
        raise ContractError(
            "every migrated context stratum must resolve to at least one "
            "existing grounded profile: " + ", ".join(unused_strata)
        )

    segment_weights = [
        (
            segment_id,
            _number(segment["study_weight"], f"segments[{segment_id}].study_weight"),
        )
        for segment_id, segment in sorted(segments.items())
    ]
    normalized_segments = _normalized(segment_weights, "segment weights")

    provisional = evidence_basis == "none"
    structural_findings: list[dict[str, object]] = []
    segment_weight_mappings: list[dict[str, object]] = []
    for segment_id, segment in sorted(segments.items()):
        finding_ids = (
            []
            if provisional
            else _support_ids(segment.get("finding_ids"))
        )
        evidence_ids = (
            []
            if provisional
            else _support_ids(
                segment.get("evidence_ids"),
                segment.get("weight_source_evidence"),
            )
        )
        original_weight = _number(
            segment["study_weight"],
            f"segments[{segment_id}].study_weight",
        )
        migrated_weight = normalized_segments[segment_id]
        structural_findings.append({
            "structural_group_id": segment_id,
            "cell_ids": [],
            "structural_finding_ids": finding_ids,
            "evidence_ids": evidence_ids,
            "must_cover": True,
            "planning_allocation": migrated_weight,
        })
        segment_weight_mappings.append({
            "segment_id": segment_id,
            "v2_study_weight": original_weight,
            "v3_planning_allocation": migrated_weight,
            "weight_semantic": "planning_allocation",
        })

    stratum_weights_by_segment: dict[str, dict[str, float]] = {}
    context_weight_mappings: list[dict[str, object]] = []
    for segment_id in sorted(segments):
        used_stratum_ids = sorted({
            str(profile["context_stratum_id"])
            for profile in profiles_by_segment[segment_id]
        })
        weighted_strata = [
            (
                stratum_id,
                _number(
                    strata[stratum_id]["planned_weight"],
                    f"context_strata[{stratum_id}].planned_weight",
                ),
            )
            for stratum_id in used_stratum_ids
        ]
        normalized_strata = _normalized(
            weighted_strata,
            f"context weights for {segment_id}",
        )
        stratum_weights_by_segment[segment_id] = normalized_strata
        for stratum_id, original_weight in weighted_strata:
            context_weight_mappings.append({
                "segment_id": segment_id,
                "context_stratum_id": stratum_id,
                "v2_planned_weight": original_weight,
                "v3_conditional_planning_allocation":
                    normalized_strata[stratum_id],
                "weight_semantic": "planning_allocation",
            })

    overlay_findings: list[dict[str, object]] = []
    profile_specs: list[dict[str, object]] = []
    profile_mappings: list[dict[str, object]] = []
    group_support = {
        str(item["structural_group_id"]): (
            set(item["structural_finding_ids"]),
            set(item["evidence_ids"]),
        )
        for item in structural_findings
    }
    for profile in sorted(
        profiles,
        key=lambda value: str(value["grounded_profile_id"]),
    ):
        profile_id = str(profile["grounded_profile_id"])
        segment_id = str(profile["segment_id"])
        stratum_id = str(profile["context_stratum_id"])
        archetype_id = str(profile["persona_archetype_id"])
        archetype = archetypes[archetype_id]
        provenance_rows = profile["context_attribute_provenance"]
        provenance_findings = [
            finding_id
            for row in provenance_rows
            if isinstance(row, Mapping)
            for finding_id in _string_ids(row.get("finding_ids"))
        ]
        provenance_evidence = [
            evidence_id
            for row in provenance_rows
            if isinstance(row, Mapping)
            for evidence_id in _string_ids(row.get("source_evidence"))
        ]
        overlay_finding_ids = (
            []
            if provisional
            else _support_ids(
                provenance_findings,
                archetype.get("finding_ids"),
            )
        )
        overlay_evidence_ids = (
            []
            if provisional
            else _support_ids(
                provenance_evidence,
                archetype.get("evidence_ids"),
            )
        )
        overlay_id = f"legacy-{profile_id}"
        overlay_findings.append({
            "overlay_id": overlay_id,
            "description": (
                f"Explicit v2 grounded profile {profile_id}; migration "
                "preserves identity without asserting prevalence."
            ),
            "allocation_basis": "experimental" if provisional else "estimated",
            "finding_ids": overlay_finding_ids,
            "evidence_ids": overlay_evidence_ids,
            "topic_bindings": (
                []
                if provisional
                else [{
                    "topic_id": "legacy-v2-context",
                    "evidence_ids": overlay_evidence_ids,
                }]
            ),
            "decision_relevance": "topic_bound",
        })
        group_findings, group_evidence = group_support[segment_id]
        conditional = (
            stratum_weights_by_segment[segment_id][stratum_id]
            / len(profiles_by_stratum[stratum_id])
        )
        profile_specs.append({
            "status": "provisional" if provisional else "supported",
            "profile_id": profile_id,
            "structural_group_id": segment_id,
            "overlay_ids": [overlay_id],
            "support_finding_ids": (
                []
                if provisional
                else sorted(group_findings | set(overlay_finding_ids))
            ),
            "support_evidence_ids": (
                []
                if provisional
                else sorted(group_evidence | set(overlay_evidence_ids))
            ),
            "conditional_overlay_allocation": conditional,
        })
        profile_mappings.append({
            "legacy_grounded_profile_id": profile_id,
            "migrated_profile_id": profile_id,
            "segment_id": segment_id,
            "persona_archetype_id": archetype_id,
            "context_stratum_id": stratum_id,
            "conditional_planning_allocation": conditional,
            "effective_planning_allocation":
                normalized_segments[segment_id] * conditional,
            "weight_semantic": "planning_allocation",
        })
    return (
        structural_findings,
        overlay_findings,
        profile_specs,
        segment_weight_mappings,
        context_weight_mappings,
        profile_mappings,
    )


def _audit_binding(
    source_package_sha256: str,
) -> dict[str, object]:
    return {
        "applicability": "legacy_v2_migration",
        "status": "not_available",
        "source_package_sha256": source_package_sha256,
        "reason": _AUDIT_UNAVAILABLE_REASON,
    }


_DARWIN_ROOT_ALIASES = (
    (Path("/var"), Path("/private/var")),
    (Path("/tmp"), Path("/private/tmp")),
)
_STAGE_PREFIX = ".audience-v3-migration-"


def _canonical_publication_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if sys.platform != "darwin":
        return absolute
    for alias, target in _DARWIN_ROOT_ALIASES:
        if absolute != alias and alias not in absolute.parents:
            continue
        try:
            alias_lstat = os.lstat(alias)
            alias_stat = os.stat(alias)
            target_stat = os.stat(target)
        except OSError:
            continue
        if (
            stat.S_ISLNK(alias_lstat.st_mode)
            and Path(os.path.realpath(alias)) == target
            and os.path.samestat(alias_stat, target_stat)
        ):
            return target / absolute.relative_to(alias)
    return absolute


def _require_no_symlink_components(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            candidate_stat = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContractError(
                f"could not inspect migration output path {candidate}: {exc}"
            ) from exc
        if stat.S_ISLNK(candidate_stat.st_mode):
            raise ContractError(
                "migration output path must not contain a symlink: "
                f"{candidate}"
            )


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    missing = [name for name in required if not hasattr(os, name)]
    if missing:
        raise ContractError(
            "descriptor-pinned migration publication is unavailable: "
            + ", ".join(missing)
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _open_pinned_parent(output_dir: Path) -> tuple[Path, int]:
    canonical = _canonical_publication_path(output_dir)
    if canonical == Path("/") or not canonical.name:
        raise ContractError("migration output directory must not be the root")
    _require_no_symlink_components(canonical)
    flags = _directory_flags()
    current_fd = os.open("/", flags)
    current_path = Path("/")
    try:
        for component in canonical.parent.parts[1:]:
            current_path /= component
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ContractError(
                        "could not create migration output parent "
                        f"{current_path}: {exc}"
                    ) from exc
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise ContractError(
                        "migration output parent must contain no symlink "
                        f"components: {current_path}: {exc}"
                    ) from exc
            except OSError as exc:
                raise ContractError(
                    "migration output parent must contain no symlink "
                    f"components: {current_path}: {exc}"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        parent_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ContractError(
                f"migration output parent is not a directory: {canonical.parent}"
            )
        _require_pinned_path(
            canonical.parent,
            current_fd,
            "migration output parent changed during publication setup",
        )
        return canonical, current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _require_pinned_path(path: Path, fd: int, message: str) -> None:
    try:
        _require_no_symlink_components(path)
        path_stat = os.stat(path, follow_symlinks=False)
        pinned_stat = os.fstat(fd)
    except (OSError, ValueError):
        raise ContractError(message) from None
    if not os.path.samestat(path_stat, pinned_stat):
        raise ContractError(message)


def _entry_matches(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
) -> bool:
    try:
        entry_stat = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return False
    return _identity(entry_stat) == expected


def _create_pinned_directory(
    parent_fd: int,
    name: str,
    *,
    collision_message: str | None = None,
) -> tuple[int, tuple[int, int]]:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError as exc:
        if collision_message is not None:
            raise ContractError(collision_message) from exc
        raise
    created_stat = os.stat(
        name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    if not stat.S_ISDIR(created_stat.st_mode):
        raise ContractError(
            "migration publication directory changed before it could be pinned"
        )
    created_identity = _identity(created_stat)
    try:
        directory_fd = os.open(
            name,
            _directory_flags(),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        _remove_matching_directory(parent_fd, name, created_identity)
        raise ContractError(
            "migration publication directory changed before it could be pinned"
        ) from exc
    pinned_stat = os.fstat(directory_fd)
    if not os.path.samestat(created_stat, pinned_stat):
        os.close(directory_fd)
        _remove_matching_directory(parent_fd, name, created_identity)
        raise ContractError(
            "migration publication directory changed before it could be pinned"
        )
    os.fchmod(directory_fd, 0o700)
    return directory_fd, created_identity


def _create_stage(parent_fd: int) -> tuple[str, int, tuple[int, int]]:
    for _attempt in range(128):
        name = _STAGE_PREFIX + secrets.token_hex(12)
        try:
            stage_fd, stage_identity = _create_pinned_directory(
                parent_fd,
                name,
            )
        except FileExistsError:
            continue
        return name, stage_fd, stage_identity
    raise ContractError("could not allocate a private migration staging directory")


def _write_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("migration staging write made no progress")
        view = view[written:]


def _write_staged_file(stage_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_fd = os.open(name, flags, 0o600, dir_fd=stage_fd)
    try:
        os.fchmod(file_fd, 0o600)
        _write_fd(file_fd, payload)
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _unlink_known_files(directory_fd: int) -> None:
    for filename in OUTPUT_FILENAMES:
        try:
            os.unlink(filename, dir_fd=directory_fd)
        except FileNotFoundError:
            continue
        except OSError:
            continue


def _remove_matching_directory(
    parent_fd: int,
    name: str,
    expected: tuple[int, int] | None,
) -> bool:
    if expected is None or not _entry_matches(parent_fd, name, expected):
        return False
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        return False
    return True


def _publication_still_matches(
    canonical_output: Path,
    parent_fd: int,
    parent_identity: tuple[int, int],
    output_fd: int,
    output_identity: tuple[int, int],
) -> bool:
    try:
        _require_no_symlink_components(canonical_output)
        parent_path_stat = os.stat(
            canonical_output.parent,
            follow_symlinks=False,
        )
        output_path_stat = os.stat(
            canonical_output,
            follow_symlinks=False,
        )
        pinned_parent_stat = os.fstat(parent_fd)
        pinned_output_stat = os.fstat(output_fd)
    except (OSError, ValueError):
        return False
    return (
        _identity(parent_path_stat) == parent_identity
        and _identity(pinned_parent_stat) == parent_identity
        and _identity(output_path_stat) == output_identity
        and _identity(pinned_output_stat) == output_identity
        and _entry_matches(
            parent_fd,
            canonical_output.name,
            output_identity,
        )
    )


def _write_outputs(
    output_dir: Path,
    documents: Mapping[str, object],
) -> None:
    payloads = {
        filename: canonical_json_bytes(documents[filename])
        for filename in OUTPUT_FILENAMES
    }
    canonical_output, parent_fd = _open_pinned_parent(output_dir)
    parent_identity = _identity(os.fstat(parent_fd))
    stage_name: str | None = None
    stage_fd: int | None = None
    stage_identity: tuple[int, int] | None = None
    output_fd: int | None = None
    output_identity: tuple[int, int] | None = None
    try:
        stage_name, stage_fd, stage_identity = _create_stage(parent_fd)
        for filename in OUTPUT_FILENAMES:
            _write_staged_file(stage_fd, filename, payloads[filename])
        os.fsync(stage_fd)

        collision_message = (
            "migration output directory already exists: "
            f"{canonical_output}. Choose a new path; existing outputs are "
            "never overwritten."
        )
        output_fd, output_identity = _create_pinned_directory(
            parent_fd,
            canonical_output.name,
            collision_message=collision_message,
        )
        for filename in OUTPUT_FILENAMES:
            os.rename(
                filename,
                filename,
                src_dir_fd=stage_fd,
                dst_dir_fd=output_fd,
            )
        os.fsync(output_fd)
        output_stat = os.fstat(output_fd)
        file_stats = [
            os.stat(
                filename,
                dir_fd=output_fd,
                follow_symlinks=False,
            )
            for filename in OUTPUT_FILENAMES
        ]
        if (
            set(os.listdir(output_fd)) != set(OUTPUT_FILENAMES)
            or stat.S_IMODE(output_stat.st_mode) != 0o700
            or any(
                not stat.S_ISREG(file_stat.st_mode)
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                for file_stat in file_stats
            )
        ):
            raise ContractError(
                "migration publication contents or permissions are invalid"
            )
        if not _publication_still_matches(
            canonical_output,
            parent_fd,
            parent_identity,
            output_fd,
            output_identity,
        ):
            raise ContractError(
                "migration publication path changed or was replaced"
            )
        if not _remove_matching_directory(
            parent_fd,
            stage_name,
            stage_identity,
        ):
            raise ContractError(
                "migration staging directory changed or could not be removed"
            )
        stage_identity = None
        os.fsync(parent_fd)
        if not _publication_still_matches(
            canonical_output,
            parent_fd,
            parent_identity,
            output_fd,
            output_identity,
        ):
            raise ContractError(
                "migration publication path changed or was replaced"
            )
    except BaseException:
        if output_fd is not None:
            _unlink_known_files(output_fd)
        if stage_fd is not None:
            _unlink_known_files(stage_fd)
        if output_identity is not None:
            _remove_matching_directory(
                parent_fd,
                canonical_output.name,
                output_identity,
            )
        if stage_name is not None:
            _remove_matching_directory(
                parent_fd,
                stage_name,
                stage_identity,
            )
        raise
    finally:
        if output_fd is not None:
            os.close(output_fd)
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(parent_fd)


def migrate_v2_to_v3(
    *,
    v2_package_path: Path,
    new_panel_version: str,
    migrated_at: str,
    migrated_by: str,
    output_dir: Path,
    now: datetime | None = None,
) -> dict[str, object]:
    """Migrate one exact v2 archive to five unpackaged, honest Tier 1 documents."""

    require_timestamp(migrated_at, "migrated_at")
    require_string(migrated_by, "migrated_by")
    try:
        snapshot = read_validated_package_archive(v2_package_path, now=now)
    except ValueError as exc:
        raise ContractError(f"v2 package validation failed: {exc}") from exc
    raw_package = snapshot["archive_bytes"]
    validation = snapshot["validation"]
    files = snapshot["members"]
    if (
        not isinstance(raw_package, bytes)
        or not isinstance(validation, Mapping)
        or not isinstance(files, Mapping)
    ):
        raise ContractError(
            "public package reader returned an invalid validated snapshot"
        )
    brief_v2 = _json_member(files, "persona-research-brief.json")
    panel_v2 = _json_member(files, "saved-audience-panel.json")

    old_version = str(panel_v2["version"])
    old_version_tuple = _version_tuple(old_version, "v2 panel version")
    new_version_tuple = _version_tuple(
        new_panel_version,
        "new_panel_version",
    )
    if new_version_tuple <= old_version_tuple:
        raise ContractError(
            "new_panel_version must be a newer semantic panel version than "
            f"the v2 package version {old_version}"
        )

    source_package_sha256 = (
        "sha256:" + hashlib.sha256(raw_package).hexdigest()
    )
    if source_package_sha256 != (
        "sha256:" + str(validation["package_zip_sha256"])
    ):
        raise ContractError("validated package SHA-256 does not bind the read bytes")

    migrated_brief_v2 = deepcopy(brief_v2)
    migrated_brief_v2["updated_at"] = migrated_at
    migrated_panel_v2 = deepcopy(panel_v2)
    migrated_panel_v2["version"] = new_panel_version
    migrated_panel_v2["updated_at"] = migrated_at
    for segment in migrated_panel_v2["segments"]:
        segment["weighting_rule"] = "planning_allocation"
    for stratum in migrated_panel_v2["context_strata"]:
        stratum["weighting_rule"] = "planning_allocation"
    try:
        require_valid_audience_research_pair(
            migrated_brief_v2,
            migrated_panel_v2,
            now=now,
        )
    except ValueError as exc:
        raise ContractError(
            f"migrated v2 projections are invalid: {exc}"
        ) from exc
    evidence_basis = _evidence_basis(
        migrated_brief_v2,
        migrated_panel_v2,
    )

    no_frame = _no_frame_result(
        panel=migrated_panel_v2,
        new_panel_version=new_panel_version,
        migrated_at=migrated_at,
        source_package_sha256=source_package_sha256,
    )
    (
        structural_inputs,
        overlay_inputs,
        profile_specs,
        segment_weight_mappings,
        context_weight_mappings,
        profile_mappings,
    ) = _composition_inputs(
        migrated_panel_v2,
        evidence_basis=evidence_basis,
    )
    composition = build_composition_plan(
        population_frame=no_frame,
        structural_findings=structural_inputs,
        overlay_findings=overlay_inputs,
        supported_profile_specs=profile_specs,
        requested_tier="tier_1",
        evidence_basis=evidence_basis,
        plan_id=f"{panel_v2['panel_id']}-migration-composition",
        plan_version=new_panel_version,
        built_at=migrated_at,
    )
    if composition["schema_version"] != COMPOSITION_PLAN_VERSION:
        raise ContractError("composition validator returned an unexpected schema")

    frame_result_sha256 = sha256_json(no_frame)
    structural_ids = sorted({
        identifier
        for group in structural_inputs
        for identifier in group["structural_finding_ids"]
    })
    overlay_ids = sorted({
        identifier
        for overlay in overlay_inputs
        for identifier in overlay["finding_ids"]
    })
    brief_v3 = {
        **migrated_brief_v2,
        "schema_version": RESEARCH_BRIEF_V3,
        "panel_tier": "tier_1",
        "evidence_basis": evidence_basis,
        "workflow_state_binding": "legacy-v2-migration",
        "population_frame_result_sha256": frame_result_sha256,
        "population_frame_sha256": None,
        "authorized_audience_import": None,
        "structural_findings": structural_ids,
        "overlay_findings": overlay_ids,
        "claim_boundary": _CLAIM_BOUNDARY,
        "dimensional_validity": [{
            "dimension": "population-structure",
            "status": "insufficient",
            "limitations": [_NO_FRAME_LIMITATION],
        }],
        "scoped_approvals": [{
            "scope": "legacy-v2-panel-creation",
            "status": "approved",
            "target_sha256": (
                "sha256:" + str(validation["brief_sha256"])
            ),
        }],
    }
    try:
        brief_v3 = validate_research_brief_v3(brief_v3)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc

    brief_projection_sha256 = sha256_json(migrated_brief_v2)
    panel_projection_sha256 = sha256_json(migrated_panel_v2)
    validity = {
        "schema_version": VALIDITY_PROFILE_VERSION,
        "validity_id": f"{panel_v2['panel_id']}-migration-validity",
        "binding_state": "panel_final",
        "panel_id": panel_v2["panel_id"],
        "panel_tier": "tier_1",
        "evidence_basis": evidence_basis,
        "axes": {
            "structural_frame": {
                "status": "insufficient",
                "coverage": 0.0,
                "limitations": [_NO_FRAME_LIMITATION],
            },
            "overlay_evidence": {
                "status": (
                    "not_available"
                    if evidence_basis == "none"
                    else "directional"
                ),
                "coverage": None,
                "limitations": [
                    (
                        "The v2 package contains no research sources."
                        if evidence_basis == "none"
                        else (
                            "Existing v2 research is preserved without "
                            "creating a structural prevalence claim."
                        )
                    )
                ],
            },
            "allocation_fidelity": {
                "status": "not_available",
                "coverage": None,
                "limitations": [
                    "Migrated weights are planning allocations, not observed prevalence."
                ],
            },
            "outcome_calibration": {
                "status": "not_available",
                "coverage": None,
                "limitations": [
                    "Migration creates no calibration or outcome evidence."
                ],
            },
            "external_validation": {
                "status": "not_available",
                "coverage": None,
                "limitations": [
                    "Migration creates no external-validation claim."
                ],
            },
        },
        "predeclared_validation_design": None,
        "held_out_outcome_evidence": [],
        "source_bindings": {
            "brief_sha256": brief_projection_sha256,
            "panel_sha256": panel_projection_sha256,
            "frame_result_sha256": frame_result_sha256,
            "frame_sha256": None,
            "composition_sha256": sha256_json(composition),
        },
    }
    try:
        validity = validate_validity_profile(validity)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc

    panel_v3 = {
        **migrated_panel_v2,
        "schema_version": SAVED_PANEL_V3,
        "panel_tier": "tier_1",
        "evidence_basis": evidence_basis,
        "brief_id": brief_v2["brief_id"],
        "population_frame_result_sha256": frame_result_sha256,
        "population_frame_sha256": None,
        "composition_plan_sha256": sha256_json(composition),
        "validity_profile_sha256": sha256_json(validity),
        "authorized_handoff_sha256": None,
        "audit_binding": _audit_binding(source_package_sha256),
        "claim_boundary": _CLAIM_BOUNDARY,
        "package_status": "unpackaged",
    }
    try:
        panel_v3 = validate_saved_panel_v3(panel_v3, now=now)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc

    try:
        (
            brief_v3,
            panel_v3,
            no_frame,
            composition,
            validity,
            workflow_state,
            construction_audit,
        ) = validate_audience_research_v3(
            brief_v3,
            panel_v3,
            frame=no_frame,
            composition=composition,
            validity=validity,
            workflow_state=None,
            construction_audit=None,
            now=now,
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if (
        brief_v3 is None
        or panel_v3 is None
        or no_frame is None
        or composition is None
        or validity is None
        or workflow_state is not None
        or construction_audit is not None
    ):
        raise ContractError(
            "full v3 migration validation returned an invalid migration shape"
        )

    limitations = [
        _NO_FRAME_LIMITATION,
        (
            "Segment and context weights are retained only as normalized "
            "planning allocations; they are not population weights."
        ),
        (
            "When multiple grounded profiles share one v2 context stratum, "
            "that stratum's planning allocation is split equally because v2 "
            "does not encode within-stratum profile prevalence."
        ),
        (
            "No Release B1 workflow approval, construction audit, report "
            "inputs, evidence ledger, finding support, synthesis matrix, or "
            "report manifest is created; the panel binds the migration-only "
            "audit-unavailability record to the exact source package."
        ),
        (
            "No v3 archive is created. Package generator 2.0.0 is owned by "
            "Release B2."
        ),
    ]
    if evidence_basis == "none":
        limitations.append("The v2 package contains no research sources.")
    provenance = {
        "schema_version": MIGRATION_PROVENANCE_VERSION,
        "migrated_at": migrated_at,
        "migrated_by": migrated_by,
        "source_package": {
            "schema_version": validation["schema_version"],
            "panel_id": validation["panel_id"],
            "panel_version": validation["panel_version"],
            "brief_id": validation["brief_id"],
            "package_sha256": source_package_sha256,
            "package_byte_count": len(raw_package),
            "package_manifest_sha256": (
                "sha256:" + str(validation["package_manifest_sha256"])
            ),
            "brief_sha256": "sha256:" + str(validation["brief_sha256"]),
            "panel_sha256": "sha256:" + str(validation["panel_sha256"]),
            "original_bytes_preserved": True,
        },
        "target": {
            "brief_schema_version": RESEARCH_BRIEF_V3,
            "panel_schema_version": SAVED_PANEL_V3,
            "composition_schema_version": COMPOSITION_PLAN_VERSION,
            "validity_schema_version": VALIDITY_PROFILE_VERSION,
            "panel_version": new_panel_version,
            "panel_tier": "tier_1",
            "evidence_basis": evidence_basis,
            "population_frame_sha256": None,
            "package_status": "unpackaged",
            "v3_archive_created": False,
            "document_sha256": {
                "audience-research-brief-v3.json": sha256_json(brief_v3),
                "saved-audience-panel-v3.json": sha256_json(panel_v3),
                "panel-composition-plan.json": sha256_json(composition),
                "panel-validity-profile.json": sha256_json(validity),
            },
        },
        "no_defensible_frame_result": no_frame,
        "segment_weight_mappings": segment_weight_mappings,
        "context_weight_mappings": context_weight_mappings,
        "profile_mappings": profile_mappings,
        "limitations": limitations,
    }
    documents = {
        "audience-research-brief-v3.json": brief_v3,
        "saved-audience-panel-v3.json": panel_v3,
        "panel-composition-plan.json": composition,
        "panel-validity-profile.json": validity,
        "migration-provenance.json": provenance,
    }
    _write_outputs(Path(output_dir), documents)
    return {
        "status": "migrated",
        "panel_id": panel_v2["panel_id"],
        "previous_panel_version": old_version,
        "new_panel_version": new_panel_version,
        "source_package_sha256": source_package_sha256,
        "output_dir": str(output_dir),
        "outputs": {
            filename: str(Path(output_dir) / filename)
            for filename in OUTPUT_FILENAMES
        },
    }


__all__ = [
    "MIGRATION_PROVENANCE_VERSION",
    "migrate_v2_to_v3",
]
