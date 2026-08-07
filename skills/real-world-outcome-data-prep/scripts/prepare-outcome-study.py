#!/usr/bin/env python3
"""Draft or seal one real-world outcome study without overwriting outputs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


CURRENT_SCRIPTS = Path(__file__).resolve().parent
if str(CURRENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CURRENT_SCRIPTS))

from outcome_data_prep.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
)
from outcome_data_prep.registration import (  # noqa: E402
    RegistrationDraft,
    build_registration_draft,
    seal_study_registration,
)
from outcome_data_prep.runtime_guard import (  # noqa: E402
    RuntimeGuardError,
    require_approved_runtime,
)
from outcome_data_prep.study_authority import StudyAuthorityError  # noqa: E402


PANEL_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "audience-panel-builder" / "scripts"
)
if str(PANEL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PANEL_SCRIPTS))

from audience_panel_builder.population.validation.package import (  # noqa: E402
    _rename_directory_no_replace,
)


_SETUP_KEYS = {"run_root", "panel_package", "campaign_plans", "supplied_facts"}
_DRAFT_KEYS = {
    "preregistration",
    "delivery_map",
    "creative_manifest",
    "study_summary",
    "evidence_status",
    "unresolved_questions",
}


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain an object")
    return value


def _resolve_path(value: object, *, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a path string")
    selected = Path(value)
    return selected if selected.is_absolute() else base / selected


def _draft_payload(draft: RegistrationDraft) -> dict[str, object]:
    return {
        "preregistration": draft.preregistration,
        "delivery_map": draft.delivery_map,
        "creative_manifest": draft.creative_manifest,
        "study_summary": draft.study_summary,
        "evidence_status": draft.evidence_status,
        "unresolved_questions": list(draft.unresolved_questions),
    }


def _write_new_draft(output_dir: Path, draft: RegistrationDraft) -> None:
    target = Path(output_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ContractError(f"draft output already exists: {target}")
    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent)
    )
    try:
        payload = canonical_json_bytes(_draft_payload(draft))
        path = stage / "registration-draft.json"
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    raise OSError("draft write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        summary = stage / "study-summary.md"
        summary.write_text(draft.study_summary, encoding="utf-8")
        summary.chmod(0o600)
        _rename_directory_no_replace(stage, target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _load_draft(path: Path) -> RegistrationDraft:
    payload = _load_json(path / "registration-draft.json", "registration draft")
    if set(payload) != _DRAFT_KEYS:
        raise ContractError("registration draft fields do not match the closed schema")
    unresolved = payload["unresolved_questions"]
    if (
        not isinstance(unresolved, list)
        or any(not isinstance(item, str) for item in unresolved)
    ):
        raise ContractError("registration draft unresolved_questions is invalid")
    for field in ("preregistration", "delivery_map", "creative_manifest"):
        if not isinstance(payload[field], dict):
            raise ContractError(f"registration draft {field} must be an object")
    if not isinstance(payload["study_summary"], str):
        raise ContractError("registration draft study_summary must be text")
    if not isinstance(payload["evidence_status"], str):
        raise ContractError("registration draft evidence_status must be text")
    return RegistrationDraft(
        preregistration=payload["preregistration"],  # type: ignore[arg-type]
        delivery_map=payload["delivery_map"],  # type: ignore[arg-type]
        creative_manifest=payload["creative_manifest"],  # type: ignore[arg-type]
        study_summary=payload["study_summary"],
        evidence_status=payload["evidence_status"],
        unresolved_questions=tuple(unresolved),
    )


def _draft_command(args: argparse.Namespace) -> dict[str, object]:
    require_approved_runtime("prepare_study")
    setup_path = Path(args.setup_input)
    setup = _load_json(setup_path, "study setup input")
    if set(setup) != _SETUP_KEYS:
        raise ContractError("study setup input fields do not match the closed schema")
    base = setup_path.parent
    campaign_plans = setup["campaign_plans"]
    if not isinstance(campaign_plans, list) or not campaign_plans:
        raise ContractError("study setup campaign_plans must be a non-empty list")
    resolved_plans: list[object] = []
    for index, plan in enumerate(campaign_plans):
        if isinstance(plan, str):
            resolved_plans.append(
                _resolve_path(
                    plan, base=base, label=f"campaign_plans[{index}]"
                )
            )
        else:
            resolved_plans.append(plan)
    supplied = setup["supplied_facts"]
    if not isinstance(supplied, dict):
        raise ContractError("study setup supplied_facts must be an object")
    supplied = dict(supplied)
    for field in ("producer_evidence_root", "producer_snapshot_root"):
        if field in supplied:
            supplied[field] = str(
                _resolve_path(supplied[field], base=base, label=field)
            )
    draft = build_registration_draft(
        run_root=_resolve_path(
            setup["run_root"], base=base, label="run_root"
        ),
        panel_package=_resolve_path(
            setup["panel_package"], base=base, label="panel_package"
        ),
        campaign_plans=resolved_plans,
        supplied_facts=supplied,
    )
    _write_new_draft(Path(args.output_dir), draft)
    return {
        "status": "drafted",
        "output": str(Path(args.output_dir)),
        "evidence_status": draft.evidence_status,
        "unresolved_questions": list(draft.unresolved_questions),
    }


def _seal_command(args: argparse.Namespace) -> dict[str, object]:
    require_approved_runtime("prepare_study")
    draft = _load_draft(Path(args.draft_dir))
    sealed = seal_study_registration(
        draft=draft,
        authority_root=Path(args.authority_root),
        authority_index=Path(args.authority_index),
        authority_registry=Path(args.authority_registry),
        authority_secret_file=Path(args.authority_secret_file),
        output_dir=Path(args.output_dir),
    )
    return {
        "status": "sealed",
        "output": str(sealed.study_root),
        "registration_sha256": sealed.registration_sha256,
        "delivery_map_sha256": sealed.delivery_map_sha256,
        "creative_manifest_sha256": sealed.creative_manifest_sha256,
        "receipt_sha256": sealed.receipt_sha256,
        "evidence_status": sealed.evidence_status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    draft = subcommands.add_parser("draft")
    draft.add_argument("--setup-input", required=True, type=Path)
    draft.add_argument("--output-dir", required=True, type=Path)
    seal = subcommands.add_parser("seal")
    seal.add_argument("--draft-dir", required=True, type=Path)
    seal.add_argument("--output-dir", required=True, type=Path)
    seal.add_argument("--authority-root", required=True, type=Path)
    seal.add_argument("--authority-index", required=True, type=Path)
    seal.add_argument("--authority-registry", required=True, type=Path)
    seal.add_argument("--authority-secret-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = (
            _draft_command(args)
            if args.command == "draft"
            else _seal_command(args)
        )
        code = 0
    except (RuntimeGuardError, StudyAuthorityError, ContractError) as exc:
        collision = "already exists" in str(exc)
        payload = {
            "status": "error",
            "error": "output_collision" if collision else "validation",
            "message": str(exc),
        }
        code = 3 if collision else 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        payload = {
            "status": "error",
            "error": "validation",
            "message": str(exc),
        }
        code = 2
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
