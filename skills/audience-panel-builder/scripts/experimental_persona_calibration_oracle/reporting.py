"""Static, escaped reporting for hidden-oracle evaluation results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from html.parser import HTMLParser
import re

from audience_panel_builder.common import ContractError
from audience_panel_builder.population.experimental_calibration.contracts import (
    validate_experimental_proposal,
    validate_sandbox_candidate_binding,
)

from .contracts import validate_synthetic_evaluation


class UnsafeReportTemplate(ContractError):
    """The supplied local template can execute or fetch external content."""


_PLACEHOLDER = "{{REPORT_CONTENT}}"
_FORBIDDEN_TAGS = {
    "base",
    "embed",
    "form",
    "iframe",
    "math",
    "object",
    "script",
    "svg",
}
_FORBIDDEN_ATTRIBUTES = {
    "action",
    "background",
    "formaction",
    "href",
    "poster",
    "src",
    "srcset",
    "xlink:href",
}
_UNSAFE_CSS = re.compile(
    r"(?:@import|url\s*\(|image-set\s*\(|https?://|//[A-Za-z0-9])",
    re.IGNORECASE,
)
_PROHIBITED_COPY = (
    re.compile(r"\bc1\b", re.IGNORECASE),
    re.compile(r"\bc2\b", re.IGNORECASE),
    re.compile(r"\btier\s+4\b", re.IGNORECASE),
    re.compile(r"\bcalibrated\b", re.IGNORECASE),
    re.compile(r"\bproven improvement\b", re.IGNORECASE),
    re.compile(r"\bcfos prefer\b", re.IGNORECASE),
    re.compile(r"\bwill improve\b", re.IGNORECASE),
)


class _TemplateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.in_style = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _FORBIDDEN_TAGS:
            self.errors.append(f"<{normalized_tag}> is forbidden")
        if normalized_tag == "style":
            self.in_style = True
        if normalized_tag == "meta":
            names = {name.lower(): value for name, value in attrs}
            if "http-equiv" in names or (
                "charset" not in names and names.get("name") != "viewport"
            ):
                self.errors.append("unsafe meta element")
        for name, value in attrs:
            normalized = name.lower()
            if normalized.startswith("on"):
                self.errors.append(f"event attribute {normalized} is forbidden")
            if normalized in _FORBIDDEN_ATTRIBUTES:
                self.errors.append(f"attribute {normalized} is forbidden")
            if normalized == "style" and value and _UNSAFE_CSS.search(value):
                self.errors.append("CSS external reference is forbidden")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if "innerhtml" in data.lower():
            self.errors.append("innerHTML is forbidden")
        if self.in_style and _UNSAFE_CSS.search(data):
            self.errors.append("CSS external reference is forbidden")


def _validate_template(template: object) -> str:
    if not isinstance(template, str) or not template:
        raise UnsafeReportTemplate("report template must be non-empty text")
    if template.count(_PLACEHOLDER) != 1:
        raise UnsafeReportTemplate(
            "report template must contain one REPORT_CONTENT placeholder"
        )
    parser = _TemplateParser()
    try:
        parser.feed(template)
        parser.close()
    except Exception as exc:  # HTMLParser reports malformed internals variably.
        raise UnsafeReportTemplate("report template is malformed") from exc
    if parser.errors:
        raise UnsafeReportTemplate("; ".join(sorted(set(parser.errors))))
    return template


def _text(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def _list(items: Sequence[object], *, empty: str = "None recorded.") -> str:
    if not items:
        return f"<p>{escape(empty)}</p>"
    return "<ul>" + "".join(
        f"<li>{escape(_text(item))}</li>" for item in items
    ) + "</ul>"


def _behavior_rows(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return "<p>No behavioral operation was created.</p>"
    return "<dl>" + "".join(
        (
            f"<dt>{escape(str(row['field']).replace('_', ' ').title())}</dt>"
            f"<dd>{escape(_text(row['value']))}</dd>"
        )
        for row in rows
    ) + "</dl>"


def _measurement_rows(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return "<p>No measurement context was admitted.</p>"
    headings = (
        "Platform",
        "Metric",
        "Denominator",
        "Attribution",
        "Maturity",
    )
    keys = ("platform", "metric", "denominator", "attribution", "maturity")
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{heading}</th>" for heading in headings)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>"
            + "".join(f"<td>{escape(str(row[key]))}</td>" for key in keys)
            + "</tr>"
            for row in rows
        )
        + "</tbody></table>"
    )


def _family_rows(evaluation: Mapping[str, object]) -> str:
    rows = [
        {
            "family": family["scenario_family_id"],
            "partition": family["partition"],
            "result": scenario["result"].replace("_", " "),
            "failures": len(scenario["failure_details"]),
        }
        for family in evaluation["scenario_family_results"]
        for scenario in family["scenario_results"]
    ]
    return (
        "<table><thead><tr><th>Scenario family</th><th>Partition</th>"
        "<th>Result</th><th>Disclosed issues</th></tr></thead><tbody>"
        + "".join(
            (
                f"<tr><td>{escape(str(row['family']))}</td>"
                f"<td>{escape(str(row['partition']))}</td>"
                f"<td>{escape(str(row['result']).title())}</td>"
                f"<td>{row['failures']}</td></tr>"
            )
            for row in rows
        )
        + "</tbody></table>"
    )


def render_experimental_report(
    *,
    evaluation: dict[str, object],
    proposals: Sequence[dict[str, object]],
    candidates: Sequence[dict[str, object]],
    template: str,
) -> str:
    """Render one local report without accepting hidden truth or raw HTML."""

    checked = validate_synthetic_evaluation(evaluation)
    checked_proposals = sorted(
        (validate_experimental_proposal(row) for row in proposals),
        key=lambda row: str(row["proposal_id"]),
    )
    checked_candidates = sorted(
        (validate_sandbox_candidate_binding(row) for row in candidates),
        key=lambda row: str(row["candidate_id"]),
    )
    evidence = checked["evidence_bindings"]
    if sorted(row["proposal_sha256"] for row in checked_proposals) != evidence[
        "proposal_sha256s"
    ]:
        raise ContractError("report proposals do not match the evaluation")
    if sorted(
        row["candidate_binding_sha256"] for row in checked_candidates
    ) != evidence["candidate_binding_sha256s"]:
        raise ContractError("report candidates do not match the evaluation")
    safe_template = _validate_template(template)
    projection = checked["report_projection"]

    causes = [
        f"{row['cause'].replace('_', ' ')}: {row['status'].replace('_', ' ')}"
        for row in projection["alternative_explanations"]
    ]
    technical = (
        "<table><thead><tr><th>Binding</th><th>SHA-256</th></tr></thead><tbody>"
        + "".join(
            (
                f"<tr><td>{escape(str(row['label']))}</td>"
                f"<td><code>{escape(str(row['sha256']))}</code></td></tr>"
            )
            for row in projection["technical_bindings"]
        )
        + (
            f"<tr><td>Evaluation</td><td><code>"
            f"{escape(str(checked['evaluation_sha256']))}</code></td></tr>"
        )
        + "</tbody></table>"
    )
    state_notice = (
        f'<p class="state">'
        f"{escape(str(projection['visible_result_state']))}</p>"
    )
    candidate_notice = (
        "<p><strong>Cannot be registered or activated.</strong></p>"
        if projection["candidate_created"]
        else "<p>No sandbox candidate was created.</p>"
    )
    body = f"""
<main>
  <header>
    <p class="eyebrow">Experimental research sandbox</p>
    <h1>Experimental Persona Behavior Calibration Sandbox</h1>
    <p><strong>Built and evaluated with fictional synthetic fixtures only.</strong></p>
    <p>This output does not validate real-world panel accuracy.</p>
    <p>The proposal is not proven to improve real-world outcomes.</p>
    <p>This report cannot modify an active panel.</p>
    {state_notice}
    {candidate_notice}
  </header>
  <section>
    <h2>Existing persona behavior</h2>
    {_behavior_rows(projection["existing_persona_behavior"])}
  </section>
  <section>
    <h2>Proposed hypothesis</h2>
    {_behavior_rows(projection["proposed_hypothesis"])}
  </section>
  <section>
    <h2>Exact persona diff</h2>
    {_list(projection["exact_persona_diff"])}
  </section>
  <section>
    <h2>Associations</h2>
    {_list(projection["associations"])}
    <h3>Supporting evidence</h3>
    {_list(projection["supporting_evidence"])}
    <h3>Contrary evidence</h3>
    {_list(projection["contrary_evidence"])}
  </section>
  <section>
    <h2>Alternative explanations</h2>
    {_list(causes)}
  </section>
  <section>
    <h2>Measurement context</h2>
    {_measurement_rows(projection["measurement_context"])}
  </section>
  <section>
    <h2>Family results</h2>
    {_family_rows(checked)}
    <h3>Abstentions</h3>
    {_list(projection["abstentions"])}
    <h3>Failures</h3>
    {_list(projection["failures"])}
  </section>
  <section>
    <h2>Limits</h2>
    {_list(projection["limits"])}
  </section>
  <section>
    <h2>Technical bindings</h2>
    {technical}
  </section>
</main>
""".strip()
    rendered = safe_template.replace(_PLACEHOLDER, body)
    for pattern in _PROHIBITED_COPY:
        if pattern.search(rendered):
            raise ContractError(
                "rendered report contains prohibited audience-facing claim language"
            )
    return rendered


__all__ = [
    "UnsafeReportTemplate",
    "render_experimental_report",
]
