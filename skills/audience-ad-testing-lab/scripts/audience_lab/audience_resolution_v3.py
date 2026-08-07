"""Resolve one validated v3 audience package into an immutable run envelope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from .audience_library import (
    ImmutableVersionConflict,
    LibrarySafetyError,
    SNAPSHOT_RELATIVE_PATH,
    _atomic_write,
    _audience_lock,
    _canonical_json,
    _normalized_scope_text,
    _parse_timestamp,
    _private_mkdir,
    _reason,
    _reject_symlink_components,
    _require_string_array,
    _validate_scope_input,
    copy_json,
)
from .audience_package import (
    PackageSafetyError,
    PackageValidationError,
)
from .audience_package_dispatch import validate_supported_audience_package
from .audience_package_v3 import (
    ARCHIVE_FILES_V3,
    AUTHORIZED_RUNTIME_AUTHORITY_MEMBER,
    LEGACY_MIGRATION_ARCHIVE_FILES_V3,
    GENERATOR_VERSION_V3,
    PACKAGE_SCHEMA_VERSION_V3,
    TIER3_ARCHIVE_FILES_V3,
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
    read_v3_archive_members,
)
from .audience_research_v3 import (
    validate_v3_runtime_authority,
    validate_v3_runtime_permission_policy,
)


RUN_ENVELOPE_VERSION = "audience-run-envelope-v3"
RESOLUTION_AUTHORITY_VERSION = "audience-resolution-authority-v1"
_RESOLUTION_AUTHORITY_KEYS = {
    "schema_version",
    "resolved_at",
    "package_zip_sha256",
    "study_scope_sha256",
    "explicit_refresh_triggers_sha256",
}
_ENVELOPE_KEYS = {
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
_JSON_MEMBERS = {
    "manifest": "package-manifest.json",
    "brief": "audience-research-brief.json",
    "panel": "saved-audience-panel.json",
    "frame": "audience-population-frame.json",
    "composition": "panel-composition-plan.json",
    "workflow": "panel-workflow-state.json",
    "audit": "panel-construction-audit.json",
}
_SYNTHETIC_AD_TESTING_ALLOWED_USES = frozenset(
    {
        "Synthetic ad testing",
        (
            "Directional synthetic ad testing under the named public proxy "
            "boundary"
        ),
        "Synthetic ad testing for the exact authorized aggregate cohort",
    }
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_resolution_clock(value: datetime | None) -> datetime:
    clock = datetime.now(timezone.utc) if value is None else value
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise ValueError("v3 resolution clock must be a timezone-aware datetime")
    return clock.astimezone(timezone.utc)


def _resolution_authority(
    *,
    resolved_at: str,
    raw: bytes,
    study_scope: Mapping[str, object],
    explicit_refresh_triggers: list[str],
) -> dict[str, object]:
    return {
        "schema_version": RESOLUTION_AUTHORITY_VERSION,
        "resolved_at": resolved_at,
        "package_zip_sha256": "sha256:" + _sha256(raw),
        "study_scope_sha256": "sha256:"
        + _sha256(_canonical_json(study_scope)),
        "explicit_refresh_triggers_sha256": "sha256:"
        + _sha256(_canonical_json(explicit_refresh_triggers)),
    }


def _bound_resolution_authority(
    run_directory: Path,
    requested: datetime | None,
    *,
    raw: bytes,
    study_scope: Mapping[str, object],
    explicit_refresh_triggers: list[str],
) -> tuple[datetime, dict[str, object]]:
    audience = Path(run_directory).expanduser() / "audience"
    resolution = audience / "resolution.json"
    authority_path = audience / "resolution-authority.json"
    authority_exists = authority_path.exists() or authority_path.is_symlink()
    resolution_exists = resolution.exists() or resolution.is_symlink()
    if not authority_exists and not resolution_exists:
        clock = _canonical_resolution_clock(requested)
        resolved_at = clock.isoformat().replace("+00:00", "Z")
        return clock, _resolution_authority(
            resolved_at=resolved_at,
            raw=raw,
            study_scope=study_scope,
            explicit_refresh_triggers=explicit_refresh_triggers,
        )
    if authority_exists != resolution_exists:
        raise ImmutableVersionConflict(
            "run audience resolution authority is partial"
        )
    if (
        authority_path.is_symlink()
        or not authority_path.is_file()
        or resolution.is_symlink()
        or not resolution.is_file()
    ):
        raise ImmutableVersionConflict(
            "run audience resolution timestamp authority is unsafe"
        )
    try:
        payload = json.loads(authority_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != _RESOLUTION_AUTHORITY_KEYS
            or payload.get("schema_version") != RESOLUTION_AUTHORITY_VERSION
            or authority_path.read_bytes() != _canonical_json(payload)
        ):
            raise ValueError("authority contract mismatch")
        resolved_at = payload["resolved_at"]
        clock = _parse_timestamp(resolved_at, "resolved_at")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ImmutableVersionConflict(
            "run audience resolution timestamp authority is invalid"
        ) from exc
    canonical = clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if resolved_at != canonical:
        raise ImmutableVersionConflict(
            "run audience resolution timestamp is not canonical UTC"
        )
    expected = _resolution_authority(
        resolved_at=resolved_at,
        raw=raw,
        study_scope=study_scope,
        explicit_refresh_triggers=explicit_refresh_triggers,
    )
    if payload != expected:
        raise ImmutableVersionConflict(
            "run audience resolution authority does not match the immutable inputs"
        )
    return clock.astimezone(timezone.utc), expected


def _validated_snapshot(
    raw: bytes,
) -> tuple[bytes, dict[str, object], dict[str, bytes], dict[str, object]]:
    validation = validate_supported_audience_package(raw)
    if validation.get("schema_version") != PACKAGE_SCHEMA_VERSION_V3:
        raise PackageValidationError("v3 resolution requires an audience-panel-package-v3 archive")
    try:
        _snapshot, manifest_bytes = read_v3_archive_manifest(raw)
        manifest_value = json.loads(manifest_bytes.decode("utf-8"))
        members = read_v3_archive_members(
            raw,
            allowed_files=archive_files_v3_for_manifest(manifest_value),
        )
        documents = {
            key: json.loads(members[name].decode("utf-8"))
            for key, name in _JSON_MEMBERS.items()
        }
        if AUTHORIZED_RUNTIME_AUTHORITY_MEMBER in members:
            documents["authorized_runtime_authority"] = json.loads(
                members[AUTHORIZED_RUNTIME_AUTHORITY_MEMBER].decode(
                    "utf-8"
                )
            )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError("validated v3 package documents are unreadable") from exc
    if not all(
        isinstance(value, dict)
        for key, value in documents.items()
        if key not in {"workflow", "audit"}
    ) or any(
        value is not None and not isinstance(value, dict)
        for key, value in documents.items()
        if key in {"workflow", "audit"}
    ):
        raise PackageValidationError("validated v3 package documents must be objects")
    try:
        validate_v3_runtime_permission_policy(
            documents["brief"],
            documents["panel"],
            documents["frame"],
        )
        if documents["brief"]["panel_tier"] == "tier_3":
            authority = documents.get("authorized_runtime_authority")
            if not isinstance(authority, Mapping):
                raise ValueError(
                    "Tier 3 package is missing runtime authority"
                )
            validate_v3_runtime_authority(
                documents["brief"],
                documents["panel"],
                documents["frame"],
                authority,
            )
    except ValueError as exc:
        raise PackageValidationError(
            f"v3 runtime permission policy is invalid: {exc}"
        ) from exc
    return raw, validation, members, documents


def _safe_invalid_context(
    raw: bytes,
) -> tuple[dict[str, object], dict[str, bytes], dict[str, dict[str, object]]]:
    _snapshot, manifest_bytes = read_v3_archive_manifest(raw)
    try:
        manifest_value = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        manifest_value = {}
    manifest = manifest_value if isinstance(manifest_value, dict) else {}
    members: dict[str, bytes] = {"package-manifest.json": manifest_bytes}
    for allowlist in (
        ARCHIVE_FILES_V3,
        TIER3_ARCHIVE_FILES_V3,
        LEGACY_MIGRATION_ARCHIVE_FILES_V3,
    ):
        try:
            members = read_v3_archive_members(
                raw,
                allowed_files=allowlist,
            )
            break
        except PackageSafetyError:
            continue
    documents: dict[str, dict[str, object]] = {}
    for key, name in _JSON_MEMBERS.items():
        if name not in members:
            continue
        try:
            value = json.loads(members[name].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            documents[key] = value
    return manifest, members, documents


def _placeholder_audience_lock(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    return {
        "persona_research_brief_id": None,
        "panel_id": manifest.get("panel_id"),
        "panel_version": manifest.get("panel_version"),
        "segment_weights": {},
        "segment_names": {},
        "archetype_names": {},
        "segment_weight_provenance": [],
        "unique_archetypes": 0,
        "unique_grounded_context_profiles": 0,
        "attribute_provenance": [],
    }


def _invalid_reason(
    manifest: Mapping[str, object],
    documents: Mapping[str, Mapping[str, object]],
    error: PackageValidationError,
) -> dict[str, object]:
    if (
        manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION_V3
        or manifest.get("generator_version") != GENERATOR_VERSION_V3
    ):
        return _reason(
            "unsupported_package",
            "audience_package",
            {
                "schema_version": PACKAGE_SCHEMA_VERSION_V3,
                "generator_version": GENERATOR_VERSION_V3,
            },
            {
                "schema_version": manifest.get("schema_version"),
                "generator_version": manifest.get("generator_version"),
            },
            "The audience package schema or generator is unsupported.",
        )
    panel = documents.get("panel", {})
    composition = documents.get("composition", {})
    saved_records = panel.get("grounded_context_profiles", [])
    planned_records = composition.get("profiles", [])
    saved_ids = [
        item.get("grounded_profile_id")
        for item in saved_records
        if isinstance(item, Mapping)
    ] if isinstance(saved_records, list) else []
    planned_ids = [
        item.get("profile_id")
        for item in planned_records
        if isinstance(item, Mapping)
    ] if isinstance(planned_records, list) else []
    if any(
        not isinstance(identity, str) or not identity
        for identity in (*saved_ids, *planned_ids)
    ):
        return _reason(
            "invalid_profile_identity",
            "grounded_context_profiles",
            "non-empty string profile identities",
            {"saved_panel": saved_ids, "composition": planned_ids},
            "Every saved-panel and composition profile identity must be a non-empty string.",
        )
    if (
        len(saved_ids) != len(set(saved_ids))
        or len(planned_ids) != len(set(planned_ids))
    ):
        return _reason(
            "duplicate_profile_id",
            "grounded_context_profiles",
            "unique exact profile identities",
            {"saved_panel": saved_ids, "composition": planned_ids},
            "The package contains duplicate runtime profile identities.",
        )
    missing = sorted(set(saved_ids) - set(planned_ids))
    if missing:
        return _reason(
            "missing_profile_id",
            "panel_composition_plan.profiles",
            sorted(saved_ids),
            sorted(planned_ids),
            "The composition plan is missing saved-panel profile identities.",
        )
    extra = sorted(set(planned_ids) - set(saved_ids))
    if extra:
        return _reason(
            "extra_profile_id",
            "panel_composition_plan.profiles",
            sorted(saved_ids),
            sorted(planned_ids),
            "The composition plan contains profile identities absent from the saved panel.",
        )
    return _reason(
        "invalid_package",
        "audience_package",
        "one fully validated immutable v3 package",
        str(error),
        "The audience package failed canonical validation.",
    )


def _incompatible_envelope(
    raw: bytes,
    error: PackageValidationError,
    *,
    resolved_at: str,
) -> tuple[dict[str, object], dict[str, bytes]]:
    manifest, members, documents = _safe_invalid_context(raw)
    envelope = {
        "schema_version": RUN_ENVELOPE_VERSION,
        "resolved_at": resolved_at,
        "resolution_status": "incompatible",
        "resolution_reasons": [_invalid_reason(manifest, documents, error)],
        "audience_package": {
            "schema_version": manifest.get("schema_version"),
            "generator_version": manifest.get("generator_version"),
            "package_manifest_sha256": _sha256(
                members["package-manifest.json"]
            ),
            "package_zip_sha256": _sha256(raw),
            "panel_id": manifest.get("panel_id"),
            "panel_version": manifest.get("panel_version"),
            "tier": manifest.get("tier"),
            "evidence_basis": manifest.get("evidence_basis"),
        },
        "audience_lock": _placeholder_audience_lock(manifest),
        "context_strata": [],
        "grounded_context_profiles": [],
        "profile_weights": [],
        "allocation_constraints": [],
        "allocation_basis": "directional_planning",
        "claim_boundary": (
            "No runtime claim is available for an incompatible audience package."
        ),
        "snapshot": {
            "relative_path": SNAPSHOT_RELATIVE_PATH,
            "package_sha256": _sha256(raw),
            "manifest_sha256": _sha256(members["package-manifest.json"]),
            "members": [
                {
                    "path": name,
                    "sha256": _sha256(payload),
                    "byte_count": len(payload),
                }
                for name, payload in sorted(members.items())
            ],
        },
    }
    if set(envelope) != _ENVELOPE_KEYS:
        raise AssertionError("v3 incompatible envelope drifted from its allowlist")
    return envelope, members


def _scope_resolution(
    panel: Mapping[str, object],
    study_scope: Mapping[str, object],
    *,
    now: datetime,
    explicit_refresh_triggers: list[str],
) -> tuple[str, list[dict[str, object]]]:
    saved = panel["audience_scope"]
    if not isinstance(saved, Mapping):
        raise PackageValidationError("saved panel audience scope is invalid")
    reasons: list[dict[str, object]] = []
    status = "ready"
    incompatible_fields = ("audience", "category", "geography", "buying_context")
    for field in incompatible_fields:
        if _normalized_scope_text(study_scope[field]) != _normalized_scope_text(saved[field]):
            status = "incompatible"
            reasons.append(
                _reason(
                    f"{field}_mismatch",
                    field,
                    saved[field],
                    study_scope[field],
                    f"The saved panel {field.replace('_', ' ')} is incompatible with this study.",
                )
            )
    if _normalized_scope_text(study_scope["market"]) != _normalized_scope_text(saved["market"]):
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(
            _reason(
                "market_mismatch",
                "market",
                saved["market"],
                study_scope["market"],
                "The saved panel market requires a reviewed refresh for this study.",
            )
        )
    if [
        _normalized_scope_text(item) for item in study_scope["exclusions"]
    ] != [_normalized_scope_text(item) for item in saved["exclusions"]]:
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(
            _reason(
                "exclusions_mismatch",
                "exclusions",
                saved["exclusions"],
                study_scope["exclusions"],
                "The saved exclusions require a reviewed refresh for this study.",
            )
        )

    refresh = panel["refresh_conditions"]
    if not isinstance(refresh, Mapping):
        raise PackageValidationError("saved panel refresh conditions are invalid")
    review_after = _parse_timestamp(
        refresh["review_after"], "refresh_conditions.review_after"
    )
    updated_at = _parse_timestamp(panel["updated_at"], "panel.updated_at")
    if now > review_after:
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(
            _reason(
                "review_after_elapsed",
                "refresh_conditions.review_after",
                refresh["review_after"],
                now.isoformat().replace("+00:00", "Z"),
                "The package has passed its scheduled review date.",
            )
        )
    if now > updated_at + timedelta(days=refresh["max_age_days"]):
        if status != "incompatible":
            status = "needs_refresh"
        reasons.append(
            _reason(
                "max_age_elapsed",
                "refresh_conditions.max_age_days",
                refresh["max_age_days"],
                (now - updated_at).total_seconds() / 86400,
                "The package has exceeded its maximum approved age.",
            )
        )
    saved_triggers = {
        _normalized_scope_text(trigger): trigger
        for trigger in refresh["triggers"]
    }
    for trigger in explicit_refresh_triggers:
        normalized = _normalized_scope_text(trigger)
        if normalized in saved_triggers:
            if status != "incompatible":
                status = "needs_refresh"
            reasons.append(
                _reason(
                    "refresh_trigger_present",
                    "refresh_conditions.triggers",
                    saved_triggers[normalized],
                    trigger,
                    "A saved research-refresh trigger is present in this study.",
                )
            )
    governance = panel["governance"]
    allowed_uses = (
        governance.get("allowed_uses", [])
        if isinstance(governance, Mapping)
        else []
    )
    if (
        not isinstance(governance, Mapping)
        or not isinstance(allowed_uses, list)
        or not any(
            allowed_use in _SYNTHETIC_AD_TESTING_ALLOWED_USES
            for allowed_use in allowed_uses
        )
        or not isinstance(governance.get("privacy_confirmation"), Mapping)
        or governance["privacy_confirmation"].get("confirmed") is not True
    ):
        status = "incompatible"
        reasons.append(
            _reason(
                "permission_incompatible",
                "governance.allowed_uses",
                "Synthetic ad testing with confirmed privacy permission",
                governance,
                "The package governance does not permit synthetic ad testing.",
            )
        )
    reasons.sort(key=lambda item: (str(item["field"]), str(item["code"])))
    return status, reasons


def _joined_profiles(
    panel: Mapping[str, object],
    composition: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    saved_profiles = panel["grounded_context_profiles"]
    composition_profiles = composition["profiles"]
    groups = composition["structural_groups"]
    if not all(isinstance(value, list) for value in (saved_profiles, composition_profiles, groups)):
        raise PackageValidationError("v3 profile collections are invalid")

    def unique_by(
        records: list[object], key: str, label: str
    ) -> dict[str, Mapping[str, object]]:
        indexed: dict[str, Mapping[str, object]] = {}
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(record.get(key), str):
                raise PackageValidationError(f"{label} contains an invalid profile identity")
            identity = str(record[key])
            if identity in indexed:
                raise PackageValidationError(f"{label} contains duplicate profile identities")
            indexed[identity] = record
        return indexed

    saved_by_id = unique_by(
        saved_profiles, "grounded_profile_id", "saved panel"
    )
    composition_by_id = unique_by(
        composition_profiles, "profile_id", "composition plan"
    )
    if set(saved_by_id) != set(composition_by_id):
        raise PackageValidationError(
            "composition and saved-panel profile identities must match exactly"
        )
    group_by_id = unique_by(groups, "structural_group_id", "structural groups")
    composition_hash = str(panel["composition_plan_sha256"])
    joined: list[dict[str, object]] = []
    weights: list[dict[str, object]] = []
    for profile_id in sorted(saved_by_id):
        saved = saved_by_id[profile_id]
        planned = composition_by_id[profile_id]
        group_id = str(planned["structural_group_id"])
        group = group_by_id.get(group_id)
        if group is None:
            raise PackageValidationError(
                f"composition profile {profile_id} references an unknown structural group"
            )
        must_cover = [group_id] if group["must_cover"] is True else []
        profile_hash = "sha256:" + _sha256(_canonical_json(saved["profile_snapshot"]))
        eligible = planned["support_status"] in {"supported", "provisional"}
        augmented = copy_json(saved)
        augmented.update(
            {
                "reported_segment_id": saved["segment_id"],
                "structural_group_id": group_id,
                "overlay_ids": copy_json(planned["overlay_ids"]),
                "structural_weight": group["structural_weight"],
                "conditional_overlay_allocation": planned[
                    "conditional_overlay_allocation"
                ],
                "effective_weight": planned["effective_profile_allocation"],
                "weight_semantics": planned["effective_weight_semantic"],
                "must_cover_group_ids": must_cover,
                "composition_plan_sha256": composition_hash,
                "profile_snapshot_sha256": profile_hash,
                "eligibility": planned["support_status"],
                "eligible": eligible,
            }
        )
        joined.append(augmented)
        weights.append(
            {
                "grounded_profile_id": profile_id,
                "reported_segment_id": saved["segment_id"],
                "structural_group_id": group_id,
                "structural_weight": group["structural_weight"],
                "conditional_overlay_allocation": planned[
                    "conditional_overlay_allocation"
                ],
                "effective_weight": planned["effective_profile_allocation"],
                "weight_semantics": planned["effective_weight_semantic"],
                "must_cover_group_ids": must_cover,
            }
        )
    return joined, weights


def _snapshot_record(
    validation: Mapping[str, object],
    members: Mapping[str, bytes],
) -> dict[str, object]:
    return {
        "relative_path": SNAPSHOT_RELATIVE_PATH,
        "package_sha256": validation["package_zip_sha256"],
        "manifest_sha256": validation["package_manifest_sha256"],
        "members": [
            {
                "path": name,
                "sha256": _sha256(members[name]),
                "byte_count": len(members[name]),
            }
            for name in sorted(members)
        ],
    }


def _snapshot_matches(
    path: Path, raw: bytes, members: Mapping[str, bytes]
) -> bool:
    expected = dict(members)
    expected["audience-panel-package.zip"] = raw
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return {child.name for child in children} == set(expected) and all(
        child.is_file()
        and not child.is_symlink()
        and child.read_bytes() == expected[child.name]
        for child in children
    )


def _preflight_run_unit(
    run_directory: Path,
    raw: bytes,
    members: Mapping[str, bytes],
    envelope: Mapping[str, object],
    resolution_authority: Mapping[str, object],
) -> tuple[Path, bool]:
    run = Path(run_directory).expanduser()
    if not run.is_absolute():
        raise LibrarySafetyError("run directory must be absolute")
    _reject_symlink_components(run, label="run directory")
    if run.exists() and (run.is_symlink() or not run.is_dir()):
        raise LibrarySafetyError("run directory must be a real directory")
    audience = run / "audience"
    _reject_symlink_components(audience, label="run audience directory")
    if not audience.exists() and not audience.is_symlink():
        return run, False
    if audience.is_symlink() or not audience.is_dir():
        raise LibrarySafetyError("run audience directory must be a real directory")
    snapshot = audience / "snapshot"
    resolution = audience / "resolution.json"
    authority = audience / "resolution-authority.json"
    snapshot_exists = snapshot.exists() or snapshot.is_symlink()
    resolution_exists = resolution.exists() or resolution.is_symlink()
    authority_exists = authority.exists() or authority.is_symlink()
    if (
        len({snapshot_exists, resolution_exists, authority_exists}) != 1
        or not snapshot_exists
    ):
        raise ImmutableVersionConflict(
            "run audience state is partial; snapshot, resolution, and authority must exist together"
        )
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or resolution.is_symlink()
        or not resolution.is_file()
        or authority.is_symlink()
        or not authority.is_file()
    ):
        raise ImmutableVersionConflict(
            "run audience snapshot or resolution is unsafe"
        )
    encoded = _canonical_json(envelope)
    if not _snapshot_matches(snapshot, raw, members):
        raise ImmutableVersionConflict(
            "run audience snapshot already exists with different snapshot bytes"
        )
    if resolution.read_bytes() != encoded:
        raise ImmutableVersionConflict(
            "run audience resolution already exists with different envelope bytes"
        )
    if authority.read_bytes() != _canonical_json(resolution_authority):
        raise ImmutableVersionConflict(
            "run audience resolution authority already exists with different bytes"
        )
    return run, True


def _materialize_new_run_unit(
    run: Path,
    raw: bytes,
    members: Mapping[str, bytes],
    envelope: Mapping[str, object],
    resolution_authority: Mapping[str, object],
) -> None:
    if not run.exists():
        _private_mkdir(run)
    stage = Path(tempfile.mkdtemp(prefix=".resolve-v3-", dir=run))
    os.chmod(stage, 0o700)
    staged_audience = stage / "audience"
    staged_snapshot = staged_audience / "snapshot"
    staged_snapshot.mkdir(mode=0o700, parents=True)
    try:
        for name, payload in members.items():
            _atomic_write(staged_snapshot / name, payload)
        _atomic_write(staged_snapshot / "audience-panel-package.zip", raw)
        _atomic_write(
            staged_audience / "resolution.json",
            _canonical_json(envelope),
        )
        _atomic_write(
            staged_audience / "resolution-authority.json",
            _canonical_json(resolution_authority),
        )
        try:
            os.replace(staged_audience, run / "audience")
        except OSError as exc:
            try:
                _run, identical = _preflight_run_unit(
                    run,
                    raw,
                    members,
                    envelope,
                    resolution_authority,
                )
            except (ImmutableVersionConflict, LibrarySafetyError):
                raise ImmutableVersionConflict(
                    "run audience unit changed during atomic materialization"
                ) from exc
            if not identical:
                raise ImmutableVersionConflict(
                    "run audience unit could not be materialized atomically"
                ) from exc
        os.chmod(run / "audience", 0o700)
        os.chmod(run / "audience" / "snapshot", 0o700)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _resolve_audience_v3(
    *,
    package_path: Path,
    study_scope: dict[str, object],
    run_directory: Path,
    explicit_refresh_triggers: list[str] | tuple[str, ...],
    now: datetime | None,
) -> dict[str, object]:
    scope = _validate_scope_input(study_scope, "study_scope")
    triggers = _require_string_array(
        list(explicit_refresh_triggers),
        "explicit_refresh_triggers",
    )
    raw, _manifest_bytes = read_v3_archive_manifest(Path(package_path))
    resolution_clock, resolution_authority = (
        _bound_resolution_authority(
            Path(run_directory),
            now,
            raw=raw,
            study_scope=scope,
            explicit_refresh_triggers=triggers,
        )
    )
    resolved_at = resolution_clock.isoformat().replace("+00:00", "Z")
    try:
        _raw, validation, members, documents = _validated_snapshot(raw)
    except PackageValidationError as exc:
        envelope, invalid_members = _incompatible_envelope(
            raw,
            exc,
            resolved_at=resolved_at,
        )
        _preflight_run_unit(
            Path(run_directory),
            raw,
            invalid_members,
            envelope,
            resolution_authority,
        )
        return envelope
    manifest = documents["manifest"]
    panel = documents["panel"]
    frame = documents["frame"]
    composition = documents["composition"]
    workflow = documents["workflow"]
    audit = documents["audit"]
    joined, weights = _joined_profiles(panel, composition)
    status, reasons = _scope_resolution(
        panel,
        scope,
        now=resolution_clock,
        explicit_refresh_triggers=triggers,
    )
    audit_binding = panel["audit_binding"]
    legacy_migration = (
        isinstance(audit_binding, Mapping)
        and audit_binding.get("applicability") == "legacy_v2_migration"
    )
    approval_ready = (
        workflow is None and audit is None
        if legacy_migration
        else (
            isinstance(workflow, Mapping)
            and isinstance(audit, Mapping)
            and workflow.get("state") == "approved"
            and audit.get("result") == "pass"
        )
    )
    if not approval_ready:
        status = "incompatible"
        reasons.append(
            _reason(
                "approval_incompatible",
                "workflow_state",
                "approved workflow and passing audit",
                {
                    "workflow": (
                        workflow.get("state")
                        if isinstance(workflow, Mapping)
                        else None
                    ),
                    "audit": (
                        audit.get("result")
                        if isinstance(audit, Mapping)
                        else None
                    ),
                },
                "The package is not approved and audited for reusable resolution.",
            )
        )
        reasons.sort(key=lambda item: (str(item["field"]), str(item["code"])))
    allocation_basis = (
        "structural_frame"
        if manifest["tier"] in {"tier_2", "tier_3"}
        and frame["eligibility"] in {"eligible_tier_2", "eligible_tier_3"}
        else "directional_planning"
    )
    envelope = {
        "schema_version": RUN_ENVELOPE_VERSION,
        "resolved_at": resolved_at,
        "resolution_status": status,
        "resolution_reasons": reasons,
        "audience_package": {
            "schema_version": manifest["schema_version"],
            "generator_version": manifest["generator_version"],
            "package_manifest_sha256": validation["package_manifest_sha256"],
            "package_zip_sha256": validation["package_zip_sha256"],
            "panel_id": manifest["panel_id"],
            "panel_version": manifest["panel_version"],
            "tier": manifest["tier"],
            "evidence_basis": manifest["evidence_basis"],
        },
        "audience_lock": _audience_lock(panel),
        "context_strata": copy_json(panel["context_strata"]),
        "grounded_context_profiles": joined,
        "profile_weights": weights,
        "allocation_constraints": copy_json(
            composition["allocation_constraints"]
        ),
        "allocation_basis": allocation_basis,
        "claim_boundary": panel["claim_boundary"],
        "snapshot": _snapshot_record(validation, members),
    }
    if set(envelope) != _ENVELOPE_KEYS:
        raise AssertionError("v3 audience envelope drifted from its allowlist")
    run, identical = _preflight_run_unit(
        Path(run_directory),
        raw,
        members,
        envelope,
        resolution_authority,
    )
    if status == "ready":
        if not identical:
            _materialize_new_run_unit(
                run,
                raw,
                members,
                envelope,
                resolution_authority,
            )
    return envelope


def resolve_audience_v3(
    *,
    package_path: Path,
    study_scope: dict[str, object],
    run_directory: Path,
    now: datetime | None = None,
) -> dict[str, object]:
    """Resolve a v3 package and freeze its exact runtime profile joins."""

    return _resolve_audience_v3(
        package_path=package_path,
        study_scope=study_scope,
        run_directory=run_directory,
        explicit_refresh_triggers=(),
        now=now,
    )


__all__ = [
    "RESOLUTION_AUTHORITY_VERSION",
    "RUN_ENVELOPE_VERSION",
    "resolve_audience_v3",
]
