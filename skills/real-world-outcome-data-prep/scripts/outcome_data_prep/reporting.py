"""Privacy-safe human reports for prepared outcome imports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contracts import validate_readiness_report


READINESS_HEADINGS = {
    "contract_ready": "Ready for Real-World Outcome Validation",
    "incomplete": (
        "Incomplete: export verification or required evidence is missing"
    ),
    "descriptive_only": (
        "Descriptive only: this was not a preregistered holdout"
    ),
    "blocked": "Blocked: the evidence cannot be admitted safely",
}


def render_readiness_report(report: Mapping[str, object]) -> str:
    """Render one exact readiness decision without evaluating outcomes."""

    validated = validate_readiness_report(report)
    reasons = "\n".join(
        f"- {reason}" for reason in validated["reasons"]
    )
    return (
        f"# {READINESS_HEADINGS[validated['operational_status']]}\n\n"
        f"{reasons}\n\n"
        "This preparation step does not decide whether the panel was right "
        "and does not change any persona or active panel.\n"
    )


def render_matching_report(
    *,
    matched: Sequence[Mapping[str, object]],
    quarantined: Sequence[Mapping[str, object]],
    rejected: Sequence[Mapping[str, object]] = (),
    unresolved: Sequence[Mapping[str, object]] = (),
) -> str:
    """List dispositions by safe source-row reference only."""

    def reference(value: Mapping[str, object]) -> str:
        row = value.get("normalized_observation")
        if isinstance(row, Mapping):
            candidate = row.get("source_row_reference")
        else:
            candidate = value.get("source_row_reference")
        return candidate if isinstance(candidate, str) else "not-available"

    def reason(value: Mapping[str, object]) -> str:
        candidate = value.get("reason")
        return candidate if isinstance(candidate, str) else "none"

    sections = []
    for heading, records in (
        ("Matched rows", matched),
        ("Quarantined rows", quarantined),
        ("Rejected rows", rejected),
        ("Unresolved rows", unresolved),
    ):
        lines = [f"## {heading}"]
        if records:
            lines.extend(
                f"- `{reference(item)}` — {reason(item)}"
                for item in records
            )
        else:
            lines.append("- None")
        sections.append("\n".join(lines))
    return (
        "# Outcome import matching report\n\n"
        + "\n\n".join(sections)
        + "\n\nThis report contains structural matching only. It does not "
        "compare creatives, evaluate the panel, or change a persona or "
        "active panel.\n"
    )


__all__ = [
    "READINESS_HEADINGS",
    "render_matching_report",
    "render_readiness_report",
]
