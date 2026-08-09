#!/usr/bin/env python3
"""Freeze reviewed C2 creative attributes before real outcome access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    write_new_bytes,
)
from audience_panel_builder.population.experimental_calibration.attributes import (  # noqa: E402
    build_creative_attribute_registry,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        try:
            value = json.loads(args.input.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                "creative attribute input must be readable UTF-8 JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ContractError("creative attribute input must contain an object")
        registry = build_creative_attribute_registry(**value)
        write_new_bytes(
            args.output,
            canonical_json_bytes(registry),
            "C2 creative attribute registry",
        )
        payload = {
            "status": "registered_before_outcome_access",
            "registry_id": registry["registry_id"],
            "registry_sha256": registry["registry_sha256"],
            "output": str(args.output),
        }
        code = 0
    except (ContractError, OSError, TypeError) as exc:
        payload = {"status": "error", "error": "validation", "message": str(exc)}
        code = 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
