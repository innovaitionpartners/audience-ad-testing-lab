#!/usr/bin/env python3
"""Verify or durably recover one Tier 4 synthetic-producer publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from audience_panel_builder.population.validation.evidence_errors import (
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
    ProducerRuntimeUnavailable,
)
from audience_panel_builder.population.validation.evidence_snapshot import (
    recover_evidence_snapshot_publication,
)
from audience_panel_builder.population.validation.producer_evidence import (
    recover_synthetic_producer_evidence_publication,
    recover_synthetic_producer_revocation_publication,
    verify_synthetic_producer,
)
from audience_panel_builder.population.validation.replay_inputs import (
    ProducerReplayInputs,
)


def _digest(value: str) -> str:
    if not value.startswith("sha256:"):
        raise argparse.ArgumentTypeError("digest must use the sha256: prefix")
    return value


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surface", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--result-sha256", required=True, type=_digest)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--surface", required=True)
    for option in (
        "study-manifest", "accepted-responses", "raw-provider-returns",
        "rejected-attempts", "cumulative-dispatch-audit", "result",
    ):
        verify.add_argument("--" + option, required=True, type=Path)
    for option in (
        "screening-jobs", "recovery-configuration",
        "command-dispatch-audit-input", "screening-result",
        "screening-producer-evidence",
    ):
        verify.add_argument("--" + option, type=Path)
    verify.add_argument(
        "--allowed-source-root", required=True, action="append", type=Path
    )
    verify.add_argument("--runtime-root", required=True, type=Path)
    verify.add_argument("--snapshot-root", required=True, type=Path)
    verify.add_argument("--evidence-root", required=True, type=Path)

    snapshot = commands.add_parser("recover-snapshot")
    _identity_arguments(snapshot)
    snapshot.add_argument("--snapshot-root", required=True, type=Path)

    receipt = commands.add_parser("recover-receipt")
    _identity_arguments(receipt)
    receipt.add_argument("--evidence-root", required=True, type=Path)
    receipt.add_argument("--snapshot-root", required=True, type=Path)

    revocation = commands.add_parser("recover-revocation")
    _identity_arguments(revocation)
    revocation.add_argument("--evidence-root", required=True, type=Path)
    return parser


def _print(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.mode == "verify":
            inputs = ProducerReplayInputs(
                study_manifest=args.study_manifest,
                accepted_responses=args.accepted_responses,
                raw_provider_returns=args.raw_provider_returns,
                rejected_attempts=args.rejected_attempts,
                cumulative_dispatch_audit=args.cumulative_dispatch_audit,
                result=args.result,
                screening_jobs=args.screening_jobs,
                recovery_configuration=args.recovery_configuration,
                command_dispatch_audit_input=args.command_dispatch_audit_input,
                screening_result=args.screening_result,
                screening_producer_evidence=args.screening_producer_evidence,
            )
            record = verify_synthetic_producer(
                surface=args.surface,
                inputs=inputs,
                allowed_source_roots=args.allowed_source_root,
                runtime_root=args.runtime_root,
                snapshot_root=args.snapshot_root,
                evidence_root=args.evidence_root,
            )
            result = record["result_binding"]
            receipt_id = (
                f"{record['surface']}--{record['run_id']}--"
                f"{str(result['canonical_document_sha256'])[7:]}"
            )
            _print({
                "status": "verified",
                "evidence_path": str(
                    args.evidence_root
                    / (receipt_id + ".producer-evidence.json")
                ),
                "producer_evidence_sha256": record["producer_evidence_sha256"],
                "result_sha256": result["canonical_document_sha256"],
                "result_bytes_sha256": result["raw_bytes_sha256"],
            })
        elif args.mode == "recover-snapshot":
            snapshot = recover_evidence_snapshot_publication(
                surface=args.surface,
                run_id=args.run_id,
                result_sha256=args.result_sha256,
                snapshot_root=args.snapshot_root,
            )
            _print({
                "status": "recovered",
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "archive_sha256": snapshot.archive_sha256,
            })
        elif args.mode == "recover-receipt":
            record = recover_synthetic_producer_evidence_publication(
                surface=args.surface,
                run_id=args.run_id,
                result_sha256=args.result_sha256,
                evidence_root=args.evidence_root,
                snapshot_root=args.snapshot_root,
            )
            result = record["result_binding"]
            receipt_id = (
                f"{args.surface}--{args.run_id}--"
                f"{args.result_sha256[7:]}"
            )
            _print({
                "status": "recovered",
                "evidence_path": str(
                    args.evidence_root
                    / (receipt_id + ".producer-evidence.json")
                ),
                "producer_evidence_sha256": record["producer_evidence_sha256"],
                "result_sha256": result["canonical_document_sha256"],
                "result_bytes_sha256": result["raw_bytes_sha256"],
            })
        else:
            marker = recover_synthetic_producer_revocation_publication(
                surface=args.surface,
                run_id=args.run_id,
                result_sha256=args.result_sha256,
                evidence_root=args.evidence_root,
            )
            _print({
                "status": "revoked",
                "receipt_id": marker["receipt_id"],
                "producer_evidence_sha256": marker[
                    "producer_evidence_sha256"
                ],
            })
        return 0
    except ProducerPublicationIndeterminate as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except ProducerOutputCollision as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ProducerRuntimeUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except (ProducerAuthenticationError, ProducerEvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
