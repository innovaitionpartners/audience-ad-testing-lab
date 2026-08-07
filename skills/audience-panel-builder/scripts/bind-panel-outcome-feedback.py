#!/usr/bin/env python3
"""Bind canonical aggregate outcome feedback to one saved panel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.population.feedback import (  # noqa: E402
    bind_outcome_feedback,
)


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path} is not readable canonical JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--feedback", action="append", required=True, type=Path)
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--bound-at", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        binding = bind_outcome_feedback(
            panel=_load(args.panel),
            feedback_documents=[_load(path) for path in args.feedback],
            binding_id=args.binding_id,
            bound_at=args.bound_at,
        )
        write_new_bytes(
            args.output,
            canonical_json_bytes(binding),
            "panel outcome feedback binding",
        )
        payload, code = {
            "status": "bound",
            "output": str(args.output),
            "binding_sha256": binding["binding_sha256"],
        }, 0
    except ContractError as exc:
        collision = "already exists" in str(exc)
        payload, code = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": str(exc),
        }, 3 if collision else 2
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        payload, code = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
