#!/usr/bin/env python3
"""Register one exact approval-bound reusable audience package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile


CURRENT_SCRIPTS = Path(__file__).resolve().parent
SIBLING_SCRIPTS = CURRENT_SCRIPTS.parents[1] / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(CURRENT_SCRIPTS))
sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_library import (  # noqa: E402
    ImmutableVersionConflict,
    LibraryLockError,
    LibrarySafetyError,
    register_package,
)
from audience_lab.audience_package import (  # noqa: E402
    PackageSafetyError,
    PackageValidationError,
    validate_package_archive,
)
from audience_panel_builder.approval_gate import (  # noqa: E402
    require_registration_ready,
)
from audience_panel_builder.common import ContractError, canonical_json_bytes  # noqa: E402
from audience_panel_builder.construction_audit import (  # noqa: E402
    require_passing_construction_audit_for_documents,
)


class ArgumentParseError(ValueError):
    pass


class CanonicalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParseError(message)


def _load(path: Path, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must contain a JSON object")
    return payload


def _require_safe_package_path(path: Path) -> Path:
    if ".." in path.parts:
        raise PackageSafetyError(
            "archive path must not contain parent-directory traversal"
        )
    candidate = path.absolute()
    current = Path(candidate.anchor)
    platform_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
    }
    for part in candidate.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            permitted_target = platform_aliases.get(current)
            if permitted_target is None or current.resolve() != permitted_target:
                raise PackageSafetyError(
                    f"archive path contains a symlink component: {current}"
                )
    if not candidate.is_file():
        raise PackageSafetyError(
            "archive path must be a regular file, not a symlink"
        )
    return candidate


def _register_approved_package(
    *,
    workflow_state: dict[str, object],
    construction_audit: dict[str, object],
    evidence_ledger: dict[str, object],
    finding_support: dict[str, object],
    synthesis_matrix: dict[str, object],
    report_manifest: dict[str, object],
    panel_review_manifest: dict[str, object],
    package_path: Path,
    library_root: Path | None,
) -> dict[str, object]:
    safe_path = _require_safe_package_path(package_path)
    package_snapshot = safe_path.read_bytes()
    package_validation = validate_package_archive(package_snapshot)
    try:
        with zipfile.ZipFile(io.BytesIO(package_snapshot)) as archive:
            packaged_brief = json.loads(
                archive.read("persona-research-brief.json").decode("utf-8")
            )
            packaged_panel = json.loads(
                archive.read("saved-audience-panel.json").decode("utf-8")
            )
    except (
        zipfile.BadZipFile,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        raise PackageValidationError(
            "validated package snapshot could not be read"
        ) from exc
    audit_result = require_passing_construction_audit_for_documents(
        construction_audit,
        brief=packaged_brief,
        panel=packaged_panel,
        evidence_ledger=evidence_ledger,
        finding_support=finding_support,
        synthesis_matrix=synthesis_matrix,
        report_manifest=report_manifest,
        panel_review_manifest=panel_review_manifest,
    )
    package_sha256 = hashlib.sha256(package_snapshot).hexdigest()
    require_registration_ready(
        workflow_state=workflow_state,
        panel_id=str(package_validation["panel_id"]),
        panel_version=str(package_validation["panel_version"]),
        brief_sha256=str(audit_result["brief_sha256"]),
        panel_sha256=str(audit_result["panel_sha256"]),
        synthesis_sha256=str(audit_result["synthesis_matrix_sha256"]),
        report_inputs_sha256=str(audit_result["report_inputs_sha256"]),
        package_sha256=package_sha256,
        audit_sha256=str(audit_result["audit_sha256"]),
        panel_review_manifest_sha256=str(
            audit_result["panel_review_manifest_sha256"]
        ),
    )
    return register_package(
        package_snapshot,
        library_root=library_root,
    )


def main() -> int:
    parser = CanonicalArgumentParser(
        description="Register an exact approved Audience Panel Builder package."
    )
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--finding-support", required=True, type=Path)
    parser.add_argument("--synthesis", required=True, type=Path)
    parser.add_argument("--report-manifest", required=True, type=Path)
    parser.add_argument("--panel-review-manifest", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--library-root", type=Path)
    try:
        args = parser.parse_args()
        workflow_state = _load(args.workflow_state, "workflow state")
        construction_audit = _load(args.audit, "construction audit")
        payload = _register_approved_package(
            workflow_state=workflow_state,
            construction_audit=construction_audit,
            evidence_ledger=_load(args.ledger, "evidence ledger"),
            finding_support=_load(
                args.finding_support,
                "finding support",
            ),
            synthesis_matrix=_load(args.synthesis, "synthesis matrix"),
            report_manifest=_load(
                args.report_manifest,
                "report manifest",
            ),
            panel_review_manifest=_load(
                args.panel_review_manifest,
                "panel review manifest",
            ),
            package_path=args.package,
            library_root=args.library_root,
        )
        code = 0
    except ImmutableVersionConflict as exc:
        payload, code = {
            "status": "error",
            "error": "immutable_version_conflict",
            "message": str(exc),
        }, 3
    except (LibrarySafetyError, PackageSafetyError) as exc:
        payload, code = {
            "status": "error",
            "error": "package_safety",
            "message": str(exc),
        }, 6
    except LibraryLockError as exc:
        payload, code = {
            "status": "error",
            "error": "library_lock",
            "message": str(exc),
        }, 7
    except (
        ArgumentParseError,
        ContractError,
        PackageValidationError,
        json.JSONDecodeError,
        UnicodeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ) as exc:
        payload, code = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }, 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
