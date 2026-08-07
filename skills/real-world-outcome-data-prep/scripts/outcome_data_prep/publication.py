"""Immutable, authority-authenticated import publication and recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import time

from .common import (
    ContractError,
    canonical_json_bytes,
    closed_object,
    require_identifier,
    sha256_bytes,
    sha256_json,
)
from .contracts import (
    EVIDENCE_STATUSES,
    validate_correction_request,
    validate_import_event,
    validate_normalized_observation,
    validate_observation_binding,
    validate_source_manifest,
)
from .study_authority import (
    IMPORT_COMPLETION_CLAIM_DOMAIN,
    IMPORT_CURRENT_POINTER_DOMAIN,
    IMPORT_LEDGER_ENVELOPE_DOMAIN,
    IMPORT_PENDING_TRANSACTION_DOMAIN,
    PublicationAuthorityContext,
    StudyAuthority,
    StudyAuthorityError,
    authenticate_study_authority_hmac,
    publication_authority_context,
    study_authority_hmac,
)
from .validation_handoff import validate_validation_handoff_document


GENERATION_VERSION = "outcome-import-generation-manifest-v1"
ANALYTICAL_IDENTITY_VERSION = "outcome-analytical-identity-v1"
LEDGER_ENVELOPE_VERSION = "outcome-import-ledger-envelope-v1"
PENDING_TRANSACTION_VERSION = "outcome-import-pending-transaction-v1"
CURRENT_POINTER_VERSION = "outcome-current-import-v1"
COMPLETION_CLAIM_VERSION = "outcome-import-completion-claim-v1"
COMPLETED_TRANSACTIONS_DIRECTORY = "completed-import-transactions"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMPORT_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLATFORM_ALIASES = frozenset({
    "aux", "con", "nul", "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})
_GENERATION_KEYS = {
    "schema_version", "study_id", "registration_id", "registration_sha256",
    "registration_receipt_sha256", "import_id", "imported_at",
    "imported_by", "previous_evidence_status", "next_evidence_status",
    "delivery_started_at", "first_outcome_accessed_at",
    "source_exported_at", "source_manifest_sha256",
    "validation_handoff_sha256", "analytical_identity_sha256",
    "correction_id", "correction_request_sha256",
    "supersedes_import_id", "superseded_observation_ids", "files",
    "generation_sha256",
}
_FILE_KEYS = {"relative_path", "sha256", "byte_count", "role", "source_id"}
_FILE_ROLES = {
    "source_manifest", "accepted_source", "validation_handoff",
    "analytical_identity", "import_event", "correction_request",
    "supporting_record",
}
_IDENTITY_KEYS = {
    "schema_version", "observations", "analytical_identity_sha256",
}
_IDENTITY_ROW_KEYS = {
    "registration_id", "registration_sha256", "delivery_map_sha256",
    "delivery_mapping_id", "delivery_mapping_sha256",
    "campaign_plan_sha256", "platform", "platform_campaign_id",
    "platform_ad_group_id", "platform_ad_id", "platform_creative_id",
    "block_id", "study_id", "arm_id", "batch_id", "segment_ids",
    "creative_id", "variant_id", "asset_sha256", "panel_sha256",
    "package_sha256", "run_id", "result_sha256", "metric_id",
    "measurement_window", "attribution_window",
}
_ENVELOPE_KEYS = {
    "schema_version", "study_id", "registration_id", "registration_sha256",
    "registration_receipt_sha256", "event", "previous_ledger_digest",
    "previous_evidence_status", "next_evidence_status",
    "delivery_started_at", "first_outcome_accessed_at",
    "source_exported_at", "imported_at", "import_digest",
    "analytical_identity_sha256", "correction_id",
    "correction_request_sha256", "supersedes_import_id",
    "superseded_observation_ids", "source_manifest_sha256",
    "validation_handoff_sha256", "envelope_sha256", "event_hmac_sha256",
}
_POINTER_KEYS = {
    "schema_version", "study_id", "import_id", "import_digest",
    "ledger_digest", "evidence_status", "analytical_identity_sha256",
    "current_pointer_sha256", "pointer_hmac_sha256",
}
_PENDING_KEYS = {
    "schema_version", "study_id", "import_id", "import_digest",
    "old_ledger_digest", "old_ledger_byte_count", "new_ledger_digest",
    "new_ledger_byte_count", "event_envelope", "event_bytes_sha256",
    "old_pointer_file_sha256", "new_pointer", "new_pointer_file_sha256",
    "pending_sha256", "pending_hmac_sha256",
}
_COMPLETION_CLAIM_KEYS = {
    "schema_version", "study_id", "import_id", "import_digest",
    "receipt_name", "pending_file_sha256", "pending_device",
    "pending_inode", "pending_owner", "pending_mode", "pending_link_count",
    "pending_byte_count", "completed_directory_device",
    "completed_directory_inode", "completed_directory_owner",
    "completed_directory_mode", "ledger_digest", "ledger_byte_count",
    "pointer_file_sha256", "completion_claim_sha256",
    "claim_hmac_sha256",
}

ALLOWED_STATUS_TRANSITIONS = frozenset({
    ("preregistered_holdout", "preregistered_holdout"),
    ("preregistered_holdout", "descriptive_only"),
    ("preregistered_holdout", "blocked"),
    ("descriptive_only", "descriptive_only"),
    ("descriptive_only", "blocked"),
    ("blocked", "blocked"),
})


class ImportConflict(ContractError):
    """Import bytes or authenticated history cannot be reconciled safely."""


@dataclass(frozen=True)
class ImportCommit:
    import_id: str
    import_digest: str
    generation_path: Path
    ledger_digest: str
    analytical_identity_sha256: str


@dataclass(frozen=True)
class StudyState:
    current_import_id: str | None
    current_evidence_status: str
    ledger_digest: str | None
    ledger_verified: bool


@dataclass(frozen=True)
class _StagedGeneration:
    root: Path
    root_identity: tuple[int, int, int, int]
    manifest: dict[str, object]
    source_manifest: dict[str, object]
    event: dict[str, object]
    handoff: dict[str, object] | None
    analytical_identity: dict[str, object]
    correction: dict[str, object] | None

    @property
    def import_id(self) -> str:
        return str(self.manifest["import_id"])

    @property
    def import_digest(self) -> str:
        return str(self.manifest["generation_sha256"])

    @property
    def analytical_identity_sha256(self) -> str:
        return str(self.manifest["analytical_identity_sha256"])


@dataclass(frozen=True)
class _LedgerState:
    raw: bytes
    events: tuple[dict[str, object], ...]
    prefix_digests: tuple[str, ...]
    current_status: str
    current_import_id: str | None
    ledger_digest: str | None
    first_outcome_accessed_at: str | None
    last_source_exported_at: str | None
    last_imported_at: str | None


def _transaction_step(name: str) -> None:
    """Crash-injection seam used only by conformance tests."""

    del name


def _publication_race_step(name: str) -> None:
    """Deterministic mutation seam used only by conformance tests."""

    del name


def _safe_import_component(value: object, label: str = "import_id") -> str:
    if (
        not isinstance(value, str)
        or not _IMPORT_COMPONENT.fullmatch(value)
        or ".." in value
        or value.endswith(".")
        or value.casefold().split(".", 1)[0] in _PLATFORM_ALIASES
    ):
        raise ImportConflict(f"{label} must be one portable safe component")
    return value


def _publication_context(
    study_root: Path, authority: StudyAuthority
) -> PublicationAuthorityContext:
    try:
        return publication_authority_context(
            study_root=study_root, authority=authority
        )
    except StudyAuthorityError as exc:
        raise ImportConflict("study authority reauthentication failed") from exc


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ImportConflict(f"{label} chronology is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportConflict(f"{label} chronology is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportConflict(f"{label} chronology is invalid")
    return parsed


def _digest(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ImportConflict(f"{label} is not a prefixed SHA-256 digest")
    return value


def _identifier_or_none(value: object, label: str) -> str | None:
    if value is None:
        return None
    try:
        return require_identifier(value, label)
    except ContractError as exc:
        raise ImportConflict(str(exc)) from exc


def _json_load(raw: bytes, label: str) -> dict[str, object]:
    def duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=duplicate_free,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ImportConflict(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ImportConflict(f"{label} must be an object")
    return value


def _json_array_load(raw: bytes, label: str) -> list[object]:
    def duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=duplicate_free,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ImportConflict(f"{label} is not strict JSON") from exc
    if not isinstance(value, list):
        raise ImportConflict(f"{label} must be an array")
    return value


def _closed(value: object, keys: set[str], label: str) -> dict[str, object]:
    try:
        return closed_object(value, keys, label)
    except ContractError as exc:
        raise ImportConflict(str(exc)) from exc


def require_monotone_status(previous: str, next_value: str) -> None:
    if (previous, next_value) not in ALLOWED_STATUS_TRANSITIONS:
        raise ImportConflict("evidence status transition is not monotone")


def _validate_chronology(
    *,
    delivery_started_at: object,
    first_outcome_accessed_at: object,
    source_exported_at: object,
    imported_at: object,
) -> None:
    delivery = _timestamp(delivery_started_at, "delivery start")
    accessed = _timestamp(first_outcome_accessed_at, "outcome access")
    exported = _timestamp(source_exported_at, "source export")
    imported = _timestamp(imported_at, "import")
    if not delivery <= accessed <= exported <= imported:
        raise ImportConflict("import chronology is not monotone")


def _canonical_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ImportConflict("generation file path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ImportConflict("generation file path contains traversal")
    normalized = path.as_posix()
    if normalized == "generation-manifest.json" or len(normalized) > 512:
        raise ImportConflict("generation file path is reserved or too long")
    return normalized


def _reject_symlink_components(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ImportConflict(f"{label} contains parent traversal")
    absolute = path.absolute()
    current = Path(absolute.anchor)
    aliases = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() and (
                current not in aliases or current.resolve() != aliases[current]
            ):
                raise ImportConflict(f"{label} contains a symlink component")


def _safe_tree(root: Path) -> tuple[dict[str, bytes], frozenset[str]]:
    _reject_symlink_components(root, "staged generation path")
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ImportConflict("staged generation is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ImportConflict("staged generation must be a non-symlink directory")
    if stat.S_IMODE(root_info.st_mode) != 0o700:
        raise ImportConflict("staged generation directories must use mode 0700")
    output: dict[str, bytes] = {}
    directories: set[str] = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise ImportConflict("staged generation traversal failed") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = path.lstat()
            except OSError as exc:
                raise ImportConflict("staged generation entry changed") from exc
            if stat.S_ISLNK(info.st_mode):
                raise ImportConflict("staged generation contains a symlink")
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o700:
                    raise ImportConflict(
                        "staged generation directories must use mode 0700"
                    )
                directories.add(path.relative_to(root).as_posix())
                stack.append(path)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ImportConflict(
                    "staged generation files must be unlinked regular files"
                )
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ImportConflict("staged generation files must use mode 0600")
            relative = path.relative_to(root).as_posix()
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                opened = os.fstat(descriptor)
                if (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                ) != (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ):
                    raise ImportConflict("staged generation file changed while opening")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, 1_048_576)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise ImportConflict("staged generation exceeds byte limit")
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                if (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ImportConflict("staged generation file changed while read")
                output[relative] = b"".join(chunks)
            finally:
                os.close(descriptor)
    return output, frozenset(directories)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif stat.S_ISDIR(info.st_mode):
            directories.append(path)
        else:
            raise ImportConflict("staged generation changed before fsync")
    for directory in sorted(
        set(directories), key=lambda item: len(item.parts), reverse=True
    ):
        _fsync_directory(directory)


def _validate_identity_document(value: object) -> dict[str, object]:
    document = _closed(value, _IDENTITY_KEYS, "analytical_identity")
    if document["schema_version"] != ANALYTICAL_IDENTITY_VERSION:
        raise ImportConflict("analytical identity version is invalid")
    raw_rows = document["observations"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ImportConflict("analytical identity observations must be nonempty")
    rows = [
        _closed(row, _IDENTITY_ROW_KEYS, "analytical_identity row")
        for row in raw_rows
    ]
    for row in rows:
        for key, item in row.items():
            if key == "segment_ids":
                if (
                    not isinstance(item, list)
                    or not item
                    or not all(isinstance(part, str) and part for part in item)
                    or len(set(item)) != len(item)
                ):
                    raise ImportConflict("analytical identity segments are invalid")
            elif key.endswith("sha256"):
                _digest(item, f"analytical identity {key}")
            elif not isinstance(item, str) or not item:
                raise ImportConflict(f"analytical identity {key} is invalid")
    ordered = sorted(rows, key=canonical_json_bytes)
    if rows != ordered or len({canonical_json_bytes(row) for row in rows}) != len(rows):
        raise ImportConflict("analytical identity observations are not canonical")
    supplied = _digest(
        document["analytical_identity_sha256"], "analytical identity digest"
    )
    unhashed = {
        "schema_version": ANALYTICAL_IDENTITY_VERSION,
        "observations": rows,
        "analytical_identity_sha256": None,
    }
    if supplied != sha256_json(unhashed):
        raise ImportConflict("analytical identity self-hash is invalid")
    return {**unhashed, "analytical_identity_sha256": supplied}


def analytical_identity_document(
    handoff: Mapping[str, object],
) -> dict[str, object]:
    checked = validate_validation_handoff_document(handoff)
    rows = []
    for binding in checked["observation_bindings"]:
        assert isinstance(binding, Mapping)
        rows.append({key: deepcopy(binding[key]) for key in _IDENTITY_ROW_KEYS})
    rows = sorted(rows, key=canonical_json_bytes)
    document: dict[str, object] = {
        "schema_version": ANALYTICAL_IDENTITY_VERSION,
        "observations": rows,
        "analytical_identity_sha256": None,
    }
    document["analytical_identity_sha256"] = sha256_json(document)
    return _validate_identity_document(document)


def _analytical_identity_from_bindings(
    bindings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    rows = sorted(
        [
            {key: deepcopy(binding[key]) for key in _IDENTITY_ROW_KEYS}
            for binding in bindings
        ],
        key=canonical_json_bytes,
    )
    document: dict[str, object] = {
        "schema_version": ANALYTICAL_IDENTITY_VERSION,
        "observations": rows,
        "analytical_identity_sha256": None,
    }
    document["analytical_identity_sha256"] = sha256_json(document)
    return _validate_identity_document(document)


def _role_file(
    entries: Sequence[dict[str, object]], role: str, *, required: bool
) -> str | None:
    selected = [str(item["relative_path"]) for item in entries if item["role"] == role]
    if len(selected) > 1 or (required and len(selected) != 1):
        raise ImportConflict(f"generation requires exactly one {role} file")
    return selected[0] if selected else None


def _named_supporting_file(
    entries: Sequence[dict[str, object]], relative_path: str
) -> str:
    selected = [
        item for item in entries
        if item["relative_path"] == relative_path
        and item["role"] == "supporting_record"
    ]
    if len(selected) != 1:
        raise ImportConflict(
            f"generation requires exactly one {relative_path} supporting record"
        )
    return relative_path


def validate_complete_staged_generation(
    staged_generation: Path, *, authority: StudyAuthority
) -> _StagedGeneration:
    root = Path(staged_generation)
    files, live_directories = _safe_tree(root)
    manifest_raw = files.get("generation-manifest.json")
    if manifest_raw is None:
        raise ImportConflict("generation manifest is missing")
    manifest = _closed(
        _json_load(manifest_raw, "generation manifest"),
        _GENERATION_KEYS,
        "generation_manifest",
    )
    if manifest["schema_version"] != GENERATION_VERSION:
        raise ImportConflict("generation manifest version is invalid")
    for key in ("study_id", "registration_id", "imported_by"):
        _identifier_or_none(manifest[key], f"generation_manifest.{key}")
    _safe_import_component(
        manifest["import_id"], "generation_manifest.import_id"
    )
    for key in (
        "registration_sha256", "registration_receipt_sha256",
        "source_manifest_sha256",
        "analytical_identity_sha256", "generation_sha256",
    ):
        _digest(manifest[key], f"generation_manifest.{key}")
    _digest(
        manifest["validation_handoff_sha256"],
        "generation_manifest.validation_handoff_sha256",
        nullable=True,
    )
    _digest(
        manifest["correction_request_sha256"],
        "generation_manifest.correction_request_sha256",
        nullable=True,
    )
    previous = manifest["previous_evidence_status"]
    next_status = manifest["next_evidence_status"]
    if previous not in EVIDENCE_STATUSES or next_status not in EVIDENCE_STATUSES:
        raise ImportConflict("generation evidence status is invalid")
    require_monotone_status(str(previous), str(next_status))
    _validate_chronology(
        delivery_started_at=manifest["delivery_started_at"],
        first_outcome_accessed_at=manifest["first_outcome_accessed_at"],
        source_exported_at=manifest["source_exported_at"],
        imported_at=manifest["imported_at"],
    )
    entries_raw = manifest["files"]
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ImportConflict("generation file manifest is empty")
    entries: list[dict[str, object]] = []
    for raw in entries_raw:
        item = _closed(raw, _FILE_KEYS, "generation file")
        relative = _canonical_relative(item["relative_path"])
        digest = _digest(item["sha256"], "generation file digest")
        count = item["byte_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ImportConflict("generation file byte count is invalid")
        if item["role"] not in _FILE_ROLES:
            raise ImportConflict("generation file role is invalid")
        source_id = _identifier_or_none(item["source_id"], "generation source_id")
        if (item["role"] == "accepted_source") != (source_id is not None):
            raise ImportConflict("generation source role binding is invalid")
        entries.append({
            "relative_path": relative,
            "sha256": digest,
            "byte_count": count,
            "role": item["role"],
            "source_id": source_id,
        })
    if entries != sorted(entries, key=lambda item: str(item["relative_path"])):
        raise ImportConflict("generation files are not canonical")
    paths = [str(item["relative_path"]) for item in entries]
    if len(set(paths)) != len(paths):
        raise ImportConflict("generation file paths are duplicated")
    implied_directories: set[str] = set()
    for relative in paths:
        parts = PurePosixPath(relative).parts[:-1]
        for index in range(1, len(parts) + 1):
            implied_directories.add(PurePosixPath(*parts[:index]).as_posix())
    if live_directories != frozenset(implied_directories):
        raise ImportConflict(
            "generation directory inventory is not manifest-implied"
        )
    expected_paths = set(paths) | {"generation-manifest.json"}
    if set(files) != expected_paths:
        raise ImportConflict("generation contains unmanifested or missing files")
    for item in entries:
        raw = files[str(item["relative_path"])]
        if len(raw) != item["byte_count"] or sha256_bytes(raw) != item["sha256"]:
            raise ImportConflict("generation file bytes do not match the manifest")
    unhashed = {**manifest, "files": entries, "generation_sha256": None}
    if manifest["generation_sha256"] != sha256_json(unhashed):
        raise ImportConflict("generation manifest self-hash is invalid")
    canonical_manifest = {
        **unhashed,
        "generation_sha256": manifest["generation_sha256"],
    }
    if canonical_json_bytes(canonical_manifest) != manifest_raw:
        raise ImportConflict("generation manifest bytes are not canonical")

    source_path = _role_file(entries, "source_manifest", required=True)
    event_path = _role_file(entries, "import_event", required=True)
    identity_path = _role_file(entries, "analytical_identity", required=True)
    normalized_path = _named_supporting_file(
        entries, "normalized-observations.json"
    )
    bindings_path = _named_supporting_file(
        entries, "observation-bindings.json"
    )
    handoff_path = _role_file(
        entries,
        "validation_handoff",
        required=next_status == "preregistered_holdout",
    )
    if next_status != "preregistered_holdout" and handoff_path is not None:
        raise ImportConflict(
            "non-preregistered generation cannot contain a validation handoff"
        )
    correction_path = _role_file(
        entries, "correction_request", required=manifest["correction_id"] is not None
    )
    try:
        source_manifest = validate_source_manifest(
            _json_load(files[str(source_path)], "source manifest")
        )
        event = validate_import_event(
            _json_load(files[str(event_path)], "import event")
        )
    except ContractError as exc:
        raise ImportConflict(str(exc)) from exc
    identity = _validate_identity_document(
        _json_load(files[str(identity_path)], "analytical identity")
    )
    try:
        normalized_observations = [
            validate_normalized_observation(item)
            for item in _json_array_load(
                files[normalized_path], "normalized observations"
            )
        ]
        observation_bindings = [
            validate_observation_binding(item)
            for item in _json_array_load(
                files[bindings_path], "observation bindings"
            )
        ]
    except ContractError as exc:
        raise ImportConflict(str(exc)) from exc
    if (
        not normalized_observations
        or len(normalized_observations) != len(observation_bindings)
    ):
        raise ImportConflict(
            "normalized observations and bindings must be nonempty and aligned"
        )
    for observation, binding in zip(
        normalized_observations, observation_bindings
    ):
        projection = observation["validation_projection"]
        assert isinstance(projection, Mapping)
        if (
            binding["observation_id"] != observation["observation_id"]
            or binding["normalized_observation_sha256"]
            != observation["normalized_observation_sha256"]
        ):
            raise ImportConflict(
                "normalized observation and binding identities are inconsistent"
            )
        if (
            projection["evidence_status"] != next_status
            or binding["evidence_status"] != next_status
            or projection["evidence_status"] != binding["evidence_status"]
        ):
            raise ImportConflict(
                "normalized projection and binding statuses do not match "
                "generation evidence status"
            )
    if _analytical_identity_from_bindings(observation_bindings) != identity:
        raise ImportConflict(
            "normalized observation bindings changed analytical identity"
        )
    _safe_import_component(event["import_id"], "import_event.import_id")
    handoff = None
    if handoff_path is not None:
        try:
            handoff = validate_validation_handoff_document(
                _json_load(files[handoff_path], "validation handoff")
            )
        except ContractError as exc:
            raise ImportConflict(str(exc)) from exc
        if analytical_identity_document(handoff) != identity:
            raise ImportConflict("validation handoff analytical identity changed")
        if (
            handoff["normalized_observations"] != normalized_observations
            or handoff["observation_bindings"] != observation_bindings
        ):
            raise ImportConflict(
                "validation handoff does not match staged supporting records"
            )
    if (handoff is None) != (manifest["validation_handoff_sha256"] is None):
        raise ImportConflict("generation validation handoff binding is inconsistent")
    if handoff is not None and handoff["handoff_sha256"] != manifest[
        "validation_handoff_sha256"
    ]:
        raise ImportConflict("generation validation handoff digest is inconsistent")
    if handoff is not None:
        registration_binding = handoff["registration_binding"]
        assert isinstance(registration_binding, Mapping)
        if (
            registration_binding["registration_id"]
            != manifest["registration_id"]
            or registration_binding["registration_sha256"]
            != manifest["registration_sha256"]
        ):
            raise ImportConflict(
                "validation handoff registration identity is inconsistent"
            )
    supporting_observation_ids = [
        row["observation_id"] for row in normalized_observations
    ]
    if event["observation_ids"] != supporting_observation_ids:
        raise ImportConflict(
            "import event observations do not match staged observations"
        )
    if identity["analytical_identity_sha256"] != manifest[
        "analytical_identity_sha256"
    ]:
        raise ImportConflict("generation analytical identity digest is inconsistent")
    if (
        source_manifest["study_id"] != manifest["study_id"]
        or source_manifest["import_id"] != manifest["import_id"]
        or source_manifest["source_manifest_sha256"]
        != manifest["source_manifest_sha256"]
        or event["study_id"] != manifest["study_id"]
        or event["import_id"] != manifest["import_id"]
        or event["imported_at"] != manifest["imported_at"]
        or event["imported_by"] != manifest["imported_by"]
        or event["source_manifest_sha256"]
        != manifest["source_manifest_sha256"]
    ):
        raise ImportConflict("generation import identities are inconsistent")
    source_rows = source_manifest["sources"]
    assert isinstance(source_rows, list)
    source_by_id: dict[str, Mapping[str, object]] = {}
    for row in source_rows:
        if not isinstance(row, Mapping):
            raise ImportConflict("source manifest row is invalid")
        source_id = row.get("source_id")
        source_digest = row.get("source_sha256")
        if not isinstance(source_id, str) or not isinstance(source_digest, str):
            raise ImportConflict("source manifest lacks a durable source binding")
        _digest(source_digest, "source manifest source digest")
        source_by_id[source_id] = row
    admitted = [item for item in entries if item["role"] == "accepted_source"]
    if {str(item["source_id"]) for item in admitted} != set(source_by_id):
        raise ImportConflict("accepted source files do not match the source manifest")
    for item in admitted:
        if source_by_id[str(item["source_id"])]["source_sha256"] != item["sha256"]:
            raise ImportConflict("accepted source bytes do not match source provenance")

    correction = None
    correction_id = _identifier_or_none(manifest["correction_id"], "correction_id")
    supersedes = _identifier_or_none(
        manifest["supersedes_import_id"], "supersedes_import_id"
    )
    if supersedes is not None:
        _safe_import_component(supersedes, "supersedes_import_id")
    superseded = manifest["superseded_observation_ids"]
    if not isinstance(superseded, list) or not all(
        isinstance(item, str) and item for item in superseded
    ) or len(set(superseded)) != len(superseded):
        raise ImportConflict("superseded observation identities are invalid")
    correction_fields = (
        correction_id,
        manifest["correction_request_sha256"],
        supersedes,
        superseded,
        correction_path,
    )
    if correction_id is None:
        if correction_fields != (None, None, None, [], None):
            raise ImportConflict("ordinary import carries correction metadata")
    else:
        if (
            manifest["correction_request_sha256"] is None
            or supersedes is None
            or not superseded
            or correction_path is None
        ):
            raise ImportConflict("correction generation metadata is incomplete")
        correction = _json_load(files[correction_path], "correction request")
        if correction.get("correction_id") != correction_id:
            raise ImportConflict("correction identity is inconsistent")
    # Authority/root binding is checked by commit and recovery, not by a stage
    # that intentionally lives outside the study root.
    root_info = root.lstat()
    return _StagedGeneration(
        root=root,
        root_identity=(
            root_info.st_dev,
            root_info.st_ino,
            root_info.st_uid,
            stat.S_IMODE(root_info.st_mode),
        ),
        manifest={**unhashed, "generation_sha256": manifest["generation_sha256"]},
        source_manifest=source_manifest,
        event=event,
        handoff=handoff,
        analytical_identity=identity,
        correction=correction,
    )


def _build_envelope(
    staged: _StagedGeneration,
    *,
    previous_ledger_digest: str | None,
    context: PublicationAuthorityContext,
    authority: StudyAuthority,
) -> dict[str, object]:
    manifest = staged.manifest
    document: dict[str, object] = {
        "schema_version": LEDGER_ENVELOPE_VERSION,
        "study_id": context.study_id,
        "registration_id": context.registration_id,
        "registration_sha256": context.registration_sha256,
        "registration_receipt_sha256": context.registration_receipt_sha256,
        "event": deepcopy(staged.event),
        "previous_ledger_digest": previous_ledger_digest,
        "previous_evidence_status": manifest["previous_evidence_status"],
        "next_evidence_status": manifest["next_evidence_status"],
        "delivery_started_at": manifest["delivery_started_at"],
        "first_outcome_accessed_at": manifest["first_outcome_accessed_at"],
        "source_exported_at": manifest["source_exported_at"],
        "imported_at": manifest["imported_at"],
        "import_digest": staged.import_digest,
        "analytical_identity_sha256": staged.analytical_identity_sha256,
        "correction_id": manifest["correction_id"],
        "correction_request_sha256": manifest["correction_request_sha256"],
        "supersedes_import_id": manifest["supersedes_import_id"],
        "superseded_observation_ids": deepcopy(
            manifest["superseded_observation_ids"]
        ),
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "validation_handoff_sha256": manifest["validation_handoff_sha256"],
        "envelope_sha256": None,
        "event_hmac_sha256": None,
    }
    document["envelope_sha256"] = sha256_json(document)
    hmac_payload = {**document, "event_hmac_sha256": None}
    document["event_hmac_sha256"] = study_authority_hmac(
        domain=IMPORT_LEDGER_ENVELOPE_DOMAIN,
        payload=hmac_payload,
        authority=authority,
    )
    return _validate_envelope(document, context=context, authority=authority)


def _validate_envelope(
    value: object,
    *,
    context: PublicationAuthorityContext,
    authority: StudyAuthority,
) -> dict[str, object]:
    document = _closed(value, _ENVELOPE_KEYS, "import ledger envelope")
    if document["schema_version"] != LEDGER_ENVELOPE_VERSION:
        raise ImportConflict("import ledger envelope version is invalid")
    try:
        event = validate_import_event(document["event"])
    except ContractError as exc:
        raise ImportConflict(str(exc)) from exc
    _safe_import_component(event["import_id"], "import ledger import_id")
    if (
        document["study_id"] != context.study_id
        or document["registration_id"] != context.registration_id
        or document["registration_sha256"] != context.registration_sha256
        or document["registration_receipt_sha256"]
        != context.registration_receipt_sha256
        or event["study_id"] != context.study_id
        or event["imported_at"] != document["imported_at"]
        or event["source_manifest_sha256"]
        != document["source_manifest_sha256"]
    ):
        raise ImportConflict("import ledger envelope study binding is invalid")
    for key in (
        "registration_sha256", "registration_receipt_sha256", "import_digest",
        "analytical_identity_sha256", "source_manifest_sha256",
    ):
        _digest(document[key], f"import ledger envelope {key}")
    for key in (
        "previous_ledger_digest", "correction_request_sha256",
        "validation_handoff_sha256",
    ):
        _digest(document[key], f"import ledger envelope {key}", nullable=True)
    if document["previous_evidence_status"] not in EVIDENCE_STATUSES or document[
        "next_evidence_status"
    ] not in EVIDENCE_STATUSES:
        raise ImportConflict("import ledger envelope evidence status is invalid")
    require_monotone_status(
        str(document["previous_evidence_status"]),
        str(document["next_evidence_status"]),
    )
    _validate_chronology(
        delivery_started_at=document["delivery_started_at"],
        first_outcome_accessed_at=document["first_outcome_accessed_at"],
        source_exported_at=document["source_exported_at"],
        imported_at=document["imported_at"],
    )
    if document["delivery_started_at"] != context.delivery_started_at:
        raise ImportConflict("sealed delivery-start chronology changed")
    _identifier_or_none(document["correction_id"], "correction_id")
    supersedes = _identifier_or_none(
        document["supersedes_import_id"], "supersedes_import_id"
    )
    if supersedes is not None:
        _safe_import_component(supersedes, "supersedes_import_id")
    superseded = document["superseded_observation_ids"]
    if not isinstance(superseded, list) or not all(
        isinstance(item, str) and item for item in superseded
    ) or len(set(superseded)) != len(superseded):
        raise ImportConflict("superseded observation identities are invalid")
    if document["correction_id"] is None:
        if (
            document["correction_request_sha256"] is not None
            or document["supersedes_import_id"] is not None
            or superseded != []
        ):
            raise ImportConflict("import ledger correction binding is inconsistent")
    elif (
        document["correction_request_sha256"] is None
        or document["supersedes_import_id"] is None
        or not superseded
    ):
        raise ImportConflict("import ledger correction binding is inconsistent")
    supplied_sha = _digest(document["envelope_sha256"], "envelope self-hash")
    unhashed = {**document, "envelope_sha256": None, "event_hmac_sha256": None}
    if supplied_sha != sha256_json(unhashed):
        raise ImportConflict("import ledger envelope self-hash is invalid")
    payload = {**document, "event_hmac_sha256": None}
    try:
        authenticate_study_authority_hmac(
            domain=IMPORT_LEDGER_ENVELOPE_DOMAIN,
            payload=payload,
            supplied_hmac=document["event_hmac_sha256"],
            authority=authority,
            label="import ledger envelope",
        )
    except StudyAuthorityError as exc:
        raise ImportConflict("import ledger envelope authentication failed") from exc
    return {**document, "event": event}


def _ledger_raw(study_root: Path) -> bytes:
    path = study_root / "import-ledger.jsonl"
    if not path.exists() and not path.is_symlink():
        return b""
    try:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise OSError("unsafe ledger")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            chunks = []
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise OSError("ledger changed")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ImportConflict("import ledger is unsafe or changed") from exc


def _replay_ledger_bytes(
    raw: bytes,
    *,
    context: PublicationAuthorityContext,
    authority: StudyAuthority,
) -> _LedgerState:
    if raw and not raw.endswith(b"\n"):
        raise ImportConflict("import ledger has altered newline bytes")
    prefix = b""
    events: list[dict[str, object]] = []
    prefixes: list[str] = []
    status_value = context.initial_evidence_status
    first_access: str | None = None
    last_export: str | None = None
    last_import: str | None = None
    import_ids: set[str] = set()
    correction_ids: set[str] = set()
    active_observations: dict[str, str] = {}
    for line in raw.splitlines(keepends=True):
        if not line or line == b"\n":
            raise ImportConflict("import ledger contains an empty line")
        value = _json_load(line, "import ledger line")
        if canonical_json_bytes(value) != line:
            raise ImportConflict("import ledger line bytes are not canonical")
        envelope = _validate_envelope(value, context=context, authority=authority)
        expected_previous = sha256_bytes(prefix) if prefix else None
        if envelope["previous_ledger_digest"] != expected_previous:
            raise ImportConflict("import ledger hash chain is invalid")
        if envelope["previous_evidence_status"] != status_value:
            raise ImportConflict("import ledger status chain is invalid")
        require_monotone_status(status_value, str(envelope["next_evidence_status"]))
        current_access = str(envelope["first_outcome_accessed_at"])
        if first_access is None:
            first_access = current_access
        elif current_access != first_access:
            raise ImportConflict("first outcome-access chronology changed")
        current_export = str(envelope["source_exported_at"])
        current_import = str(envelope["imported_at"])
        if (
            last_export is not None
            and _timestamp(current_export, "source export")
            < _timestamp(last_export, "source export")
        ):
            raise ImportConflict("source-export chronology is not monotone")
        if (
            last_import is not None
            and _timestamp(current_import, "import")
            < _timestamp(last_import, "import")
        ):
            raise ImportConflict("import chronology is not monotone")
        event = envelope["event"]
        assert isinstance(event, Mapping)
        import_id = str(event["import_id"])
        if import_id in import_ids:
            raise ImportConflict("import ledger duplicates an import identity")
        import_ids.add(import_id)
        correction_id = envelope["correction_id"]
        superseded = envelope["superseded_observation_ids"]
        assert isinstance(superseded, list)
        if correction_id is not None:
            if correction_id in correction_ids:
                raise ImportConflict("import ledger duplicates a correction identity")
            correction_ids.add(str(correction_id))
            supersedes_import = str(envelope["supersedes_import_id"])
            if supersedes_import not in import_ids - {import_id}:
                raise ImportConflict("correction references an unknown prior import")
            for observation_id in superseded:
                if active_observations.get(str(observation_id)) != supersedes_import:
                    raise ImportConflict(
                        "correction does not supersede an active prior observation"
                    )
                del active_observations[str(observation_id)]
        for observation_id in event["observation_ids"]:
            if observation_id in active_observations:
                raise ImportConflict("active observation identity is duplicated")
            active_observations[str(observation_id)] = import_id
        prefix += line
        prefixes.append(sha256_bytes(prefix))
        events.append(envelope)
        status_value = str(envelope["next_evidence_status"])
        last_export = current_export
        last_import = current_import
    return _LedgerState(
        raw=raw,
        events=tuple(events),
        prefix_digests=tuple(prefixes),
        current_status=status_value,
        current_import_id=(
            str(events[-1]["event"]["import_id"])
            if events else None  # type: ignore[index]
        ),
        ledger_digest=sha256_bytes(raw) if raw else None,
        first_outcome_accessed_at=first_access,
        last_source_exported_at=last_export,
        last_imported_at=last_import,
    )


def replay_authenticated_ledger(
    study_root: Path, *, authority: StudyAuthority
) -> StudyState:
    root = Path(study_root)
    context = _publication_context(root, authority)
    state = _replay_ledger_bytes(
        _ledger_raw(root), context=context, authority=authority
    )
    _verify_generations(root, state, authority=authority)
    _validate_completed_transactions(root, state, authority=authority)
    return StudyState(
        current_import_id=state.current_import_id,
        current_evidence_status=state.current_status,
        ledger_digest=state.ledger_digest,
        ledger_verified=True,
    )


class StudyLock(AbstractContextManager["StudyLock"]):
    def __init__(self, study_root: Path, *, timeout_seconds: float = 10.0) -> None:
        self.study_root = Path(study_root)
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.descriptor: int | None = None

    def __enter__(self) -> "StudyLock":
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(
                self.study_root / ".outcome-import.lock", flags, 0o600
            )
            os.fchmod(descriptor, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe lock")
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ImportConflict("study import lock is unsafe") from exc
        deadline = time.monotonic() + self.timeout_seconds
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    self.descriptor = descriptor
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ImportConflict("study import lock timed out")
                    time.sleep(0.01)
                except OSError as exc:
                    raise ImportConflict("study import lock acquisition failed") from exc
        finally:
            if not acquired:
                os.close(descriptor)

    def __exit__(self, *args: object) -> None:
        del args
        if self.descriptor is None:
            return
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _directory_identity(info: os.stat_result, label: str) -> tuple[int, int, int, int]:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ImportConflict(f"{label} must be a non-symlink directory")
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
    )


def _safe_source_component(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or len(os.fsencode(value)) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ImportConflict("staged generation name is not one safe component")
    return value


@dataclass(frozen=True)
class _PinnedTreeEntry:
    relative_path: str
    parent_relative_path: str | None
    name: str
    descriptor: int
    kind: str
    device: int
    inode: int
    owner: int
    mode: int
    link_count: int
    byte_count: int | None
    sha256: str | None
    modified_ns: int | None
    changed_ns: int | None
    children: tuple[str, ...] | None


def _pinned_object_identity(
    info: os.stat_result, *, kind: str
) -> tuple[object, ...]:
    expected_type = stat.S_IFDIR if kind == "directory" else stat.S_IFREG
    if stat.S_IFMT(info.st_mode) != expected_type:
        raise ImportConflict("pinned generation object type changed")
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_size if kind == "file" else None,
        info.st_mtime_ns if kind == "file" else None,
        info.st_ctime_ns if kind == "file" else None,
    )


def _tree_entry_identity(entry: _PinnedTreeEntry) -> tuple[object, ...]:
    return (
        entry.device,
        entry.inode,
        entry.owner,
        entry.mode,
        entry.link_count,
        entry.byte_count,
        entry.modified_ns,
        entry.changed_ns,
    )


def _descriptor_digest(descriptor: int) -> tuple[int, str]:
    hasher = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1_048_576, offset)
        if not chunk:
            break
        offset += len(chunk)
        hasher.update(chunk)
    return offset, "sha256:" + hasher.hexdigest()


class _PinnedPublicationDirectories(
    AbstractContextManager["_PinnedPublicationDirectories"]
):
    def __init__(self, *, study_root: Path, stage: Path, imports: Path) -> None:
        self.study_root = study_root
        self.stage = stage
        self.imports = imports
        self.source_parent = stage.parent
        self.source_name = _safe_source_component(stage.name)
        self.study_root_fd = -1
        self.source_parent_fd = -1
        self.stage_fd = -1
        self.imports_fd = -1
        self.study_root_identity: tuple[int, int, int, int] | None = None
        self.source_parent_identity: tuple[int, int, int, int] | None = None
        self.stage_identity: tuple[int, int, int, int] | None = None
        self.imports_identity: tuple[int, int, int, int] | None = None
        self.tree_entries: dict[str, _PinnedTreeEntry] = {}
        self.tree_owned_descriptors: list[int] = []

    @staticmethod
    def _flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

    def __enter__(self) -> "_PinnedPublicationDirectories":
        try:
            self.study_root_fd = os.open(self.study_root, self._flags())
            self.source_parent_fd = os.open(self.source_parent, self._flags())
            self.stage_fd = os.open(
                self.source_name,
                self._flags(),
                dir_fd=self.source_parent_fd,
            )
            self.imports_fd = os.open(self.imports, self._flags())
            self.study_root_identity = _directory_identity(
                os.fstat(self.study_root_fd), "study root"
            )
            self.source_parent_identity = _directory_identity(
                os.fstat(self.source_parent_fd), "staged generation parent"
            )
            self.stage_identity = _directory_identity(
                os.fstat(self.stage_fd), "staged generation"
            )
            self.imports_identity = _directory_identity(
                os.fstat(self.imports_fd), "imports directory"
            )
            self._pin_generation_tree()
            self.verify_before_move()
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *args: object) -> None:
        del args
        for descriptor in reversed(self.tree_owned_descriptors):
            os.close(descriptor)
        self.tree_owned_descriptors = []
        self.tree_entries = {}
        for name in (
            "imports_fd", "stage_fd", "source_parent_fd", "study_root_fd"
        ):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)

    def _pin_generation_tree(self) -> None:
        entries: dict[str, _PinnedTreeEntry] = {}

        def walk(
            descriptor: int,
            relative_path: str,
            parent_relative_path: str | None,
            name: str,
        ) -> None:
            if len(entries) >= 8_192:
                raise ImportConflict("pinned generation tree is too large")
            try:
                children = tuple(sorted(os.listdir(descriptor)))
            except OSError as exc:
                raise ImportConflict(
                    "pinned generation directory inventory changed"
                ) from exc
            info = os.fstat(descriptor)
            identity = _pinned_object_identity(info, kind="directory")
            entries[relative_path] = _PinnedTreeEntry(
                relative_path=relative_path,
                parent_relative_path=parent_relative_path,
                name=name,
                descriptor=descriptor,
                kind="directory",
                device=int(identity[0]),
                inode=int(identity[1]),
                owner=int(identity[2]),
                mode=int(identity[3]),
                link_count=int(identity[4]),
                byte_count=None,
                sha256=None,
                modified_ns=None,
                changed_ns=None,
                children=children,
            )
            for child_name in children:
                _safe_source_component(child_name)
                child_relative = (
                    child_name
                    if not relative_path
                    else f"{relative_path}/{child_name}"
                )
                try:
                    path_info = os.stat(
                        child_name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise ImportConflict(
                        "pinned generation child identity changed"
                    ) from exc
                if stat.S_ISDIR(path_info.st_mode):
                    child_fd = os.open(
                        child_name,
                        self._flags(),
                        dir_fd=descriptor,
                    )
                    self.tree_owned_descriptors.append(child_fd)
                    opened = os.fstat(child_fd)
                    if _pinned_object_identity(
                        opened, kind="directory"
                    ) != _pinned_object_identity(path_info, kind="directory"):
                        raise ImportConflict(
                            "pinned generation child identity changed"
                        )
                    walk(
                        child_fd,
                        child_relative,
                        relative_path,
                        child_name,
                    )
                    continue
                if not stat.S_ISREG(path_info.st_mode):
                    raise ImportConflict(
                        "pinned generation contains an unsafe object"
                    )
                child_fd = os.open(
                    child_name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                self.tree_owned_descriptors.append(child_fd)
                opened = os.fstat(child_fd)
                identity = _pinned_object_identity(opened, kind="file")
                if identity != _pinned_object_identity(path_info, kind="file"):
                    raise ImportConflict(
                        "pinned generation child identity changed"
                    )
                byte_count, digest = _descriptor_digest(child_fd)
                if byte_count != opened.st_size:
                    raise ImportConflict(
                        "pinned generation file length changed"
                    )
                entries[child_relative] = _PinnedTreeEntry(
                    relative_path=child_relative,
                    parent_relative_path=relative_path,
                    name=child_name,
                    descriptor=child_fd,
                    kind="file",
                    device=int(identity[0]),
                    inode=int(identity[1]),
                    owner=int(identity[2]),
                    mode=int(identity[3]),
                    link_count=int(identity[4]),
                    byte_count=int(identity[5]),
                    sha256=digest,
                    modified_ns=int(identity[6]),
                    changed_ns=int(identity[7]),
                    children=None,
                )

        walk(self.stage_fd, "", None, self.source_name)
        self.tree_entries = entries

    def _verify_generation_tree(self) -> None:
        if not self.tree_entries or "" not in self.tree_entries:
            raise ImportConflict("pinned generation proof is unavailable")
        for relative_path in sorted(
            self.tree_entries,
            key=lambda value: (value.count("/"), value),
        ):
            entry = self.tree_entries[relative_path]
            observed = _pinned_object_identity(
                os.fstat(entry.descriptor), kind=entry.kind
            )
            if observed != _tree_entry_identity(entry):
                raise ImportConflict(
                    f"pinned generation {relative_path or '.'} identity changed"
                )
            if entry.parent_relative_path is not None:
                parent = self.tree_entries[entry.parent_relative_path]
                try:
                    path_info = os.stat(
                        entry.name,
                        dir_fd=parent.descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise ImportConflict(
                        f"pinned generation {relative_path} identity changed"
                    ) from exc
                if _pinned_object_identity(
                    path_info, kind=entry.kind
                ) != _tree_entry_identity(entry):
                    raise ImportConflict(
                        f"pinned generation {relative_path} identity changed"
                    )
            if entry.kind == "directory":
                try:
                    children = tuple(sorted(os.listdir(entry.descriptor)))
                except OSError as exc:
                    raise ImportConflict(
                        "pinned generation directory inventory changed"
                    ) from exc
                if children != entry.children:
                    raise ImportConflict(
                        f"pinned generation {relative_path or '.'} inventory changed"
                    )
            else:
                byte_count, digest = _descriptor_digest(entry.descriptor)
                if byte_count != entry.byte_count or digest != entry.sha256:
                    raise ImportConflict(
                        f"pinned generation {relative_path} bytes changed"
                    )

    @staticmethod
    def _path_identity(path: Path, label: str) -> tuple[int, int, int, int]:
        try:
            return _directory_identity(path.lstat(), label)
        except OSError as exc:
            raise ImportConflict(f"{label} identity changed") from exc

    @staticmethod
    def _named_identity(
        parent_fd: int, name: str, label: str
    ) -> tuple[int, int, int, int]:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            return _directory_identity(info, label)
        except OSError as exc:
            raise ImportConflict(f"{label} identity changed") from exc

    @staticmethod
    def _require(
        observed: tuple[int, int, int, int],
        expected: tuple[int, int, int, int] | None,
        label: str,
    ) -> None:
        if expected is None or observed != expected:
            raise ImportConflict(f"{label} identity changed")

    def _verify_fixed_directories(self) -> None:
        checks = (
            (
                _directory_identity(os.fstat(self.study_root_fd), "study root"),
                self.study_root_identity,
                "study root",
            ),
            (
                _directory_identity(
                    os.fstat(self.source_parent_fd), "staged generation parent"
                ),
                self.source_parent_identity,
                "staged generation parent",
            ),
            (
                _directory_identity(os.fstat(self.stage_fd), "staged generation"),
                self.stage_identity,
                "staged generation",
            ),
            (
                _directory_identity(os.fstat(self.imports_fd), "imports directory"),
                self.imports_identity,
                "imports directory",
            ),
            (
                self._path_identity(self.study_root, "study root"),
                self.study_root_identity,
                "study root path",
            ),
            (
                self._path_identity(
                    self.source_parent, "staged generation parent"
                ),
                self.source_parent_identity,
                "staged generation parent path",
            ),
            (
                self._path_identity(self.imports, "imports directory"),
                self.imports_identity,
                "imports directory path",
            ),
        )
        for observed, expected, label in checks:
            self._require(observed, expected, label)

    def verify_before_move(self) -> None:
        self._verify_fixed_directories()
        self._require(
            self._named_identity(
                self.source_parent_fd,
                self.source_name,
                "staged generation",
            ),
            self.stage_identity,
            "staged generation path",
        )
        self._verify_generation_tree()

    def verify_after_move(self, target_name: str) -> None:
        self._verify_fixed_directories()
        self._require(
            self._named_identity(
                self.imports_fd, target_name, "published generation"
            ),
            self.stage_identity,
            "published generation path",
        )
        self._verify_generation_tree()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, raw: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ImportConflict("atomic file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temp.unlink(missing_ok=True)
        raise


def _write_new(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ImportConflict(f"{path.name} already exists") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ImportConflict("new file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _safe_file_bytes(path: Path, label: str) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise OSError("unsafe file")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ) != (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ):
                raise OSError("file changed while opening")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise OSError("file changed while reading")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ImportConflict(f"{label} is unsafe") from exc


def _pointer(
    envelope: Mapping[str, object],
    *,
    ledger_digest: str,
    authority: StudyAuthority,
) -> dict[str, object]:
    event = envelope["event"]
    assert isinstance(event, Mapping)
    document: dict[str, object] = {
        "schema_version": CURRENT_POINTER_VERSION,
        "study_id": envelope["study_id"],
        "import_id": event["import_id"],
        "import_digest": envelope["import_digest"],
        "ledger_digest": ledger_digest,
        "evidence_status": envelope["next_evidence_status"],
        "analytical_identity_sha256": envelope["analytical_identity_sha256"],
        "current_pointer_sha256": None,
        "pointer_hmac_sha256": None,
    }
    document["current_pointer_sha256"] = sha256_json(document)
    document["pointer_hmac_sha256"] = study_authority_hmac(
        domain=IMPORT_CURRENT_POINTER_DOMAIN,
        payload={**document, "pointer_hmac_sha256": None},
        authority=authority,
    )
    return _validate_pointer(document, authority=authority)


def _validate_pointer(value: object, *, authority: StudyAuthority) -> dict[str, object]:
    document = _closed(value, _POINTER_KEYS, "current import pointer")
    if document["schema_version"] != CURRENT_POINTER_VERSION:
        raise ImportConflict("current import pointer version is invalid")
    for key in ("study_id", "import_id"):
        _identifier_or_none(document[key], f"current pointer {key}")
    _safe_import_component(document["import_id"], "current pointer import_id")
    for key in (
        "import_digest", "ledger_digest", "analytical_identity_sha256",
        "current_pointer_sha256",
    ):
        _digest(document[key], f"current pointer {key}")
    if document["evidence_status"] not in EVIDENCE_STATUSES:
        raise ImportConflict("current pointer evidence status is invalid")
    supplied = document["current_pointer_sha256"]
    if supplied != sha256_json({
        **document,
        "current_pointer_sha256": None,
        "pointer_hmac_sha256": None,
    }):
        raise ImportConflict("current pointer self-hash is invalid")
    try:
        authenticate_study_authority_hmac(
            domain=IMPORT_CURRENT_POINTER_DOMAIN,
            payload={**document, "pointer_hmac_sha256": None},
            supplied_hmac=document["pointer_hmac_sha256"],
            authority=authority,
            label="current pointer",
        )
    except StudyAuthorityError as exc:
        raise ImportConflict("current pointer authentication failed") from exc
    return document


def _pending_transaction(
    *,
    staged: _StagedGeneration,
    old_ledger: bytes,
    envelope: Mapping[str, object],
    old_pointer: bytes | None,
    new_pointer: Mapping[str, object],
    authority: StudyAuthority,
) -> dict[str, object]:
    event_bytes = canonical_json_bytes(envelope)
    new_ledger = old_ledger + event_bytes
    pointer_bytes = canonical_json_bytes(new_pointer)
    document: dict[str, object] = {
        "schema_version": PENDING_TRANSACTION_VERSION,
        "study_id": envelope["study_id"],
        "import_id": staged.import_id,
        "import_digest": staged.import_digest,
        "old_ledger_digest": sha256_bytes(old_ledger) if old_ledger else None,
        "old_ledger_byte_count": len(old_ledger),
        "new_ledger_digest": sha256_bytes(new_ledger),
        "new_ledger_byte_count": len(new_ledger),
        "event_envelope": deepcopy(dict(envelope)),
        "event_bytes_sha256": sha256_bytes(event_bytes),
        "old_pointer_file_sha256": (
            None if old_pointer is None else sha256_bytes(old_pointer)
        ),
        "new_pointer": deepcopy(dict(new_pointer)),
        "new_pointer_file_sha256": sha256_bytes(pointer_bytes),
        "pending_sha256": None,
        "pending_hmac_sha256": None,
    }
    document["pending_sha256"] = sha256_json(document)
    document["pending_hmac_sha256"] = study_authority_hmac(
        domain=IMPORT_PENDING_TRANSACTION_DOMAIN,
        payload={**document, "pending_hmac_sha256": None},
        authority=authority,
    )
    return _validate_pending(document, authority=authority)


def _validate_pending(value: object, *, authority: StudyAuthority) -> dict[str, object]:
    document = _closed(value, _PENDING_KEYS, "pending import transaction")
    if document["schema_version"] != PENDING_TRANSACTION_VERSION:
        raise ImportConflict("pending import transaction version is invalid")
    for key in ("study_id", "import_id"):
        _identifier_or_none(document[key], f"pending {key}")
    _safe_import_component(document["import_id"], "pending import_id")
    for key in (
        "import_digest", "new_ledger_digest", "event_bytes_sha256",
        "new_pointer_file_sha256", "pending_sha256",
    ):
        _digest(document[key], f"pending {key}")
    for key in ("old_ledger_digest", "old_pointer_file_sha256"):
        _digest(document[key], f"pending {key}", nullable=True)
    for key in ("old_ledger_byte_count", "new_ledger_byte_count"):
        count = document[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ImportConflict("pending import transaction byte count is invalid")
    event_bytes = canonical_json_bytes(document["event_envelope"])
    pointer = _validate_pointer(document["new_pointer"], authority=authority)
    if (
        sha256_bytes(event_bytes) != document["event_bytes_sha256"]
        or sha256_bytes(canonical_json_bytes(pointer))
        != document["new_pointer_file_sha256"]
        or document["new_ledger_byte_count"]
        != document["old_ledger_byte_count"] + len(event_bytes)
    ):
        raise ImportConflict("pending import transaction bindings are invalid")
    supplied = document["pending_sha256"]
    if supplied != sha256_json({
        **document,
        "pending_sha256": None,
        "pending_hmac_sha256": None,
    }):
        raise ImportConflict("pending import transaction self-hash is invalid")
    try:
        authenticate_study_authority_hmac(
            domain=IMPORT_PENDING_TRANSACTION_DOMAIN,
            payload={**document, "pending_hmac_sha256": None},
            supplied_hmac=document["pending_hmac_sha256"],
            authority=authority,
            label="pending import transaction",
        )
    except StudyAuthorityError as exc:
        raise ImportConflict(
            "pending import transaction authentication failed"
        ) from exc
    return {**document, "new_pointer": pointer}


def _read_pending(
    study_root: Path, *, authority: StudyAuthority
) -> dict[str, object] | None:
    raw = _safe_file_bytes(
        study_root / ".outcome-import-pending.json", "pending import transaction"
    )
    if raw is None:
        return None
    value = _json_load(raw, "pending import transaction")
    if canonical_json_bytes(value) != raw:
        raise ImportConflict("pending import transaction bytes are not canonical")
    return _validate_pending(value, authority=authority)


def _private_file_identity(info: os.stat_result) -> tuple[int, ...]:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ImportConflict("private transaction file identity changed")
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_private_file_at(
    parent_fd: int, name: str, label: str
) -> tuple[int, bytes, tuple[int, ...]]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise ImportConflict(f"{label} is unavailable") from exc
    try:
        identity = _private_file_identity(os.fstat(descriptor))
        chunks: list[bytes] = []
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1_048_576, offset)
            if not chunk:
                break
            offset += len(chunk)
            chunks.append(chunk)
        if offset != identity[5]:
            raise ImportConflict(f"{label} length changed")
        if _private_file_identity(os.fstat(descriptor)) != identity:
            raise ImportConflict(f"{label} identity changed")
        path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _private_file_identity(path_info) != identity:
            raise ImportConflict(f"{label} path identity changed")
        return descriptor, b"".join(chunks), identity
    except BaseException:
        os.close(descriptor)
        raise


def _remove_prepared_pending(
    study_root: Path,
    *,
    expected_pending: Mapping[str, object],
    authority: StudyAuthority,
) -> None:
    checked = _read_pending(study_root, authority=authority)
    if checked != expected_pending:
        raise ImportConflict("prepared pending transaction changed")
    path = study_root / ".outcome-import-pending.json"
    path.unlink()
    _fsync_directory(study_root)


def _stable_private_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return _private_file_identity(info)[:6]


def _completion_names(import_id: object) -> tuple[str, str]:
    identifier = _safe_import_component(import_id, "completion import_id")
    return f"{identifier}.receipt.json", f"{identifier}.claim.json"


def _open_completed_directory_at(
    study_root_fd: int, *, create: bool
) -> tuple[int, tuple[int, int, int, int]] | None:
    root_identity = _directory_identity(os.fstat(study_root_fd), "study root")
    if create:
        try:
            os.mkdir(
                COMPLETED_TRANSACTIONS_DIRECTORY,
                mode=0o700,
                dir_fd=study_root_fd,
            )
            os.fsync(study_root_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ImportConflict(
                "completed transaction directory is unsafe"
            ) from exc
    try:
        descriptor = os.open(
            COMPLETED_TRANSACTIONS_DIRECTORY,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=study_root_fd,
        )
    except FileNotFoundError:
        if not create:
            return None
        raise ImportConflict("completed transaction directory is unavailable")
    except OSError as exc:
        raise ImportConflict("completed transaction directory is unsafe") from exc
    try:
        identity = _directory_identity(
            os.fstat(descriptor), "completed transaction directory"
        )
        path_identity = _directory_identity(
            os.stat(
                COMPLETED_TRANSACTIONS_DIRECTORY,
                dir_fd=study_root_fd,
                follow_symlinks=False,
            ),
            "completed transaction directory",
        )
        if (
            identity != path_identity
            or identity[0] != root_identity[0]
            or identity[2] != root_identity[2]
            or identity[3] != 0o700
        ):
            raise ImportConflict("completed transaction directory identity changed")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _write_new_at(parent_fd: int, name: str, raw: bytes, label: str) -> None:
    _safe_source_component(name)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise ImportConflict(f"{label} already exists") from exc
    except OSError as exc:
        raise ImportConflict(f"{label} cannot be created safely") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ImportConflict(f"{label} write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)


def _completion_claim(
    *,
    pending: Mapping[str, object],
    pending_raw: bytes,
    pending_identity: tuple[int, ...],
    completed_directory_identity: tuple[int, int, int, int],
    ledger_raw: bytes,
    pointer_raw: bytes,
    authority: StudyAuthority,
) -> dict[str, object]:
    receipt_name, _ = _completion_names(pending["import_id"])
    document: dict[str, object] = {
        "schema_version": COMPLETION_CLAIM_VERSION,
        "study_id": pending["study_id"],
        "import_id": pending["import_id"],
        "import_digest": pending["import_digest"],
        "receipt_name": receipt_name,
        "pending_file_sha256": sha256_bytes(pending_raw),
        "pending_device": pending_identity[0],
        "pending_inode": pending_identity[1],
        "pending_owner": pending_identity[2],
        "pending_mode": pending_identity[3],
        "pending_link_count": pending_identity[4],
        "pending_byte_count": pending_identity[5],
        "completed_directory_device": completed_directory_identity[0],
        "completed_directory_inode": completed_directory_identity[1],
        "completed_directory_owner": completed_directory_identity[2],
        "completed_directory_mode": completed_directory_identity[3],
        "ledger_digest": sha256_bytes(ledger_raw),
        "ledger_byte_count": len(ledger_raw),
        "pointer_file_sha256": sha256_bytes(pointer_raw),
        "completion_claim_sha256": None,
        "claim_hmac_sha256": None,
    }
    document["completion_claim_sha256"] = sha256_json(document)
    document["claim_hmac_sha256"] = study_authority_hmac(
        domain=IMPORT_COMPLETION_CLAIM_DOMAIN,
        payload={**document, "claim_hmac_sha256": None},
        authority=authority,
    )
    return _validate_completion_claim(document, authority=authority)


def _validate_completion_claim(
    value: object, *, authority: StudyAuthority
) -> dict[str, object]:
    document = _closed(value, _COMPLETION_CLAIM_KEYS, "completion claim")
    if document["schema_version"] != COMPLETION_CLAIM_VERSION:
        raise ImportConflict("completion claim version is invalid")
    for key in ("study_id", "import_id"):
        _identifier_or_none(document[key], f"completion claim {key}")
    receipt_name, _ = _completion_names(document["import_id"])
    if document["receipt_name"] != receipt_name:
        raise ImportConflict("completion claim receipt name is invalid")
    for key in (
        "import_digest", "pending_file_sha256", "ledger_digest",
        "pointer_file_sha256", "completion_claim_sha256",
    ):
        _digest(document[key], f"completion claim {key}")
    for key in (
        "pending_device", "pending_inode", "pending_owner", "pending_mode",
        "pending_link_count", "pending_byte_count",
        "completed_directory_device", "completed_directory_inode",
        "completed_directory_owner", "completed_directory_mode",
        "ledger_byte_count",
    ):
        number = document[key]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ImportConflict("completion claim identity is invalid")
    if (
        document["pending_mode"] != 0o600
        or document["pending_link_count"] != 1
        or document["completed_directory_mode"] != 0o700
    ):
        raise ImportConflict("completion claim private identity is invalid")
    if document["completion_claim_sha256"] != sha256_json({
        **document,
        "completion_claim_sha256": None,
        "claim_hmac_sha256": None,
    }):
        raise ImportConflict("completion claim self-hash is invalid")
    try:
        authenticate_study_authority_hmac(
            domain=IMPORT_COMPLETION_CLAIM_DOMAIN,
            payload={**document, "claim_hmac_sha256": None},
            supplied_hmac=document["claim_hmac_sha256"],
            authority=authority,
            label="completion claim",
        )
    except StudyAuthorityError as exc:
        raise ImportConflict("completion claim authentication failed") from exc
    return document


def _claim_file_identity(claim: Mapping[str, object]) -> tuple[int, ...]:
    return (
        int(claim["pending_device"]),
        int(claim["pending_inode"]),
        int(claim["pending_owner"]),
        int(claim["pending_mode"]),
        int(claim["pending_link_count"]),
        int(claim["pending_byte_count"]),
    )


def _claim_directory_identity(
    claim: Mapping[str, object]
) -> tuple[int, int, int, int]:
    return (
        int(claim["completed_directory_device"]),
        int(claim["completed_directory_inode"]),
        int(claim["completed_directory_owner"]),
        int(claim["completed_directory_mode"]),
    )


def _validate_transaction_binding(
    *,
    pending: Mapping[str, object],
    claim: Mapping[str, object],
    envelope: Mapping[str, object],
    ledger: _LedgerState,
    event_index: int,
    authority: StudyAuthority,
) -> None:
    event = envelope["event"]
    assert isinstance(event, Mapping)
    prefix_count = sum(
        len(canonical_json_bytes(item))
        for item in ledger.events[: event_index + 1]
    )
    old_count = prefix_count - len(canonical_json_bytes(envelope))
    old_digest = ledger.prefix_digests[event_index - 1] if event_index else None
    prefix_digest = ledger.prefix_digests[event_index]
    expected_pointer = _pointer(
        envelope, ledger_digest=prefix_digest, authority=authority
    )
    if (
        pending["study_id"] != envelope["study_id"]
        or pending["import_id"] != event["import_id"]
        or pending["import_digest"] != envelope["import_digest"]
        or pending["event_envelope"] != envelope
        or pending["old_ledger_digest"] != old_digest
        or pending["old_ledger_byte_count"] != old_count
        or pending["new_ledger_digest"] != prefix_digest
        or pending["new_ledger_byte_count"] != prefix_count
        or pending["new_pointer"] != expected_pointer
        or claim["study_id"] != pending["study_id"]
        or claim["import_id"] != pending["import_id"]
        or claim["import_digest"] != pending["import_digest"]
        or claim["ledger_digest"] != prefix_digest
        or claim["ledger_byte_count"] != prefix_count
        or claim["pointer_file_sha256"]
        != pending["new_pointer_file_sha256"]
    ):
        raise ImportConflict("completed transaction ledger binding is invalid")


def _validate_completed_transactions(
    study_root: Path,
    ledger: _LedgerState,
    *,
    authority: StudyAuthority,
    active_pending: Mapping[str, object] | None = None,
) -> None:
    root_fd = os.open(
        study_root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    completed_fd = -1
    try:
        opened = _open_completed_directory_at(root_fd, create=False)
        active_id = (
            None if active_pending is None else str(active_pending["import_id"])
        )
        events = {
            str(envelope["event"]["import_id"]): (index, envelope)
            for index, envelope in enumerate(ledger.events)
        }
        required_completed = set(events)
        if active_id in required_completed:
            required_completed.remove(active_id)
        if opened is None:
            if required_completed:
                raise ImportConflict("completed transaction receipt is missing")
            return
        completed_fd, completed_identity = opened
        try:
            names = set(os.listdir(completed_fd))
        except OSError as exc:
            raise ImportConflict("completed transaction inventory is unsafe") from exc
        allowed: set[str] = set()
        for identifier in required_completed:
            allowed.update(_completion_names(identifier))
        active_claim_name: str | None = None
        if active_id is not None and active_id in events:
            active_receipt_name, active_claim_name = _completion_names(active_id)
            if active_receipt_name in names:
                raise ImportConflict(
                    "pending and completed transaction records conflict"
                )
            if active_claim_name in names:
                allowed.add(active_claim_name)
        unexpected = names - allowed
        if unexpected:
            raise ImportConflict("completed transaction inventory is unexpected")
        for identifier in sorted(required_completed):
            receipt_name, claim_name = _completion_names(identifier)
            if receipt_name not in names or claim_name not in names:
                raise ImportConflict("completed transaction receipt or claim is missing")
            claim_fd, claim_raw, _ = _open_private_file_at(
                completed_fd, claim_name, "completion claim"
            )
            try:
                receipt_fd, receipt_raw, receipt_identity = _open_private_file_at(
                    completed_fd, receipt_name, "completed transaction receipt"
                )
                try:
                    claim_value = _json_load(claim_raw, "completion claim")
                    if canonical_json_bytes(claim_value) != claim_raw:
                        raise ImportConflict(
                            "completion claim bytes are not canonical"
                        )
                    claim = _validate_completion_claim(
                        claim_value, authority=authority
                    )
                    if _claim_directory_identity(claim) != completed_identity:
                        raise ImportConflict(
                            "completed transaction directory identity changed"
                        )
                    if (
                        _claim_file_identity(claim)
                        != _stable_private_file_identity(os.fstat(receipt_fd))
                        or _claim_file_identity(claim) != receipt_identity[:6]
                        or claim["pending_file_sha256"] != sha256_bytes(receipt_raw)
                    ):
                        raise ImportConflict(
                            "completed transaction receipt identity changed"
                        )
                    receipt_value = _json_load(
                        receipt_raw, "completed transaction receipt"
                    )
                    if canonical_json_bytes(receipt_value) != receipt_raw:
                        raise ImportConflict(
                            "completed transaction receipt bytes are not canonical"
                        )
                    pending = _validate_pending(
                        receipt_value, authority=authority
                    )
                    index, envelope = events[identifier]
                    _validate_transaction_binding(
                        pending=pending,
                        claim=claim,
                        envelope=envelope,
                        ledger=ledger,
                        event_index=index,
                        authority=authority,
                    )
                    generation = _validate_generation_at(
                        study_root / "imports" / identifier,
                        authority=authority,
                    )
                    if generation.import_digest != pending["import_digest"]:
                        raise ImportConflict(
                            "completed transaction generation binding is invalid"
                        )
                finally:
                    os.close(receipt_fd)
            finally:
                os.close(claim_fd)
        if active_claim_name is not None and active_claim_name in names:
            assert active_pending is not None and active_id is not None
            claim_fd, claim_raw, _ = _open_private_file_at(
                completed_fd, active_claim_name, "active completion claim"
            )
            try:
                pending_fd, pending_raw, pending_identity = _open_private_file_at(
                    root_fd,
                    ".outcome-import-pending.json",
                    "active pending transaction",
                )
                try:
                    claim_value = _json_load(
                        claim_raw, "active completion claim"
                    )
                    if canonical_json_bytes(claim_value) != claim_raw:
                        raise ImportConflict(
                            "active completion claim is not canonical"
                        )
                    claim = _validate_completion_claim(
                        claim_value, authority=authority
                    )
                    if (
                        _claim_directory_identity(claim) != completed_identity
                        or _claim_file_identity(claim) != pending_identity[:6]
                        or claim["pending_file_sha256"]
                        != sha256_bytes(pending_raw)
                    ):
                        raise ImportConflict(
                            "active completion claim identity changed"
                        )
                    index, envelope = events[active_id]
                    _validate_transaction_binding(
                        pending=active_pending,
                        claim=claim,
                        envelope=envelope,
                        ledger=ledger,
                        event_index=index,
                        authority=authority,
                    )
                finally:
                    os.close(pending_fd)
            finally:
                os.close(claim_fd)
    finally:
        if completed_fd >= 0:
            os.close(completed_fd)
        os.close(root_fd)


def _claim_completed_transaction(
    study_root: Path,
    *,
    generation_path: Path,
    staged: _StagedGeneration,
    pinned: _PinnedPublicationDirectories,
    authority: StudyAuthority,
    expected_ledger: bytes,
    expected_pointer: Mapping[str, object],
    expected_pending: Mapping[str, object],
) -> None:
    """Atomically preserve the exact authenticated pending record as history."""

    _prove_published_generation(
        generation_path=generation_path,
        staged=staged,
        pinned=pinned,
        authority=authority,
    )
    context = _publication_context(study_root, authority)
    pinned.verify_after_move(staged.import_id)
    opened: list[int] = []
    completed_fd = -1
    try:
        ledger_fd, ledger_raw, ledger_identity = _open_private_file_at(
            pinned.study_root_fd,
            "import-ledger.jsonl",
            "completed import ledger",
        )
        opened.append(ledger_fd)
        pointer_fd, pointer_raw, pointer_identity = _open_private_file_at(
            pinned.study_root_fd,
            "current-import.json",
            "completed current pointer",
        )
        opened.append(pointer_fd)
        pending_fd, pending_raw, pending_identity = _open_private_file_at(
            pinned.study_root_fd,
            ".outcome-import-pending.json",
            "completed pending transaction",
        )
        opened.append(pending_fd)
        if ledger_raw != expected_ledger:
            raise ImportConflict("completed import ledger bytes changed")
        ledger_state = _replay_ledger_bytes(
            ledger_raw, context=context, authority=authority
        )
        if (
            ledger_state.current_import_id != staged.import_id
            or ledger_state.ledger_digest != expected_pending["new_ledger_digest"]
            or len(ledger_raw) != expected_pending["new_ledger_byte_count"]
        ):
            raise ImportConflict("completed import ledger binding changed")
        pointer_value = _json_load(pointer_raw, "completed current pointer")
        if canonical_json_bytes(pointer_value) != pointer_raw:
            raise ImportConflict("completed current pointer bytes changed")
        checked_pointer = _validate_pointer(
            pointer_value, authority=authority
        )
        if (
            checked_pointer != expected_pointer
            or sha256_bytes(pointer_raw)
            != expected_pending["new_pointer_file_sha256"]
        ):
            raise ImportConflict("completed current pointer binding changed")
        pending_value = _json_load(
            pending_raw, "completed pending transaction"
        )
        if canonical_json_bytes(pending_value) != pending_raw:
            raise ImportConflict("completed pending transaction bytes changed")
        checked_pending = _validate_pending(
            pending_value, authority=authority
        )
        if checked_pending != expected_pending:
            raise ImportConflict("completed pending transaction identity changed")
        opened_completed = _open_completed_directory_at(
            pinned.study_root_fd, create=True
        )
        assert opened_completed is not None
        completed_fd, completed_identity = opened_completed
        receipt_name, claim_name = _completion_names(staged.import_id)
        try:
            os.stat(receipt_name, dir_fd=completed_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ImportConflict("completed transaction receipt already exists")
        claim = _completion_claim(
            pending=checked_pending,
            pending_raw=pending_raw,
            pending_identity=pending_identity[:6],
            completed_directory_identity=completed_identity,
            ledger_raw=ledger_raw,
            pointer_raw=pointer_raw,
            authority=authority,
        )
        claim_raw = canonical_json_bytes(claim)
        try:
            existing_claim_fd, existing_claim_raw, _ = _open_private_file_at(
                completed_fd, claim_name, "completion claim"
            )
        except ImportConflict as exc:
            try:
                os.stat(claim_name, dir_fd=completed_fd, follow_symlinks=False)
            except FileNotFoundError:
                _write_new_at(
                    completed_fd, claim_name, claim_raw, "completion claim"
                )
            else:
                raise exc
        else:
            try:
                if existing_claim_raw != claim_raw:
                    raise ImportConflict("completion claim is conflicting")
            finally:
                os.close(existing_claim_fd)
        _prove_published_generation(
            generation_path=generation_path,
            staged=staged,
            pinned=pinned,
            authority=authority,
        )
        _publication_context(study_root, authority)
        for descriptor, name, identity in (
            (ledger_fd, "import-ledger.jsonl", ledger_identity),
            (pointer_fd, "current-import.json", pointer_identity),
            (
                pending_fd,
                ".outcome-import-pending.json",
                pending_identity,
            ),
        ):
            if _private_file_identity(os.fstat(descriptor)) != identity:
                raise ImportConflict("completed transaction file changed")
            path_info = os.stat(
                name,
                dir_fd=pinned.study_root_fd,
                follow_symlinks=False,
            )
            if _private_file_identity(path_info) != identity:
                raise ImportConflict("completed transaction path changed")
        if _directory_identity(
            os.fstat(completed_fd), "completed transaction directory"
        ) != completed_identity:
            raise ImportConflict("completed transaction directory identity changed")
        if _directory_identity(
            os.stat(
                COMPLETED_TRANSACTIONS_DIRECTORY,
                dir_fd=pinned.study_root_fd,
                follow_symlinks=False,
            ),
            "completed transaction directory",
        ) != completed_identity:
            raise ImportConflict("completed transaction directory path changed")
        claim_check_fd, claim_check_raw, _ = _open_private_file_at(
            completed_fd, claim_name, "completion claim"
        )
        try:
            if claim_check_raw != claim_raw:
                raise ImportConflict("completion claim changed before publication")
        finally:
            os.close(claim_check_fd)
        try:
            os.stat(receipt_name, dir_fd=completed_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ImportConflict("completed transaction receipt already exists")
        _validate_completed_transactions(
            study_root,
            ledger_state,
            authority=authority,
            active_pending=checked_pending,
        )
        expected_inventory: set[str] = {claim_name}
        for envelope in ledger_state.events[:-1]:
            event = envelope["event"]
            assert isinstance(event, Mapping)
            expected_inventory.update(_completion_names(event["import_id"]))
        if set(os.listdir(completed_fd)) != expected_inventory:
            raise ImportConflict("completed transaction inventory changed")
        _transaction_step("after_completion_claim")
        _rename_entry_no_replace(
            pinned.study_root_fd,
            ".outcome-import-pending.json",
            completed_fd,
            receipt_name,
        )
        try:
            os.stat(
                ".outcome-import-pending.json",
                dir_fd=pinned.study_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ImportConflict("completed transaction left a pending record")
        if _stable_private_file_identity(os.fstat(pending_fd)) != pending_identity[:6]:
            raise ImportConflict("completed transaction retained identity changed")
        retained_count, retained_digest = _descriptor_digest(pending_fd)
        if (
            retained_count != len(pending_raw)
            or retained_digest != sha256_bytes(pending_raw)
        ):
            raise ImportConflict("completed transaction retained bytes changed")
        receipt_fd, receipt_raw, receipt_identity = _open_private_file_at(
            completed_fd, receipt_name, "completed transaction receipt"
        )
        try:
            if (
                receipt_raw != pending_raw
                or receipt_identity[:6] != pending_identity[:6]
                or _stable_private_file_identity(os.fstat(receipt_fd))
                != pending_identity[:6]
            ):
                raise ImportConflict(
                    "completed transaction receipt identity or bytes changed"
                )
        finally:
            os.close(receipt_fd)
        _prove_published_generation(
            generation_path=generation_path,
            staged=staged,
            pinned=pinned,
            authority=authority,
        )
        _publication_context(study_root, authority)
        _validate_completed_transactions(
            study_root, ledger_state, authority=authority
        )
    finally:
        if completed_fd >= 0:
            os.close(completed_fd)
        for descriptor in reversed(opened):
            os.close(descriptor)


def _rename_entry_no_replace(
    source_parent_fd: int,
    source_name: str,
    target_parent_fd: int,
    target_name: str,
) -> None:
    _safe_source_component(source_name)
    _safe_source_component(target_name)
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    target_bytes = os.fsencode(target_name)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            raise ImportConflict("atomic no-replace publication is unavailable")
        function.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_parent_fd,
            source_bytes,
            target_parent_fd,
            target_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise ImportConflict("atomic no-replace publication is unavailable")
        function.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_parent_fd,
            source_bytes,
            target_parent_fd,
            target_bytes,
            0x00000001,
        )
    else:
        raise ImportConflict("atomic no-replace publication is unsupported")
    if result != 0:
        number = ctypes.get_errno()
        if number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise ImportConflict("atomic no-replace destination already exists")
        error = OSError(number, os.strerror(number))
        raise ImportConflict("atomic no-replace publication failed") from error
    os.fsync(target_parent_fd)
    os.fsync(source_parent_fd)


def _rename_directory_no_replace(
    source_parent_fd: int,
    source_name: str,
    target_parent_fd: int,
    target_name: str,
) -> None:
    _safe_import_component(target_name)
    _rename_entry_no_replace(
        source_parent_fd, source_name, target_parent_fd, target_name
    )


def _ensure_imports(study_root: Path) -> Path:
    imports = study_root / "imports"
    try:
        imports.mkdir(mode=0o700, exist_ok=True)
        info = imports.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("unsafe imports")
        os.chmod(imports, 0o700)
    except OSError as exc:
        raise ImportConflict("study imports directory is unsafe") from exc
    _fsync_directory(study_root)
    return imports


def _generation_content_identity(
    generation: _StagedGeneration,
) -> tuple[object, ...]:
    return (
        generation.root_identity,
        generation.manifest,
        generation.source_manifest,
        generation.event,
        generation.handoff,
        generation.analytical_identity,
        generation.correction,
    )


def _prove_published_generation(
    *,
    generation_path: Path,
    staged: _StagedGeneration,
    pinned: _PinnedPublicationDirectories,
    authority: StudyAuthority,
) -> None:
    pinned.verify_after_move(staged.import_id)
    published = validate_complete_staged_generation(
        generation_path, authority=authority
    )
    pinned.verify_after_move(staged.import_id)
    if _generation_content_identity(published) != (
        _generation_content_identity(staged)
    ):
        raise ImportConflict("published generation changed during publication")


def _append_ledger_bytes(
    study_root: Path,
    old: bytes,
    event_bytes: bytes,
    *,
    generation_path: Path,
    staged: _StagedGeneration,
    pinned: _PinnedPublicationDirectories,
    authority: StudyAuthority,
) -> None:
    _prove_published_generation(
        generation_path=generation_path,
        staged=staged,
        pinned=pinned,
        authority=authority,
    )
    _publication_context(study_root, authority)
    pinned.verify_after_move(staged.import_id)
    descriptor = os.open(
        "import-ledger.jsonl",
        os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=pinned.study_root_fd,
    )
    try:
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ImportConflict("import ledger is unsafe")
        current = os.pread(descriptor, info.st_size, 0)
        if current != old:
            raise ImportConflict("import ledger changed during publication")
        view = memoryview(event_bytes)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ImportConflict("import ledger append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(pinned.study_root_fd)


def _append_ledger(
    study_root: Path,
    old: bytes,
    event_bytes: bytes,
    *,
    generation_path: Path,
    staged: _StagedGeneration,
    pinned: _PinnedPublicationDirectories,
    authority: StudyAuthority,
) -> None:
    """Validate pinned evidence inside the ledger-write critical section."""

    _append_ledger_bytes(
        study_root,
        old,
        event_bytes,
        generation_path=generation_path,
        staged=staged,
        pinned=pinned,
        authority=authority,
    )


def _validate_generation_at(
    path: Path, *, authority: StudyAuthority
) -> _StagedGeneration:
    return validate_complete_staged_generation(path, authority=authority)


def _verify_generations(
    study_root: Path,
    ledger: _LedgerState,
    *,
    authority: StudyAuthority,
) -> dict[str, _StagedGeneration]:
    imports = study_root / "imports"
    if not imports.exists() and not imports.is_symlink():
        if ledger.events:
            raise ImportConflict("import ledger references missing generations")
        return {}
    try:
        info = imports.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("unsafe imports")
        children = sorted(imports.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise ImportConflict("study imports directory is unsafe") from exc
    expected = {
        str(envelope["event"]["import_id"]): envelope  # type: ignore[index]
        for envelope in ledger.events
    }
    actual = {child.name for child in children}
    if actual - set(expected):
        raise ImportConflict("orphan import generation is present")
    if set(expected) - actual:
        raise ImportConflict("import ledger generation is missing")
    generations: dict[str, _StagedGeneration] = {}
    for child in children:
        generation = _validate_generation_at(child, authority=authority)
        envelope = expected[child.name]
        if (
            generation.import_id != child.name
            or generation.import_digest != envelope["import_digest"]
            or generation.analytical_identity_sha256
            != envelope["analytical_identity_sha256"]
            or generation.manifest["source_manifest_sha256"]
            != envelope["source_manifest_sha256"]
            or generation.manifest["validation_handoff_sha256"]
            != envelope["validation_handoff_sha256"]
        ):
            raise ImportConflict("immutable generation does not match its ledger event")
        generations[child.name] = generation
    for envelope in ledger.events:
        event = envelope["event"]
        assert isinstance(event, Mapping)
        generation = generations[str(event["import_id"])]
        _validate_correction_against_history(generation, ledger, generations)
    return generations


def _expected_pointers(
    ledger: _LedgerState, *, authority: StudyAuthority
) -> list[dict[str, object]]:
    return [
        _pointer(envelope, ledger_digest=digest, authority=authority)
        for envelope, digest in zip(ledger.events, ledger.prefix_digests)
    ]


def _repair_or_verify_pointer(
    study_root: Path,
    ledger: _LedgerState,
    *,
    authority: StudyAuthority,
) -> None:
    raw = _safe_file_bytes(study_root / "current-import.json", "current pointer")
    if not ledger.events:
        if raw is not None:
            raise ImportConflict("current pointer exists without an import ledger")
        return
    expected = _expected_pointers(ledger, authority=authority)
    if raw is None:
        _atomic_replace(
            study_root / "current-import.json", canonical_json_bytes(expected[-1])
        )
        return
    value = _json_load(raw, "current pointer")
    if canonical_json_bytes(value) != raw:
        raise ImportConflict("current pointer bytes are not canonical")
    pointer = _validate_pointer(value, authority=authority)
    if pointer == expected[-1]:
        return
    if pointer in expected[:-1]:
        _atomic_replace(
            study_root / "current-import.json", canonical_json_bytes(expected[-1])
        )
        return
    raise ImportConflict("current pointer authentication or binding is invalid")


def _recover_pending_locked(
    study_root: Path,
    *,
    context: PublicationAuthorityContext,
    authority: StudyAuthority,
) -> None:
    pending = _read_pending(study_root, authority=authority)
    if pending is None:
        return
    envelope = _validate_envelope(
        pending["event_envelope"], context=context, authority=authority
    )
    event_bytes = canonical_json_bytes(envelope)
    old_count = int(pending["old_ledger_byte_count"])
    new_count = int(pending["new_ledger_byte_count"])
    raw = _ledger_raw(study_root)
    old_matches = (
        len(raw) == old_count
        and (sha256_bytes(raw) if raw else None) == pending["old_ledger_digest"]
    )
    new_matches = (
        len(raw) == new_count and sha256_bytes(raw) == pending["new_ledger_digest"]
    )
    partial_matches = (
        old_count < len(raw) < new_count
        and (sha256_bytes(raw[:old_count]) if old_count else None)
        == pending["old_ledger_digest"]
        and raw[old_count:] == event_bytes[: len(raw) - old_count]
    )
    generation_path = study_root / "imports" / str(pending["import_id"])
    generation_exists = generation_path.exists() or generation_path.is_symlink()
    pointer_raw = _safe_file_bytes(
        study_root / "current-import.json", "current pointer"
    )
    old_pointer = (
        (pointer_raw is None and pending["old_pointer_file_sha256"] is None)
        or (
            pointer_raw is not None
            and sha256_bytes(pointer_raw) == pending["old_pointer_file_sha256"]
        )
    )
    new_pointer = (
        pointer_raw is not None
        and sha256_bytes(pointer_raw) == pending["new_pointer_file_sha256"]
        and _json_load(pointer_raw, "current pointer") == pending["new_pointer"]
    )
    if old_matches or partial_matches:
        completed_ledger = _replay_ledger_bytes(
            raw[:old_count], context=context, authority=authority
        )
    elif new_matches:
        completed_ledger = _replay_ledger_bytes(
            raw, context=context, authority=authority
        )
    else:
        raise ImportConflict("pending import transaction ledger is conflicting")
    _validate_completed_transactions(
        study_root,
        completed_ledger,
        authority=authority,
        active_pending=pending,
    )
    if not generation_exists and old_matches and old_pointer:
        _remove_prepared_pending(
            study_root,
            expected_pending=pending,
            authority=authority,
        )
        return
    if not generation_exists:
        raise ImportConflict("pending import transaction has ledger without generation")
    imports = study_root / "imports"
    with _PinnedPublicationDirectories(
        study_root=study_root,
        stage=generation_path,
        imports=imports,
    ) as pinned:
        generation = _validate_generation_at(
            generation_path, authority=authority
        )
        if (
            generation.root_identity != pinned.stage_identity
            or generation.import_digest != pending["import_digest"]
        ):
            raise ImportConflict("pending import generation is conflicting")
        _prove_published_generation(
            generation_path=generation_path,
            staged=generation,
            pinned=pinned,
            authority=authority,
        )
        if (old_matches or partial_matches) and old_pointer:
            append_bytes = (
                event_bytes
                if old_matches
                else event_bytes[len(raw) - old_count :]
            )
            _append_ledger_bytes(
                study_root,
                raw,
                append_bytes,
                generation_path=generation_path,
                staged=generation,
                pinned=pinned,
                authority=authority,
            )
            _prove_published_generation(
                generation_path=generation_path,
                staged=generation,
                pinned=pinned,
                authority=authority,
            )
            raw = _ledger_raw(study_root)
            new_matches = (
                len(raw) == new_count
                and sha256_bytes(raw) == pending["new_ledger_digest"]
            )
        if new_matches and old_pointer:
            _prove_published_generation(
                generation_path=generation_path,
                staged=generation,
                pinned=pinned,
                authority=authority,
            )
            _publication_context(study_root, authority)
            _atomic_replace(
                study_root / "current-import.json",
                canonical_json_bytes(pending["new_pointer"]),
            )
            _prove_published_generation(
                generation_path=generation_path,
                staged=generation,
                pinned=pinned,
                authority=authority,
            )
            pointer_raw = _safe_file_bytes(
                study_root / "current-import.json", "current pointer"
            )
            new_pointer = (
                pointer_raw is not None
                and sha256_bytes(pointer_raw)
                == pending["new_pointer_file_sha256"]
            )
        if new_matches and new_pointer:
            _claim_completed_transaction(
                study_root,
                generation_path=generation_path,
                staged=generation,
                pinned=pinned,
                authority=authority,
                expected_ledger=raw,
                expected_pointer=pending["new_pointer"],
                expected_pending=pending,
            )
            return
    raise ImportConflict("pending import transaction has an unverifiable state")


def _validate_correction_against_history(
    staged: _StagedGeneration,
    ledger: _LedgerState,
    generations: Mapping[str, _StagedGeneration],
) -> None:
    correction = staged.correction
    if correction is None:
        return
    supersedes = str(staged.manifest["supersedes_import_id"])
    prior = generations.get(supersedes)
    if prior is None:
        raise ImportConflict("correction references a missing immutable generation")
    if prior.analytical_identity_sha256 != staged.analytical_identity_sha256:
        raise ImportConflict("correction changes analytical identity")
    prior_sources = prior.source_manifest["sources"]
    replacement_sources = staged.source_manifest["sources"]
    if not isinstance(prior_sources, list) or len(prior_sources) != 1:
        raise ImportConflict("correction requires one superseded source")
    if not isinstance(replacement_sources, list) or len(replacement_sources) != 1:
        raise ImportConflict("correction requires one replacement source")
    try:
        checked = validate_correction_request(
            correction,
            trusted_correction_context={
                "superseded_import": {
                    "import_id": supersedes,
                    "source_sha256": prior_sources[0]["source_sha256"],
                },
                "replacement_source": {
                    "source_manifest_id": staged.source_manifest[
                        "source_manifest_id"
                    ],
                    "source_sha256": replacement_sources[0]["source_sha256"],
                },
            },
        )
    except (ContractError, KeyError, TypeError) as exc:
        raise ImportConflict(
            "correction source or identity binding is invalid"
        ) from exc
    if (
        checked["expected_analytical_identity_sha256"]
        != staged.analytical_identity_sha256
        or checked["correction_request_sha256"]
        != staged.manifest["correction_request_sha256"]
        or checked["supersedes_observation_ids"]
        != staged.manifest["superseded_observation_ids"]
    ):
        raise ImportConflict("correction request does not match the generation")
    prior_event = next(
        envelope for envelope in ledger.events
        if envelope["event"]["import_id"] == supersedes  # type: ignore[index]
    )
    prior_event_value = prior_event["event"]
    assert isinstance(prior_event_value, Mapping)
    prior_observations = set(prior_event_value["observation_ids"])
    if not set(checked["supersedes_observation_ids"]).issubset(prior_observations):
        raise ImportConflict("correction supersession identity is invalid")


def _idempotent_commit(
    staged: _StagedGeneration,
    ledger: _LedgerState,
    generations: Mapping[str, _StagedGeneration],
    *,
    expected_previous_ledger_digest: str | None,
) -> ImportCommit | None:
    matching = [
        (index, envelope)
        for index, envelope in enumerate(ledger.events)
        if envelope["event"]["import_id"] == staged.import_id  # type: ignore[index]
    ]
    if not matching:
        return None
    index, envelope = matching[0]
    generation = generations.get(staged.import_id)
    if (
        index != len(ledger.events) - 1
        or generation is None
        or generation.import_digest != staged.import_digest
        or envelope["import_digest"] != staged.import_digest
        or envelope["analytical_identity_sha256"]
        != staged.analytical_identity_sha256
        or envelope["previous_ledger_digest"]
        != expected_previous_ledger_digest
        or generation.manifest != staged.manifest
    ):
        raise ImportConflict("conflicting retry for immutable import identity")
    assert ledger.ledger_digest is not None
    return ImportCommit(
        import_id=staged.import_id,
        import_digest=staged.import_digest,
        generation_path=generation.root,
        ledger_digest=ledger.ledger_digest,
        analytical_identity_sha256=staged.analytical_identity_sha256,
    )


def commit_import_generation(
    *,
    study_root: Path,
    staged_generation: Path,
    expected_previous_ledger_digest: str | None,
    authority: StudyAuthority,
) -> ImportCommit:
    root = Path(study_root)
    context = _publication_context(root, authority)
    staged = validate_complete_staged_generation(
        Path(staged_generation), authority=authority
    )
    if (
        staged.manifest["study_id"] != context.study_id
        or staged.manifest["registration_id"] != context.registration_id
        or staged.manifest["registration_sha256"]
        != context.registration_sha256
        or staged.manifest["registration_receipt_sha256"]
        != context.registration_receipt_sha256
        or staged.manifest["delivery_started_at"] != context.delivery_started_at
    ):
        raise ImportConflict("staged generation does not bind the sealed study")
    with StudyLock(root):
        context = _publication_context(root, authority)
        restaged = validate_complete_staged_generation(
            Path(staged_generation), authority=authority
        )
        if restaged != staged:
            raise ImportConflict("staged generation changed before publication")
        _fsync_tree(staged.root)
        _recover_pending_locked(root, context=context, authority=authority)
        ledger = _replay_ledger_bytes(
            _ledger_raw(root), context=context, authority=authority
        )
        generations = _verify_generations(root, ledger, authority=authority)
        _validate_completed_transactions(root, ledger, authority=authority)
        retry = _idempotent_commit(
            staged,
            ledger,
            generations,
            expected_previous_ledger_digest=expected_previous_ledger_digest,
        )
        if retry is not None:
            _repair_or_verify_pointer(root, ledger, authority=authority)
            return retry
        if ledger.ledger_digest != expected_previous_ledger_digest:
            raise ImportConflict("previous ledger digest changed")
        if staged.manifest["previous_evidence_status"] != ledger.current_status:
            raise ImportConflict("staged previous evidence status is stale")
        require_monotone_status(
            ledger.current_status, str(staged.manifest["next_evidence_status"])
        )
        if (
            ledger.first_outcome_accessed_at is not None
            and staged.manifest["first_outcome_accessed_at"]
            != ledger.first_outcome_accessed_at
        ):
            raise ImportConflict("first outcome-access chronology changed")
        if (
            ledger.last_source_exported_at is not None
            and _timestamp(staged.manifest["source_exported_at"], "source export")
            < _timestamp(ledger.last_source_exported_at, "source export")
        ):
            raise ImportConflict("source-export chronology is not monotone")
        if (
            ledger.last_imported_at is not None
            and _timestamp(staged.manifest["imported_at"], "import")
            < _timestamp(ledger.last_imported_at, "import")
        ):
            raise ImportConflict("import chronology is not monotone")
        _validate_correction_against_history(staged, ledger, generations)
        envelope = _build_envelope(
            staged,
            previous_ledger_digest=ledger.ledger_digest,
            context=context,
            authority=authority,
        )
        event_bytes = canonical_json_bytes(envelope)
        new_ledger_digest = sha256_bytes(ledger.raw + event_bytes)
        pointer = _pointer(
            envelope, ledger_digest=new_ledger_digest, authority=authority
        )
        old_pointer = _safe_file_bytes(root / "current-import.json", "current pointer")
        if old_pointer is not None:
            _validate_pointer(
                _json_load(old_pointer, "current pointer"),
                authority=authority,
            )
        pending = _pending_transaction(
            staged=staged,
            old_ledger=ledger.raw,
            envelope=envelope,
            old_pointer=old_pointer,
            new_pointer=pointer,
            authority=authority,
        )
        _write_new(
            root / ".outcome-import-pending.json", canonical_json_bytes(pending)
        )
        _transaction_step("after_pending")
        _publication_context(root, authority)
        imports = _ensure_imports(root)
        with _PinnedPublicationDirectories(
            study_root=root,
            stage=staged.root,
            imports=imports,
        ) as pinned:
            prepublication = validate_complete_staged_generation(
                staged.root, authority=authority
            )
            if (
                prepublication != staged
                or prepublication.root_identity != pinned.stage_identity
            ):
                raise ImportConflict(
                    "staged generation changed before publication"
                )
            pinned.verify_before_move()
            _publication_race_step("after_final_stage_validation")
            pinned.verify_before_move()
            _rename_directory_no_replace(
                pinned.source_parent_fd,
                pinned.source_name,
                pinned.imports_fd,
                staged.import_id,
            )
            generation_path = imports / staged.import_id
            pinned.verify_after_move(staged.import_id)
            _transaction_step("after_generation")
            _publication_race_step("after_generation_before_validation")
            _prove_published_generation(
                generation_path=generation_path,
                staged=staged,
                pinned=pinned,
                authority=authority,
            )
            _append_ledger(
                root,
                ledger.raw,
                event_bytes,
                generation_path=generation_path,
                staged=staged,
                pinned=pinned,
                authority=authority,
            )
            _transaction_step("after_ledger")
            _prove_published_generation(
                generation_path=generation_path,
                staged=staged,
                pinned=pinned,
                authority=authority,
            )
            _publication_context(root, authority)
            _atomic_replace(
                root / "current-import.json", canonical_json_bytes(pointer)
            )
            _transaction_step("after_pointer")
            _prove_published_generation(
                generation_path=generation_path,
                staged=staged,
                pinned=pinned,
                authority=authority,
            )
            _publication_context(root, authority)
            _claim_completed_transaction(
                root,
                generation_path=generation_path,
                staged=staged,
                pinned=pinned,
                authority=authority,
                expected_ledger=ledger.raw + event_bytes,
                expected_pointer=pointer,
                expected_pending=pending,
            )
        _transaction_step("after_pending_completed")
        _publication_context(root, authority)
        completed_ledger = _replay_ledger_bytes(
            _ledger_raw(root), context=context, authority=authority
        )
        _validate_completed_transactions(
            root, completed_ledger, authority=authority
        )
        return ImportCommit(
            import_id=staged.import_id,
            import_digest=staged.import_digest,
            generation_path=generation_path,
            ledger_digest=new_ledger_digest,
            analytical_identity_sha256=staged.analytical_identity_sha256,
        )


def recover_study(
    *, study_root: Path, authority: StudyAuthority
) -> StudyState:
    root = Path(study_root)
    context = _publication_context(root, authority)
    with StudyLock(root):
        context = _publication_context(root, authority)
        _recover_pending_locked(root, context=context, authority=authority)
        ledger = _replay_ledger_bytes(
            _ledger_raw(root), context=context, authority=authority
        )
        _verify_generations(root, ledger, authority=authority)
        _validate_completed_transactions(root, ledger, authority=authority)
        _repair_or_verify_pointer(root, ledger, authority=authority)
        _publication_context(root, authority)
        return StudyState(
            current_import_id=ledger.current_import_id,
            current_evidence_status=ledger.current_status,
            ledger_digest=ledger.ledger_digest,
            ledger_verified=True,
        )


__all__ = [
    "ALLOWED_STATUS_TRANSITIONS",
    "ANALYTICAL_IDENTITY_VERSION",
    "CURRENT_POINTER_VERSION",
    "GENERATION_VERSION",
    "ImportCommit",
    "ImportConflict",
    "LEDGER_ENVELOPE_VERSION",
    "PENDING_TRANSACTION_VERSION",
    "StudyState",
    "analytical_identity_document",
    "commit_import_generation",
    "recover_study",
    "replay_authenticated_ledger",
    "require_monotone_status",
    "validate_complete_staged_generation",
]
