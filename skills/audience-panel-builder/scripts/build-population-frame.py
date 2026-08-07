#!/usr/bin/env python3
"""Build one canonical population frame and provisional validity profile."""

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
from audience_panel_builder.population.frame import build_population_frame  # noqa: E402
from audience_panel_builder.population.validity import (  # noqa: E402
    assess_population_validity,
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
    parser.add_argument("--frame-request", required=True, type=Path)
    parser.add_argument(
        "--observation-batch", action="append", default=[], type=Path
    )
    parser.add_argument("--overlay-evidence", action="append", default=[], type=Path)
    parser.add_argument("--outcome-feedback", action="append", default=[], type=Path)
    parser.add_argument("--built-at", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validity-output", required=True, type=Path)
    args = parser.parse_args()
    try:
        request = _load(args.frame_request)
        frame = build_population_frame(
            frame_request=request,
            observation_batches=[_load(path) for path in args.observation_batch],
            built_at=args.built_at,
        )
        validity = assess_population_validity(
            frame_request=request,
            population_frame=frame,
            overlay_evidence=[_load(path) for path in args.overlay_evidence],
            outcome_feedback=[_load(path) for path in args.outcome_feedback],
        )
        if args.output == args.validity_output:
            raise ContractError("frame and validity outputs must be different paths")
        if args.output.exists() or args.validity_output.exists():
            raise ContractError(
                "population frame output already exists; existing outputs are never overwritten"
            )
        write_new_bytes(
            args.output,
            canonical_json_bytes(frame),
            "population frame output",
        )
        try:
            write_new_bytes(
                args.validity_output,
                canonical_json_bytes(validity),
                "population validity output",
            )
        except Exception:
            # Do not claim transactional publication; surface the incomplete pair.
            raise
        payload, code = {
            "status": "built",
            "frame": str(args.output),
            "validity": str(args.validity_output),
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
