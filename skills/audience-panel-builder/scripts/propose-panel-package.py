#!/usr/bin/env python3
"""Calculate one canonical package proposal without retaining package bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(CURRENT_SCRIPTS))
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_package import PackageSafetyError, PackageValidationError  # noqa: E402
from audience_lab.audience_research import AudienceResearchValidationError  # noqa: E402
from audience_panel_builder.approval_gate import build_package_proposal  # noqa: E402
from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402


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
        description="Calculate an approved panel package proposal without materializing it."
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
    try:
        args = parser.parse_args()
        payload = build_package_proposal(
            workflow_state=_load(args.workflow_state, "workflow state"),
            construction_audit=_load(args.audit, "construction audit"),
            brief_path=args.brief,
            panel_path=args.panel,
            ledger_path=args.ledger,
            finding_support_path=args.finding_support,
            synthesis_path=args.synthesis,
            report_manifest_path=args.report_manifest,
            panel_review_manifest_path=args.panel_review_manifest,
        )
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
