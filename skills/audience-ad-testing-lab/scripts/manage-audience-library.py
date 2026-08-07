#!/usr/bin/env python3
"""Register and inspect immutable Ad Testing Lab audience packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audience_lab.audience_library import (
    AudienceResolutionBlocked, ImmutableVersionConflict, LibraryLockError,
    LibraryNotFoundError, LibrarySafetyError, find_package, list_panels, register_package,
    resolve_audience_panel, show_panel, validate_audience_intake,
)
from audience_lab.audience_package import (
    PACKAGE_SCHEMA_VERSION, PackageSafetyError, PackageValidationError,
    read_safe_archive_manifest,
)
from audience_lab.audience_resolution_v3 import _resolve_audience_v3


class ArgumentParseError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParseError(message)


def main() -> int:
    parser = JsonArgumentParser(
        description=(
            "Register, inspect, and resolve immutable Ad Testing Lab audience packages. "
            "Set AUDIENCE_LAB_LIBRARY_DIR to an absolute path to isolate a temporary library."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser(
        "register", help="Register one approved research-backed package immutably."
    )
    register.add_argument("package", type=Path, help="Audience package ZIP to register.")
    commands.add_parser("list", help="List every registered panel and immutable version.")
    show = commands.add_parser("show", help="Show one registered panel/version record.")
    show.add_argument("panel_id", help="Registered panel identifier.")
    show.add_argument("version", help="Exact semantic version, such as 1.0.0.")
    resolve = commands.add_parser(
        "resolve", help="Check study compatibility and create a run-local snapshot."
    )
    resolve.add_argument("audience_intake", type=Path, help="Audience intake JSON file.")
    resolve.add_argument("study_scope", type=Path, help="New study scope JSON file.")
    resolve.add_argument("run_dir", type=Path, help="Study run directory for the snapshot.")
    resolve.add_argument(
        "--refresh-trigger", action="append", default=[],
        help="Known refresh trigger to evaluate; repeat for multiple triggers.",
    )
    try:
        args = parser.parse_args()
        if args.command == "register":
            payload = register_package(args.package)
        elif args.command == "list":
            payload = list_panels()
        elif args.command == "show":
            payload = show_panel(args.panel_id, args.version)
        else:
            intake = validate_audience_intake(
                json.loads(args.audience_intake.read_text(encoding="utf-8"))
            )
            if intake["route"] != "audience_panel":
                raise ArgumentParseError(
                    "resolve requires the exact saved-panel or file-package audience route"
                )
            scope = json.loads(args.study_scope.read_text(encoding="utf-8"))
            source = intake["value"]
            package_path = (
                find_package(source["panel_id"], source["version"])
                if source["source"] == "library"
                else Path(source["package_path"]).expanduser()
            )
            _raw, manifest_bytes = read_safe_archive_manifest(package_path)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            package_schema = (
                manifest.get("schema_version")
                if isinstance(manifest, dict)
                else None
            )
            if package_schema != PACKAGE_SCHEMA_VERSION:
                payload = _resolve_audience_v3(
                    package_path=package_path,
                    study_scope=scope,
                    run_directory=args.run_dir,
                    explicit_refresh_triggers=args.refresh_trigger,
                    now=None,
                )
                code = 0 if payload["resolution_status"] == "ready" else 5
            else:
                payload = resolve_audience_panel(
                    source, scope, run_dir=args.run_dir,
                    explicit_refresh_triggers=args.refresh_trigger,
                )
                code = 0
        if args.command != "resolve":
            code = 0
    except AudienceResolutionBlocked as exc:
        payload, code = exc.result, 5
    except ImmutableVersionConflict as exc:
        payload, code = {"status": "error", "error": "immutable_version_conflict", "message": str(exc)}, 3
    except LibraryNotFoundError as exc:
        payload, code = {"status": "error", "error": "not_found", "message": str(exc)}, 4
    except (LibrarySafetyError, PackageSafetyError) as exc:
        payload, code = {"status": "error", "error": "package_safety", "message": str(exc)}, 6
    except LibraryLockError as exc:
        payload, code = {"status": "error", "error": "library_lock", "message": str(exc)}, 7
    except (
        ArgumentParseError, PackageValidationError, OSError, UnicodeError,
        json.JSONDecodeError, ValueError,
    ) as exc:
        payload, code = {"status": "error", "error": "validation", "message": str(exc)}, 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
