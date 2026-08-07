"""Authenticated study receipts and non-serializable import authority."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import weakref

from .common import ContractError, canonical_json_bytes, sha256_bytes, sha256_json
from .contracts import (
    AUTHENTICATED_REGISTRATION_RECEIPT_VERSION,
    IMPORT_EVENT_VERSION,
    validate_chronology,
    validate_creative_manifest,
    validate_delivery_map,
    validate_import_event,
    validate_authenticated_registration_receipt,
)


PANEL_BUILDER_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
)
if str(PANEL_BUILDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))

from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    authenticate_preregistration_design,
    load_trusted_authority_registry,
    read_protected_authority_secret,
)


STUDY_RECEIPT_DOMAIN = b"audience-ad-testing-lab/outcome-study-receipt/v1\x00"
IMPORT_EVENT_DOMAIN = b"audience-ad-testing-lab/outcome-import-event/v1\x00"
IMPORT_LEDGER_ENVELOPE_DOMAIN = (
    b"audience-ad-testing-lab/outcome-import-ledger-envelope/v1\x00"
)
IMPORT_PENDING_TRANSACTION_DOMAIN = (
    b"audience-ad-testing-lab/outcome-import-pending-transaction/v1\x00"
)
IMPORT_CURRENT_POINTER_DOMAIN = (
    b"audience-ad-testing-lab/outcome-import-current-pointer/v1\x00"
)
IMPORT_COMPLETION_CLAIM_DOMAIN = (
    b"audience-ad-testing-lab/outcome-import-completion-claim/v1\x00"
)
_PUBLICATION_HMAC_DOMAINS = frozenset({
    IMPORT_LEDGER_ENVELOPE_DOMAIN,
    IMPORT_PENDING_TRANSACTION_DOMAIN,
    IMPORT_CURRENT_POINTER_DOMAIN,
    IMPORT_COMPLETION_CLAIM_DOMAIN,
})

_ALLOWED_EVENT_TYPES = frozenset(
    {
        "registration_sealed",
        "delivery_map_sealed",
        "delivery_started",
        "outcome_not_accessed",
        "outcome_access_started",
        "reported_outcome_access",
        "source_accessed",
        "source_exported",
        "import_authenticated",
    }
)


class StudyAuthorityError(ContractError):
    """A study receipt or chronology failed its authority boundary."""


@dataclass(frozen=True)
class AuthenticatedStudy:
    study_root: Path
    registration: dict[str, object]
    delivery_map: dict[str, object]
    creative_manifest: dict[str, object]
    registration_receipt: dict[str, object]
    evidence_status: str
    ledger_digest: str | None


@dataclass(frozen=True)
class PublicationAuthorityContext:
    study_root: Path
    study_id: str
    registration_id: str
    registration_sha256: str
    registration_receipt_sha256: str
    initial_evidence_status: str
    delivery_started_at: str


class StudyAuthority:
    __slots__ = ("__weakref__",)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        raise StudyAuthorityError(
            "StudyAuthority can only be minted by authenticate_study_receipt"
        )

    def __setattr__(self, name: str, value: object) -> None:
        del self, name, value
        raise StudyAuthorityError("StudyAuthority capabilities are immutable")


@dataclass(frozen=True)
class _AuthorityState:
    study_root: Path
    study_id: str
    registration_id: str
    registration_sha256: str
    delivery_map_sha256: str
    creative_manifest_sha256: str
    receipt_sha256: str
    evidence_status: str
    ledger_digest: str | None
    authority_secret: bytes
    root_identity: tuple[int, int, int, int]
    registration_document_sha256: str
    delivery_map_document_sha256: str
    creative_manifest_document_sha256: str
    receipt_document_sha256: str


_AUTHORITY_STATES: weakref.WeakKeyDictionary[StudyAuthority, _AuthorityState] = (
    weakref.WeakKeyDictionary()
)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise StudyAuthorityError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StudyAuthorityError(f"{label} is not a timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StudyAuthorityError(f"{label} is not timezone-aware")
    return parsed


def _event_groups(
    chronology: Mapping[str, object],
) -> dict[str, list[dict[str, object]]]:
    checked = validate_chronology(chronology)
    groups: dict[str, list[dict[str, object]]] = {}
    for raw_event in checked["events"]:  # type: ignore[union-attr]
        event = dict(raw_event)
        event_type = str(event["event_type"])
        if event_type not in _ALLOWED_EVENT_TYPES:
            raise StudyAuthorityError("chronology evidence has an unknown event type")
        groups.setdefault(event_type, []).append(event)
    return groups


def derive_evidence_status(
    chronology: Mapping[str, object],
    *,
    allowed_delivery_evidence_sha256: frozenset[str] | None = None,
) -> str:
    """Derive chronology only; never decide analytical eligibility."""

    try:
        groups = _event_groups(chronology)
    except (ContractError, KeyError, TypeError, ValueError):
        return "blocked"

    for event_type, events in groups.items():
        if event_type in {
            "registration_sealed",
            "delivery_map_sealed",
            "delivery_started",
            "outcome_not_accessed",
            "outcome_access_started",
            "reported_outcome_access",
        } and len(events) > 1:
            return "blocked"

    all_events = [event for events in groups.values() for event in events]
    authority_ids = {str(event["authority_id"]) for event in all_events}
    if len(authority_ids) > 1:
        return "blocked"
    for event in all_events:
        occurred = _timestamp(event["occurred_at"], "chronology occurred_at")
        attested = _timestamp(event["attested_at"], "chronology attested_at")
        if attested < occurred:
            return "blocked"

    registration = groups.get("registration_sealed", [])
    delivery_map = groups.get("delivery_map_sealed", [])
    delivery_start = groups.get("delivery_started", [])
    outcome_not_accessed = groups.get("outcome_not_accessed", [])
    outcome_access_started = groups.get("outcome_access_started", [])
    if outcome_not_accessed and outcome_access_started:
        return "blocked"

    if delivery_start:
        if not allowed_delivery_evidence_sha256:
            return "descriptive_only"
        if any(
            str(event["evidence_source_sha256"])
            not in allowed_delivery_evidence_sha256
            for event in delivery_start
        ):
            return "blocked"

    if not registration or not delivery_map:
        return "descriptive_only"

    registered_at = _timestamp(
        registration[0]["occurred_at"], "registration chronology"
    )
    mapping_at = _timestamp(
        delivery_map[0]["occurred_at"], "delivery-map chronology"
    )
    if mapping_at < registered_at:
        return "blocked"

    outcome_access_events = (
        outcome_access_started
        + groups.get("reported_outcome_access", [])
        + groups.get("source_accessed", [])
    )
    if outcome_access_events:
        first_access = min(
            _timestamp(event["occurred_at"], "outcome-access chronology")
            for event in outcome_access_events
        )
        if mapping_at >= first_access or registered_at >= first_access:
            return "descriptive_only"

    outcome_attestation = outcome_not_accessed or outcome_access_started
    if not delivery_start or not outcome_attestation:
        return "descriptive_only"
    started_at = _timestamp(
        delivery_start[0]["occurred_at"], "delivery-start chronology"
    )
    if mapping_at > started_at or registered_at > started_at:
        return "blocked"
    attested_at = _timestamp(
        outcome_attestation[0]["occurred_at"], "outcome chronology"
    )
    if attested_at < started_at:
        return "blocked"
    return "preregistered_holdout"


def study_receipt_projection(
    *,
    registration: Mapping[str, object],
    delivery_map: Mapping[str, object],
    creative_manifest: Mapping[str, object],
    chronology: Mapping[str, object],
) -> dict[str, object]:
    raw_mappings = delivery_map.get("mappings")
    if not isinstance(raw_mappings, list):
        raise StudyAuthorityError("delivery map mappings are invalid")
    allowed_delivery_evidence = frozenset(
        str(mapping["campaign_plan_sha256"])
        for mapping in raw_mappings
        if isinstance(mapping, Mapping)
        and isinstance(mapping.get("campaign_plan_sha256"), str)
    )
    groups = _event_groups(chronology)
    registration_events = groups.get("registration_sealed", [])
    map_events = groups.get("delivery_map_sealed", [])
    approval = registration.get("approval")
    if (
        len(registration_events) != 1
        or len(map_events) != 1
        or not isinstance(approval, Mapping)
    ):
        raise StudyAuthorityError(
            "authority chronology is incomplete"
        )
    registered_at = registration.get("registered_at")
    registered_by = registration.get("registered_by")
    approved_by = approval.get("approved_by")
    registration_event = registration_events[0]
    map_event = map_events[0]
    map_projection = {
        "study_id": delivery_map.get("study_id"),
        "registration_id": registration.get("registration_id"),
        "mappings": raw_mappings,
    }
    if (
        registration_event["occurred_at"] != registered_at
        or registration_event["attested_at"] != registered_at
        or registration_event["attested_by"] != registered_by
        or registration_event["authority_id"] != approved_by
        or registration_event["evidence_source_sha256"]
        != registration.get("registration_sha256")
        or map_event["occurred_at"] != registered_at
        or map_event["attested_at"] != registered_at
        or map_event["attested_by"] != registered_by
        or map_event["authority_id"] != approved_by
        or map_event["evidence_source_sha256"] != sha256_json(map_projection)
    ):
        raise StudyAuthorityError(
            "authority chronology does not bind the exact sealed state"
        )
    return {
        "schema_version": AUTHENTICATED_REGISTRATION_RECEIPT_VERSION,
        "study_id": delivery_map["study_id"],
        "registration_id": registration["registration_id"],
        "registration_sha256": registration["registration_sha256"],
        "delivery_map_sha256": delivery_map["delivery_map_sha256"],
        "creative_manifest_sha256": creative_manifest[
            "creative_manifest_sha256"
        ],
        "chronology": dict(chronology),
        "evidence_status": derive_evidence_status(
            chronology,
            allowed_delivery_evidence_sha256=allowed_delivery_evidence,
        ),
    }


def authority_hmac(
    *, domain: bytes, payload: Mapping[str, object], secret: bytes
) -> str:
    message = domain + canonical_json_bytes(payload)
    return "sha256:" + hmac.new(secret, message, hashlib.sha256).hexdigest()


def _duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError("not a regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
            ) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_uid,
            ):
                raise OSError("file identity changed while opening")
            chunks: list[bytes] = []
            length = 0
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                length += len(chunk)
                if length > 256 * 1024 * 1024:
                    raise OSError("file exceeds authenticated input limit")
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
                raise OSError("file changed while read")
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        value = json.loads(
            raw,
            object_pairs_hook=_duplicate_free,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise StudyAuthorityError(
            f"authenticated study receipt cannot load {label}"
        ) from exc
    if not isinstance(value, dict):
        raise StudyAuthorityError(
            f"authenticated study receipt {label} must be an object"
        )
    return value


def authenticate_study_receipt(
    *,
    study_root: Path,
    authority_registry: Path,
    authority_secret_file: Path,
) -> tuple[AuthenticatedStudy, StudyAuthority]:
    """Authenticate every study identity before minting import authority."""

    root = Path(study_root)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise StudyAuthorityError(
            "authenticated study receipt root is unavailable"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise StudyAuthorityError(
            "authenticated study receipt root must be a non-symlink directory"
        )
    try:
        secret = read_protected_authority_secret(Path(authority_secret_file))
        registry = load_trusted_authority_registry(
            Path(authority_registry), authority_secret=secret
        )
        registration_raw = _load_json(
            root / "study-registration.json", "study registration"
        )
        registration, _approval = authenticate_preregistration_design(
            registration_raw, authority_registry=registry
        )
        delivery_map = validate_delivery_map(
            _load_json(root / "delivery-map.json", "delivery map")
        )
        creative_manifest = validate_creative_manifest(
            _load_json(root / "creative-manifest.json", "creative manifest")
        )
        receipt = validate_authenticated_registration_receipt(
            _load_json(root / "registration-receipt.json", "registration receipt")
        )
    except StudyAuthorityError:
        raise
    except (ContractError, KeyError, OSError, TypeError, ValueError) as exc:
        raise StudyAuthorityError(
            "authenticated study receipt validation failed"
        ) from exc

    if (
        delivery_map["registration_id"] != registration["registration_id"]
        or creative_manifest["registration_id"] != registration["registration_id"]
        or receipt["registration_id"] != registration["registration_id"]
        or receipt["study_id"] != delivery_map["study_id"]
        or receipt["registration_sha256"] != registration["registration_sha256"]
        or receipt["delivery_map_sha256"] != delivery_map["delivery_map_sha256"]
        or receipt["creative_manifest_sha256"]
        != creative_manifest["creative_manifest_sha256"]
    ):
        raise StudyAuthorityError(
            "authenticated study receipt does not bind the exact study files"
        )
    if receipt["chronology"] != delivery_map["chronology"]:
        raise StudyAuthorityError("study receipt authentication failed")

    projection = study_receipt_projection(
        registration=registration,
        delivery_map=delivery_map,
        creative_manifest=creative_manifest,
        chronology=receipt["chronology"],  # type: ignore[arg-type]
    )
    expected_receipt_sha = sha256_json(
        {
            **projection,
            "receipt_sha256": None,
            "receipt_hmac_sha256": None,
        }
    )
    expected_hmac = authority_hmac(
        domain=STUDY_RECEIPT_DOMAIN,
        payload=projection,
        secret=secret,
    )
    if (
        receipt["receipt_sha256"] != expected_receipt_sha
        or receipt["evidence_status"] != projection["evidence_status"]
        or not hmac.compare_digest(
            str(receipt["receipt_hmac_sha256"]), expected_hmac
        )
    ):
        raise StudyAuthorityError(
            "study receipt authentication failed"
        )

    ledger = root / "import-ledger.jsonl"
    ledger_digest = None
    if ledger.exists():
        try:
            ledger_stat = ledger.lstat()
            if (
                stat.S_ISLNK(ledger_stat.st_mode)
                or not stat.S_ISREG(ledger_stat.st_mode)
            ):
                raise OSError("ledger is not a regular file")
            ledger_digest = sha256_bytes(ledger.read_bytes())
        except OSError as exc:
            raise StudyAuthorityError(
                "authenticated import ledger is unavailable"
            ) from exc
    authenticated = AuthenticatedStudy(
        study_root=root,
        registration=registration,
        delivery_map=delivery_map,
        creative_manifest=creative_manifest,
        registration_receipt=receipt,
        evidence_status=str(receipt["evidence_status"]),
        ledger_digest=ledger_digest,
    )
    authority = object.__new__(StudyAuthority)
    _AUTHORITY_STATES[authority] = _AuthorityState(
        study_root=root.resolve(),
        study_id=str(receipt["study_id"]),
        registration_id=str(receipt["registration_id"]),
        registration_sha256=str(receipt["registration_sha256"]),
        delivery_map_sha256=str(receipt["delivery_map_sha256"]),
        creative_manifest_sha256=str(receipt["creative_manifest_sha256"]),
        receipt_sha256=str(receipt["receipt_sha256"]),
        evidence_status=str(receipt["evidence_status"]),
        ledger_digest=ledger_digest,
        authority_secret=secret,
        root_identity=(
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_uid,
            stat.S_IMODE(root_stat.st_mode),
        ),
        registration_document_sha256=sha256_json(registration),
        delivery_map_document_sha256=sha256_json(delivery_map),
        creative_manifest_document_sha256=sha256_json(creative_manifest),
        receipt_document_sha256=sha256_json(receipt),
    )
    return authenticated, authority


def verify_study_authority(
    authenticated: object, *, authority: StudyAuthority
) -> AuthenticatedStudy:
    """Verify one live capability still binds the exact authenticated files."""

    if type(authenticated) is not AuthenticatedStudy:
        raise StudyAuthorityError("authenticated study capability is invalid")
    if not isinstance(authority, StudyAuthority):
        raise StudyAuthorityError("StudyAuthority capability is invalid")
    state = _AUTHORITY_STATES.get(authority)
    if state is None:
        raise StudyAuthorityError("StudyAuthority capability is inactive")
    try:
        current_registration = _load_json(
            state.study_root / "study-registration.json",
            "study registration",
        )
        current_map = validate_delivery_map(
            _load_json(state.study_root / "delivery-map.json", "delivery map")
        )
        current_manifest = validate_creative_manifest(
            _load_json(
                state.study_root / "creative-manifest.json",
                "creative manifest",
            )
        )
        current_receipt = validate_authenticated_registration_receipt(
            _load_json(
                state.study_root / "registration-receipt.json",
                "registration receipt",
            )
        )
        ledger = state.study_root / "import-ledger.jsonl"
        current_ledger_digest = None
        if ledger.exists():
            ledger_stat = ledger.lstat()
            if (
                stat.S_ISLNK(ledger_stat.st_mode)
                or not stat.S_ISREG(ledger_stat.st_mode)
            ):
                raise OSError("ledger is not a regular file")
            current_ledger_digest = sha256_bytes(ledger.read_bytes())
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        raise StudyAuthorityError(
            "authenticated study files no longer validate"
        ) from exc
    supplied = (
        authenticated.study_root.resolve(),
        authenticated.registration,
        authenticated.delivery_map,
        authenticated.creative_manifest,
        authenticated.registration_receipt,
        authenticated.evidence_status,
        authenticated.ledger_digest,
    )
    expected = (
        state.study_root,
        current_registration,
        current_map,
        current_manifest,
        current_receipt,
        state.evidence_status,
        current_ledger_digest,
    )
    if supplied != expected or (
        current_registration.get("registration_id") != state.registration_id
        or current_registration.get("registration_sha256")
        != state.registration_sha256
        or current_map.get("study_id") != state.study_id
        or current_map.get("delivery_map_sha256")
        != state.delivery_map_sha256
        or current_manifest.get("creative_manifest_sha256")
        != state.creative_manifest_sha256
        or current_receipt.get("receipt_sha256") != state.receipt_sha256
        or current_receipt.get("evidence_status") != state.evidence_status
        or current_ledger_digest != state.ledger_digest
    ):
        raise StudyAuthorityError(
            "StudyAuthority does not bind the exact authenticated study"
        )
    return AuthenticatedStudy(
        study_root=state.study_root,
        registration=deepcopy(current_registration),
        delivery_map=deepcopy(current_map),
        creative_manifest=deepcopy(current_manifest),
        registration_receipt=deepcopy(current_receipt),
        evidence_status=state.evidence_status,
        ledger_digest=current_ledger_digest,
    )


def authenticate_import_event(
    event: object, *, authority: StudyAuthority
) -> dict[str, object]:
    """Authenticate one import-event envelope against a live study capability."""

    if not isinstance(authority, StudyAuthority):
        raise StudyAuthorityError("StudyAuthority capability is invalid")
    state = _AUTHORITY_STATES.get(authority)
    if state is None:
        raise StudyAuthorityError("StudyAuthority capability is inactive")
    if not isinstance(event, Mapping) or set(event) != {
        "event",
        "event_hmac_sha256",
    }:
        raise StudyAuthorityError("authenticated import event envelope is invalid")
    try:
        checked = validate_import_event(event["event"])
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        raise StudyAuthorityError(
            "authenticated import event contract is invalid"
        ) from exc
    if (
        checked["schema_version"] != IMPORT_EVENT_VERSION
        or checked["study_id"] != state.study_id
    ):
        raise StudyAuthorityError(
            "authenticated import event study identity is invalid"
        )
    expected = authority_hmac(
        domain=IMPORT_EVENT_DOMAIN,
        payload=import_event_authority_projection(
            checked,
            registration_id=state.registration_id,
            receipt_sha256=state.receipt_sha256,
        ),
        secret=state.authority_secret,
    )
    supplied = event["event_hmac_sha256"]
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        raise StudyAuthorityError("import event authentication failed")
    return checked


def import_event_authority_projection(
    event: Mapping[str, object],
    *,
    registration_id: str,
    receipt_sha256: str,
) -> dict[str, object]:
    """Bind an unchanged import event to one exact authenticated study seal."""

    return {
        "schema_version": "outcome-import-event-authentication-v1",
        "registration_id": registration_id,
        "registration_receipt_sha256": receipt_sha256,
        "event": dict(event),
    }


def publication_authority_context(
    *, study_root: Path, authority: StudyAuthority
) -> PublicationAuthorityContext:
    """Reauthenticate immutable study identity without freezing ledger bytes.

    Import publication necessarily advances the ledger after the authority was
    minted. This helper therefore rechecks the sealed study files and root
    identity while deliberately leaving ledger replay to the publication
    transaction that owns it.
    """

    if not isinstance(authority, StudyAuthority):
        raise StudyAuthorityError("StudyAuthority capability is invalid")
    state = _AUTHORITY_STATES.get(authority)
    if state is None:
        raise StudyAuthorityError("StudyAuthority capability is inactive")
    root = Path(study_root)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise StudyAuthorityError("study publication root is unavailable") from exc
    identity = (
        root_stat.st_dev,
        root_stat.st_ino,
        root_stat.st_uid,
        stat.S_IMODE(root_stat.st_mode),
    )
    if (
        stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or root.resolve() != state.study_root
        or identity != state.root_identity
    ):
        raise StudyAuthorityError(
            "StudyAuthority does not bind the exact study publication root"
        )
    try:
        registration = _load_json(
            state.study_root / "study-registration.json", "study registration"
        )
        delivery_map = validate_delivery_map(
            _load_json(state.study_root / "delivery-map.json", "delivery map")
        )
        creative_manifest = validate_creative_manifest(
            _load_json(
                state.study_root / "creative-manifest.json", "creative manifest"
            )
        )
        receipt = validate_authenticated_registration_receipt(
            _load_json(
                state.study_root / "registration-receipt.json",
                "registration receipt",
            )
        )
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        raise StudyAuthorityError(
            "authenticated study files no longer validate"
        ) from exc
    if (
        registration.get("registration_id") != state.registration_id
        or registration.get("registration_sha256") != state.registration_sha256
        or delivery_map.get("study_id") != state.study_id
        or delivery_map.get("delivery_map_sha256") != state.delivery_map_sha256
        or creative_manifest.get("creative_manifest_sha256")
        != state.creative_manifest_sha256
        or receipt.get("receipt_sha256") != state.receipt_sha256
        or receipt.get("evidence_status") != state.evidence_status
        or sha256_json(registration) != state.registration_document_sha256
        or sha256_json(delivery_map) != state.delivery_map_document_sha256
        or sha256_json(creative_manifest)
        != state.creative_manifest_document_sha256
        or sha256_json(receipt) != state.receipt_document_sha256
    ):
        raise StudyAuthorityError(
            "StudyAuthority does not bind the exact sealed study files"
        )
    chronology = receipt.get("chronology")
    if not isinstance(chronology, Mapping):
        raise StudyAuthorityError("authenticated study chronology is invalid")
    groups = _event_groups(chronology)
    delivery = groups.get("delivery_started", [])
    if len(delivery) != 1:
        raise StudyAuthorityError(
            "authenticated study delivery-start chronology is incomplete"
        )
    return PublicationAuthorityContext(
        study_root=state.study_root,
        study_id=state.study_id,
        registration_id=state.registration_id,
        registration_sha256=state.registration_sha256,
        registration_receipt_sha256=state.receipt_sha256,
        initial_evidence_status=state.evidence_status,
        delivery_started_at=str(delivery[0]["occurred_at"]),
    )


def study_authority_hmac(
    *, domain: bytes, payload: Mapping[str, object], authority: StudyAuthority
) -> str:
    """Sign a closed prep-owned transaction document with study authority."""

    if domain not in _PUBLICATION_HMAC_DOMAINS:
        raise StudyAuthorityError("study authority HMAC domain is invalid")
    if not isinstance(authority, StudyAuthority):
        raise StudyAuthorityError("StudyAuthority capability is invalid")
    state = _AUTHORITY_STATES.get(authority)
    if state is None:
        raise StudyAuthorityError("StudyAuthority capability is inactive")
    return authority_hmac(
        domain=domain,
        payload=deepcopy(dict(payload)),
        secret=state.authority_secret,
    )


def authenticate_study_authority_hmac(
    *,
    domain: bytes,
    payload: Mapping[str, object],
    supplied_hmac: object,
    authority: StudyAuthority,
    label: str,
) -> None:
    expected = study_authority_hmac(
        domain=domain, payload=payload, authority=authority
    )
    if not isinstance(supplied_hmac, str) or not hmac.compare_digest(
        supplied_hmac, expected
    ):
        raise StudyAuthorityError(f"{label} authentication failed")


__all__ = [
    "AuthenticatedStudy",
    "IMPORT_EVENT_DOMAIN",
    "IMPORT_COMPLETION_CLAIM_DOMAIN",
    "IMPORT_CURRENT_POINTER_DOMAIN",
    "IMPORT_LEDGER_ENVELOPE_DOMAIN",
    "IMPORT_PENDING_TRANSACTION_DOMAIN",
    "STUDY_RECEIPT_DOMAIN",
    "StudyAuthority",
    "StudyAuthorityError",
    "authenticate_import_event",
    "authenticate_study_receipt",
    "authority_hmac",
    "authenticate_study_authority_hmac",
    "derive_evidence_status",
    "import_event_authority_projection",
    "PublicationAuthorityContext",
    "publication_authority_context",
    "study_authority_hmac",
    "study_receipt_projection",
    "verify_study_authority",
]
