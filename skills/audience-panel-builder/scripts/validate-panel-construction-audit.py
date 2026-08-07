#!/usr/bin/env python3
"""Validate one blind Audience Panel Builder construction audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_SCRIPTS))

from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.construction_audit import (  # noqa: E402
    validate_construction_audit_for_documents,
)


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--finding-support", required=True, type=Path)
    parser.add_argument("--synthesis", required=True, type=Path)
    parser.add_argument("--report-manifest", required=True, type=Path)
    parser.add_argument("--panel-review-manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        documents = {
            "brief": _load(args.brief), "panel": _load(args.panel), "ledger": _load(args.ledger),
            "finding_support": _load(args.finding_support), "synthesis": _load(args.synthesis),
            "report_manifest": _load(args.report_manifest),
            "panel_review_manifest": _load(args.panel_review_manifest),
        }
        result = validate_construction_audit_for_documents(
            _load(args.audit),
            brief=documents["brief"],
            panel=documents["panel"],
            evidence_ledger=documents["ledger"],
            finding_support=documents["finding_support"],
            synthesis_matrix=documents["synthesis"],
            report_manifest=documents["report_manifest"],
            panel_review_manifest=documents["panel_review_manifest"],
        )
        payload, code = {
            "valid": True,
            "result": result["audit"]["result"],
            "audit_sha256": result["audit_sha256"],
        }, 0
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        payload, code = {"valid": False, "result": None, "audit_sha256": None, "error": str(exc)}, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
