#!/usr/bin/env python3
"""Render the evidence-derived Audience Panel Builder research report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = Path(__file__).resolve().parents[2] / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(CURRENT_SCRIPTS))
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.reporting import (  # noqa: E402
    _release_a_report_inputs,
    build_source_inventory,
    build_verbatim_inventory,
    render_research_report,
)


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--scored-sources", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--finding-support", required=True, type=Path)
    parser.add_argument("--synthesis", required=True, type=Path)
    parser.add_argument("--report-inputs", type=Path)
    parser.add_argument("--population-frame", type=Path)
    parser.add_argument("--composition-plan", type=Path)
    parser.add_argument("--validity-profile", type=Path)
    parser.add_argument("--panel-review-manifest", type=Path)
    parser.add_argument("--panel-summary", type=Path)
    parser.add_argument("--panel-review-html", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        scored_sources = _load(args.scored_sources)
        ledger = _load(args.ledger)
        finding_support = _load(args.finding_support)
        documents = {
            "workflow_state": _load(args.workflow_state),
            "brief": _load(args.brief),
            "panel": _load(args.panel),
            "plan": _load(args.plan),
            "scored_sources": scored_sources,
            "evidence_ledger": ledger,
            "finding_support": finding_support,
            "synthesis_matrix": _load(args.synthesis),
            "source_inventory": build_source_inventory(scored_sources=scored_sources, evidence_ledger=ledger),
            "verbatim_inventory": build_verbatim_inventory(evidence_ledger=ledger, finding_support=finding_support),
        }
        population_paths = (
            args.population_frame,
            args.composition_plan,
            args.validity_profile,
        )
        if any(path is not None for path in population_paths):
            if not all(path is not None for path in population_paths):
                raise ContractError(
                    "v3 report rendering requires population frame, "
                    "composition plan, and validity profile together"
                )
            if args.report_inputs is None:
                raise ContractError(
                    "v3 report rendering requires exact report inputs"
                )
            documents.update({
                "population_frame": _load(args.population_frame),
                "composition_plan": _load(args.composition_plan),
                "validity_profile": _load(args.validity_profile),
            })
        report_inputs = _load(args.report_inputs) if args.report_inputs else _release_a_report_inputs(documents)
        if not all(
            path is not None
            for path in (
                args.panel_review_manifest,
                args.panel_summary,
                args.panel_review_html,
            )
        ):
            raise ContractError(
                "panel review manifest, Markdown summary, and HTML review are required together"
            )
        manifest = render_research_report(
            report_inputs=report_inputs, documents=documents,
            generated_at=args.generated_at, output_dir=args.output_dir,
            panel_review_manifest=_load(args.panel_review_manifest),
            panel_review_summary=args.panel_summary.read_bytes(),
            panel_review_html=args.panel_review_html.read_bytes(),
        )
        payload, code = {"status": "rendered", "manifest": manifest, "output_dir": str(args.output_dir)}, 0
    except ContractError as exc:
        message = str(exc)
        collision = "already exists:" in message and "output directory" in message
        payload, code = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": message,
        }, 3 if collision else 2
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
