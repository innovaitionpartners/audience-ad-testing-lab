#!/usr/bin/env python3
"""Materialize one exact proposal-approved v2 audience package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(CURRENT_SCRIPTS))
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_package import (  # noqa: E402
    PackageSafetyError,
    PackageValidationError,
    build_audience_package,
)
from audience_lab.audience_research import AudienceResearchValidationError  # noqa: E402
from audience_panel_builder.approval_gate import (  # noqa: E402
    build_package_proposal,
    require_package_build_ready,
)
from audience_panel_builder.common import ContractError, canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.construction_audit import (  # noqa: E402
    require_passing_construction_audit_for_documents,
)


class ArgumentParseError(ValueError):
    pass


class CanonicalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParseError(message)


def _load(path: Path, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must contain a JSON object")
    return payload


def main() -> int:
    parser = CanonicalArgumentParser(
        description="Build an exact approved Audience Panel Builder package."
    )
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--finding-support", required=True, type=Path)
    parser.add_argument("--synthesis", required=True, type=Path)
    parser.add_argument("--report-manifest", required=True, type=Path)
    parser.add_argument("--panel-review-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    try:
        args = parser.parse_args()
        workflow_state = _load(args.workflow_state, "workflow state")
        construction_audit = _load(args.audit, "construction audit")
        proposal = build_package_proposal(
            workflow_state=workflow_state,
            construction_audit=construction_audit,
            brief_path=args.brief,
            panel_path=args.panel,
            ledger_path=args.ledger,
            finding_support_path=args.finding_support,
            synthesis_path=args.synthesis,
            report_manifest_path=args.report_manifest,
            panel_review_manifest_path=args.panel_review_manifest,
        )
        audit_result = require_passing_construction_audit_for_documents(
            construction_audit,
            brief=_load(args.brief, "brief"),
            panel=_load(args.panel, "panel"),
            evidence_ledger=_load(args.ledger, "evidence ledger"),
            finding_support=_load(
                args.finding_support,
                "finding support",
            ),
            synthesis_matrix=_load(args.synthesis, "synthesis matrix"),
            report_manifest=_load(
                args.report_manifest,
                "report manifest",
            ),
            panel_review_manifest=_load(
                args.panel_review_manifest,
                "panel review manifest",
            ),
        )
        require_package_build_ready(
            workflow_state=workflow_state,
            panel_id=str(proposal["panel_id"]),
            panel_version=str(proposal["panel_version"]),
            brief_sha256=str(proposal["brief_sha256"]),
            panel_sha256=str(proposal["panel_sha256"]),
            synthesis_sha256=str(audit_result["synthesis_matrix_sha256"]),
            report_inputs_sha256=str(audit_result["report_inputs_sha256"]),
            audit_sha256=str(audit_result["audit_sha256"]),
            panel_review_manifest_sha256=str(
                audit_result["panel_review_manifest_sha256"]
            ),
            proposed_package_sha256=str(proposal["package_sha256"]),
        )
        brief = _load(args.brief, "brief")
        panel = _load(args.panel, "panel")
        if sha256_json(brief).removeprefix("sha256:") != proposal["brief_sha256"]:
            raise ContractError("brief changed after package proposal calculation")
        if sha256_json(panel).removeprefix("sha256:") != proposal["panel_sha256"]:
            raise ContractError("panel changed after package proposal calculation")
        payload = build_audience_package(
            brief,
            panel,
            args.output_dir,
            generator_version="1.0.0",
        ).to_dict()
        code = 0
    except PackageSafetyError as exc:
        payload, code = {
            "status": "error",
            "error": "package_safety",
            "message": str(exc),
        }, 6
    except (
        ArgumentParseError,
        ContractError,
        AudienceResearchValidationError,
        PackageValidationError,
        json.JSONDecodeError,
        UnicodeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        payload, code = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
