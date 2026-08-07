#!/usr/bin/env python3
"""Plan exact population sources from a frame request and registry v2."""

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
)
from audience_panel_builder.population.registry import (  # noqa: E402
    route_population_sources,
)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON input: {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame-request", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        frame_request = _read_json(args.frame_request)
        registry = _read_json(args.registry)
        capabilities = _read_json(args.capabilities)
        if not isinstance(frame_request, dict):
            raise ContractError("frame request must be an object")
        if not isinstance(registry, dict):
            raise ContractError("source registry must be an object")
        if not isinstance(capabilities, dict):
            raise ContractError("capabilities must be an object")
        plan = route_population_sources(
            frame_request=frame_request,
            registry=registry,
            capabilities=capabilities,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(canonical_json_bytes(plan))
    except (ContractError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{args.output} {plan['schema_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
