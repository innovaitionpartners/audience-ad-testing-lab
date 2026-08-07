"""Strict v2 contracts for audience research briefs and saved panels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse


RESEARCH_BRIEF_SCHEMA_VERSION = "audience-research-brief-v2"
SAVED_PANEL_SCHEMA_VERSION = "saved-audience-panel-v2"

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){7,15}(?!\w)")
_URL_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
_ACCOUNT_RE = re.compile(r"(?i)\b(?:account|contact)[\s_-]*(?:id|number)\b")
_GPS_COORD_RE = re.compile(r"(?<![\d.])[-+]?\d{1,2}\.\d{3,}\s*[,/]\s*[-+]?\d{1,3}\.\d{3,}(?!\d)")
_BLOCKED_TRAITS = (
    "race", "racial", "ethnic", "ethnicity", "religion", "religious",
    "sexual orientation", "health", "disabled", "disability", "biometric",
    "genetic", "dna", "exact geolocation", "exact coordinates", "gps",
    "lat-long", "latitude", "longitude", "financial account", "political",
    "union member", "union membership", "citizenship", "immigration",
    # Representative protected-trait values. These are scanned only in
    # operational audience-construction structures, never research prose.
    "black", "hispanic", "latino", "latina", "asian", "indigenous",
    "christian", "muslim", "jewish", "hindu", "buddhist", "gay", "lesbian",
    "bisexual", "wheelchair user", "democratic voter", "republican voter",
    "afl-cio member", "afl-cio", "labor union member", "noncitizen",
    "non-citizen", "undocumented immigrant", "immigrant status",
)
_PERSON_KEYS = {
    "person_name", "full_name", "email", "phone", "street_address",
    "account_id", "contact_id", "speaker_identity",
}

_BRIEF_KEYS = {
    "schema_version", "brief_id", "created_at", "updated_at", "status",
    "target_audience", "research_mode", "research_depth", "research_questions",
    "evidence_sources", "findings", "coverage", "segment_hypotheses",
    "evidence_gaps", "privacy_confirmation", "approval",
}
_TARGET_KEYS = {"audience", "category", "market", "geography", "buying_context", "exclusions"}
_PRIVACY_KEYS = {"confirmed", "confirmed_by", "confirmed_at", "note"}
_APPROVAL_KEYS = {"approved_for_panel_creation", "approved_by", "approved_at", "approval_note"}
_EVIDENCE_KEYS = {
    "evidence_id", "type", "source_label", "source_url", "collection_method",
    "date", "confidence", "usable_for", "permitted_uses", "limits",
}
_FINDING_KEYS = {
    "finding_id", "evidence_ids", "statement", "category", "confidence",
    "inference_boundary", "creative_implications",
}
_COVERAGE_KEYS = {
    "pain_points_challenges", "motivations_goals", "decision_criteria",
    "buying_triggers", "fears_objections", "proof_needs", "media_behaviors",
}
_HYPOTHESIS_KEYS = {
    "segment_id", "name", "origin", "finding_ids", "evidence_ids", "confidence",
    "why_it_matters_for_ad_testing",
}
_GAP_KEYS = {"gap", "impact_on_panel", "mitigation"}

_PANEL_KEYS = {
    "schema_version", "panel_id", "panel_name", "version", "created_at", "updated_at",
    "audience_scope", "persona_research", "segments", "persona_archetypes",
    "context_strata", "grounded_context_profiles", "replicate_strategy",
    "calibration_history", "refresh_conditions", "governance",
}
_SCOPE_KEYS = _TARGET_KEYS | {"scope_fingerprint"}
_PERSONA_RESEARCH_KEYS = {
    "brief_id", "mode", "status", "approved_at", "expires_at", "source_types",
    "evidence_ids", "coverage", "evidence_gaps", "source_state",
}
_SEGMENT_KEYS = {
    "segment_id", "name", "origin", "study_weight", "weighting_rule",
    "weight_source_evidence", "finding_ids", "evidence_ids", "description",
    "primary_needs", "primary_objections", "creative_implications",
}
_ARCHETYPE_KEYS = {
    "persona_archetype_id", "segment_id", "display_name", "role_context",
    "decision_context", "motivations", "anxieties", "triggers", "objections",
    "proof_needs", "finding_ids", "evidence_ids", "evidence_strength",
    "inference_boundary",
}
_STRATUM_KEYS = {
    "context_stratum_id", "segment_id", "planned_weight", "weighting_rule", "dimensions",
}
_PROVENANCE_KEYS = {"attribute", "value", "status", "source_evidence", "finding_ids"}
_DIMENSION_KEYS = {"name", "value", "status", "source_evidence", "finding_ids"}
_PROFILE_KEYS = {
    "grounded_profile_id", "segment_id", "persona_archetype_id",
    "context_stratum_id", "profile_snapshot", "context_attribute_provenance",
}
_SNAPSHOT_KEYS = {"role_context", "decision_context", "motivations", "anxieties", "proof_needs"}
_REPLICATE_KEYS = {
    "worker_unit", "shared_context_fallback_allowed", "fields_allowed_to_vary",
    "fields_never_to_invent",
}
_CALIBRATION_KEYS = {
    "date", "source_type", "mapped_run_id", "mapped_variants", "mapped_segments",
    "objective", "time_window", "data_quality", "directional_alignment", "action",
    "what_was_learned", "next_run_guidance",
}
_REFRESH_KEYS = {"review_after", "max_age_days", "triggers"}
_GOVERNANCE_KEYS = {"pii_policy", "allowed_uses", "excluded_uses", "privacy_confirmation"}

_RESEARCH_MODES = {
    "use_existing_saved_panel", "user_provided_research", "public_research",
    "crm_first_party", "hybrid_research", "provisional_no_research",
}
_RESEARCH_DEPTHS = {"quick_directional", "standard", "robust"}
_CONFIDENCE = {"high", "medium", "low"}
_EVIDENCE_TYPES = {
    "public_research", "analyst_report", "industry_report", "survey", "interview",
    "community", "job_description", "crm_aggregate", "sales_notes", "support",
    "analytics", "performance", "user_context", "other",
}
_COLLECTION_METHODS = {
    "supplied_by_user", "web_research", "desk_research", "first_party_summary",
    "derived_from_performance", "prior_saved_panel", "other",
}
_FINDING_CATEGORIES = {
    "pain_points_challenges", "motivations_goals", "questions_being_asked",
    "information_sources_influences", "decision_criteria", "buying_triggers",
    "current_approaches", "fears_objections", "emerging_trends_awareness",
    "proof_needs", "media_behaviors",
}


@dataclass(frozen=True, order=True)
class ValidationError:
    """One deterministic, machine-readable contract violation."""

    code: str
    path: str
    message: str

    @property
    def field(self) -> str:
        return self.path

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.path, "message": self.message}


class AudienceResearchValidationError(ValueError):
    """Raised by the require-valid helpers with all structured violations."""

    def __init__(self, errors: Sequence[ValidationError]):
        self.errors = tuple(errors)
        super().__init__(json.dumps([error.to_dict() for error in errors], sort_keys=True))


def _add(errors: list[ValidationError], code: str, path: str, message: str) -> None:
    errors.append(ValidationError(code, path, message))


def _object(value: Any, path: str, keys: set[str], errors: list[ValidationError]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _add(errors, "invalid_type", path, "must be an object")
        return None
    unknown = sorted(set(value) - keys, key=str)
    missing = sorted(keys - set(value))
    for key in unknown:
        _add(errors, "unknown_field", f"{path}.{key}", "field is not allowed")
    for key in missing:
        _add(errors, "missing_field", f"{path}.{key}", "field is required")
    return value


def _array(value: Any, path: str, errors: list[ValidationError], *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        _add(errors, "invalid_type", path, "must be an array")
        return []
    if nonempty and not value:
        _add(errors, "empty_array", path, "must not be empty")
    return value


def _text(value: Any, path: str, errors: list[ValidationError], *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _add(errors, "invalid_string", path, "must be a non-empty string")
        return None
    return value


def _enum(value: Any, allowed: set[str], path: str, code: str, errors: list[ValidationError]) -> str | None:
    if not isinstance(value, str) or value not in allowed:
        _add(errors, code, path, "must be one of: " + ", ".join(sorted(allowed)))
        return None
    return value


def _id(value: Any, path: str, errors: list[ValidationError]) -> str | None:
    text = _text(value, path, errors)
    if text is not None and not _ID_RE.fullmatch(text):
        _add(errors, "invalid_identifier", path, "must be a canonical lowercase ASCII identifier")
        return None
    return text


def _timestamp(value: Any, path: str, errors: list[ValidationError], *, nullable: bool = False) -> datetime | None:
    if nullable and value is None:
        return None
    text = _text(value, path, errors)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        _add(errors, "invalid_timestamp", path, "must be an ISO 8601 timestamp with timezone")
        return None


def _string_list(value: Any, path: str, errors: list[ValidationError], *, nonempty: bool = False) -> list[str]:
    items = _array(value, path, errors, nonempty=nonempty)
    result: list[str] = []
    for index, item in enumerate(items):
        text = _text(item, f"{path}[{index}]", errors)
        if text is not None:
            result.append(text)
    if len(result) != len(set(result)):
        _add(errors, "duplicate_value", path, "values must be unique")
    return result


def _references(values: Iterable[str], allowed: set[str], path: str, errors: list[ValidationError], kind: str) -> None:
    for index, value in enumerate(values):
        if value not in allowed:
            _add(errors, f"unresolved_{kind}", f"{path}[{index}]", f"{value!r} does not resolve")


def _safe_string_set(value: Any) -> set[str]:
    """Return only strings from a raw list; schema validation reports the bad shape."""

    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _safe_mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _validate_finding_evidence_binding(
    finding_refs: set[str], evidence_refs: set[str], finding_evidence: Mapping[str, set[str]],
    path: str, errors: list[ValidationError],
) -> None:
    expected: set[str] = set()
    for finding_id in finding_refs:
        expected.update(finding_evidence.get(finding_id, set()))
    if finding_refs and evidence_refs != expected:
        _add(errors, "finding_evidence_mismatch", path, "evidence IDs must exactly match the evidence cited by the selected findings")


def _provenance_signature(record: Any, *, dimension: bool) -> tuple[Any, ...] | None:
    if not isinstance(record, Mapping):
        return None
    key = "name" if dimension else "attribute"
    scalars = (record.get(key), record.get("value"), record.get("status"))
    if not all(isinstance(value, str) for value in scalars):
        return None
    return scalars + (
        tuple(sorted(_safe_string_set(record.get("source_evidence")))),
        tuple(sorted(_safe_string_set(record.get("finding_ids")))),
    )


def _unique(records: Sequence[Mapping[str, Any]], key: str, path: str, errors: list[ValidationError]) -> set[str]:
    seen: set[str] = set()
    for index, record in enumerate(records):
        value = _id(record.get(key), f"{path}[{index}].{key}", errors)
        if value in seen:
            _add(errors, "duplicate_identifier", f"{path}[{index}].{key}", f"duplicate {key}: {value}")
        if value is not None:
            seen.add(value)
    return seen


def _privacy_scan(value: Any, path: str, errors: list[ValidationError]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.casefold() in _PERSON_KEYS:
                _add(errors, "private_person_field", child_path, "person-level identifiers are prohibited")
            _privacy_scan(child, child_path, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _privacy_scan(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        if _EMAIL_RE.search(value):
            _add(errors, "pii_email", path, "email-like content is prohibited")
        phone_exempt = (
            path.endswith(
                (
                    ".date",
                    "_at",
                    "review_after",
                    "expires_at",
                    "scope_fingerprint",
                    ".version",
                    "schema_version",
                )
            )
            or path.endswith("_id")
            or "_ids[" in path
        )
        # Public source URLs often contain long report IDs and dated slugs.
        # Use a phone-shaped URL pattern there while preserving the broader
        # prose scan everywhere else.
        phone_pattern = _URL_PHONE_RE if path.endswith(".source_url") else _PHONE_RE
        if not phone_exempt and phone_pattern.search(value):
            _add(errors, "pii_phone", path, "phone-like content is prohibited")
        if _ACCOUNT_RE.search(value):
            _add(errors, "pii_account_identifier", path, "account-style identifiers are prohibited")
        if _GPS_COORD_RE.search(value):
            _add(errors, "pii_precise_geolocation", path, "precise GPS coordinate values are prohibited")


def _sensitive_trait_scan(value: Any, path: str, errors: list[ValidationError]) -> None:
    """Reject operationalized protected traits while allowing research discussion."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            _sensitive_trait_scan(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _sensitive_trait_scan(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        lowered = value.casefold()
        for trait in _BLOCKED_TRAITS:
            if re.search(rf"(?<![a-z]){re.escape(trait)}(?![a-z])", lowered):
                _add(errors, "blocked_sensitive_trait", path, f"protected trait cannot be operationalized: {trait}")
                break


def _reject_provisional_refs(value: Any, path: str, errors: list[ValidationError]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in {"evidence_ids", "finding_ids", "source_evidence", "weight_source_evidence"} and isinstance(child, list) and child:
                _add(errors, "provisional_research_reference", child_path, "provisional_no_research cannot contain research references")
            _reject_provisional_refs(child, child_path, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_provisional_refs(child, f"{path}[{index}]", errors)


def _normalized_scope_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", value).strip().split()).casefold()


def compute_scope_fingerprint(scope: Any) -> str:
    """Return the canonical fingerprint for the five audience-scope dimensions."""

    safe_scope = scope if isinstance(scope, Mapping) else {}
    values = [_normalized_scope_value(safe_scope.get(key)) for key in ("audience", "category", "market", "geography", "buying_context")]
    raw = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate_research_brief(payload: Mapping[str, Any]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    brief = _object(payload, "$", _BRIEF_KEYS, errors)
    if brief is None:
        return errors
    if brief.get("schema_version") != RESEARCH_BRIEF_SCHEMA_VERSION:
        _add(errors, "invalid_schema_version", "$.schema_version", f"must equal {RESEARCH_BRIEF_SCHEMA_VERSION}")
    _id(brief.get("brief_id"), "$.brief_id", errors)
    created = _timestamp(brief.get("created_at"), "$.created_at", errors)
    updated = _timestamp(brief.get("updated_at"), "$.updated_at", errors)
    if created and updated and updated < created:
        _add(errors, "invalid_time_order", "$.updated_at", "must not precede created_at")
    status = _enum(brief.get("status"), {"approved", "provisional_no_research"}, "$.status", "invalid_status", errors)

    target = _object(brief.get("target_audience"), "$.target_audience", _TARGET_KEYS, errors)
    if target:
        for key in _TARGET_KEYS - {"exclusions"}:
            _text(target.get(key), f"$.target_audience.{key}", errors)
        _string_list(target.get("exclusions"), "$.target_audience.exclusions", errors)
    mode = _enum(brief.get("research_mode"), _RESEARCH_MODES, "$.research_mode", "invalid_research_mode", errors)
    _enum(brief.get("research_depth"), _RESEARCH_DEPTHS, "$.research_depth", "invalid_research_depth", errors)
    if status == "provisional_no_research" and mode != "provisional_no_research":
        _add(errors, "provisional_mode_mismatch", "$.research_mode", "provisional status requires provisional_no_research mode")
    if status == "approved" and mode == "provisional_no_research":
        _add(errors, "approved_mode_mismatch", "$.research_mode", "approved status cannot use provisional_no_research mode")
    _string_list(brief.get("research_questions"), "$.research_questions", errors, nonempty=status == "approved")

    evidence_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(brief.get("evidence_sources"), "$.evidence_sources", errors, nonempty=status == "approved")):
        record = _object(item, f"$.evidence_sources[{index}]", _EVIDENCE_KEYS, errors)
        if not record:
            continue
        evidence_records.append(record)
        _enum(record.get("type"), _EVIDENCE_TYPES, f"$.evidence_sources[{index}].type", "invalid_evidence_type", errors)
        _enum(record.get("collection_method"), _COLLECTION_METHODS, f"$.evidence_sources[{index}].collection_method", "invalid_collection_method", errors)
        _enum(record.get("confidence"), _CONFIDENCE, f"$.evidence_sources[{index}].confidence", "invalid_confidence", errors)
        for key in ("source_label", "date", "limits"):
            _text(record.get(key), f"$.evidence_sources[{index}].{key}", errors)
        url = record.get("source_url")
        if url is not None:
            text = _text(url, f"$.evidence_sources[{index}].source_url", errors)
            if text and (urlparse(text).scheme not in {"http", "https"} or not urlparse(text).netloc):
                _add(errors, "invalid_source_url", f"$.evidence_sources[{index}].source_url", "must be null or an HTTP/HTTPS URL")
        _string_list(record.get("usable_for"), f"$.evidence_sources[{index}].usable_for", errors)
        _string_list(record.get("permitted_uses"), f"$.evidence_sources[{index}].permitted_uses", errors, nonempty=True)
    evidence_ids = _unique(evidence_records, "evidence_id", "$.evidence_sources", errors)

    finding_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(brief.get("findings"), "$.findings", errors, nonempty=status == "approved")):
        record = _object(item, f"$.findings[{index}]", _FINDING_KEYS, errors)
        if not record:
            continue
        finding_records.append(record)
        refs = _string_list(record.get("evidence_ids"), f"$.findings[{index}].evidence_ids", errors, nonempty=True)
        _references(refs, evidence_ids, f"$.findings[{index}].evidence_ids", errors, "evidence")
        for key in ("statement", "inference_boundary"):
            _text(record.get(key), f"$.findings[{index}].{key}", errors)
        _enum(record.get("category"), _FINDING_CATEGORIES, f"$.findings[{index}].category", "invalid_finding_category", errors)
        _enum(record.get("confidence"), _CONFIDENCE, f"$.findings[{index}].confidence", "invalid_confidence", errors)
        _string_list(record.get("creative_implications"), f"$.findings[{index}].creative_implications", errors)
    finding_ids = _unique(finding_records, "finding_id", "$.findings", errors)
    finding_evidence = {
        record.get("finding_id"): _safe_string_set(record.get("evidence_ids"))
        for record in finding_records if isinstance(record.get("finding_id"), str)
    }

    coverage = _object(brief.get("coverage"), "$.coverage", _COVERAGE_KEYS, errors)
    if coverage:
        for key in _COVERAGE_KEYS:
            if not isinstance(coverage.get(key), str) or coverage.get(key) not in {"strong", "thin", "empty"}:
                _add(errors, "invalid_coverage", f"$.coverage.{key}", "must be strong, thin, or empty")
            elif status == "provisional_no_research" and coverage.get(key) != "empty":
                _add(errors, "provisional_coverage_mismatch", f"$.coverage.{key}", "provisional_no_research coverage must be empty")

    hypotheses: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(brief.get("segment_hypotheses"), "$.segment_hypotheses", errors, nonempty=True)):
        record = _object(item, f"$.segment_hypotheses[{index}]", _HYPOTHESIS_KEYS, errors)
        if not record:
            continue
        hypotheses.append(record)
        origin = record.get("origin")
        if not isinstance(origin, str) or origin not in {"research_derived", "user_proposed_research_validated", "provisional_user_defined"}:
            _add(errors, "invalid_segment_origin", f"$.segment_hypotheses[{index}].origin", "origin is not supported")
        if status == "approved" and origin == "provisional_user_defined":
            _add(errors, "origin_status_mismatch", f"$.segment_hypotheses[{index}].origin", "approved research cannot use provisional_user_defined")
        if status == "provisional_no_research" and origin != "provisional_user_defined":
            _add(errors, "origin_status_mismatch", f"$.segment_hypotheses[{index}].origin", "provisional research requires provisional_user_defined")
        frefs = _string_list(record.get("finding_ids"), f"$.segment_hypotheses[{index}].finding_ids", errors, nonempty=status == "approved")
        erefs = _string_list(record.get("evidence_ids"), f"$.segment_hypotheses[{index}].evidence_ids", errors, nonempty=status == "approved")
        _references(frefs, finding_ids, f"$.segment_hypotheses[{index}].finding_ids", errors, "finding")
        _references(erefs, evidence_ids, f"$.segment_hypotheses[{index}].evidence_ids", errors, "evidence")
        _validate_finding_evidence_binding(set(frefs), set(erefs), finding_evidence, f"$.segment_hypotheses[{index}].evidence_ids", errors)
        for key in ("name", "why_it_matters_for_ad_testing"):
            _text(record.get(key), f"$.segment_hypotheses[{index}].{key}", errors)
        _enum(record.get("confidence"), _CONFIDENCE, f"$.segment_hypotheses[{index}].confidence", "invalid_confidence", errors)
        if status == "provisional_no_research" and record.get("confidence") != "low":
            _add(errors, "provisional_confidence_mismatch", f"$.segment_hypotheses[{index}].confidence", "provisional hypotheses must use low confidence")
    _unique(hypotheses, "segment_id", "$.segment_hypotheses", errors)

    for index, item in enumerate(_array(brief.get("evidence_gaps"), "$.evidence_gaps", errors)):
        record = _object(item, f"$.evidence_gaps[{index}]", _GAP_KEYS, errors)
        if record:
            for key in _GAP_KEYS:
                _text(record.get(key), f"$.evidence_gaps[{index}].{key}", errors)

    privacy = _object(brief.get("privacy_confirmation"), "$.privacy_confirmation", _PRIVACY_KEYS, errors)
    if privacy:
        if privacy.get("confirmed") is not True:
            _add(errors, "privacy_not_confirmed", "$.privacy_confirmation.confirmed", "must be true")
        _text(privacy.get("confirmed_by"), "$.privacy_confirmation.confirmed_by", errors)
        _timestamp(privacy.get("confirmed_at"), "$.privacy_confirmation.confirmed_at", errors)
        _text(privacy.get("note"), "$.privacy_confirmation.note", errors, allow_empty=True)
    approval = _object(brief.get("approval"), "$.approval", _APPROVAL_KEYS, errors)
    if approval:
        if approval.get("approved_for_panel_creation") is not True:
            _add(errors, "brief_not_approved", "$.approval.approved_for_panel_creation", "must be true, including explicit provisional acceptance")
        _text(approval.get("approved_by"), "$.approval.approved_by", errors)
        _timestamp(approval.get("approved_at"), "$.approval.approved_at", errors)
        _text(approval.get("approval_note"), "$.approval.approval_note", errors, allow_empty=True)
    if status == "provisional_no_research":
        for key in ("research_questions", "evidence_sources", "findings"):
            value = brief.get(key)
            if isinstance(value, list) and value:
                _add(errors, "provisional_research_content", f"$.{key}", "provisional_no_research requires an empty array")
        _reject_provisional_refs(brief, "$", errors)
    _sensitive_trait_scan(brief.get("target_audience"), "$.target_audience", errors)
    _sensitive_trait_scan(brief.get("segment_hypotheses"), "$.segment_hypotheses", errors)
    _privacy_scan(brief, "$", errors)
    return sorted(set(errors))


def _validate_provenance(record: Mapping[str, Any], path: str, evidence_ids: set[str], finding_ids: set[str], errors: list[ValidationError], *, dimension: bool = False, require_refs: bool = True) -> None:
    obj = _object(record, path, _DIMENSION_KEYS if dimension else _PROVENANCE_KEYS, errors)
    if not obj:
        return
    attribute_key = "name" if dimension else "attribute"
    _text(obj.get(attribute_key), f"{path}.{attribute_key}", errors)
    _text(obj.get("value"), f"{path}.value", errors)
    if not isinstance(obj.get("status"), str) or obj.get("status") not in {"observed", "estimated", "experimental"}:
        _add(errors, "invalid_provenance_status", f"{path}.status", "must be observed, estimated, or experimental")
    erefs = _string_list(obj.get("source_evidence"), f"{path}.source_evidence", errors, nonempty=require_refs)
    frefs = _string_list(obj.get("finding_ids"), f"{path}.finding_ids", errors, nonempty=require_refs)
    if not require_refs and obj.get("status") != "experimental":
        _add(errors, "provisional_provenance_status", f"{path}.status", "provisional provenance without sources must be experimental")
    _references(erefs, evidence_ids, f"{path}.source_evidence", errors, "evidence")
    _references(frefs, finding_ids, f"{path}.finding_ids", errors, "finding")


def validate_saved_panel(payload: Mapping[str, Any], brief: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> list[ValidationError]:
    errors: list[ValidationError] = []
    panel = _object(payload, "$", _PANEL_KEYS, errors)
    if panel is None:
        return errors
    if panel.get("schema_version") != SAVED_PANEL_SCHEMA_VERSION:
        _add(errors, "invalid_schema_version", "$.schema_version", f"must equal {SAVED_PANEL_SCHEMA_VERSION}")
    _id(panel.get("panel_id"), "$.panel_id", errors)
    _text(panel.get("panel_name"), "$.panel_name", errors)
    version = panel.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        _add(errors, "invalid_version", "$.version", "must be canonical semantic version X.Y.Z")
    created = _timestamp(panel.get("created_at"), "$.created_at", errors)
    updated = _timestamp(panel.get("updated_at"), "$.updated_at", errors)
    if created and updated and updated < created:
        _add(errors, "invalid_time_order", "$.updated_at", "must not precede created_at")

    scope = _object(panel.get("audience_scope"), "$.audience_scope", _SCOPE_KEYS, errors)
    if scope:
        for key in _TARGET_KEYS - {"exclusions"}:
            _text(scope.get(key), f"$.audience_scope.{key}", errors)
        _string_list(scope.get("exclusions"), "$.audience_scope.exclusions", errors)
        fingerprint = scope.get("scope_fingerprint")
        if not isinstance(fingerprint, str) or not _HASH_RE.fullmatch(fingerprint):
            _add(errors, "invalid_scope_fingerprint", "$.audience_scope.scope_fingerprint", "must be a lowercase SHA-256 value")
        elif fingerprint != compute_scope_fingerprint(scope):
            _add(errors, "scope_fingerprint_mismatch", "$.audience_scope.scope_fingerprint", "does not match the canonical audience scope")

    persona = _object(panel.get("persona_research"), "$.persona_research", _PERSONA_RESEARCH_KEYS, errors)
    status = persona.get("status") if persona else None
    if persona:
        _id(persona.get("brief_id"), "$.persona_research.brief_id", errors)
        mode = _enum(persona.get("mode"), _RESEARCH_MODES, "$.persona_research.mode", "invalid_research_mode", errors)
        if not isinstance(status, str) or status not in {"approved", "provisional_no_research"}:
            _add(errors, "invalid_status", "$.persona_research.status", "must be approved or provisional_no_research")
        if status == "provisional_no_research" and mode != "provisional_no_research":
            _add(errors, "provisional_mode_mismatch", "$.persona_research.mode", "provisional status requires provisional_no_research mode")
        if status == "approved" and mode == "provisional_no_research":
            _add(errors, "approved_mode_mismatch", "$.persona_research.mode", "approved status cannot use provisional_no_research mode")
        _timestamp(persona.get("approved_at"), "$.persona_research.approved_at", errors)
        expires = _timestamp(persona.get("expires_at"), "$.persona_research.expires_at", errors, nullable=True)
        source_types = _string_list(persona.get("source_types"), "$.persona_research.source_types", errors)
        _string_list(persona.get("evidence_ids"), "$.persona_research.evidence_ids", errors)
        panel_coverage = _object(persona.get("coverage"), "$.persona_research.coverage", _COVERAGE_KEYS, errors)
        if panel_coverage:
            for key in _COVERAGE_KEYS:
                if not isinstance(panel_coverage.get(key), str) or panel_coverage.get(key) not in {"strong", "thin", "empty"}:
                    _add(errors, "invalid_coverage", f"$.persona_research.coverage.{key}", "must be strong, thin, or empty")
                elif status == "provisional_no_research" and panel_coverage.get(key) != "empty":
                    _add(errors, "provisional_coverage_mismatch", f"$.persona_research.coverage.{key}", "provisional_no_research coverage must be empty")
        for index, item in enumerate(_array(persona.get("evidence_gaps"), "$.persona_research.evidence_gaps", errors)):
            record = _object(item, f"$.persona_research.evidence_gaps[{index}]", _GAP_KEYS, errors)
            if record:
                for key in _GAP_KEYS:
                    _text(record.get(key), f"$.persona_research.evidence_gaps[{index}].{key}", errors)
        source_state = persona.get("source_state")
        if not isinstance(source_state, str) or source_state not in {"documented_sources", "no_research_sources"}:
            _add(errors, "invalid_source_state", "$.persona_research.source_state", "must be documented_sources or no_research_sources")
        if status == "approved":
            if expires is not None:
                _add(errors, "approved_panel_expiry", "$.persona_research.expires_at", "approved research-backed panels require null")
            if source_state != "documented_sources" or not source_types:
                _add(errors, "approved_sources_required", "$.persona_research.source_state", "approved research-backed panels require documented sources")
        if status == "provisional_no_research":
            if source_state != "no_research_sources" or source_types:
                _add(errors, "provisional_source_mismatch", "$.persona_research.source_state", "provisional panels cannot claim research sources")
            if _safe_string_set(persona.get("evidence_ids")):
                _add(errors, "provisional_evidence_mismatch", "$.persona_research.evidence_ids", "provisional panels cannot claim evidence IDs")
            if created and expires:
                if expires <= created or expires > created + timedelta(days=30):
                    _add(errors, "invalid_provisional_expiry", "$.persona_research.expires_at", "must be after creation and no more than 30 days later")
                current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
                if expires <= current:
                    _add(errors, "provisional_expired", "$.persona_research.expires_at", "provisional panel has expired")
            elif expires is None:
                _add(errors, "provisional_expiry_required", "$.persona_research.expires_at", "provisional panels require an expiry")

    raw_brief = brief if isinstance(brief, Mapping) else {}
    brief_evidence = {r.get("evidence_id") for r in _safe_mapping_list(raw_brief.get("evidence_sources")) if isinstance(r.get("evidence_id"), str)}
    brief_findings = {r.get("finding_id") for r in _safe_mapping_list(raw_brief.get("findings")) if isinstance(r.get("finding_id"), str)}
    finding_evidence = {
        record.get("finding_id"): _safe_string_set(record.get("evidence_ids"))
        for record in _safe_mapping_list(raw_brief.get("findings")) if isinstance(record.get("finding_id"), str)
    }
    evidence_ids = brief_evidence or _safe_string_set(persona.get("evidence_ids") if persona else None)
    finding_ids = brief_findings

    segment_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(panel.get("segments"), "$.segments", errors, nonempty=True)):
        record = _object(item, f"$.segments[{index}]", _SEGMENT_KEYS, errors)
        if not record:
            continue
        segment_records.append(record)
        origin = record.get("origin")
        if not isinstance(origin, str) or origin not in {"research_derived", "user_proposed_research_validated", "provisional_user_defined"}:
            _add(errors, "invalid_segment_origin", f"$.segments[{index}].origin", "origin is not supported")
        if status == "approved" and origin == "provisional_user_defined" or status == "provisional_no_research" and origin != "provisional_user_defined":
            _add(errors, "origin_status_mismatch", f"$.segments[{index}].origin", "segment origin does not match panel status")
        weight = record.get("study_weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            _add(errors, "invalid_study_weight", f"$.segments[{index}].study_weight", "must be a positive number")
        rule = _text(record.get("weighting_rule"), f"$.segments[{index}].weighting_rule", errors)
        wrefs = _string_list(record.get("weight_source_evidence"), f"$.segments[{index}].weight_source_evidence", errors)
        if not wrefs and rule != "planning_allocation":
            _add(errors, "unsupported_weight_provenance", f"$.segments[{index}].weighting_rule", "weights without source evidence must use planning_allocation")
        _references(wrefs, evidence_ids, f"$.segments[{index}].weight_source_evidence", errors, "evidence")
        frefs = _string_list(record.get("finding_ids"), f"$.segments[{index}].finding_ids", errors, nonempty=status == "approved")
        erefs = _string_list(record.get("evidence_ids"), f"$.segments[{index}].evidence_ids", errors, nonempty=status == "approved")
        _references(frefs, finding_ids, f"$.segments[{index}].finding_ids", errors, "finding")
        _references(erefs, evidence_ids, f"$.segments[{index}].evidence_ids", errors, "evidence")
        _validate_finding_evidence_binding(set(frefs), set(erefs), finding_evidence, f"$.segments[{index}].evidence_ids", errors)
        for key in ("name", "description"):
            _text(record.get(key), f"$.segments[{index}].{key}", errors)
        for key in ("primary_needs", "primary_objections", "creative_implications"):
            _string_list(record.get(key), f"$.segments[{index}].{key}", errors)
    segment_ids = _unique(segment_records, "segment_id", "$.segments", errors)

    archetype_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(panel.get("persona_archetypes"), "$.persona_archetypes", errors, nonempty=True)):
        record = _object(item, f"$.persona_archetypes[{index}]", _ARCHETYPE_KEYS, errors)
        if not record:
            continue
        archetype_records.append(record)
        if not isinstance(record.get("segment_id"), str) or record.get("segment_id") not in segment_ids:
            _add(errors, "unresolved_segment", f"$.persona_archetypes[{index}].segment_id", "segment does not resolve")
        for key in ("display_name", "role_context", "decision_context", "evidence_strength", "inference_boundary"):
            _text(record.get(key), f"$.persona_archetypes[{index}].{key}", errors)
        traits = []
        for key in ("motivations", "anxieties", "triggers", "objections", "proof_needs"):
            traits += _string_list(record.get(key), f"$.persona_archetypes[{index}].{key}", errors)
        frefs = _string_list(record.get("finding_ids"), f"$.persona_archetypes[{index}].finding_ids", errors, nonempty=bool(traits) and status == "approved")
        erefs = _string_list(record.get("evidence_ids"), f"$.persona_archetypes[{index}].evidence_ids", errors, nonempty=bool(traits) and status == "approved")
        _references(frefs, finding_ids, f"$.persona_archetypes[{index}].finding_ids", errors, "finding")
        _references(erefs, evidence_ids, f"$.persona_archetypes[{index}].evidence_ids", errors, "evidence")
        _validate_finding_evidence_binding(set(frefs), set(erefs), finding_evidence, f"$.persona_archetypes[{index}].evidence_ids", errors)
        _enum(record.get("evidence_strength"), _CONFIDENCE, f"$.persona_archetypes[{index}].evidence_strength", "invalid_confidence", errors)
        if status == "provisional_no_research" and record.get("evidence_strength") != "low":
            _add(errors, "provisional_confidence_mismatch", f"$.persona_archetypes[{index}].evidence_strength", "provisional archetypes must use low evidence strength")
    archetype_ids = _unique(archetype_records, "persona_archetype_id", "$.persona_archetypes", errors)
    archetypes = {r.get("persona_archetype_id"): r for r in archetype_records if isinstance(r.get("persona_archetype_id"), str)}

    stratum_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(panel.get("context_strata"), "$.context_strata", errors, nonempty=True)):
        record = _object(item, f"$.context_strata[{index}]", _STRATUM_KEYS, errors)
        if not record:
            continue
        stratum_records.append(record)
        if not isinstance(record.get("segment_id"), str) or record.get("segment_id") not in segment_ids:
            _add(errors, "unresolved_segment", f"$.context_strata[{index}].segment_id", "segment does not resolve")
        weight = record.get("planned_weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            _add(errors, "invalid_planned_weight", f"$.context_strata[{index}].planned_weight", "must be a positive number")
        _text(record.get("weighting_rule"), f"$.context_strata[{index}].weighting_rule", errors)
        dimensions = _array(record.get("dimensions"), f"$.context_strata[{index}].dimensions", errors, nonempty=True)
        dimension_names = [dimension.get("name") for dimension in dimensions if isinstance(dimension, Mapping) and isinstance(dimension.get("name"), str)]
        if len(dimension_names) != len(set(dimension_names)):
            _add(errors, "duplicate_provenance", f"$.context_strata[{index}].dimensions", "dimension names must be unique")
        for dindex, dimension in enumerate(dimensions):
            if isinstance(dimension, Mapping):
                _validate_provenance(dimension, f"$.context_strata[{index}].dimensions[{dindex}]", evidence_ids, finding_ids, errors, dimension=True, require_refs=status == "approved")
                _validate_finding_evidence_binding(
                    _safe_string_set(dimension.get("finding_ids")), _safe_string_set(dimension.get("source_evidence")), finding_evidence,
                    f"$.context_strata[{index}].dimensions[{dindex}].source_evidence", errors,
                )
            else:
                _add(errors, "invalid_type", f"$.context_strata[{index}].dimensions[{dindex}]", "must be an object")
    stratum_ids = _unique(stratum_records, "context_stratum_id", "$.context_strata", errors)
    strata = {r.get("context_stratum_id"): r for r in stratum_records if isinstance(r.get("context_stratum_id"), str)}

    profile_records: list[Mapping[str, Any]] = []
    for index, item in enumerate(_array(panel.get("grounded_context_profiles"), "$.grounded_context_profiles", errors, nonempty=True)):
        record = _object(item, f"$.grounded_context_profiles[{index}]", _PROFILE_KEYS, errors)
        if not record:
            continue
        profile_records.append(record)
        segment_id = record.get("segment_id") if isinstance(record.get("segment_id"), str) else None
        archetype_id = record.get("persona_archetype_id") if isinstance(record.get("persona_archetype_id"), str) else None
        stratum_id = record.get("context_stratum_id") if isinstance(record.get("context_stratum_id"), str) else None
        archetype = archetypes.get(archetype_id)
        stratum = strata.get(stratum_id)
        if archetype_id not in archetype_ids:
            _add(errors, "unresolved_archetype", f"$.grounded_context_profiles[{index}].persona_archetype_id", "archetype does not resolve")
        if stratum_id not in stratum_ids:
            _add(errors, "unresolved_context_stratum", f"$.grounded_context_profiles[{index}].context_stratum_id", "context stratum does not resolve")
        if segment_id not in segment_ids:
            _add(errors, "unresolved_segment", f"$.grounded_context_profiles[{index}].segment_id", "segment does not resolve")
        if archetype and archetype.get("segment_id") != segment_id or stratum and stratum.get("segment_id") != segment_id:
            _add(errors, "unsupported_profile_combination", f"$.grounded_context_profiles[{index}]", "segment, archetype, and stratum must belong to the same explicit segment")
        snapshot = _object(record.get("profile_snapshot"), f"$.grounded_context_profiles[{index}].profile_snapshot", _SNAPSHOT_KEYS, errors)
        if snapshot:
            for key in ("role_context", "decision_context"):
                _text(snapshot.get(key), f"$.grounded_context_profiles[{index}].profile_snapshot.{key}", errors)
                if archetype and snapshot.get(key) != archetype.get(key):
                    _add(errors, "unsupported_profile_variation", f"$.grounded_context_profiles[{index}].profile_snapshot.{key}", "must equal the selected archetype")
            for key in ("motivations", "anxieties", "proof_needs"):
                vals = _string_list(
                    snapshot.get(key),
                    f"$.grounded_context_profiles[{index}].profile_snapshot.{key}",
                    errors,
                    nonempty=status == "approved",
                )
                if archetype:
                    unsupported = sorted(set(vals) - _safe_string_set(archetype.get(key)))
                    if unsupported:
                        _add(errors, "unsupported_profile_variation", f"$.grounded_context_profiles[{index}].profile_snapshot.{key}", "values must be grounded in the selected archetype")
        provenance = _array(record.get("context_attribute_provenance"), f"$.grounded_context_profiles[{index}].context_attribute_provenance", errors, nonempty=True)
        provenance_names = [prov.get("attribute") for prov in provenance if isinstance(prov, Mapping) and isinstance(prov.get("attribute"), str)]
        if len(provenance_names) != len(set(provenance_names)):
            _add(errors, "duplicate_provenance", f"$.grounded_context_profiles[{index}].context_attribute_provenance", "provenance attribute names must be unique")
        for pindex, prov in enumerate(provenance):
            if isinstance(prov, Mapping):
                _validate_provenance(prov, f"$.grounded_context_profiles[{index}].context_attribute_provenance[{pindex}]", evidence_ids, finding_ids, errors, require_refs=status == "approved")
                _validate_finding_evidence_binding(
                    _safe_string_set(prov.get("finding_ids")), _safe_string_set(prov.get("source_evidence")), finding_evidence,
                    f"$.grounded_context_profiles[{index}].context_attribute_provenance[{pindex}].source_evidence", errors,
                )
            else:
                _add(errors, "invalid_type", f"$.grounded_context_profiles[{index}].context_attribute_provenance[{pindex}]", "must be an object")
        if stratum:
            expected_signatures = [_provenance_signature(d, dimension=True) for d in _safe_mapping_list(stratum.get("dimensions"))]
            actual_signatures = [_provenance_signature(p, dimension=False) for p in provenance]
            if len(expected_signatures) != len(set(expected_signatures)):
                _add(errors, "duplicate_provenance", "$.context_strata", "context-stratum dimensions must be unique")
            if len(actual_signatures) != len(set(actual_signatures)):
                _add(errors, "duplicate_provenance", f"$.grounded_context_profiles[{index}].context_attribute_provenance", "grounded-profile provenance entries must be unique")
            if sorted(expected_signatures, key=repr) != sorted(actual_signatures, key=repr):
                _add(errors, "profile_provenance_mismatch", f"$.grounded_context_profiles[{index}].context_attribute_provenance", "must exactly match the selected context stratum dimensions")
    _unique(profile_records, "grounded_profile_id", "$.grounded_context_profiles", errors)

    replicate = _object(panel.get("replicate_strategy"), "$.replicate_strategy", _REPLICATE_KEYS, errors)
    if replicate:
        _text(replicate.get("worker_unit"), "$.replicate_strategy.worker_unit", errors)
        if not isinstance(replicate.get("shared_context_fallback_allowed"), bool):
            _add(errors, "invalid_type", "$.replicate_strategy.shared_context_fallback_allowed", "must be boolean")
        _string_list(replicate.get("fields_allowed_to_vary"), "$.replicate_strategy.fields_allowed_to_vary", errors)
        _string_list(replicate.get("fields_never_to_invent"), "$.replicate_strategy.fields_never_to_invent", errors, nonempty=True)
    for index, item in enumerate(_array(panel.get("calibration_history"), "$.calibration_history", errors)):
        record = _object(item, f"$.calibration_history[{index}]", _CALIBRATION_KEYS, errors)
        if record:
            for key in _CALIBRATION_KEYS:
                if key in {"mapped_variants", "mapped_segments"}:
                    _string_list(record.get(key), f"$.calibration_history[{index}].{key}", errors)
                else:
                    _text(record.get(key), f"$.calibration_history[{index}].{key}", errors)
    refresh = _object(panel.get("refresh_conditions"), "$.refresh_conditions", _REFRESH_KEYS, errors)
    if refresh:
        _timestamp(refresh.get("review_after"), "$.refresh_conditions.review_after", errors)
        max_age = refresh.get("max_age_days")
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
            _add(errors, "invalid_max_age", "$.refresh_conditions.max_age_days", "must be a positive integer")
        _string_list(refresh.get("triggers"), "$.refresh_conditions.triggers", errors, nonempty=True)
    governance = _object(panel.get("governance"), "$.governance", _GOVERNANCE_KEYS, errors)
    if governance:
        _text(governance.get("pii_policy"), "$.governance.pii_policy", errors)
        _string_list(governance.get("allowed_uses"), "$.governance.allowed_uses", errors, nonempty=True)
        _string_list(governance.get("excluded_uses"), "$.governance.excluded_uses", errors, nonempty=True)
        privacy = _object(governance.get("privacy_confirmation"), "$.governance.privacy_confirmation", _PRIVACY_KEYS, errors)
        if privacy:
            if privacy.get("confirmed") is not True:
                _add(errors, "privacy_not_confirmed", "$.governance.privacy_confirmation.confirmed", "must be true")
            _text(privacy.get("confirmed_by"), "$.governance.privacy_confirmation.confirmed_by", errors)
            _timestamp(privacy.get("confirmed_at"), "$.governance.privacy_confirmation.confirmed_at", errors)
            _text(privacy.get("note"), "$.governance.privacy_confirmation.note", errors, allow_empty=True)
    if status == "provisional_no_research":
        _reject_provisional_refs(panel, "$", errors)
    for key in ("audience_scope", "segments", "persona_archetypes", "context_strata", "grounded_context_profiles"):
        _sensitive_trait_scan(panel.get(key), f"$.{key}", errors)
    _privacy_scan(panel, "$", errors)
    return sorted(set(errors))


def validate_audience_research_pair(brief: Mapping[str, Any], panel: Mapping[str, Any], *, now: datetime | None = None) -> list[ValidationError]:
    """Validate both files and every cross-file identity/provenance boundary."""

    errors = list(validate_research_brief(brief)) + list(validate_saved_panel(panel, brief, now=now))
    safe_brief = brief if isinstance(brief, Mapping) else {}
    safe_panel = panel if isinstance(panel, Mapping) else {}
    persona = safe_panel.get("persona_research", {})
    if isinstance(persona, Mapping):
        if persona.get("brief_id") != safe_brief.get("brief_id"):
            _add(errors, "brief_identity_mismatch", "$.panel.persona_research.brief_id", "must equal research brief brief_id")
        if persona.get("mode") != safe_brief.get("research_mode"):
            _add(errors, "research_mode_mismatch", "$.panel.persona_research.mode", "must equal research brief research_mode")
        if persona.get("status") != safe_brief.get("status"):
            _add(errors, "research_status_mismatch", "$.panel.persona_research.status", "must equal research brief status")
        approval = safe_brief.get("approval", {})
        if isinstance(approval, Mapping) and persona.get("approved_at") != approval.get("approved_at"):
            _add(errors, "approval_time_mismatch", "$.panel.persona_research.approved_at", "must equal research brief approval timestamp")
        evidence_records = _safe_mapping_list(safe_brief.get("evidence_sources"))
        brief_evidence = {item.get("evidence_id") for item in evidence_records if isinstance(item.get("evidence_id"), str)}
        if _safe_string_set(persona.get("evidence_ids")) != brief_evidence:
            _add(errors, "evidence_set_mismatch", "$.panel.persona_research.evidence_ids", "must exactly equal research brief evidence IDs")
        brief_source_types = {item.get("type") for item in evidence_records if isinstance(item.get("type"), str)}
        if _safe_string_set(persona.get("source_types")) != brief_source_types:
            _add(errors, "source_type_set_mismatch", "$.panel.persona_research.source_types", "must exactly equal research brief source types")
        if persona.get("coverage") != safe_brief.get("coverage"):
            _add(errors, "coverage_mismatch", "$.panel.persona_research.coverage", "must exactly equal research brief coverage")
        if persona.get("evidence_gaps") != safe_brief.get("evidence_gaps"):
            _add(errors, "evidence_gaps_mismatch", "$.panel.persona_research.evidence_gaps", "must exactly equal research brief evidence gaps")
    panel_scope = safe_panel.get("audience_scope")
    brief_target = safe_brief.get("target_audience")
    if isinstance(panel_scope, Mapping) and isinstance(brief_target, Mapping):
        for key in _TARGET_KEYS:
            if panel_scope.get(key) != brief_target.get(key):
                _add(errors, "audience_scope_mismatch", f"$.panel.audience_scope.{key}", "must equal research brief target_audience")
    hypotheses = {item.get("segment_id"): item for item in _safe_mapping_list(safe_brief.get("segment_hypotheses")) if isinstance(item.get("segment_id"), str)}
    for index, segment in enumerate(_safe_mapping_list(safe_panel.get("segments"))):
        segment_id = segment.get("segment_id") if isinstance(segment.get("segment_id"), str) else None
        hypothesis = hypotheses.get(segment_id)
        if hypothesis is None:
            _add(errors, "unresolved_segment_hypothesis", f"$.panel.segments[{index}].segment_id", "must resolve to an approved segment hypothesis")
        else:
            if segment.get("origin") != hypothesis.get("origin"):
                _add(errors, "segment_origin_mismatch", f"$.panel.segments[{index}].origin", "must equal the approved hypothesis origin")
            if not _safe_string_set(segment.get("finding_ids")).issubset(_safe_string_set(hypothesis.get("finding_ids"))):
                _add(errors, "unsupported_segment_findings", f"$.panel.segments[{index}].finding_ids", "must be a subset of the approved hypothesis findings")
            if not _safe_string_set(segment.get("evidence_ids")).issubset(_safe_string_set(hypothesis.get("evidence_ids"))):
                _add(errors, "unsupported_segment_evidence", f"$.panel.segments[{index}].evidence_ids", "must be a subset of the approved hypothesis evidence")
    segment_provenance = {
        item.get("segment_id"): (_safe_string_set(item.get("finding_ids")), _safe_string_set(item.get("evidence_ids")))
        for item in _safe_mapping_list(safe_panel.get("segments")) if isinstance(item.get("segment_id"), str)
    }
    for collection_name in ("persona_archetypes", "context_strata"):
        for index, item in enumerate(_safe_mapping_list(safe_panel.get(collection_name))):
            segment_id = item.get("segment_id") if isinstance(item.get("segment_id"), str) else None
            allowed_findings, allowed_evidence = segment_provenance.get(segment_id, (set(), set()))
            if collection_name == "persona_archetypes":
                used_findings, used_evidence = _safe_string_set(item.get("finding_ids")), _safe_string_set(item.get("evidence_ids"))
                if not used_findings.issubset(allowed_findings) or not used_evidence.issubset(allowed_evidence):
                    _add(errors, "unsupported_segment_provenance", f"$.panel.{collection_name}[{index}]", "provenance must stay within the selected segment")
            else:
                for dindex, dimension in enumerate(_safe_mapping_list(item.get("dimensions"))):
                    if not _safe_string_set(dimension.get("finding_ids")).issubset(allowed_findings) or not _safe_string_set(dimension.get("source_evidence")).issubset(allowed_evidence):
                        _add(errors, "unsupported_segment_provenance", f"$.panel.context_strata[{index}].dimensions[{dindex}]", "provenance must stay within the selected segment")
    for index, profile in enumerate(_safe_mapping_list(safe_panel.get("grounded_context_profiles"))):
        segment_id = profile.get("segment_id") if isinstance(profile.get("segment_id"), str) else None
        allowed_findings, allowed_evidence = segment_provenance.get(segment_id, (set(), set()))
        for pindex, provenance in enumerate(_safe_mapping_list(profile.get("context_attribute_provenance"))):
            if not _safe_string_set(provenance.get("finding_ids")).issubset(allowed_findings) or not _safe_string_set(provenance.get("source_evidence")).issubset(allowed_evidence):
                _add(errors, "unsupported_segment_provenance", f"$.panel.grounded_context_profiles[{index}].context_attribute_provenance[{pindex}]", "provenance must stay within the selected segment")
    return sorted(set(errors))


_validate_research_brief_impl = validate_research_brief
_validate_saved_panel_impl = validate_saved_panel
_validate_audience_research_pair_impl = validate_audience_research_pair


def _total_validation(call: Any) -> list[ValidationError]:
    try:
        return call()
    except (TypeError, ValueError, AttributeError, KeyError, IndexError):
        return [ValidationError("malformed_payload", "$", "payload contains an invalid scalar or container shape")]


def validate_research_brief(payload: Any) -> list[ValidationError]:
    """Validate any JSON-compatible value without raising."""

    return _total_validation(lambda: _validate_research_brief_impl(payload))


def validate_saved_panel(payload: Any, brief: Any = None, *, now: datetime | None = None) -> list[ValidationError]:
    """Validate any JSON-compatible value without raising."""

    return _total_validation(lambda: _validate_saved_panel_impl(payload, brief, now=now))


def validate_audience_research_pair(brief: Any, panel: Any, *, now: datetime | None = None) -> list[ValidationError]:
    """Validate any JSON-compatible pair without raising."""

    return _total_validation(lambda: _validate_audience_research_pair_impl(brief, panel, now=now))


def require_valid_audience_research_pair(brief: Mapping[str, Any], panel: Mapping[str, Any], *, now: datetime | None = None) -> None:
    errors = validate_audience_research_pair(brief, panel, now=now)
    if errors:
        raise AudienceResearchValidationError(errors)


__all__ = [
    "AudienceResearchValidationError", "RESEARCH_BRIEF_SCHEMA_VERSION",
    "SAVED_PANEL_SCHEMA_VERSION", "ValidationError", "compute_scope_fingerprint",
    "require_valid_audience_research_pair", "validate_audience_research_pair",
    "validate_research_brief", "validate_saved_panel",
]
