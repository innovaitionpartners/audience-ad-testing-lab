#!/usr/bin/env python3
"""Generate the closed, deterministic calibration stage manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Root directory of the plugin repository.",
    )
    args = parser.parse_args(argv)

    plugin_root = args.plugin_root.resolve(strict=True)
    panel_builder_scripts = plugin_root / "skills" / "audience-panel-builder" / "scripts"

    if str(panel_builder_scripts) not in sys.path:
        sys.path.insert(0, str(panel_builder_scripts))

    from audience_panel_builder.common import canonical_json_bytes as pb_canonical_bytes
    from experimental_persona_calibration_oracle.sandbox import (
        _ENTRYPOINTS,
        _discover_closure,
    )

    manifests_dir = (
        panel_builder_scripts
        / "audience_panel_builder"
        / "population"
        / "experimental_calibration"
        / "private_stage_manifests"
    )

    for name, entrypoint in _ENTRYPOINTS.items():
        cli_path = panel_builder_scripts / entrypoint.cli
        print(f"Discovering closure for {name} using {cli_path.name}...")
        files = _discover_closure(
            cli_path,
            omitted_initializers=entrypoint.namespace_packages,
        )
        document = {
            "engine_entrypoint": name,
            "files": files,
            "schema_version": "experimental-calibration-source-allowlist-v1",
            "source_manifest_sha256": None,
        }
        sha = "sha256:" + hashlib.sha256(pb_canonical_bytes(document)).hexdigest()
        document["source_manifest_sha256"] = sha

        out_path = manifests_dir / f"{name}.json"
        print(f"Writing manifest to {out_path}...")
        out_path.write_bytes(pb_canonical_bytes(document))

    print("All calibration stage manifests successfully regenerated!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
