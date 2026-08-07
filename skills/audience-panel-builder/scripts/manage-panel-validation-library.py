#!/usr/bin/env python3
"""Register and inspect immutable Tier 4 validation claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from audience_panel_builder.population.validation.library import (  # noqa: E402
    ImmutableVersionConflict, LibraryError, LibraryLockError, LibraryNotFoundError,
    append_claim_lifecycle_event, claim_lifecycle_status, current_claim, list_claims,
    register_validation_package, show_claim,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    load_trusted_authority_registry,
    read_protected_authority_secret,
)


def _emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-registry", required=True, type=Path)
    parser.add_argument("--authority-secret-file", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register"); register.add_argument("package", type=Path); register.add_argument("--registered-at", required=True); register.add_argument("--library-root", required=True, type=Path)
    listing = commands.add_parser("list"); listing.add_argument("--library-root", required=True, type=Path)
    show = commands.add_parser("show"); show.add_argument("claim_id"); show.add_argument("--library-root", required=True, type=Path)
    status = commands.add_parser("status"); status.add_argument("claim_id"); status.add_argument("--as-of", required=True); status.add_argument("--library-root", required=True, type=Path)
    current = commands.add_parser("current"); current.add_argument("panel_id"); current.add_argument("panel_version"); current.add_argument("claim_scope_sha256"); current.add_argument("--as-of", required=True); current.add_argument("--library-root", required=True, type=Path)
    transition = commands.add_parser("transition")
    transition.add_argument("claim_id"); transition.add_argument("event_type", choices=("expired", "superseded", "withdrawn", "invalidated")); transition.add_argument("--effective-at", required=True); transition.add_argument("--actor-id", required=True); transition.add_argument("--reason", required=True); transition.add_argument("--evidence-sha256", required=True, action="append"); transition.add_argument("--replacement-claim-id"); transition.add_argument("--library-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        registry = load_trusted_authority_registry(
            args.authority_registry,
            authority_secret=read_protected_authority_secret(
                args.authority_secret_file,
            ),
        )
        if args.command == "register": payload = register_validation_package(args.package, library_root=args.library_root, registered_at=args.registered_at, authority_registry=registry)
        elif args.command == "list": payload = list_claims(library_root=args.library_root, authority_registry=registry)
        elif args.command == "show": payload = show_claim(args.claim_id, library_root=args.library_root, authority_registry=registry)
        elif args.command == "status": payload = claim_lifecycle_status(args.claim_id, library_root=args.library_root, as_of=args.as_of, authority_registry=registry)
        elif args.command == "current": payload = current_claim(args.panel_id, args.panel_version, args.claim_scope_sha256, library_root=args.library_root, as_of=args.as_of, authority_registry=registry)
        else: payload = append_claim_lifecycle_event(claim_id=args.claim_id, event_type=args.event_type, effective_at=args.effective_at, actor_id=args.actor_id, reason=args.reason, evidence_sha256=args.evidence_sha256, replacement_claim_id=args.replacement_claim_id, library_root=args.library_root, authority_registry=registry)
        _emit(payload); return 0
    except ImmutableVersionConflict as exc: _emit({"status": "error", "error": "immutable_version_conflict", "message": str(exc)}); return 3
    except LibraryNotFoundError as exc: _emit({"status": "error", "error": "not_found", "message": str(exc)}); return 4
    except LibraryLockError as exc: _emit({"status": "error", "error": "library_lock", "message": str(exc)}); return 7
    except (LibraryError, OSError, ValueError) as exc: _emit({"status": "error", "error": "validation", "message": str(exc)}); return 2


if __name__ == "__main__":
    raise SystemExit(main())
