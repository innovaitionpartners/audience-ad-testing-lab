"""Independent producer record for loose legacy-v2 panel jobs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .audience_library import (
    audience_package_binding,
    load_audience_resolution,
    resolve_audience_panel,
)
from .audience_package import read_validated_package_archive


LEGACY_V2_PRODUCER_VERSION = "audience-jobs-producer-record-v2"
LEGACY_V2_EVIDENCE_VERSION = "audience-jobs-producer-evidence-v1"
PRODUCER_EVIDENCE_FIELD = "producer_evidence"
LEGACY_V2_PRODUCER_KEYS = {
    "schema_version",
    "origin",
    "producer",
    "producer_version",
    "source_assignment_core",
    "source_dispatch_context",
    "source_manifest",
    "canonical_job_cores",
}
PRODUCER_EVIDENCE_KEYS = {
    "schema_version",
    "evidence_id",
    "source_package",
    "source_package_validation",
    "source_assignment",
    "source_dispatch_context",
    "source_manifest",
    "produced_jobs",
}
EVIDENCE_FILE_BINDING_KEYS = {
    "path",
    "sha256",
    "byte_count",
}
EVIDENCE_FILENAMES = {
    "source_package": "audience-panel-package.zip",
    "source_package_validation": "source-package-validation.json",
    "source_assignment": "source-assignment.json",
    "source_dispatch_context": "source-dispatch-context.json",
    "source_manifest": "source-manifest.json",
    "produced_jobs": "produced-jobs.json",
}
V2_PACKAGE_BINDING_KEYS = {
    "panel_id",
    "panel_version",
    "panel_sha256",
    "panel_byte_count",
    "brief_id",
    "brief_sha256",
    "brief_byte_count",
    "package_manifest_sha256",
    "package_manifest_byte_count",
    "package_zip_sha256",
    "package_zip_byte_count",
    "resolved_snapshot_path",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_V3_FIELDS = {
    "audience_profile_rosters",
    "audience_allocation_fidelity",
    "audience_run_claim",
    "audience_allocation_subset",
    "audience_dispatch",
}


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    def language_neutral(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, Mapping):
            return {
                key: language_neutral(nested)
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [language_neutral(nested) for nested in item]
        return item

    return (
        json.dumps(
            language_neutral(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_new_file(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o400)


def _require_read_only_file(path: Path, field: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"legacy v2 independent producer evidence {field} is missing "
            "or unsafe"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o222:
        raise ValueError(
            f"legacy v2 independent producer evidence {field} must be "
            "application-managed and read-only"
        )
    return path.read_bytes()


def _validated_evidence_binding(
    value: Any,
    *,
    record_path: Path,
    evidence_directory: Path,
    binding_name: str,
) -> tuple[dict[str, Any], bytes]:
    binding = _mapping(
        value,
        f"legacy v2 producer_evidence.{binding_name}",
    )
    if set(binding) != EVIDENCE_FILE_BINDING_KEYS:
        raise ValueError(
            f"legacy v2 producer_evidence.{binding_name} keys are invalid"
        )
    relative = binding.get("path")
    expected_relative = (
        f"{record_path.stem}.evidence/"
        f"{EVIDENCE_FILENAMES[binding_name]}"
    )
    if (
        not isinstance(relative, str)
        or relative != expected_relative
        or "\\" in relative
    ):
        raise ValueError(
            f"legacy v2 producer_evidence.{binding_name} path is not canonical"
        )
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(
            f"legacy v2 producer_evidence.{binding_name} path is unsafe"
        )
    target = record_path.parent.joinpath(*pure.parts)
    if target.parent != evidence_directory:
        raise ValueError(
            f"legacy v2 producer_evidence.{binding_name} leaves its "
            "application-managed evidence directory"
        )
    raw = _require_read_only_file(target, binding_name)
    digest = binding.get("sha256")
    byte_count = binding.get("byte_count")
    if (
        not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count < 1
        or digest != _sha256(raw)
        or byte_count != len(raw)
    ):
        raise ValueError(
            f"legacy v2 producer_evidence.{binding_name} raw-byte binding "
            "does not match"
        )
    return dict(binding), raw


def _canonical_json_evidence(raw: bytes, field: str) -> Any:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"legacy v2 independent producer evidence {field} is invalid JSON"
        ) from exc
    try:
        expected = _canonical_json_bytes(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"legacy v2 independent producer evidence {field} is "
            "noncanonical"
        ) from exc
    if raw != expected:
        raise ValueError(
            f"legacy v2 independent producer evidence {field} must use "
            "canonical JSON bytes"
        )
    return payload


def _v2_package_binding(
    assignment_core: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    candidates = [
        value
        for value in (
            assignment_core.get("audience_package"),
            manifest.get("audience_package")
            if isinstance(manifest, Mapping)
            else None,
        )
        if value is not None
    ]
    if not candidates or not all(
        isinstance(value, Mapping) for value in candidates
    ):
        raise ValueError(
            "legacy v2 producer evidence requires an exact source-v2 "
            "audience package binding"
        )
    if any(dict(value) != dict(candidates[0]) for value in candidates[1:]):
        raise ValueError(
            "legacy v2 source package bindings do not match"
        )
    binding = candidates[0]
    if set(binding) != V2_PACKAGE_BINDING_KEYS:
        raise ValueError(
            "legacy v2 source package binding keys are invalid"
        )
    for field in (
        "panel_id",
        "panel_version",
        "brief_id",
        "resolved_snapshot_path",
    ):
        if not isinstance(binding[field], str) or not binding[field]:
            raise ValueError(
                f"legacy v2 source package {field} is invalid"
            )
    for field in (
        "panel_sha256",
        "brief_sha256",
        "package_manifest_sha256",
        "package_zip_sha256",
    ):
        if (
            not isinstance(binding[field], str)
            or not _DIGEST.fullmatch(binding[field])
        ):
            raise ValueError(
                f"legacy v2 source package {field} is invalid"
            )
    for field in (
        "panel_byte_count",
        "brief_byte_count",
        "package_manifest_byte_count",
        "package_zip_byte_count",
    ):
        if (
            isinstance(binding[field], bool)
            or not isinstance(binding[field], int)
            or binding[field] < 1
        ):
            raise ValueError(
                f"legacy v2 source package {field} is invalid"
            )
    return binding


def _upstream_jobs(
    assignment_core: Mapping[str, Any],
    record_type: str,
) -> Sequence[Any] | None:
    if record_type == "screening_response":
        assignment = _mapping(
            assignment_core.get("assignment"),
            "source_assignment_core.assignment",
        )
        return _sequence(
            assignment.get("synthetic_replicate_jobs"),
            "source_assignment_core.assignment.synthetic_replicate_jobs",
        )
    if record_type == "boundary_response":
        boundary_plan = _mapping(
            assignment_core.get("boundary_plan"),
            "source_assignment_core.boundary_plan",
        )
        return _sequence(
            boundary_plan.get("predeclared_pair_assignments"),
            "source_assignment_core.boundary_plan."
            "predeclared_pair_assignments",
        )
    if record_type == "finalist_response":
        approved = _sequence(
            assignment_core.get("approved_finalist_ids"),
            "source_assignment_core.approved_finalist_ids",
        )
        if not approved:
            raise ValueError(
                "legacy v2 finalist producer evidence is empty"
            )
        return None
    raise ValueError("legacy v2 source record_type is unsupported")


def _validate_upstream_binding(
    assignment_core: Mapping[str, Any],
    canonical_jobs: Sequence[Mapping[str, Any]],
    record_type: str,
) -> None:
    upstream = _upstream_jobs(assignment_core, record_type)
    if record_type == "finalist_response":
        approved = list(assignment_core["approved_finalist_ids"])
        if any(job.get("variation_ids") != approved for job in canonical_jobs):
            raise ValueError(
                "legacy v2 finalist jobs do not match the approved roster"
            )
        return
    assert upstream is not None
    upstream_by_id: dict[str, Mapping[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, raw in enumerate(upstream):
        item = _mapping(raw, f"legacy v2 upstream job[{index}]")
        identity = (
            item.get("synthetic_replicate_id")
            if record_type == "screening_response"
            else item.get("pair_assignment_id")
        )
        if not isinstance(identity, str) or not identity:
            raise ValueError("legacy v2 upstream job identity is invalid")
        if identity in upstream_by_id:
            raise ValueError("legacy v2 upstream job identities are duplicated")
        upstream_by_id[identity] = item
        ordered_ids.append(identity)
    canonical_ids = [
        str(job.get("synthetic_replicate_id"))
        for job in canonical_jobs
    ]
    if canonical_ids != [
        identity for identity in ordered_ids if identity in set(canonical_ids)
    ]:
        raise ValueError(
            "legacy v2 canonical job order does not match its source assignment"
        )
    for job in canonical_jobs:
        identity = str(job["synthetic_replicate_id"])
        source = upstream_by_id.get(identity)
        if source is None:
            raise ValueError(
                "legacy v2 canonical job is absent from its source assignment"
            )
        if (
            job.get("variation_ids") != source.get("variation_ids")
            or (
                record_type == "screening_response"
                and job.get("shown_order") != source.get("shown_order")
            )
            or (
                record_type == "boundary_response"
                and (
                    job.get("pair_assignment_id") != identity
                    or job.get("boundary_wave")
                    != source.get("wave", source.get("boundary_wave"))
                )
            )
        ):
            raise ValueError(
                "legacy v2 canonical job core does not match its source "
                "assignment"
            )


def _load_independent_producer_evidence(
    *,
    authority: Mapping[str, Any],
    assignment_core: Mapping[str, Any],
    context: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    record_path: Path | str | None,
) -> dict[str, Any]:
    evidence_raw = context.get(PRODUCER_EVIDENCE_FIELD)
    if not isinstance(evidence_raw, Mapping):
        raise ValueError(
            "legacy v2 producer record requires independently supplied "
            "application-managed producer evidence"
        )
    evidence = dict(evidence_raw)
    if (
        set(evidence) != PRODUCER_EVIDENCE_KEYS
        or evidence.get("schema_version") != LEGACY_V2_EVIDENCE_VERSION
    ):
        raise ValueError(
            "legacy v2 independent producer evidence schema is invalid"
        )
    if record_path is None:
        raise ValueError(
            "legacy v2 independent producer evidence requires its canonical "
            "producer-record path"
        )
    record = Path(record_path).expanduser().absolute()
    record_bytes = _require_read_only_file(record, "producer_record")
    record_payload = _canonical_json_evidence(
        record_bytes,
        "producer_record",
    )
    if not isinstance(record_payload, Mapping) or dict(record_payload) != dict(
        authority
    ):
        raise ValueError(
            "legacy v2 producer record bytes do not match the supplied record"
        )
    evidence_directory = record.parent / f"{record.stem}.evidence"
    if (
        evidence_directory.is_symlink()
        or not evidence_directory.is_dir()
        or stat.S_IMODE(evidence_directory.stat().st_mode) & 0o222
    ):
        raise ValueError(
            "legacy v2 independent producer evidence directory is missing, "
            "unsafe, or mutable"
        )

    bindings: dict[str, dict[str, Any] | None] = {}
    raw_files: dict[str, bytes] = {}
    for binding_name in EVIDENCE_FILENAMES:
        if binding_name == "source_manifest" and manifest is None:
            if evidence.get(binding_name) is not None:
                raise ValueError(
                    "legacy v2 producer evidence cannot bind a source "
                    "manifest when the producer record has none"
                )
            bindings[binding_name] = None
            continue
        if evidence.get(binding_name) is None:
            raise ValueError(
                f"legacy v2 producer evidence {binding_name} is required"
            )
        binding, raw = _validated_evidence_binding(
            evidence[binding_name],
            record_path=record,
            evidence_directory=evidence_directory,
            binding_name=binding_name,
        )
        bindings[binding_name] = binding
        raw_files[binding_name] = raw

    evidence_identity_input = {
        binding_name: evidence[binding_name]
        for binding_name in EVIDENCE_FILENAMES
    }
    evidence_id = evidence.get("evidence_id")
    if (
        not isinstance(evidence_id, str)
        or not _DIGEST.fullmatch(evidence_id)
        or evidence_id
        != _sha256(_canonical_json_bytes(evidence_identity_input))
    ):
        raise ValueError(
            "legacy v2 independent producer evidence ID does not bind its "
            "exact raw-byte records"
        )

    reopened_assignment = _canonical_json_evidence(
        raw_files["source_assignment"],
        "source_assignment",
    )
    reopened_context = _canonical_json_evidence(
        raw_files["source_dispatch_context"],
        "source_dispatch_context",
    )
    reopened_manifest = (
        None
        if manifest is None
        else _canonical_json_evidence(
            raw_files["source_manifest"],
            "source_manifest",
        )
    )
    reopened_jobs = _canonical_json_evidence(
        raw_files["produced_jobs"],
        "produced_jobs",
    )
    package_preflight = _canonical_json_evidence(
        raw_files["source_package_validation"],
        "source_package_validation",
    )
    context_without_evidence = dict(context)
    context_without_evidence.pop(PRODUCER_EVIDENCE_FIELD)
    if (
        reopened_assignment != dict(assignment_core)
        or reopened_context != context_without_evidence
        or reopened_manifest
        != (None if manifest is None else dict(manifest))
    ):
        raise ValueError(
            "legacy v2 producer record does not exactly match its "
            "independently reopened assignment, context, and manifest"
        )
    if not isinstance(reopened_jobs, Mapping):
        raise ValueError(
            "legacy v2 independently reopened produced jobs must be an object"
        )

    package_binding = bindings["source_package"]
    assert isinstance(package_binding, dict)
    package_path = record.parent / PurePosixPath(
        str(package_binding["path"])
    )
    package_snapshot = read_validated_package_archive(package_path)
    if package_preflight != package_snapshot["validation"]:
        raise ValueError(
            "legacy v2 source package validation evidence does not match "
            "the actual validated v2 archive"
        )
    return {
        "context": context_without_evidence,
        "produced_jobs": dict(reopened_jobs),
        "package_path": package_path,
        "package_snapshot": package_snapshot,
    }


def validate_legacy_v2_producer_record(
    record: object,
    candidate_jobs_payload: Mapping[str, Any],
    *,
    record_path: Path | str | None = None,
) -> dict[str, Any]:
    """Authenticate exact candidate jobs against independent v2 source evidence."""

    authority = _mapping(record, "legacy v2 producer record")
    if (
        set(authority) != LEGACY_V2_PRODUCER_KEYS
        or authority.get("schema_version")
        != LEGACY_V2_PRODUCER_VERSION
        or authority.get("origin") != "legacy_v2"
        or authority.get("producer") != "prepare-panel-jobs.py"
        or authority.get("producer_version") != "2.1.0"
    ):
        raise ValueError("legacy v2 producer record is invalid")
    assignment_core = _mapping(
        authority["source_assignment_core"],
        "legacy v2 source_assignment_core",
    )
    context = _mapping(
        authority["source_dispatch_context"],
        "legacy v2 source_dispatch_context",
    )
    raw_manifest = authority["source_manifest"]
    manifest = (
        None
        if raw_manifest is None
        else _mapping(raw_manifest, "legacy v2 source_manifest")
    )
    for source in (assignment_core, manifest or {}):
        if _V3_FIELDS & set(source):
            raise ValueError(
                "legacy v2 producer evidence cannot contain v3 authority"
            )
    package_claim = _v2_package_binding(assignment_core, manifest)
    independent = _load_independent_producer_evidence(
        authority=authority,
        assignment_core=assignment_core,
        context=context,
        manifest=manifest,
        record_path=record_path,
    )
    context = independent["context"]
    source_study_id = assignment_core.get(
        "study_id",
        manifest.get("study_id") if manifest else None,
    )
    source_method = assignment_core.get(
        "method",
        manifest.get("method") if manifest else None,
    )
    record_type = context.get("record_type")
    if (
        not isinstance(source_study_id, str)
        or not source_study_id
        or source_method
        not in {"complete_exposure", "partial_exposure_maxdiff"}
        or context.get("study_id") != source_study_id
        or record_type
        not in {
            "screening_response",
            "boundary_response",
            "finalist_response",
        }
    ):
        raise ValueError(
            "legacy v2 producer evidence has incoherent source identity"
        )
    canonical_raw = _sequence(
        authority["canonical_job_cores"],
        "legacy v2 canonical_job_cores",
    )
    if not canonical_raw:
        raise ValueError("legacy v2 canonical_job_cores must not be empty")
    canonical_jobs = [
        dict(_mapping(raw, f"legacy v2 canonical_job_cores[{index}]"))
        for index, raw in enumerate(canonical_raw)
    ]
    for index, job in enumerate(canonical_jobs):
        if _V3_FIELDS & set(job) or any(
            field in job
            for field in (
                "audience_slot_id",
                "profile_snapshot_sha256",
            )
        ):
            raise ValueError(
                "legacy v2 canonical job cannot contain v3 audience bindings"
            )
        if (
            job.get("study_id") != source_study_id
            or job.get("method") != source_method
            or job.get("record_type") != record_type
        ):
            raise ValueError(
                f"legacy v2 canonical_job_cores[{index}] source identity "
                "is invalid"
            )
    _validate_upstream_binding(
        assignment_core,
        canonical_jobs,
        str(record_type),
    )
    produced_jobs_payload = independent["produced_jobs"]
    if (
        produced_jobs_payload.get("study_id") != source_study_id
        or produced_jobs_payload.get("method") != source_method
        or produced_jobs_payload.get("record_type") != record_type
        or produced_jobs_payload.get("synthetic_replicate_jobs")
        != canonical_jobs
    ):
        raise ValueError(
            "legacy v2 producer record does not match the independently "
            "reopened complete produced jobs"
        )

    package_snapshot = independent["package_snapshot"]
    members = _mapping(
        package_snapshot.get("members"),
        "validated v2 package members",
    )
    try:
        panel = json.loads(
            bytes(members["saved-audience-panel.json"]).decode("utf-8")
        )
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "legacy v2 validated source package panel is unavailable"
        ) from exc
    panel_scope = _mapping(
        panel.get("audience_scope"),
        "legacy v2 source package audience_scope",
    )
    scope = {
        key: deepcopy(panel_scope[key])
        for key in (
            "audience",
            "market",
            "geography",
            "category",
            "buying_context",
            "exclusions",
        )
    }
    updated_at = panel.get("updated_at")
    if not isinstance(updated_at, str):
        raise ValueError(
            "legacy v2 source package updated_at is required"
        )
    try:
        validation_time = datetime.fromisoformat(
            updated_at.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            "legacy v2 source package updated_at is invalid"
        ) from exc
    with tempfile.TemporaryDirectory(
        prefix="audience-v2-producer-evidence-"
    ) as temporary:
        reconstruction_root = Path(temporary)
        resolution = resolve_audience_panel(
            {
                "source": "file",
                "package_path": str(independent["package_path"]),
            },
            scope,
            run_dir=reconstruction_root,
            now=validation_time,
        )
        expected_package_binding = audience_package_binding(
            reconstruction_root,
            resolution,
        )
        if dict(package_claim) != expected_package_binding:
            raise ValueError(
                "legacy v2 source package claim does not match the actual "
                "independently validated archive"
            )
        from .dispatch import enrich_assignment_jobs  # noqa: PLC0415

        reconstructed_jobs_payload = enrich_assignment_jobs(
            assignment_core,
            context,
            manifest=manifest,
            audience_resolution=(
                reconstruction_root / "audience" / "resolution.json"
            ),
        )
    if reconstructed_jobs_payload != produced_jobs_payload:
        raise ValueError(
            "legacy v2 complete produced job cores and ordering do not match "
            "the independently reopened source package, assignment, "
            "dispatch context, and manifest"
        )
    candidate_raw = _sequence(
        candidate_jobs_payload.get("synthetic_replicate_jobs"),
        "candidate synthetic_replicate_jobs",
    )
    candidate_jobs = [
        dict(_mapping(raw, f"candidate job[{index}]"))
        for index, raw in enumerate(candidate_raw)
    ]
    canonical_by_id = {
        str(job["synthetic_replicate_id"]): job
        for job in canonical_jobs
    }
    candidate_ids = [
        str(job.get("synthetic_replicate_id"))
        for job in candidate_jobs
    ]
    if (
        not candidate_ids
        or len(candidate_ids) != len(set(candidate_ids))
        or candidate_ids
        != [
            str(job["synthetic_replicate_id"])
            for job in canonical_jobs
            if str(job["synthetic_replicate_id"]) in set(candidate_ids)
        ]
        or any(
            canonical_by_id.get(identity) != job
            for identity, job in zip(
                candidate_ids,
                candidate_jobs,
                strict=True,
            )
        )
    ):
        raise ValueError(
            "legacy v2 producer record does not bind the complete canonical "
            "candidate job cores in exact source order"
        )
    return dict(authority)


def build_legacy_v2_producer_record(
    *,
    assignment_core: Mapping[str, Any],
    dispatch_context: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    jobs_payload: Mapping[str, Any],
    producer_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one immutable record around independently persisted evidence."""

    source_context = deepcopy(dict(dispatch_context))
    if PRODUCER_EVIDENCE_FIELD in source_context:
        raise ValueError(
            "source dispatch context cannot predeclare producer_evidence"
        )
    source_context[PRODUCER_EVIDENCE_FIELD] = deepcopy(
        dict(producer_evidence)
    )
    record = {
        "schema_version": LEGACY_V2_PRODUCER_VERSION,
        "origin": "legacy_v2",
        "producer": "prepare-panel-jobs.py",
        "producer_version": "2.1.0",
        "source_assignment_core": deepcopy(dict(assignment_core)),
        "source_dispatch_context": source_context,
        "source_manifest": (
            None if manifest is None else deepcopy(dict(manifest))
        ),
        "canonical_job_cores": deepcopy(
            list(jobs_payload["synthetic_replicate_jobs"])
        ),
    }
    return record


def write_legacy_v2_producer_record(
    *,
    record_path: Path | str,
    assignment_core: Mapping[str, Any],
    dispatch_context: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    jobs_payload: Mapping[str, Any],
    audience_resolution: Path | str,
) -> dict[str, Any]:
    """Persist exact immutable evidence, then write and reopen its record."""

    output = Path(record_path).expanduser().absolute()
    if output.name in {"", ".", ".."} or output.is_symlink():
        raise ValueError("legacy v2 producer-record output path is unsafe")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise ValueError(
            "legacy v2 producer-record parent must be a real directory"
        )
    evidence_directory = output.parent / f"{output.stem}.evidence"
    if (
        output.exists()
        or output.is_symlink()
        or evidence_directory.exists()
        or evidence_directory.is_symlink()
    ):
        raise ValueError(
            "legacy v2 producer evidence output already exists and cannot "
            "be overwritten"
        )

    resolution_path = Path(audience_resolution).expanduser().absolute()
    resolution = load_audience_resolution(resolution_path)
    actual_package_binding = audience_package_binding(
        resolution_path.parent.parent,
        resolution,
    )
    claimed_package_binding = _v2_package_binding(
        assignment_core,
        manifest,
    )
    if dict(claimed_package_binding) != actual_package_binding:
        raise ValueError(
            "legacy v2 source assignment does not bind the exact resolved "
            "v2 package"
        )
    package_path = (
        resolution_path.parent
        / "snapshot"
        / "audience-panel-package.zip"
    )
    package_snapshot = read_validated_package_archive(package_path)
    source_bytes: dict[str, bytes | None] = {
        "source_package": bytes(package_snapshot["archive_bytes"]),
        "source_package_validation": _canonical_json_bytes(
            package_snapshot["validation"]
        ),
        "source_assignment": _canonical_json_bytes(
            dict(assignment_core)
        ),
        "source_dispatch_context": _canonical_json_bytes(
            dict(dispatch_context)
        ),
        "source_manifest": (
            None
            if manifest is None
            else _canonical_json_bytes(dict(manifest))
        ),
        "produced_jobs": _canonical_json_bytes(dict(jobs_payload)),
    }
    bindings: dict[str, dict[str, Any] | None] = {}
    for binding_name, filename in EVIDENCE_FILENAMES.items():
        raw = source_bytes[binding_name]
        if raw is None:
            bindings[binding_name] = None
            continue
        bindings[binding_name] = {
            "path": f"{evidence_directory.name}/{filename}",
            "sha256": _sha256(raw),
            "byte_count": len(raw),
        }
    evidence_identity_input = {
        binding_name: bindings[binding_name]
        for binding_name in EVIDENCE_FILENAMES
    }
    producer_evidence = {
        "schema_version": LEGACY_V2_EVIDENCE_VERSION,
        "evidence_id": _sha256(
            _canonical_json_bytes(evidence_identity_input)
        ),
        **bindings,
    }
    record = build_legacy_v2_producer_record(
        assignment_core=assignment_core,
        dispatch_context=dispatch_context,
        manifest=manifest,
        jobs_payload=jobs_payload,
        producer_evidence=producer_evidence,
    )

    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{evidence_directory.name}-",
            dir=output.parent,
        )
    )
    wrote_record = False
    moved_evidence = False
    try:
        os.chmod(stage, 0o700)
        for binding_name, filename in EVIDENCE_FILENAMES.items():
            raw = source_bytes[binding_name]
            if raw is not None:
                _write_new_file(stage / filename, raw)
        os.chmod(stage, 0o500)
        os.replace(stage, evidence_directory)
        moved_evidence = True
        _write_new_file(output, _canonical_json_bytes(record))
        wrote_record = True
        validate_legacy_v2_producer_record(
            record,
            jobs_payload,
            record_path=output,
        )
    except BaseException:
        if wrote_record:
            output.unlink(missing_ok=True)
        if moved_evidence and evidence_directory.exists():
            os.chmod(evidence_directory, 0o700)
            shutil.rmtree(evidence_directory, ignore_errors=True)
        elif stage.exists():
            os.chmod(stage, 0o700)
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return record
