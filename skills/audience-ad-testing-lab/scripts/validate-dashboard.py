#!/usr/bin/env python3
"""Validate a deterministic Ad Testing Lab dashboard."""

from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter
from datetime import datetime
from functools import lru_cache
import hashlib
from html.parser import HTMLParser
import json
import math
import mimetypes
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from audience_lab.dashboard import (
    AUDIENCE_SNAPSHOT_FILES,
    AUDIENCE_SNAPSHOT_FILES_V3,
    BRAND_LOGO_PLACEHOLDER,
    DashboardInputError,
    FEEDBACK_CLAIM_FIELDS,
    FEEDBACK_EVIDENCE_SCOPES,
    FEEDBACK_TYPES,
    JSON_PAYLOAD_PLACEHOLDER,
    V3_ALLOCATION_SCRIPT,
    V3_ALLOCATION_SCRIPT_PLACEHOLDER,
    V3_ALLOCATION_SECTION_HTML,
    V3_ALLOCATION_SECTION_PLACEHOLDER,
    TIER4_VALIDATION_SCRIPT,
    TIER4_VALIDATION_SCRIPT_PLACEHOLDER,
    TIER4_VALIDATION_SECTION_HTML,
    TIER4_VALIDATION_SECTION_PLACEHOLDER,
    _audience_payload_from_panel,
    _validated_audience_package,
    _validated_v3_audience_package,
    _validated_v3_run_allocation,
    feedback_action_error,
    feedback_claim_error,
)
from audience_lab.audience_package_v3 import (
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
)
from audience_lab.lineage import CANONICAL_LINEAGE_FILES, validate_bound_lineage


PLACEHOLDER_RE = re.compile(r"__[A-Z0-9_]+__")
PAYLOAD_RE = re.compile(
    r'<script\s+type="application/json"\s+id="audience-lab-data">(.*?)</script>',
    re.I | re.S,
)
EXTERNAL_ASSET_RE = re.compile(r'(?i)(?:src|href)\s*=\s*["\']https?://')
UNQUALIFIED_SCREENING_PERCENT_RE = re.compile(
    r"(?i)\b\d{1,3}(?:\.\d+)?%\s+(?:liked(?:\s+it)?|preferred|would\s+choose)\b"
)
FORBIDDEN_TERMS = (
    "Confidence Rating",
    "Ranked Synthetic Preference",
    "independent panelists",
    "real focus group",
)
REQUIRED_MARKETER_TEXT = (
    "Creative test results",
    "What this test says",
    "The ads we tested",
    "How every ad performed",
    "Overall result",
    "How often it ranked among the leaders",
    "How the top ads compared",
    "How the top ads scored",
    "Chosen first",
    "Panelist profile",
    "What synthetic panelists said",
    "All verbatim AI feedback",
    "Show feedback for",
    "AI audience responses provides the compact table view",
    "AI audience responses",
    "Who participated in this run",
    "Synthetic panelist ID",
    "Response table",
    "Full panelist records",
    "Download responses for Excel (.csv)",
    "How this test worked",
    "The basic idea",
    "What each synthetic panelist was asked",
    "Technical audit resources",
    "What the results mean",
    "Download the source data",
    "Synthetic panelists",
    "Audience profiles",
    "Synthetic panelists by test stage",
    "Main-test synthetic panelists",
    "Tie-break synthetic panelists",
    "Closer-review synthetic panelists",
    "No substantive audience research was conducted",
    "Technical audit files",
)
PAYLOAD_MARKETER_TEXT = {
    "Overall result",
    "How often it ranked among the leaders",
}
REJECTED_PRIMARY_HEADING_RE = re.compile(
    r"<h[1-3][^>]*>\s*(?:Run validity and provenance|Screening evidence|"
    r"Approved finalist evidence|Feedback themes|Synthetic panelist responses)\s*</h[1-3]>",
    re.I,
)
REQUIRED_TABS = (
    "Overview",
    "Ads tested",
    "Test audience",
    "All ad results",
    "Top ads",
    "Feedback",
    "AI audience responses",
    "Methodology",
    "Downloads",
)
REQUIRED_FILTERS = (
    "stage",
    "synthetic-profile",
    "segment",
    "archetype",
    "creative",
    "best",
    "weakest",
    "tie",
    "unable-to-judge",
)
REQUIRED_INTEGRITY_DIMENSIONS = (
    "Research basis",
    "Input fidelity",
    "Review integrity",
    "Design adequacy",
    "Result stability",
)
APPROVED_ROSTER_STATES = {"approved", "approved_with_override"}
RENDERABLE_IMAGE_MIME_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
}
HTML_INERT_SUBTREES = frozenset({"template", "noscript"})
HTML_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)"
    r"\s*(?:!\s*important\s*)?(?:;|$)",
    re.I,
)
ALLOCATION_SECTION_ATTRIBUTES = {
    "class": "ledger-panel",
    "id": "audience-run-allocation",
}
ALLOCATION_BODY_ATTRIBUTES = {
    "class": "advanced-details-body",
    "id": "audience-run-allocation-body",
}
TIER4_SECTION_ATTRIBUTES = {
    "class": "ledger-panel",
    "id": "held-out-ordering-validation",
}
TIER4_BODY_ATTRIBUTES = {
    "class": "advanced-details-body",
    "id": "held-out-ordering-validation-body",
}
DYNAMIC_AUDIENCE_TAB_ATTRIBUTES = {
    "class": "tab-panel",
    "id": "panel-audience",
    "role": "tabpanel",
    "aria-labelledby": "tab-audience",
    "hidden": None,
}
CANONICAL_PAYLOAD_SENTINEL = "audience-lab-authenticated-json-payload"


def _exact_attributes(
    attrs: list[tuple[str, str | None]],
    expected: Mapping[str, str | None],
) -> bool:
    return len(attrs) == len(expected) and dict(attrs) == expected


def _hides_ui(
    tag: str,
    attrs: list[tuple[str, str | None]],
    attributes: Mapping[str, str | None],
) -> bool:
    is_runtime_controlled_audience_tab = (
        tag == "section"
        and _exact_attributes(attrs, DYNAMIC_AUDIENCE_TAB_ATTRIBUTES)
    )
    # The canonical Audience tab starts hidden; authenticated runtime code
    # removes that attribute when the user activates the tab.
    if (
        "hidden" in attributes
        and not is_runtime_controlled_audience_tab
    ) or "inert" in attributes:
        return True
    aria_hidden = attributes.get("aria-hidden")
    if (
        isinstance(aria_hidden, str)
        and aria_hidden.strip().lower() == "true"
    ):
        return True
    style = attributes.get("style")
    return isinstance(style, str) and HIDDEN_STYLE_RE.search(style) is not None


class DashboardHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tab_labels: list[str] = []
        self.element_ids: Counter[str] = Counter()
        self.elements_by_id: Counter[tuple[str, str]] = Counter()
        self.exact_allocation_sections = 0
        self.exact_allocation_bodies = 0
        self.nested_allocation_bodies = 0
        self.exact_tier4_sections = 0
        self.exact_tier4_bodies = 0
        self.nested_tier4_bodies = 0
        self.active_body_count = 0
        self.script_elements: list[
            tuple[list[tuple[str, str | None]], str, bool]
        ] = []
        self._element_stack: list[tuple[str, bool, bool, bool, bool]] = []
        self._in_tab = False
        self._tab_text: list[str] = []
        self._script_attributes: list[tuple[str, str | None]] | None = None
        self._script_is_executable = False
        self._script_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        ancestor_excluded = (
            self._element_stack[-1][1] if self._element_stack else False
        )
        ancestor_hidden = (
            self._element_stack[-1][2] if self._element_stack else False
        )
        under_active_body = any(
            element[3] for element in self._element_stack
        )
        excluded = ancestor_excluded or tag in HTML_INERT_SUBTREES
        hidden = ancestor_hidden or _hides_ui(tag, attrs, attributes)
        is_active_body = (
            tag == "body"
            and not excluded
            and not under_active_body
        )
        if is_active_body:
            self.active_body_count += 1
        active_ui = under_active_body and not excluded and not hidden

        element_id = attributes.get("id")
        if active_ui and isinstance(element_id, str):
            self.element_ids[element_id] += 1
            self.elements_by_id[(tag, element_id)] += 1

        exact_section = (
            active_ui
            and tag == "section"
            and _exact_attributes(attrs, ALLOCATION_SECTION_ATTRIBUTES)
        )
        if exact_section:
            self.exact_allocation_sections += 1
        exact_body = (
            active_ui
            and tag == "div"
            and _exact_attributes(attrs, ALLOCATION_BODY_ATTRIBUTES)
        )
        if exact_body:
            self.exact_allocation_bodies += 1
            if any(element[4] for element in self._element_stack):
                self.nested_allocation_bodies += 1
        exact_tier4_section = (
            active_ui
            and tag == "section"
            and _exact_attributes(attrs, TIER4_SECTION_ATTRIBUTES)
        )
        if exact_tier4_section:
            self.exact_tier4_sections += 1
        exact_tier4_body = (
            active_ui
            and tag == "div"
            and _exact_attributes(attrs, TIER4_BODY_ATTRIBUTES)
        )
        if exact_tier4_body:
            self.exact_tier4_bodies += 1
            if any(element[4] for element in self._element_stack):
                self.nested_tier4_bodies += 1

        if tag == "script":
            self._script_attributes = list(attrs)
            self._script_is_executable = under_active_body and not excluded
            self._script_text = []
        if tag == "button" and attributes.get("role") == "tab":
            self._in_tab = True
            self._tab_text = []
        if tag not in HTML_VOID_ELEMENTS:
            self._element_stack.append(
                (tag, excluded, hidden, is_active_body, exact_section or exact_tier4_section)
            )

    def handle_data(self, data: str) -> None:
        if self._script_attributes is not None:
            self._script_text.append(data)
        if self._in_tab:
            self._tab_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self._script_attributes is not None:
            self.script_elements.append(
                (
                    self._script_attributes,
                    "".join(self._script_text),
                    self._script_is_executable,
                )
            )
            self._script_attributes = None
            self._script_is_executable = False
            self._script_text = []
        if tag == "button" and self._in_tab:
            self.tab_labels.append(" ".join("".join(self._tab_text).split()))
            self._in_tab = False
            self._tab_text = []
        for position in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[position][0] == tag:
                del self._element_stack[position:]
                break

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in HTML_VOID_ELEMENTS:
            self.handle_endtag(tag)

    def runtime_script_bodies(self) -> list[str]:
        markers = (
            "(() => {",
            '"use strict";',
            'const payloadNode = document.getElementById("audience-lab-data");',
            "function make(tag, className, text) {",
            "function renderAudience() {",
        )
        return [
            body
            for attributes, body, is_executable in self.script_elements
            if is_executable
            and not attributes
            and all(marker in body for marker in markers)
        ]


@lru_cache(maxsize=4)
def _canonical_runtime_script_sha256(has_run_allocation: bool, has_tier4_validation: bool = False) -> str:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "dashboard-template.html"
    )
    template = template_path.read_text(encoding="utf-8")
    if template.count(V3_ALLOCATION_SCRIPT_PLACEHOLDER) != 1 or template.count(TIER4_VALIDATION_SCRIPT_PLACEHOLDER) != 1:
        raise RuntimeError(
            "shipped dashboard template does not contain exactly one "
            "v3 allocation renderer placeholder"
        )
    canonical = template.replace(
        V3_ALLOCATION_SCRIPT_PLACEHOLDER,
        V3_ALLOCATION_SCRIPT if has_run_allocation else "",
        1,
    ).replace(
        TIER4_VALIDATION_SCRIPT_PLACEHOLDER,
        TIER4_VALIDATION_SCRIPT if has_tier4_validation else "",
        1,
    )
    parser = DashboardHTMLParser()
    parser.feed(canonical)
    parser.close()
    runtime_bodies = parser.runtime_script_bodies()
    if len(runtime_bodies) != 1:
        raise RuntimeError(
            "shipped dashboard template does not contain exactly one "
            "canonical runtime script"
        )
    return hashlib.sha256(runtime_bodies[0].encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _canonical_dashboard_shell(
    has_run_allocation: bool,
    has_tier4_validation: bool,
) -> str:
    assets_path = Path(__file__).resolve().parent.parent / "assets"
    template_path = assets_path / "dashboard-template.html"
    template = template_path.read_text(encoding="utf-8")
    replacements = (
        (JSON_PAYLOAD_PLACEHOLDER, CANONICAL_PAYLOAD_SENTINEL),
        (V3_ALLOCATION_SECTION_PLACEHOLDER, V3_ALLOCATION_SECTION_HTML if has_run_allocation else ""),
        (V3_ALLOCATION_SCRIPT_PLACEHOLDER, V3_ALLOCATION_SCRIPT if has_run_allocation else ""),
        (TIER4_VALIDATION_SECTION_PLACEHOLDER, TIER4_VALIDATION_SECTION_HTML if has_tier4_validation else ""),
        (TIER4_VALIDATION_SCRIPT_PLACEHOLDER, TIER4_VALIDATION_SCRIPT if has_tier4_validation else ""),
    )
    canonical = template
    for placeholder, replacement in replacements:
        if canonical.count(placeholder) != 1:
            raise RuntimeError(
                "shipped dashboard template does not contain exactly one "
                f"{placeholder} placeholder"
            )
        canonical = canonical.replace(placeholder, replacement, 1)

    if canonical.count(BRAND_LOGO_PLACEHOLDER) != 1:
        raise RuntimeError(
            "shipped dashboard template does not contain exactly one "
            f"{BRAND_LOGO_PLACEHOLDER} placeholder"
        )
    logo_path = assets_path / "ip-logo-white.png"
    if not logo_path.is_file():
        raise RuntimeError(f"shipped dashboard brand logo not found: {logo_path}")
    logo_data_url = (
        "data:image/png;base64,"
        + base64.b64encode(logo_path.read_bytes()).decode("ascii")
    )
    return canonical.replace(BRAND_LOGO_PLACEHOLDER, logo_data_url, 1)


def visible_text(html: str) -> str:
    without_scripts = re.sub(r"<script\b.*?</script>", " ", html, flags=re.I | re.S)
    without_styles = re.sub(r"<style\b.*?</style>", " ", without_scripts, flags=re.I | re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", without_styles).split())


def _specialist_keys(value: Any, path: str = "payload") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if "specialist_score" in str(key).lower():
                findings.append(child)
            findings.extend(_specialist_keys(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_specialist_keys(item, f"{path}[{index}]"))
    return findings


def _string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key == "data_url":
                continue
            values.extend(_string_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_string_values(item))
    return values


def _screening_strings(payload: Mapping[str, Any]) -> list[str]:
    values = _string_values(payload.get("screening"))
    feedback = payload.get("feedback")
    if isinstance(feedback, list):
        values.extend(
            text
            for item in feedback
            if isinstance(item, Mapping) and item.get("stage") == "screening"
            for text in _string_values(item)
        )
    responses = payload.get("responses")
    if isinstance(responses, list):
        values.extend(
            text
            for item in responses
            if isinstance(item, Mapping) and item.get("stage") == "screening"
            for text in _string_values(item)
        )
    return values


def _aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _decode_data_url(value: Any, context: str) -> tuple[bytes | None, str | None]:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None, f"{context} is not a data URL."
    metadata, encoded = value.split(",", 1)
    if not metadata.endswith(";base64"):
        return None, f"{context} must use base64 encoding."
    try:
        return base64.b64decode(encoded, validate=True), None
    except (binascii.Error, ValueError):
        return None, f"{context} contains invalid base64 data."


def _data_url_mime_type(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None
    metadata = value.split(",", 1)[0][5:]
    mime_type = metadata.split(";", 1)[0].strip().lower()
    return mime_type or None


def _path_mime_type(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    guessed, _ = mimetypes.guess_type(value)
    return guessed.lower() if guessed else None


def _decode_source_exports(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    sources: dict[str, Any] = {}
    errors: list[str] = []
    exports = payload.get("exports")
    if not isinstance(exports, list):
        return sources, ["Dashboard source exports are missing."]
    for index, item in enumerate(exports):
        if not isinstance(item, Mapping):
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        if filename in sources:
            errors.append(f"Duplicate source export: {filename}")
            continue
        raw, error = _decode_data_url(item.get("data_url"), f"Source export {filename}")
        if error:
            errors.append(error)
            continue
        assert raw is not None
        if filename == "audience-panel-package.zip":
            sources[filename] = raw
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"Source export {filename} is not valid UTF-8.")
            continue
        try:
            if filename.endswith(".jsonl"):
                sources[filename] = [
                    json.loads(line) for line in text.splitlines() if line.strip()
                ]
            elif filename.endswith(".json"):
                sources[filename] = json.loads(text)
            else:
                sources[filename] = text
        except json.JSONDecodeError as exc:
            errors.append(f"Source export {filename} is invalid JSON: {exc.msg}")
    return sources, errors


def _source_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item) for item in values]


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_written_value(value: Any) -> bool:
    if _nonempty(value):
        return True
    return isinstance(value, list) and any(_nonempty(item) for item in value)


def _feedback_contract_errors(
    themes: Any,
    context: str,
    *,
    responses: list[Mapping[str, Any]] | None = None,
    roster_ids: set[str] | None = None,
    finalist_ids: set[str] | None = None,
    approved: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(themes, list):
        return [f"{context} themes must be an array."]
    response_by_id = {
        str(item.get("response_id")): item for item in (responses or [])
    }
    stage_types = {
        "screening": "screening_response",
        "boundary": "boundary_response",
        "finalist": "finalist_response",
    }
    feedback_types_by_creative: dict[str, set[str]] = {}
    for index, theme in enumerate(themes):
        item_context = f"{context} theme {index}"
        if not isinstance(theme, Mapping):
            errors.append(f"{item_context} must be an object.")
            continue
        for field in (
            "stage",
            "creative_id",
            "segment_id",
            "lane",
            "feedback_type",
            "evidence_scope",
            "theme",
            "why_it_matters",
            "recommended_action",
            "source_type",
        ):
            if not _nonempty(theme.get(field)):
                errors.append(f"{item_context} missing nonempty {field}.")
        stage = str(theme.get("stage", ""))
        creative_id = str(theme.get("creative_id", ""))
        segment_id = str(theme.get("segment_id", ""))
        feedback_type = str(theme.get("feedback_type", ""))
        evidence_scope = str(theme.get("evidence_scope", ""))
        if stage not in stage_types:
            errors.append(f"{item_context} has an invalid stage.")
        if roster_ids is not None and creative_id not in roster_ids:
            errors.append(f"{item_context} references a creative outside the roster.")
        if feedback_type not in FEEDBACK_TYPES:
            errors.append(f"{item_context} has an invalid feedback_type.")
        else:
            feedback_types_by_creative.setdefault(creative_id, set()).add(feedback_type)
        if evidence_scope not in FEEDBACK_EVIDENCE_SCOPES:
            errors.append(f"{item_context} has an invalid evidence_scope.")
        limitations = theme.get("limitations")
        if not isinstance(limitations, list) or not limitations or not all(
            _nonempty(item) for item in limitations
        ):
            errors.append(f"{item_context} limitations must be a nonempty string array.")
        for field in FEEDBACK_CLAIM_FIELDS:
            error = feedback_claim_error(field, theme.get(field))
            if error:
                errors.append(f"{item_context} {error}.")
        action = str(theme.get("recommended_action", ""))
        action_error = feedback_action_error(feedback_type, action)
        if action_error:
            errors.append(f"{item_context} {action_error}.")
        source_ids = _source_ids(theme.get("response_ids"))
        if (
            not isinstance(theme.get("response_ids"), list)
            or not source_ids
            or any(not item.strip() for item in source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            errors.append(f"{item_context} response_ids must be unique nonempty IDs.")
        if len(source_ids) == 1:
            if evidence_scope != "single_source_observation":
                errors.append(
                    f"{item_context} cannot claim a cross-response pattern from one response."
                )
            visible_limit = " ".join(
                [action, *(str(item) for item in limitations or [])]
            ).lower()
            if "single-source" not in visible_limit and "single source" not in visible_limit:
                errors.append(f"{item_context} must visibly label single-source evidence.")
        elif len(source_ids) > 1 and evidence_scope != "cross_response_pattern":
            errors.append(
                f"{item_context} with multiple responses must use cross_response_pattern."
            )
        exposed = theme.get("exposed_base")
        exposed_count = exposed.get("count") if isinstance(exposed, Mapping) else None
        if (
            not isinstance(exposed, Mapping)
            or isinstance(exposed_count, bool)
            or not isinstance(exposed_count, int)
            or exposed_count < len(set(source_ids))
            or not _nonempty(exposed.get("label"))
        ):
            errors.append(f"{item_context} exposed_base is invalid.")
        if responses is not None:
            expected_type = stage_types.get(stage)
            sources_match = bool(source_ids) and all(
                response_id in response_by_id
                and response_by_id[response_id].get("record_type") == expected_type
                and creative_id
                in response_by_id[response_id].get("assigned_variation_ids", [])
                and str(response_by_id[response_id].get("segment_id", "")) == segment_id
                for response_id in source_ids
            )
            assigned_count = sum(
                1
                for response in responses
                if response.get("record_type") == expected_type
                and creative_id in response.get("assigned_variation_ids", [])
                and str(response.get("segment_id", "")) == segment_id
            )
            if not sources_match or exposed_count != assigned_count:
                errors.append(f"{item_context} provenance/base disagrees with responses.")

    if responses is not None:
        written_creatives: set[str] = set()
        friction_creatives: set[str] = set()
        for response in responses:
            for reaction in response.get("per_creative_reactions", []):
                if not isinstance(reaction, Mapping):
                    continue
                creative_id = str(reaction.get("variation_id", ""))
                if any(
                    _has_written_value(reaction.get(field))
                    for field in (
                        "immediate_reaction",
                        "noticed_or_understood_first",
                        "strongest_positive_signal",
                        "strongest_negative_signal",
                    )
                ):
                    written_creatives.add(creative_id)
                if _has_written_value(
                    reaction.get("strongest_negative_signal")
                ) or reaction.get("judgment_status") == "unable_to_judge":
                    friction_creatives.add(creative_id)
            for review in response.get("finalist_reviews", []):
                if not isinstance(review, Mapping):
                    continue
                creative_id = str(review.get("variation_id", ""))
                if _has_written_value(review.get("immediate_reaction")) or _has_written_value(
                    review.get("feedback")
                ):
                    written_creatives.add(creative_id)
                if str(review.get("feedback_type", "")).lower() in {
                    "negative",
                    "friction",
                    "disagreement",
                }:
                    friction_creatives.add(creative_id)
            comparison = response.get("comparative_choice")
            if isinstance(comparison, Mapping):
                weakest = str(comparison.get("weakest_variation_id", ""))
                if weakest and _has_written_value(comparison.get("weakest_reason")):
                    friction_creatives.add(weakest)
            pairwise = response.get("pairwise_choice")
            if isinstance(pairwise, Mapping) and pairwise.get("status") in {
                "tie",
                "no_meaningful_difference",
                "unable_to_judge",
            }:
                friction_creatives.update(
                    str(item) for item in response.get("assigned_variation_ids", [])
                )
        uncovered = sorted(
            creative_id
            for creative_id in written_creatives
            if not feedback_types_by_creative.get(creative_id, set())
            & {"strength", "friction"}
        )
        if uncovered:
            errors.append(
                f"{context} coverage gap for creatives with written reactions: "
                + ", ".join(uncovered)
            )
        if approved:
            for creative_id in finalist_ids or set():
                observed = feedback_types_by_creative.get(creative_id, set())
                missing = sorted({"strength", "next_test"} - observed)
                if creative_id in friction_creatives and not observed & {
                    "friction",
                    "disagreement",
                }:
                    missing.append("friction or disagreement")
                if missing:
                    errors.append(
                        f"{context} coverage gap for approved top ad {creative_id}: "
                        + ", ".join(missing)
                    )
    return errors


def _rendered_feedback_errors(feedback: Any) -> list[str]:
    if not isinstance(feedback, list):
        return ["Rendered payload feedback must be an array."]
    canonical: list[Any] = []
    for item in feedback:
        if not isinstance(item, Mapping):
            canonical.append(item)
            continue
        creative = item.get("creative")
        normalized = dict(item)
        normalized["creative_id"] = (
            str(creative.get("variation_id", ""))
            if isinstance(creative, Mapping)
            else ""
        )
        canonical.append(normalized)
    return _feedback_contract_errors(canonical, "Rendered payload feedback")


def _export_bytes(payload: Mapping[str, Any]) -> tuple[dict[str, bytes], list[str]]:
    decoded: dict[str, bytes] = {}
    errors: list[str] = []
    exports = payload.get("exports")
    if not isinstance(exports, list):
        return decoded, ["Dashboard source exports are missing."]
    for item in exports:
        if not isinstance(item, Mapping):
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        raw, error = _decode_data_url(item.get("data_url"), f"Source export {filename}")
        if error:
            errors.append(error)
        elif raw is not None:
            decoded[filename] = raw
    return decoded, errors


def _validate_lineage_sources(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    responses: list[Mapping[str, Any]],
    sources: Mapping[str, Any],
) -> list[str]:
    """Independently verify bound call lineage embedded in dashboard downloads."""

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping) or not any(
        name in outputs for name in CANONICAL_LINEAGE_FILES
    ):
        return []
    raw_bytes, byte_errors = _export_bytes(payload)
    if byte_errors:
        return byte_errors
    records_by_filename: dict[str, list[Mapping[str, Any]]] = {
        "panelist-responses.jsonl": responses
    }
    for filename in CANONICAL_LINEAGE_FILES.values():
        if filename == "panelist-responses.jsonl":
            continue
        records = sources.get(filename)
        if not isinstance(records, list):
            return [f"Dashboard source export is missing bound lineage: {filename}"]
        records_by_filename[filename] = records
    try:
        validate_bound_lineage(manifest, records_by_filename, raw_bytes)
    except ValueError as exc:
        return [str(exc)]
    return []

    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        return []
    canonical = {
        "accepted_responses": "panelist-responses.jsonl",
        "raw_provider_returns": "raw-provider-returns.jsonl",
        "rejected_attempts": "rejected-attempts.jsonl",
    }
    present = {name for name in canonical if name in outputs}
    if not present:
        return []
    errors: list[str] = []
    if present != set(canonical):
        return [
            "Dashboard source manifest must bind accepted responses, raw provider "
            "returns, and rejected attempts together."
        ]
    raw_bytes, byte_errors = _export_bytes(payload)
    errors.extend(byte_errors)
    for name, filename in canonical.items():
        binding = outputs.get(name)
        records = responses if name == "accepted_responses" else sources.get(filename)
        if not isinstance(binding, Mapping) or binding.get("path") != filename:
            errors.append(f"Dashboard source manifest outputs.{name}.path is invalid.")
            continue
        if not isinstance(records, list):
            errors.append(f"Dashboard source export is missing bound lineage: {filename}")
            continue
        content = raw_bytes.get(filename)
        actual_hash = (
            "sha256:" + hashlib.sha256(content).hexdigest()
            if content is not None
            else None
        )
        if binding.get("content_hash") != actual_hash:
            errors.append(f"Dashboard source manifest hash is false for {filename}.")
        if binding.get("record_count") != len(records):
            errors.append(f"Dashboard source manifest record_count is false for {filename}.")

    raw_records = sources.get("raw-provider-returns.jsonl")
    rejected_records = sources.get("rejected-attempts.jsonl")
    if not isinstance(raw_records, list) or not isinstance(rejected_records, list):
        return errors
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            errors.append("Raw provider return lineage contains a non-object record.")
            continue
        provider_id = raw.get("provider_return_id")
        if not isinstance(provider_id, str) or not provider_id or provider_id in raw_by_id:
            errors.append("Raw provider return IDs must be unique non-empty strings.")
            continue
        validation_errors = raw.get("validation_errors")
        if (
            not isinstance(raw.get("accepted"), bool)
            or not isinstance(validation_errors, list)
            or raw["accepted"] == bool(validation_errors)
        ):
            errors.append(
                f"Raw provider return {provider_id} acceptance and validation errors disagree."
            )
        raw_by_id[provider_id] = raw

    rejected_by_id: dict[str, Mapping[str, Any]] = {}
    for rejected in rejected_records:
        if not isinstance(rejected, Mapping):
            errors.append("Rejected-attempt lineage contains a non-object record.")
            continue
        provider_id = rejected.get("provider_return_id")
        raw = raw_by_id.get(str(provider_id))
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or provider_id in rejected_by_id
            or raw is None
            or raw.get("accepted") is not False
        ):
            errors.append("Rejected attempts must identify unique rejected raw returns.")
            continue
        if any(
            rejected.get(field) != raw.get(field)
            for field in (
                "synthetic_replicate_id",
                "reviewer_dispatch_id",
                "stage",
                "position_seen",
                "attempt_number",
                "validation_errors",
            )
        ):
            errors.append(f"Rejected attempt {provider_id} disagrees with its raw return.")
        rejected_by_id[provider_id] = rejected
    if set(rejected_by_id) != {
        provider_id for provider_id, raw in raw_by_id.items() if raw.get("accepted") is False
    }:
        errors.append("Rejected-attempt lineage does not exactly cover rejected raw returns.")

    referenced: set[str] = set()
    for response in responses:
        attempts = response.get("runtime_attempts")
        if not isinstance(attempts, list):
            errors.append("Accepted response runtime_attempts must be an array.")
            continue
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                errors.append("Accepted response runtime attempt must be an object.")
                continue
            provider_id = attempt.get("provider_return_id")
            raw = raw_by_id.get(str(provider_id))
            expected_outcome = (
                "accepted" if isinstance(raw, Mapping) and raw.get("accepted") else "rejected"
            )
            if (
                not isinstance(provider_id, str)
                or raw is None
                or raw.get("synthetic_replicate_id") != response.get("synthetic_replicate_id")
                or raw.get("reviewer_dispatch_id") != response.get("reviewer_dispatch_id")
                or raw.get("stage") != attempt.get("stage")
                or raw.get("position_seen")
                != (attempt.get("position_seen") if attempt.get("stage") == "reaction" else None)
                or raw.get("attempt_number") != attempt.get("attempt_number")
                or raw.get("validation_errors") != attempt.get("validation_errors")
                or attempt.get("outcome") != expected_outcome
            ):
                errors.append(
                    f"Accepted response runtime attempt {provider_id} disagrees with raw lineage."
                )
            else:
                referenced.add(provider_id)
    if referenced != set(raw_by_id):
        errors.append("Raw provider returns do not exactly cover accepted response attempts.")

    usage = manifest.get("usage")
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
    if not isinstance(usage, Mapping):
        errors.append("Lineage-bound source manifest has no usage accounting.")
    else:
        for field, expected in expected_usage.items():
            if usage.get(field) != expected:
                errors.append(
                    f"Dashboard source manifest usage.{field} does not match bound lineage."
                )
        planned = usage.get("unique_job_slots_planned")
        if (
            isinstance(planned, bool)
            or not isinstance(planned, int)
            or planned < expected_usage["unique_job_slots_dispatched"]
        ):
            errors.append(
                "Dashboard source manifest planned job slots do not cover dispatched slots."
            )
    return errors


def _validate_source_export_integrity(
    payload: Mapping[str, Any], sources: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    required = (
        "study-manifest.json",
        "creative-roster.json",
        "panelist-responses.jsonl",
        "screening-model-results.json",
        "finalist-results.json",
        "feedback-synthesis.json",
    )
    missing = [filename for filename in required if filename not in sources]
    if missing:
        return ["Dashboard is missing source export(s): " + ", ".join(missing)]
    manifest = sources["study-manifest.json"]
    roster = sources["creative-roster.json"]
    responses = sources["panelist-responses.jsonl"]
    screening = sources["screening-model-results.json"]
    finalists = sources["finalist-results.json"]
    feedback = sources["feedback-synthesis.json"]
    if not all(isinstance(item, Mapping) for item in (manifest, roster, screening, finalists, feedback)):
        return ["Dashboard source exports must contain JSON objects."]
    if not isinstance(responses, list) or not all(isinstance(item, Mapping) for item in responses):
        return ["panelist-responses.jsonl source export must contain JSON objects."]
    errors.extend(_validate_lineage_sources(payload, manifest, responses, sources))
    errors.extend(_validate_audience_export_integrity(payload, manifest, responses))

    study_id = manifest.get("study_id")
    for filename, source in (
        ("creative-roster.json", roster),
        ("screening-model-results.json", screening),
        ("finalist-results.json", finalists),
        ("feedback-synthesis.json", feedback),
    ):
        if source.get("study_id") != study_id:
            errors.append(f"Dashboard source export cross-file study_id mismatch: {filename}")
    if any(response.get("study_id") != study_id for response in responses):
        errors.append("Dashboard source export cross-file study_id mismatch: panelist responses")

    raw_creatives = roster.get("creatives")
    if not isinstance(raw_creatives, list):
        errors.append("creative-roster.json source export has no creative roster.")
        raw_creatives = []
    roster_ids = [
        str(item.get("variation_id", ""))
        for item in raw_creatives
        if isinstance(item, Mapping)
    ]
    roster_set = set(roster_ids)
    for field in ("utilities", "top_k_inclusion_frequencies", "classifications"):
        values = screening.get(field)
        if not isinstance(values, Mapping) or set(map(str, values)) != roster_set:
            errors.append(
                f"Dashboard source export cross-file {field} keys do not match the roster."
            )
    ranked_ids = _source_ids(screening.get("ranked_ids"))
    if len(ranked_ids) != len(roster_ids) or set(ranked_ids) != roster_set:
        errors.append("Dashboard source export cross-file ranked_ids is not a roster permutation.")
    if manifest.get("validity_status") != screening.get("validity_status"):
        errors.append("Dashboard source export cross-file validity_status mismatch.")

    finalist_ids = _source_ids(finalists.get("approved_finalist_ids"))
    requested_size = manifest.get("requested_shortlist_size")
    if (
        isinstance(requested_size, bool)
        or not isinstance(requested_size, int)
        or len(finalist_ids) != requested_size
        or len(set(finalist_ids)) != len(finalist_ids)
        or not set(finalist_ids).issubset(roster_set)
    ):
        errors.append("Dashboard source export cross-file finalist roster is invalid.")
    finalist_set = set(finalist_ids)
    decision = (
        finalists.get("roster_decision")
        if isinstance(finalists.get("roster_decision"), Mapping)
        else {}
    )
    decision_status = decision.get("status")
    approved = decision_status in APPROVED_ROSTER_STATES
    override = decision.get("override", False)
    if decision_status not in APPROVED_ROSTER_STATES | {"awaiting_approval"}:
        errors.append("Dashboard source export roster decision status is invalid.")
    if not isinstance(override, bool) or (
        (decision_status == "approved_with_override") != (override is True)
    ):
        errors.append(
            "Dashboard source export approved_with_override status and override flag disagree."
        )
    if approved and _aware_datetime(decision.get("approved_at")) is None:
        errors.append(
            "Dashboard source export approved roster requires a timezone-aware approved_at."
        )
    for response in responses:
        if response.get("record_type") != "finalist_response":
            continue
        review_ids = [
            str(item.get("variation_id", ""))
            for item in response.get("finalist_reviews", [])
            if isinstance(item, Mapping)
        ]
        for values in (
            _source_ids(response.get("assigned_variation_ids")),
            _source_ids(response.get("shown_order")),
            review_ids,
            _source_ids(response.get("final_preference_ranking")),
        ):
            if len(values) != len(finalist_ids) or set(values) != finalist_set:
                errors.append(
                    "Dashboard source export cross-file finalist response does not match the roster."
                )
                break

    accepted_records = finalists.get("accepted_response_records")
    accepted_replicates = finalists.get("accepted_unique_replicates")
    job_slots = finalists.get("unique_job_slots_consumed")
    finalist_model_calls = finalists.get("total_model_calls")
    counts = finalists.get("first_choice_counts")
    shares = finalists.get("conditional_first_choice_share")
    if approved:
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
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in (
                    accepted_records,
                    accepted_replicates,
                    job_slots,
                    finalist_model_calls,
                )
            )
            or accepted_records != len(finalist_responses)
            or accepted_replicates != len(actual_replicates)
            or job_slots != len(actual_slots)
            or (actual_calls > 0 and finalist_model_calls != actual_calls)
            or (actual_calls == 0 and finalist_model_calls < accepted_records)
            or not isinstance(counts, Mapping)
            or set(map(str, counts)) != finalist_set
            or not isinstance(shares, Mapping)
            or set(map(str, shares)) != finalist_set
        ):
            errors.append("Dashboard source export cross-file finalist metrics are malformed.")
        else:
            try:
                count_values = [counts[item] for item in finalist_ids]
                share_values = [float(shares[item]) for item in finalist_ids]
                valid_counts = all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in count_values
                )
                valid_shares = all(math.isfinite(item) for item in share_values)
                derived = all(
                    math.isclose(
                        share_values[index],
                        count_values[index] / accepted_records,
                        abs_tol=1e-9,
                    )
                    for index in range(len(finalist_ids))
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                valid_counts = valid_shares = derived = False
                count_values = []
                share_values = []
            if (
                not valid_counts
                or not valid_shares
                or sum(count_values) != accepted_records
                or not math.isclose(sum(share_values), 1.0, abs_tol=1e-9)
                or not derived
            ):
                errors.append(
                    "Dashboard source export cross-file finalist counts/shares disagree."
                )

    themes = feedback.get("themes")
    errors.extend(
        _feedback_contract_errors(
            themes,
            "Dashboard source export feedback",
            responses=responses,
            roster_ids=roster_set,
            finalist_ids=finalist_set,
            approved=approved,
        )
    )

    usage = manifest.get("usage")
    total_model_calls = None
    if isinstance(usage, Mapping) and "total_model_calls" in usage:
        total_model_calls = usage.get("total_model_calls")
        if (
            isinstance(total_model_calls, bool)
            or not isinstance(total_model_calls, int)
            or total_model_calls < len(responses)
        ):
            errors.append(
                "Dashboard source export total_model_calls must be a non-negative integer "
                "at least as large as accepted response records."
            )
    audience = manifest.get("audience_lock")
    if isinstance(audience, Mapping):
        for field in ("unique_archetypes", "unique_grounded_context_profiles"):
            value = audience.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(
                    f"Dashboard source export audience_lock.{field} must be a "
                    "non-negative integer."
                )
        observed_archetypes = {
            str(item.get("persona_archetype_id"))
            for item in responses
            if isinstance(item.get("persona_archetype_id"), str)
            and item.get("persona_archetype_id", "").strip()
        }
        declared_archetypes = audience.get("unique_archetypes")
        if (
            isinstance(declared_archetypes, int)
            and not isinstance(declared_archetypes, bool)
            and declared_archetypes < len(observed_archetypes)
        ):
            errors.append(
                "Dashboard source export unique_archetypes is smaller than observed "
                "accepted-response archetypes."
            )

    creative_format = manifest.get("creative_format")
    if creative_format not in {"copy_only", "static_image", "carousel", "video_representation"}:
        errors.append("Dashboard source export uses a noncanonical creative_format.")
    raw_media: dict[str, tuple[str, str]] = {}
    for creative in raw_creatives:
        if not isinstance(creative, Mapping):
            continue
        creative_id = str(creative.get("variation_id", ""))
        media_items = creative.get("media", [])
        if not isinstance(media_items, list):
            continue
        for medium in media_items:
            if not isinstance(medium, Mapping):
                continue
            representation_id = str(medium.get("representation_id", ""))
            content_hash = str(medium.get("content_hash", ""))
            if not representation_id or not content_hash or representation_id in raw_media:
                errors.append(
                    "Dashboard source export media representations require unique IDs and hashes."
                )
                continue
            if _path_mime_type(medium.get("path")) not in RENDERABLE_IMAGE_MIME_TYPES:
                errors.append(
                    f"Dashboard source export media representation {representation_id} "
                    "does not use a renderable image MIME type."
                )
            raw_media[representation_id] = (creative_id, content_hash)
    saliency = sources.get("saliency-index.json")
    if creative_format == "copy_only" and saliency is not None:
        errors.append("Dashboard source export copy-only study includes saliency evidence.")
    if creative_format != "copy_only":
        if not isinstance(saliency, Mapping):
            errors.append("Dashboard source export imagery study lacks saliency evidence.")
        else:
            provider = saliency.get("provider")
            method = saliency.get("method")
            revealed_at = _aware_datetime(saliency.get("revealed_at"))
            decision = finalists.get("roster_decision")
            approved_at = (
                _aware_datetime(decision.get("approved_at"))
                if isinstance(decision, Mapping)
                else None
            )
            if not isinstance(provider, str) or not provider.strip():
                errors.append("Dashboard source export saliency provider is missing.")
            if not isinstance(method, str) or not method.strip():
                errors.append("Dashboard source export saliency method is missing.")
            if approved_at is None or revealed_at is None or approved_at >= revealed_at:
                errors.append("Dashboard source export saliency approval/reveal order is invalid.")
            saliency_entries = saliency.get("entries")
            seen: set[str] = set()
            if not isinstance(saliency_entries, list):
                errors.append("Dashboard source export saliency entries are missing.")
                saliency_entries = []
            for index, entry in enumerate(saliency_entries):
                if not isinstance(entry, Mapping):
                    errors.append(f"Dashboard source export saliency entry {index} is malformed.")
                    continue
                representation_id = str(entry.get("representation_id", ""))
                source = raw_media.get(representation_id)
                if representation_id in seen:
                    errors.append(
                        f"Dashboard source export duplicate saliency representation: {representation_id}"
                    )
                seen.add(representation_id)
                if source is None or (
                    str(entry.get("variation_id", "")) != source[0]
                    or str(entry.get("content_hash", "")) != source[1]
                ):
                    errors.append(
                        f"Dashboard source export saliency representation {representation_id} is unbound."
                    )
                overlay_content_hash = entry.get("overlay_content_hash")
                if (
                    not isinstance(overlay_content_hash, str)
                    or not overlay_content_hash.strip()
                ):
                    errors.append(
                        f"Dashboard source export saliency entry {index} "
                        "overlay_content_hash is missing."
                    )
                for path_field in ("original_path", "overlay_path"):
                    if _path_mime_type(entry.get(path_field)) not in RENDERABLE_IMAGE_MIME_TYPES:
                        errors.append(
                            f"Dashboard source export saliency entry {index} {path_field} "
                            "does not use a renderable image MIME type."
                        )
                target_time = _aware_datetime(entry.get("target_declared_at"))
                limitations = entry.get("limitations")
                if (
                    not isinstance(entry.get("predeclared_target"), str)
                    or not entry.get("predeclared_target", "").strip()
                    or target_time is None
                    or revealed_at is None
                    or target_time >= revealed_at
                    or entry.get("categorical_alignment")
                    not in {"aligned", "partially_aligned", "misaligned", "unclear"}
                    or entry.get("provider") != provider
                    or not isinstance(limitations, list)
                    or not limitations
                    or not all(isinstance(item, str) and item.strip() for item in limitations)
                ):
                    errors.append(
                        f"Dashboard source export saliency entry {index} provenance is incomplete."
                    )
            if seen != set(raw_media):
                errors.append(
                    "Dashboard source export saliency coverage does not match media representations."
                )

    study = payload.get("study") if isinstance(payload.get("study"), Mapping) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if (
        study.get("study_id") != study_id
        or study.get("method") != manifest.get("method")
        or study.get("creative_format_id") != manifest.get("creative_format")
        or summary.get("validity_status") != screening.get("validity_status")
    ):
        errors.append("Rendered payload does not match its source export study truth.")
    payload_creatives = (
        payload.get("creatives") if isinstance(payload.get("creatives"), list) else []
    )
    payload_ids = [
        str(item.get("variation_id", ""))
        for item in payload_creatives
        if isinstance(item, Mapping)
    ]
    if payload_ids != roster_ids:
        errors.append("Rendered payload creative roster does not match its source export.")
    creative_names = {
        str(item.get("variation_id", item.get("creative_id", ""))): str(
            item.get(
                "display_name",
                item.get("name", item.get("variation_id", item.get("creative_id", ""))),
            )
        )
        for item in raw_creatives
        if isinstance(item, Mapping)
    }
    segment_names = (
        {
            str(item_id): str(name)
            for item_id, name in audience.get("segment_names", {}).items()
            if _nonempty(str(item_id)) and _nonempty(name)
        }
        if isinstance(audience, Mapping)
        and isinstance(audience.get("segment_names"), Mapping)
        else {}
    )
    expected_feedback: list[dict[str, Any]] = []
    if isinstance(themes, list):
        for theme in themes:
            if not isinstance(theme, Mapping):
                continue
            creative_id = str(theme.get("creative_id", ""))
            segment_id = str(theme.get("segment_id", ""))
            exposed = theme.get("exposed_base")
            expected_feedback.append(
                {
                    "stage": str(theme.get("stage", "not recorded")),
                    "creative": {
                        "variation_id": creative_id,
                        "display_name": creative_names.get(creative_id, creative_id),
                    },
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
                        "count": exposed.get("count") if isinstance(exposed, Mapping) else None,
                        "label": str(exposed.get("label", ""))
                        if isinstance(exposed, Mapping)
                        else "",
                    },
                    "limitations": [str(item) for item in theme.get("limitations", [])],
                }
            )
    if payload.get("feedback") != expected_feedback:
        errors.append(
            "Rendered payload feedback does not exactly match feedback-synthesis.json."
        )
    if len(payload_creatives) != len(raw_creatives):
        errors.append(
            "Rendered creative records do not match the creative-roster.json source export."
        )
    for index, source_creative in enumerate(raw_creatives):
        rendered_creative = (
            payload_creatives[index] if index < len(payload_creatives) else None
        )
        if not isinstance(source_creative, Mapping) or not isinstance(
            rendered_creative, Mapping
        ):
            errors.append(
                f"Rendered creative {index} does not match the creative-roster.json "
                "source export."
            )
            continue
        creative_id = source_creative.get(
            "variation_id", source_creative.get("creative_id")
        )
        display_name = source_creative.get(
            "display_name", source_creative.get("name", creative_id)
        )
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = creative_id
        expected_fields = {
            "variation_id": creative_id,
            "display_name": display_name,
            "format": str(source_creative.get("format", "not specified")),
            "headline": str(source_creative.get("headline", "")),
            "body": str(
                source_creative.get("body", source_creative.get("copy", ""))
            ),
            "cta": str(source_creative.get("cta", "")),
            "visual_description": str(
                source_creative.get("visual_description", "")
            ),
            "input_fidelity": str(
                source_creative.get("input_fidelity", "not recorded")
            ),
        }
        for field, expected in expected_fields.items():
            if rendered_creative.get(field) != expected:
                errors.append(
                    f"Rendered creative {creative_id} {field} does not match the "
                    "creative-roster.json source export."
                )

        source_media = source_creative.get("media", [])
        rendered_media = rendered_creative.get("media", [])
        if not isinstance(source_media, list):
            source_media = []
        if not isinstance(rendered_media, list):
            rendered_media = []
        if len(rendered_media) != len(source_media):
            errors.append(
                f"Rendered creative {creative_id} media count does not match the "
                "creative-roster.json source export."
            )
        for media_index, source_medium in enumerate(source_media):
            rendered_medium = (
                rendered_media[media_index]
                if media_index < len(rendered_media)
                else None
            )
            if not isinstance(source_medium, Mapping) or not isinstance(
                rendered_medium, Mapping
            ):
                errors.append(
                    f"Rendered creative {creative_id} media {media_index} does not "
                    "match the creative-roster.json source export."
                )
                continue
            representation_id = str(source_medium.get("representation_id", ""))
            expected_hash = str(source_medium.get("content_hash", ""))
            expected_mime = _path_mime_type(source_medium.get("path"))
            expected_media_fields = {
                "representation_id": representation_id,
                "content_hash": expected_hash,
                "kind": str(source_medium.get("kind", "image")),
                "label": str(
                    source_medium.get(
                        "label", "Supplied creative representation"
                    )
                ),
                "alt": str(
                    source_medium.get(
                        "alt", f"Supplied representation for {display_name}"
                    )
                ),
                "mime_type": expected_mime,
            }
            for field, expected in expected_media_fields.items():
                if rendered_medium.get(field) != expected:
                    errors.append(
                        f"Rendered media representation {representation_id} {field} "
                        "does not match the creative-roster.json source export."
                    )
            rendered_bytes, media_error = _decode_data_url(
                rendered_medium.get("data_url"),
                f"Rendered media representation {representation_id}",
            )
            if media_error is None and rendered_bytes is not None:
                actual_hash = f"sha256:{hashlib.sha256(rendered_bytes).hexdigest()}"
                if actual_hash != expected_hash:
                    errors.append(
                        f"Rendered media representation {representation_id} bytes do "
                        "not match the creative-roster.json source export."
                    )
                if rendered_medium.get("byte_count") != len(rendered_bytes):
                    errors.append(
                        f"Rendered media representation {representation_id} byte_count "
                        "does not match its source export-backed bytes."
                    )
                if rendered_medium.get("availability") != "embedded":
                    errors.append(
                        f"Rendered media representation {representation_id} availability "
                        "does not match its source export-backed bytes."
                    )
                if _data_url_mime_type(rendered_medium.get("data_url")) != expected_mime:
                    errors.append(
                        f"Rendered media representation {representation_id} MIME does "
                        "not match the creative-roster.json source export."
                    )
            elif rendered_medium.get("data_url") is None:
                if rendered_medium.get("availability") != (
                    "not embedded because the local file exceeds 20 MB"
                ):
                    errors.append(
                        f"Rendered media representation {representation_id} availability "
                        "does not match compiler output."
                    )
            else:
                errors.append(
                    f"Rendered media representation {representation_id} bytes cannot be "
                    "verified against the creative-roster.json source export."
                )
    source_media_tuples = {
        (creative_id, representation_id, content_hash)
        for representation_id, (creative_id, content_hash) in raw_media.items()
    }
    payload_media_tuples = {
        (
            str(creative.get("variation_id", "")),
            str(medium.get("representation_id", "")),
            str(medium.get("content_hash", "")),
        )
        for creative in payload_creatives
        if isinstance(creative, Mapping)
        for medium in (
            creative.get("media", []) if isinstance(creative.get("media"), list) else []
        )
        if isinstance(medium, Mapping)
    }
    if payload_media_tuples != source_media_tuples:
        errors.append(
            "Rendered payload media representation tuples do not match source exports."
        )

    source_stage_names = {
        "screening_response": "screening",
        "boundary_response": "boundary",
        "finalist_response": "finalist",
    }
    accepted_records_by_stage = Counter(
        source_stage_names[str(response.get("record_type"))]
        for response in responses
        if str(response.get("record_type")) in source_stage_names
    )
    accepted_replicates_by_stage: dict[str, set[str]] = {}
    for response in responses:
        stage = source_stage_names.get(str(response.get("record_type")))
        if stage is None:
            continue
        accepted_replicates_by_stage.setdefault(stage, set()).add(
            str(response.get("synthetic_replicate_id", ""))
        )
    all_accepted_replicates = {
        str(response.get("synthetic_replicate_id", "")) for response in responses
    }
    accepted_context_strata = {
        str(response.get("context_stratum_id"))
        for response in responses
        if response.get("context_stratum_id") is not None
    }
    payload_denominators = (
        summary.get("denominators")
        if isinstance(summary.get("denominators"), Mapping)
        else {}
    )
    expected_denominators = {
        "total_model_calls": total_model_calls,
        "accepted_response_records": len(responses),
        "accepted_unique_replicates": len(all_accepted_replicates),
        "unique_archetypes": (
            audience.get("unique_archetypes") if isinstance(audience, Mapping) else None
        ),
        "grounded_context_profiles": (
            audience.get("unique_grounded_context_profiles")
            if isinstance(audience, Mapping)
            else None
        ),
        "accepted_context_strata": len(accepted_context_strata),
    }
    if payload_denominators != expected_denominators:
        errors.append("Rendered payload denominators do not match their source exports.")
    if summary.get("accepted_response_records_by_stage") != dict(
        sorted(accepted_records_by_stage.items())
    ):
        errors.append(
            "Rendered accepted response records by stage do not match source exports."
        )
    expected_replicates_by_stage = {
        stage: len(replicates)
        for stage, replicates in sorted(accepted_replicates_by_stage.items())
    }
    if summary.get("accepted_unique_replicates_by_stage") != expected_replicates_by_stage:
        errors.append(
            "Rendered accepted synthetic replicates by stage do not match source exports."
        )
    payload_screening = payload.get("screening") if isinstance(payload.get("screening"), Mapping) else {}
    payload_rows = payload_screening.get("rows") if isinstance(payload_screening.get("rows"), list) else []
    if [str(item.get("variation_id", "")) for item in payload_rows if isinstance(item, Mapping)] != ranked_ids:
        errors.append("Rendered payload screening rank does not match its source export.")
    for row in payload_rows:
        if not isinstance(row, Mapping):
            continue
        creative_id = str(row.get("variation_id", ""))
        if (
            row.get("utility") != screening.get("utilities", {}).get(creative_id)
            or row.get("stability") != screening.get("top_k_inclusion_frequencies", {}).get(creative_id)
            or row.get("classification") != screening.get("classifications", {}).get(creative_id)
        ):
            errors.append("Rendered payload screening metrics do not match source exports.")
            break
    payload_finalists = payload.get("finalists") if isinstance(payload.get("finalists"), Mapping) else {}
    rendered_refs = payload_finalists.get("approved_finalists" if approved else "pending_finalists")
    rendered_ids = [
        str(item.get("variation_id", ""))
        for item in rendered_refs
        if isinstance(item, Mapping)
    ] if isinstance(rendered_refs, list) else []
    if rendered_ids != finalist_ids:
        errors.append("Rendered payload finalist roster does not match its source export.")
    if approved:
        if (
            payload_finalists.get("conditional_first_choice_share") != shares
            or payload_finalists.get("first_choice_counts") != counts
            or payload_finalists.get("accepted_response_records") != accepted_records
            or payload_finalists.get("accepted_unique_replicates") != accepted_replicates
            or payload_finalists.get("unique_job_slots_consumed") != job_slots
            or payload_finalists.get("total_model_calls") != finalist_model_calls
        ):
            errors.append("Rendered payload finalist metrics do not match source exports.")
    elif (
        payload_finalists.get("metrics_available") is not False
        or payload_finalists.get("conditional_first_choice_share")
        or payload_finalists.get("first_choice_counts")
    ):
        errors.append("Rendered payload exposes finalist metrics before approval.")
    methodology = payload.get("methodology") if isinstance(payload.get("methodology"), Mapping) else {}
    if methodology.get("interpretation_limits") != screening.get("interpretation_limits"):
        errors.append("Rendered methodology limits do not match the source export.")
    diagnostics = (
        screening.get("model_diagnostics")
        if isinstance(screening.get("model_diagnostics"), Mapping)
        else {}
    )
    connected = diagnostics.get("connected")
    if not isinstance(connected, bool):
        errors.append("Dashboard source export comparison graph diagnostic is missing.")
    else:
        integrity = methodology.get("run_integrity")
        design = next(
            (
                item
                for item in integrity
                if isinstance(item, Mapping)
                and item.get("dimension") == "Design adequacy"
            ),
            None,
        ) if isinstance(integrity, list) else None
        expected_statement = (
            "comparison graph was connected"
            if connected
            else "comparison graph was disconnected"
        )
        opposite_statement = (
            "comparison graph was disconnected"
            if connected
            else "comparison graph was connected"
        )
        overview = str(design.get("overview", "")).lower() if isinstance(design, Mapping) else ""
        if expected_statement not in overview or opposite_statement in overview:
            errors.append(
                "Rendered comparison graph statement does not match exported diagnostics."
            )
    payload_visual = (
        payload.get("visual_evidence")
        if isinstance(payload.get("visual_evidence"), Mapping)
        else None
    )
    if isinstance(saliency, Mapping):
        if payload_visual is None:
            errors.append(
                "Rendered saliency evidence does not match saliency-index.json source export."
            )
        else:
            source_decision = (
                finalists.get("roster_decision")
                if isinstance(finalists.get("roster_decision"), Mapping)
                else {}
            )
            expected_override_status = (
                "saliency-informed human override"
                if source_decision.get("changed_after_saliency_reveal") is True
                else "no post-reveal roster change"
            )
            source_top_fields = {
                "provider": saliency.get("provider"),
                "method": saliency.get("method"),
                "approval_status": str(source_decision.get("status")),
                "override_status": expected_override_status,
            }
            for field, expected in source_top_fields.items():
                if payload_visual.get(field) != expected:
                    errors.append(
                        f"Rendered saliency {field} does not match its source export."
                    )
            if _aware_datetime(payload_visual.get("approved_at")) != _aware_datetime(
                source_decision.get("approved_at")
            ):
                errors.append(
                    "Rendered saliency approved_at does not match its source export."
                )
            if _aware_datetime(payload_visual.get("revealed_at")) != _aware_datetime(
                saliency.get("revealed_at")
            ):
                errors.append(
                    "Rendered saliency revealed_at does not match its source export."
                )

            source_entries = (
                saliency.get("entries")
                if isinstance(saliency.get("entries"), list)
                else []
            )
            rendered_entries = (
                payload_visual.get("entries")
                if isinstance(payload_visual.get("entries"), list)
                else []
            )
            if len(rendered_entries) != len(source_entries):
                errors.append(
                    "Rendered saliency entry count does not match its source export."
                )
            rendered_by_representation = {
                str(entry.get("representation_id", "")): entry
                for entry in rendered_entries
                if isinstance(entry, Mapping)
            }
            source_creative_names: dict[str, Any] = {}
            for creative in raw_creatives:
                if not isinstance(creative, Mapping):
                    continue
                creative_id = creative.get(
                    "variation_id", creative.get("creative_id")
                )
                display_name = creative.get(
                    "display_name", creative.get("name", creative_id)
                )
                if not isinstance(display_name, str) or not display_name.strip():
                    display_name = creative_id
                source_creative_names[str(creative_id)] = display_name

            for index, source_entry in enumerate(source_entries):
                if not isinstance(source_entry, Mapping):
                    continue
                representation_id = str(
                    source_entry.get("representation_id", "")
                )
                rendered_entry = rendered_by_representation.get(representation_id)
                if not isinstance(rendered_entry, Mapping):
                    errors.append(
                        f"Rendered saliency representation {representation_id} is "
                        "missing from its source export binding."
                    )
                    continue
                creative_id = str(source_entry.get("variation_id", ""))
                expected_fields = {
                    "variation_id": creative_id,
                    "display_name": source_creative_names.get(
                        creative_id, creative_id
                    ),
                    "representation_id": representation_id,
                    "content_hash": source_entry.get("content_hash"),
                    "overlay_content_hash": source_entry.get(
                        "overlay_content_hash"
                    ),
                    "predeclared_target": source_entry.get("predeclared_target"),
                    "categorical_alignment": source_entry.get(
                        "categorical_alignment"
                    ),
                    "provider": source_entry.get("provider"),
                    "limitations": source_entry.get("limitations"),
                    "original_mime_type": _path_mime_type(
                        source_entry.get("original_path")
                    ),
                    "overlay_mime_type": _path_mime_type(
                        source_entry.get("overlay_path")
                    ),
                }
                for field, expected in expected_fields.items():
                    if rendered_entry.get(field) != expected:
                        errors.append(
                            f"Rendered saliency representation {representation_id} "
                            f"{field} does not match its source export."
                        )
                if _aware_datetime(
                    rendered_entry.get("target_declared_at")
                ) != _aware_datetime(source_entry.get("target_declared_at")):
                    errors.append(
                        f"Rendered saliency representation {representation_id} "
                        "target_declared_at does not match its source export."
                    )
                for data_field, hash_field in (
                    ("original_data_url", "content_hash"),
                    ("overlay_data_url", "overlay_content_hash"),
                ):
                    rendered_bytes, media_error = _decode_data_url(
                        rendered_entry.get(data_field),
                        f"Rendered saliency representation {representation_id} "
                        f"{data_field}",
                    )
                    expected_hash = source_entry.get(hash_field)
                    if media_error is not None or rendered_bytes is None:
                        errors.append(
                            f"Rendered saliency representation {representation_id} "
                            f"{data_field} cannot be verified against its source export."
                        )
                    elif (
                        f"sha256:{hashlib.sha256(rendered_bytes).hexdigest()}"
                        != expected_hash
                    ):
                        errors.append(
                            f"Rendered saliency representation {representation_id} "
                            f"{data_field} bytes do not match its source export."
                        )
    elif payload_visual is not None:
        errors.append(
            "Rendered saliency evidence has no saliency-index.json source export."
        )
    return errors


def _validate_audience_export_integrity(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    responses: list[Mapping[str, Any]],
) -> list[str]:
    raw_bytes, byte_errors = _export_bytes(payload)
    if byte_errors:
        return byte_errors
    binding = manifest.get("audience_package")
    audience = payload.get("audience")
    if not isinstance(binding, Mapping):
        audience_names = set(AUDIENCE_SNAPSHOT_FILES) & set(raw_bytes)
        errors = []
        if audience_names:
            errors.append("Legacy dashboard must not embed v2 audience package downloads.")
        if not isinstance(audience, Mapping) or audience.get("state") != "legacy":
            errors.append("Legacy dashboard must label audience research package unavailable.")
        return errors
    if binding.get("schema_version") == "audience-panel-package-v3":
        try:
            raw_package = raw_bytes["audience-panel-package.zip"]
            _snapshot, manifest_bytes = read_v3_archive_manifest(
                raw_package
            )
            archive_files = archive_files_v3_for_manifest(
                json.loads(manifest_bytes.decode("utf-8"))
            )
            snapshot_files = archive_files + (
                "audience-panel-package.zip",
                "audience-resolution.json",
                "audience-resolution-authority.json",
            )
        except (
            KeyError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            return [
                "Dashboard embedded v3 audience package is invalid: "
                + str(exc)
            ]
        missing = set(snapshot_files) - set(raw_bytes)
        if missing:
            return [
                "Dashboard is missing embedded v3 audience export(s): "
                + ", ".join(sorted(missing))
            ]
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                run_dir = Path(temp_dir)
                snapshot = run_dir / "audience" / "snapshot"
                snapshot.mkdir(parents=True)
                for filename in snapshot_files:
                    if filename == "audience-resolution.json":
                        (run_dir / "audience" / "resolution.json").write_bytes(
                            raw_bytes[filename]
                        )
                    elif filename == "audience-resolution-authority.json":
                        (
                            run_dir
                            / "audience"
                            / "resolution-authority.json"
                        ).write_bytes(raw_bytes[filename])
                    else:
                        (snapshot / filename).write_bytes(raw_bytes[filename])
                for filename, raw in raw_bytes.items():
                    if filename in {
                        "screening-jobs.json",
                        "finalist-jobs.json",
                        "dispatch-audit.jsonl",
                    } or re.fullmatch(
                        r"boundary-wave-\d{4,}-jobs\.json", filename
                    ):
                        (run_dir / filename).write_bytes(raw)

                audience_files = {
                    filename: raw_bytes[filename]
                    for filename in snapshot_files
                }
                brief, panel, composition, _envelope = (
                    _validated_v3_audience_package(
                        manifest,
                        audience_files,
                        run_dir / "audience" / "resolution.json",
                    )
                )

                def source_json(filename: str) -> dict[str, Any]:
                    value = json.loads(raw_bytes[filename].decode("utf-8"))
                    if not isinstance(value, dict):
                        raise DashboardInputError(
                            f"embedded {filename} must contain a JSON object"
                        )
                    return value

                screening = source_json("screening-model-results.json")
                finalists = source_json("finalist-results.json")
                boundary_source = (
                    source_json("boundary-results.json")
                    if "boundary-results.json" in raw_bytes
                    else None
                )
                allocation, _job_files = _validated_v3_run_allocation(
                    run_dir=run_dir,
                    manifest=manifest,
                    composition=composition,
                    responses=responses,
                    screening=screening,
                    boundary=boundary_source,
                    finalists=finalists,
                )
                expected = _audience_payload_from_panel(
                    brief,
                    panel,
                    responses,
                    "research_backed",
                )
                expected["run_allocation"] = allocation
        except (
            DashboardInputError,
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            return [f"Embedded v3 audience allocation validation failed: {exc}"]
        if audience != expected:
            return [
                "Rendered v3 audience allocation does not match its authenticated source exports."
            ]
        exports = payload.get("exports")
        by_name = {
            item.get("filename"): item
            for item in exports
            if isinstance(item, Mapping) and isinstance(item.get("filename"), str)
        } if isinstance(exports, list) else {}
        errors: list[str] = []
        for filename in (
            "audience-research-report.html",
            "saved-audience-panel.json",
        ):
            if by_name.get(filename, {}).get("audience") != "marketer":
                errors.append(
                    f"Audience download {filename} must be a primary marketer download."
                )
        for filename in (
            "audience-research-brief.json",
            "audience-panel-package.zip",
            "package-manifest.json",
            "audience-resolution.json",
            "audience-resolution-authority.json",
        ):
            if by_name.get(filename, {}).get("audience") != "technical":
                errors.append(
                    f"Audience JSON {filename} must stay under technical downloads."
                )
        return errors
    missing = set(AUDIENCE_SNAPSHOT_FILES) - set(raw_bytes)
    if missing:
        return [
            "Dashboard is missing embedded audience export(s): "
            + ", ".join(sorted(missing))
        ]
    audience_files = {
        filename: raw_bytes[filename] for filename in AUDIENCE_SNAPSHOT_FILES
    }
    try:
        brief, panel, state = _validated_audience_package(manifest, audience_files)
        expected = _audience_payload_from_panel(brief, panel, responses, state)
    except DashboardInputError as exc:
        return [str(exc)]
    if audience != expected:
        return [
            "Rendered Test Audience hierarchy does not match the embedded saved audience panel."
        ]
    exports = payload.get("exports")
    by_name = {
        item.get("filename"): item
        for item in exports
        if isinstance(item, Mapping) and isinstance(item.get("filename"), str)
    } if isinstance(exports, list) else {}
    errors: list[str] = []
    for filename in (
        "audience-research-report.html",
        "saved-audience-panel.json",
        "research-sources.csv",
    ):
        if by_name.get(filename, {}).get("audience") != "marketer":
            errors.append(f"Audience download {filename} must be a primary marketer download.")
    for filename in (
        "persona-research-brief.json",
        "audience-panel-package.zip",
        "package-manifest.json",
    ):
        if by_name.get(filename, {}).get("audience") != "technical":
            errors.append(f"Audience JSON {filename} must stay under technical downloads.")
    return errors


def _validate_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["Dashboard payload must be a JSON object."]
    required_shapes = {
        "study": Mapping,
        "summary": Mapping,
        "creatives": list,
        "screening": Mapping,
        "finalists": Mapping,
        "feedback": list,
        "responses": list,
        "methodology": Mapping,
        "exports": list,
    }
    for key, expected in required_shapes.items():
        if not isinstance(payload.get(key), expected):
            errors.append(f"Dashboard payload {key} has the wrong shape.")
    tier4 = payload.get("tier4_validation")
    if tier4 is not None:
        if not isinstance(tier4, Mapping):
            errors.append("Tier 4 validation payload must be an object.")
        else:
            expected = {
                "headline", "claim_text", "disclaimer", "scope", "expires_at",
                "limitations", "claim_id", "claim_sha256", "package_zip_sha256",
                "claim_status", "active_claim",
                "metric", "synthetic_binding", "qualifying_block_count",
                "segment_result", "influence_diagnostics",
                "refresh_triggers",
            }
            if set(tier4) != expected:
                errors.append("Tier 4 validation payload keys are invalid.")
            else:
                active = tier4.get("active_claim")
                status = tier4.get("claim_status")
                if (
                    not isinstance(tier4.get("headline"), str)
                    or not tier4["headline"]
                    or not isinstance(tier4.get("claim_text"), str)
                    or not tier4["claim_text"]
                    or not isinstance(tier4.get("disclaimer"), str)
                    or not tier4["disclaimer"]
                    or not isinstance(tier4.get("scope"), Mapping)
                    or not isinstance(tier4.get("metric"), Mapping)
                    or not isinstance(
                        tier4.get("synthetic_binding"), Mapping,
                    )
                    or isinstance(
                        tier4.get("qualifying_block_count"), bool,
                    )
                    or not isinstance(
                        tier4.get("qualifying_block_count"), int,
                    )
                    or tier4["qualifying_block_count"] < 0
                    or not isinstance(tier4.get("segment_result"), list)
                    or any(
                        not isinstance(item, Mapping)
                        or set(item)
                        != {"segment_id", "status", "clear_reversal"}
                        or not isinstance(item.get("segment_id"), str)
                        or item.get("status")
                        not in {"pass", "limitations", "fail"}
                        or not isinstance(item.get("clear_reversal"), bool)
                        for item in tier4.get("segment_result", [])
                    )
                    or not isinstance(
                        tier4.get("influence_diagnostics"), Mapping,
                    )
                    or set(tier4.get("influence_diagnostics", {}))
                    != {
                        "status", "maximum_block_contribution",
                        "leave_one_block", "leave_one_batch",
                    }
                    or tier4.get("influence_diagnostics", {}).get("status")
                    not in {
                        "all_leave_outs_meet_registered_point_and_raw_p_thresholds",
                        "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds",
                        "unavailable",
                    }
                    or not isinstance(
                        tier4.get("influence_diagnostics", {}).get(
                            "leave_one_block",
                        ),
                        list,
                    )
                    or not isinstance(
                        tier4.get("influence_diagnostics", {}).get(
                            "leave_one_batch",
                        ),
                        list,
                    )
                    or not isinstance(tier4.get("refresh_triggers"), list)
                    or any(
                        not isinstance(item, str)
                        for item in tier4.get("refresh_triggers", [])
                    )
                    or not isinstance(tier4.get("limitations"), list)
                    or any(
                        not isinstance(item, str)
                        for item in tier4.get("limitations", [])
                    )
                    or not isinstance(tier4.get("package_zip_sha256"), str)
                    or not tier4["package_zip_sha256"]
                    or not isinstance(active, bool)
                    or status not in {
                        "active", "expired", "superseded", "withdrawn",
                        "invalidated", "not_yet_active", "not_current",
                        "not_issued",
                    }
                ):
                    errors.append("Tier 4 validation payload is malformed.")
                elif active and (
                    status != "active"
                    or tier4.get("headline")
                    != "Held-out ordering validation"
                    or not isinstance(tier4.get("claim_id"), str)
                    or not isinstance(tier4.get("claim_sha256"), str)
                    or not isinstance(tier4.get("expires_at"), str)
                ):
                    errors.append(
                        "Active Tier 4 validation payload is malformed."
                    )
                elif status == "not_issued" and any(
                    tier4.get(key) is not None
                    for key in ("claim_id", "claim_sha256", "expires_at")
                ):
                    errors.append(
                        "Negative Tier 4 validation payload exposes claim identity."
                    )
    errors.extend(_rendered_feedback_errors(payload.get("feedback")))

    study = payload.get("study")
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        denominators = summary.get("denominators")
        if not isinstance(denominators, Mapping):
            errors.append("Summary denominators are missing.")
        else:
            for key in (
                "total_model_calls",
                "accepted_response_records",
                "accepted_unique_replicates",
                "unique_archetypes",
                "grounded_context_profiles",
                "accepted_context_strata",
            ):
                if key not in denominators:
                    errors.append(f"Summary denominator missing: {key}")
            for key in (
                "accepted_response_records",
                "accepted_unique_replicates",
                "unique_archetypes",
                "grounded_context_profiles",
                "accepted_context_strata",
            ):
                value = denominators.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    errors.append(f"Summary denominator must be a non-negative integer: {key}")
            total_model_calls = denominators.get("total_model_calls")
            if total_model_calls is not None and (
                isinstance(total_model_calls, bool)
                or not isinstance(total_model_calls, int)
                or total_model_calls < denominators.get("accepted_response_records", 0)
            ):
                errors.append(
                    "Summary total_model_calls must be at least accepted_response_records."
                )
        for key in (
            "accepted_response_records_by_stage",
            "accepted_unique_replicates_by_stage",
        ):
            values = summary.get(key)
            if not isinstance(values, Mapping) or any(
                stage not in {"screening", "boundary", "finalist"}
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for stage, value in values.items()
            ):
                errors.append(f"Summary {key} is malformed.")
        for key in (
            "validity_status",
            "human_alignment_validation",
            "field_performance_calibration",
            "usable_blocks",
            "roster_decision",
        ):
            if key not in summary:
                errors.append(f"Summary validity/provenance field missing: {key}")
        run_integrity = summary.get("run_integrity")
        if not isinstance(run_integrity, list):
            errors.append("Summary run-integrity dimensions are missing.")
        elif [
            item.get("dimension") for item in run_integrity if isinstance(item, Mapping)
        ] != list(REQUIRED_INTEGRITY_DIMENSIONS):
            errors.append("Summary must contain all five run-integrity dimensions in order.")
        decision = summary.get("roster_decision")
        if isinstance(decision, Mapping):
            approved = decision.get("is_approved") is True
            status = decision.get("status")
            override = decision.get("override")
            if approved != (status in APPROVED_ROSTER_STATES):
                errors.append("Summary roster approval boolean disagrees with its status.")
            if not isinstance(override, bool) or (
                (status == "approved_with_override") != (override is True)
            ):
                errors.append(
                    "Summary approved_with_override status and override flag disagree."
                )
            moving = summary.get("ads_moving_forward")
            pending = summary.get("ads_pending_approval")
            if approved and (not isinstance(moving, list) or pending != []):
                errors.append("Approved summary finalist language is inconsistent.")
            if not approved and (moving != [] or not isinstance(pending, list)):
                errors.append("Awaiting-approval summary must expose only pending finalists.")
            decision_text = " ".join(_string_values({
                "decision": decision,
                "basis": summary.get("decision_basis"),
                "overview": summary.get("overview_intro"),
            })).lower()
            if not approved and (
                "moving forward" in decision_text
                or "made the final shortlist decision" in decision_text
                or "made the cut" in decision_text
            ):
                errors.append("Awaiting-approval summary contains final-decision language.")
            if not isinstance(summary.get("overview_intro"), str) or not summary.get(
                "overview_intro", ""
            ).strip():
                errors.append("Summary overview intro is missing.")
            elif not approved and "pending" not in summary["overview_intro"].lower():
                errors.append("Awaiting-approval overview must state that approval is pending.")

    screening = payload.get("screening")
    if isinstance(screening, Mapping):
        method = study.get("method") if isinstance(study, Mapping) else None
        expected_labels = {
            "primary_label": "How often it ranked among the leaders",
            "technical_label": "Conditional Within-Run Stability",
            "estimand_primary_label": "Overall result",
        }
        if method == "partial_exposure_maxdiff":
            expected_labels.update(
                estimand_technical_label="Centered protocol-relative utility",
            )
        elif method == "complete_exposure":
            expected_labels.update(
                estimand_technical_label="Complete-exposure comparison utility",
            )
        else:
            errors.append("Dashboard study method is unsupported.")
        for key, expected in expected_labels.items():
            if screening.get(key) != expected:
                errors.append(f"Screening {key} must be {expected}.")

    methodology = payload.get("methodology")
    if isinstance(methodology, Mapping):
        if not isinstance(study, Mapping) or methodology.get("method_id") != study.get("method"):
            errors.append("Methodology method_id does not match the study method.")
        method_integrity = methodology.get("run_integrity")
        summary_integrity = summary.get("run_integrity") if isinstance(summary, Mapping) else None
        if method_integrity != summary_integrity:
            errors.append("Methodology run-integrity dimensions do not match Overview.")
        if isinstance(method_integrity, list):
            for index, item in enumerate(method_integrity):
                if not isinstance(item, Mapping):
                    errors.append(f"Run-integrity dimension {index} must be an object.")
                    continue
                for key in ("dimension", "overview_label", "status", "overview", "details"):
                    if not item.get(key):
                        errors.append(f"Run-integrity dimension {index} missing: {key}")
        limits = methodology.get("interpretation_limits")
        if not isinstance(limits, list) or not limits or not all(
            isinstance(item, str) and item.strip() for item in limits
        ):
            errors.append("Methodology must include nonempty run-specific interpretation limits.")
        if isinstance(screening, Mapping) and limits != screening.get("interpretation_limits"):
            errors.append("Methodology interpretation limits do not match screening.")
        method_text = " ".join(_string_values(methodology))
        if isinstance(study, Mapping) and study.get("method") == "complete_exposure":
            if "maxdiff" in method_text.lower() or "four-ad" in method_text.lower():
                errors.append("Complete-exposure methodology contains partial-exposure details.")

    finalist_payload = payload.get("finalists")
    if isinstance(finalist_payload, Mapping):
        decision = finalist_payload.get("roster_decision")
        approved = isinstance(decision, Mapping) and decision.get("is_approved") is True
        if finalist_payload.get("metrics_available") is not approved:
            errors.append("Finalist metrics availability does not match roster approval.")
        if not approved and any(
            finalist_payload.get(key)
            for key in (
                "accepted_response_records",
                "accepted_unique_replicates",
                "unique_job_slots_consumed",
                "total_model_calls",
                "conditional_first_choice_share",
                "first_choice_counts",
                "rubric_summary",
                "testing_map",
            )
        ):
            errors.append("Awaiting-approval dashboard exposes finalist metrics.")

    creatives = payload.get("creatives")
    roster_ids: set[str] = set()
    media_by_representation: dict[str, tuple[str, Mapping[str, Any]]] = {}
    if isinstance(creatives, list):
        for creative_index, creative in enumerate(creatives):
            if not isinstance(creative, Mapping):
                errors.append(f"Creative {creative_index} must be an object.")
                continue
            if not creative.get("display_name") or not creative.get("variation_id"):
                errors.append(f"Creative {creative_index} lacks a human name or stable ID.")
            creative_id = str(creative.get("variation_id", ""))
            roster_ids.add(creative_id)
            media = creative.get("media", [])
            if not isinstance(media, list):
                errors.append(f"Creative {creative_index} media must be an array.")
                continue
            for media_index, medium in enumerate(media):
                if not isinstance(medium, Mapping):
                    errors.append(f"Creative {creative_index} media {media_index} must be an object.")
                    continue
                if "path" in medium:
                    errors.append(f"Creative {creative_index} media {media_index} leaks a local path.")
                representation_id = medium.get("representation_id")
                content_hash = medium.get("content_hash")
                if not isinstance(representation_id, str) or not representation_id:
                    errors.append(f"Creative {creative_index} media {media_index} lacks representation_id.")
                    continue
                if representation_id in media_by_representation:
                    errors.append(f"Duplicate media representation_id: {representation_id}")
                if not isinstance(content_hash, str) or not content_hash:
                    errors.append(f"Creative {creative_index} media {media_index} lacks content_hash.")
                raw_media, media_error = _decode_data_url(
                    medium.get("data_url"),
                    f"Creative {creative_index} media {media_index}",
                )
                if media_error and medium.get("availability") == "embedded":
                    errors.append(media_error)
                data_mime = _data_url_mime_type(medium.get("data_url"))
                declared_mime = str(medium.get("mime_type", "")).lower()
                if medium.get("availability") == "embedded" and (
                    data_mime not in RENDERABLE_IMAGE_MIME_TYPES
                    or declared_mime != data_mime
                ):
                    errors.append(
                        f"Creative media representation {representation_id} must use a "
                        "consistent renderable image MIME type."
                    )
                if raw_media is not None:
                    actual_hash = f"sha256:{hashlib.sha256(raw_media).hexdigest()}"
                    if actual_hash != content_hash:
                        errors.append(
                            f"Creative media representation {representation_id} content_hash is false."
                        )
                media_by_representation[representation_id] = (creative_id, medium)

    visual = payload.get("visual_evidence")
    imagery_expected = study.get("imagery_expected") if isinstance(study, Mapping) else None
    visual_status = (
        methodology.get("visual_attention_status")
        if isinstance(methodology, Mapping)
        else None
    )
    if imagery_expected is True and visual is None:
        errors.append("An imagery study must include an attention heatmap.")
    if imagery_expected is False and visual is not None:
        errors.append("A copy-only study must not include an attention heatmap.")
    if imagery_expected is False and visual_status != "No imagery was tested.":
        errors.append('A copy-only study must say "No imagery was tested."')
    if imagery_expected not in (True, False):
        errors.append("Study imagery_expected must be a boolean.")
    if visual is not None:
        if not isinstance(visual, Mapping):
            errors.append("visual_evidence must be null or an object.")
        else:
            if visual.get("roster_approved_before_reveal") is not True:
                errors.append("Attention heatmap cannot render before roster approval.")
            if not isinstance(visual.get("provider"), str) or not visual.get("provider", "").strip():
                errors.append("Attention heatmap requires a nonempty provider.")
            if not isinstance(visual.get("method"), str) or not visual.get("method", "").strip():
                errors.append("Attention heatmap requires a nonempty method.")
            approved_at = _aware_datetime(visual.get("approved_at"))
            revealed_at = _aware_datetime(visual.get("revealed_at"))
            if approved_at is None or revealed_at is None or approved_at >= revealed_at:
                errors.append("Attention heatmap approval/reveal timestamps are invalid or unordered.")
            entries = visual.get("entries")
            seen_representations: set[str] = set()
            if not isinstance(entries, list) or not entries:
                errors.append("Attention heatmap requires at least one inspectable entry.")
            else:
                for index, entry in enumerate(entries):
                    if not isinstance(entry, Mapping):
                        errors.append(f"Attention heatmap entry {index} must be an object.")
                        continue
                    for key in (
                        "representation_id",
                        "content_hash",
                        "overlay_content_hash",
                        "original_data_url",
                        "overlay_data_url",
                        "predeclared_target",
                        "target_declared_at",
                        "categorical_alignment",
                        "provider",
                        "limitations",
                    ):
                        if not entry.get(key):
                            errors.append(f"Attention heatmap entry {index} missing: {key}")
                    representation_id = str(entry.get("representation_id", ""))
                    if representation_id in seen_representations:
                        errors.append(f"Duplicate attention representation_id: {representation_id}")
                    seen_representations.add(representation_id)
                    source = media_by_representation.get(representation_id)
                    if source is None:
                        errors.append(
                            f"Attention heatmap references unknown media representation: {representation_id}"
                        )
                    else:
                        creative_id, medium = source
                        if (
                            entry.get("variation_id") != creative_id
                            or entry.get("content_hash") != medium.get("content_hash")
                            or entry.get("original_data_url") != medium.get("data_url")
                        ):
                            errors.append(
                                f"Attention heatmap representation {representation_id} is not bound to the tested media."
                            )
                    target_time = _aware_datetime(entry.get("target_declared_at"))
                    if target_time is None or revealed_at is None or target_time >= revealed_at:
                        errors.append(
                            f"Attention heatmap entry {index} target was not predeclared before reveal."
                        )
                    if entry.get("provider") != visual.get("provider"):
                        errors.append(f"Attention heatmap entry {index} provider disagrees with index.")
                    if entry.get("categorical_alignment") not in {
                        "aligned", "partially_aligned", "misaligned", "unclear"
                    }:
                        errors.append(f"Attention heatmap entry {index} alignment is invalid.")
                    limitations = entry.get("limitations")
                    if not isinstance(limitations, list) or not limitations or not all(
                        isinstance(item, str) and item.strip() for item in limitations
                    ):
                        errors.append(f"Attention heatmap entry {index} limitations are missing.")
                    for key, hash_key in (
                        ("original_data_url", "content_hash"),
                        ("overlay_data_url", "overlay_content_hash"),
                    ):
                        rendered_bytes, media_error = _decode_data_url(
                            entry.get(key), f"Attention heatmap entry {index} {key}"
                        )
                        if media_error:
                            errors.append(media_error)
                        elif rendered_bytes is not None:
                            actual_hash = (
                                f"sha256:{hashlib.sha256(rendered_bytes).hexdigest()}"
                            )
                            if actual_hash != entry.get(hash_key):
                                errors.append(
                                    f"Attention heatmap entry {index} {hash_key} is false."
                                )
                        mime_type = _data_url_mime_type(entry.get(key))
                        declared_key = (
                            "original_mime_type"
                            if key == "original_data_url"
                            else "overlay_mime_type"
                        )
                        if (
                            mime_type not in RENDERABLE_IMAGE_MIME_TYPES
                            or str(entry.get(declared_key, "")).lower() != mime_type
                        ):
                            errors.append(
                                f"Attention heatmap entry {index} {key} must use a "
                                "consistent renderable image MIME type."
                            )
            missing_ids = sorted(set(media_by_representation) - seen_representations)
            unknown_ids = sorted(seen_representations - set(media_by_representation))
            if missing_ids:
                errors.append(
                    "Attention heatmap is missing tested media representations: "
                    + ", ".join(missing_ids)
                )
            if unknown_ids:
                errors.append(
                    "Attention heatmap references unknown tested media representations: "
                    + ", ".join(unknown_ids)
                )

    exports = payload.get("exports")
    if isinstance(exports, list):
        if not exports:
            errors.append("Dashboard contains no direct exports.")
        for index, item in enumerate(exports):
            if not isinstance(item, Mapping):
                errors.append(f"Export {index} must be an object.")
                continue
            if not item.get("filename"):
                errors.append(f"Export {index} has no filename.")
            if not str(item.get("data_url", "")).startswith("data:"):
                errors.append(f"Export {index} is not a self-contained data URL.")

    sources, source_errors = _decode_source_exports(payload)
    errors.extend(source_errors)
    if not source_errors:
        errors.extend(_validate_source_export_integrity(payload, sources))

    specialist = _specialist_keys(payload)
    if specialist:
        errors.append("Dashboard payload contains forbidden specialist-score fields: " + ", ".join(specialist))
    payload_text = " ".join(_string_values(payload))
    for term in FORBIDDEN_TERMS:
        if term.lower() in payload_text.lower():
            errors.append(f"Forbidden dashboard terminology in embedded data: {term}")
    screening_text = " ".join(_screening_strings(payload))
    if UNQUALIFIED_SCREENING_PERCENT_RE.search(screening_text):
        errors.append("Dashboard embedded data contains an unqualified screening percentage claim.")
    return errors


def validate(path: Path, allow_placeholders: bool = False) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"Dashboard file not found: {path}"]
    if not path.is_file():
        return [f"Dashboard path is not a file: {path}"]
    try:
        html = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"Dashboard is not valid UTF-8: {exc}"]

    parser = DashboardHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        errors.append(f"HTML parser failed: {exc}")

    placeholders = sorted(set(PLACEHOLDER_RE.findall(html)))
    if placeholders and not allow_placeholders:
        errors.append("Unresolved placeholders: " + ", ".join(placeholders))

    text = visible_text(html)
    for required in REQUIRED_MARKETER_TEXT:
        if allow_placeholders and placeholders and required in PAYLOAD_MARKETER_TEXT:
            continue
        if required not in html:
            errors.append(f"Missing required dashboard label: {required}")
    if REJECTED_PRIMARY_HEADING_RE.search(html):
        errors.append("Dashboard uses rejected research jargon as a primary heading.")
    for tab in REQUIRED_TABS:
        if tab not in parser.tab_labels:
            errors.append(f"Missing required dashboard tab: {tab}")
    for filter_name in REQUIRED_FILTERS:
        if f'data-filter="{filter_name}"' not in html:
            errors.append(f"Missing required response filter: {filter_name}")

    for term in FORBIDDEN_TERMS:
        if term.lower() in text.lower():
            errors.append(f"Forbidden dashboard terminology: {term}")
    if UNQUALIFIED_SCREENING_PERCENT_RE.search(text):
        errors.append("Dashboard contains an unqualified screening percentage claim.")
    if EXTERNAL_ASSET_RE.search(html):
        errors.append("Dashboard references a network asset instead of embedding it.")
    if "fetch(" in html or "XMLHttpRequest" in html:
        errors.append("Dashboard attempts a runtime file or network fetch.")

    matches = list(PAYLOAD_RE.finditer(html))
    if allow_placeholders and placeholders:
        return errors
    if len(matches) != 1:
        errors.append("Dashboard must contain exactly one audience-lab JSON payload.")
    else:
        payload_match = matches[0]
        encoded_payload = payload_match.group(1)
        if "<" in encoded_payload:
            errors.append("Dashboard JSON payload contains an unsafe literal less-than sign.")
        try:
            payload = json.loads(encoded_payload)
        except json.JSONDecodeError as exc:
            errors.append(f"Dashboard JSON payload is invalid: {exc.msg}")
        else:
            errors.extend(_validate_payload(payload))
            audience = payload.get("audience")
            has_run_allocation = (
                isinstance(audience, Mapping)
                and "run_allocation" in audience
            )
            has_tier4_validation = "tier4_validation" in payload
            payload_start, payload_end = payload_match.span(1)
            normalized_html = (
                html[:payload_start]
                + CANONICAL_PAYLOAD_SENTINEL
                + html[payload_end:]
            )
            try:
                canonical_shell = _canonical_dashboard_shell(
                    has_run_allocation, has_tier4_validation
                )
            except (OSError, RuntimeError, UnicodeDecodeError) as exc:
                errors.append(
                    "Canonical dashboard shell cannot be derived from the "
                    f"shipped template and logo: {exc}."
                )
            else:
                if normalized_html != canonical_shell:
                    errors.append(
                        "Dashboard does not match the canonical dashboard shell."
                    )
            expected_surface_count = 1 if has_run_allocation else 0
            allocation_surfaces = (
                ("deterministic Run allocation section", V3_ALLOCATION_SECTION_HTML),
                ("deterministic Run allocation renderer", V3_ALLOCATION_SCRIPT),
                (
                    "Run allocation section ID",
                    'id="audience-run-allocation"',
                ),
                (
                    "Run allocation body ID",
                    'id="audience-run-allocation-body"',
                ),
            )
            for label, surface in allocation_surfaces:
                actual_count = html.count(surface)
                if actual_count != expected_surface_count:
                    errors.append(
                        f"{label} must appear exactly {expected_surface_count} "
                        f"time(s); found {actual_count}."
                    )
            tier4_surface_count = 1 if has_tier4_validation else 0
            for label, surface in (
                ("Held-out ordering validation section", TIER4_VALIDATION_SECTION_HTML),
                ("Held-out ordering validation renderer", TIER4_VALIDATION_SCRIPT),
                ("Held-out ordering validation section ID", 'id="held-out-ordering-validation"'),
                ("Held-out ordering validation body ID", 'id="held-out-ordering-validation-body"'),
            ):
                actual_count = html.count(surface)
                if actual_count != tier4_surface_count:
                    errors.append(
                        f"{label} must appear exactly {tier4_surface_count} "
                        f"time(s); found {actual_count}."
                    )
            tier4_nodes = (
                ("actual Held-out ordering validation section node", parser.elements_by_id[("section", "held-out-ordering-validation")]),
                ("actual Held-out ordering validation body node", parser.elements_by_id[("div", "held-out-ordering-validation-body")]),
                ("actual Held-out ordering validation section ID", parser.element_ids["held-out-ordering-validation"]),
                ("actual Held-out ordering validation body ID", parser.element_ids["held-out-ordering-validation-body"]),
                ("visible canonical Held-out ordering validation section", parser.exact_tier4_sections),
                ("visible canonical Held-out ordering validation body", parser.exact_tier4_bodies),
                ("Held-out ordering validation body nested within its canonical section", parser.nested_tier4_bodies),
            )
            for label, actual_count in tier4_nodes:
                if actual_count != tier4_surface_count:
                    errors.append(
                        f"{label} must appear exactly {tier4_surface_count} "
                        f"time(s); found {actual_count}."
                    )
            allocation_nodes = (
                (
                    "actual Run allocation section node",
                    parser.elements_by_id[
                        ("section", "audience-run-allocation")
                    ],
                ),
                (
                    "actual Run allocation body node",
                    parser.elements_by_id[
                        ("div", "audience-run-allocation-body")
                    ],
                ),
                (
                    "actual Run allocation section ID",
                    parser.element_ids["audience-run-allocation"],
                ),
                (
                    "actual Run allocation body ID",
                    parser.element_ids["audience-run-allocation-body"],
                ),
            )
            for label, actual_count in allocation_nodes:
                if actual_count != expected_surface_count:
                    errors.append(
                        f"{label} must appear exactly {expected_surface_count} "
                        f"time(s); found {actual_count}."
                    )
            canonical_allocation_nodes = (
                (
                    "visible canonical Run allocation section",
                    parser.exact_allocation_sections,
                ),
                (
                    "visible canonical Run allocation body",
                    parser.exact_allocation_bodies,
                ),
                (
                    "Run allocation body nested within its canonical section",
                    parser.nested_allocation_bodies,
                ),
            )
            for label, actual_count in canonical_allocation_nodes:
                if actual_count != expected_surface_count:
                    errors.append(
                        f"{label} must appear exactly {expected_surface_count} "
                        f"time(s); found {actual_count}."
                    )
            if parser.active_body_count != 1:
                errors.append(
                    "Dashboard must contain exactly one real active body "
                    f"element; found {parser.active_body_count}."
                )
            runtime_bodies = parser.runtime_script_bodies()
            if len(runtime_bodies) != 1:
                errors.append(
                    "Dashboard must contain exactly one active canonical "
                    f"runtime script; found {len(runtime_bodies)}."
                )
            else:
                actual_runtime_sha256 = hashlib.sha256(
                    runtime_bodies[0].encode("utf-8")
                ).hexdigest()
                try:
                    expected_runtime_sha256 = (
                        _canonical_runtime_script_sha256(
                            has_run_allocation, has_tier4_validation
                        )
                    )
                except (OSError, RuntimeError) as exc:
                    errors.append(
                        "Canonical dashboard runtime script cannot be derived "
                        f"from the shipped template: {exc}."
                    )
                else:
                    if actual_runtime_sha256 != expected_runtime_sha256:
                        mode = (
                            "Tier 4 validation" if has_tier4_validation
                            else "v3 allocation" if has_run_allocation else "legacy"
                        )
                        errors.append(
                            "Active dashboard runtime script does not match the "
                            f"canonical {mode} runtime digest."
                        )
            if payload.get("visual_evidence") is not None:
                for label in (
                    "Attention heatmap",
                    "Where attention is likely to go",
                    "Original ad",
                    "Predicted attention",
                    "Warmer areas = more predicted attention",
                    "How to read this",
                ):
                    if label not in html:
                        errors.append(f"Attention heatmap is missing label: {label}")
            if "Overlay opacity" in html or 'type="range"' in html:
                errors.append("Attention heatmap must not include the obsolete opacity control.")
            if "former control" in html or "no opacity control is needed" in html:
                errors.append("Attention heatmap contains product-change commentary.")
            if "Context strata represented" in html:
                errors.append("Dashboard uses context-strata jargon as a visible primary label.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dashboard", type=Path)
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="Allow compiler placeholders while validating the source template",
    )
    args = parser.parse_args()
    errors = validate(args.dashboard.expanduser().resolve(), args.allow_placeholders)
    if errors:
        print("Dashboard validation FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Dashboard validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
