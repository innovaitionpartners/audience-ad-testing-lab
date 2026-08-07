#!/usr/bin/env python3
"""Render readable panel and construction validation summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "audience-ad-testing-lab" / "scripts"
)
sys.path.insert(0, str(CURRENT_SCRIPTS))
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    create_new_directory,
)
from audience_panel_builder.review import (  # noqa: E402
    PANEL_REVIEW_HTML_PATH,
    PANEL_REVIEW_MARKDOWN_PATH,
    build_source_link_overrides,
    build_panel_review_manifest,
    render_panel_approval_request,
    render_panel_review_html,
    render_panel_summary,
    render_validation_report,
)
from audience_panel_builder.reporting import _validate_research_pair  # noqa: E402


def _load(path: Path | None):
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--scored-sources", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--finding-support", type=Path)
    parser.add_argument("--synthesis-matrix", type=Path)
    parser.add_argument("--run-plan", type=Path)
    parser.add_argument("--run-results", type=Path)
    parser.add_argument("--review-revision", default="review-v1")
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    try:
        if (args.run_plan is None) != (args.run_results is None):
            raise ContractError("--run-plan and --run-results must be supplied together")
        raw_brief = _load(args.brief)
        raw_panel = _load(args.panel)
        run_plan = _load(args.run_plan)
        run_results = _load(args.run_results)
        scored_sources = _load(args.scored_sources)
        source_links = build_source_link_overrides(scored_sources)
        brief, panel, _, _ = _validate_research_pair(raw_brief, raw_panel)
        summary = render_panel_summary(
            brief,
            panel,
            run_plan=run_plan,
            run_results=run_results,
            source_links=source_links,
        )
        html = render_panel_review_html(
            brief,
            panel,
            run_plan=run_plan,
            run_results=run_results,
            source_links=source_links,
        )
        validation = render_validation_report(
            brief,
            panel,
            plan=_load(args.plan),
            scored_sources=scored_sources,
            ledger=_load(args.ledger),
            finding_support=_load(args.finding_support),
            synthesis_matrix=_load(args.synthesis_matrix),
            run_plan=run_plan,
            run_results=run_results,
        )
        output_dir = create_new_directory(
            args.output_dir,
            "panel review output directory",
        )
        summary_bytes = summary.encode("utf-8")
        html_bytes = html.encode("utf-8")
        manifest = build_panel_review_manifest(
            panel=panel,
            summary_bytes=summary_bytes,
            html_bytes=html_bytes,
            review_revision=args.review_revision,
            generated_at=args.generated_at or panel["updated_at"],
        )
        manifest_bytes = canonical_json_bytes(manifest)
        (output_dir / "saved-audience-panel.json").write_bytes(
            canonical_json_bytes(panel)
        )
        (output_dir / PANEL_REVIEW_MARKDOWN_PATH).write_bytes(summary_bytes)
        (output_dir / PANEL_REVIEW_HTML_PATH).write_bytes(html_bytes)
        (output_dir / "validation-report.md").write_text(
            validation, encoding="utf-8"
        )
        (output_dir / "panel-review-manifest.json").write_bytes(manifest_bytes)
        (output_dir / "panel-construction-approval-request.md").write_text(
            render_panel_approval_request(panel=panel, manifest=manifest),
            encoding="utf-8",
        )
    except (
        ContractError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
