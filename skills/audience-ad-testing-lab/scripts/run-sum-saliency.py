#!/usr/bin/env python3
"""Blocking wrapper for mandatory imagery SUM saliency inference.

This helper does not install SUM or download model weights. It runs an existing
SUM checkout. Any missing provider, failed inference, or missing output exits
nonzero so an imagery workflow cannot render without complete heatmap evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


CONDITION_LABELS = {
    0: "natural_scene_mouse",
    1: "natural_scene_eye_tracking",
    2: "e_commercial",
    3: "user_interface",
}


def emit(payload: dict, exit_code: int = 0) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


def valid_png(path: Path) -> bool:
    """Require a real PNG header and positive IHDR dimensions."""

    try:
        header = path.read_bytes()[:24]
    except OSError:
        return False
    return (
        len(header) == 24
        and header[:8] == b"\x89PNG\r\n\x1a\n"
        and header[12:16] == b"IHDR"
        and struct.unpack(">I", header[8:12])[0] == 13
        and struct.unpack(">I", header[16:20])[0] > 0
        and struct.unpack(">I", header[20:24])[0] > 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run mandatory SUM saliency inference for an ad image.")
    parser.add_argument("--img-path", required=True, type=Path, help="Image, thumbnail, keyframe, or screenshot path.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for generated saliency outputs.")
    parser.add_argument("--condition", required=True, type=int, choices=sorted(CONDITION_LABELS), help="SUM condition 0-3.")
    parser.add_argument("--heat-map-type", default="Overlay", choices=["HOT", "Overlay"], help="SUM heatmap output mode.")
    parser.add_argument("--sum-repo", type=Path, default=None, help="Path to an existing Arhosseini77/SUM checkout.")
    parser.add_argument("--from-pretrained", default="", help="Optional Hugging Face model id passed through to SUM.")
    args = parser.parse_args()

    image_path = args.img_path.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    sum_repo = args.sum_repo or (Path(os.environ["SUM_REPO"]) if os.environ.get("SUM_REPO") else None)

    if not image_path.exists() or not image_path.is_file():
        return emit({
            "status": "blocked",
            "blocking": True,
            "provider": "sum",
            "reason": "image_not_found",
            "source_image_path": str(image_path),
        }, 4)

    if sum_repo is None:
        return emit({
            "status": "blocked",
            "blocking": True,
            "provider": "sum",
            "reason": "Set --sum-repo or SUM_REPO to an existing Arhosseini77/SUM checkout.",
            "source_image_path": str(image_path),
            "sum_condition": args.condition,
            "sum_condition_label": CONDITION_LABELS[args.condition],
        }, 4)

    sum_repo = sum_repo.expanduser().resolve()
    inference_script = sum_repo / "inference.py"
    if not inference_script.exists():
        return emit({
            "status": "blocked",
            "blocking": True,
            "provider": "sum",
            "reason": f"SUM inference.py not found at {inference_script}",
            "source_image_path": str(image_path),
            "sum_repo": str(sum_repo),
            "sum_condition": args.condition,
            "sum_condition_label": CONDITION_LABELS[args.condition],
        }, 4)

    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(inference_script),
        "--img_path",
        str(image_path),
        "--condition",
        str(args.condition),
        "--output_path",
        str(output_dir),
        "--heat_map_type",
        args.heat_map_type,
    ]
    if args.from_pretrained:
        command.extend(["--from_pretrained", args.from_pretrained])

    completed = subprocess.run(
        command,
        cwd=str(sum_repo),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    stem = image_path.stem
    saliency_path = output_dir / f"{stem}_saliencymap.png"
    overlay_path = output_dir / f"{stem}_overlay.png"
    saliency_valid = valid_png(saliency_path)
    overlay_valid = valid_png(overlay_path)
    generated = completed.returncode == 0 and saliency_valid and overlay_valid

    payload = {
        "status": "generated" if generated else "blocked",
        "blocking": not generated,
        "provider": "sum",
        "source_image_path": str(image_path),
        "sum_repo": str(sum_repo),
        "sum_condition": args.condition,
        "sum_condition_label": CONDITION_LABELS[args.condition],
        "heat_map_type": args.heat_map_type,
        "saliency_map_path": str(saliency_path) if saliency_valid else "",
        "overlay_path": str(overlay_path) if overlay_valid else "",
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "returncode": completed.returncode,
    }
    if not generated:
        if completed.returncode != 0:
            payload["reason"] = "sum_inference_failed"
        elif not saliency_valid:
            payload["reason"] = "saliency_output_missing_or_invalid"
        else:
            payload["reason"] = "overlay_output_missing_or_invalid"
    return emit(payload, 0 if generated else 4)


if __name__ == "__main__":
    raise SystemExit(main())
