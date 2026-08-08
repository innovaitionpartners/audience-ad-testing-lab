#!/usr/bin/env python3
"""Install the plugin through real client marketplaces and verify the package."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


MARKETPLACE = "innovaition-ad-testing"
PLUGIN = "audience-ad-testing-lab"
PLUGIN_ID = f"{PLUGIN}@{MARKETPLACE}"
VERSION = "1.0.0"
EXPECTED_SKILLS = (
    "audience-ad-testing-lab",
    "audience-data-lab",
    "audience-panel-builder",
    "real-world-outcome-data-prep",
)


class SmokeFailure(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise SmokeFailure(
            f"command failed with {result.returncode}: {' '.join(command)}"
        )
    return result


def json_output(command: list[str], *, env: dict[str, str] | None = None) -> object:
    result = run(command, env=env)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"command did not return JSON: {' '.join(command)}") from exc


def require_client(name: str) -> None:
    if shutil.which(name) is None:
        raise SmokeFailure(f"required client is unavailable: {name}")


def assert_skill_tree(plugin_root: Path) -> None:
    for skill in EXPECTED_SKILLS:
        skill_file = plugin_root / "skills" / skill / "SKILL.md"
        if not skill_file.is_file():
            raise SmokeFailure(f"installed skill is missing: {skill_file}")


def verify_installed_runtime(plugin_root: Path) -> None:
    scripts = plugin_root / "skills" / "real-world-outcome-data-prep" / "scripts"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(scripts)
    code = (
        "from outcome_data_prep.runtime_guard import require_approved_runtime; "
        "identity = require_approved_runtime('validate_study'); "
        "assert identity.release_version == '0.3.1'; "
        "print(identity.release_tree_sha256)"
    )
    run([sys.executable, "-c", code], env=env)


def smoke_claude(plugin_root: Path) -> None:
    require_client("claude")
    with tempfile.TemporaryDirectory(prefix="audience-claude-plugin-") as config:
        env = os.environ.copy()
        env["CLAUDE_CONFIG_DIR"] = config
        run(
            [
                "claude",
                "plugin",
                "marketplace",
                "add",
                str(plugin_root),
                "--scope",
                "user",
            ],
            env=env,
        )
        run(
            ["claude", "plugin", "install", PLUGIN_ID, "--scope", "user"],
            env=env,
        )
        installed = json_output(["claude", "plugin", "list", "--json"], env=env)
        if not isinstance(installed, list):
            raise SmokeFailure("Claude plugin list must be an array")
        match = next(
            (
                item
                for item in installed
                if isinstance(item, dict) and item.get("id") == PLUGIN_ID
            ),
            None,
        )
        if match is None or match.get("enabled") is not True:
            raise SmokeFailure("Claude marketplace installation is not enabled")
        if match.get("version") != VERSION:
            raise SmokeFailure("Claude installed the wrong plugin version")
        install_path = match.get("installPath")
        if not isinstance(install_path, str):
            raise SmokeFailure("Claude did not report an installation path")
        installed_root = Path(install_path).resolve(strict=True)
        assert_skill_tree(installed_root)

        details = run(["claude", "plugin", "details", PLUGIN_ID], env=env).stdout
        if "Skills (4)" not in details:
            raise SmokeFailure("Claude did not discover exactly four skills")
        for skill in EXPECTED_SKILLS:
            if skill not in details:
                raise SmokeFailure(f"Claude inventory omitted {skill}")
        verify_installed_runtime(installed_root)


def _codex_marketplaces() -> list[dict[str, object]]:
    payload = json_output(["codex", "plugin", "marketplace", "list", "--json"])
    if not isinstance(payload, dict) or not isinstance(payload.get("marketplaces"), list):
        raise SmokeFailure("Codex marketplace list has an unexpected shape")
    return [item for item in payload["marketplaces"] if isinstance(item, dict)]


def _portable_package_copy(plugin_root: Path, destination: Path) -> Path:
    packaged = destination / PLUGIN
    shutil.copytree(
        plugin_root,
        packaged,
        ignore=shutil.ignore_patterns(
            ".git",
            ".DS_Store",
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".cache",
            ".mypy_cache",
            ".nox",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            ".venv",
            "env",
            "venv",
            "tmp",
        ),
    )
    return packaged


def _smoke_codex_from_package(packaged_root: Path) -> None:
    require_client("codex")
    if any(item.get("name") == MARKETPLACE for item in _codex_marketplaces()):
        raise SmokeFailure(
            f"Codex marketplace {MARKETPLACE} already exists; use a clean test environment"
        )

    marketplace_added = False
    plugin_added = False
    try:
        run(
            [
                "codex",
                "plugin",
                "marketplace",
                "add",
                str(packaged_root),
                "--json",
            ]
        )
        marketplace_added = True
        run(["codex", "plugin", "add", PLUGIN_ID, "--json"])
        plugin_added = True

        payload = json_output(["codex", "plugin", "list", "--json"])
        if not isinstance(payload, dict) or not isinstance(payload.get("installed"), list):
            raise SmokeFailure("Codex plugin list has an unexpected shape")
        match = next(
            (
                item
                for item in payload["installed"]
                if isinstance(item, dict) and item.get("pluginId") == PLUGIN_ID
            ),
            None,
        )
        if (
            match is None
            or match.get("installed") is not True
            or match.get("enabled") is not True
        ):
            raise SmokeFailure("Codex marketplace installation is not enabled")
        if match.get("version") != VERSION:
            raise SmokeFailure("Codex installed the wrong plugin version")
        source = match.get("source")
        install_path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(install_path, str):
            raise SmokeFailure("Codex did not report an installed plugin path")
        installed_root = Path(install_path).resolve(strict=True)
        assert_skill_tree(installed_root)
        verify_installed_runtime(installed_root)
    finally:
        if plugin_added:
            run(["codex", "plugin", "remove", PLUGIN_ID, "--json"], check=False)
        if marketplace_added:
            run(
                [
                    "codex",
                    "plugin",
                    "marketplace",
                    "remove",
                    MARKETPLACE,
                    "--json",
                ],
                check=False,
            )


def smoke_codex(plugin_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="audience-codex-plugin-") as staging:
        packaged_root = _portable_package_copy(plugin_root, Path(staging))
        _smoke_codex_from_package(packaged_root)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--plugin-root", type=Path, default=Path.cwd())
    result.add_argument(
        "--clients",
        nargs="+",
        choices=("claude", "codex"),
        default=("claude", "codex"),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    plugin_root = args.plugin_root.expanduser().resolve(strict=True)
    assert_skill_tree(plugin_root)
    for client in args.clients:
        if client == "claude":
            smoke_claude(plugin_root)
        elif client == "codex":
            smoke_codex(plugin_root)
    print("Plugin installation smoke test passed: " + ", ".join(args.clients))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
