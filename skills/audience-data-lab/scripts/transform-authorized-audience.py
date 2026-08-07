#!/usr/bin/env python3
"""Transform one approved authorized-audience mapping without clobbering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_data_lab.authorized_transform import transform_authorized_bundle
from audience_data_lab.common import ContractError


def _load_json(path_value: str, label: str) -> dict[str, object]:
    path = Path(path_value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must be one readable UTF-8 JSON document") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--transformer-version", required=True)
    args = parser.parse_args()
    try:
        handoff = transform_authorized_bundle(
            source_profile=_load_json(args.profile, "source profile"),
            mapping=_load_json(args.mapping, "authorized mapping"),
            input_root=Path(args.input_root),
            output_dir=Path(args.output_dir),
            transformer_version=args.transformer_version,
        )
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{Path(args.output_dir) / 'authorized-audience-handoff.json'} {handoff['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
