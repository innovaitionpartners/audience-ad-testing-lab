#!/usr/bin/env python3
"""Normalize Last30Days or mapped social data into one strict batch contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.social import normalize_last30days, normalize_mapped_export


def _load_json_or_jsonl(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        values = [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
        if not values:
            raise
        return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize social evidence for Audience Panel Builder.")
    parser.add_argument("adapter", choices=("last30days", "mapped"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mapping", type=Path)
    try:
        args = parser.parse_args()
        source = _load_json_or_jsonl(args.input)
        if args.adapter == "last30days":
            if args.mapping is not None:
                raise ContractError("--mapping is not allowed with last30days")
            result = normalize_last30days(source)
        else:
            if args.mapping is None:
                raise ContractError("--mapping is required with mapped")
            mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
            result = normalize_mapped_export(source, mapping)
        write_new_bytes(
            args.output,
            canonical_json_bytes(result),
            "normalized social evidence batch",
        )
        payload, code = {
            "status": "normalized",
            "output": str(args.output),
            "batch_id": result["batch_id"],
            "observations": len(result["observations"]),
            "coverage_warnings": len(result["coverage_warnings"]),
        }, 0
    except (ContractError, json.JSONDecodeError, UnicodeError, OSError, ValueError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
