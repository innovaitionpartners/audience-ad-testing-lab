from __future__ import annotations

import inspect
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.population.validation.producer_evidence import (  # noqa: E402
    PRODUCER_EVIDENCE_VERSION,
    _BOOTSTRAP_SHA256,
    _archive_inputs,
    _create_or_recover_exact_snapshot,
    _require_live_snapshot_matches,
    _ResourceLimits,
    _parse_json,
    _private_stage,
    _publish_receipt,
    _publish_revocation,
    _recover_receipt,
    _recover_revocation,
    _receipt_id,
    _receipt_name,
    _read_bounded,
    _revocation_name,
    _validate_receipt_document,
    recover_synthetic_producer_evidence_publication,
    recover_synthetic_producer_revocation_publication,
    validate_synthetic_producer_evidence,
    verify_synthetic_producer,
)
from audience_panel_builder.common import canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
    ProducerRuntimeUnavailable,
)
from audience_panel_builder.population.validation.replay_inputs import (  # noqa: E402
    ProducerReplayInputs,
    assemble_replay_inputs,
)
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    EvidenceSnapshot,
    create_evidence_snapshot,
    open_evidence_snapshot,
    recover_evidence_snapshot_publication,
)


SHA = "sha256:" + ("ab" * 32)


def changed_frozen_snapshot_commit(snapshot: EvidenceSnapshot) -> bytes:
    record = json.loads(snapshot.commit_path.read_bytes())
    replacements = (
        "2000-01-01T00:00:00.000000Z",
        "2000-01-01T00:00:00.000001Z",
    )
    record["frozen_at"] = next(
        value for value in replacements if value != record["frozen_at"]
    )
    record["snapshot_sha256"] = None
    record["snapshot_sha256"] = sha256_json(record)
    return canonical_json_bytes(record)


def binding(path: str, count: int | None = None) -> dict[str, object]:
    return {
        "path": path,
        "raw_bytes_sha256": SHA,
        "canonical_document_sha256": SHA,
        "record_count": count,
    }


def valid_record() -> dict[str, object]:
    semantics = {
        "entry_point": (
            "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
        ),
        "subcommand": "screening",
        "bootstrap_sha256": _BOOTSTRAP_SHA256,
        "dependency_closure": [{
            "path": (
                "skills/audience-ad-testing-lab/scripts/"
                "aggregate-screening.py"
            ),
            "byte_count": 10,
            "raw_bytes_sha256": SHA,
        }],
        "runtime_fingerprint": {
            "python_implementation": "CPython",
            "python_version": "3.14.5",
            "numpy_version": "2.4.2",
            "scipy_version": "1.17.0",
            "platform_system": "Darwin",
            "platform_release": "25.5.0",
            "machine": "arm64",
            "numpy_build_sha256": SHA,
            "blas_lapack_sha256": SHA,
        },
        "policy_bindings": {
            "calibration_policy_version": "complete-exposure-calibration-v2",
            "production_resamples": 2000,
            "cutoff_tie_tolerance": 1e-12,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "exact-utility-equality-v1",
            "recovery_configuration_sha256": SHA,
        },
        "output_serialization": {
            "producer_raw_serialization": {
                "encoding": "utf-8",
                "indent": 2,
                "sort_keys": True,
                "allow_nan": False,
                "ensure_ascii": True,
                "separators": None,
                "terminal_lf": True,
            },
            "canonical_document_serialization": {
                "encoding": "utf-8",
                "indent": None,
                "sort_keys": True,
                "allow_nan": False,
                "ensure_ascii": False,
                "separators": [",", ":"],
                "terminal_lf": True,
            },
        },
        "producer_semantics_sha256": None,
    }
    semantics["producer_semantics_sha256"] = sha256_json(semantics)
    record = {
        "schema_version": PRODUCER_EVIDENCE_VERSION,
        "surface": "complete_exposure_ordering",
        "method": "complete_exposure",
        "stage": "screening",
        "run_id": "run-001",
        "frozen_at": "2026-07-27T12:00:00.000000Z",
        "sealed_at": "2026-07-27T12:00:01.000000Z",
        "producer_semantics": semantics,
        "input_bindings": {
            "study_manifest": binding("study-manifest.json"),
            "accepted_responses": binding("panelist-responses.jsonl", 1),
            "raw_provider_returns": binding("raw-provider-returns.jsonl", 1),
            "rejected_attempts": binding("rejected-attempts.jsonl", 1),
            "dispatch_audit": binding("dispatch-audit.jsonl", 1),
            "command_dispatch_audit_input": None,
            "screening_jobs": binding("screening-jobs.json"),
            "screening_response_projection": binding(
                "screening-response-projection.jsonl", 1
            ),
            "recovery_configuration": binding("recovery-configuration.json"),
        },
        "result_binding": binding("screening-model-results.json"),
        "snapshot_binding": {
            "snapshot_id": (
                "complete_exposure_ordering--run-001--" + ("ab" * 32)
            ),
            "snapshot_sha256": SHA,
            "archive_sha256": SHA,
        },
        "producer_evidence_sha256": None,
    }
    record["producer_evidence_sha256"] = sha256_json(record)
    return record


def surface_record(surface: str) -> dict[str, object]:
    record = json.loads(json.dumps(valid_record()))
    if surface == "complete_exposure_ordering":
        return record
    record["surface"] = surface
    record["method"] = "partial_exposure_maxdiff"
    semantics = record["producer_semantics"]
    if surface == "maxdiff_screening_ordering":
        semantics["policy_bindings"] = {
            "maxdiff_configuration_sha256": SHA,
            "required_bootstrap_count": 2000,
            "minimum_successful_fit_floor": 0.95,
            "clear_finalist_threshold": 0.90,
            "clear_non_finalist_threshold": 0.10,
            "minimum_utility_tie_tolerance": 1e-12,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "effective_ordering_tolerance": 1e-8,
            "rounding_rule": "python-half-even-v1",
            "recovery_configuration_sha256": SHA,
        }
    else:
        record["stage"] = "boundary"
        record["result_binding"]["path"] = "boundary-results.json"
        semantics["subcommand"] = "boundary"
        semantics["policy_bindings"] = {
            "pairwise_configuration_sha256": SHA,
            "clear_finalist_threshold": 0.90,
            "clear_non_finalist_threshold": 0.10,
            "minimum_utility_tie_tolerance": 1e-12,
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "effective_ordering_tolerance": 1e-8,
            "rounding_rule": "python-half-even-v1",
            "upstream_screening_producer_semantics_sha256": SHA,
        }
        inputs = record["input_bindings"]
        del inputs["screening_jobs"]
        del inputs["screening_response_projection"]
        del inputs["recovery_configuration"]
        inputs["boundary_response_projection"] = binding(
            "boundary-response-projection.jsonl", 1
        )
        inputs["screening_result"] = binding(
            "screening-model-results.json"
        )
        inputs["screening_producer_evidence"] = {
            **binding(
                "maxdiff_screening_ordering--run-001--"
                + ("ab" * 32)
                + ".producer-evidence.json"
            ),
            "producer_evidence_sha256": SHA,
            "producer_semantics_sha256": SHA,
            "result_sha256": SHA,
            "result_bytes_sha256": SHA,
        }
    semantics["producer_semantics_sha256"] = None
    semantics["producer_semantics_sha256"] = sha256_json(semantics)
    record["snapshot_binding"]["snapshot_id"] = (
        f"{surface}--run-001--" + ("ab" * 32)
    )
    record["producer_evidence_sha256"] = None
    record["producer_evidence_sha256"] = sha256_json(record)
    return record


class Tier4SyntheticProducerEvidenceContractTests(unittest.TestCase):
    def test_public_surface_is_closed_and_versioned(self):
        self.assertEqual(
            "panel-synthetic-producer-evidence-v1",
            PRODUCER_EVIDENCE_VERSION,
        )
        self.assertEqual(
            {
                "surface",
                "inputs",
                "allowed_source_roots",
                "runtime_root",
                "snapshot_root",
                "evidence_root",
            },
            set(inspect.signature(verify_synthetic_producer).parameters),
        )
        self.assertEqual(
            {
                "surface", "run_id", "result_sha256",
                "evidence_root", "snapshot_root",
            },
            set(
                inspect.signature(
                    validate_synthetic_producer_evidence
                ).parameters
            ),
        )
        self.assertEqual(
            {
                "surface", "run_id", "result_sha256",
                "evidence_root", "snapshot_root",
            },
            set(
                inspect.signature(
                    recover_synthetic_producer_evidence_publication
                ).parameters
            ),
        )
        self.assertEqual(
            {"surface", "run_id", "result_sha256", "evidence_root"},
            set(
                inspect.signature(
                    recover_synthetic_producer_revocation_publication
                ).parameters
            ),
        )

    def test_closed_receipt_self_hash_surface_snapshot_and_chronology(self):
        record = valid_record()
        self.assertEqual(
            record,
            _validate_receipt_document(
                record,
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
            ),
        )
        cases: list[tuple[str, dict[str, object]]] = []
        for field in (
            "schema_version", "surface", "method", "stage", "run_id",
            "frozen_at", "sealed_at", "producer_semantics",
            "input_bindings", "result_binding", "snapshot_binding",
            "producer_evidence_sha256",
        ):
            changed = json.loads(json.dumps(record))
            if field in {"frozen_at", "sealed_at"}:
                changed[field] = "2026-07-27T11:00:00.000000Z"
            elif field == "producer_semantics":
                changed[field]["bootstrap_sha256"] = "sha256:" + ("cd" * 32)
            elif field == "input_bindings":
                changed[field]["accepted_responses"]["record_count"] = 2
            elif field == "result_binding":
                changed[field]["path"] = "boundary-results.json"
            elif field == "snapshot_binding":
                changed[field]["archive_sha256"] = "sha256:" + ("cd" * 32)
            elif field == "producer_evidence_sha256":
                changed[field] = "sha256:" + ("cd" * 32)
            else:
                changed[field] = "wrong"
            cases.append((field, changed))
        extra = json.loads(json.dumps(record))
        extra["extra"] = True
        cases.append(("extra", extra))
        for name, changed in cases:
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                _validate_receipt_document(
                    changed,
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                )

    def test_all_three_surfaces_have_exact_distinct_recursive_binding_shapes(self):
        for surface in (
            "complete_exposure_ordering",
            "maxdiff_screening_ordering",
            "pairwise_boundary_ordering",
        ):
            with self.subTest(surface=surface):
                record = surface_record(surface)
                self.assertEqual(
                    record,
                    _validate_receipt_document(
                        record,
                        surface=surface,
                        run_id="run-001",
                        result_sha256=SHA,
                    ),
                )
        pairwise = surface_record("pairwise_boundary_ordering")
        mutations = []
        for field in ("result_sha256", "result_bytes_sha256"):
            changed = json.loads(json.dumps(pairwise))
            changed["input_bindings"]["screening_producer_evidence"][field] = (
                "sha256:" + ("cd" * 32)
            )
            changed["producer_evidence_sha256"] = None
            changed["producer_evidence_sha256"] = sha256_json(changed)
            mutations.append((field, changed))
        for field, changed in mutations:
            with self.subTest(field=field), self.assertRaises(
                ProducerAuthenticationError
            ):
                _validate_receipt_document(
                    changed,
                    surface="pairwise_boundary_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                )

    def test_rehashed_bootstrap_and_role_binding_mutations_fail(self):
        mutations: list[tuple[str, dict[str, object]]] = []
        bootstrap = valid_record()
        bootstrap["producer_semantics"]["bootstrap_sha256"] = SHA
        bootstrap["producer_semantics"]["producer_semantics_sha256"] = None
        bootstrap["producer_semantics"]["producer_semantics_sha256"] = (
            sha256_json(bootstrap["producer_semantics"])
        )
        bootstrap["producer_evidence_sha256"] = None
        bootstrap["producer_evidence_sha256"] = sha256_json(bootstrap)
        mutations.append(("bootstrap", bootstrap))

        for role, field, value in (
            ("study_manifest", "path", "other.json"),
            ("study_manifest", "record_count", 1),
            ("accepted_responses", "path", "other.jsonl"),
            ("accepted_responses", "record_count", None),
            ("raw_provider_returns", "record_count", 0),
            ("rejected_attempts", "record_count", None),
            ("dispatch_audit", "record_count", None),
            ("screening_jobs", "record_count", 1),
            ("screening_response_projection", "record_count", None),
            (
                "recovery_configuration", "path",
                "nested/recovery.json",
            ),
            (
                "recovery_configuration", "path",
                "nested\\recovery.json",
            ),
        ):
            changed = valid_record()
            changed["input_bindings"][role][field] = value
            changed["producer_evidence_sha256"] = None
            changed["producer_evidence_sha256"] = sha256_json(changed)
            mutations.append((f"{role}.{field}", changed))

        nested_command = valid_record()
        nested_command["input_bindings"][
            "command_dispatch_audit_input"
        ] = binding("nested/command-dispatch-audit.jsonl", 1)
        nested_command["producer_evidence_sha256"] = None
        nested_command["producer_evidence_sha256"] = sha256_json(
            nested_command
        )
        mutations.append(("nested command audit", nested_command))

        pairwise = surface_record("pairwise_boundary_ordering")
        pairwise["input_bindings"]["command_dispatch_audit_input"] = binding(
            "command-dispatch-audit-input.jsonl", 1
        )
        pairwise["producer_evidence_sha256"] = None
        pairwise["producer_evidence_sha256"] = sha256_json(pairwise)
        mutations.append(("pairwise command audit", pairwise))

        upstream = surface_record("pairwise_boundary_ordering")
        upstream["input_bindings"]["screening_producer_evidence"][
            "record_count"
        ] = 1
        upstream["producer_evidence_sha256"] = None
        upstream["producer_evidence_sha256"] = sha256_json(upstream)
        mutations.append(("upstream record count", upstream))

        for recovery_name, command_name in (
            ("recovery.json", None),
            (
                "recovery-configuration-custom.json",
                "command-dispatch-audit.jsonl",
            ),
        ):
            accepted = valid_record()
            accepted["input_bindings"]["recovery_configuration"][
                "path"
            ] = recovery_name
            if command_name is not None:
                accepted["input_bindings"][
                    "command_dispatch_audit_input"
                ] = binding(command_name, 1)
            accepted["producer_evidence_sha256"] = None
            accepted["producer_evidence_sha256"] = sha256_json(accepted)
            self.assertEqual(
                accepted,
                _validate_receipt_document(
                    accepted,
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                ),
            )

        for label, changed in mutations:
            with self.subTest(label=label), self.assertRaises(
                ProducerAuthenticationError
            ):
                _validate_receipt_document(
                    changed,
                    surface=str(changed["surface"]),
                    run_id="run-001",
                    result_sha256=SHA,
                )

    def test_live_snapshot_must_equal_every_authenticated_snapshot_field(self):
        first = {
            "member_path": "inputs/result.json",
            "raw_bytes_sha256": SHA,
            "canonical_document_sha256": SHA,
            "record_count": 1,
        }
        second = {
            "member_path": "runtime/producer.py",
            "raw_bytes_sha256": "sha256:" + ("cd" * 32),
            "canonical_document_sha256": None,
            "record_count": None,
        }
        expected = EvidenceSnapshot(
            snapshot_id="snapshot-1",
            commit_path=Path("/unused/snapshot.json"),
            frozen_at="2026-07-27T12:00:00.000000Z",
            snapshot_sha256="sha256:" + ("11" * 32),
            archive_sha256="sha256:" + ("22" * 32),
            bindings=(("result", first), ("runtime", second)),
        )

        class LiveSnapshot:
            snapshot_id = expected.snapshot_id
            frozen_at = expected.frozen_at
            snapshot_sha256 = expected.snapshot_sha256
            archive_sha256 = expected.archive_sha256
            bindings = (
                ("runtime", dict(reversed(tuple(second.items())))),
                ("result", dict(reversed(tuple(first.items())))),
            )

        _require_live_snapshot_matches(expected, LiveSnapshot())

        for field in (
            "snapshot_id",
            "frozen_at",
            "snapshot_sha256",
            "archive_sha256",
        ):
            live = LiveSnapshot()
            setattr(live, field, getattr(live, field) + "-changed")
            with self.subTest(identity_field=field), self.assertRaises(
                ProducerAuthenticationError
            ):
                _require_live_snapshot_matches(expected, live)

        binding_mutations = []
        for key in (
            "member_path",
            "raw_bytes_sha256",
            "canonical_document_sha256",
            "record_count",
        ):
            changed = dict(first)
            changed[key] = (
                True if key == "record_count" else str(changed[key]) + "-changed"
            )
            binding_mutations.append(
                (f"result.{key}", (("result", changed), ("runtime", second)))
            )
        binding_mutations.extend(
            (
                ("missing", (("result", first),)),
                (
                    "extra",
                    (
                        ("result", first),
                        ("runtime", second),
                        ("extra", dict(second)),
                    ),
                ),
                (
                    "renamed",
                    (("other-result", first), ("runtime", second)),
                ),
            )
        )
        for label, changed in binding_mutations:
            live = LiveSnapshot()
            live.bindings = changed
            with self.subTest(binding_mutation=label), self.assertRaises(
                ProducerAuthenticationError
            ):
                _require_live_snapshot_matches(expected, live)

    def test_resource_limits_allow_equality_and_reject_one_over(self):
        limits = _ResourceLimits(
            maximum_bytes=1024,
            maximum_depth=3,
            maximum_array_items=2,
            maximum_object_keys=2,
            maximum_string_bytes=2,
            maximum_scalars=2,
        )
        self.assertEqual(
            {"a": ["é", 1]},
            _parse_json(
                canonical_json_bytes({"a": ["é", 1]}),
                limits,
                label="test document",
            ),
        )
        cases = (
            {"a": [1, 2, 3]},
            {"a": {"b": {"c": 1}}},
            {"abc": 1},
            {"a": "abc"},
            {"a": [1, 2], "b": 3},
            {"a": 1, "b": 2, "c": 3},
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(
                ProducerAuthenticationError
            ):
                _parse_json(
                    canonical_json_bytes(value),
                    limits,
                    label="test document",
                )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "bounded.json"
        path.write_bytes(b"12345678")
        fd = os.open(path, os.O_RDONLY)
        try:
            self.assertEqual(
                b"12345678",
                _read_bounded(
                    fd, _ResourceLimits(maximum_bytes=8), label="bounded"
                ),
            )
            with self.assertRaises(ProducerAuthenticationError):
                _read_bounded(
                    fd, _ResourceLimits(maximum_bytes=7), label="bounded"
                )
        finally:
            os.close(fd)

    def test_real_receipt_and_revocation_readers_enforce_every_limit_edge(self):
        def metrics(value: object) -> dict[str, int]:
            maximum_depth = 0
            maximum_array_items = 0
            maximum_object_keys = 0
            maximum_string_bytes = 0
            scalars = 0
            stack = [(value, 1)]
            while stack:
                item, depth = stack.pop()
                maximum_depth = max(maximum_depth, depth)
                if isinstance(item, dict):
                    maximum_object_keys = max(
                        maximum_object_keys, len(item)
                    )
                    for key, child in item.items():
                        maximum_string_bytes = max(
                            maximum_string_bytes,
                            len(key.encode("utf-8")),
                        )
                        stack.append((child, depth + 1))
                elif isinstance(item, list):
                    maximum_array_items = max(
                        maximum_array_items, len(item)
                    )
                    stack.extend((child, depth + 1) for child in item)
                else:
                    scalars += 1
                    if isinstance(item, str):
                        maximum_string_bytes = max(
                            maximum_string_bytes,
                            len(item.encode("utf-8")),
                        )
            return {
                "maximum_depth": maximum_depth,
                "maximum_array_items": maximum_array_items,
                "maximum_object_keys": maximum_object_keys,
                "maximum_string_bytes": maximum_string_bytes,
                "maximum_scalars": scalars,
            }

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        receipt_raw = canonical_json_bytes(record)
        receipt_metrics = metrics(record)
        receipt_limits = _ResourceLimits(
            maximum_bytes=len(receipt_raw), **receipt_metrics
        )
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_RECEIPT_LIMITS",
            receipt_limits,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            self.assertEqual(
                record,
                validate_synthetic_producer_evidence(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                    snapshot_root=snapshots,
                ),
            )
        for field, value in (
            ("maximum_bytes", len(receipt_raw)),
            *receipt_metrics.items(),
        ):
            if value < 1:
                continue
            limited = {
                "maximum_bytes": len(receipt_raw),
                **receipt_metrics,
            }
            limited[field] = value - 1
            with self.subTest(receipt_limit=field), patch(
                "audience_panel_builder.population.validation."
                "producer_evidence._RECEIPT_LIMITS",
                _ResourceLimits(**limited),
            ), patch(
                "audience_panel_builder.population.validation."
                "producer_evidence._validate_snapshot"
            ), self.assertRaises(ProducerAuthenticationError):
                validate_synthetic_producer_evidence(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                    snapshot_root=snapshots,
                )

        marker = _publish_revocation(
            surface="complete_exposure_ordering",
            run_id="run-001",
            result_sha256=SHA,
            evidence_root=evidence,
        )
        marker_raw = canonical_json_bytes(marker)
        marker_metrics = metrics(marker)
        marker_limits = _ResourceLimits(
            maximum_bytes=len(marker_raw), **marker_metrics
        )
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_REVOCATION_LIMITS",
            marker_limits,
        ):
            self.assertEqual(
                marker,
                recover_synthetic_producer_revocation_publication(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                ),
            )
        for field, value in (
            ("maximum_bytes", len(marker_raw)),
            *marker_metrics.items(),
        ):
            if value < 1:
                continue
            limited = {
                "maximum_bytes": len(marker_raw),
                **marker_metrics,
            }
            limited[field] = value - 1
            with self.subTest(revocation_limit=field), patch(
                "audience_panel_builder.population.validation."
                "producer_evidence._REVOCATION_LIMITS",
                _ResourceLimits(**limited),
            ), self.assertRaises(ProducerAuthenticationError):
                recover_synthetic_producer_revocation_publication(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                )

    def test_receipt_publication_recovery_collision_and_mutation_fail_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            published = _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
            self.assertEqual(record, published)
            path = evidence / _receipt_name(
                "complete_exposure_ordering", "run-001", SHA
            )
            self.assertEqual(0o400, path.stat().st_mode & 0o777)
            self.assertEqual(canonical_json_bytes(record), path.read_bytes())
            with self.assertRaises(ProducerOutputCollision):
                _publish_receipt(
                    record, evidence_root=evidence, snapshot_root=snapshots
                )
            os.chmod(path, 0o600)
            path.write_bytes(path.read_bytes() + b" ")
            os.chmod(path, 0o400)
            with self.assertRaises(ProducerAuthenticationError):
                recover_synthetic_producer_evidence_publication(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                    snapshot_root=snapshots,
                )

    def test_receipt_indeterminate_requires_successful_fsync_recovery(self):
        original_fsync = os.fsync
        for failed_call in (1, 2):
            with self.subTest(failed_call=failed_call):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                base = Path(temporary.name)
                evidence = base / "evidence"
                snapshots = base / "snapshots"
                evidence.mkdir()
                snapshots.mkdir()
                record = valid_record()
                calls = 0

                def fail_once(fd: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise OSError("injected fsync failure")
                    original_fsync(fd)

                with patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence._validate_snapshot"
                ), patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence.os.fsync",
                    side_effect=fail_once,
                ), self.assertRaises(ProducerPublicationIndeterminate):
                    _publish_receipt(
                        record,
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    )
                # Complete canonical bytes and metadata do not resolve the
                # exit-5 state. Claim validation must perform the explicit
                # file/root recovery itself before authorizing the receipt.
                with patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence._validate_snapshot"
                ):
                    recovered = validate_synthetic_producer_evidence(
                        surface="complete_exposure_ordering",
                        run_id="run-001",
                        result_sha256=SHA,
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    )
                self.assertEqual(record, recovered)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "os.fsync",
            side_effect=OSError("repeated fsync failure"),
        ), self.assertRaises(ProducerPublicationIndeterminate):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "os.fsync",
            side_effect=OSError("repeated recovery fsync failure"),
        ), self.assertRaises(ProducerPublicationIndeterminate):
            validate_synthetic_producer_evidence(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )

    def test_publication_retains_original_receipt_and_root_authority(self):
        original_recover = _recover_receipt
        for replacement in ("receipt", "root"):
            with self.subTest(replacement=replacement):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                base = Path(temporary.name)
                evidence = base / "evidence"
                snapshots = base / "snapshots"
                evidence.mkdir()
                snapshots.mkdir()
                record = valid_record()
                name = _receipt_name(
                    "complete_exposure_ordering", "run-001", SHA
                )

                def replace_then_recover(**kwargs):
                    path = evidence / name
                    if replacement == "receipt":
                        raw = path.read_bytes()
                        path.unlink()
                        path.write_bytes(raw)
                        os.chmod(path, 0o400)
                    else:
                        parked = base / "parked-evidence"
                        evidence.rename(parked)
                        evidence.mkdir()
                        (evidence / name).write_bytes(
                            canonical_json_bytes(record)
                        )
                        os.chmod(evidence / name, 0o400)
                    return original_recover(**kwargs)

                with patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence._validate_snapshot"
                ), patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence._recover_receipt",
                    side_effect=replace_then_recover,
                ), self.assertRaises(ProducerAuthenticationError):
                    _publish_receipt(
                        record,
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    )

    def test_failed_third_publication_preserves_two_valid_siblings_and_root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()

        def identified(run_id: str, byte: str) -> dict[str, object]:
            record = valid_record()
            digest = "sha256:" + (byte * 64)
            record["run_id"] = run_id
            record["result_binding"]["raw_bytes_sha256"] = digest
            record["result_binding"]["canonical_document_sha256"] = digest
            record["snapshot_binding"]["snapshot_id"] = (
                f"complete_exposure_ordering--{run_id}--{byte * 64}"
            )
            record["producer_evidence_sha256"] = None
            record["producer_evidence_sha256"] = sha256_json(record)
            return record

        siblings = [
            identified("run-sibling-1", "1"),
            identified("run-sibling-2", "2"),
        ]
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            for record in siblings:
                _publish_receipt(
                    record,
                    evidence_root=evidence,
                    snapshot_root=snapshots,
                )
        sibling_paths = [
            evidence / _receipt_name(
                "complete_exposure_ordering",
                str(record["run_id"]),
                str(
                    record["result_binding"][
                        "canonical_document_sha256"
                    ]
                ),
            )
            for record in siblings
        ]
        before = [path.read_bytes() for path in sibling_paths]
        root_before = evidence.stat()

        def short_write(fd: int, value: bytes) -> None:
            os.write(fd, value[:17])
            raise OSError("injected short publication")

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_write_all",
            side_effect=short_write,
        ), self.assertRaises(ProducerEvidenceError):
            _publish_receipt(
                identified("run-sibling-3", "3"),
                evidence_root=evidence,
                snapshot_root=snapshots,
            )
        root_after = evidence.stat()
        self.assertEqual(
            (root_before.st_dev, root_before.st_ino, root_before.st_mode),
            (root_after.st_dev, root_after.st_ino, root_after.st_mode),
        )
        self.assertEqual(before, [path.read_bytes() for path in sibling_paths])
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            for record in siblings:
                self.assertEqual(
                    record,
                    validate_synthetic_producer_evidence(
                        surface="complete_exposure_ordering",
                        run_id=str(record["run_id"]),
                        result_sha256=str(
                            record["result_binding"][
                                "canonical_document_sha256"
                            ]
                        ),
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    ),
                )

    def test_staging_and_projection_filesystem_failures_are_closed(self):
        parent = None
        with patch.object(
            Path, "rmdir", side_effect=OSError("cleanup denied")
        ), self.assertRaises(ProducerEvidenceError):
            with _private_stage() as (_runtime, projection):
                parent = projection.parent
        assert parent is not None
        parent.rmdir()

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        dummy = ProducerReplayInputs(
            study_manifest=Path("/tmp/study-manifest.json"),
            accepted_responses=Path("/tmp/panelist-responses.jsonl"),
            raw_provider_returns=Path("/tmp/raw-provider-returns.jsonl"),
            rejected_attempts=Path("/tmp/rejected-attempts.jsonl"),
            cumulative_dispatch_audit=Path("/tmp/dispatch-audit.jsonl"),
            result=Path("/tmp/screening-model-results.json"),
            screening_jobs=Path("/tmp/screening-jobs.json"),
            recovery_configuration=Path(
                "/tmp/recovery-configuration.json"
            ),
            command_dispatch_audit_input=None,
            screening_result=None,
            screening_producer_evidence=None,
        )
        assembled = {
            "run_id": "run-stage-failure",
            "result_binding": {
                **binding("screening-model-results.json"),
                "record_count": None,
            },
        }
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "assemble_replay_inputs",
            return_value=assembled,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_configuration",
            return_value=({}, None),
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "build_producer_semantics",
            return_value=object(),
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_archive_inputs",
            side_effect=OSError("projection write denied"),
        ), self.assertRaises(ProducerEvidenceError):
            verify_synthetic_producer(
                surface="complete_exposure_ordering",
                inputs=dummy,
                allowed_source_roots=[Path("/tmp")],
                runtime_root=ROOT,
                snapshot_root=snapshots,
                evidence_root=evidence,
            )

    def test_existing_complete_receipt_is_indeterminate_before_snapshot(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        dummy = ProducerReplayInputs(
            study_manifest=Path("/tmp/study-manifest.json"),
            accepted_responses=Path("/tmp/panelist-responses.jsonl"),
            raw_provider_returns=Path("/tmp/raw-provider-returns.jsonl"),
            rejected_attempts=Path("/tmp/rejected-attempts.jsonl"),
            cumulative_dispatch_audit=Path("/tmp/dispatch-audit.jsonl"),
            result=Path("/tmp/screening-model-results.json"),
            screening_jobs=Path("/tmp/screening-jobs.json"),
            recovery_configuration=Path(
                "/tmp/recovery-configuration.json"
            ),
            command_dispatch_audit_input=None,
            screening_result=None,
            screening_producer_evidence=None,
        )
        assembled = {
            "run_id": "run-001",
            "result_binding": record["result_binding"],
        }
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "assemble_replay_inputs",
            return_value=assembled,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "create_evidence_snapshot"
        ) as create, self.assertRaises(ProducerPublicationIndeterminate):
            verify_synthetic_producer(
                surface="complete_exposure_ordering",
                inputs=dummy,
                allowed_source_roots=[Path("/tmp")],
                runtime_root=ROOT,
                snapshot_root=snapshots,
                evidence_root=evidence,
            )
        create.assert_not_called()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            self.assertEqual(
                record,
                validate_synthetic_producer_evidence(
                    surface="complete_exposure_ordering",
                    run_id="run-001",
                    result_sha256=SHA,
                    evidence_root=evidence,
                    snapshot_root=snapshots,
                ),
            )

    def test_private_revocation_publication_is_exact_and_recoverable(self):
        original_fsync = os.fsync
        for failed_call in (None, 1, 2):
            with self.subTest(failed_call=failed_call):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                base = Path(temporary.name)
                evidence = base / "evidence"
                snapshots = base / "snapshots"
                evidence.mkdir()
                snapshots.mkdir()
                record = valid_record()
                with patch(
                    "audience_panel_builder.population.validation."
                    "producer_evidence._validate_snapshot"
                ):
                    _publish_receipt(
                        record,
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    )
                calls = 0

                def maybe_fail(fd: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise OSError("revocation fsync uncertainty")
                    original_fsync(fd)

                if failed_call is None:
                    marker = _publish_revocation(
                        surface="complete_exposure_ordering",
                        run_id="run-001",
                        result_sha256=SHA,
                        evidence_root=evidence,
                    )
                else:
                    with patch(
                        "audience_panel_builder.population.validation."
                        "producer_evidence.os.fsync",
                        side_effect=maybe_fail,
                    ), self.assertRaises(ProducerPublicationIndeterminate):
                        _publish_revocation(
                            surface="complete_exposure_ordering",
                            run_id="run-001",
                            result_sha256=SHA,
                            evidence_root=evidence,
                        )
                    marker = (
                        recover_synthetic_producer_revocation_publication(
                            surface="complete_exposure_ordering",
                            run_id="run-001",
                            result_sha256=SHA,
                            evidence_root=evidence,
                        )
                    )
                self.assertEqual("revoked", marker["status"])
                with self.assertRaises(ProducerAuthenticationError):
                    validate_synthetic_producer_evidence(
                        surface="complete_exposure_ordering",
                        run_id="run-001",
                        result_sha256=SHA,
                        evidence_root=evidence,
                        snapshot_root=snapshots,
                    )

    def test_revocation_publication_rejects_byte_identical_replacement(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        original_recover = _recover_revocation

        def replace_then_recover(**kwargs):
            path = evidence / _revocation_name(
                "complete_exposure_ordering", "run-001", SHA
            )
            raw = path.read_bytes()
            path.unlink()
            path.write_bytes(raw)
            os.chmod(path, 0o400)
            return original_recover(**kwargs)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_recover_revocation",
            side_effect=replace_then_recover,
        ), self.assertRaises(ProducerAuthenticationError):
            _publish_revocation(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
            )

    def test_revocation_recovery_is_flat_closed_and_denies_claim(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        receipt_id = _receipt_id(
            "complete_exposure_ordering", "run-001", SHA
        )
        marker = {
            "schema_version": "producer-evidence-publication-state-v1",
            "receipt_id": receipt_id,
            "producer_evidence_sha256": record["producer_evidence_sha256"],
            "status": "revoked",
        }
        path = evidence / _revocation_name(
            "complete_exposure_ordering", "run-001", SHA
        )
        path.write_bytes(canonical_json_bytes(marker))
        os.chmod(path, 0o400)
        self.assertEqual(
            marker,
            recover_synthetic_producer_revocation_publication(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
            ),
        )
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "os.fsync",
            side_effect=OSError("revocation durability uncertain"),
        ), self.assertRaises(ProducerPublicationIndeterminate):
            recover_synthetic_producer_revocation_publication(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
            )
        self.assertEqual(
            marker,
            recover_synthetic_producer_revocation_publication(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
            ),
        )
        with self.assertRaises(ProducerAuthenticationError):
            recover_synthetic_producer_evidence_publication(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )
        with self.assertRaises(ProducerAuthenticationError):
            validate_synthetic_producer_evidence(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )

    def test_trusted_root_symlink_mode_and_receipt_hardlink_fail_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()
        record = valid_record()
        link = base / "evidence-link"
        link.symlink_to(evidence, target_is_directory=True)
        with self.assertRaises(ProducerAuthenticationError):
            _publish_receipt(
                record, evidence_root=link, snapshot_root=snapshots
            )
        os.chmod(evidence, 0o777)
        with self.assertRaises(ProducerAuthenticationError):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        os.chmod(evidence, 0o700)
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ):
            _publish_receipt(
                record, evidence_root=evidence, snapshot_root=snapshots
            )
        receipt = evidence / _receipt_name(
            "complete_exposure_ordering", "run-001", SHA
        )
        os.link(receipt, evidence / "external-hardlink")
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_validate_snapshot"
        ), self.assertRaises(ProducerAuthenticationError):
            validate_synthetic_producer_evidence(
                surface="complete_exposure_ordering",
                run_id="run-001",
                result_sha256=SHA,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )

    def test_cli_is_closed_and_maps_exact_failure_exit_codes(self):
        path = (
            ROOT
            / "skills/audience-panel-builder/scripts/"
            "verify-panel-synthetic-producer.py"
        )
        spec = importlib.util.spec_from_file_location("producer_cli", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        base = [
            "verify",
            "--surface", "complete_exposure_ordering",
            "--study-manifest", "/tmp/study-manifest.json",
            "--accepted-responses", "/tmp/panelist-responses.jsonl",
            "--raw-provider-returns", "/tmp/raw-provider-returns.jsonl",
            "--rejected-attempts", "/tmp/rejected-attempts.jsonl",
            "--cumulative-dispatch-audit", "/tmp/dispatch-audit.jsonl",
            "--result", "/tmp/screening-model-results.json",
            "--allowed-source-root", "/tmp",
            "--runtime-root", "/tmp",
            "--snapshot-root", "/tmp",
            "--evidence-root", "/tmp",
        ]
        for error, code in (
            (ProducerAuthenticationError("auth"), 2),
            (ProducerOutputCollision("collision"), 3),
            (ProducerRuntimeUnavailable("runtime"), 4),
            (ProducerPublicationIndeterminate("durability"), 5),
        ):
            with self.subTest(code=code), patch.object(
                module, "verify_synthetic_producer", side_effect=error
            ), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(code, module.main(base))

        record = valid_record()
        stdout = io.StringIO()
        with patch.object(
            module, "verify_synthetic_producer", return_value=record
        ), redirect_stderr(io.StringIO()), redirect_stdout(stdout):
            self.assertEqual(0, module.main(base))
        self.assertEqual(
            {
                "status",
                "evidence_path",
                "producer_evidence_sha256",
                "result_sha256",
                "result_bytes_sha256",
            },
            set(json.loads(stdout.getvalue())),
        )


@unittest.skipUnless(
    os.environ.get("AUDIENCE_TIER4_REAL_PROVIDER") == "1",
    "real complete receipt publication is CI opt-in",
)
class Tier4SyntheticProducerEvidenceRealProviderTests(unittest.TestCase):
    def test_real_complete_producer_publishes_validates_and_recovers(self):
        from conformance.test_task9_integration import (
            complete_calibration_policy,
            complete_job,
            complete_manifest,
            complete_response,
        )
        from conformance.test_task9_review_fixes_wave2 import (
            _bind_without_semantic_validation,
            _raw_for_response,
            _rejected_from_raw,
        )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        sources = base / "sources"
        snapshots = base / "snapshots"
        evidence = base / "evidence"
        for path in (sources, snapshots, evidence):
            path.mkdir()
        manifest = complete_manifest()
        responses = [
            complete_response(
                index,
                ["creative-a", "creative-b", "creative-c", "creative-d"],
            )
            for index in range(1, 10)
        ]
        # One successful retry keeps rejected-attempt lineage nonempty without
        # adding a tenth dispatch beyond the protected screening reserve.
        accepted = responses[0]["runtime_attempts"][0]
        accepted["attempt_number"] = 2
        accepted["attempt_id"] = accepted["provider_return_id"] = (
            accepted["provider_return_id"].replace("-a1-", "-a2-")
        )
        responses[0]["per_creative_reactions"][0][
            "source_provenance"
        ]["provider_return_id"] = accepted["provider_return_id"]
        rejected = {
            "attempt_id": "raw-S1-0001-r1-a1-ce-01",
            "stage": "reaction",
            "position_seen": 1,
            "attempt_number": 1,
            "provider_return_id": "raw-S1-0001-r1-a1-ce-01",
            "outcome": "rejected",
            "validation_errors": ["schema mismatch"],
        }
        responses[0]["runtime_attempts"].insert(0, rejected)
        raw_returns = [
            raw for response in responses for raw in _raw_for_response(response)
        ]
        contract = {
            "retry_limit_per_return": 1,
            "reaction_positions": [1, 2, 3, 4],
            "comparison_required": True,
        }
        workflow = {
            "status": "complete",
            "responses": responses,
            "raw_provider_returns": raw_returns,
            "rejected_attempts": [_rejected_from_raw(raw_returns[0])],
            "dispatch_audit": [
                {
                    "record_type": response["record_type"],
                    "synthetic_replicate_id": response[
                        "synthetic_replicate_id"
                    ],
                    "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                    "accepted": True,
                    "attempt_contract": contract,
                    "reaction_attempts": (
                        [2, 1, 1, 1]
                        if index == 0
                        else [1, 1, 1, 1]
                    ),
                    "comparison_attempts": 1,
                }
                for index, response in enumerate(responses)
            ],
            "requested_replicates": 9,
            "completed_replicates": 9,
        }
        run_dir, *_rest = _bind_without_semantic_validation(
            sources, manifest, workflow
        )
        jobs = base / "screening-jobs.json"
        jobs.write_bytes(canonical_json_bytes({
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": [
                complete_job(response) for response in responses
            ],
        }))
        recovery = base / "recovery.json"
        recovery.write_bytes(
            canonical_json_bytes(complete_calibration_policy())
        )
        projection = base / "screening-response-projection.jsonl"
        projection.write_bytes(
            b"".join(canonical_json_bytes(response) for response in responses)
        )
        result = base / "screening-model-results.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "skills/audience-ad-testing-lab/scripts/"
                    "aggregate-screening.py"
                ),
                "screening",
                "--manifest", str(run_dir / "study-manifest.json"),
                "--jobs", str(jobs),
                "--responses", str(projection),
                "--recovery-config", str(recovery),
                "--output", str(result),
            ],
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONPATH": str(
                    ROOT / "skills/audience-ad-testing-lab/scripts"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        inputs = ProducerReplayInputs(
            study_manifest=run_dir / "study-manifest.json",
            accepted_responses=run_dir / "panelist-responses.jsonl",
            raw_provider_returns=run_dir / "raw-provider-returns.jsonl",
            rejected_attempts=run_dir / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=run_dir / "dispatch-audit.jsonl",
            result=result,
            screening_jobs=jobs,
            recovery_configuration=recovery,
            command_dispatch_audit_input=None,
            screening_result=None,
            screening_producer_evidence=None,
        )
        assembled = assemble_replay_inputs(
            surface="complete_exposure_ordering", paths=inputs
        )
        failure_snapshots = base / "failure-snapshots"
        failure_evidence = base / "failure-evidence"
        failure_snapshots.mkdir()
        failure_evidence.mkdir()
        evidence_identity = failure_evidence.stat()

        def assert_no_failure_receipt() -> None:
            self.assertEqual([], list(failure_evidence.iterdir()))
            current = failure_evidence.stat()
            self.assertEqual(
                (
                    evidence_identity.st_dev,
                    evidence_identity.st_ino,
                    evidence_identity.st_mode,
                ),
                (current.st_dev, current.st_ino, current.st_mode),
            )

        call = {
            "surface": "complete_exposure_ordering",
            "inputs": inputs,
            "allowed_source_roots": [sources, base],
            "runtime_root": ROOT,
            "snapshot_root": failure_snapshots,
            "evidence_root": failure_evidence,
        }
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "create_evidence_snapshot",
            side_effect=OSError("snapshot creation failed"),
        ), self.assertRaises(ProducerEvidenceError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=ProducerAuthenticationError(
                "snapshot open failed"
            ),
        ), self.assertRaises(ProducerAuthenticationError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "recover_evidence_snapshot_publication",
            side_effect=ProducerPublicationIndeterminate(
                "snapshot recovery remains indeterminate"
            ),
        ), self.assertRaises(ProducerPublicationIndeterminate):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "recover_evidence_snapshot_publication",
            side_effect=ProducerAuthenticationError(
                "snapshot entry replaced during recovery"
            ),
        ), self.assertRaises(ProducerAuthenticationError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        def mismatched_archive(*args, **kwargs):
            sources_map, binding_map, replay_map = _archive_inputs(
                *args, **kwargs
            )
            changed = json.loads(json.dumps(binding_map))
            changed["study_manifest"]["raw_bytes_sha256"] = (
                "sha256:" + ("cd" * 32)
            )
            return sources_map, changed, replay_map

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_archive_inputs",
            side_effect=mismatched_archive,
        ), self.assertRaises(ProducerAuthenticationError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        original_open = open_evidence_snapshot

        @contextmanager
        def expired_snapshot(**kwargs):
            with original_open(**kwargs) as capability:
                expired = capability
            yield expired

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=expired_snapshot,
        ), self.assertRaises(ProducerEvidenceError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_resolve_runtime_for_replay",
            side_effect=ProducerEvidenceError("extraction entry failed"),
        ), self.assertRaises(ProducerEvidenceError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        @contextmanager
        def cleanup_failure(**kwargs):
            with original_open(**kwargs) as capability:
                yield capability
            raise ProducerEvidenceError("extraction cleanup failed")

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=cleanup_failure,
        ), self.assertRaises(ProducerEvidenceError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        for label, error in (
            (
                "unavailable",
                ProducerRuntimeUnavailable("sandbox unavailable"),
            ),
            (
                "nonzero",
                ProducerAuthenticationError("producer exited nonzero"),
            ),
            (
                "timeout",
                ProducerRuntimeUnavailable("producer timed out"),
            ),
        ):
            with self.subTest(replay_failure=label), patch(
                "audience_panel_builder.population.validation."
                "producer_evidence.replay_producer",
                side_effect=error,
            ), self.assertRaises(type(error)):
                verify_synthetic_producer(**call)
            assert_no_failure_receipt()

        wrong_binding = dict(assembled["result_binding"])
        wrong_binding["raw_bytes_sha256"] = "sha256:" + ("cd" * 32)
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "replay_producer",
            return_value=wrong_binding,
        ), self.assertRaises(ProducerAuthenticationError):
            verify_synthetic_producer(**call)
        assert_no_failure_receipt()

        gap_snapshots = base / "gap-snapshots"
        gap_attacker = base / "gap-attacker-snapshots"
        gap_snapshots.mkdir()
        gap_attacker.mkdir()
        gap_extra = base / "gap-unbound-extra.json"
        gap_extra.write_bytes(canonical_json_bytes({"unbound": True}))
        attacker_built = False
        expected_gap_snapshot: EvidenceSnapshot | None = None

        def build_then_prepare_replacement(*args, **kwargs):
            nonlocal attacker_built, expected_gap_snapshot
            expected_snapshot = _create_or_recover_exact_snapshot(
                *args, **kwargs
            )
            expected_gap_snapshot = expected_snapshot
            attacker_sources = dict(kwargs["sources"])
            attacker_sources["unbound-extra.json"] = gap_extra
            create_evidence_snapshot(
                surface=kwargs["surface"],
                run_id=kwargs["run_id"],
                result_sha256=kwargs["result_sha256"],
                sources=attacker_sources,
                bindings=kwargs["bindings"],
                allowed_roots=kwargs["allowed_roots"],
                snapshot_root=gap_attacker,
            )
            attacker_built = True
            return expected_snapshot

        @contextmanager
        def open_same_euid_replacement(**kwargs):
            parked = base / "gap-original-snapshots"
            gap_snapshots.rename(parked)
            gap_attacker.rename(gap_snapshots)
            try:
                with open_evidence_snapshot(**kwargs) as capability:
                    yield capability
            finally:
                gap_snapshots.rename(gap_attacker)
                parked.rename(gap_snapshots)

        gap_call = {
            **call,
            "snapshot_root": gap_snapshots,
            "evidence_root": base / "gap-evidence",
        }
        gap_call["evidence_root"].mkdir()
        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_create_or_recover_exact_snapshot",
            side_effect=build_then_prepare_replacement,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=open_same_euid_replacement,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_resolve_runtime_for_replay",
        ) as resolve_runtime, patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "replay_producer",
        ) as replay, self.assertRaisesRegex(
            ProducerAuthenticationError,
            "live evidence snapshot does not equal",
        ):
            verify_synthetic_producer(**gap_call)
        self.assertTrue(attacker_built)
        resolve_runtime.assert_not_called()
        replay.assert_not_called()
        self.assertEqual([], list(gap_call["evidence_root"].iterdir()))
        self.assertIsNotNone(expected_gap_snapshot)
        self.assertEqual(
            expected_gap_snapshot,
            recover_evidence_snapshot_publication(
                surface="complete_exposure_ordering",
                run_id=str(assembled["run_id"]),
                result_sha256=str(
                    assembled["result_binding"][
                        "canonical_document_sha256"
                    ]
                ),
                snapshot_root=gap_snapshots,
            ),
        )

        result_sha256 = str(
            assembled["result_binding"]["canonical_document_sha256"]
        )
        real_fsync = os.fsync
        fsync_calls = 0

        def interrupt_snapshot_publication(*args, **kwargs):
            nonlocal fsync_calls

            def fail_first_recovery_fsync(fd):
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 5:
                    raise OSError("snapshot durability uncertainty")
                return real_fsync(fd)

            with patch(
                "audience_panel_builder.population.validation."
                "evidence_snapshot.os.fsync",
                side_effect=fail_first_recovery_fsync,
            ):
                return create_evidence_snapshot(*args, **kwargs)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "create_evidence_snapshot",
            side_effect=interrupt_snapshot_publication,
        ), self.assertRaises(ProducerPublicationIndeterminate):
            verify_synthetic_producer(
                surface="complete_exposure_ordering",
                inputs=inputs,
                allowed_source_roots=[sources, base],
                runtime_root=ROOT,
                snapshot_root=snapshots,
                evidence_root=evidence,
            )
        cli = (
            ROOT
            / "skills/audience-panel-builder/scripts/"
            "verify-panel-synthetic-producer.py"
        )
        recovered_snapshot = subprocess.run(
            [
                sys.executable, str(cli), "recover-snapshot",
                "--surface", "complete_exposure_ordering",
                "--run-id", str(assembled["run_id"]),
                "--result-sha256", result_sha256,
                "--snapshot-root", str(snapshots),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(
            0, recovered_snapshot.returncode, recovered_snapshot.stderr
        )
        receipt_fsync_calls = 0

        def interrupt_receipt_publication(*args, **kwargs):
            nonlocal receipt_fsync_calls

            def fail_receipt_file_fsync(fd):
                nonlocal receipt_fsync_calls
                receipt_fsync_calls += 1
                if receipt_fsync_calls == 1:
                    raise OSError("receipt durability uncertainty")
                return real_fsync(fd)

            with patch(
                "audience_panel_builder.population.validation."
                "producer_evidence.os.fsync",
                side_effect=fail_receipt_file_fsync,
            ):
                return _publish_receipt(*args, **kwargs)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_publish_receipt",
            side_effect=interrupt_receipt_publication,
        ), self.assertRaises(ProducerPublicationIndeterminate):
            verify_synthetic_producer(
                surface="complete_exposure_ordering",
                inputs=inputs,
                allowed_source_roots=[sources, base],
                runtime_root=ROOT,
                snapshot_root=snapshots,
                evidence_root=evidence,
            )
        identity = [
            "--surface", "complete_exposure_ordering",
            "--run-id", str(assembled["run_id"]),
            "--result-sha256", result_sha256,
        ]
        recovered_receipt = subprocess.run(
            [
                sys.executable, str(cli), "recover-receipt", *identity,
                "--evidence-root", str(evidence),
                "--snapshot-root", str(snapshots),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(
            0, recovered_receipt.returncode, recovered_receipt.stderr
        )
        record = validate_synthetic_producer_evidence(
            surface="complete_exposure_ordering",
            run_id=str(assembled["run_id"]),
            result_sha256=result_sha256,
            evidence_root=evidence,
            snapshot_root=snapshots,
        )
        self.assertEqual(
            result_sha256,
            record["result_binding"]["canonical_document_sha256"],
        )
        self.assertEqual(
            record,
            validate_synthetic_producer_evidence(
                surface="complete_exposure_ordering",
                run_id=str(record["run_id"]),
                result_sha256=result_sha256,
                evidence_root=evidence,
                snapshot_root=snapshots,
            ),
        )
        self.assertEqual(
            record,
            recover_synthetic_producer_evidence_publication(
                surface="complete_exposure_ordering",
                run_id=str(record["run_id"]),
                result_sha256=result_sha256,
                evidence_root=evidence,
                snapshot_root=snapshots,
            ),
        )
        complete_snapshot = recover_evidence_snapshot_publication(
            surface="complete_exposure_ordering",
            run_id=str(record["run_id"]),
            result_sha256=result_sha256,
            snapshot_root=snapshots,
        )
        complete_commit = complete_snapshot.commit_path
        complete_commit_raw = complete_commit.read_bytes()
        complete_commit_identity = complete_commit.stat()
        changed_commit_raw = changed_frozen_snapshot_commit(
            complete_snapshot
        )
        evidence_before_gap = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_ino,
            )
            for path in evidence.iterdir()
        }
        original_open_for_claim = open_evidence_snapshot

        @contextmanager
        def open_changed_complete_commit(**kwargs):
            parked = complete_commit.with_name(
                complete_commit.name + ".saved-a"
            )
            complete_commit.rename(parked)
            complete_commit.write_bytes(changed_commit_raw)
            os.chmod(complete_commit, 0o400)
            try:
                with original_open_for_claim(**kwargs) as capability:
                    yield capability
            finally:
                complete_commit.unlink()
                parked.rename(complete_commit)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=open_changed_complete_commit,
        ), patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "_resolve_runtime_for_replay",
        ) as claim_runtime, self.assertRaisesRegex(
            ProducerAuthenticationError,
            "live evidence snapshot does not equal",
        ):
            validate_synthetic_producer_evidence(
                surface="complete_exposure_ordering",
                run_id=str(record["run_id"]),
                result_sha256=result_sha256,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )
        claim_runtime.assert_not_called()
        self.assertEqual(complete_commit_raw, complete_commit.read_bytes())
        restored_commit_identity = complete_commit.stat()
        self.assertEqual(
            (
                complete_commit_identity.st_dev,
                complete_commit_identity.st_ino,
                complete_commit_identity.st_mode,
            ),
            (
                restored_commit_identity.st_dev,
                restored_commit_identity.st_ino,
                restored_commit_identity.st_mode,
            ),
        )
        self.assertEqual(
            evidence_before_gap,
            {
                path.name: (
                    path.read_bytes(),
                    path.stat().st_mode,
                    path.stat().st_ino,
                )
                for path in evidence.iterdir()
            },
        )
        self.assertEqual(
            complete_snapshot,
            recover_evidence_snapshot_publication(
                surface="complete_exposure_ordering",
                run_id=str(record["run_id"]),
                result_sha256=result_sha256,
                snapshot_root=snapshots,
            ),
        )
        for mode, roots, keys in (
            (
                "recover-snapshot",
                ["--snapshot-root", str(snapshots)],
                {
                    "status", "snapshot_id", "snapshot_sha256",
                    "archive_sha256",
                },
            ),
            (
                "recover-receipt",
                [
                    "--evidence-root", str(evidence),
                    "--snapshot-root", str(snapshots),
                ],
                {
                    "status", "evidence_path",
                    "producer_evidence_sha256", "result_sha256",
                    "result_bytes_sha256",
                },
            ),
        ):
            completed = subprocess.run(
                [sys.executable, str(cli), mode, *identity, *roots],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=240,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(keys, set(json.loads(completed.stdout)))

        retry = subprocess.run(
            [
                sys.executable, str(cli), "verify",
                "--surface", "complete_exposure_ordering",
                "--study-manifest", str(inputs.study_manifest),
                "--accepted-responses", str(inputs.accepted_responses),
                "--raw-provider-returns", str(inputs.raw_provider_returns),
                "--rejected-attempts", str(inputs.rejected_attempts),
                "--cumulative-dispatch-audit",
                str(inputs.cumulative_dispatch_audit),
                "--result", str(inputs.result),
                "--screening-jobs", str(inputs.screening_jobs),
                "--recovery-configuration",
                str(inputs.recovery_configuration),
                "--allowed-source-root", str(sources),
                "--allowed-source-root", str(base),
                "--runtime-root", str(ROOT),
                "--snapshot-root", str(snapshots),
                "--evidence-root", str(evidence),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(5, retry.returncode, retry.stderr)
        self.assertEqual(b"", retry.stdout)

        revocation_fsync_calls = 0

        def fail_revocation_file_fsync(fd):
            nonlocal revocation_fsync_calls
            revocation_fsync_calls += 1
            if revocation_fsync_calls == 1:
                raise OSError("revocation durability uncertainty")
            return real_fsync(fd)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "os.fsync",
            side_effect=fail_revocation_file_fsync,
        ), self.assertRaises(ProducerPublicationIndeterminate):
            _publish_revocation(
                surface="complete_exposure_ordering",
                run_id=str(record["run_id"]),
                result_sha256=result_sha256,
                evidence_root=evidence,
            )
        revoked = subprocess.run(
            [
                sys.executable, str(cli), "recover-revocation",
                *identity, "--evidence-root", str(evidence),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(0, revoked.returncode, revoked.stderr)
        self.assertEqual(
            {"status", "receipt_id", "producer_evidence_sha256"},
            set(json.loads(revoked.stdout)),
        )
        denied = subprocess.run(
            [
                sys.executable, str(cli), "recover-receipt",
                *identity,
                "--evidence-root", str(evidence),
                "--snapshot-root", str(snapshots),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(2, denied.returncode)
        self.assertEqual(b"", denied.stdout)

    def test_real_maxdiff_and_recursive_pairwise_receipts(self):
        from conformance.test_maxdiff import (
            full_job_for_response,
            full_response_for_block,
            matching_manifest as maxdiff_manifest,
            recovery_config,
        )
        from conformance.test_pairwise import (
            boundary_fixture,
            matching_manifest as pairwise_manifest,
        )
        from conformance.test_tier4_replay_inputs import (
            _accepted_workflow,
        )
        from conformance.test_task9_review_fixes_wave2 import (
            _bind_without_semantic_validation,
        )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        evidence = base / "evidence"
        snapshots = base / "snapshots"
        evidence.mkdir()
        snapshots.mkdir()

        max_root = base / "maxdiff"
        max_root.mkdir()
        run_id = "screening-acme-q3-001"
        max_responses = [
            full_response_for_block(
                ["V1", "V2", "V3", "V4"], index, study_id=run_id
            )
            for index in range(1, 13)
        ]
        for index, response in enumerate(max_responses, 1):
            response["persona_archetype_id"] = (
                f"A{((index - 1) // 4) + 1}"
            )
            creative_ids = ["V1", "V2", "V3", "V4"]
            choice = response["comparative_choice"]
            choice["best_variation_id"] = creative_ids[(index - 1) % 4]
            choice["weakest_variation_id"] = creative_ids[(index + 1) % 4]
            provider_ids = {}
            for attempt in response["runtime_attempts"]:
                old = attempt["provider_return_id"]
                new = f"{old}-{index:02d}"
                provider_ids[old] = new
                attempt["provider_return_id"] = new
                attempt["attempt_id"] = f"{attempt['attempt_id']}-{index:02d}"
            for reaction in response["per_creative_reactions"]:
                provenance = reaction["source_provenance"]
                provenance["provider_return_id"] = provider_ids[
                    provenance["provider_return_id"]
                ]
            comparison = response["comparative_choice"][
                "source_provenance"
            ]
            comparison["provider_return_id"] = provider_ids[
                comparison["provider_return_id"]
            ]
        max_manifest = maxdiff_manifest(study_id=run_id)
        max_manifest["synthetic_replicate_capacity"][
            "screening_planned"
        ] = len(max_responses) + 1
        max_manifest["maximum_synthetic_panelists"] = (
            len(max_responses) + 1
            + max_manifest["synthetic_replicate_capacity"][
                "boundary_reserved"
            ]
            + max_manifest["synthetic_replicate_capacity"][
                "finalist_reserved"
            ]
        )
        max_run, *_ = _bind_without_semantic_validation(
            max_root, max_manifest, _accepted_workflow(max_responses)
        )
        planned_jobs = [
            full_job_for_response(response)
            for response in max_responses
        ]
        exhausted_job = json.loads(json.dumps(planned_jobs[0]))
        exhausted_job.update({
            "response_id": "response-exhausted-authorized",
            "synthetic_replicate_id": "replicate-exhausted-authorized",
            "dispatch_id": "dispatch-exhausted-authorized",
        })
        planned_jobs.append(exhausted_job)
        max_jobs = max_root / "screening-jobs.json"
        max_jobs.write_bytes(canonical_json_bytes({
            "study_id": run_id,
            "method": "partial_exposure_maxdiff",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": planned_jobs,
        }))
        config = recovery_config()
        config["calibration_status"] = "calibrated"
        config["library_size_bands"] = [{
            "name": "small_calibrated_library",
            "minimum": 4,
            "maximum": 10,
        }]
        config["shortlist_size_bands"] = [{
            "name": "small_calibrated_shortlist",
            "minimum": 2,
            "maximum": 3,
        }]
        config["utility_separation_band"][
            "maximum_log_utility_gap"
        ] = 100.0
        recovery = max_root / "recovery.json"
        recovery.write_bytes(canonical_json_bytes(config))
        max_result = max_root / "screening-model-results.json"
        command_audit = max_root / "command-dispatch-audit.jsonl"
        command_audit.write_bytes(
            (max_run / "dispatch-audit.jsonl").read_bytes()
        )
        max_paths = ProducerReplayInputs(
            study_manifest=max_run / "study-manifest.json",
            accepted_responses=max_run / "panelist-responses.jsonl",
            raw_provider_returns=max_run / "raw-provider-returns.jsonl",
            rejected_attempts=max_run / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=max_run / "dispatch-audit.jsonl",
            result=max_result,
            screening_jobs=max_jobs,
            recovery_configuration=recovery,
            command_dispatch_audit_input=command_audit,
            screening_result=None,
            screening_producer_evidence=None,
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "skills/audience-ad-testing-lab/scripts/"
                    "aggregate-screening.py"
                ),
                "screening",
                "--manifest", str(max_paths.study_manifest),
                "--jobs", str(max_paths.screening_jobs),
                "--responses", str(max_paths.accepted_responses),
                "--dispatch-audit", str(command_audit),
                "--recovery-config", str(recovery),
                "--output", str(max_paths.result),
            ],
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONPATH": str(
                    ROOT / "skills/audience-ad-testing-lab/scripts"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        max_record = verify_synthetic_producer(
            surface="maxdiff_screening_ordering",
            inputs=max_paths,
            allowed_source_roots=[ROOT, max_root],
            runtime_root=ROOT,
            snapshot_root=snapshots,
            evidence_root=evidence,
        )
        max_result_sha = str(
            max_record["result_binding"]["canonical_document_sha256"]
        )
        max_receipt = evidence / _receipt_name(
            "maxdiff_screening_ordering",
            str(max_record["run_id"]),
            max_result_sha,
        )

        pair_root = base / "pairwise"
        pair_root.mkdir()
        screening = json.loads(max_paths.result.read_bytes())
        self.assertIn("boundary_plan", screening, screening)
        assignments = screening["boundary_plan"][
            "predeclared_pair_assignments"
        ]
        pair_responses = []
        for index, (template, assignment) in enumerate(
            zip(boundary_fixture()[:4], assignments[:4], strict=True),
            1,
        ):
            response = json.loads(json.dumps(template))
            old_ids = list(response["assigned_variation_ids"])
            new_ids = list(assignment["variation_ids"])
            replacements = dict(zip(old_ids, new_ids, strict=True))
            response.update({
                "study_id": run_id,
                "response_id": f"boundary-response-{index:02d}",
                "synthetic_replicate_id": (
                    f"boundary-replicate-{index:02d}"
                ),
                "reviewer_dispatch_id": (
                    f"boundary-dispatch-{index:02d}"
                ),
                "assigned_variation_ids": new_ids,
                "shown_order": [
                    replacements[item] for item in response["shown_order"]
                ],
                "blind_labels": {
                    replacements[creative_id]: label
                    for creative_id, label in response["blind_labels"].items()
                },
                "pair_assignment_id": assignment["pair_assignment_id"],
                "boundary_wave": assignment["wave"],
            })
            for reaction in response["per_creative_reactions"]:
                reaction["variation_id"] = replacements[
                    reaction["variation_id"]
                ]
                reaction["reaction_id"] = (
                    f"{reaction['reaction_id']}-{index:02d}"
                )
            choice = response["pairwise_choice"]
            preferred = choice["preferred_variation_id"]
            if preferred:
                choice["preferred_variation_id"] = replacements[preferred]
            choice["frozen_reaction_ids"] = [
                reaction["reaction_id"]
                for reaction in response["per_creative_reactions"]
            ]
            pair_responses.append(response)
        pair_manifest = pairwise_manifest(
            records=pair_responses,
            creative_ids=("V1", "V2", "V3", "V4"),
            shortlist_size=2,
        )
        pair_manifest["synthetic_replicate_capacity"] = json.loads(
            json.dumps(max_manifest["synthetic_replicate_capacity"])
        )
        pair_manifest["maximum_synthetic_panelists"] = max_manifest[
            "maximum_synthetic_panelists"
        ]
        pair_run, *_ = _bind_without_semantic_validation(
            pair_root,
            pair_manifest,
            _accepted_workflow(pair_responses),
        )
        pair_screening = pair_root / "screening-model-results.json"
        pair_screening.write_bytes(max_paths.result.read_bytes())
        pair_result = pair_root / "boundary-results.json"
        pair_paths = ProducerReplayInputs(
            study_manifest=pair_run / "study-manifest.json",
            accepted_responses=pair_run / "panelist-responses.jsonl",
            raw_provider_returns=pair_run / "raw-provider-returns.jsonl",
            rejected_attempts=pair_run / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=pair_run / "dispatch-audit.jsonl",
            result=pair_result,
            screening_jobs=None,
            recovery_configuration=None,
            command_dispatch_audit_input=None,
            screening_result=pair_screening,
            screening_producer_evidence=max_receipt,
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "skills/audience-ad-testing-lab/scripts/"
                    "aggregate-screening.py"
                ),
                "boundary",
                "--manifest", str(pair_paths.study_manifest),
                "--screening-results", str(pair_paths.screening_result),
                "--responses", str(pair_paths.accepted_responses),
                "--output", str(pair_paths.result),
            ],
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONPATH": str(
                    ROOT / "skills/audience-ad-testing-lab/scripts"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        self.assertEqual(
            0,
            completed.returncode,
            (completed.stderr, pair_paths.result.read_text(encoding="utf-8")),
        )
        pair_record = verify_synthetic_producer(
            surface="pairwise_boundary_ordering",
            inputs=pair_paths,
            allowed_source_roots=[ROOT, pair_root, evidence],
            runtime_root=ROOT,
            snapshot_root=snapshots,
            evidence_root=evidence,
        )
        self.assertEqual(
            max_record["producer_evidence_sha256"],
            pair_record["input_bindings"]["screening_producer_evidence"][
                "producer_evidence_sha256"
            ],
        )
        self.assertEqual(
            pair_record,
            validate_synthetic_producer_evidence(
                surface="pairwise_boundary_ordering",
                run_id=str(pair_record["run_id"]),
                result_sha256=str(
                    pair_record["result_binding"][
                        "canonical_document_sha256"
                    ]
                ),
                evidence_root=evidence,
                snapshot_root=snapshots,
            ),
        )
        pair_result_sha = str(
            pair_record["result_binding"]["canonical_document_sha256"]
        )
        pair_snapshot = recover_evidence_snapshot_publication(
            surface="pairwise_boundary_ordering",
            run_id=str(pair_record["run_id"]),
            result_sha256=pair_result_sha,
            snapshot_root=snapshots,
        )
        pair_commit = pair_snapshot.commit_path
        pair_commit_raw = pair_commit.read_bytes()
        pair_commit_identity = pair_commit.stat()
        changed_pair_commit = changed_frozen_snapshot_commit(pair_snapshot)
        evidence_before_recursive_gap = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_ino,
            )
            for path in evidence.iterdir()
        }
        original_recursive_open = open_evidence_snapshot
        pairwise_open_count = 0
        recursive_member_resolutions = 0

        class TrackedRecursiveCapability:
            def __init__(self, capability):
                self.capability = capability

            def __getattr__(self, name):
                return getattr(self.capability, name)

            def resolve_member(self, binding_name):
                nonlocal recursive_member_resolutions
                recursive_member_resolutions += 1
                return self.capability.resolve_member(binding_name)

        @contextmanager
        def open_changed_recursive_commit(**kwargs):
            nonlocal pairwise_open_count
            if kwargs["surface"] != "pairwise_boundary_ordering":
                with original_recursive_open(**kwargs) as capability:
                    yield capability
                return
            pairwise_open_count += 1
            if pairwise_open_count != 2:
                with original_recursive_open(**kwargs) as capability:
                    yield capability
                return
            parked = pair_commit.with_name(pair_commit.name + ".saved-a")
            pair_commit.rename(parked)
            pair_commit.write_bytes(changed_pair_commit)
            os.chmod(pair_commit, 0o400)
            try:
                with original_recursive_open(**kwargs) as capability:
                    yield TrackedRecursiveCapability(capability)
            finally:
                pair_commit.unlink()
                parked.rename(pair_commit)

        with patch(
            "audience_panel_builder.population.validation.producer_evidence."
            "open_evidence_snapshot",
            side_effect=open_changed_recursive_commit,
        ), self.assertRaisesRegex(
            ProducerAuthenticationError,
            "live evidence snapshot does not equal",
        ):
            validate_synthetic_producer_evidence(
                surface="pairwise_boundary_ordering",
                run_id=str(pair_record["run_id"]),
                result_sha256=pair_result_sha,
                evidence_root=evidence,
                snapshot_root=snapshots,
            )
        self.assertEqual(2, pairwise_open_count)
        self.assertEqual(0, recursive_member_resolutions)
        self.assertEqual(pair_commit_raw, pair_commit.read_bytes())
        restored_pair_identity = pair_commit.stat()
        self.assertEqual(
            (
                pair_commit_identity.st_dev,
                pair_commit_identity.st_ino,
                pair_commit_identity.st_mode,
            ),
            (
                restored_pair_identity.st_dev,
                restored_pair_identity.st_ino,
                restored_pair_identity.st_mode,
            ),
        )
        self.assertEqual(
            evidence_before_recursive_gap,
            {
                path.name: (
                    path.read_bytes(),
                    path.stat().st_mode,
                    path.stat().st_ino,
                )
                for path in evidence.iterdir()
            },
        )
        self.assertEqual(
            pair_snapshot,
            recover_evidence_snapshot_publication(
                surface="pairwise_boundary_ordering",
                run_id=str(pair_record["run_id"]),
                result_sha256=pair_result_sha,
                snapshot_root=snapshots,
            ),
        )


if __name__ == "__main__":
    unittest.main()
