"""Deterministic, self-contained dashboard compilation for synthetic ad studies."""

from __future__ import annotations

import base64
from collections import Counter
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import mimetypes
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
import zipfile

from .audience_package import (
    ARCHIVE_FILES as AUDIENCE_ARCHIVE_FILES,
    PackageValidationError,
    validate_package_archive,
)
from .audience_package_v3 import (
    ARCHIVE_FILES_V3 as AUDIENCE_ARCHIVE_FILES_V3,
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
    read_v3_archive_members,
    validate_package_archive_v3,
)
from .contracts import (
    SUPPORTED_CREATIVE_FORMATS,
    validate_v3_dispatch_authority,
    validate_v3_jobs_envelope,
)
from .lineage import CANONICAL_LINEAGE_FILES, validate_bound_lineage
from .planning import load_reusable_v3_audience_resolution
from .responses import (
    validate_dispatch_audit_job_bindings,
    validate_response_job_bindings,
)


REQUIRED_INPUTS = (
    "study-manifest.json",
    "creative-roster.json",
    "panelist-responses.jsonl",
    "screening-model-results.json",
    "finalist-results.json",
    "feedback-synthesis.json",
)
OPTIONAL_INPUTS = (
    "boundary-results.json",
    "saliency-index.json",
    "raw-provider-returns.jsonl",
    "rejected-attempts.jsonl",
    "dispatch-audit.jsonl",
)
JSON_PAYLOAD_PLACEHOLDER = "__DASHBOARD_DATA__"
BRAND_LOGO_PLACEHOLDER = "__IP_LOGO_DATA_URL__"
V3_ALLOCATION_SECTION_PLACEHOLDER = "__V3_ALLOCATION_SECTION__"
V3_ALLOCATION_SCRIPT_PLACEHOLDER = "__V3_ALLOCATION_SCRIPT__"
TIER4_VALIDATION_SECTION_PLACEHOLDER = "__TIER4_VALIDATION_SECTION__"
TIER4_VALIDATION_SCRIPT_PLACEHOLDER = "__TIER4_VALIDATION_SCRIPT__"
MAX_EMBED_BYTES = 20 * 1024 * 1024
APPROVED_ROSTER_STATES = frozenset({"approved", "approved_with_override"})
FEEDBACK_TYPES = frozenset({"strength", "friction", "disagreement", "next_test"})
FEEDBACK_EVIDENCE_SCOPES = frozenset(
    {"single_source_observation", "cross_response_pattern"}
)
FEEDBACK_CLAIM_FIELDS = ("theme", "why_it_matters", "recommended_action", "limitations")
SURVEY_INCIDENCE_RE = re.compile(
    r"(?i)(?:\b\d{1,3}(?:\.\d+)?\s*%\b|\bpercent(?:age)?\b|\b\d+\s+out\s+of\s+\d+\b)"
)
CUSTOMER_PREFERENCE_RE = re.compile(
    r"(?i)\b(?:customers?|consumers?|people|respondents?|the audience|the market|"
    r"the population)\b.{0,45}\b(?:prefer|preferred|like|liked|want|would choose|"
    r"would buy)\b"
)
PERFORMANCE_CERTAINTY_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:will|guaranteed to|proven to|expected to)\s+"
    r"(?:improve|increase|boost|drive|raise|lift)\b|"
    r"\b(?:proves?|guarantees?)\b.{0,60}\b(?:ctr|click-through|conversion|revenue|"
    r"sales|campaign performance|performance|outcomes?)\b|"
    r"\b(?:predicted|proven|guaranteed)\s+(?:ctr|click-through|conversion|revenue|"
    r"sales|campaign performance|performance|outcomes?)\b|"
    r"\b(?:ctr|click-through|conversion|revenue|sales|campaign performance|performance)"
    r"\b.{0,35}\b(?:will improve|will increase|is guaranteed|is proven)\b)"
)
PROVEN_OUTCOME_RE = re.compile(
    r"(?i)(?:\b(?:proves?|guarantees?)\b|\b(?:proven|guaranteed|predicted)\s+"
    r"(?:fix|improvement|outcome|result|winner|solution)\b)"
)
NEGATED_CLAIM_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:is\s+)?not\s+(?:a\s+)?(?:proof|evidence)\s+that\b[^.;]*|"
    r"\b(?:does\s+not|cannot|can\s+not)\s+(?:prove|establish|guarantee|predict)\b[^.;]*|"
    r"\bnot\s+(?:a\s+)?(?:proven|guaranteed|predicted)\s+"
    r"(?:fix|improvement|outcome|result|winner|solution)\b)"
)
ACTION_TEST_LANGUAGE_RE = re.compile(
    r"(?i)\b(?:test|testing|try|compare|comparison|hypothesis|experiment)\b"
)
CHANGE_ACTION_RE = re.compile(
    r"(?i)\b(?:change|add|remove|replace|rewrite|revise|narrow|strengthen|simplify|"
    r"swap|make)\b"
)
IMAGE_CAPABLE_FORMATS = frozenset(SUPPORTED_CREATIVE_FORMATS - {"copy_only"})
SUPPORTED_METHODS = frozenset({"complete_exposure", "partial_exposure_maxdiff"})
RENDERABLE_IMAGE_MIME_TYPES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
    }
)
STAGES = {
    "screening_response": ("screening", "First round"),
    "boundary_response": ("boundary", "Tie-break"),
    "finalist_response": ("finalist", "Finalist round"),
}
SPECIALIST_KEYS = frozenset(
    {
        "specialist_score",
        "specialist_scores",
        "creative_specialist_score",
        "creative_specialist_scores",
        "platform_specialist_score",
        "platform_specialist_scores",
    }
)
AUDIENCE_SNAPSHOT_FILES = AUDIENCE_ARCHIVE_FILES + ("audience-panel-package.zip",)
AUDIENCE_SNAPSHOT_FILES_V3 = AUDIENCE_ARCHIVE_FILES_V3 + (
    "audience-panel-package.zip",
    "audience-resolution.json",
    "audience-resolution-authority.json",
)
V3_ALLOCATION_JOB_INDEX_VERSION = "audience-allocation-jobs-index-v1"
V3_ALLOCATION_DISCLAIMER = "This is not a human sample or a customer survey."
V3_ALLOCATION_CLAIMS = {
    "directional_profile_allocation": (
        "This reusable Tier 1 panel allocates synthetic panelists across approved "
        "profiles using directional planning allocations. It does not claim "
        "population composition."
    ),
    "frame_aligned": (
        "The synthetic roster is aligned to the approved structural frame within "
        "the product allocation threshold."
    ),
    "allocation_distorted": (
        "The requested synthetic capacity cannot preserve the approved structural "
        "composition within the product threshold."
    ),
}
V3_ALLOCATION_CONTINUATION = (
    "This run remains a Tier 1 directional creative hypothesis stress test even "
    "though the saved panel retains its approved reusable tier."
)
V3_ALLOCATION_SECTION_HTML = """
      <section class="ledger-panel" id="audience-run-allocation">
        <header><h3>Run allocation</h3></header>
        <div class="advanced-details-body" id="audience-run-allocation-body"></div>
      </section>"""
V3_ALLOCATION_SCRIPT = """
        const allocation = data.audience.run_allocation;
        const allocationRoot = document.getElementById("audience-run-allocation-body");
        allocationRoot.append(make("p", "notice", allocation.disclaimer));
        const semantics = make("dl", "data-ledger");
        addDataRow(semantics, "Reusable panel", `${allocation.package.panel_id} · version ${allocation.package.panel_version}`);
        addDataRow(semantics, "Reusable panel tier", humanize(allocation.package.tier));
        addDataRow(semantics, "Structural weight semantics", allocation.reusable_weight_semantics.structural.map(humanize).join(" · "));
        addDataRow(semantics, "Overlay weight semantics", allocation.reusable_weight_semantics.overlay.map(humanize).join(" · "));
        allocationRoot.append(semantics);
        allocation.stages.forEach(stage => {
          const section = make("section", "audience-group");
          section.append(make("h4", "", `${humanize(stage.stage)} allocation`));
          if (stage.dispatch_status === "not_applicable") {
            section.append(make("p", "notice", stage.message));
            allocationRoot.append(section);
            return;
          }
          section.append(make("p", "status-line", humanize(stage.dispatch_status)));
          (stage.diagnostics || []).forEach(diagnostic => {
            const details = make("details", "creative-details");
            details.open = diagnostic.run_claim_authority;
            details.append(make("summary", "", humanize(diagnostic.diagnostic_scope)));
            const ledger = make("dl", "data-ledger");
            addDataRow(ledger, "Synthetic slots", number(diagnostic.requested_slot_count));
            addDataRow(ledger, "Allocation status", humanize(diagnostic.fidelity_status));
            addDataRow(ledger, "Must-cover groups represented", diagnostic.all_must_cover_groups_represented ? "yes" : "no");
            addDataRow(ledger, "Claim effect", humanize(diagnostic.claim_effect));
            addDataRow(ledger, "Authority", diagnostic.authority_label);
            details.append(ledger, make("p", "", diagnostic.claim_language));
            if (diagnostic.user_decision) details.append(make("p", "notice", diagnostic.user_decision));
            const groups = make("dl", "data-ledger");
            (diagnostic.structural_groups || []).forEach(group => {
              addDataRow(
                groups,
                humanize(group.structural_group_id),
                `${group.assigned_slots} slots · target ${percent(group.target_weight)} · raw slot share ${percent(group.raw_slot_share)} · analysis-effective share ${percent(group.analysis_effective_share)} · absolute deviation ${percent(group.absolute_deviation)}`,
              );
            });
            details.append(groups);
            section.append(details);
          });
          allocationRoot.append(section);
        });"""
TIER4_VALIDATION_SECTION_HTML = """
      <section class="ledger-panel" id="held-out-ordering-validation">
        <header><h3 id="held-out-ordering-validation-headline">Outcome calibration</h3></header>
        <div class="advanced-details-body" id="held-out-ordering-validation-body"></div>
      </section>"""
TIER4_VALIDATION_SCRIPT = """
        const tier4 = data.tier4_validation;
        document.getElementById("held-out-ordering-validation-headline").textContent = tier4.headline;
        const tier4Root = document.getElementById("held-out-ordering-validation-body");
        tier4Root.append(make("p", "notice", tier4.claim_text));
        tier4Root.append(make("p", "", tier4.disclaimer));
        const tier4Scope = make("dl", "data-ledger");
        Object.entries(tier4.scope).forEach(([label, value]) => addDataRow(tier4Scope, humanize(label), value));
        addDataRow(tier4Scope, "Qualifying test blocks", number(tier4.qualifying_block_count));
        if (tier4.expires_at) addDataRow(tier4Scope, "Claim expires", tier4.expires_at);
        addDataRow(tier4Scope, "Claim status", tier4.claim_status);
        tier4Root.append(tier4Scope);
        if (tier4.segment_result.length) tier4Root.append(make("p", "", `Segment result: ${tier4.segment_result.map(item => `${item.segment_id}: ${item.status}`).join("; ")}`));
        tier4Root.append(make("p", "", `Influence leave-out threshold check: ${humanize(tier4.influence_diagnostics.status)} (${tier4.influence_diagnostics.leave_one_block.length} block and ${tier4.influence_diagnostics.leave_one_batch.length} batch exclusions disclosed)`));
        if (tier4.refresh_triggers.length) tier4Root.append(make("p", "", `Refresh triggers: ${tier4.refresh_triggers.join("; ")}`));
        if (tier4.limitations.length) tier4Root.append(make("p", "", `Limits: ${tier4.limitations.join(" ")}`));
"""


class DashboardInputError(ValueError):
    """Raised when a run directory cannot be compiled without inventing evidence."""


def feedback_claim_error(field: str, value: Any) -> str | None:
    """Return a deterministic reason when feedback copy overstates synthetic evidence."""

    values = value if isinstance(value, Sequence) and not isinstance(value, str) else [value]
    text = " ".join(str(item) for item in values if isinstance(item, str)).strip()
    if not text:
        return None
    claim_text = NEGATED_CLAIM_RE.sub("", text)
    if SURVEY_INCIDENCE_RE.search(claim_text):
        return f"{field} cannot use survey-style incidence or percentage language"
    if CUSTOMER_PREFERENCE_RE.search(claim_text):
        return f"{field} cannot claim customer or population preference"
    if PERFORMANCE_CERTAINTY_RE.search(claim_text) or PROVEN_OUTCOME_RE.search(
        claim_text
    ):
        return f"{field} cannot predict or guarantee performance outcomes"
    return None


def feedback_action_error(feedback_type: str, action: str) -> str | None:
    """Require proposed creative changes to remain explicit test hypotheses."""

    needs_test_language = feedback_type == "next_test" or CHANGE_ACTION_RE.search(action)
    if needs_test_language and not ACTION_TEST_LANGUAGE_RE.search(action):
        return "recommended_action proposing a change must use explicit test or hypothesis language"
    return None


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_non_empty_string(value: Any, context: str) -> str:
    if not _non_empty_string(value):
        raise DashboardInputError(f"{context} must be a non-empty string")
    return str(value).strip()


def _require_aware_datetime(value: Any, context: str) -> datetime:
    raw = _require_non_empty_string(value, context)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DashboardInputError(
            f"{context} must be a valid ISO 8601 timestamp with a timezone offset"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DashboardInputError(
            f"{context} must include a timezone offset (for example, Z or +00:00)"
        )
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _require_id_list(value: Any, context: str) -> list[str]:
    raw = _require_sequence(value, context)
    identifiers = [str(item) for item in raw]
    if not identifiers or any(not _non_empty_string(item) for item in identifiers):
        raise DashboardInputError(f"{context} must contain non-empty stable IDs")
    return identifiers


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DashboardInputError(f"{context} must contain a JSON object")
    return value


def _require_sequence(value: Any, context: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise DashboardInputError(f"{context} must contain an array")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise DashboardInputError(f"{path.name} is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DashboardInputError(
            f"{path.name} is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    return dict(_require_mapping(value, path.name))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DashboardInputError(f"{path.name} is not valid UTF-8: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DashboardInputError(
                f"{path.name} line {line_number} is not valid JSON: {exc.msg}"
            ) from exc
        records.append(dict(_require_mapping(value, f"{path.name} line {line_number}")))
    if not records:
        raise DashboardInputError(f"{path.name} contains no accepted response records")
    return records


def _load_jsonl_allow_empty(path: Path) -> list[dict[str, Any]]:
    """Load a lineage JSONL file whose canonical empty state is zero bytes."""

    if not path.is_file():
        raise DashboardInputError(f"missing lineage file: {path.name}")
    if path.stat().st_size == 0:
        return []
    return _load_jsonl(path)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tier4_validation_payload(
    run_dir: Path,
    manifest: Mapping[str, Any],
    *,
    authority_registry: object | None,
) -> dict[str, Any] | None:
    """Authenticate one optional Tier 4 claim without deriving any statistic.

    The manifest binding is intentionally narrow and run-relative: the dashboard
    reads one validated archive, then projects only the already-issued claim.
    """

    binding = manifest.get("validation_package")
    if binding is None:
        return None
    if authority_registry is None:
        raise DashboardInputError(
            "Tier 4 validation display requires a live trusted authority registry"
        )
    if not isinstance(binding, Mapping):
        raise DashboardInputError("study-manifest.json validation_package must be an object")
    required = {
        "package_path", "package_zip_sha256", "package_manifest_sha256",
        "claim_id", "claim_sha256", "claim_scope_sha256", "panel_id",
        "panel_version", "library_path",
    }
    if set(binding) != required:
        raise DashboardInputError("study-manifest.json validation_package keys are invalid")
    relative = binding.get("package_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise DashboardInputError("validation_package.package_path must be a non-empty relative path")
    package_path = (run_dir / relative).resolve()
    if package_path.parent != (run_dir / "validation").resolve() or package_path.name != "audience-panel-validation-package.zip":
        raise DashboardInputError("validation_package.package_path must be validation/audience-panel-validation-package.zip")
    if package_path.is_symlink() or not package_path.is_file():
        raise DashboardInputError("validation package is missing or unsafe")
    panel_builder_scripts = Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
    if str(panel_builder_scripts) not in sys.path:
        sys.path.insert(0, str(panel_builder_scripts))
    try:
        from audience_panel_builder.population.validation.package import (  # noqa: PLC0415
            ValidationPackageError,
            validate_validation_package,
        )
    except ImportError as exc:
        raise DashboardInputError("Tier 4 validation package support is unavailable") from exc
    try:
        validated = validate_validation_package(
            package_path, authority_registry=authority_registry,
        )
    except (OSError, ValueError, ValidationPackageError) as exc:
        raise DashboardInputError("validation package failed authentication") from exc
    claim = validated.get("claim")
    claim_kind = validated.get("claim_kind")
    if claim_kind not in {"claim", "negative"}:
        raise DashboardInputError("validation package result kind is invalid")
    expected = {
        "package_zip_sha256": validated["package_zip_sha256"],
        "package_manifest_sha256": validated["package_manifest_sha256"],
        "claim_id": validated["claim_id"] if claim_kind == "claim" else None,
        "claim_sha256": (
            validated["claim_sha256"] if claim_kind == "claim" else None
        ),
        "claim_scope_sha256": validated["claim_scope_sha256"],
        "panel_id": validated["panel_binding"]["panel_id"],
        "panel_version": validated["panel_binding"]["panel_version"],
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise DashboardInputError("validation_package binding does not match authenticated claim")
    audience_binding = manifest.get("audience_package")
    if not isinstance(audience_binding, Mapping):
        raise DashboardInputError("a Tier 4 dashboard claim requires an authenticated audience_package")
    if (
        audience_binding.get("panel_id") != expected["panel_id"]
        or audience_binding.get("panel_version") != expected["panel_version"]
        or audience_binding.get("package_zip_sha256") != str(validated["panel_binding"]["package_sha256"])[7:]
    ):
        raise DashboardInputError("validation claim panel/package does not match this dashboard")
    evaluation = validated.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise DashboardInputError("validation package evaluation is missing")
    scope = (
        claim.get("claim_scope")
        if isinstance(claim, Mapping)
        else evaluation.get("claim_scope")
    )
    if not isinstance(scope, Mapping) or scope.get("panel_binding") != validated["panel_binding"]:
        raise DashboardInputError("validation claim scope does not exactly bind its panel")
    outcome_scope = scope.get("outcome_scope")
    if not isinstance(outcome_scope, Mapping):
        raise DashboardInputError("validation claim scope is invalid")
    synthetic_binding = scope.get("synthetic_binding")
    metric_binding = evaluation.get("metric_binding")
    if (
        not isinstance(synthetic_binding, Mapping)
        or not isinstance(metric_binding, Mapping)
    ):
        raise DashboardInputError(
            "validation claim scope is missing its synthetic or metric binding"
        )
    display_scope = {
        "panel_id": str(validated["panel_binding"]["panel_id"]),
        "panel_version": str(validated["panel_binding"]["panel_version"]),
        "synthetic_surface": str(synthetic_binding["surface"]),
        "synthetic_run_id": str(synthetic_binding["run_id"]),
        "synthetic_result_sha256": str(
            synthetic_binding["result_sha256"]
        ),
        **{str(key): str(value) for key, value in outcome_scope.items()},
        "outcome_metric": str(metric_binding["name"]),
    }
    qualifying_block_count = sum(
        1 for comparison in evaluation.get("comparisons", [])
        if isinstance(comparison, Mapping)
        and all(
            isinstance(observation, Mapping)
            and observation.get("holdout_status") == "eligible_held_out"
            for observation in comparison.get("observations", [])
        )
    )
    segment_result = [
        {
            "segment_id": str(item["segment_id"]),
            "status": str(item["status"]),
            "clear_reversal": bool(item["clear_reversal"]),
        }
        for item in evaluation.get("segment_diagnostics", [])
        if isinstance(item, Mapping)
    ]
    influence_diagnostics = evaluation.get("influence_diagnostics")
    if not isinstance(influence_diagnostics, Mapping):
        raise DashboardInputError(
            "validation evaluation is missing influence diagnostics"
        )
    if claim_kind == "negative":
        if binding.get("library_path") is not None:
            raise DashboardInputError(
                "negative validation package must not name a claim library"
            )
        status = str(evaluation.get("decision", {}).get("status"))
        headline, summary = {
            "tier4_not_supported": (
                "The result did not support Tier 4",
                "The held-out real-world results did not support an active Tier 4 ordering claim.",
            ),
            "evaluated_with_limitations": (
                "Not enough evidence yet",
                "The held-out evidence did not meet every condition required for Tier 4.",
            ),
            "invalid": (
                "The validation could not be used",
                "The validation evidence could not support a Tier 4 conclusion.",
            ),
        }.get(status, (None, None))
        if headline is None or summary is None:
            raise DashboardInputError(
                "negative validation package has an unsupported evaluation state"
            )
        return {
            "headline": headline,
            "claim_text": summary,
            "disclaimer": (
                "This does not predict click-through rate, conversion rate, "
                "revenue, winning probability, or causal lift."
            ),
            "scope": display_scope,
            "metric": dict(metric_binding),
            "synthetic_binding": dict(synthetic_binding),
            "qualifying_block_count": qualifying_block_count,
            "segment_result": segment_result,
            "influence_diagnostics": dict(influence_diagnostics),
            "refresh_triggers": [],
            "expires_at": None,
            "limitations": [
                str(item) for item in evaluation.get("limitations", [])
            ],
            "claim_status": "not_issued",
            "active_claim": False,
            "claim_id": None,
            "claim_sha256": None,
            "package_zip_sha256": str(validated["package_zip_sha256"]),
        }
    if not isinstance(claim, Mapping) or claim.get("status") != "active":
        raise DashboardInputError(
            "validation package claim document must remain initially active"
        )
    library_relative = binding.get("library_path")
    if (
        not isinstance(library_relative, str)
        or not library_relative
        or Path(library_relative).is_absolute()
    ):
        raise DashboardInputError(
            "active validation claim requires a relative validation library path"
        )
    library_root = (run_dir / library_relative).resolve()
    if (
        library_root.parent != run_dir.resolve()
        or library_root.name != "validation-library"
    ):
        raise DashboardInputError(
            "validation library path must be validation-library"
        )
    try:
        from audience_panel_builder.population.validation.library import (  # noqa: PLC0415
            LibraryError,
            claim_lifecycle_status,
            current_claim,
        )
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        lifecycle = claim_lifecycle_status(
            str(claim["claim_id"]),
            library_root=library_root,
            as_of=as_of,
            authority_registry=authority_registry,
        )
    except (LibraryError, OSError, ValueError) as exc:
        raise DashboardInputError(
            "validation claim lifecycle could not be authenticated"
        ) from exc
    registered_claim = lifecycle.get("claim")
    if (
        not isinstance(registered_claim, Mapping)
        or registered_claim.get("claim_id") != claim.get("claim_id")
        or registered_claim.get("claim_sha256") != claim.get("claim_sha256")
        or registered_claim.get("claim_scope_sha256")
        != validated["claim_scope_sha256"]
        or registered_claim.get("panel_id")
        != validated["panel_binding"]["panel_id"]
        or registered_claim.get("panel_version")
        != validated["panel_binding"]["panel_version"]
        or registered_claim.get("package_sha256")
        != validated["package_zip_sha256"]
        or registered_claim.get("package_manifest_sha256")
        != validated["package_manifest_sha256"]
    ):
        raise DashboardInputError(
            "validation library lifecycle does not bind this exact claim"
        )
    lifecycle_status = str(lifecycle.get("lifecycle_status"))
    if lifecycle_status not in {
        "active", "expired", "superseded", "withdrawn", "invalidated",
        "not_yet_active",
    }:
        raise DashboardInputError(
            "validation library returned an unknown lifecycle state"
        )
    if lifecycle_status == "active":
        try:
            current = current_claim(
                str(validated["panel_binding"]["panel_id"]),
                str(validated["panel_binding"]["panel_version"]),
                str(validated["claim_scope_sha256"]),
                library_root=library_root,
                as_of=as_of,
                authority_registry=authority_registry,
            )
        except LibraryError:
            lifecycle_status = "not_current"
        else:
            selected = current.get("claim")
            if (
                not isinstance(selected, Mapping)
                or selected.get("claim_id") != claim.get("claim_id")
            ):
                lifecycle_status = "not_current"
    active_claim = lifecycle_status == "active"
    headline = {
        "active": "Held-out ordering validation",
        "expired": "Tier 4 claim expired",
        "superseded": "Tier 4 claim superseded",
        "withdrawn": "Tier 4 claim withdrawn",
        "invalidated": "Tier 4 claim invalidated",
        "not_yet_active": "Tier 4 claim not active yet",
        "not_current": "Tier 4 claim registered but not current",
    }[lifecycle_status]
    return {
        "headline": headline,
        "claim_text": (
            str(claim["claim_text"])
            if active_claim
            else "This previously issued Tier 4 claim is not currently active."
        ),
        "disclaimer": str(claim["required_disclaimer"]),
        "scope": display_scope,
        "metric": dict(metric_binding),
        "synthetic_binding": dict(synthetic_binding),
        "qualifying_block_count": qualifying_block_count,
        "segment_result": segment_result,
        "influence_diagnostics": dict(influence_diagnostics),
        "refresh_triggers": [
            str(item) for item in claim.get("refresh_triggers", [])
        ],
        "expires_at": str(claim["expires_at"]),
        "limitations": [str(item) for item in claim.get("limitations", [])],
        "claim_status": lifecycle_status,
        "active_claim": active_claim,
        "claim_id": str(claim["claim_id"]),
        "claim_sha256": str(claim["claim_sha256"]),
        "package_zip_sha256": str(validated["package_zip_sha256"]),
    }


def _audience_snapshot_bytes(run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, bytes] | None:
    binding = manifest.get("audience_package")
    if not isinstance(binding, Mapping):
        return None
    if binding.get("schema_version") == "audience-panel-package-v3":
        snapshot = run_dir / "audience" / "snapshot"
        resolution = run_dir / "audience" / "resolution.json"
        resolution_authority = (
            run_dir / "audience" / "resolution-authority.json"
        )
        if (
            not snapshot.is_dir()
            or snapshot.is_symlink()
            or not resolution.is_file()
            or resolution.is_symlink()
            or resolution.resolve().parent != (run_dir / "audience").resolve()
            or not resolution_authority.is_file()
            or resolution_authority.is_symlink()
            or resolution_authority.resolve().parent
            != (run_dir / "audience").resolve()
        ):
            raise DashboardInputError(
                "v3 audience package requires safe canonical audience/snapshot "
                "plus audience resolution and resolution-authority files"
            )
        package_path = snapshot / "audience-panel-package.zip"
        try:
            _raw, manifest_bytes = read_v3_archive_manifest(package_path)
            archive_files = archive_files_v3_for_manifest(
                json.loads(manifest_bytes.decode("utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise DashboardInputError(
                f"v3 audience snapshot package is invalid: {exc}"
            ) from exc
        files: dict[str, bytes] = {}
        for filename in archive_files + (
            "audience-panel-package.zip",
        ):
            path = snapshot / filename
            if (
                not path.is_file()
                or path.is_symlink()
                or path.resolve().parent != snapshot.resolve()
            ):
                raise DashboardInputError(
                    f"v3 audience snapshot is missing safe {filename}"
                )
            files[filename] = path.read_bytes()
        files["audience-resolution.json"] = resolution.read_bytes()
        files["audience-resolution-authority.json"] = (
            resolution_authority.read_bytes()
        )
        return files
    if binding.get("resolved_snapshot_path") != "audience/snapshot":
        raise DashboardInputError(
            "study-manifest.json audience_package.resolved_snapshot_path must be audience/snapshot"
        )
    snapshot = run_dir / "audience" / "snapshot"
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise DashboardInputError("v2 audience package requires a safe audience/snapshot directory")
    files: dict[str, bytes] = {}
    for filename in AUDIENCE_SNAPSHOT_FILES:
        path = snapshot / filename
        if not path.is_file() or path.is_symlink() or path.resolve().parent != snapshot.resolve():
            raise DashboardInputError(f"audience snapshot is missing safe {filename}")
        files[filename] = path.read_bytes()
    return files


def _validated_v3_audience_package(
    manifest: Mapping[str, Any],
    files: Mapping[str, bytes],
    resolution_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate exact v3 audience bytes and the canonical run envelope."""

    raw_zip = files.get("audience-panel-package.zip")
    if not isinstance(raw_zip, bytes):
        raise DashboardInputError(
            "embedded v3 audience package is missing audience-panel-package.zip"
        )
    try:
        _raw, manifest_bytes = read_v3_archive_manifest(raw_zip)
        archive_files = archive_files_v3_for_manifest(
            json.loads(manifest_bytes.decode("utf-8"))
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DashboardInputError(
            f"embedded v3 audience package manifest is invalid: {exc}"
        ) from exc
    required = set(
        archive_files
        + (
            "audience-panel-package.zip",
            "audience-resolution.json",
            "audience-resolution-authority.json",
        )
    )
    if not required.issubset(files):
        missing = ", ".join(sorted(required - set(files)))
        raise DashboardInputError(f"embedded v3 audience package is missing: {missing}")
    try:
        validation = validate_package_archive_v3(
            resolution_path.parent / "snapshot" / "audience-panel-package.zip"
        )
        archived = read_v3_archive_members(
            raw_zip,
            allowed_files=archive_files,
        )
        envelope, envelope_bytes = load_reusable_v3_audience_resolution(
            resolution_path
        )
    except (PackageValidationError, ValueError, OSError) as exc:
        raise DashboardInputError(f"v3 audience package validation failed: {exc}") from exc
    for filename, expected in archived.items():
        if files.get(filename) != expected:
            raise DashboardInputError(
                f"embedded {filename} does not match audience-panel-package.zip"
            )
    if files["audience-resolution.json"] != envelope_bytes:
        raise DashboardInputError(
            "embedded audience-resolution.json does not match the canonical run envelope"
        )
    if (
        files["audience-resolution-authority.json"]
        != (resolution_path.parent / "resolution-authority.json").read_bytes()
    ):
        raise DashboardInputError(
            "embedded audience-resolution-authority.json does not match the canonical run authority"
        )
    try:
        brief = json.loads(files["audience-research-brief.json"].decode("utf-8"))
        panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
        composition = json.loads(
            files["panel-composition-plan.json"].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardInputError("embedded v3 audience JSON is invalid") from exc
    if not all(
        isinstance(value, dict) for value in (brief, panel, composition, envelope)
    ):
        raise DashboardInputError("embedded v3 audience documents must be objects")
    binding = manifest.get("audience_package")
    expected_binding = envelope["audience_package"]
    if binding != expected_binding:
        raise DashboardInputError(
            "study-manifest.json audience_package does not match the canonical "
            "v3 audience envelope"
        )
    if manifest.get("audience_lock") != envelope["audience_lock"]:
        raise DashboardInputError(
            "study-manifest.json audience_lock does not match the canonical "
            "v3 audience envelope"
        )
    if validation.get("package_manifest_sha256") != binding.get(
        "package_manifest_sha256"
    ) or validation.get("package_zip_sha256") != binding.get(
        "package_zip_sha256"
    ):
        raise DashboardInputError(
            "study-manifest.json audience_package hashes do not match embedded "
            "v3 audience bytes"
        )
    return brief, panel, composition, envelope


def _validated_audience_package(
    manifest: Mapping[str, Any], files: Mapping[str, bytes]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate dashboard audience bytes and return canonical brief, panel, and state."""

    required = set(AUDIENCE_SNAPSHOT_FILES)
    if not required.issubset(files):
        missing = ", ".join(sorted(required - set(files)))
        raise DashboardInputError(f"embedded audience package is missing: {missing}")
    raw_zip = files["audience-panel-package.zip"]
    try:
        validation = validate_package_archive(raw_zip)
    except PackageValidationError as exc:
        raise DashboardInputError(f"audience package validation failed: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
            archived = {name: archive.read(name) for name in AUDIENCE_ARCHIVE_FILES}
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DashboardInputError(f"audience package could not be decoded: {exc}") from exc
    for filename, expected in archived.items():
        if files.get(filename) != expected:
            raise DashboardInputError(
                f"embedded {filename} does not match audience-panel-package.zip"
            )
    try:
        brief = json.loads(files["persona-research-brief.json"].decode("utf-8"))
        panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardInputError("embedded audience JSON is invalid") from exc
    if not isinstance(brief, dict) or not isinstance(panel, dict):
        raise DashboardInputError("embedded audience brief and panel must be objects")
    binding = manifest.get("audience_package")
    if not isinstance(binding, Mapping):
        raise DashboardInputError("v2 audience files require manifest audience_package binding")
    expected_binding = {
        "panel_id": validation["panel_id"],
        "panel_version": validation["panel_version"],
        "panel_sha256": validation["panel_sha256"],
        "panel_byte_count": len(files["saved-audience-panel.json"]),
        "brief_id": validation["brief_id"],
        "brief_sha256": validation["brief_sha256"],
        "brief_byte_count": len(files["persona-research-brief.json"]),
        "package_manifest_sha256": validation["package_manifest_sha256"],
        "package_manifest_byte_count": len(files["package-manifest.json"]),
        "package_zip_sha256": validation["package_zip_sha256"],
        "package_zip_byte_count": len(raw_zip),
        "resolved_snapshot_path": "audience/snapshot",
    }
    if set(binding) != set(expected_binding):
        raise DashboardInputError("study-manifest.json audience_package keys are invalid")
    for field, expected in expected_binding.items():
        if binding.get(field) != expected:
            raise DashboardInputError(
                f"study-manifest.json audience_package.{field} does not match embedded audience bytes"
            )
    lock = manifest.get("audience_lock")
    if not isinstance(lock, Mapping):
        raise DashboardInputError("v2 audience package requires audience_lock")
    expected_lock_fields = {
        "persona_research_brief_id": brief.get("brief_id"),
        "panel_id": panel.get("panel_id"),
        "panel_version": panel.get("version"),
        "segment_weights": {
            item.get("segment_id"): item.get("study_weight")
            for item in panel.get("segments", [])
            if isinstance(item, Mapping)
        },
        "segment_names": {
            item.get("segment_id"): item.get("name")
            for item in panel.get("segments", [])
            if isinstance(item, Mapping)
        },
        "archetype_names": {
            item.get("persona_archetype_id"): item.get("display_name")
            for item in panel.get("persona_archetypes", [])
            if isinstance(item, Mapping)
        },
        "unique_archetypes": len(panel.get("persona_archetypes", [])),
        "unique_grounded_context_profiles": len(panel.get("grounded_context_profiles", [])),
    }
    for field, expected in expected_lock_fields.items():
        if lock.get(field) != expected:
            raise DashboardInputError(
                f"study-manifest.json audience_lock.{field} does not match saved-audience-panel.json"
            )
    persona_research = panel.get("persona_research")
    provisional = (
        isinstance(persona_research, Mapping)
        and persona_research.get("status") == "provisional_no_research"
    )
    return brief, panel, "provisional" if provisional else "research_backed"


def _audience_payload_from_panel(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    _responses: Sequence[Mapping[str, Any]],
    state: str,
) -> dict[str, Any]:
    segment_names = {
        str(item.get("segment_id")): str(item.get("name"))
        for item in panel.get("segments", [])
        if isinstance(item, Mapping)
    }
    archetypes_by_id = {
        str(item.get("persona_archetype_id")): item
        for item in panel.get("persona_archetypes", [])
        if isinstance(item, Mapping)
    }
    archetypes_by_segment: dict[str, list[Mapping[str, Any]]] = {}
    for archetype in archetypes_by_id.values():
        archetypes_by_segment.setdefault(str(archetype.get("segment_id")), []).append(
            archetype
        )
    panelist_profiles: list[dict[str, Any]] = []
    for index, profile in enumerate(panel.get("grounded_context_profiles", []), 1):
        if not isinstance(profile, Mapping):
            continue
        archetype_id = str(profile.get("persona_archetype_id"))
        archetype = archetypes_by_id.get(archetype_id, {})
        snapshot = profile.get("profile_snapshot", {})
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        segment_id = str(profile.get("segment_id"))
        panelist_profiles.append(
            {
                "number_label": f"Panelist profile {index}",
                "profile_id": str(profile.get("grounded_profile_id")),
                "segment_id": segment_id,
                "segment_name": segment_names.get(segment_id, segment_id),
                "perspective_id": archetype_id,
                "perspective_name": str(archetype.get("display_name", archetype_id)),
                "context_stratum_id": str(profile.get("context_stratum_id", "")),
                "context_attribute_provenance": _clean_specialist_fields(
                    profile.get("context_attribute_provenance", [])
                ),
                "role_context": str(
                    snapshot.get(
                        "role_context", archetype.get("role_context", "")
                    )
                ),
                "decision_context": str(
                    snapshot.get(
                        "decision_context", archetype.get("decision_context", "")
                    )
                ),
                "motivations": [
                    str(value)
                    for value in snapshot.get(
                        "motivations", archetype.get("motivations", [])
                    )
                ],
                "concerns": [
                    str(value)
                    for value in [
                        *snapshot.get("anxieties", archetype.get("anxieties", [])),
                        *archetype.get("objections", []),
                    ]
                ],
                "triggers": [
                    str(value) for value in archetype.get("triggers", [])
                ],
                "proof_needs": [
                    str(value)
                    for value in snapshot.get(
                        "proof_needs", archetype.get("proof_needs", [])
                    )
                ],
                "evidence_strength": str(archetype.get("evidence_strength", "")),
                "inference_boundary": str(archetype.get("inference_boundary", "")),
            }
        )
    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(panel.get("segments", []), 1):
        if not isinstance(segment, Mapping):
            continue
        segment_id = str(segment.get("segment_id"))
        mindsets = []
        for archetype in archetypes_by_segment.get(segment_id, []):
            archetype_id = str(archetype.get("persona_archetype_id"))
            mindsets.append(
                {
                    "archetype_id": archetype_id,
                    "name": str(archetype.get("display_name")),
                    "role_context": str(archetype.get("role_context")),
                    "decision_context": str(archetype.get("decision_context")),
                    "motivations": [
                        str(value) for value in archetype.get("motivations", [])
                    ],
                    "anxieties": [
                        str(value) for value in archetype.get("anxieties", [])
                    ],
                    "triggers": [
                        str(value) for value in archetype.get("triggers", [])
                    ],
                    "objections": [
                        str(value) for value in archetype.get("objections", [])
                    ],
                    "proof_needs": [
                        str(value) for value in archetype.get("proof_needs", [])
                    ],
                    "evidence_strength": str(archetype.get("evidence_strength")),
                    "inference_boundary": str(archetype.get("inference_boundary")),
                }
            )
        segments.append(
            {
                "segment_id": segment_id,
                "number_label": f"Segment {index}",
                "name": str(segment.get("name")),
                "description": str(segment.get("description")),
                "mindsets": mindsets,
            }
        )
    persona_research = panel.get("persona_research", {})
    if not isinstance(persona_research, Mapping):
        persona_research = {}
    expiry = persona_research.get("expires_at")
    state_label = (
        "Provisional audience — no research sources"
        if state == "provisional"
        else "Research-backed audience panel"
    )
    scope = panel.get("audience_scope", {})
    if not isinstance(scope, Mapping):
        scope = {}
    role_contexts = sorted(
        {
            str(archetype.get("role_context"))
            for archetypes in archetypes_by_segment.values()
            for archetype in archetypes
            if _non_empty_string(archetype.get("role_context"))
        }
    )
    grounded_profile_count = len(panelist_profiles)
    segment_count = len(segments)
    profile_label = "panelist profile" if grounded_profile_count == 1 else "panelist profiles"
    segment_label = "audience segment" if segment_count == 1 else "audience segments"
    return {
        "state": state,
        "state_label": state_label,
        "intro": (
            f"{grounded_profile_count} provisional {profile_label} represented "
            f"{segment_count} {segment_label}. No research sources were used, so "
            "these profiles should be treated as draft audience assumptions."
            if state == "provisional"
            else f"{grounded_profile_count} research-backed {profile_label} represented "
            f"{segment_count} {segment_label}. Each profile combines a role, buyer "
            "mindset, and buying context."
        ),
        "panel_id": str(panel.get("panel_id")),
        "panel_name": str(panel.get("panel_name")),
        "panel_version": str(panel.get("version")),
        "research_mode": str(persona_research.get("mode", brief.get("research_mode", ""))),
        "research_date": str(persona_research.get("approved_at", brief.get("updated_at", ""))),
        "expires_at": expiry,
        "target_audience": str(scope.get("audience", "")),
        "scope": _clean_specialist_fields(scope),
        "role_contexts": role_contexts,
        "segments": segments,
        "panelist_profiles": panelist_profiles,
        "panelist_profile_count": grounded_profile_count,
    }


def _v3_bound_job_file(
    run_dir: Path,
    binding: Mapping[str, Any],
    *,
    expected_path: str,
    context: str,
) -> tuple[dict[str, Any], bytes]:
    if set(binding) != {"status", "path", "content_hash", "record_count"}:
        raise DashboardInputError(f"{context} dispatched binding keys do not match the allowlist")
    if binding.get("status") != "dispatched":
        raise DashboardInputError(f"{context} dispatched binding status is invalid")
    if binding.get("path") != expected_path:
        raise DashboardInputError(f"{context} path must be {expected_path}")
    path = run_dir / expected_path
    if (
        not path.is_file()
        or path.is_symlink()
        or path.resolve().parent != run_dir.resolve()
    ):
        raise DashboardInputError(f"{context} is missing safe {expected_path}")
    raw = path.read_bytes()
    expected_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    if binding.get("content_hash") != expected_hash:
        raise DashboardInputError(f"{context} content_hash does not match raw file bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardInputError(f"{context} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise DashboardInputError(f"{context} must contain a JSON object")
    jobs = payload.get("synthetic_replicate_jobs")
    record_count = binding.get("record_count")
    if (
        not isinstance(jobs, list)
    ):
        raise DashboardInputError(
            f"{context} must bind a successful worker-ready job envelope"
        )
    if (
        isinstance(record_count, bool)
        or not isinstance(record_count, int)
        or record_count != len(jobs)
    ):
        raise DashboardInputError(
            f"{context} record_count must equal synthetic_replicate_jobs"
        )
    return payload, raw


def _v3_diagnostic_payload(
    source: Mapping[str, Any],
    *,
    scope: str,
    run_claim_authority: bool,
) -> dict[str, Any]:
    fidelity = source["fidelity"]
    status = str(fidelity["status"])
    diagnostic = {
        "diagnostic_scope": scope,
        "requested_slot_count": (
            len(source["selected_slot_ids"])
            if "selected_slot_ids" in source
            else len(source["assignments"])
        ),
        "structural_groups": copy.deepcopy(source["structural_group_diagnostics"]),
        "must_cover": copy.deepcopy(source["must_cover_diagnostics"]),
        "fidelity_status": status,
        "all_must_cover_groups_represented": bool(
            fidelity["all_must_cover_groups_represented"]
        ),
        "observed_maximum_absolute_deviation": fidelity[
            "observed_maximum_absolute_deviation"
        ],
        "claim_effect": source["claim_effect"],
        "claim_language": V3_ALLOCATION_CLAIMS[status],
        "run_claim_authority": run_claim_authority,
        "authority_label": (
            "Selected-for-dispatch diagnostics are the run-claim authority."
            if run_claim_authority
            else "Full-reserve diagnostics show capacity, not realized dispatch."
        ),
    }
    if fidelity["allocation_basis"] == "structural_frame":
        diagnostic["maximum_absolute_deviation"] = fidelity[
            "maximum_absolute_deviation"
        ]
    if source["claim_effect"] == "directional_tier_1_for_this_run":
        diagnostic["user_decision"] = V3_ALLOCATION_CONTINUATION
    return diagnostic


def _v3_allocation_payload_from_validated_sources(
    *,
    manifest: Mapping[str, Any],
    composition: Mapping[str, Any],
    stage_envelopes: Mapping[str, Mapping[str, Any] | None],
    stage_statuses: Mapping[str, str],
) -> dict[str, Any]:
    """Compile display-only allocation evidence from already validated authorities."""

    rosters = manifest["audience_profile_rosters"]
    stages: list[dict[str, Any]] = []
    for stage, roster_key in (
        ("screening", "screening"),
        ("boundary", "boundary_reserve"),
        ("finalist", "finalist_reserve"),
    ):
        status = stage_statuses[stage]
        if status == "not_applicable":
            stages.append(
                {
                    "stage": "boundary",
                    "dispatch_status": "not_applicable",
                    "message": (
                        "Not applicable — complete exposure has no boundary stage"
                    ),
                }
            )
            continue
        full = rosters[roster_key]
        diagnostics = [
            _v3_diagnostic_payload(
                full,
                scope="full_reserve",
                run_claim_authority=False,
            )
        ]
        envelope = stage_envelopes.get(stage)
        if status == "dispatched":
            if not isinstance(envelope, Mapping):
                raise DashboardInputError(
                    f"{stage} dispatched allocation envelope is unavailable"
                )
            diagnostics.append(
                _v3_diagnostic_payload(
                    envelope["audience_allocation_subset"],
                    scope="selected_for_dispatch",
                    run_claim_authority=True,
                )
            )
        stages.append(
            {
                "stage": stage,
                "dispatch_status": status,
                "diagnostics": diagnostics,
            }
        )
    structural_semantics = sorted(
        {
            str(item["weight_semantic"])
            for item in composition.get("structural_groups", [])
            if isinstance(item, Mapping) and _non_empty_string(item.get("weight_semantic"))
        }
    )
    overlay_semantics = sorted(
        {
            str(item["overlay_weight_semantic"])
            for item in composition.get("profiles", [])
            if isinstance(item, Mapping)
            and _non_empty_string(item.get("overlay_weight_semantic"))
        }
    )
    package = manifest["audience_package"]
    return {
        "schema_version": "audience-run-allocation-dashboard-v1",
        "package": {
            "panel_id": package["panel_id"],
            "panel_version": package["panel_version"],
            "tier": package["tier"],
        },
        "reusable_weight_semantics": {
            "structural": structural_semantics,
            "overlay": overlay_semantics,
        },
        "stages": stages,
        "disclaimer": V3_ALLOCATION_DISCLAIMER,
    }


def _validated_v3_run_allocation(
    *,
    run_dir: Path,
    manifest: Mapping[str, Any],
    composition: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
    screening: Mapping[str, Any],
    boundary: Mapping[str, Any] | None,
    finalists: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Authenticate Task 6 stage bindings and compile run-allocation evidence."""

    try:
        authority = validate_v3_dispatch_authority(manifest)
        _resolution, _resolution_bytes = load_reusable_v3_audience_resolution(
            run_dir / "audience" / "resolution.json"
        )
    except ValueError as exc:
        raise DashboardInputError(f"v3 audience allocation authority is invalid: {exc}") from exc
    outputs = manifest.get("outputs")
    index = outputs.get("audience_allocation_jobs") if isinstance(outputs, Mapping) else None
    if not isinstance(index, Mapping):
        raise DashboardInputError(
            "study-manifest.json outputs.audience_allocation_jobs is required for v3"
        )
    if set(index) != {"schema_version", "screening", "boundary", "finalist"}:
        raise DashboardInputError(
            "audience_allocation_jobs keys do not match the allowlist"
        )
    if index.get("schema_version") != V3_ALLOCATION_JOB_INDEX_VERSION:
        raise DashboardInputError(
            "audience_allocation_jobs schema_version is unsupported"
        )

    response_types = {
        str(item.get("record_type"))
        for item in responses
        if isinstance(item, Mapping)
    }
    stage_statuses: dict[str, str] = {}
    stage_envelopes: dict[str, Mapping[str, Any] | None] = {}
    job_files: dict[str, bytes] = {}
    accepted_job_authority: list[Mapping[str, Any]] = []

    def validate_status(
        stage: str,
        record: Any,
        *,
        has_result: bool,
        response_type: str,
    ) -> str:
        if not isinstance(record, Mapping):
            raise DashboardInputError(
                f"audience_allocation_jobs.{stage} must be an object"
            )
        status = record.get("status")
        if status == "not_dispatched":
            if set(record) != {"status"}:
                raise DashboardInputError(
                    f"{stage} not_dispatched binding keys do not match the allowlist"
                )
            if has_result or response_type in response_types:
                raise DashboardInputError(
                    f"{stage} cannot be not_dispatched when accepted results or responses exist"
                )
            return status
        if status != "dispatched":
            raise DashboardInputError(
                f"audience_allocation_jobs.{stage}.status is unsupported"
            )
        if not has_result or response_type not in response_types:
            raise DashboardInputError(
                f"{stage} dispatched binding requires accepted results and responses"
            )
        return status

    screening_record = index["screening"]
    screening_status = validate_status(
        "screening",
        screening_record,
        has_result=bool(screening),
        response_type="screening_response",
    )
    if screening_status == "dispatched":
        screening_jobs, raw = _v3_bound_job_file(
            run_dir,
            screening_record,
            expected_path="screening-jobs.json",
            context="audience_allocation_jobs.screening",
        )
        try:
            screening_jobs = validate_v3_jobs_envelope(
                screening_jobs,
                allocation_plan=authority["audience_profile_rosters"]["screening"],
                authority=authority,
                audience_resolution=run_dir / "audience" / "resolution.json",
                dispatch_authority=authority,
            )
        except ValueError as exc:
            raise DashboardInputError(
                f"screening worker-ready job envelope is invalid: {exc}"
            ) from exc
        stage_envelopes["screening"] = screening_jobs
        accepted_job_authority.extend(screening_jobs["synthetic_replicate_jobs"])
        job_files["screening-jobs.json"] = raw
    else:
        stage_envelopes["screening"] = None
    stage_statuses["screening"] = screening_status

    boundary_record = index["boundary"]
    if manifest.get("method") == "complete_exposure":
        if boundary_record != {
            "status": "not_applicable",
            "reason": "method_complete_exposure",
        }:
            raise DashboardInputError(
                "complete exposure requires exact not_applicable boundary allocation binding"
            )
        if boundary is not None or "boundary_response" in response_types:
            raise DashboardInputError(
                "complete exposure forbids boundary results and accepted responses"
            )
        if any(run_dir.glob("boundary-wave-*-jobs.json")):
            raise DashboardInputError(
                "complete exposure forbids a boundary allocation job file"
            )
        stage_statuses["boundary"] = "not_applicable"
        stage_envelopes["boundary"] = None
    else:
        if not isinstance(boundary_record, Mapping):
            raise DashboardInputError(
                "audience_allocation_jobs.boundary must be an object"
            )
        boundary_status = boundary_record.get("status")
        if boundary_status == "not_dispatched":
            validate_status(
                "boundary",
                boundary_record,
                has_result=boundary is not None,
                response_type="boundary_response",
            )
            stage_envelopes["boundary"] = None
        elif boundary_status == "dispatched":
            if set(boundary_record) != {"status", "waves"}:
                raise DashboardInputError(
                    "boundary dispatched binding keys do not match the allowlist"
                )
            validate_status(
                "boundary",
                {"status": "dispatched", "path": "", "content_hash": "", "record_count": 0},
                has_result=boundary is not None,
                response_type="boundary_response",
            )
            waves = boundary_record.get("waves")
            if not isinstance(waves, list) or not waves:
                raise DashboardInputError(
                    "boundary dispatched binding requires ordered contiguous waves"
                )
            prior_selected: list[str] = []
            latest: Mapping[str, Any] | None = None
            for position, wave_binding in enumerate(waves, 1):
                if not isinstance(wave_binding, Mapping) or set(wave_binding) != {
                    "wave",
                    "path",
                    "content_hash",
                    "record_count",
                }:
                    raise DashboardInputError(
                        f"boundary wave {position} binding keys do not match the allowlist"
                    )
                if wave_binding.get("wave") != position:
                    raise DashboardInputError(
                        "boundary waves must be ordered and contiguous from wave 1"
                    )
                filename = f"boundary-wave-{position:04d}-jobs.json"
                jobs, raw = _v3_bound_job_file(
                    run_dir,
                    {
                        "status": "dispatched",
                        "path": wave_binding["path"],
                        "content_hash": wave_binding["content_hash"],
                        "record_count": wave_binding["record_count"],
                    },
                    expected_path=filename,
                    context=f"audience_allocation_jobs.boundary wave {position}",
                )
                try:
                    jobs = validate_v3_jobs_envelope(
                        jobs,
                        allocation_plan=authority["audience_profile_rosters"][
                            "boundary_reserve"
                        ],
                        authority=authority,
                        audience_resolution=run_dir / "audience" / "resolution.json",
                        dispatch_authority=screening,
                    )
                except ValueError as exc:
                    raise DashboardInputError(
                        f"boundary wave {position} worker-ready job envelope is invalid: {exc}"
                    ) from exc
                selected = jobs["audience_allocation_subset"]["selected_slot_ids"]
                newly_authorized = jobs["audience_dispatch"][
                    "newly_authorized_slot_ids"
                ]
                expected_wave_slots = [
                    assignment["slot_id"]
                    for assignment in authority[
                        "audience_profile_rosters"
                    ]["boundary_reserve"]["assignments"]
                    if re.fullmatch(
                        r"boundary-wave-(0[1-9]|[1-9][0-9]+)-job-[0-9]{4}",
                        assignment["slot_id"],
                    )
                    and int(
                        re.fullmatch(
                            r"boundary-wave-(0[1-9]|[1-9][0-9]+)-job-[0-9]{4}",
                            assignment["slot_id"],
                        ).group(1)
                    )
                    == position
                ]
                if newly_authorized != expected_wave_slots:
                    raise DashboardInputError(
                        f"boundary wave {position} must contain exactly its complete frozen wave"
                    )
                if selected != [*prior_selected, *newly_authorized]:
                    raise DashboardInputError(
                        f"boundary wave {position} does not preserve its prior successful envelope authority"
                    )
                prior_selected = list(selected)
                latest = jobs
                jobs_by_slot = {
                    str(job["audience_slot_id"]): job
                    for job in jobs["synthetic_replicate_jobs"]
                }
                accepted_job_authority.extend(
                    jobs_by_slot[str(slot_id)]
                    for slot_id in newly_authorized
                )
                job_files[filename] = raw
            stage_envelopes["boundary"] = latest
        else:
            raise DashboardInputError(
                "audience_allocation_jobs.boundary.status is unsupported"
            )
        stage_statuses["boundary"] = str(boundary_status)

    finalist_record = index["finalist"]
    finalist_status = validate_status(
        "finalist",
        finalist_record,
        has_result=bool(finalists),
        response_type="finalist_response",
    )
    if finalist_status == "dispatched":
        finalist_jobs, raw = _v3_bound_job_file(
            run_dir,
            finalist_record,
            expected_path="finalist-jobs.json",
            context="audience_allocation_jobs.finalist",
        )
        try:
            finalist_jobs = validate_v3_jobs_envelope(
                finalist_jobs,
                allocation_plan=authority["audience_profile_rosters"][
                    "finalist_reserve"
                ],
                authority=authority,
                audience_resolution=run_dir / "audience" / "resolution.json",
                dispatch_authority=finalists,
            )
        except ValueError as exc:
            raise DashboardInputError(
                f"finalist worker-ready job envelope is invalid: {exc}"
            ) from exc
        stage_envelopes["finalist"] = finalist_jobs
        accepted_job_authority.extend(finalist_jobs["synthetic_replicate_jobs"])
        job_files["finalist-jobs.json"] = raw
    else:
        stage_envelopes["finalist"] = None
    stage_statuses["finalist"] = finalist_status

    response_binding_errors = validate_response_job_bindings(
        accepted_job_authority,
        responses,
        require_exact_set=False,
    )
    if response_binding_errors:
        raise DashboardInputError(
            "accepted response job binding is invalid: "
            + "; ".join(response_binding_errors)
        )
    jobs_by_replicate = {
        str(job["synthetic_replicate_id"]): job
        for job in accepted_job_authority
    }
    for response_index, response in enumerate(responses):
        replicate_id = str(response.get("synthetic_replicate_id"))
        job = jobs_by_replicate[replicate_id]
        for field in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            if response.get(field) != job.get(field):
                raise DashboardInputError(
                    f"accepted response[{response_index}].{field} does not match "
                    "its manifest-bound job"
                )

    dispatch_audit_path = run_dir / "dispatch-audit.jsonl"
    if dispatch_audit_path.is_file():
        dispatch_audit = _load_jsonl(dispatch_audit_path)
        runtime = manifest.get("runtime")
        retry_limit = (
            runtime.get("retry_limit_per_return")
            if isinstance(runtime, Mapping)
            else None
        )
        audit_binding_errors = validate_dispatch_audit_job_bindings(
            accepted_job_authority,
            responses,
            dispatch_audit,
            retry_limit_per_return=retry_limit,
        )
        if audit_binding_errors:
            raise DashboardInputError(
                "dispatch audit accepted/exhausted job binding is invalid: "
                + "; ".join(audit_binding_errors)
            )
    return (
        _v3_allocation_payload_from_validated_sources(
            manifest=authority,
            composition=composition,
            stage_envelopes=stage_envelopes,
            stage_statuses=stage_statuses,
        ),
        job_files,
    )


def _validate_lineage_integrity(
    run_dir: Path,
    manifest: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
) -> None:
    """Bind source downloads to accepted records and every provider/model call."""

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping) or not any(
        name in outputs for name in CANONICAL_LINEAGE_FILES
    ):
        return
    records_by_filename: dict[str, Sequence[Mapping[str, Any]]] = {
        "panelist-responses.jsonl": list(responses)
    }
    content_by_filename: dict[str, bytes] = {}
    try:
        for filename in CANONICAL_LINEAGE_FILES.values():
            path = run_dir / filename
            content_by_filename[filename] = path.read_bytes()
            if filename != "panelist-responses.jsonl":
                records_by_filename[filename] = _load_jsonl_allow_empty(path)
        validate_bound_lineage(
            manifest, records_by_filename, content_by_filename
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DashboardInputError(str(exc)) from exc
    return

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        return
    canonical = {
        "accepted_responses": "panelist-responses.jsonl",
        "raw_provider_returns": "raw-provider-returns.jsonl",
        "rejected_attempts": "rejected-attempts.jsonl",
    }
    present = {name for name in canonical if name in outputs}
    if not present:
        return
    if present != set(canonical):
        raise DashboardInputError(
            "study-manifest.json outputs must bind accepted responses, raw provider "
            "returns, and rejected attempts together"
        )

    bound_records: dict[str, list[dict[str, Any]]] = {}
    for name, filename in canonical.items():
        binding = outputs.get(name)
        if not isinstance(binding, Mapping) or binding.get("path") != filename:
            raise DashboardInputError(
                f"study-manifest.json outputs.{name}.path must be {filename}"
            )
        path = run_dir / filename
        records = (
            list(responses)
            if name == "accepted_responses"
            else _load_jsonl_allow_empty(path)
        )
        if not path.is_file() or binding.get("content_hash") != _sha256(path):
            raise DashboardInputError(
                f"study-manifest.json outputs.{name}.content_hash does not match {filename}"
            )
        if binding.get("record_count") != len(records):
            raise DashboardInputError(
                f"study-manifest.json outputs.{name}.record_count does not match {filename}"
            )
        bound_records[name] = records

    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(bound_records["raw_provider_returns"]):
        provider_id = _require_non_empty_string(
            raw.get("provider_return_id"),
            f"raw-provider-returns.jsonl line {index + 1}.provider_return_id",
        )
        if provider_id in raw_by_id:
            raise DashboardInputError(
                f"duplicate raw provider_return_id: {provider_id}"
            )
        if not isinstance(raw.get("accepted"), bool):
            raise DashboardInputError(
                f"raw-provider-returns.jsonl line {index + 1}.accepted must be a boolean"
            )
        validation_errors = raw.get("validation_errors")
        if not isinstance(validation_errors, list):
            raise DashboardInputError(
                f"raw-provider-returns.jsonl line {index + 1}.validation_errors must be an array"
            )
        if raw["accepted"] == bool(validation_errors):
            raise DashboardInputError(
                f"raw-provider-returns.jsonl line {index + 1} acceptance and validation errors disagree"
            )
        raw_by_id[provider_id] = raw

    rejected_by_id: dict[str, Mapping[str, Any]] = {}
    for index, rejected in enumerate(bound_records["rejected_attempts"]):
        provider_id = _require_non_empty_string(
            rejected.get("provider_return_id"),
            f"rejected-attempts.jsonl line {index + 1}.provider_return_id",
        )
        if provider_id in rejected_by_id:
            raise DashboardInputError(
                f"duplicate rejected provider_return_id: {provider_id}"
            )
        raw = raw_by_id.get(provider_id)
        if raw is None or raw.get("accepted") is not False:
            raise DashboardInputError(
                "rejected-attempts.jsonl must reference only rejected raw provider returns"
            )
        for field in (
            "synthetic_replicate_id",
            "reviewer_dispatch_id",
            "stage",
            "position_seen",
            "attempt_number",
            "validation_errors",
        ):
            if rejected.get(field) != raw.get(field):
                raise DashboardInputError(
                    f"rejected attempt {provider_id} field {field} does not match its raw return"
                )
        rejected_by_id[provider_id] = rejected
    if set(rejected_by_id) != {
        provider_id for provider_id, raw in raw_by_id.items() if raw["accepted"] is False
    }:
        raise DashboardInputError(
            "rejected-attempts.jsonl must exactly cover rejected raw provider returns"
        )

    referenced_attempts: set[str] = set()
    for response_index, response in enumerate(responses, 1):
        attempts = response.get("runtime_attempts")
        if not isinstance(attempts, list):
            raise DashboardInputError(
                f"panelist-responses.jsonl line {response_index}.runtime_attempts must be an array"
            )
        for attempt_index, attempt in enumerate(attempts, 1):
            if not isinstance(attempt, Mapping):
                raise DashboardInputError(
                    f"panelist-responses.jsonl line {response_index} runtime attempt {attempt_index} must be an object"
                )
            provider_id = _require_non_empty_string(
                attempt.get("provider_return_id"),
                f"panelist-responses.jsonl line {response_index} runtime attempt {attempt_index}.provider_return_id",
            )
            raw = raw_by_id.get(provider_id)
            if raw is None:
                raise DashboardInputError(
                    f"runtime attempt {provider_id} has no bound raw provider return"
                )
            expected_outcome = "accepted" if raw["accepted"] else "rejected"
            expected = {
                "synthetic_replicate_id": response.get("synthetic_replicate_id"),
                "reviewer_dispatch_id": response.get("reviewer_dispatch_id"),
                "stage": attempt.get("stage"),
                "position_seen": (
                    attempt.get("position_seen") if attempt.get("stage") == "reaction" else None
                ),
                "attempt_number": attempt.get("attempt_number"),
                "validation_errors": attempt.get("validation_errors"),
            }
            if any(raw.get(field) != value for field, value in expected.items()):
                raise DashboardInputError(
                    f"runtime attempt {provider_id} identity does not match its raw provider return"
                )
            if attempt.get("outcome") != expected_outcome:
                raise DashboardInputError(
                    f"runtime attempt {provider_id} outcome does not match its raw provider return"
                )
            referenced_attempts.add(provider_id)
    if referenced_attempts != set(raw_by_id):
        raise DashboardInputError(
            "raw-provider-returns.jsonl must exactly cover response runtime attempts"
        )

    usage = manifest.get("usage")
    if not isinstance(usage, Mapping):
        raise DashboardInputError(
            "lineage-bound study-manifest.json requires usage accounting"
        )
    expected_usage = {
        "accepted_response_records": len(responses),
        "accepted_unique_replicates": len(
            {str(item.get("synthetic_replicate_id")) for item in responses}
        ),
        "unique_job_slots_dispatched": len(
            {str(item.get("reviewer_dispatch_id")) for item in responses}
        ),
        "total_model_calls": len(raw_by_id),
        "rejected_attempts": len(rejected_by_id),
    }
    for field, expected in expected_usage.items():
        if usage.get(field) != expected:
            raise DashboardInputError(
                f"study-manifest.json usage.{field} must equal the bound lineage count {expected}"
            )
    planned = usage.get("unique_job_slots_planned")
    if (
        isinstance(planned, bool)
        or not isinstance(planned, int)
        or planned < expected_usage["unique_job_slots_dispatched"]
    ):
        raise DashboardInputError(
            "study-manifest.json usage.unique_job_slots_planned must cover dispatched job slots"
        )


def _validate_run_paths(run_dir: Path, template_path: Path) -> tuple[Path, Path]:
    run_dir = run_dir.expanduser().resolve()
    template_path = template_path.expanduser().resolve()
    if not run_dir.is_dir():
        raise DashboardInputError(f"run directory not found: {run_dir}")
    for filename in REQUIRED_INPUTS:
        path = run_dir / filename
        if not path.is_file():
            raise DashboardInputError(f"missing required dashboard input: {filename}")
    if not template_path.is_file():
        raise DashboardInputError(f"dashboard template not found: {template_path}")
    return run_dir, template_path


def _validate_study_ids(
    study_id: str,
    named_payloads: Iterable[tuple[str, Mapping[str, Any]]],
    responses: Sequence[Mapping[str, Any]],
) -> None:
    for filename, payload in named_payloads:
        if payload.get("study_id") != study_id:
            raise DashboardInputError(
                f"{filename} study_id must match study-manifest.json study_id"
            )
    for index, response in enumerate(responses, 1):
        if response.get("study_id") != study_id:
            raise DashboardInputError(
                f"panelist-responses.jsonl line {index} study_id must match the manifest"
            )


def _validate_cross_stage_integrity(
    manifest: Mapping[str, Any],
    roster: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
    screening: Mapping[str, Any],
    boundary: Mapping[str, Any] | None,
    finalists: Mapping[str, Any],
    feedback: Mapping[str, Any],
) -> None:
    method = manifest.get("method")
    if method not in SUPPORTED_METHODS:
        raise DashboardInputError(
            "unsupported study method; expected complete_exposure or "
            "partial_exposure_maxdiff"
        )

    raw_creatives = _require_sequence(
        roster.get("creatives"), "creative-roster.json creatives"
    )
    roster_ids: list[str] = []
    for index, raw in enumerate(raw_creatives):
        creative = _require_mapping(raw, f"creative-roster.json creatives[{index}]")
        roster_ids.append(
            _require_non_empty_string(
                creative.get("variation_id", creative.get("creative_id")),
                f"creative-roster.json creatives[{index}].variation_id",
            )
        )
    if len(set(roster_ids)) != len(roster_ids):
        raise DashboardInputError("creative-roster.json variation IDs must be unique")
    roster_set = set(roster_ids)

    for field in ("utilities", "top_k_inclusion_frequencies", "classifications"):
        values = screening.get(field)
        if not isinstance(values, Mapping) or set(map(str, values)) != roster_set:
            raise DashboardInputError(
                f"screening-model-results.json {field} keys must match the creative roster"
            )
    ranked_ids = _require_id_list(
        screening.get("ranked_ids"), "screening-model-results.json ranked_ids"
    )
    if len(ranked_ids) != len(roster_ids) or set(ranked_ids) != roster_set:
        raise DashboardInputError(
            "screening-model-results.json ranked_ids must be a permutation of the creative roster"
        )

    usable_field = (
        "usable_complete_exposure_observation"
        if method == "complete_exposure"
        else "usable_maxdiff_block"
    )
    accepted_usable_screening = sum(
        1
        for response in responses
        if response.get("record_type") == "screening_response"
        and response.get(usable_field) is True
    )
    diagnostics = screening.get("model_diagnostics")
    if isinstance(diagnostics, Mapping):
        reported_usable = diagnostics.get("usable_observation_count")
        if isinstance(reported_usable, int) and reported_usable != accepted_usable_screening:
            raise DashboardInputError(
                "screening-model-results.json usable_observation_count does not match "
                "accepted usable screening responses"
            )
    screening_validity = screening.get("validity_status")
    if manifest.get("validity_status") != screening_validity:
        raise DashboardInputError(
            "study-manifest.json validity_status must match "
            "screening-model-results.json validity_status"
        )
    if boundary is not None and boundary.get("status") == "resolved" and screening_validity != "valid":
        raise DashboardInputError(
            "a resolved boundary result requires a valid frozen screening result"
        )
    decision = finalists.get("roster_decision")
    if not isinstance(decision, Mapping):
        raise DashboardInputError("finalist-results.json roster_decision must be an object")
    decision_status = decision.get("status")
    if decision_status not in APPROVED_ROSTER_STATES | {"awaiting_approval"}:
        raise DashboardInputError("finalist-results.json roster_decision.status is unsupported")
    override = decision.get("override", False)
    if not isinstance(override, bool):
        raise DashboardInputError(
            "finalist-results.json roster_decision.override must be a boolean"
        )
    if (decision_status == "approved_with_override") != override:
        raise DashboardInputError(
            "finalist-results.json status approved_with_override must be used if and "
            "only if roster_decision.override is true"
        )
    if decision_status in APPROVED_ROSTER_STATES:
        _require_aware_datetime(
            decision.get("approved_at"),
            "finalist-results.json roster_decision.approved_at",
        )

    finalist_ids = _require_id_list(
        finalists.get("approved_finalist_ids"),
        "finalist-results.json approved_finalist_ids",
    )
    requested_size = manifest.get("requested_shortlist_size")
    if isinstance(requested_size, bool) or not isinstance(requested_size, int) or requested_size < 1:
        raise DashboardInputError(
            "study-manifest.json requested_shortlist_size must be a positive integer"
        )
    if (
        len(finalist_ids) != requested_size
        or len(set(finalist_ids)) != len(finalist_ids)
        or not set(finalist_ids).issubset(roster_set)
    ):
        raise DashboardInputError(
            "finalist roster must contain unique roster IDs equal to requested_shortlist_size"
        )
    finalist_set = set(finalist_ids)

    for line_number, response in enumerate(responses, 1):
        if response.get("record_type") != "finalist_response":
            continue
        assigned = _require_id_list(
            response.get("assigned_variation_ids"),
            f"panelist-responses.jsonl line {line_number} finalist response assignment",
        )
        shown = _require_id_list(
            response.get("shown_order"),
            f"panelist-responses.jsonl line {line_number} finalist response shown_order",
        )
        raw_reviews = _require_sequence(
            response.get("finalist_reviews"),
            f"panelist-responses.jsonl line {line_number} finalist response reviews",
        )
        review_ids = [
            str(_require_mapping(item, "finalist response review").get("variation_id", ""))
            for item in raw_reviews
        ]
        ranking = _require_id_list(
            response.get("final_preference_ranking"),
            f"panelist-responses.jsonl line {line_number} finalist response ranking",
        )
        if any(
            len(values) != len(finalist_ids) or set(values) != finalist_set
            for values in (assigned, shown, review_ids, ranking)
        ):
            raise DashboardInputError(
                f"panelist-responses.jsonl line {line_number} finalist response must exactly "
                "match the finalist set in assignment, shown order, reviews, and ranking"
            )

    if decision_status in APPROVED_ROSTER_STATES:
        accepted_records = finalists.get("accepted_response_records")
        accepted_replicates = finalists.get("accepted_unique_replicates")
        job_slots = finalists.get("unique_job_slots_consumed")
        total_calls = finalists.get("total_model_calls")
        accounting = {
            "accepted_response_records": accepted_records,
            "accepted_unique_replicates": accepted_replicates,
            "unique_job_slots_consumed": job_slots,
            "total_model_calls": total_calls,
        }
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in accounting.values()
        ):
            raise DashboardInputError(
                "finalist accounting fields must be positive integers after approval"
            )
        finalist_responses = [
            response
            for response in responses
            if response.get("record_type") == "finalist_response"
        ]
        actual_replicates = {
            str(response.get("synthetic_replicate_id"))
            for response in finalist_responses
        }
        actual_slots = {
            str(response.get("reviewer_dispatch_id"))
            for response in finalist_responses
        }
        actual_calls = sum(
            len(response.get("runtime_attempts", []))
            for response in finalist_responses
            if isinstance(response.get("runtime_attempts"), list)
        )
        if (
            accepted_records != len(finalist_responses)
            or accepted_replicates != len(actual_replicates)
            or job_slots != len(actual_slots)
            or (actual_calls > 0 and total_calls != actual_calls)
            or (actual_calls == 0 and total_calls < accepted_records)
        ):
            raise DashboardInputError(
                "finalist accounting must match accepted response records, unique "
                "replicates, unique job slots, and runtime model calls"
            )
        counts = finalists.get("first_choice_counts")
        shares = finalists.get("conditional_first_choice_share")
        if not isinstance(counts, Mapping) or set(map(str, counts)) != finalist_set:
            raise DashboardInputError(
                "finalist first-choice counts must have exactly the finalist roster keys"
            )
        if not isinstance(shares, Mapping) or set(map(str, shares)) != finalist_set:
            raise DashboardInputError(
                "finalist conditional shares must have exactly the finalist roster keys"
            )
        normalized_counts: dict[str, int] = {}
        normalized_shares: dict[str, float] = {}
        for creative_id in finalist_ids:
            count = counts.get(creative_id)
            share = shares.get(creative_id)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise DashboardInputError(
                    "finalist first-choice counts must be non-negative integers"
                )
            if (
                isinstance(share, bool)
                or not isinstance(share, (int, float))
                or not math.isfinite(float(share))
            ):
                raise DashboardInputError("finalist conditional shares must be finite numbers")
            normalized_counts[creative_id] = count
            normalized_shares[creative_id] = float(share)
        if sum(normalized_counts.values()) != accepted_records:
            raise DashboardInputError(
                "finalist first-choice counts must sum to accepted_response_records"
            )
        if not math.isclose(
            sum(normalized_shares.values()), 1.0, rel_tol=0.0, abs_tol=1e-9
        ):
            raise DashboardInputError("finalist conditional shares must be normalized to 1")
        for creative_id in finalist_ids:
            expected = normalized_counts[creative_id] / accepted_records
            if not math.isclose(
                normalized_shares[creative_id], expected, rel_tol=0.0, abs_tol=1e-9
            ):
                raise DashboardInputError(
                    "finalist conditional shares must be derived from first-choice counts "
                    "and accepted_response_records"
                )

    if isinstance(decision, Mapping) and decision.get("status") in APPROVED_ROSTER_STATES:
        if screening_validity != "valid" and decision.get("override") is not True:
            raise DashboardInputError(
                "an approved finalist roster after a non-valid screening result requires "
                "an explicit human override"
            )

    response_by_id: dict[str, Mapping[str, Any]] = {}
    for line_number, response in enumerate(responses, 1):
        response_id = _require_non_empty_string(
            response.get("response_id"),
            f"panelist-responses.jsonl line {line_number}.response_id",
        )
        if response_id in response_by_id:
            raise DashboardInputError(f"duplicate response_id: {response_id}")
        response_by_id[response_id] = response
    raw_themes = _require_sequence(
        feedback.get("themes"), "feedback-synthesis.json themes"
    )
    stage_types = {stage: record_type for record_type, (stage, _) in STAGES.items()}
    feedback_types_by_creative: dict[str, set[str]] = {
        creative_id: set() for creative_id in roster_set
    }

    def has_written_value(value: Any) -> bool:
        if _non_empty_string(value):
            return True
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ) and any(_non_empty_string(item) for item in value)

    creatives_with_written_reactions: set[str] = set()
    creatives_with_friction_evidence: set[str] = set()
    for response in responses:
        for reaction in response.get("per_creative_reactions", []):
            if not isinstance(reaction, Mapping):
                continue
            creative_id = str(reaction.get("variation_id", ""))
            if any(
                has_written_value(reaction.get(field))
                for field in (
                    "immediate_reaction",
                    "noticed_or_understood_first",
                    "strongest_positive_signal",
                    "strongest_negative_signal",
                )
            ):
                creatives_with_written_reactions.add(creative_id)
            if has_written_value(reaction.get("strongest_negative_signal")) or reaction.get(
                "judgment_status"
            ) == "unable_to_judge":
                creatives_with_friction_evidence.add(creative_id)
        for review in response.get("finalist_reviews", []):
            if not isinstance(review, Mapping):
                continue
            creative_id = str(review.get("variation_id", ""))
            if has_written_value(review.get("immediate_reaction")) or has_written_value(
                review.get("feedback")
            ):
                creatives_with_written_reactions.add(creative_id)
            if str(review.get("feedback_type", "")).lower() in {
                "negative",
                "friction",
                "disagreement",
            }:
                creatives_with_friction_evidence.add(creative_id)
        comparison = response.get("comparative_choice")
        if isinstance(comparison, Mapping):
            weakest_id = str(comparison.get("weakest_variation_id", ""))
            if weakest_id and has_written_value(comparison.get("weakest_reason")):
                creatives_with_friction_evidence.add(weakest_id)
        pairwise = response.get("pairwise_choice")
        if isinstance(pairwise, Mapping) and pairwise.get("status") in {
            "tie",
            "no_meaningful_difference",
            "unable_to_judge",
        }:
            creatives_with_friction_evidence.update(
                str(item) for item in response.get("assigned_variation_ids", [])
            )

    for index, raw_theme in enumerate(raw_themes):
        theme = _require_mapping(raw_theme, f"feedback-synthesis.json themes[{index}]")
        context = f"feedback-synthesis.json themes[{index}]"
        stage = str(theme.get("stage", ""))
        expected_type = stage_types.get(stage)
        if expected_type is None:
            raise DashboardInputError(
                f"{context} has an unsupported stage"
            )
        creative_id = _require_non_empty_string(theme.get("creative_id"), f"{context}.creative_id")
        if creative_id not in roster_set:
            raise DashboardInputError(
                f"{context} references a creative outside the roster"
            )
        segment_id = _require_non_empty_string(theme.get("segment_id"), f"{context}.segment_id")
        _require_non_empty_string(theme.get("lane"), f"{context}.lane")
        feedback_type = _require_non_empty_string(
            theme.get("feedback_type"), f"{context}.feedback_type"
        )
        if feedback_type not in FEEDBACK_TYPES:
            raise DashboardInputError(
                f"{context}.feedback_type must be strength, friction, disagreement, or next_test"
            )
        feedback_types_by_creative[creative_id].add(feedback_type)
        evidence_scope = _require_non_empty_string(
            theme.get("evidence_scope"), f"{context}.evidence_scope"
        )
        if evidence_scope not in FEEDBACK_EVIDENCE_SCOPES:
            raise DashboardInputError(
                f"{context}.evidence_scope must be single_source_observation or "
                "cross_response_pattern"
            )
        _require_non_empty_string(theme.get("theme"), f"{context}.theme")
        _require_non_empty_string(
            theme.get("why_it_matters"), f"{context}.why_it_matters"
        )
        recommended_action = _require_non_empty_string(
            theme.get("recommended_action"), f"{context}.recommended_action"
        )
        _require_non_empty_string(theme.get("source_type"), f"{context}.source_type")
        limitations = _require_sequence(theme.get("limitations"), f"{context}.limitations")
        if not limitations or any(not _non_empty_string(item) for item in limitations):
            raise DashboardInputError(
                f"{context}.limitations must contain at least one non-empty limitation"
            )
        for field in FEEDBACK_CLAIM_FIELDS:
            error = feedback_claim_error(field, theme.get(field))
            if error:
                raise DashboardInputError(f"{context}.{error}")
        action_error = feedback_action_error(feedback_type, recommended_action)
        if action_error:
            raise DashboardInputError(f"{context}.{action_error}")
        source_ids = _require_id_list(
            theme.get("response_ids"),
            f"{context}.response_ids",
        )
        if len(source_ids) != len(set(source_ids)):
            raise DashboardInputError(f"{context}.response_ids must not contain duplicates")
        if len(source_ids) == 1:
            if evidence_scope != "single_source_observation":
                raise DashboardInputError(
                    f"{context} cannot claim a cross-response pattern from one response"
                )
            single_source_label = " ".join(
                [recommended_action, *(str(item) for item in limitations)]
            ).lower()
            if "single-source" not in single_source_label and "single source" not in single_source_label:
                raise DashboardInputError(
                    f"{context} with one response_id must be visibly labeled as single-source"
                )
        elif evidence_scope != "cross_response_pattern":
            raise DashboardInputError(
                f"{context} with multiple response_ids must use cross_response_pattern"
            )
        for response_id in source_ids:
            response = response_by_id.get(response_id)
            if response is None:
                raise DashboardInputError(
                    f"feedback-synthesis.json themes[{index}] response ID {response_id} "
                    "does not exist in accepted responses"
                )
            if (
                response.get("record_type") != expected_type
                or creative_id not in response.get("assigned_variation_ids", [])
                or str(response.get("segment_id", "")) != segment_id
            ):
                raise DashboardInputError(
                    f"feedback-synthesis.json themes[{index}] response {response_id} "
                    "does not match the theme stage, creative assignment, and segment"
                )
        matching_exposures = sum(
            1
            for response in responses
            if response.get("record_type") == expected_type
            and creative_id in response.get("assigned_variation_ids", [])
            and str(response.get("segment_id", "")) == segment_id
        )
        exposed_base = theme.get("exposed_base")
        if not isinstance(exposed_base, Mapping):
            raise DashboardInputError(
                f"feedback-synthesis.json themes[{index}].exposed_base must be an object"
            )
        exposed_count = exposed_base.get("count")
        _require_non_empty_string(
            exposed_base.get("label"), f"{context}.exposed_base.label"
        )
        if (
            isinstance(exposed_count, bool)
            or not isinstance(exposed_count, int)
            or exposed_count != matching_exposures
            or exposed_count < len(set(source_ids))
        ):
            raise DashboardInputError(
                f"feedback-synthesis.json themes[{index}].exposed_base count must equal "
                "accepted response assignments for the stage, creative, and segment"
            )

    uncovered = sorted(
        creative_id
        for creative_id in creatives_with_written_reactions
        if not feedback_types_by_creative.get(creative_id, set())
        & {"strength", "friction"}
    )
    if uncovered:
        raise DashboardInputError(
            "feedback-synthesis.json coverage gap: creatives with usable written reactions "
            "require at least one strength or friction theme: " + ", ".join(uncovered)
        )
    if decision_status in APPROVED_ROSTER_STATES:
        for creative_id in finalist_ids:
            observed = feedback_types_by_creative.get(creative_id, set())
            required = {"strength", "next_test"}
            missing = sorted(required - observed)
            if (
                creative_id in creatives_with_friction_evidence
                and not observed & {"friction", "disagreement"}
            ):
                missing.append("friction or disagreement")
            if missing:
                raise DashboardInputError(
                    "feedback-synthesis.json coverage gap: approved top ad "
                    f"{creative_id} requires feedback types: {', '.join(missing)}"
                )

    usage = manifest.get("usage")
    if isinstance(usage, Mapping) and "total_model_calls" in usage:
        total_model_calls = usage.get("total_model_calls")
        if (
            isinstance(total_model_calls, bool)
            or not isinstance(total_model_calls, int)
            or total_model_calls < len(responses)
        ):
            raise DashboardInputError(
                "study-manifest.json usage.total_model_calls must be a non-negative integer "
                "at least as large as accepted response records"
            )
    audience = manifest.get("audience_lock")
    if isinstance(audience, Mapping):
        for field in ("unique_archetypes", "unique_grounded_context_profiles"):
            value = audience.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise DashboardInputError(
                    f"study-manifest.json audience_lock.{field} must be a non-negative integer"
                )
        observed_archetypes = {
            str(response.get("persona_archetype_id"))
            for response in responses
            if _non_empty_string(response.get("persona_archetype_id"))
        }
        if audience.get("unique_archetypes", 0) < len(observed_archetypes):
            raise DashboardInputError(
                "study-manifest.json audience_lock.unique_archetypes cannot be smaller "
                "than the distinct archetypes in accepted response records"
            )


def _name_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(item_id): str(name)
        for item_id, name in value.items()
        if _non_empty_string(item_id) and _non_empty_string(name)
    }


def _within_run_dir(run_dir: Path, path: Path) -> bool:
    return path == run_dir or run_dir in path.parents


def _resolve_media_path(run_dir: Path, raw_path: Any, context: str) -> Path:
    if not _non_empty_string(raw_path):
        raise DashboardInputError(f"{context} path must be a non-empty string")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise DashboardInputError(f"{context} file not found: {raw_path}") from exc
    if not _within_run_dir(run_dir, resolved):
        raise DashboardInputError(f"{context} must stay inside run directory")
    if not resolved.is_file():
        raise DashboardInputError(f"{context} must point to a file")
    return resolved


def _mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    return "application/octet-stream"


def _embed_file(run_dir: Path, raw_path: Any, context: str) -> dict[str, Any]:
    path = _resolve_media_path(run_dir, raw_path, context)
    byte_count = path.stat().st_size
    mime_type = _mime_type(path)
    content_hash = _sha256(path)
    if byte_count > MAX_EMBED_BYTES:
        return {
            "data_url": None,
            "mime_type": mime_type,
            "byte_count": byte_count,
            "content_hash": content_hash,
            "availability": "not embedded because the local file exceeds 20 MB",
        }
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "data_url": f"data:{mime_type};base64,{encoded}",
        "mime_type": mime_type,
        "byte_count": byte_count,
        "content_hash": content_hash,
        "availability": "embedded",
    }


def _require_renderable_image(embedded: Mapping[str, Any], context: str) -> None:
    mime_type = str(embedded.get("mime_type", "")).lower()
    if mime_type not in RENDERABLE_IMAGE_MIME_TYPES:
        raise DashboardInputError(
            f"{context} must use a renderable image MIME type; got "
            f"{mime_type or 'unknown'}"
        )


def _clean_specialist_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _clean_specialist_fields(item)
            for key, item in value.items()
            if str(key).lower() not in SPECIALIST_KEYS
            and "specialist_score" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_clean_specialist_fields(item) for item in value]
    return value


def _normalize_creatives(
    run_dir: Path, roster: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw_creatives = _require_sequence(roster.get("creatives"), "creative-roster.json creatives")
    if not raw_creatives:
        raise DashboardInputError("creative-roster.json creatives must not be empty")
    creatives: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    representation_ids: set[str] = set()
    for index, raw in enumerate(raw_creatives):
        creative = _require_mapping(raw, f"creative-roster.json creatives[{index}]")
        creative_id = creative.get("variation_id", creative.get("creative_id"))
        if not _non_empty_string(creative_id):
            raise DashboardInputError(
                f"creative-roster.json creatives[{index}] requires variation_id"
            )
        if creative_id in names:
            raise DashboardInputError(f"duplicate creative variation_id: {creative_id}")
        display_name = creative.get("display_name", creative.get("name", creative_id))
        if not _non_empty_string(display_name):
            display_name = creative_id
        names[creative_id] = display_name

        raw_media = creative.get("media", [])
        if raw_media is None:
            raw_media = []
        raw_media = _require_sequence(
            raw_media, f"creative-roster.json creatives[{index}].media"
        )
        media: list[dict[str, Any]] = []
        for media_index, item in enumerate(raw_media):
            medium = _require_mapping(
                item,
                f"creative-roster.json creatives[{index}].media[{media_index}]",
            )
            embedded = _embed_file(
                run_dir,
                medium.get("path"),
                f"creative {creative_id} media[{media_index}]",
            )
            _require_renderable_image(
                embedded,
                f"creative {creative_id} media[{media_index}]",
            )
            representation_id = _require_non_empty_string(
                medium.get("representation_id"),
                f"creative-roster.json creatives[{index}].media[{media_index}].representation_id",
            )
            if representation_id in representation_ids:
                raise DashboardInputError(
                    f"duplicate media representation_id: {representation_id}"
                )
            representation_ids.add(representation_id)
            declared_hash = _require_non_empty_string(
                medium.get("content_hash"),
                f"creative-roster.json creatives[{index}].media[{media_index}].content_hash",
            )
            if declared_hash != embedded["content_hash"]:
                raise DashboardInputError(
                    f"creative-roster.json media {representation_id} content_hash does not "
                    "match the actual media file"
                )
            media.append(
                {
                    "representation_id": representation_id,
                    "content_hash": declared_hash,
                    "kind": str(medium.get("kind", "image")),
                    "label": str(medium.get("label", "Supplied creative representation")),
                    "alt": str(
                        medium.get("alt", f"Supplied representation for {display_name}")
                    ),
                    **embedded,
                }
            )
        creatives.append(
            {
                "variation_id": creative_id,
                "display_name": display_name,
                "format": str(creative.get("format", "not specified")),
                "headline": str(creative.get("headline", "")),
                "body": str(creative.get("body", creative.get("copy", ""))),
                "cta": str(creative.get("cta", "")),
                "visual_description": str(creative.get("visual_description", "")),
                "input_fidelity": str(creative.get("input_fidelity", "not recorded")),
                "media": media,
            }
        )
    return creatives, names


def _creative_ref(creative_id: Any, names: Mapping[str, str]) -> dict[str, str]:
    stable_id = str(creative_id)
    return {"variation_id": stable_id, "display_name": names.get(stable_id, stable_id)}


def _normalize_responses(
    responses: Sequence[Mapping[str, Any]],
    creative_names: Mapping[str, str],
    segment_names: Mapping[str, str],
    archetype_names: Mapping[str, str],
    research_brief_id: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    response_ids: set[str] = set()
    roster = set(creative_names)
    for index, raw in enumerate(responses):
        response_id = raw.get("response_id")
        if not _non_empty_string(response_id):
            raise DashboardInputError(
                f"panelist-responses.jsonl line {index + 1} requires response_id"
            )
        if response_id in response_ids:
            raise DashboardInputError(f"duplicate response_id: {response_id}")
        response_ids.add(response_id)
        record_type = raw.get("record_type")
        if record_type not in STAGES:
            raise DashboardInputError(
                f"panelist-responses.jsonl line {index + 1} has unsupported record_type"
            )
        stage, stage_label = STAGES[record_type]
        assigned = raw.get("assigned_variation_ids")
        assigned = _require_sequence(
            assigned, f"panelist-responses.jsonl line {index + 1} assigned_variation_ids"
        )
        assigned_ids = [str(item) for item in assigned]
        unknown = sorted(set(assigned_ids) - roster)
        if unknown:
            raise DashboardInputError(
                "panelist-responses.jsonl references creative IDs outside the roster: "
                + ", ".join(unknown)
            )
        shown = raw.get("shown_order", assigned_ids)
        shown = _require_sequence(
            shown, f"panelist-responses.jsonl line {index + 1} shown_order"
        )
        shown_ids = [str(item) for item in shown]
        if set(shown_ids) != set(assigned_ids):
            raise DashboardInputError(
                f"panelist-responses.jsonl line {index + 1} shown_order must match assigned creatives"
            )

        segment_id = str(raw.get("segment_id", "not-recorded"))
        context_stratum_id = (
            str(raw.get("context_stratum_id"))
            if _non_empty_string(raw.get("context_stratum_id"))
            else None
        )
        archetype_id = str(raw.get("persona_archetype_id", "not-recorded"))
        replicate_id = str(raw.get("synthetic_replicate_id", response_id))
        profile_name = raw.get("synthetic_profile_name")
        if not _non_empty_string(profile_name):
            profile_name = f"Synthetic profile {replicate_id}"
        reactions: list[dict[str, Any]] = []
        for reaction in raw.get("per_creative_reactions", []):
            if not isinstance(reaction, Mapping):
                continue
            creative_id = str(reaction.get("variation_id", ""))
            reactions.append(
                {
                    "creative": _creative_ref(creative_id, creative_names),
                    "immediate_reaction": str(reaction.get("immediate_reaction", "")),
                    "noticed_first": str(
                        reaction.get("noticed_or_understood_first", "")
                    ),
                    "positive_signal": str(reaction.get("strongest_positive_signal", "")),
                    "negative_signal": str(reaction.get("strongest_negative_signal", "")),
                    "judgment_status": str(reaction.get("judgment_status", "not recorded")),
                }
            )
        choice_status = "not applicable"
        best_id = ""
        weakest_id = ""
        reason = ""
        if record_type == "screening_response":
            choice = raw.get("comparative_choice", {})
            if isinstance(choice, Mapping):
                choice_status = str(choice.get("status", "not recorded"))
                best_id = str(choice.get("best_variation_id", ""))
                weakest_id = str(choice.get("weakest_variation_id", ""))
                best_reason = str(choice.get("best_reason", "")).strip()
                weakest_reason = str(choice.get("weakest_reason", "")).strip()
                reason_parts = []
                if best_reason:
                    reason_parts.append(f"Why strongest: {best_reason}")
                if weakest_reason:
                    reason_parts.append(f"Why weakest: {weakest_reason}")
                reason = " ".join(reason_parts)
        elif record_type == "boundary_response":
            choice = raw.get("pairwise_choice", {})
            if isinstance(choice, Mapping):
                choice_status = str(choice.get("status", "not recorded"))
                best_id = str(choice.get("preferred_variation_id", ""))
                reason = str(choice.get("reason", ""))
        else:
            ranking = raw.get("final_preference_ranking", [])
            if isinstance(ranking, list) and ranking:
                choice_status = "final ranking"
                best_id = str(ranking[0])
                weakest_id = str(ranking[-1])
            reason = "No separate ranking rationale was collected; see each ad’s reaction and improvement note."

        unable = (
            "unable" in choice_status
            or any(item["judgment_status"] == "unable_to_judge" for item in reactions)
        )
        tie = choice_status in {"tie", "no_meaningful_difference"}
        finalist_reviews: list[dict[str, Any]] = []
        for review in raw.get("finalist_reviews", []):
            if not isinstance(review, Mapping):
                continue
            finalist_creative_id = str(review.get("variation_id", ""))
            finalist_reviews.append(
                {
                    "creative": _creative_ref(finalist_creative_id, creative_names),
                    "immediate_reaction": str(review.get("immediate_reaction", "")),
                    "rubric_scores": _clean_specialist_fields(
                        review.get("rubric_scores", {})
                    ),
                    "feedback": _clean_specialist_fields(review.get("feedback", [])),
                }
            )
        normalized.append(
            {
                "response_id": response_id,
                "stage": stage,
                "stage_label": stage_label,
                "synthetic_profile_id": replicate_id,
                "synthetic_profile_name": profile_name,
                "segment_id": segment_id,
                "segment_name": segment_names.get(segment_id, segment_id),
                "context_stratum_id": context_stratum_id,
                "archetype_id": archetype_id,
                "archetype_name": archetype_names.get(archetype_id, archetype_id),
                "research_brief_id": research_brief_id,
                "profile_snapshot": _clean_specialist_fields(raw.get("profile_snapshot", {})),
                "context_attribute_provenance": _clean_specialist_fields(
                    raw.get("context_attribute_provenance", [])
                ),
                "worker_context_isolation": str(
                    raw.get("worker_context_isolation", "not recorded")
                ),
                "assigned_creatives": [
                    _creative_ref(item, creative_names) for item in assigned_ids
                ],
                "shown_order": [_creative_ref(item, creative_names) for item in shown_ids],
                "reactions": reactions,
                "choice": {
                    "status": choice_status,
                    "best": _creative_ref(best_id, creative_names) if best_id else None,
                    "weakest": (
                        _creative_ref(weakest_id, creative_names) if weakest_id else None
                    ),
                    "reason": reason,
                },
                "finalist_reviews": finalist_reviews,
                "validation": _clean_specialist_fields(raw.get("validation", {})),
                "runtime_attempts": _clean_specialist_fields(
                    raw.get("runtime_attempts", [])
                ),
                "flags": {
                    "best": bool(best_id),
                    "weakest": bool(weakest_id),
                    "tie": tie,
                    "unable_to_judge": unable,
                },
            }
        )
    return normalized


def _screening_rows(
    screening: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
    creative_names: Mapping[str, str],
) -> list[dict[str, Any]]:
    utilities = screening.get("utilities") if isinstance(screening.get("utilities"), Mapping) else {}
    stability = (
        screening.get("top_k_inclusion_frequencies")
        if isinstance(screening.get("top_k_inclusion_frequencies"), Mapping)
        else {}
    )
    classifications = (
        screening.get("classifications")
        if isinstance(screening.get("classifications"), Mapping)
        else {}
    )
    diagnostics = (
        screening.get("model_diagnostics")
        if isinstance(screening.get("model_diagnostics"), Mapping)
        else {}
    )
    usable_counts = (
        diagnostics.get("usable_participations_per_creative")
        if isinstance(diagnostics.get("usable_participations_per_creative"), Mapping)
        else {}
    )
    screening_responses = [
        item for item in responses if item.get("record_type") == "screening_response"
    ]
    rows: list[dict[str, Any]] = []
    for creative_id in creative_names:
        exposure_count = 0
        usable_exposure_count = 0
        best_count = 0
        weakest_count = 0
        tie_count = 0
        unable_count = 0
        positions: Counter[int] = Counter()
        for raw in screening_responses:
            assigned = raw.get("assigned_variation_ids", [])
            if creative_id not in assigned:
                continue
            exposure_count += 1
            shown = raw.get("shown_order", [])
            if creative_id in shown:
                positions[shown.index(creative_id) + 1] += 1
            usable = raw.get("usable_maxdiff_block") is True
            if usable:
                usable_exposure_count += 1
            choice = raw.get("comparative_choice", {})
            if not isinstance(choice, Mapping):
                choice = {}
            status = choice.get("status")
            if usable and choice.get("best_variation_id") == creative_id:
                best_count += 1
            if usable and choice.get("weakest_variation_id") == creative_id:
                weakest_count += 1
            if status == "no_meaningful_difference":
                tie_count += 1
            reaction = next(
                (
                    item
                    for item in raw.get("per_creative_reactions", [])
                    if isinstance(item, Mapping)
                    and item.get("variation_id") == creative_id
                ),
                None,
            )
            if status == "unable_to_judge" or (
                isinstance(reaction, Mapping)
                and reaction.get("judgment_status") == "unable_to_judge"
            ):
                unable_count += 1

        rows.append(
            {
                **_creative_ref(creative_id, creative_names),
                "utility": utilities.get(creative_id),
                "stability": stability.get(creative_id),
                "classification": str(classifications.get(creative_id, "unresolved")),
                "usable_participations": usable_counts.get(creative_id),
                "exposure_count": exposure_count,
                "usable_exposure_count": usable_exposure_count,
                "best_count": best_count,
                "weakest_count": weakest_count,
                "tie_count": tie_count,
                "unable_count": unable_count,
                "position_counts": {str(key): value for key, value in sorted(positions.items())},
            }
        )
    ranked = screening.get("ranked_ids")
    if isinstance(ranked, list):
        order = {str(item): index for index, item in enumerate(ranked)}
        rows.sort(key=lambda item: order.get(item["variation_id"], len(order)))
    return rows


def _normalize_feedback(
    feedback: Mapping[str, Any],
    creative_names: Mapping[str, str],
    segment_names: Mapping[str, str],
) -> list[dict[str, Any]]:
    raw_themes = feedback.get("themes", [])
    raw_themes = _require_sequence(raw_themes, "feedback-synthesis.json themes")
    themes: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_themes):
        theme = _require_mapping(raw, f"feedback-synthesis.json themes[{index}]")
        creative_id = str(theme.get("creative_id", "not-recorded"))
        segment_id = str(theme.get("segment_id", "not-recorded"))
        exposed = theme.get("exposed_base", {})
        if not isinstance(exposed, Mapping):
            exposed = {}
        themes.append(
            {
                "stage": str(theme.get("stage", "not recorded")),
                "creative": _creative_ref(creative_id, creative_names),
                "segment_id": segment_id,
                "segment_name": segment_names.get(segment_id, segment_id),
                "lane": str(theme.get("lane", "theme")),
                "feedback_type": str(theme.get("feedback_type", "")),
                "evidence_scope": str(theme.get("evidence_scope", "")),
                "theme": str(theme.get("theme", "")),
                "why_it_matters": str(theme.get("why_it_matters", "")),
                "recommended_action": str(theme.get("recommended_action", "")),
                "source_type": str(theme.get("source_type", "model-generated synthesis")),
                "response_ids": [str(item) for item in theme.get("response_ids", [])],
                "exposed_base": {
                    "count": exposed.get("count"),
                    "label": str(exposed.get("label", "exposed base not recorded")),
                },
                "limitations": [str(item) for item in theme.get("limitations", [])],
            }
        )
    return themes


def _normalize_visual_evidence(
    run_dir: Path,
    saliency: Mapping[str, Any] | None,
    finalists: Mapping[str, Any],
    creative_names: Mapping[str, str],
    imagery_expected: bool,
    media_representations: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if not imagery_expected:
        if saliency is not None:
            raise DashboardInputError(
                "copy_only studies must not include saliency-index.json"
            )
        return None, "No imagery was tested."
    if saliency is None:
        raise DashboardInputError(
            "image-capable study requires saliency-index.json attention evidence"
        )
    if saliency.get("status") != "available":
        raise DashboardInputError(
            "image-capable study requires available saliency evidence"
        )
    provider = _require_non_empty_string(
        saliency.get("provider"), "saliency-index.json provider"
    )
    method = _require_non_empty_string(
        saliency.get("method"), "saliency-index.json method"
    )
    decision = finalists.get("roster_decision")
    if not isinstance(decision, Mapping):
        raise DashboardInputError(
            "attention heatmap requires roster approval before reveal"
        )
    if decision.get("status") not in APPROVED_ROSTER_STATES:
        raise DashboardInputError(
            "attention heatmap requires roster approval before reveal"
        )
    approved_at = _require_aware_datetime(
        decision.get("approved_at"),
        "finalist-results.json roster_decision.approved_at",
    )
    revealed_at = _require_aware_datetime(
        saliency.get("revealed_at"), "saliency-index.json revealed_at"
    )
    if approved_at >= revealed_at:
        raise DashboardInputError(
            "attention heatmap requires evidence that roster approval happened before reveal"
        )
    raw_entries = _require_sequence(saliency.get("entries"), "saliency-index.json entries")
    if not raw_entries:
        raise DashboardInputError(
            "image-capable study requires at least one saliency evidence entry"
        )
    entries: list[dict[str, Any]] = []
    seen_representation_ids: set[str] = set()
    for index, raw in enumerate(raw_entries):
        entry = _require_mapping(raw, f"saliency-index.json entries[{index}]")
        creative_id = _require_non_empty_string(
            entry.get("variation_id"),
            f"saliency-index.json entries[{index}].variation_id",
        )
        if creative_id not in creative_names:
            raise DashboardInputError(
                f"saliency-index.json entries[{index}] references a creative outside the roster"
            )
        representation_id = _require_non_empty_string(
            entry.get("representation_id"),
            f"saliency-index.json entries[{index}].representation_id",
        )
        if representation_id in seen_representation_ids:
            raise DashboardInputError(
                "saliency-index.json contains duplicate entry for media representation "
                f"{representation_id}"
            )
        seen_representation_ids.add(representation_id)
        representation = media_representations.get(representation_id)
        if representation is None:
            raise DashboardInputError(
                f"saliency-index.json representation_id {representation_id} is outside "
                "the tested media representations"
            )
        if representation.get("variation_id") != creative_id:
            raise DashboardInputError(
                f"saliency-index.json representation_id {representation_id} does not "
                "belong to variation_id {creative_id}"
            )
        declared_hash = _require_non_empty_string(
            entry.get("content_hash"),
            f"saliency-index.json entries[{index}].content_hash",
        )
        if declared_hash != representation.get("content_hash"):
            raise DashboardInputError(
                f"saliency-index.json entry {representation_id} content_hash does not "
                "match the tested media representation"
            )
        original = _embed_file(
            run_dir,
            entry.get("original_path"),
            f"saliency entry {creative_id} original",
        )
        overlay = _embed_file(
            run_dir,
            entry.get("overlay_path"),
            f"saliency entry {creative_id} overlay",
        )
        _require_renderable_image(
            original,
            f"saliency entry {creative_id} original",
        )
        _require_renderable_image(
            overlay,
            f"saliency entry {creative_id} overlay",
        )
        if original["data_url"] is None or overlay["data_url"] is None:
            raise DashboardInputError(
                f"attention heatmap media for {creative_id} must be small enough to embed"
            )
        if original["content_hash"] != declared_hash:
            raise DashboardInputError(
                f"saliency-index.json entry {representation_id} original content_hash "
                "does not match the tested media representation"
            )
        declared_overlay_hash = _require_non_empty_string(
            entry.get("overlay_content_hash"),
            f"saliency-index.json entries[{index}].overlay_content_hash",
        )
        if overlay["content_hash"] != declared_overlay_hash:
            raise DashboardInputError(
                f"saliency-index.json entry {representation_id} overlay_content_hash "
                "does not match the actual overlay file"
            )
        entry_provider = _require_non_empty_string(
            entry.get("provider"),
            f"saliency-index.json entries[{index}].provider",
        )
        if entry_provider != provider:
            raise DashboardInputError(
                f"saliency-index.json entries[{index}].provider must match the index provider"
            )
        predeclared_target = _require_non_empty_string(
            entry.get("predeclared_target"),
            f"saliency-index.json entries[{index}].predeclared_target",
        )
        if not _non_empty_string(entry.get("target_declared_at")):
            raise DashboardInputError(
                f"saliency-index.json entries[{index}].target_declared_at must be recorded "
                "before saliency-index.json revealed_at"
            )
        target_declared_at = _require_aware_datetime(
            entry.get("target_declared_at"),
            f"saliency-index.json entries[{index}].target_declared_at",
        )
        if target_declared_at >= revealed_at:
            raise DashboardInputError(
                f"saliency-index.json entries[{index}].target_declared_at must be before "
                "saliency-index.json revealed_at"
            )
        categorical_alignment = _require_non_empty_string(
            entry.get("categorical_alignment"),
            f"saliency-index.json entries[{index}].categorical_alignment",
        )
        if categorical_alignment not in {
            "aligned",
            "partially_aligned",
            "misaligned",
            "unclear",
        }:
            raise DashboardInputError(
                f"saliency-index.json entries[{index}].categorical_alignment is unsupported"
            )
        limitations_raw = _require_sequence(
            entry.get("limitations"),
            f"saliency-index.json entries[{index}].limitations",
        )
        limitations = [
            _require_non_empty_string(
                item, f"saliency-index.json entries[{index}].limitations[{item_index}]"
            )
            for item_index, item in enumerate(limitations_raw)
        ]
        if not limitations:
            raise DashboardInputError(
                f"saliency-index.json entries[{index}].limitations must not be empty"
            )
        entries.append(
            {
                **_creative_ref(creative_id, creative_names),
                "representation_id": representation_id,
                "content_hash": declared_hash,
                "overlay_content_hash": declared_overlay_hash,
                "original_data_url": original["data_url"],
                "original_mime_type": original["mime_type"],
                "overlay_data_url": overlay["data_url"],
                "overlay_mime_type": overlay["mime_type"],
                "predeclared_target": predeclared_target,
                "target_declared_at": target_declared_at.isoformat(),
                "categorical_alignment": categorical_alignment,
                "provider": entry_provider,
                "limitations": limitations,
            }
        )
    missing_ids = sorted(set(media_representations) - seen_representation_ids)
    extra_ids = sorted(seen_representation_ids - set(media_representations))
    if missing_ids:
        raise DashboardInputError(
            "saliency-index.json is missing attention heatmap entries for tested media "
            "representations: " + ", ".join(missing_ids)
        )
    if extra_ids:
        raise DashboardInputError(
            "saliency-index.json references unknown tested media representations: "
            + ", ".join(extra_ids)
        )
    changed = decision.get("changed_after_saliency_reveal") is True
    return (
        {
            "provider": provider,
            "method": method,
            "roster_approved_before_reveal": True,
            "approval_status": str(decision.get("status")),
            "approved_at": approved_at.isoformat(),
            "revealed_at": revealed_at.isoformat(),
            "override_status": (
                "saliency-informed human override" if changed else "no post-reveal roster change"
            ),
            "entries": entries,
        },
        (
            "The attention heatmap was reviewed after the finalist decision and stays "
            "separate from audience-response and campaign-performance evidence."
        ),
    )


def _imagery_expected(
    manifest: Mapping[str, Any], creatives: Sequence[Mapping[str, Any]]
) -> bool:
    creative_format = str(manifest.get("creative_format", "")).strip().lower()
    has_embedded_media = any(bool(creative.get("media")) for creative in creatives)
    if creative_format == "copy_only":
        if has_embedded_media:
            raise DashboardInputError(
                "copy_only studies cannot contain inspectable creative media"
            )
        return False
    if creative_format in IMAGE_CAPABLE_FORMATS:
        return True
    raise DashboardInputError(
        "study-manifest.json must use the canonical creative_format contract: "
        "copy_only, static_image, carousel, or video_representation"
    )


def _data_url(path: Path) -> str:
    mime_type = _mime_type(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _csv_safe(value: Any) -> str:
    """Return a spreadsheet-safe, deterministic text cell."""
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _responses_csv_data_url(responses: Sequence[Mapping[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "test_round",
            "profile",
            "audience",
            "situation",
            "mindset",
            "ad",
            "immediate_reaction",
            "noticed_first",
            "positive_signal",
            "negative_signal",
            "overall_strongest_ad",
            "overall_weakest_ad",
            "overall_reason",
            "finalist_feedback",
            "rubric_scores",
            "response_id",
        )
    )
    for item in responses:
        assigned = item.get("assigned_creatives", [])
        if not isinstance(assigned, list):
            assigned = []
        choice = item.get("choice", {})
        if not isinstance(choice, Mapping):
            choice = {}
        best = choice.get("best")
        weakest = choice.get("weakest")
        reactions = {
            str(reaction.get("creative", {}).get("variation_id")): reaction
            for reaction in item.get("reactions", [])
            if isinstance(reaction, Mapping)
            and isinstance(reaction.get("creative"), Mapping)
        }
        finalist_reviews = {
            str(review.get("creative", {}).get("variation_id")): review
            for review in item.get("finalist_reviews", [])
            if isinstance(review, Mapping)
            and isinstance(review.get("creative"), Mapping)
        }
        for creative in assigned:
            if not isinstance(creative, Mapping):
                continue
            creative_id = str(creative.get("variation_id", ""))
            reaction = reactions.get(creative_id, {})
            finalist_review = finalist_reviews.get(creative_id, {})
            feedback = finalist_review.get("feedback", [])
            if not isinstance(feedback, list):
                feedback = []
            rubric = finalist_review.get("rubric_scores", {})
            if not isinstance(rubric, Mapping):
                rubric = {}
            writer.writerow(
                tuple(
                    _csv_safe(value)
                    for value in (
                        item.get("stage_label"),
                        item.get("synthetic_profile_name"),
                        item.get("segment_name"),
                        item.get("context_stratum_id") or "Not recorded",
                        item.get("archetype_name"),
                        creative.get("display_name", creative_id),
                        reaction.get("immediate_reaction", ""),
                        reaction.get("noticed_first", ""),
                        reaction.get("positive_signal", ""),
                        reaction.get("negative_signal", ""),
                        best.get("display_name", "") if isinstance(best, Mapping) else "",
                        weakest.get("display_name", "") if isinstance(weakest, Mapping) else "",
                        choice.get("reason", ""),
                        " | ".join(str(value) for value in feedback),
                        " | ".join(
                            f"{key}: {value}"
                            for key, value in sorted(rubric.items())
                        ),
                        item.get("response_id"),
                    )
                )
            )
    encoded = base64.b64encode(output.getvalue().encode("utf-8")).decode("ascii")
    return f"data:text/csv;charset=utf-8;base64,{encoded}"


def _build_exports(
    run_dir: Path,
    include_saliency_export: bool,
    responses: Sequence[Mapping[str, Any]],
    audience_files: Mapping[str, bytes] | None = None,
    audience_state: str = "legacy",
    allocation_job_files: Mapping[str, bytes] | None = None,
) -> list[dict[str, str]]:
    filenames = list(REQUIRED_INPUTS)
    boundary_path = run_dir / "boundary-results.json"
    if boundary_path.is_file():
        filenames.insert(4, "boundary-results.json")
    if include_saliency_export and (run_dir / "saliency-index.json").is_file():
        filenames.append("saliency-index.json")
    for filename in (
        "raw-provider-returns.jsonl",
        "rejected-attempts.jsonl",
        "dispatch-audit.jsonl",
    ):
        if (run_dir / filename).is_file():
            filenames.append(filename)
    technical = [
        {
            "filename": filename,
            "label": filename.replace("-", " ").replace(".jsonl", "").replace(".json", "").title(),
            "data_url": _data_url(run_dir / filename),
            "audience": "technical",
        }
        for filename in filenames
    ]
    primary = [
        {
            "filename": "ai-audience-responses.csv",
            "label": "AI audience responses for Excel",
            "data_url": _responses_csv_data_url(responses),
            "audience": "marketer",
        }
    ]
    if audience_files is not None:
        is_v3 = "audience-resolution.json" in audience_files
        audience_primary = [
            ("audience-research-report.html", "Audience research report"),
            (
                "saved-audience-panel.json",
                "Reusable AI audience panel"
                if audience_state == "research_backed"
                else "Provisional AI audience panel for this run",
            ),
        ]
        if not is_v3:
            audience_primary.append(
                ("research-sources.csv", "Research sources for Excel")
            )
        primary = [
            {
                "filename": filename,
                "label": label,
                "data_url": _bytes_data_url(filename, audience_files[filename]),
                "audience": "marketer",
            }
            for filename, label in audience_primary
        ] + primary
        if is_v3:
            primary_names = {filename for filename, _label in audience_primary}
            technical.extend(
                {
                    "filename": filename,
                    "label": filename.replace("-", " ").replace(".json", "").title(),
                    "data_url": _bytes_data_url(filename, audience_files[filename]),
                    "audience": "technical",
                }
                for filename in audience_files
                if filename not in primary_names
            )
        else:
            technical.extend(
                {
                    "filename": filename,
                    "label": label,
                    "data_url": _bytes_data_url(filename, audience_files[filename]),
                    "audience": "technical",
                }
                for filename, label in (
                    ("persona-research-brief.json", "Audience research brief JSON"),
                    ("audience-panel-package.zip", "Full audience package"),
                    ("package-manifest.json", "Audience package manifest JSON"),
                    ("README.txt", "Audience package README"),
                )
            )
    if allocation_job_files:
        technical.extend(
            {
                "filename": filename,
                "label": filename.replace("-", " ").replace(".json", "").title(),
                "data_url": _bytes_data_url(filename, raw),
                "audience": "technical",
            }
            for filename, raw in allocation_job_files.items()
        )
    return [*primary, *technical]


def _bytes_data_url(filename: str, value: bytes) -> str:
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    encoded = base64.b64encode(value).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _humanize(value: Any) -> str:
    return str(value).replace("_", " ").strip()


def _plain_method_label(value: Any) -> str:
    return {
        "partial_exposure_maxdiff": "Compare all ads, then review the top ads more closely",
        "complete_exposure": "Every profile reviewed every ad",
    }.get(str(value), _humanize(value))


def _plain_validity_label(value: Any) -> str:
    return {
        "valid": "Usable for choosing top ads",
        "exploratory": "Directional only",
        "invalid": "Do not use",
        "incomplete": "More AI reviews needed",
    }.get(str(value), "Review needed")


def _build_run_integrity(
    manifest: Mapping[str, Any],
    creatives: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
    screening: Mapping[str, Any],
) -> list[dict[str, Any]]:
    audience = manifest.get("audience_lock")
    if not isinstance(audience, Mapping):
        audience = {}
    diagnostics = screening.get("model_diagnostics")
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    gates = diagnostics.get("gates")
    if not isinstance(gates, Mapping):
        gates = {}
    unique_archetypes = audience.get("unique_archetypes", 0)
    grounded_profiles = audience.get("unique_grounded_context_profiles", 0)
    brief_id = str(audience.get("persona_research_brief_id", "")).strip()
    research_status = "documented" if brief_id and unique_archetypes and grounded_profiles else "incomplete"

    media_count = sum(len(creative.get("media", [])) for creative in creatives)
    supplied_assets = sum(
        1
        for creative in creatives
        if "supplied" in str(creative.get("input_fidelity", "")).lower()
    )
    input_status = "exact media locked" if media_count else "copy-only inputs"
    if media_count and supplied_assets < len(creatives):
        input_status = "mixed input fidelity"

    isolated = sum(
        1 for response in responses if response.get("worker_context_isolation") == "isolated"
    )
    progressive = sum(
        1 for response in responses if response.get("reaction_protocol") == "progressive_reveal"
    )
    validations_passed = sum(
        1
        for response in responses
        if isinstance(response.get("validation"), Mapping)
        and all(
            response["validation"].get(key) is True
            for key in ("schema_valid", "assignment_valid", "reaction_order_valid")
        )
    )
    review_status = (
        "accepted records passed"
        if isolated == len(responses)
        and progressive == len(responses)
        and validations_passed == len(responses)
        else "limitations recorded"
    )

    connected = diagnostics.get("connected") is True
    converged = gates.get("converged") is True if gates else connected
    resilient = diagnostics.get("overall_one_block_deletion_resilient") is True
    design_status = "passed planned gates" if connected and converged and resilient else "directional only"
    stability_status = "passed" if gates.get("stability") is True else "limited"
    archetype = screening.get("archetype_sensitivity")
    if not isinstance(archetype, Mapping):
        archetype = {}

    return [
        {
            "dimension": "Research basis",
            "overview_label": "Audience grounding",
            "status": research_status,
            "overview": (
                f"{grounded_profiles} buyer profiles represented "
                f"{unique_archetypes} researched buyer mindsets."
            ),
            "details": [
                f"Approved audience research brief: {brief_id or 'not recorded'}.",
                "Synthetic panelists are instantiated from these profiles; they are not human respondents.",
            ],
        },
        {
            "dimension": "Input fidelity",
            "overview_label": "Creative fidelity",
            "status": input_status,
            "overview": (
                f"{media_count} exact media "
                f"{'representation was' if media_count == 1 else 'representations were'} "
                "content-hash locked for inspection."
            ),
            "details": [
                f"{supplied_assets} of {len(creatives)} creatives were marked as supplied assets.",
                "Copy, input-fidelity labels, and available visual representations are preserved in the source exports.",
            ],
        },
        {
            "dimension": "Review integrity",
            "overview_label": "Response collection",
            "status": review_status,
            "overview": f"{validations_passed} of {len(responses)} accepted response records passed recorded validation checks.",
            "details": [
                f"Context isolation recorded for {isolated} of {len(responses)} accepted responses.",
                f"Progressive reveal recorded for {progressive} of {len(responses)} accepted responses.",
                "Creative order, assignment, schema, and response provenance remain inspectable per response.",
            ],
        },
        {
            "dimension": "Design adequacy",
            "overview_label": "Test design",
            "status": design_status,
            "overview": (
                f"The comparison graph was {'connected' if connected else 'disconnected'}; "
                "resilience and calibrated coverage determine whether the read is "
                "directional or decision-ready."
            ),
            "details": [
                f"Comparison graph connected: {'yes' if connected else 'no'}.",
                f"Model convergence recorded: {'yes' if converged else 'no'}.",
                f"One-block deletion resilience: {'passed' if resilient else 'not passed'}.",
                f"Recovery calibration: {diagnostics.get('recovery_calibration_status', 'not recorded')}.",
            ],
        },
        {
            "dimension": "Result stability",
            "overview_label": "Result consistency",
            "status": stability_status,
            "overview": "Shortlist stability and audience-mindset sensitivity apply only inside this synthetic run.",
            "details": [
                f"Conditional stability gate: {stability_status}.",
                f"Leave-one-archetype-out top-K consistency: {archetype.get('top_k_consistent', 'not recorded')}.",
                "Ties, boundary width, and model-conditional disagreement do not establish human or campaign validity.",
            ],
        },
    ]


def _methodology_payload(
    manifest: Mapping[str, Any],
    screening: Mapping[str, Any],
    visual_method_note: str,
    run_integrity: list[dict[str, Any]],
) -> dict[str, Any]:
    method = str(manifest.get("method"))
    assignment = (
        manifest.get("assignment")
        if isinstance(manifest.get("assignment"), Mapping)
        else {}
    )
    model = manifest.get("model") if isinstance(manifest.get("model"), Mapping) else {}
    diagnostics = (
        screening.get("model_diagnostics")
        if isinstance(screening.get("model_diagnostics"), Mapping)
        else {}
    )
    bootstrap = (
        diagnostics.get("bootstrap")
        if isinstance(diagnostics.get("bootstrap"), Mapping)
        else {}
    )
    weighting = (
        diagnostics.get("analysis_weighting")
        if isinstance(diagnostics.get("analysis_weighting"), Mapping)
        else {}
    )
    gates = (
        diagnostics.get("gates")
        if isinstance(diagnostics.get("gates"), Mapping)
        else {}
    )
    interpretation_limits = _require_sequence(
        screening.get("interpretation_limits"),
        "screening-model-results.json interpretation_limits",
    )
    limits = [
        _require_non_empty_string(
            item, f"screening-model-results.json interpretation_limits[{index}]"
        )
        for index, item in enumerate(interpretation_limits)
    ]
    if not limits:
        raise DashboardInputError(
            "screening-model-results.json interpretation_limits must not be empty"
        )

    shared_definitions = [
        {
            "term": "First-choice share in the finalist round",
            "definition": "The technical field is Conditional First-Choice Share. Every accepted finalist-round response saw every finalist in the recorded set. The share applies only to that set and is not survey incidence.",
        },
        {
            "term": "Checked against people",
            "definition": "The audit field is human_alignment_validation. It records whether these synthetic results were compared with an appropriate human benchmark. Not evaluated does not mean aligned.",
        },
        {
            "term": "Checked against campaign results",
            "definition": "The audit field is field_performance_calibration. It records whether the study was compared with live delivery or outcome data. None means no performance claim is supported.",
        },
        {
            "term": "Attention heatmap",
            "definition": "For inspectable imagery, the workflow generates or imports a post-approval saliency view for each exact media representation. This is not eye-tracking, preference, click-through, or conversion data.",
        },
    ]
    shared_controls = [
        "The approved audience, segment mix, and buyer mindsets stayed fixed across test stages.",
        "Synthetic responses, automatic attention heatmaps, human checks, and live campaign results remain separate sources of evidence.",
        "Every accepted response retains its assigned creatives, shown order, validation state, and stable response ID.",
    ]
    if method == "partial_exposure_maxdiff":
        definitions = [
            {
                "term": "First-round signal",
                "definition": "The technical field is centered protocol-relative utility from the MaxDiff model. It compares ads only within the assigned subsets shown in this run; it is not a like rate or market estimate.",
            },
            {
                "term": "Stayed in the cut",
                "definition": "The technical field is Conditional Within-Run Stability. It shows how often an ad returned to this run's shortlist when complete response records were resampled. It applies only to this test setup.",
            },
            *shared_definitions,
        ]
        controls = [
            *shared_controls,
            "First-round profiles saw balanced partial subsets in randomized order and reacted to each ad before making a best-and-weakest comparison.",
            "When the cutoff required a tie-break, a separate Davidson pairwise model handled only boundary ads; its scale was not pooled with MaxDiff utility.",
            "Finalist-round profiles used fresh AI contexts and saw every approved finalist before making a first choice.",
        ]
        audit_details = [
            {"label": "Study method ID", "value": method},
            {
                "label": "First-round measure",
                "value": str(screening.get("estimand", "not recorded")),
            },
            {
                "label": "Shortlist stability field",
                "value": str(screening.get("stability_diagnostic", "not recorded")),
            },
            {
                "label": "Tie-break model",
                "value": "connected Davidson pairwise model; separate scale",
            },
            {
                "label": "Recovery configuration",
                "value": str(screening.get("recovery_config_version", "not recorded")),
            },
        ]
    elif method == "complete_exposure":
        definitions = [
            {
                "term": "Complete-set comparison signal",
                "definition": "Every first-round synthetic profile reviewed every ad in the study. The recorded comparison signal is conditional on that complete set and is not a customer preference or market estimate.",
            },
            {
                "term": "Stayed in the cut",
                "definition": "Conditional Within-Run Stability describes how consistently an ad returned to the shortlist when this run's complete-set response records were resampled.",
            },
            *shared_definitions,
        ]
        controls = [
            *shared_controls,
            "Every first-round profile saw every ad in randomized or counterbalanced order and reacted before comparing the complete set.",
            "No partial-exposure boundary model or pairwise tie-break was used for this study method.",
            "Finalist metrics, when approved, remain conditional on the recorded finalist set.",
        ]
        audit_details = [
            {"label": "Study method ID", "value": method},
            {"label": "Exposure design", "value": "complete exposure; every ad shown"},
            {
                "label": "First-round measure",
                "value": str(screening.get("estimand", "not recorded")),
            },
            {
                "label": "Shortlist stability field",
                "value": str(screening.get("stability_diagnostic", "not recorded")),
            },
            {"label": "Boundary model", "value": "not used"},
        ]
    else:  # guarded before payload compilation
        raise DashboardInputError(f"unsupported study method: {method}")
    audit_details.extend(
        [
            {
                "label": "Recorded validity state",
                "value": str(screening.get("validity_status", "not recorded")),
            },
            {
                "label": "Assignment version",
                "value": str(assignment.get("assignment_version", "not recorded")),
            },
            {
                "label": "Ads per main-test assignment",
                "value": str(assignment.get("block_size", "not recorded")),
            },
            {
                "label": "Assignment randomization seed",
                "value": str(assignment.get("randomization_seed", "not recorded")),
            },
            {
                "label": "Planned panelist participations per ad and segment",
                "value": str(
                    assignment.get("planned_participations_per_creative", "not recorded")
                ),
            },
            {
                "label": "Usable participation floor per ad and segment",
                "value": str(
                    diagnostics.get("usable_participation_floor", "not recorded")
                ),
            },
            {
                "label": "Segment weighting",
                "value": str(weighting.get("method", "not recorded")),
            },
            {
                "label": "Comparison graph connected",
                "value": "yes" if diagnostics.get("connected") is True else "no",
            },
            {
                "label": "Model converged and identified",
                "value": (
                    "yes"
                    if diagnostics.get("converged") is True
                    and diagnostics.get("identified") is True
                    else "no"
                ),
            },
            {
                "label": "One-panelist deletion resilience",
                "value": (
                    "passed"
                    if diagnostics.get("overall_one_block_deletion_resilient") is True
                    else "not passed"
                ),
            },
            {
                "label": "Bootstrap resampling",
                "value": (
                    f"{bootstrap.get('successful_fits', 'not recorded')} successful of "
                    f"{bootstrap.get('requested_fits', 'not recorded')} requested · "
                    f"seed {bootstrap.get('seed', 'not recorded')} · "
                    f"{bootstrap.get('resample_unit', 'not recorded')} · "
                    f"{bootstrap.get('stratification', 'not recorded')}"
                ),
            },
            {
                "label": "Bootstrap success floor",
                "value": str(bootstrap.get("successful_fit_floor", "not recorded")),
            },
            {
                "label": "Shortlist thresholds",
                "value": (
                    f"top performer ≥ {model.get('clear_finalist_threshold', 'not recorded')} · "
                    f"lower performer ≤ {model.get('clear_non_finalist_threshold', 'not recorded')}"
                ),
            },
            {
                "label": "Recorded gate results",
                "value": " · ".join(
                    f"{_humanize(key)}: {'passed' if value is True else 'not passed'}"
                    for key, value in sorted(gates.items())
                )
                or "not recorded",
            },
        ]
    )
    return {
        "method_id": method,
        "method_label": _plain_method_label(method),
        "visual_attention_status": visual_method_note,
        "definitions": definitions,
        "controls": controls,
        "run_integrity": run_integrity,
        "interpretation_limits": limits,
        "audit_details": audit_details,
    }


def _roster_status(finalists: Mapping[str, Any]) -> dict[str, Any]:
    decision = finalists.get("roster_decision")
    if not isinstance(decision, Mapping):
        decision = {}
    status = str(decision.get("status", "not recorded"))
    is_approved = status in APPROVED_ROSTER_STATES
    override = is_approved and decision.get("override") is True
    changed = is_approved and decision.get("changed_after_saliency_reveal") is True
    status_label = {
        "approved": "Top ads approved",
        "approved_with_override": "Top ads selected with a human override",
        "awaiting_approval": "Waiting for approval",
    }.get(status, _humanize(status))
    return {
        "status": status,
        "status_label": status_label,
        "is_approved": is_approved,
        "override": override,
        "override_label": (
            "Finalist decision pending; no final shortlist decision has been recorded"
            if not is_approved
            else (
                "saliency-informed human override"
                if changed
                else (
                    "A person made the final shortlist decision"
                    if override
                    else "No manual shortlist change was recorded"
                )
            )
        ),
        "override_reason": str(decision.get("override_reason", "")) if is_approved else "",
        "approved_at": decision.get("approved_at") if is_approved else None,
        "approved_by": str(decision.get("approved_by", "not recorded")) if is_approved else "",
    }


def _is_deterministic_conformance_fixture(run_dir: Path) -> bool:
    raw_returns_path = run_dir / "raw-provider-returns.jsonl"
    if not raw_returns_path.is_file():
        return False
    for raw_line in raw_returns_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            raw_record = json.loads(raw_line)
        except json.JSONDecodeError:
            return False
        raw_return = raw_record.get("raw_return")
        return (
            isinstance(raw_return, Mapping)
            and raw_return.get("fixture") == "deterministic conformance provider return"
        )
    return False


def _payload(
    run_dir: Path,
    manifest: Mapping[str, Any],
    creatives: list[dict[str, Any]],
    creative_names: Mapping[str, str],
    responses_raw: Sequence[Mapping[str, Any]],
    responses: list[dict[str, Any]],
    screening: Mapping[str, Any],
    boundary: Mapping[str, Any] | None,
    finalists: Mapping[str, Any],
    feedback: Mapping[str, Any],
    imagery_expected: bool,
    visual_evidence: dict[str, Any] | None,
    visual_method_note: str,
    audience_payload: Mapping[str, Any],
    audience_files: Mapping[str, bytes] | None,
    allocation_job_files: Mapping[str, bytes] | None,
) -> dict[str, Any]:
    deterministic_fixture = _is_deterministic_conformance_fixture(run_dir)
    audience = manifest.get("audience_lock")
    if not isinstance(audience, Mapping):
        audience = {}
    external = manifest.get("external_validity")
    if not isinstance(external, Mapping):
        external = {}
    segment_names = _name_map(audience.get("segment_names"))
    stage_counts = Counter(item["stage"] for item in responses)
    stage_replicates: dict[str, set[str]] = {}
    for item in responses:
        stage_replicates.setdefault(item["stage"], set()).add(
            item["synthetic_profile_id"]
        )
    unique_profiles = {item["synthetic_profile_id"] for item in responses}
    observed_archetypes = {item["archetype_id"] for item in responses}
    observed_context_strata = {
        item["context_stratum_id"]
        for item in responses
        if item.get("context_stratum_id") is not None
    }
    screening_usable_field = (
        "usable_complete_exposure_observation"
        if manifest.get("method") == "complete_exposure"
        else "usable_maxdiff_block"
    )
    usable_screening = sum(
        1
        for item in responses_raw
        if item.get("record_type") == "screening_response"
        and item.get(screening_usable_field) is True
    )
    usable_boundary = sum(
        1
        for item in responses_raw
        if item.get("record_type") == "boundary_response"
        and item.get("usable_pairwise_observation") is True
    )
    complete_finalist = sum(
        1 for item in responses_raw if item.get("record_type") == "finalist_response"
    )
    total_model_calls = (
        manifest.get("usage", {}).get("total_model_calls")
        if isinstance(manifest.get("usage"), Mapping)
        else None
    )
    if isinstance(total_model_calls, bool) or not isinstance(total_model_calls, int):
        total_model_calls = None
    unique_archetypes = audience.get("unique_archetypes")
    if not isinstance(unique_archetypes, int):
        unique_archetypes = len(observed_archetypes)
    grounded_profiles = audience.get("unique_grounded_context_profiles")
    if not isinstance(grounded_profiles, int):
        grounded_profiles = len(unique_profiles)
    approval = _roster_status(finalists)
    validity = str(screening.get("validity_status", manifest.get("validity_status", "not recorded")))
    human_alignment = str(external.get("human_alignment_validation", "not_evaluated"))
    field_calibration = str(external.get("field_performance_calibration", "none"))
    finalist_ids = finalists.get("approved_finalist_ids", [])
    if not isinstance(finalist_ids, list):
        finalist_ids = []
    finalist_refs = [_creative_ref(item, creative_names) for item in finalist_ids]
    approved_finalists = finalist_refs if approval["is_approved"] else []
    pending_finalists = [] if approval["is_approved"] else finalist_refs
    if not approval["is_approved"]:
        decision_basis = (
            "These ads are proposed for closer review and are waiting for approval."
        )
    elif approval["override"]:
        decision_basis = approval["override_reason"] or (
            "A person selected these top ads because the initial comparison did not produce "
            "a clear enough cutoff."
        )
    elif boundary and boundary.get("status") == "resolved":
        decision_basis = (
            "The first round identified the leaders, and a focused tie-break settled the "
            "last place among the top ads."
        )
    else:
        decision_basis = "These ads ranked highest in the test and received a closer review."
    method = str(manifest.get("method"))
    boundary_payload = (
        _clean_specialist_fields(boundary)
        if boundary and method == "partial_exposure_maxdiff"
        else None
    )
    metrics_available = approval["is_approved"]
    finalist_payload = {
        "approved_finalists": approved_finalists,
        "pending_finalists": pending_finalists,
        "roster_decision": approval,
        "metrics_available": metrics_available,
        "accepted_response_records": (
            finalists.get("accepted_response_records") if metrics_available else None
        ),
        "accepted_unique_replicates": (
            finalists.get("accepted_unique_replicates") if metrics_available else None
        ),
        "unique_job_slots_consumed": (
            finalists.get("unique_job_slots_consumed") if metrics_available else None
        ),
        "total_model_calls": (
            finalists.get("total_model_calls") if metrics_available else None
        ),
        "conditional_first_choice_share": (
            _clean_specialist_fields(finalists.get("conditional_first_choice_share", {}))
            if metrics_available
            else {}
        ),
        "first_choice_counts": (
            _clean_specialist_fields(finalists.get("first_choice_counts", {}))
            if metrics_available
            else {}
        ),
        "rubric_summary": (
            _clean_specialist_fields(finalists.get("rubric_summary", {}))
            if metrics_available
            else {}
        ),
        "model_conditional_agreement": (
            _clean_specialist_fields(finalists.get("model_conditional_agreement", {}))
            if metrics_available
            else {}
        ),
        "segment_contrasts": (
            _clean_specialist_fields(finalists.get("segment_contrasts", []))
            if metrics_available
            else []
        ),
        "testing_map": (
            _clean_specialist_fields(finalists.get("testing_map", []))
            if metrics_available
            else []
        ),
    }
    run_integrity = _build_run_integrity(manifest, creatives, responses_raw, screening)
    methodology = _methodology_payload(
        manifest, screening, visual_method_note, run_integrity
    )
    if method == "complete_exposure":
        estimand_technical_label = "Complete-exposure comparison utility"
    else:
        estimand_technical_label = "Centered protocol-relative utility"
    estimand_primary_label = "Overall result"
    return {
        "schema_version": "audience-lab-dashboard-v1",
        "study": {
            "study_id": str(manifest.get("study_id")),
            "study_version": str(manifest.get("study_version", "not recorded")),
            "objective": str(manifest.get("study_objective", "Study objective not recorded.")),
            "creative_format": _humanize(manifest.get("creative_format", "not recorded")),
            "creative_format_id": str(manifest.get("creative_format", "not recorded")),
            "imagery_expected": imagery_expected,
            "method": str(manifest.get("method", "not recorded")),
            "method_label": _plain_method_label(
                manifest.get("method", "not recorded")
            ),
            "is_deterministic_fixture": deterministic_fixture,
        },
        "audience": _clean_specialist_fields(audience_payload),
        "summary": {
            "validity_status": validity,
            "validity_label": _plain_validity_label(validity),
            "validity_reasons": [str(item) for item in screening.get("validity_reasons", [])],
            "human_alignment_validation": human_alignment,
            "field_performance_calibration": field_calibration,
            "roster_decision": approval,
            "creative_count": len(creatives),
            "ads_moving_forward": approved_finalists,
            "ads_pending_approval": pending_finalists,
            "overview_intro": (
                "Review the proposed top ads, the evidence behind them, and the limits "
                "while it is pending approval."
                if not approval["is_approved"]
                else "See which ads performed best, why they stood out, and how the "
                "test reached that result."
            ),
            "decision_basis": decision_basis,
            "attention_heatmap_available": visual_evidence is not None,
            "denominators": {
                "total_model_calls": total_model_calls,
                "accepted_response_records": len(responses),
                "accepted_unique_replicates": len(unique_profiles),
                "unique_archetypes": unique_archetypes,
                "grounded_context_profiles": grounded_profiles,
                "accepted_context_strata": len(observed_context_strata),
            },
            "usable_blocks": {
                "screening": usable_screening,
                "boundary": usable_boundary,
                "finalist": complete_finalist,
            },
            "accepted_response_records_by_stage": dict(sorted(stage_counts.items())),
            "accepted_unique_replicates_by_stage": {
                stage: len(replicates)
                for stage, replicates in sorted(stage_replicates.items())
            },
            "run_integrity": run_integrity,
        },
        "creatives": creatives,
        "screening": {
            "primary_label": "How often it ranked among the leaders",
            "technical_label": "Conditional Within-Run Stability",
            "estimand_primary_label": estimand_primary_label,
            "estimand_technical_label": estimand_technical_label,
            "validity_status": validity,
            "rows": _screening_rows(screening, responses_raw, creative_names),
            "interpretation_limits": [
                str(item) for item in screening.get("interpretation_limits", [])
            ],
            "archetype_sensitivity": _clean_specialist_fields(
                screening.get("archetype_sensitivity", {})
            ),
            "boundary": boundary_payload,
        },
        "finalists": finalist_payload,
        "feedback": _normalize_feedback(feedback, creative_names, segment_names),
        "responses": responses,
        "methodology": methodology,
        "visual_evidence": visual_evidence,
        "exports": _build_exports(
            run_dir,
            visual_evidence is not None,
            responses,
            audience_files,
            str(audience_payload.get("state", "legacy")),
            allocation_job_files,
        ),
    }


def _script_safe_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        encoded.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def render_dashboard(
    run_dir: str | Path,
    template_path: str | Path,
    output_path: str | Path,
    include_saliency: bool = False,
    authority_registry: object | None = None,
) -> Path:
    """Compile one run; imagery automatically requires and includes attention evidence.

    ``include_saliency`` remains accepted for call-site compatibility but cannot suppress
    required evidence for imagery or create a heatmap for a copy-only study.
    """

    if not isinstance(include_saliency, bool):
        raise DashboardInputError("include_saliency must be a boolean")
    run_dir, template_path = _validate_run_paths(Path(run_dir), Path(template_path))
    output_path = Path(output_path).expanduser().resolve()

    manifest = _load_json(run_dir / "study-manifest.json")
    study_id = manifest.get("study_id")
    if not _non_empty_string(study_id):
        raise DashboardInputError("study-manifest.json requires a non-empty study_id")
    roster = _load_json(run_dir / "creative-roster.json")
    responses_raw = _load_jsonl(run_dir / "panelist-responses.jsonl")
    screening = _load_json(run_dir / "screening-model-results.json")
    finalists = _load_json(run_dir / "finalist-results.json")
    feedback = _load_json(run_dir / "feedback-synthesis.json")
    boundary_path = run_dir / "boundary-results.json"
    boundary = _load_json(boundary_path) if boundary_path.is_file() else None
    saliency_path = run_dir / "saliency-index.json"
    saliency = _load_json(saliency_path) if saliency_path.is_file() else None

    named_payloads: list[tuple[str, Mapping[str, Any]]] = [
        ("creative-roster.json", roster),
        ("screening-model-results.json", screening),
        ("finalist-results.json", finalists),
        ("feedback-synthesis.json", feedback),
    ]
    if boundary is not None:
        named_payloads.append(("boundary-results.json", boundary))
    if saliency is not None:
        named_payloads.append(("saliency-index.json", saliency))
    _validate_study_ids(study_id, named_payloads, responses_raw)
    _validate_cross_stage_integrity(
        manifest,
        roster,
        responses_raw,
        screening,
        boundary,
        finalists,
        feedback,
    )
    _validate_lineage_integrity(run_dir, manifest, responses_raw)

    creatives, creative_names = _normalize_creatives(run_dir, roster)
    audience_files = _audience_snapshot_bytes(run_dir, manifest)
    audience = manifest.get("audience_lock")
    if not isinstance(audience, Mapping):
        audience = {}
    allocation_job_files: Mapping[str, bytes] | None = None
    if audience_files is not None:
        is_v3_audience = (
            isinstance(manifest.get("audience_package"), Mapping)
            and manifest["audience_package"].get("schema_version")
            == "audience-panel-package-v3"
        )
        if is_v3_audience:
            brief, panel, composition, _envelope = _validated_v3_audience_package(
                manifest,
                audience_files,
                run_dir / "audience" / "resolution.json",
            )
            audience_state = "research_backed"
        else:
            brief, panel, audience_state = _validated_audience_package(
                manifest, audience_files
            )
        segment_names = {
            str(item["segment_id"]): str(item["name"])
            for item in panel["segments"]
        }
        archetype_names = {
            str(item["persona_archetype_id"]): str(item["display_name"])
            for item in panel["persona_archetypes"]
        }
        audience_payload = _audience_payload_from_panel(
            brief, panel, responses_raw, audience_state
        )
        if is_v3_audience:
            run_allocation, allocation_job_files = _validated_v3_run_allocation(
                run_dir=run_dir,
                manifest=manifest,
                composition=composition,
                responses=responses_raw,
                screening=screening,
                boundary=boundary,
                finalists=finalists,
            )
            audience_payload["run_allocation"] = run_allocation
    else:
        segment_names = _name_map(audience.get("segment_names"))
        archetype_names = _name_map(audience.get("archetype_names"))
        audience_payload = {
            "state": "legacy",
            "state_label": "Legacy panel metadata — audience research package unavailable",
            "intro": (
                "The saved panelist profile definitions and audience research are "
                "unavailable for this legacy panel."
            ),
            "panel_id": str(audience.get("panel_id", "")),
            "panel_name": "Legacy audience panel",
            "panel_version": str(audience.get("panel_version", "")),
            "research_mode": "unavailable",
            "research_date": "unavailable",
            "target_audience": str(
                audience.get("audience_scope", {}).get("audience", "")
                if isinstance(audience.get("audience_scope"), Mapping)
                else ""
            ),
            "scope": _clean_specialist_fields(audience.get("audience_scope", {})),
            "segments": [],
            "panelist_profiles": [],
            "panelist_profile_count": len(
                audience.get("archetype_profiles", {}) or {}
            ),
            "archetype_profiles": _clean_specialist_fields(
                audience.get("archetype_profiles", {})
            ),
        }
    responses = _normalize_responses(
        responses_raw,
        creative_names,
        segment_names,
        archetype_names,
        str(audience.get("persona_research_brief_id", "not recorded")),
    )
    imagery_expected = _imagery_expected(manifest, creatives)
    media_representations = {
        str(medium["representation_id"]): {
            "variation_id": str(creative["variation_id"]),
            "content_hash": str(medium["content_hash"]),
        }
        for creative in creatives
        for medium in creative.get("media", [])
    }
    visual_evidence, visual_note = _normalize_visual_evidence(
        run_dir,
        saliency,
        finalists,
        creative_names,
        imagery_expected,
        media_representations,
    )
    tier4_validation = _tier4_validation_payload(
        run_dir, manifest, authority_registry=authority_registry,
    )
    dashboard_data = _payload(
        run_dir,
        manifest,
        creatives,
        creative_names,
        responses_raw,
        responses,
        screening,
        boundary,
        finalists,
        feedback,
        imagery_expected,
        visual_evidence,
        visual_note,
        audience_payload,
        audience_files,
        allocation_job_files,
    )
    if tier4_validation is not None:
        dashboard_data["tier4_validation"] = tier4_validation

    try:
        template = template_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DashboardInputError(f"dashboard template is not valid UTF-8: {exc}") from exc
    placeholder_count = template.count(JSON_PAYLOAD_PLACEHOLDER)
    if placeholder_count != 1:
        raise DashboardInputError(
            "dashboard template must contain exactly one __DASHBOARD_DATA__ placeholder"
        )
    logo_placeholder_count = template.count(BRAND_LOGO_PLACEHOLDER)
    if logo_placeholder_count not in {0, 1}:
        raise DashboardInputError(
            "dashboard template may contain at most one __IP_LOGO_DATA_URL__ placeholder"
        )
    for placeholder in (
        V3_ALLOCATION_SECTION_PLACEHOLDER,
        V3_ALLOCATION_SCRIPT_PLACEHOLDER,
        TIER4_VALIDATION_SECTION_PLACEHOLDER,
        TIER4_VALIDATION_SCRIPT_PLACEHOLDER,
    ):
        if template.count(placeholder) != 1:
            raise DashboardInputError(
                f"dashboard template must contain exactly one {placeholder} placeholder"
            )
    rendered = template.replace(
        JSON_PAYLOAD_PLACEHOLDER, _script_safe_json(dashboard_data), 1
    )
    allocation_available = allocation_job_files is not None
    rendered = rendered.replace(
        V3_ALLOCATION_SECTION_PLACEHOLDER,
        V3_ALLOCATION_SECTION_HTML if allocation_available else "",
        1,
    ).replace(
        V3_ALLOCATION_SCRIPT_PLACEHOLDER,
        V3_ALLOCATION_SCRIPT if allocation_available else "",
        1,
    ).replace(
        TIER4_VALIDATION_SECTION_PLACEHOLDER,
        TIER4_VALIDATION_SECTION_HTML if tier4_validation is not None else "",
        1,
    ).replace(
        TIER4_VALIDATION_SCRIPT_PLACEHOLDER,
        TIER4_VALIDATION_SCRIPT if tier4_validation is not None else "",
        1,
    )
    if logo_placeholder_count:
        logo_path = template_path.parent / "ip-logo-white.png"
        if not logo_path.is_file():
            raise DashboardInputError(f"dashboard brand logo not found: {logo_path}")
        logo_bytes = logo_path.read_bytes()
        logo_data_url = "data:image/png;base64," + base64.b64encode(logo_bytes).decode("ascii")
        rendered = rendered.replace(BRAND_LOGO_PLACEHOLDER, logo_data_url, 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    return output_path


__all__ = [
    "DashboardInputError", "render_dashboard", "TIER4_VALIDATION_SECTION_HTML",
    "TIER4_VALIDATION_SCRIPT",
]
