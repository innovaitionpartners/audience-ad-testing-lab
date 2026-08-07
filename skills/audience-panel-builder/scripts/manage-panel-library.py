#!/usr/bin/env python3
"""Register and inspect co-shipped immutable audience packages locally."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys


SHARED_COMMAND = (
    Path(__file__).resolve().parents[2]
    / "audience-ad-testing-lab"
    / "scripts"
    / "manage-audience-library.py"
)


if __name__ == "__main__":
    sys.path.insert(0, str(SHARED_COMMAND.parent))
    runpy.run_path(str(SHARED_COMMAND), run_name="__main__")
