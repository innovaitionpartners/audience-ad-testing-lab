#!/usr/bin/env python3
"""Score and gate candidate research sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.source_scoring import score_source_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Score Audience Panel Builder source candidates.")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path)
    try:
        args = parser.parse_args()
        candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
        result = score_source_candidates(candidates)
        write_new_bytes(
            args.output,
            canonical_json_bytes(result),
            "scored audience sources",
        )
        payload, code = {
            "status": "scored",
            "output": str(args.output),
            "summary": result["summary"],
        }, 0
    except (ContractError, json.JSONDecodeError, UnicodeError, OSError, ValueError) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
