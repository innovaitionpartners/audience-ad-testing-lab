#!/usr/bin/env python3
"""Import aggregate advertising-platform outcome exports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from outcome_data_prep.common import ContractError, canonical_json_bytes
from outcome_data_prep.publication import ImportConflict
from outcome_data_prep.runtime_guard import require_approved_runtime
from outcome_data_prep.workflow import (
    CorrectionInput,
    ImportRequest,
    SourceInput,
    import_results,
    pair_source_arguments,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument(
        "--source-governance", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--source-context", type=Path, action="append", default=[]
    )
    parser.add_argument("--authority-registry", type=Path, required=True)
    parser.add_argument("--authority-secret-file", type=Path, required=True)
    parser.add_argument("--imported-at", required=True)
    parser.add_argument("--import-id", required=True)
    parser.add_argument("--corrects-import")
    parser.add_argument(
        "--corrects-observation", action="append", default=[]
    )
    parser.add_argument("--correction-requested-at")
    parser.add_argument("--correction-actor")
    parser.add_argument("--correction-reason-code")
    parser.add_argument("--correction-reason")
    parser.add_argument("--correction-id")
    return parser


def _load_object(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        require_approved_runtime("import_results")
        pairs = pair_source_arguments(
            args.source, args.source_governance, args.source_context
        )
        sources = tuple(
            SourceInput(
                source,
                _load_object(governance, "source governance"),
                (
                    None
                    if context is None
                    else _load_object(context, "source context")
                ),
            )
            for source, governance, context in pairs
        )
        correction_values = (
            args.corrects_import,
            args.corrects_observation,
            args.correction_requested_at,
            args.correction_actor,
            args.correction_reason_code,
            args.correction_reason,
        )
        present = (
            correction_values[0] is not None,
            bool(correction_values[1]),
            *(value is not None for value in correction_values[2:]),
        )
        if any(present) and not all(present):
            raise ValueError("all six correction flags must be supplied together")
        correction = None
        if all(present):
            correction = CorrectionInput(
                correction_id=args.correction_id or f"correction-{args.import_id}",
                requested_at=args.correction_requested_at,
                actor=args.correction_actor,
                reason_code=args.correction_reason_code,
                reason=args.correction_reason,
                supersedes_import_id=args.corrects_import,
                supersedes_observation_ids=tuple(args.corrects_observation),
            )
        result = import_results(
            ImportRequest(
                study_root=args.study_root,
                sources=sources,
                authority_registry=args.authority_registry,
                authority_secret_file=args.authority_secret_file,
                imported_at=args.imported_at,
                import_id=args.import_id,
            ),
            correction,
        )
        sys.stdout.buffer.write(canonical_json_bytes({
            "import_id": result.import_id,
            "import_digest": result.import_digest,
            "generation_path": str(result.generation_path),
            "ledger_digest": result.ledger_digest,
            "analytical_identity_sha256": result.analytical_identity_sha256,
            "evidence_status": result.evidence_status,
            "operational_status": result.operational_status,
            "validation_handoff_written": result.validation_handoff_written,
            "source_count": result.source_count,
            "matched_row_count": result.matched_row_count,
            "quarantined_row_count": result.quarantined_row_count,
        }))
        return 0
    except ImportConflict as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ContractError, OSError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
