from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
)
from audience_panel_builder.population.validation import (  # noqa: E402
    replay_inputs as replay_inputs_module,
)
from audience_panel_builder.population.validation.replay_inputs import (  # noqa: E402
    ProducerReplayInputs,
    assemble_replay_inputs,
)
from audience_panel_builder.population.validation.producer_semantics import (  # noqa: E402
    REPLAY_BOOTSTRAP_SOURCE,
)
from conformance.test_task9_integration import (  # noqa: E402
    complete_manifest,
    complete_response,
)
from conformance.test_task9_review_fixes_wave2 import (  # noqa: E402
    _bind_without_semantic_validation,
    _exhausted_attempt,
    _raw_for_response,
    _rejected_from_raw,
    _retained_component,
)


SHA = "sha256:" + ("ab" * 32)
SURFACE_METHOD_STAGE = {
    "complete_exposure_ordering": ("complete_exposure", "screening"),
    "maxdiff_screening_ordering": ("partial_exposure_maxdiff", "screening"),
    "pairwise_boundary_ordering": ("partial_exposure_maxdiff", "boundary"),
}


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _jsonl(records: list[dict]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _read_fixture(name: str) -> dict:
    path = ROOT / "conformance" / "fixtures" / name
    if path.suffix == ".jsonl":
        return json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted_workflow(responses: list[dict]) -> dict:
    first = responses[0]
    positions = list(range(1, len(first["shown_order"]) + 1))
    exhausted = [
        _exhausted_attempt("provider-exhausted-attempt-1", 1),
        _exhausted_attempt("provider-exhausted-attempt-2", 2),
        *[_retained_component(position) for position in positions[1:]],
    ]
    exhausted = [
        item for item in exhausted if item.get("position_seen") in positions
    ]
    contract = {
        "retry_limit_per_return": 1,
        "reaction_positions": positions,
        "comparison_required": True,
    }
    audits = [
        {
            "record_type": response["record_type"],
            "synthetic_replicate_id": response["synthetic_replicate_id"],
            "reviewer_dispatch_id": response["reviewer_dispatch_id"],
            "accepted": True,
            "attempt_contract": deepcopy(contract),
            "reaction_attempts": [1] * len(positions),
            "comparison_attempts": 1,
        }
        for response in responses
    ]
    audits.append({
        "record_type": first["record_type"],
        "synthetic_replicate_id": "replicate-exhausted-authorized",
        "reviewer_dispatch_id": "dispatch-exhausted-authorized",
        "accepted": False,
        "attempt_contract": deepcopy(contract),
        "reaction_attempts": [2] + ([1] * (len(positions) - 1)),
        "comparison_attempts": 0,
    })
    return {
        "status": "incomplete",
        "responses": responses,
        "raw_provider_returns": [
            raw
            for response in responses
            for raw in _raw_for_response(response)
        ] + exhausted,
        "rejected_attempts": [
            _rejected_from_raw(item)
            for item in exhausted
            if item["accepted"] is False
        ],
        "dispatch_audit": audits,
        "requested_replicates": len(audits),
        "completed_replicates": len(responses),
    }


class ReplayFixture:
    def __init__(self, root: Path, surface: str):
        self.root = root
        self.surface = surface
        self.method, self.stage = SURFACE_METHOD_STAGE[surface]
        self.run_id = (
            "complete-acme-001"
            if surface == "complete_exposure_ordering"
            else "screening-acme-q3-001"
        )
        self.paths = self._build()

    def _responses(self) -> list[dict]:
        if self.surface == "complete_exposure_ordering":
            return [complete_response(2), complete_response(1)]
        filename = (
            "boundary-responses.jsonl"
            if self.stage == "boundary"
            else "screening-responses-valid.jsonl"
        )
        response = _read_fixture(filename)
        response["study_id"] = self.run_id
        return [response]

    def _manifest(self) -> dict:
        if self.surface == "complete_exposure_ordering":
            return complete_manifest()
        manifest = _read_fixture("manifest-valid.json")
        manifest["study_id"] = self.run_id
        return manifest

    def _copy(self, source: Path, directory: str, filename: str) -> Path:
        target = self.root / directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return target

    def _write_json(self, directory: str, filename: str, value: object) -> Path:
        target = self.root / directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_json_bytes(value))
        return target

    def _upstream_evidence(self, screening_result: Path) -> Path:
        result_raw = screening_result.read_bytes()
        result_binding = {
            "path": "screening-model-results.json",
            "raw_bytes_sha256": _digest(result_raw),
            "canonical_document_sha256": _digest(
                canonical_json_bytes(json.loads(result_raw))
            ),
            "record_count": None,
        }

        def binding(path: str, count: int | None = None) -> dict[str, object]:
            return {
                "path": path,
                "raw_bytes_sha256": SHA,
                "canonical_document_sha256": SHA,
                "record_count": count,
            }

        semantics = {
            "entry_point": (
                "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
            ),
            "subcommand": "screening",
            "bootstrap_sha256": _digest(
                REPLAY_BOOTSTRAP_SOURCE.encode("utf-8")
            ),
            "dependency_closure": [
                {
                    "path": (
                        "skills/audience-ad-testing-lab/scripts/"
                        "aggregate-screening.py"
                    ),
                    "byte_count": 100,
                    "raw_bytes_sha256": SHA,
                },
                {
                    "path": (
                        "skills/audience-ad-testing-lab/scripts/"
                        "audience_lab/__init__.py"
                    ),
                    "byte_count": 10,
                    "raw_bytes_sha256": "sha256:" + ("bc" * 32),
                },
            ],
            "runtime_fingerprint": {
                "python_implementation": "CPython",
                "python_version": "3.14.5",
                "numpy_version": "2.3.1",
                "scipy_version": "1.16.0",
                "platform_system": "Darwin",
                "platform_release": "25.5.0",
                "machine": "arm64",
                "numpy_build_sha256": "sha256:" + ("cd" * 32),
                "blas_lapack_sha256": "sha256:" + ("de" * 32),
            },
            "policy_bindings": {
                "maxdiff_configuration_sha256": "sha256:" + ("01" * 32),
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
        evidence = {
            "schema_version": "panel-synthetic-producer-evidence-v1",
            "surface": "maxdiff_screening_ordering",
            "method": "partial_exposure_maxdiff",
            "stage": "screening",
            "run_id": self.run_id,
            "frozen_at": "2026-07-27T12:00:00Z",
            "sealed_at": "2026-07-27T12:01:00Z",
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
                "recovery_configuration": binding("maxdiff-recovery.json"),
            },
            "result_binding": result_binding,
            "snapshot_binding": {
                "snapshot_id": (
                    f"maxdiff_screening_ordering--{self.run_id}--"
                    f"{result_binding['canonical_document_sha256'][7:]}"
                ),
                "snapshot_sha256": SHA,
                "archive_sha256": SHA,
            },
            "producer_evidence_sha256": None,
        }
        evidence["producer_evidence_sha256"] = sha256_json(evidence)
        return self._write_json(
            "upstream", "screening.producer-evidence.json", evidence
        )

    def _build(self) -> ProducerReplayInputs:
        source_root = self.root / "producer"
        source_root.mkdir()
        manifest = self._manifest()
        workflow = _accepted_workflow(self._responses())
        run_dir, _bound, _exports, _sources = _bind_without_semantic_validation(
            source_root, manifest, workflow
        )

        final_manifest = self._copy(
            run_dir / "study-manifest.json", "final", "study-manifest.json"
        )
        self.earlier_manifest = self._write_json(
            "producer-time", "study-manifest.json", manifest
        )
        accepted = self._copy(
            run_dir / "panelist-responses.jsonl",
            "lineage/accepted",
            "panelist-responses.jsonl",
        )
        raw = self._copy(
            run_dir / "raw-provider-returns.jsonl",
            "lineage/raw",
            "raw-provider-returns.jsonl",
        )
        rejected = self._copy(
            run_dir / "rejected-attempts.jsonl",
            "lineage/rejected",
            "rejected-attempts.jsonl",
        )
        audit = self._copy(
            run_dir / "dispatch-audit.jsonl",
            "lineage/audit",
            "dispatch-audit.jsonl",
        )
        result_name = (
            "boundary-results.json"
            if self.stage == "boundary"
            else "screening-model-results.json"
        )
        result = self._write_json(
            "result",
            result_name,
            {"study_id": self.run_id, "ranked_ids": ["creative-a"]},
        )
        jobs = recovery = command_audit = screening_result = evidence = None
        if self.stage == "screening":
            jobs = self._write_json(
                "jobs",
                "screening-jobs.json",
                {
                    "study_id": self.run_id,
                    "method": self.method,
                    "record_type": "screening_response",
                    "synthetic_replicate_jobs": [{"fixture": True}],
                },
            )
            recovery = self._write_json(
                "configuration", "recovery.json", {"version": "fixture-v1"}
            )
            command_audit = self.root / "command" / "command-dispatch-audit.jsonl"
            command_audit.parent.mkdir(parents=True)
            command_audit.write_bytes(_jsonl([{"dispatch_id": "command-audit"}]))
        else:
            screening_result = self._write_json(
                "upstream",
                "screening-model-results.json",
                {"study_id": self.run_id, "ranked_ids": ["creative-a"]},
            )
            evidence = self._upstream_evidence(screening_result)
        return ProducerReplayInputs(
            study_manifest=final_manifest,
            accepted_responses=accepted,
            raw_provider_returns=raw,
            rejected_attempts=rejected,
            cumulative_dispatch_audit=audit,
            result=result,
            screening_jobs=jobs,
            recovery_configuration=recovery,
            command_dispatch_audit_input=command_audit,
            screening_result=screening_result,
            screening_producer_evidence=evidence,
        )


class Tier4ReplayInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture_count = 0

    def fixture(self, surface: str) -> ReplayFixture:
        self.fixture_count += 1
        target = self.root / f"{surface}-{self.fixture_count}"
        target.mkdir()
        return ReplayFixture(target, surface)

    def rewrite_accepted_and_rebind(
        self,
        fixture: ReplayFixture,
        paths: ProducerReplayInputs,
        records: list[dict],
        label: str,
    ) -> None:
        accepted = fixture.root / label / "panelist-responses.jsonl"
        accepted.parent.mkdir()
        content = _jsonl(records)
        accepted.write_bytes(content)
        manifest = json.loads(paths.study_manifest.read_text(encoding="utf-8"))
        manifest["outputs"]["accepted_responses"] = {
            "path": "panelist-responses.jsonl",
            "content_hash": _digest(content),
            "record_count": len(records),
        }
        final = fixture.root / label / "study-manifest.json"
        final.write_bytes(canonical_json_bytes(manifest))
        object.__setattr__(paths, "accepted_responses", accepted)
        object.__setattr__(paths, "study_manifest", final)

    def test_surface_matrix_accepts_only_exact_required_and_nullable_paths(self):
        for surface in SURFACE_METHOD_STAGE:
            with self.subTest(surface=surface):
                fixture = self.fixture(surface)
                assembled = assemble_replay_inputs(
                    surface=surface, paths=fixture.paths
                )
                self.assertEqual(surface, assembled["surface"])
                self.assertEqual(fixture.method, assembled["method"])
                self.assertEqual(fixture.stage, assembled["stage"])
                self.assertEqual(fixture.run_id, assembled["run_id"])
                expected = {
                    "study_manifest",
                    "accepted_responses",
                    "raw_provider_returns",
                    "rejected_attempts",
                    "dispatch_audit",
                    "command_dispatch_audit_input",
                }
                if fixture.stage == "screening":
                    expected |= {
                        "screening_jobs",
                        "screening_response_projection",
                        "recovery_configuration",
                    }
                else:
                    expected |= {
                        "boundary_response_projection",
                        "screening_result",
                        "screening_producer_evidence",
                    }
                self.assertEqual(expected, set(assembled["input_bindings"]))
                self.assertNotIn("result", assembled["input_bindings"])

        nullable = self.fixture("maxdiff_screening_ordering")
        paths = deepcopy(nullable.paths)
        object.__setattr__(paths, "command_dispatch_audit_input", None)
        assembled = assemble_replay_inputs(
            surface=nullable.surface, paths=paths
        )
        self.assertIsNone(
            assembled["input_bindings"]["command_dispatch_audit_input"]
        )

    def test_rejects_missing_extra_wrong_surface_duplicate_and_wrong_result_path(self):
        screening = self.fixture("complete_exposure_ordering")
        pairwise = self.fixture("pairwise_boundary_ordering")
        cases: list[tuple[str, str, ProducerReplayInputs]] = []
        for field in (
            "study_manifest",
            "accepted_responses",
            "raw_provider_returns",
            "rejected_attempts",
            "cumulative_dispatch_audit",
            "result",
            "screening_jobs",
            "recovery_configuration",
        ):
            altered = deepcopy(screening.paths)
            object.__setattr__(altered, field, None)
            cases.append((f"missing-{field}", screening.surface, altered))
        for field in (
            "screening_jobs",
            "recovery_configuration",
            "command_dispatch_audit_input",
        ):
            altered = deepcopy(pairwise.paths)
            object.__setattr__(altered, field, screening.paths.screening_jobs)
            cases.append((f"pairwise-extra-{field}", pairwise.surface, altered))
        for field in ("screening_result", "screening_producer_evidence"):
            altered = deepcopy(screening.paths)
            object.__setattr__(altered, field, pairwise.paths.screening_result)
            cases.append((f"screening-extra-{field}", screening.surface, altered))
            altered = deepcopy(pairwise.paths)
            object.__setattr__(altered, field, None)
            cases.append((f"pairwise-missing-{field}", pairwise.surface, altered))
        duplicate = deepcopy(screening.paths)
        object.__setattr__(
            duplicate, "recovery_configuration", duplicate.screening_jobs
        )
        cases.append(("duplicate", screening.surface, duplicate))
        wrong_result = deepcopy(screening.paths)
        renamed = screening.paths.result.with_name("boundary-results.json")
        renamed.write_bytes(screening.paths.result.read_bytes())
        object.__setattr__(wrong_result, "result", renamed)
        cases.append(("wrong-result", screening.surface, wrong_result))
        for name, surface, paths in cases:
            with self.subTest(name=name), self.assertRaises(
                (ProducerAuthenticationError, TypeError)
            ):
                assemble_replay_inputs(surface=surface, paths=paths)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface="unknown", paths=screening.paths)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(
                surface=screening.surface,
                paths={**screening.paths.__dict__, "projection": Path("x")},  # type: ignore[arg-type]
            )

    def test_requires_lineage_bound_final_manifest(self):
        fixture = self.fixture("complete_exposure_ordering")
        altered = deepcopy(fixture.paths)
        object.__setattr__(altered, "study_manifest", fixture.earlier_manifest)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=altered)

    def test_projection_preserves_full_physical_records_and_order(self):
        fixture = self.fixture("complete_exposure_ordering")
        physical = [
            json.loads(line)
            for line in fixture.paths.accepted_responses.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assembled = assemble_replay_inputs(
            surface=fixture.surface, paths=fixture.paths
        )
        projection = assembled["response_projection_bytes"]
        expected_records = [
            record
            for record in physical
            if record.get("record_type") == "screening_response"
        ]
        self.assertEqual(
            b"".join(canonical_json_bytes(record) for record in expected_records),
            projection,
        )
        self.assertEqual(
            [record["response_id"] for record in expected_records],
            [
                json.loads(line)["response_id"]
                for line in projection.decode("utf-8").splitlines()
            ],
        )
        binding = assembled["input_bindings"]["screening_response_projection"]
        self.assertEqual(_digest(projection), binding["raw_bytes_sha256"])
        self.assertEqual(_digest(projection), binding["canonical_document_sha256"])
        self.assertEqual(len(expected_records), binding["record_count"])

    def test_projection_rejects_wrong_method_invalid_response_and_empty_stage(self):
        fixture = self.fixture("complete_exposure_ordering")
        records = [
            json.loads(line)
            for line in fixture.paths.accepted_responses.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        cases = {}
        wrong_method = deepcopy(records)
        wrong_method[0]["method"] = "partial_exposure_maxdiff"
        cases["wrong-method"] = wrong_method
        stripped = deepcopy(records)
        stripped[0].pop("profile_snapshot")
        cases["stripped"] = stripped
        for name, altered_records in cases.items():
            altered = deepcopy(fixture.paths)
            self.rewrite_accepted_and_rebind(
                fixture, altered, altered_records, name
            )
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                assemble_replay_inputs(surface=fixture.surface, paths=altered)

        screening = self.fixture("maxdiff_screening_ordering")
        pairwise = self.fixture("pairwise_boundary_ordering")
        empty_stage = deepcopy(pairwise.paths)
        for role in (
            "study_manifest",
            "accepted_responses",
            "raw_provider_returns",
            "rejected_attempts",
            "cumulative_dispatch_audit",
        ):
            object.__setattr__(empty_stage, role, getattr(screening.paths, role))
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=pairwise.surface, paths=empty_stage)

    def test_pairwise_projection_is_boundary_only_and_requires_partial_exposure(self):
        fixture = self.fixture("pairwise_boundary_ordering")
        assembled = assemble_replay_inputs(
            surface=fixture.surface, paths=fixture.paths
        )
        records = [
            json.loads(line)
            for line in assembled["response_projection_bytes"].decode().splitlines()
        ]
        self.assertTrue(records)
        self.assertTrue(
            all(record["record_type"] == "boundary_response" for record in records)
        )
        self.assertTrue(
            all(record["method"] == "partial_exposure_maxdiff" for record in records)
        )

    def test_pairwise_recursively_binds_exact_upstream_result_and_receipt(self):
        fixture = self.fixture("pairwise_boundary_ordering")
        assembled = assemble_replay_inputs(
            surface=fixture.surface, paths=fixture.paths
        )
        evidence_binding = assembled["input_bindings"][
            "screening_producer_evidence"
        ]
        result_binding = assembled["input_bindings"]["screening_result"]
        self.assertEqual(
            result_binding["canonical_document_sha256"],
            evidence_binding["result_sha256"],
        )
        self.assertEqual(
            result_binding["raw_bytes_sha256"],
            evidence_binding["result_bytes_sha256"],
        )

        original = json.loads(
            fixture.paths.screening_producer_evidence.read_text(encoding="utf-8")
        )
        mutators = {
            "evidence-digest": lambda value: value.__setitem__(
                "producer_evidence_sha256", SHA
            ),
            "semantics": lambda value: value["producer_semantics"].__setitem__(
                "bootstrap_sha256", "sha256:" + ("cd" * 32)
            ),
            "result-bytes": lambda value: value["result_binding"].__setitem__(
                "raw_bytes_sha256", SHA
            ),
            "result-semantics": lambda value: value["result_binding"].__setitem__(
                "canonical_document_sha256", SHA
            ),
            "snapshot": lambda value: value["snapshot_binding"].__setitem__(
                "archive_sha256", "sha256:" + ("ef" * 32)
            ),
        }
        for name, mutate in mutators.items():
            altered_document = deepcopy(original)
            mutate(altered_document)
            altered_paths = deepcopy(fixture.paths)
            path = fixture.root / name / "screening.producer-evidence.json"
            path.parent.mkdir()
            path.write_bytes(canonical_json_bytes(altered_document))
            object.__setattr__(altered_paths, "screening_producer_evidence", path)
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                assemble_replay_inputs(
                    surface=fixture.surface, paths=altered_paths
                )

        replacement = deepcopy(fixture.paths)
        substituted = fixture.root / "substituted" / "screening-model-results.json"
        substituted.parent.mkdir()
        substituted.write_bytes(canonical_json_bytes({"ranked_ids": ["other"]}))
        object.__setattr__(replacement, "screening_result", substituted)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=replacement)

    def test_rejects_nested_upstream_semantics_mutations_even_when_rehashed(self):
        fixture = self.fixture("pairwise_boundary_ordering")
        original = json.loads(
            fixture.paths.screening_producer_evidence.read_text(encoding="utf-8")
        )

        def mutate_closure(value):
            value["producer_semantics"]["dependency_closure"][0]["path"] = "../x.py"

        def mutate_runtime(value):
            value["producer_semantics"]["runtime_fingerprint"]["numpy_version"] = ""

        def mutate_policy(value):
            value["producer_semantics"]["policy_bindings"][
                "ordering_equivalence"
            ] = "invented-v1"

        def mutate_serialization(value):
            value["producer_semantics"]["output_serialization"][
                "producer_raw_serialization"
            ]["indent"] = 4

        def mutate_semantics_extra(value):
            value["producer_semantics"]["runtime_fingerprint"]["extra"] = True

        for name, mutate in {
            "closure": mutate_closure,
            "runtime": mutate_runtime,
            "policy": mutate_policy,
            "serialization": mutate_serialization,
            "semantics-extra": mutate_semantics_extra,
        }.items():
            altered = deepcopy(original)
            mutate(altered)
            semantics = altered["producer_semantics"]
            semantics["producer_semantics_sha256"] = None
            semantics["producer_semantics_sha256"] = sha256_json(semantics)
            altered["producer_evidence_sha256"] = None
            altered["producer_evidence_sha256"] = sha256_json(altered)
            path = fixture.root / f"nested-{name}" / "screening.producer-evidence.json"
            path.parent.mkdir()
            path.write_bytes(canonical_json_bytes(altered))
            paths = deepcopy(fixture.paths)
            object.__setattr__(paths, "screening_producer_evidence", path)
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                assemble_replay_inputs(surface=fixture.surface, paths=paths)

    def test_upstream_policy_must_bind_exact_recovery_configuration(self):
        fixture = self.fixture("pairwise_boundary_ordering")
        evidence = json.loads(
            fixture.paths.screening_producer_evidence.read_text(encoding="utf-8")
        )
        evidence["producer_semantics"]["policy_bindings"][
            "recovery_configuration_sha256"
        ] = "sha256:" + ("12" * 32)
        semantics = evidence["producer_semantics"]
        semantics["producer_semantics_sha256"] = None
        semantics["producer_semantics_sha256"] = sha256_json(semantics)
        evidence["producer_evidence_sha256"] = None
        evidence["producer_evidence_sha256"] = sha256_json(evidence)
        path = fixture.root / "recovery-mismatch" / "screening.producer-evidence.json"
        path.parent.mkdir()
        path.write_bytes(canonical_json_bytes(evidence))
        paths = deepcopy(fixture.paths)
        object.__setattr__(paths, "screening_producer_evidence", path)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=paths)

    def test_result_documents_require_explicit_exact_study_identity(self):
        screening = self.fixture("maxdiff_screening_ordering")
        pairwise = self.fixture("pairwise_boundary_ordering")
        for name, fixture, role in (
            ("chosen-missing", screening, "result"),
            ("upstream-missing", pairwise, "screening_result"),
        ):
            paths = deepcopy(fixture.paths)
            original = getattr(paths, role)
            document = json.loads(original.read_text(encoding="utf-8"))
            document.pop("study_id")
            path = fixture.root / name / original.name
            path.parent.mkdir()
            path.write_bytes(canonical_json_bytes(document))
            object.__setattr__(paths, role, path)
            if role == "screening_result":
                evidence = json.loads(
                    paths.screening_producer_evidence.read_text(
                        encoding="utf-8"
                    )
                )
                raw = path.read_bytes()
                canonical_digest = _digest(canonical_json_bytes(document))
                evidence["result_binding"].update({
                    "raw_bytes_sha256": _digest(raw),
                    "canonical_document_sha256": canonical_digest,
                })
                evidence["snapshot_binding"]["snapshot_id"] = (
                    f"maxdiff_screening_ordering--{fixture.run_id}--"
                    f"{canonical_digest[7:]}"
                )
                evidence["producer_evidence_sha256"] = None
                evidence["producer_evidence_sha256"] = sha256_json(evidence)
                evidence_path = fixture.root / name / "screening.producer-evidence.json"
                evidence_path.write_bytes(canonical_json_bytes(evidence))
                object.__setattr__(
                    paths, "screening_producer_evidence", evidence_path
                )
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                assemble_replay_inputs(surface=fixture.surface, paths=paths)

    def test_descriptor_authority_rejects_ancestor_symlink_and_external_hardlink(self):
        fixture = self.fixture("complete_exposure_ordering")
        symlink_parent = fixture.root / "symlink-parent"
        symlink_parent.symlink_to(
            fixture.paths.recovery_configuration.parent, target_is_directory=True
        )
        paths = deepcopy(fixture.paths)
        object.__setattr__(
            paths,
            "recovery_configuration",
            symlink_parent / fixture.paths.recovery_configuration.name,
        )
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=paths)

        external = fixture.root / "external-hard-link.json"
        os.link(fixture.paths.recovery_configuration, external)
        paths = deepcopy(fixture.paths)
        object.__setattr__(paths, "recovery_configuration", external)
        with self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=paths)

    def test_descriptor_leaf_open_rejects_fifo_socket_and_directory_without_blocking(self):
        fifo = self.root / "writer-free.fifo"
        os.mkfifo(fifo)
        script = "\n".join([
            "from pathlib import Path",
            "import sys",
            f"sys.path.insert(0, {str(ROOT / 'skills' / 'audience-panel-builder' / 'scripts')!r})",
            "from audience_panel_builder.population.validation.evidence_errors import ProducerAuthenticationError",
            "from audience_panel_builder.population.validation.replay_inputs import _PinnedInputReader, _RESOURCE_LIMITS",
            "try:",
            "    _PinnedInputReader({'fixture': Path(sys.argv[1])}, _RESOURCE_LIMITS)",
            "except ProducerAuthenticationError:",
            "    raise SystemExit(0)",
            "raise SystemExit(2)",
        ])
        completed = subprocess.run(
            [sys.executable, "-c", script, str(fifo)],
            capture_output=True,
            timeout=2,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())

        socket_path = self.root / "listener.socket"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(socket_path))
        directory = self.root / "leaf-directory"
        directory.mkdir()
        for name, path in (("socket", socket_path), ("directory", directory)):
            with self.subTest(name=name), self.assertRaises(
                ProducerAuthenticationError
            ):
                replay_inputs_module._PinnedInputReader(
                    {"fixture": path}, replay_inputs_module._RESOURCE_LIMITS
                )

    def test_post_preflight_alias_substitution_is_rejected(self):
        fixture = self.fixture("complete_exposure_ordering")
        paths = deepcopy(fixture.paths)
        recovery = paths.recovery_configuration
        jobs = paths.screening_jobs
        real_reader = replay_inputs_module._PinnedInputReader._read_one
        mutated = False

        def substitute(reader, item):
            nonlocal mutated
            if not mutated:
                mutated = True
                recovery.unlink()
                os.link(jobs, recovery)
            return real_reader(reader, item)

        with patch.object(
            replay_inputs_module._PinnedInputReader,
            "_read_one",
            substitute,
        ), self.assertRaises(ProducerAuthenticationError):
            assemble_replay_inputs(surface=fixture.surface, paths=paths)

    def test_bounded_reader_rejects_per_file_and_aggregate_one_over(self):
        fixture = self.fixture("complete_exposure_ordering")
        selected = [
            path
            for path in fixture.paths.__dict__.values()
            if path is not None
        ]
        sizes = [path.stat().st_size for path in selected]
        current = getattr(replay_inputs_module, "_RESOURCE_LIMITS", None)
        if current is None:
            equality = object()
            one_over_file = object()
            one_over_aggregate = object()
        else:
            equality = replace(
                current,
                maximum_file_bytes=max(sizes),
                maximum_aggregate_bytes=sum(sizes),
            )
            one_over_file = replace(
                equality, maximum_file_bytes=max(sizes) - 1
            )
            one_over_aggregate = replace(
                equality, maximum_aggregate_bytes=sum(sizes) - 1
            )
        with patch.object(
            replay_inputs_module, "_RESOURCE_LIMITS", equality, create=True
        ):
            assemble_replay_inputs(surface=fixture.surface, paths=fixture.paths)
        for limits in (one_over_file, one_over_aggregate):
            with patch.object(
                replay_inputs_module, "_RESOURCE_LIMITS", limits, create=True
            ), self.assertRaises(ProducerAuthenticationError):
                assemble_replay_inputs(
                    surface=fixture.surface, paths=fixture.paths
                )

    def test_real_reader_enforces_json_resource_equality_and_one_over(self):
        path = self.root / "resource.json"
        document = {"a": ["é", 1]}
        path.write_bytes(canonical_json_bytes(document))
        base = replay_inputs_module._ResourceLimits(
            maximum_file_bytes=path.stat().st_size,
            maximum_aggregate_bytes=path.stat().st_size,
            maximum_jsonl_records=2,
            maximum_json_depth=2,
            maximum_container_items=3,
            maximum_string_bytes=2,
            maximum_scalars=3,
        )

        def read(limits):
            reader = replay_inputs_module._PinnedInputReader(
                {"fixture": path}, limits
            )
            try:
                return replay_inputs_module._read_json(
                    "fixture", reader=reader
                )[0]
            finally:
                reader.close()

        self.assertEqual(document, read(base))
        for field, value in (
            ("maximum_json_depth", 1),
            ("maximum_container_items", 2),
            ("maximum_string_bytes", 1),
            ("maximum_scalars", 2),
        ):
            with self.subTest(field=field), self.assertRaises(
                ProducerAuthenticationError
            ):
                read(replace(base, **{field: value}))

        jsonl = self.root / "records.jsonl"
        jsonl.write_bytes(b'{"id":"a"}\n{"id":"b"}\n')
        line_limits = replace(
            base,
            maximum_file_bytes=jsonl.stat().st_size,
            maximum_aggregate_bytes=jsonl.stat().st_size,
            maximum_json_depth=2,
            maximum_container_items=2,
            maximum_string_bytes=2,
            maximum_scalars=4,
        )

        def read_lines(limits):
            reader = replay_inputs_module._PinnedInputReader(
                {"fixture": jsonl}, limits
            )
            try:
                return replay_inputs_module._read_jsonl(
                    "fixture", reader=reader
                )[0]
            finally:
                reader.close()

        self.assertEqual(2, len(read_lines(line_limits)))
        with self.assertRaises(ProducerAuthenticationError):
            read_lines(replace(line_limits, maximum_jsonl_records=1))
        with self.assertRaises(ProducerAuthenticationError):
            read_lines(replace(line_limits, maximum_container_items=1))
        with self.assertRaises(ProducerAuthenticationError):
            read_lines(replace(line_limits, maximum_scalars=3))

    def test_real_reader_accepts_depth_64_rejects_65_and_normalizes_failures(self):
        def nested(depth: int) -> object:
            value: object = {}
            for _ in range(depth - 1):
                value = {"a": value}
            return value

        for depth, accepted in ((64, True), (65, False)):
            path = self.root / f"depth-{depth}.json"
            path.write_bytes(canonical_json_bytes(nested(depth)))
            limits = replace(
                replay_inputs_module._RESOURCE_LIMITS,
                maximum_file_bytes=path.stat().st_size,
                maximum_aggregate_bytes=path.stat().st_size,
            )
            reader = replay_inputs_module._PinnedInputReader(
                {"fixture": path}, limits
            )
            try:
                if accepted:
                    replay_inputs_module._read_json("fixture", reader=reader)
                else:
                    with self.assertRaises(ProducerAuthenticationError):
                        replay_inputs_module._read_json(
                            "fixture", reader=reader
                        )
            finally:
                reader.close()

        malformed = self.root / "malformed.json"
        malformed.write_bytes(b'{"value":"\xff"}\n')
        limits = replace(
            replay_inputs_module._RESOURCE_LIMITS,
            maximum_file_bytes=malformed.stat().st_size,
            maximum_aggregate_bytes=malformed.stat().st_size,
        )
        reader = replay_inputs_module._PinnedInputReader(
            {"fixture": malformed}, limits
        )
        try:
            with self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._read_json("fixture", reader=reader)
        finally:
            reader.close()

        valid = self.root / "valid-for-failures.json"
        valid.write_bytes(canonical_json_bytes({"value": 1}))
        limits = replace(
            replay_inputs_module._RESOURCE_LIMITS,
            maximum_file_bytes=valid.stat().st_size,
            maximum_aggregate_bytes=valid.stat().st_size,
        )
        reader = replay_inputs_module._PinnedInputReader(
            {"fixture": valid}, limits
        )
        try:
            for error in (
                json.JSONDecodeError("bad", "x", 0),
                RecursionError(),
                MemoryError(),
                OverflowError(),
            ):
                with patch.object(
                    replay_inputs_module.json, "loads", side_effect=error
                ), self.assertRaises(ProducerAuthenticationError):
                    replay_inputs_module._read_json("fixture", reader=reader)
        finally:
            reader.close()

    def test_direct_readers_normalize_deepcopy_and_jsonl_allocation_failures(self):
        document = self.root / "allocation.json"
        document.write_bytes(canonical_json_bytes({"value": 1}))
        records = self.root / "allocation.jsonl"
        records.write_bytes(_jsonl([{"value": 1}]))
        limits = replace(
            replay_inputs_module._RESOURCE_LIMITS,
            maximum_file_bytes=max(
                document.stat().st_size, records.stat().st_size
            ),
            maximum_aggregate_bytes=(
                document.stat().st_size + records.stat().st_size
            ),
        )
        reader = replay_inputs_module._PinnedInputReader(
            {"document": document, "records": records}, limits
        )
        try:
            with patch.object(
                replay_inputs_module, "deepcopy", side_effect=MemoryError
            ), self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._read_json("document", reader=reader)
            real_deepcopy = deepcopy
            copy_count = 0

            def fail_second_copy(value):
                nonlocal copy_count
                copy_count += 1
                if copy_count == 2:
                    raise RecursionError
                return real_deepcopy(value)

            with patch.object(
                replay_inputs_module, "deepcopy", side_effect=fail_second_copy
            ), self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._read_json("document", reader=reader)
            with patch.object(
                replay_inputs_module, "deepcopy", side_effect=OverflowError
            ), self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._read_jsonl("records", reader=reader)
            with patch.object(
                replay_inputs_module,
                "_append_jsonl_record",
                side_effect=MemoryError,
            ), self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._read_jsonl("records", reader=reader)
        finally:
            reader.close()

    def test_public_api_normalizes_resource_failures_and_preserves_closed_errors(self):
        fixture = self.fixture("complete_exposure_ordering")
        for target, error in (
            ("_PinnedInputReader", MemoryError()),
            ("_validate_matrix", OverflowError()),
            ("_read_jsonl", MemoryError()),
            ("_assemble_replay_inputs", RecursionError()),
            ("_build_replay_output", MemoryError()),
        ):
            with self.subTest(target=target), patch.object(
                replay_inputs_module, target, side_effect=error
            ), self.assertRaises(ProducerAuthenticationError):
                assemble_replay_inputs(surface=fixture.surface, paths=fixture.paths)

        sentinel = ProducerAuthenticationError("preserve-this-message")
        with patch.object(
            replay_inputs_module, "_PinnedInputReader", side_effect=sentinel
        ):
            with self.assertRaises(ProducerAuthenticationError) as raised:
                assemble_replay_inputs(surface=fixture.surface, paths=fixture.paths)
        self.assertIs(sentinel, raised.exception)
        self.assertEqual("preserve-this-message", str(raised.exception))
        for signal in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(signal=type(signal).__name__), patch.object(
                replay_inputs_module, "_validate_matrix", side_effect=signal
            ):
                with self.assertRaises(type(signal)) as raised_signal:
                    assemble_replay_inputs(
                        surface=fixture.surface, paths=fixture.paths
                    )
            self.assertIs(signal, raised_signal.exception)

    def test_byte_limits_fail_before_json_parser_is_called(self):
        path = self.root / "before-parse.json"
        path.write_bytes(canonical_json_bytes({"study_id": "run"}))
        limits = replace(
            replay_inputs_module._RESOURCE_LIMITS,
            maximum_file_bytes=path.stat().st_size - 1,
            maximum_aggregate_bytes=path.stat().st_size,
        )
        with patch.object(replay_inputs_module.json, "loads") as parser:
            with self.assertRaises(ProducerAuthenticationError):
                replay_inputs_module._PinnedInputReader(
                    {"fixture": path}, limits
                )
            parser.assert_not_called()

    def test_dataclass_is_frozen_and_rejects_caller_projection(self):
        fixture = self.fixture("complete_exposure_ordering")
        with self.assertRaises(Exception):
            fixture.paths.result = Path("replacement")  # type: ignore[misc]
        with self.assertRaises(TypeError):
            ProducerReplayInputs(
                **fixture.paths.__dict__,
                screening_response_projection=Path("caller.jsonl"),
            )


if __name__ == "__main__":
    unittest.main()
