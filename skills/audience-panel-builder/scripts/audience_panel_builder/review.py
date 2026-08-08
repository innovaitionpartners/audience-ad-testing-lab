"""Marketer-readable panel and construction validation reports."""

from __future__ import annotations

from collections import Counter
from html import escape
import hashlib
import re
from typing import Any, Mapping

from .common import (
    ContractError,
    canonical_json_bytes,
    require_timestamp,
    require_url,
)
from .evidence import validate_evidence_ledger, validate_finding_support
from .review_html import render_dashboard_panel_review_html
from .synthesis import validate_synthesis_matrix


_COUNT_KEYS = (
    "audience_groups",
    "mindsets",
    "buying_situations",
    "reusable_profiles",
    "requested_synthetic_panelists",
    "response_jobs",
    "accepted_response_records",
    "retries",
    "rejected_provider_returns",
    "model_calls",
)

_COUNT_LABELS = (
    ("audience_groups", "Audience groups"),
    ("mindsets", "Mindsets"),
    ("buying_situations", "Buying situations"),
    ("reusable_profiles", "Reusable profiles"),
    (
        "requested_synthetic_panelists",
        "Requested/planned unique synthetic panelists (job slots)",
    ),
    ("response_jobs", "Response jobs"),
    ("accepted_response_records", "Accepted response records"),
    ("retries", "Retries"),
    ("rejected_provider_returns", "Rejected provider returns"),
    ("model_calls", "Model calls"),
)

PANEL_REVIEW_MANIFEST_SCHEMA_VERSION = "panel-review-manifest-v1"
PANEL_REVIEW_MARKDOWN_PATH = "panel-summary.md"
PANEL_REVIEW_HTML_PATH = "audience-panel-review.html"
_EVIDENCE_SCOPE_RE = re.compile(
    r"(?:^|\s)Evidence scope:\s*(?P<scope>cross-audience|cohort:[a-z][a-z0-9-]*|profile:[a-z][a-z0-9-]*)(?:[.;]|$)",
    re.IGNORECASE,
)
_EVIDENCE_EXCEPTION_RE = re.compile(
    r"Evidence-specificity exception\s*\["
    r"unsupported_distinction=(?P<distinction>[^;\]\n]{12,});\s*"
    r"missing_research=(?P<research>[^;\]\n]{12,});\s*"
    r"bounded_use=(?P<use>[^;\]\n]{12,})"
    r"\]:\s*(?P<reason>[^\n]{20,})",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _bullet_list(values: list[Any], empty: str = "None documented") -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- {_text(value)}" for value in values)


def _display(value: Any, *, unknown: str = "Unknown / not documented") -> str:
    if value is None or value == "" or value == []:
        return unknown
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _text(value)


def _inline_list(values: Any, *, empty: str = "None documented") -> str:
    if not isinstance(values, list) or not values:
        return empty
    return ", ".join(_text(value) for value in values)


def _markdown_link(label: Any, url: Any, path: str) -> str:
    safe_label = _text(label).replace("[", "\\[").replace("]", "\\]")
    if url is None or url == "":
        return f"{safe_label} — link not recorded"
    safe_url = require_url(url, path)
    return f"[{safe_label}]({safe_url})"


def build_source_link_overrides(
    scored_sources: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Resolve auditable source URLs when a brief intentionally omits them."""

    if scored_sources is None:
        return {}
    candidates = scored_sources.get("candidates", [])
    if not isinstance(candidates, list):
        raise ContractError("scored_sources.candidates must be an array")
    links: dict[str, str] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise ContractError(f"scored_sources.candidates[{index}] must be an object")
        decision = candidate.get("decision")
        if not isinstance(decision, str) or decision.startswith("reject"):
            continue
        url = require_url(
            candidate.get("source_url"),
            f"scored_sources.candidates[{index}].source_url",
        )
        identifiers = [candidate.get("candidate_id")]
        evidence_item_ids = candidate.get("evidence_item_ids", [])
        if isinstance(evidence_item_ids, list):
            identifiers.extend(evidence_item_ids)
        for identifier in identifiers:
            if isinstance(identifier, str) and identifier:
                links.setdefault(identifier, url)
    return links


def _heading_list(label: str, values: Any, *, empty: str) -> list[str]:
    return [f"**{label}**", "", _bullet_list(values if isinstance(values, list) else [], empty), ""]


def _render_validity_scope(
    panel: Mapping[str, Any], *, provisional: bool
) -> list[str]:
    lines = ["## Validity and Use Boundaries", ""]
    if panel["schema_version"] == "saved-audience-panel-v3":
        lines.extend(
            [
                f"- **Panel tier:** `{panel['panel_tier']}`",
                f"- **Evidence basis:** `{panel['evidence_basis']}`",
                f"- **Claim boundary:** {_text(panel['claim_boundary'])}",
                f"- **Package status:** `{panel['package_status']}`",
                f"- **Canonical brief ID:** `{panel['brief_id']}`",
                f"- **Population-frame result binding:** {_display(panel['population_frame_result_sha256'], unknown='Not bound')}",
                f"- **Population-frame binding:** {_display(panel['population_frame_sha256'], unknown='Not bound')}",
                f"- **Composition-plan binding:** {_display(panel['composition_plan_sha256'], unknown='Not bound')}",
                f"- **Validity-profile binding:** {_display(panel['validity_profile_sha256'], unknown='Not bound')}",
                f"- **Authorized-handoff binding:** {_display(panel['authorized_handoff_sha256'], unknown='Not bound')}",
                "",
                "### Construction audit binding",
                "",
            ]
        )
        for key, value in panel["audit_binding"].items():
            lines.append(
                f"- **{_text(key).replace('_', ' ').title()}:** {_display(value, unknown='Not bound')}"
            )
        lines.extend(
            [
                "",
                (
                    "This provisional v3 panel is a one-run planning input with no research evidence. Its v3 bindings are shown as explicit empty or bounded states, not as supported population claims."
                    if provisional
                    else "Population and validity claims are limited to the bound v3 documents and the recorded claim boundary."
                ),
            ]
        )
    elif provisional:
        lines.extend(
            [
                "- A provisional package is a one-run planning input with no research evidence. It is not a Tier 1 evidence-grounded panel.",
                "- Population composition is not available.",
            ]
        )
    else:
        lines.extend(
            [
                "- An approved v2 package is a Tier 1 evidence-grounded panel.",
                "- Population composition not available in Release A.",
            ]
        )
    lines.extend(
        [
            "- Directional creative hypothesis stress test.",
            "",
        ]
    )
    return lines


def _manifest_entry(path: str, contents: bytes, media_type: str) -> dict[str, object]:
    return {
        "path": path,
        "media_type": media_type,
        "sha256": hashlib.sha256(contents).hexdigest(),
        "bytes": len(contents),
    }


def _evidence_specificity_exception(archetype: Mapping[str, Any]) -> str | None:
    boundary = archetype.get("inference_boundary")
    if not isinstance(boundary, str):
        return None
    match = _EVIDENCE_EXCEPTION_RE.search(boundary.strip())
    if match is None:
        return None
    return (
        f"Unsupported distinction: {match.group('distinction').strip()}; "
        f"missing research: {match.group('research').strip()}; "
        f"bounded use: {match.group('use').strip()}; "
        f"justification: {match.group('reason').strip()}"
    )


def _finding_evidence_scope(finding: Mapping[str, Any]) -> tuple[str, str | None]:
    boundary = finding.get("inference_boundary")
    if not isinstance(boundary, str):
        return "cross-audience", None
    match = _EVIDENCE_SCOPE_RE.search(boundary)
    if match is None:
        return "cross-audience", None
    scope = match.group("scope").lower()
    if ":" not in scope:
        return scope, None
    kind, identity = scope.split(":", 1)
    return kind, identity


def audit_evidence_specificity(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
) -> dict[str, object]:
    """Audit whether distinct archetypes have cohort/profile-specific support.

    Finding specificity comes from an explicit evidence-scope declaration in
    the finding's existing inference boundary, never from which archetypes cite
    it. Broad findings may supplement a profile, but they cannot be its only
    support across distinct archetypes unless the archetype records a structured
    exception in its existing inference boundary.
    """

    archetypes = list(panel.get("persona_archetypes", []))
    if panel.get("persona_research", {}).get("status") == "provisional_no_research":
        return {
            "status": "not_applicable",
            "profiles": [
                {
                    "persona_archetype_id": str(item.get("persona_archetype_id", "")),
                    "segment_id": str(item.get("segment_id", "")),
                    "status": "not_applicable",
                    "profile_specific_finding_ids": [],
                    "cohort_specific_finding_ids": [],
                    "broad_finding_ids": [],
                    "identical_finding_set": False,
                    "identical_narrow_finding_set": False,
                    "exception": None,
                    "resolved_findings": [],
                }
                for item in archetypes
            ],
            "identical_evidence_sets": [],
        }
    finding_set_users: dict[tuple[str, ...], list[str]] = {}
    for archetype in archetypes:
        archetype_id = str(archetype.get("persona_archetype_id", ""))
        finding_set = tuple(sorted(str(value) for value in archetype.get("finding_ids", [])))
        finding_set_users.setdefault(finding_set, []).append(archetype_id)

    findings_by_id = {
        str(finding.get("finding_id")): finding
        for finding in brief.get("findings", [])
        if isinstance(finding, Mapping)
    }
    narrow_set_users: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for archetype in archetypes:
        archetype_id = str(archetype.get("persona_archetype_id", ""))
        segment_id = str(archetype.get("segment_id", ""))
        narrow = tuple(
            sorted(
                str(finding_id)
                for finding_id in archetype.get("finding_ids", [])
                if _finding_evidence_scope(
                    findings_by_id.get(str(finding_id), {})
                )
                in {("profile", archetype_id), ("cohort", segment_id)}
            )
        )
        narrow_set_users.setdefault((segment_id, narrow), []).append(archetype_id)
    rows: list[dict[str, object]] = []
    duplicate_evidence_sets: dict[tuple[str, ...], list[str]] = {}
    for archetype in archetypes:
        evidence_ids = tuple(sorted(str(value) for value in archetype.get("evidence_ids", [])))
        duplicate_evidence_sets.setdefault(evidence_ids, []).append(
            str(archetype.get("persona_archetype_id", ""))
        )
        finding_set = tuple(sorted(str(value) for value in archetype.get("finding_ids", [])))
        identical_finding_set = len(finding_set_users.get(finding_set, [])) > 1
        profile_specific = [
            str(finding_id)
            for finding_id in archetype.get("finding_ids", [])
            if _finding_evidence_scope(findings_by_id.get(str(finding_id), {}))
            == ("profile", str(archetype.get("persona_archetype_id", "")))
        ]
        cohort_specific = [
            str(finding_id)
            for finding_id in archetype.get("finding_ids", [])
            if _finding_evidence_scope(findings_by_id.get(str(finding_id), {}))
            == ("cohort", str(archetype.get("segment_id", "")))
        ]
        broad = [
            str(finding_id)
            for finding_id in archetype.get("finding_ids", [])
            if str(finding_id) not in profile_specific
            and str(finding_id) not in cohort_specific
        ]
        narrow_set = tuple(sorted(profile_specific + cohort_specific))
        identical_narrow_finding_set = (
            len(
                narrow_set_users.get(
                    (str(archetype.get("segment_id", "")), narrow_set), []
                )
            )
            > 1
        )
        exception = _evidence_specificity_exception(archetype)
        status = (
            "pass"
            if len(archetypes) <= 1
            or profile_specific
            or (cohort_specific and not identical_narrow_finding_set)
            or exception
            else "fail"
        )
        rows.append(
            {
                "persona_archetype_id": str(archetype.get("persona_archetype_id", "")),
                "segment_id": str(archetype.get("segment_id", "")),
                "status": status,
                "profile_specific_finding_ids": profile_specific,
                "cohort_specific_finding_ids": cohort_specific,
                "broad_finding_ids": broad,
                "identical_finding_set": identical_finding_set,
                "identical_narrow_finding_set": identical_narrow_finding_set,
                "exception": exception,
                "resolved_findings": sorted(
                    finding_id
                    for finding_id in archetype.get("finding_ids", [])
                    if str(finding_id) in findings_by_id
                ),
            }
        )
    duplicates = [
        {"evidence_ids": list(evidence_ids), "persona_archetype_ids": sorted(ids)}
        for evidence_ids, ids in duplicate_evidence_sets.items()
        if evidence_ids and len(ids) > 1
    ]
    return {
        "status": "pass" if all(row["status"] == "pass" for row in rows) else "fail",
        "profiles": rows,
        "identical_evidence_sets": sorted(
            duplicates,
            key=lambda row: tuple(row["persona_archetype_ids"]),
        ),
    }


def _require_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field} must be a non-negative integer")
    return value


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def _require_records(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ContractError(f"{field} must be an array of objects")
    return value


def _render_count_rows(
    counts: Mapping[str, int | None], *, provisional: bool = False
) -> list[str]:
    rows: list[str] = []
    for key, label in _COUNT_LABELS:
        if provisional and key == "reusable_profiles":
            label = "Provisional planning profiles"
        value = counts[key]
        rendered = (
            str(value)
            if value is not None
            else "not available (construction review only)"
        )
        rows.append(f"- **{label}:** {rendered}")
    return rows


def count_panel_entities(
    *,
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    run_plan: Mapping[str, Any] | None = None,
    run_results: Mapping[str, Any] | None = None,
) -> dict[str, int | None]:
    """Return construction and lineage counts without collapsing their meanings."""

    counts: dict[str, int | None] = {
        "audience_groups": len(panel["segments"]),
        "mindsets": len(panel["persona_archetypes"]),
        "buying_situations": len(panel["context_strata"]),
        "reusable_profiles": len(panel["grounded_context_profiles"]),
    }
    if (run_plan is None) != (run_results is None):
        raise ContractError("run plan and run results must be supplied together")
    if run_plan is None:
        counts.update((key, None) for key in _COUNT_KEYS[4:])
        return counts

    run_plan = _require_mapping(run_plan, "run_plan")
    run_results = _require_mapping(run_results, "run_results")
    capacity = _require_mapping(
        run_plan.get("synthetic_replicate_capacity"),
        "run_plan.synthetic_replicate_capacity",
    )
    requested = _require_count(
        capacity.get("required_total"),
        "run_plan.synthetic_replicate_capacity.required_total",
    )
    usage = _require_mapping(run_results.get("usage"), "run_results.usage")
    planned = _require_count(
        usage.get("unique_job_slots_planned"),
        "run_results.usage.unique_job_slots_planned",
    )
    if planned != requested:
        raise ContractError("planned unique job slots disagree with run plan required_total")

    response_jobs = _require_count(
        usage.get("unique_job_slots_dispatched"),
        "run_results.usage.unique_job_slots_dispatched",
    )
    accepted_responses = _require_count(
        usage.get("accepted_response_records"),
        "run_results.usage.accepted_response_records",
    )
    rejected_returns = _require_count(
        usage.get("rejected_attempts"),
        "run_results.usage.rejected_attempts",
    )
    model_calls = _require_count(
        usage.get("total_model_calls"),
        "run_results.usage.total_model_calls",
    )
    if response_jobs > planned:
        raise ContractError("dispatched jobs cannot exceed planned unique job slots")
    if accepted_responses > response_jobs:
        raise ContractError("accepted response records cannot exceed dispatched jobs")
    if rejected_returns > model_calls:
        raise ContractError("rejected provider returns cannot exceed model calls")
    if accepted_responses > model_calls:
        raise ContractError("accepted response records cannot exceed model calls")
    raw_returns = _require_records(
        run_results.get("raw_provider_returns"),
        "run_results.raw_provider_returns",
    )
    if len(raw_returns) != model_calls:
        raise ContractError("raw_provider_returns count disagrees with total_model_calls")
    retries = 0
    rejected_raw_returns = 0
    for index, raw_return in enumerate(raw_returns):
        attempt_number = _require_count(
            raw_return.get("attempt_number"),
            f"run_results.raw_provider_returns[{index}].attempt_number",
        )
        if attempt_number < 1:
            raise ContractError("raw provider return attempt_number must be at least 1")
        if attempt_number > 1:
            retries += 1
        accepted = raw_return.get("accepted")
        if not isinstance(accepted, bool):
            raise ContractError(
                f"run_results.raw_provider_returns[{index}].accepted must be a boolean"
            )
        if not accepted:
            rejected_raw_returns += 1
    if retries > model_calls:
        raise ContractError("retries cannot exceed model calls")
    if rejected_raw_returns != rejected_returns:
        raise ContractError("rejected raw provider returns disagree with rejected_attempts")

    optional_record_checks = (
        ("dispatch_audit", response_jobs),
        ("responses", accepted_responses),
        ("rejected_attempts", rejected_returns),
    )
    for field, expected in optional_record_checks:
        if field in run_results and len(_require_records(run_results[field], f"run_results.{field}")) != expected:
            raise ContractError(f"{field} count disagrees with run results usage")

    counts.update(
        {
            "requested_synthetic_panelists": requested,
            "response_jobs": response_jobs,
            "accepted_response_records": accepted_responses,
            "retries": retries,
            "rejected_provider_returns": rejected_returns,
            "model_calls": model_calls,
        }
    )
    return counts


def render_panel_summary(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    *,
    run_plan: Mapping[str, Any] | None = None,
    run_results: Mapping[str, Any] | None = None,
    source_links: Mapping[str, str] | None = None,
) -> str:
    scope = panel["audience_scope"]
    research = panel["persona_research"]
    counts = count_panel_entities(
        brief=brief, panel=panel, run_plan=run_plan, run_results=run_results
    )
    provisional = research["status"] == "provisional_no_research"
    lines = [
        f"# {panel['panel_name']}",
        "",
        "> Canonical review projection. `saved-audience-panel.json` remains the machine-readable source of truth.",
        "",
        f"**Schema:** `{panel['schema_version']}`",
        "",
        f"**Panel ID:** `{panel['panel_id']}`",
        "",
        f"**Version:** `{panel['version']}`",
        "",
        f"**Created:** `{panel['created_at']}`",
        "",
        f"**Updated:** `{panel['updated_at']}`",
        "",
        f"**Research status:** `{research['status']}`",
        "",
    ]
    if provisional:
        lines.extend(
            [
                "> **Provisional, no-research panel.** Only the accepted scope, segment descriptions, buying context, and planning allocations come from user input. The profile structure is not a research finding. Other audience attributes remain unknown. Empty evidence fields below mean no research support is available; they are not permission to infer missing insights.",
                "",
            ]
        )
    lines.extend(
        [
        "## Audience",
        "",
        f"- **Audience:** {_text(scope['audience'])}",
        f"- **Market:** {_text(scope['market'])}",
        f"- **Category:** {_text(scope['category'])}",
        f"- **Geography:** {_text(scope['geography'])}",
        f"- **Buying context:** {_text(scope['buying_context'])}",
        f"- **Exclusions:** {_inline_list(scope['exclusions'], empty='No exclusions documented')}",
        f"- **Scope fingerprint:** `{scope['scope_fingerprint']}`",
        "",
        "## What This Panel Contains",
        "",
        f"- {counts['audience_groups']} audience segment(s)",
        f"- {counts['mindsets']} buyer mindset(s)",
        f"- {counts['buying_situations']} buyer situation(s)",
        (
            f"- {counts['reusable_profiles']} provisional planning profile(s)"
            if provisional
            else f"- {counts['reusable_profiles']} reusable grounded profile(s)"
        ),
        "",
        (
            "These are one-run planning profiles. They cannot be registered or reused, and they have no research support. Ad Testing Lab creates separate run-specific synthetic panelists from them during the initial test."
            if provisional
            else "These are reusable audience profiles. Ad Testing Lab creates separate run-specific synthetic panelists from them during a test."
        ),
        "",
        "## Count Semantics",
        "",
        *_render_count_rows(counts, provisional=provisional),
        "",
        *_render_validity_scope(panel, provisional=provisional),
        "## Research State",
        "",
        f"- **Brief ID:** `{research['brief_id']}`",
        f"- **Mode:** `{research['mode']}`",
        f"- **Status:** `{research['status']}`",
        f"- **Approved at:** {_display(research['approved_at'], unknown='Not approved')}",
        f"- **Expires at:** {_display(research['expires_at'], unknown='No expiry recorded')}",
        f"- **Source state:** `{research['source_state']}`",
        f"- **Source types:** {_inline_list(research['source_types'], empty='No research source types')}",
        f"- **Evidence IDs:** {_inline_list(research['evidence_ids'], empty='No research evidence IDs')}",
        "",
        "### Coverage",
        "",
        "| Decision area | Coverage |",
        "|---|---|",
    ])
    for key, value in research["coverage"].items():
        lines.append(f"| {_text(key).replace('_', ' ').title()} | `{value}` |")
    lines.extend(["", "### Evidence gaps", ""])
    if research["evidence_gaps"]:
        for gap in research["evidence_gaps"]:
            lines.extend(
                [
                    f"- **Gap:** {_text(gap['gap'])}",
                    f"  - Impact: {_text(gap['impact_on_panel'])}",
                    f"  - Mitigation: {_text(gap['mitigation'])}",
                ]
            )
    else:
        lines.append("- No research evidence gaps are documented.")

    lines.extend(["", "## Segments", ""])
    for segment in panel["segments"]:
        lines.extend(
            [
                f"### {_text(segment['name'])}",
                "",
                f"- **Segment ID:** `{segment['segment_id']}`",
                f"- **Origin:** `{segment['origin']}`",
                f"- **Why it exists:** {_text(segment['description'])}",
                f"- **Planning weight:** `{segment['study_weight']:.3f}`",
                f"- **Weighting rule:** `{segment['weighting_rule']}`",
                f"- **Weight source evidence:** {_inline_list(segment['weight_source_evidence'], empty='None; this is a planning allocation')}",
                f"- **Finding IDs:** {_inline_list(segment['finding_ids'], empty='No research findings')}",
                f"- **Evidence IDs:** {_inline_list(segment['evidence_ids'], empty='No research evidence')}",
                "",
                *_heading_list("Primary needs", segment["primary_needs"], empty="Unknown; no researched needs available"),
                *_heading_list("Primary objections", segment["primary_objections"], empty="Unknown; no researched objections available"),
                *_heading_list("Creative implications", segment["creative_implications"], empty="Unknown; no research-derived creative implications available"),
            ]
        )
    archetypes = {
        item["persona_archetype_id"]: item
        for item in panel["persona_archetypes"]
    }
    lines.extend(["", "## Persona Archetypes", ""])
    for archetype in panel["persona_archetypes"]:
        lines.extend(
            [
                f"### {_text(archetype['display_name'])}",
                "",
                f"- **Archetype ID:** `{archetype['persona_archetype_id']}`",
                f"- **Segment ID:** `{archetype['segment_id']}`",
                f"- **Role context:** {_text(archetype['role_context'])}",
                f"- **Decision context:** {_text(archetype['decision_context'])}",
                f"- **Evidence strength:** `{archetype['evidence_strength']}`",
                f"- **Inference boundary:** {_text(archetype['inference_boundary'])}",
                f"- **Finding IDs:** {_inline_list(archetype['finding_ids'], empty='No research findings')}",
                f"- **Evidence IDs:** {_inline_list(archetype['evidence_ids'], empty='No research evidence')}",
                "",
                *_heading_list("Motivations", archetype["motivations"], empty="Unknown; no research-backed motivations available"),
                *_heading_list("Anxieties", archetype["anxieties"], empty="Unknown; no research-backed concerns available"),
                *_heading_list("Triggers", archetype["triggers"], empty="Unknown; no research-backed triggers available"),
                *_heading_list("Objections", archetype["objections"], empty="Unknown; no research-backed objections available"),
                *_heading_list("Proof needs", archetype["proof_needs"], empty="Unknown; no research-backed proof needs available"),
            ]
        )

    lines.extend(["", "## Context Strata", ""])
    for stratum in panel["context_strata"]:
        lines.extend(
            [
                f"### `{stratum['context_stratum_id']}`",
                "",
                f"- **Segment ID:** `{stratum['segment_id']}`",
                f"- **Planned weight:** `{stratum['planned_weight']:.3f}`",
                f"- **Weighting rule:** `{stratum['weighting_rule']}`",
                "",
                "| Attribute | Value | Status | Source evidence | Finding IDs |",
                "|---|---|---|---|---|",
                *[
                    f"| {_text(item['name'])} | {_text(item['value'])} | `{item['status']}` | {_inline_list(item['source_evidence'], empty='No research evidence')} | {_inline_list(item['finding_ids'], empty='No research findings')} |"
                    for item in stratum["dimensions"]
                ],
                "",
            ]
        )

    profile_heading = (
        "## Provisional Planning Profiles"
        if provisional
        else "## Reusable Grounded Profiles"
    )
    lines.extend(["", profile_heading, ""])
    for profile in panel["grounded_context_profiles"]:
        archetype = archetypes[profile["persona_archetype_id"]]
        snapshot = profile["profile_snapshot"]
        lines.extend(
            [
                f"### {_text(archetype['display_name'])}",
                "",
                f"**Profile ID:** `{profile['grounded_profile_id']}`",
                "",
                f"- **Segment ID:** `{profile['segment_id']}`",
                f"- **Archetype ID:** `{profile['persona_archetype_id']}`",
                f"- **Context stratum ID:** `{profile['context_stratum_id']}`",
                "",
                f"**Role and industry context:** {_text(snapshot['role_context'])}",
                "",
                f"**Buying situation:** {_text(snapshot['decision_context'])}",
                "",
                "**Motivations**",
                "",
                _bullet_list(snapshot["motivations"], "Unknown; no research-backed motivations available"),
                "",
                "**Concerns**",
                "",
                _bullet_list(snapshot["anxieties"], "Unknown; no research-backed concerns available"),
                "",
                "**Proof needs**",
                "",
                _bullet_list(snapshot["proof_needs"], "Unknown; no research-backed proof needs available"),
                "",
                "#### Profile attribute provenance",
                "",
                "| Attribute | Value | Status | Source evidence | Finding IDs |",
                "|---|---|---|---|---|",
                *[
                    f"| {_text(item['attribute'])} | {_text(item['value'])} | `{item['status']}` | {_inline_list(item['source_evidence'], empty='No research evidence')} | {_inline_list(item['finding_ids'], empty='No research findings')} |"
                    for item in profile["context_attribute_provenance"]
                ],
                "",
            ]
        )

    specificity = audit_evidence_specificity(brief, panel)
    lines.extend(["## Evidence Specificity Audit", ""])
    lines.append(f"- **Status:** `{specificity['status']}`")
    duplicates = specificity["identical_evidence_sets"]
    if duplicates:
        lines.append(f"- **Identical evidence-set groups:** {len(duplicates)}")
        for row in duplicates:
            lines.append(
                "  - " + _inline_list(row["persona_archetype_ids"]) + ": "
                + _inline_list(row["evidence_ids"])
            )
    else:
        lines.append("- **Identical evidence-set groups:** None")
    lines.append("")
    lines.extend(
        [
            "| Archetype | Result | Profile-specific findings | Cohort-specific findings | Identical full set | Identical narrow set | Broad findings | Justified exception |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in specificity["profiles"]:
        lines.append(
            f"| `{row['persona_archetype_id']}` | `{row['status']}` | "
            f"{_inline_list(row['profile_specific_finding_ids'], empty='None')} | "
            f"{_inline_list(row['cohort_specific_finding_ids'], empty='None')} | "
            f"{_display(row['identical_finding_set'])} | "
            f"{_display(row['identical_narrow_finding_set'])} | "
            f"{_inline_list(row['broad_finding_ids'], empty='None')} | "
            f"{_display(row['exception'], unknown='None')} |"
        )

    strategy = panel["replicate_strategy"]
    lines.extend(
        [
            "",
            "## Replicate Strategy",
            "",
            f"- **Worker unit:** {_text(strategy['worker_unit'])}",
            f"- **Shared-context fallback allowed:** {_display(strategy['shared_context_fallback_allowed'])}",
            f"- **Fields allowed to vary:** {_inline_list(strategy['fields_allowed_to_vary'], empty='None')}",
            f"- **Fields never to invent:** {_inline_list(strategy['fields_never_to_invent'], empty='None documented')}",
            "",
            "## Calibration History",
            "",
        ]
    )
    if panel["calibration_history"]:
        for index, row in enumerate(panel["calibration_history"], start=1):
            lines.extend([f"### Calibration record {index}", ""])
            for key, value in row.items():
                lines.append(
                    f"- **{_text(key).replace('_', ' ').title()}:** "
                    f"{_inline_list(value) if isinstance(value, list) else _display(value)}"
                )
            lines.append("")
    else:
        lines.extend(
            [
                "- No calibration history is recorded. Human alignment and outcome prediction remain untested.",
                "",
            ]
        )

    refresh = panel["refresh_conditions"]
    governance = panel["governance"]
    privacy = governance["privacy_confirmation"]
    lines.extend(
        [
            "## Refresh Conditions",
            "",
            f"- **Review after:** `{refresh['review_after']}`",
            f"- **Maximum age:** {refresh['max_age_days']} days",
            f"- **Refresh triggers:** {_inline_list(refresh['triggers'], empty='No triggers documented')}",
            "",
            "## Governance",
            "",
            f"- **PII policy:** {_text(governance['pii_policy'])}",
            f"- **Allowed uses:** {_inline_list(governance['allowed_uses'], empty='No allowed uses recorded')}",
            f"- **Excluded uses:** {_inline_list(governance['excluded_uses'], empty='No excluded uses recorded')}",
            f"- **Privacy confirmed:** {_display(privacy['confirmed'])}",
            f"- **Confirmed by:** {_display(privacy['confirmed_by'], unknown='Not recorded')}",
            f"- **Confirmed at:** {_display(privacy['confirmed_at'], unknown='Not recorded')}",
            f"- **Privacy note:** {_display(privacy['note'], unknown='No note recorded')}",
            "",
            "## Research Sources",
            "",
        ]
    )
    if brief["evidence_sources"]:
        lines.extend(
            [
                "Every approved source record is listed below with its direct link, permitted uses, and documented limits.",
                "",
                "| Source | Evidence ID | Type | Date | Collection method | Confidence | Usable for | Permitted uses | Limits |",
                "|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for index, source in enumerate(brief["evidence_sources"]):
            source_url = source["source_url"] or (source_links or {}).get(
                source["evidence_id"]
            )
            lines.append(
                f"| {_markdown_link(source['source_label'], source_url, f'brief.evidence_sources[{index}].source_url')} | "
                f"`{source['evidence_id']}` | `{source['type']}` | {_text(source['date'])} | "
                f"`{source['collection_method']}` | `{source['confidence']}` | "
                f"{_inline_list(source['usable_for'], empty='None documented')} | "
                f"{_inline_list(source['permitted_uses'], empty='None documented')} | "
                f"{_text(source['limits'])} |"
            )
    else:
        lines.append(
            "- No research sources exist for this provisional no-research panel."
        )
    lines.extend(
        [
            "",
            "## Research Lineage",
            "",
            f"- **{'Provisional' if provisional else 'Approved'} brief:** `{brief['brief_id']}`",
            f"- **Research route:** `{brief['research_mode']}`",
            f"- **Research tier:** `{brief['research_depth']}`",
            f"- **Evidence source records:** {len(brief['evidence_sources'])}",
            f"- **Findings:** {len(brief['findings'])}",
            "",
            (
                "No audience research report exists for this provisional no-research route."
                if provisional
                else "See `audience-research-report.html` for finding statements, proof points, contradictions, and evidence limitations."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _render_plain_panel_review_html(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    *,
    run_plan: Mapping[str, Any] | None = None,
    run_results: Mapping[str, Any] | None = None,
    source_links: Mapping[str, str] | None = None,
) -> str:
    """Translate the complete Markdown projection into semantic HTML."""

    summary = render_panel_summary(
        brief,
        panel,
        run_plan=run_plan,
        run_results=run_results,
        source_links=source_links,
    )
    def rich(value: str) -> str:
        rendered = escape(value.replace("\\|", "|"))
        rendered = re.sub(
            r"\[([^\]]+)\]\((https?://[^)]+)\)",
            r'<a href="\2" target="_blank" rel="noreferrer">\1 <span aria-hidden="true">↗</span></a>',
            rendered,
        )
        rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
        rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
        return rendered

    body: list[str] = []
    in_list = False
    in_table = False
    table_row_index = 0
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if line.startswith("|---"):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [
                rich(cell.strip())
                for cell in re.split(r"(?<!\\)\|", line.strip("|"))
            ]
            if not in_table:
                if in_list:
                    body.append("</ul>")
                    in_list = False
                body.append("<div class=\"table-wrap\"><table>")
                in_table = True
                table_row_index = 0
            tag = "th" if table_row_index == 0 else "td"
            body.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
            table_row_index += 1
            continue
        if in_table:
            body.append("</table></div>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{rich(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("#### "):
            body.append(f"<h4>{rich(line[5:])}</h4>")
        elif line.startswith("### "):
            body.append(f"<h3>{rich(line[4:])}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{rich(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{rich(line[2:])}</h1>")
        elif line.startswith("> "):
            body.append(f"<aside>{rich(line[2:])}</aside>")
        elif line.startswith("**") and line.endswith("**"):
            body.append(f"<h4>{escape(line.strip('*'))}</h4>")
        else:
            body.append(f"<p>{rich(line)}</p>")
    if in_list:
        body.append("</ul>")
    if in_table:
        body.append("</table></div>")
    provisional_class = " provisional" if panel["persona_research"]["status"] == "provisional_no_research" else ""
    return "<!doctype html>\n" + f"""<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{escape(str(panel['panel_name']))} panel review</title><style>
:root{{--ink:#172033;--muted:#5a6478;--paper:#f4f1ea;--card:#fff;--line:#d8d5cc;--accent:#315b57;--warn:#8a4b21}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 28px 80px}}h1{{font:700 clamp(2.2rem,6vw,4.8rem)/.98 Georgia,serif;letter-spacing:-.04em;margin:.2em 0 .5em}}h2{{font:700 1.75rem/1.15 Georgia,serif;margin:2.2em 0 .65em;border-top:1px solid var(--line);padding-top:1em}}h3{{font-size:1.25rem;margin:2em 0 .5em;color:var(--accent)}}h4{{margin:1.4em 0 .4em}}p,li{{max-width:82ch}}aside{{border-left:5px solid var(--accent);background:#e7efed;padding:18px 20px;margin:18px 0;border-radius:0 10px 10px 0}}body.provisional aside{{border-color:var(--warn);background:#f6e9dd}}code{{font:0.9em ui-monospace,SFMono-Regular,monospace}}ul{{padding-left:1.35em}}li{{margin:.35em 0}}.table-wrap{{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;margin:16px 0 28px}}table{{border-collapse:collapse;width:100%;min-width:620px}}th,td{{padding:12px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}}th{{background:#eceae4;font-size:.78rem;letter-spacing:.04em;text-transform:uppercase}}tr:last-child td{{border-bottom:0}}@media print{{body{{background:#fff}}main{{padding:20px}}}}
</style></head><body class=\"{provisional_class.strip()}\"><main>{''.join(body)}</main></body></html>"""


def render_panel_review_html(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    *,
    run_plan: Mapping[str, Any] | None = None,
    run_results: Mapping[str, Any] | None = None,
    source_links: Mapping[str, str] | None = None,
) -> str:
    """Render the dashboard-native review plus the complete canonical projection."""

    plain_html = _render_plain_panel_review_html(
        brief,
        panel,
        run_plan=run_plan,
        run_results=run_results,
        source_links=source_links,
    )
    full_record_html = plain_html.split("<main>", 1)[1].rsplit("</main>", 1)[0]
    return render_dashboard_panel_review_html(
        brief=brief,
        panel=panel,
        counts=count_panel_entities(
            brief=brief,
            panel=panel,
            run_plan=run_plan,
            run_results=run_results,
        ),
        specificity=audit_evidence_specificity(brief, panel),
        full_record_html=full_record_html,
        source_links=source_links,
    )


def build_panel_review_manifest(
    *,
    panel: Mapping[str, Any],
    summary_bytes: bytes,
    html_bytes: bytes,
    review_revision: str,
    generated_at: str,
) -> dict[str, object]:
    require_timestamp(generated_at, "generated_at")
    if not re.fullmatch(r"review-v[1-9][0-9]*", review_revision):
        raise ContractError("review_revision must match review-vN")
    panel_bytes = canonical_json_bytes(panel)
    return {
        "schema_version": PANEL_REVIEW_MANIFEST_SCHEMA_VERSION,
        "panel_id": panel["panel_id"],
        "panel_version": panel["version"],
        "review_revision": review_revision,
        "generated_at": generated_at,
        "canonical_panel": _manifest_entry(
            "saved-audience-panel.json", panel_bytes, "application/json"
        ),
        "review_outputs": [
            _manifest_entry(PANEL_REVIEW_HTML_PATH, html_bytes, "text/html"),
            _manifest_entry(PANEL_REVIEW_MARKDOWN_PATH, summary_bytes, "text/markdown"),
        ],
    }


def validate_panel_review_manifest(
    payload: object,
    *,
    panel: Mapping[str, Any],
    summary_bytes: bytes,
    html_bytes: bytes,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ContractError("panel review manifest must be an object")
    expected = build_panel_review_manifest(
        panel=panel,
        summary_bytes=summary_bytes,
        html_bytes=html_bytes,
        review_revision=str(payload.get("review_revision", "")),
        generated_at=str(payload.get("generated_at", "")),
    )
    if canonical_json_bytes(payload) != canonical_json_bytes(expected):
        raise ContractError("panel review manifest does not match the exact canonical panel and review outputs")
    return expected


def render_panel_approval_request(
    *,
    panel: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> str:
    manifest_sha256 = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    return "\n".join(
        [
            f"# {panel['panel_name']} panel construction approval request",
            "",
            f"- **Panel ID:** `{panel['panel_id']}`",
            f"- **Panel version:** `{panel['version']}`",
            f"- **Review revision:** `{manifest['review_revision']}`",
            f"- **Panel review manifest SHA-256:** `{manifest_sha256}`",
            f"- **Panel construction approval target SHA-256:** `{manifest_sha256}`",
            f"- **Canonical panel SHA-256:** `{manifest['canonical_panel']['sha256']}`",
            "- **Panel construction approval:** `pending`",
            "",
            "Review `audience-panel-review.html` or `panel-summary.md`. Approve or revise only this exact review revision and manifest digest. Any change to the canonical panel, Markdown projection, HTML projection, or manifest invalidates this request and requires a new review revision.",
            "",
        ]
    )


def render_validation_report(
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    *,
    plan: Mapping[str, Any] | None = None,
    scored_sources: Mapping[str, Any] | None = None,
    ledger: Mapping[str, Any] | None = None,
    finding_support: Mapping[str, Any] | None = None,
    synthesis_matrix: Mapping[str, Any] | None = None,
    run_plan: Mapping[str, Any] | None = None,
    run_results: Mapping[str, Any] | None = None,
) -> str:
    errors: list[str] = []
    if ledger is not None:
        ledger = validate_evidence_ledger(ledger)
    if finding_support is not None:
        if ledger is None:
            raise ContractError("finding support requires an evidence ledger")
        finding_support = validate_finding_support(finding_support, ledger)
        supported = {
            item["finding_id"] for item in finding_support["findings"]
        }
        missing = sorted(
            {item["finding_id"] for item in brief["findings"]} - supported
        )
        if missing:
            errors.append(
                "Brief findings without item-level support: " + ", ".join(missing)
            )
    if synthesis_matrix is not None:
        if ledger is None or finding_support is None:
            raise ContractError(
                "synthesis matrix requires an evidence ledger and finding support"
            )
        synthesis_matrix = validate_synthesis_matrix(
            synthesis_matrix,
            ledger,
            finding_support,
        )
        synthesized = {
            finding["finding_id"]
            for question in synthesis_matrix["questions"]
            for finding in question["findings"]
        }
        missing = sorted(
            {item["finding_id"] for item in brief["findings"]} - synthesized
        )
        if missing:
            errors.append(
                "Brief findings without integrated synthesis: "
                + ", ".join(missing)
            )
    source_decisions = Counter()
    if scored_sources is not None:
        source_decisions.update(
            item["decision"] for item in scored_sources.get("candidates", [])
        )
    route = plan.get("workflow_route") if plan else "compatibility_route_not_recorded"
    evidence_basis = plan.get("evidence_basis") if plan else brief["research_mode"]
    performance_context = plan.get("performance_context") if plan else "not_recorded"
    registry_freshness = (
        plan.get("registry_freshness", {}).get("status", "not_recorded")
        if plan
        else "not_recorded"
    )
    specificity = audit_evidence_specificity(brief, panel)
    provisional = panel["persona_research"]["status"] == "provisional_no_research"
    if specificity["status"] == "fail":
        failed_profiles = [
            str(row["persona_archetype_id"])
            for row in specificity["profiles"]
            if row["status"] == "fail"
        ]
        errors.append(
            "Distinct archetypes rely only on broad cross-audience findings "
            "without a cohort/profile-specific finding or explicit evidence-specificity "
            "exception: " + ", ".join(failed_profiles)
        )
    calibration_state = "none"
    if panel["calibration_history"]:
        calibration_state = "retrospectively_evaluated"
    integrity = "failed" if errors else "passed"
    counts = count_panel_entities(
        brief=brief, panel=panel, run_plan=run_plan, run_results=run_results
    )
    lines = [
        "# Audience Panel Construction Validation",
        "",
        "## Decision",
        "",
        f"- **Research integrity:** `{integrity}`",
        f"- **Panel research status:** `{panel['persona_research']['status']}`",
        f"- **Workflow route:** `{route}`",
        f"- **Research tier:** `{brief['research_depth']}`",
        f"- **Evidence basis:** `{evidence_basis}`",
        f"- **Performance context:** `{performance_context}`",
        f"- **Source registry:** `{registry_freshness}`",
        "",
        "## What Was Validated",
        "",
        f"- {len(brief['evidence_sources'])} source record(s)",
        f"- {len(brief['findings'])} approved finding(s)",
        f"- {len(brief['segment_hypotheses'])} segment hypothesis or hypotheses",
        f"- {len(panel['segments'])} compiled segment(s)",
        f"- {len(panel['persona_archetypes'])} buyer mindset(s)",
        f"- {len(panel['grounded_context_profiles'])} explicit grounded profile(s)",
        f"- {len(ledger['evidence_items']) if ledger else 0} item-level evidence record(s) in the controlled ledger",
        "",
        "## Count Semantics",
        "",
        *_render_count_rows(counts, provisional=provisional),
        "",
        "## Source Decisions",
        "",
    ]
    if source_decisions:
        lines.extend(
            f"- **{decision}:** {count}"
            for decision, count in sorted(source_decisions.items())
        )
    else:
        lines.append("- No adjacent scored-source file was supplied.")
    lines.extend(["", "## Research Synthesis Audit", ""])
    if synthesis_matrix is None:
        lines.append("- No synthesis matrix was supplied.")
    else:
        integration_states = Counter(
            finding["integration_state"]
            for question in synthesis_matrix["questions"]
            for finding in question["findings"]
        )
        confidence_levels = Counter(
            finding["confidence"]
            for question in synthesis_matrix["questions"]
            for finding in question["findings"]
        )
        for state, count in sorted(integration_states.items()):
            lines.append(f"- **{state}:** {count} finding(s)")
        lines.append("")
        for confidence, count in sorted(confidence_levels.items()):
            lines.append(f"- **{confidence} confidence:** {count} finding(s)")
        discordant = [
            finding["finding_id"]
            for question in synthesis_matrix["questions"]
            for finding in question["findings"]
            if finding["integration_state"] == "discordant"
        ]
        lines.append("")
        lines.append(
            "- **Unresolved discordant findings:** "
            + (", ".join(discordant) if discordant else "None")
        )
    lines.extend(
        [
            "",
            "## Separate Validity States",
            "",
            f"- **Human alignment:** `{'task_validated' if panel['calibration_history'] else 'untested'}`",
            (
                f"- **Population composition:** `limited by {panel['panel_tier']} and the recorded claim boundary`"
                if panel["schema_version"] == "saved-audience-panel-v3"
                else "- **Population composition:** `not available`"
            ),
            f"- **Calibration:** `{calibration_state}`",
            "",
            (
                "- A provisional package is a one-run planning input with no research evidence. It is not a Tier 1 evidence-grounded panel."
                if provisional
                else (
                    f"- This is a `{panel['panel_tier']}` v3 panel with `{panel['evidence_basis']}` evidence basis."
                    if panel["schema_version"] == "saved-audience-panel-v3"
                    else "- An approved v2 package is a Tier 1 evidence-grounded panel."
                )
            ),
            "- Directional creative hypothesis stress test.",
            (
                f"- Claim boundary: {_text(panel['claim_boundary'])}"
                if panel["schema_version"] == "saved-audience-panel-v3"
                else "- Population composition not available in Release A."
            ),
            "",
            (
                "Provisional acceptance approves only the accepted scope, segment descriptions, buying context, and planning allocations for one initial synthetic run. It does not approve inferred audience attributes, evidence, registration, reuse, population inference, or individual targeting."
                if provisional
                else "Panel approval means the evidence and construction were approved for the named use. It does not automatically establish population composition or prediction outside the recorded calibration scope."
            ),
            "",
            "## Evidence Specificity",
            "",
            f"- **Specificity audit:** `{specificity['status']}`",
            f"- **Identical evidence-set groups:** {len(specificity['identical_evidence_sets'])}",
            "",
            "## Evidence Gaps",
            "",
        ]
    )
    gaps = brief["evidence_gaps"]
    if gaps:
        for gap in gaps:
            lines.extend(
                [
                    f"- **Gap:** {_text(gap['gap'])}",
                    f"  - Impact: {_text(gap['impact_on_panel'])}",
                    f"  - Mitigation: {_text(gap['mitigation'])}",
                ]
            )
    else:
        lines.append("- No unresolved gap was recorded in the approved brief.")
    lines.extend(["", "## Validation Errors", ""])
    lines.extend(f"- {error}" for error in errors)
    if not errors:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)
