from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PREP_SCRIPTS = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
import sys

sys.path.insert(0, str(PREP_SCRIPTS))
sys.path.insert(0, str(PANEL_SCRIPTS))

from conformance import (
    test_real_world_outcome_data_prep_validation_handoff as handoff_tests,
)
from outcome_data_prep.common import (
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from outcome_data_prep.contracts import validate_import_event
from outcome_data_prep.publication import (
    ImportConflict,
    analytical_identity_document,
    commit_import_generation,
    recover_study,
    replay_authenticated_ledger,
    validate_complete_staged_generation,
)
import outcome_data_prep.publication as publication_module
from outcome_data_prep.validation_handoff import (
    build_validation_observation,
    validate_validation_handoff,
    validate_validation_handoff_document,
)


class RealWorldOutcomeDataPrepPublicationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        handoff_tests.RealWorldOutcomeDataPrepValidationHandoffTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = handoff_tests.RealWorldOutcomeDataPrepValidationHandoffTests(
            "test_handoff_is_deterministic_and_contains_no_decision_fields"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.study = self.fixture.study.study_root
        self.authority = self.fixture.authority
        batch, inputs = self.fixture._batch()
        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=batch,
            authenticated_study=self.fixture.study,
            study_authority=self.authority,
        )
        self.handoff = validate_validation_handoff(
            authenticated_batch=batch,
            authenticated_study=self.fixture.study,
            study_authority=self.authority,
            validation_observations=[observation],
        )
        self.source_manifest = inputs["manifest"]
        self.source_path = inputs["admitted"].source_path
        self.base = Path(tempfile.mkdtemp(dir=self.fixture.study_fixture.root))
        self.base.chmod(0o700)
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.base, ignore_errors=True
            )
        )
        self.stage = self._stage()

    def _delivery_started_at(self) -> str:
        events = self.fixture.study.registration_receipt["chronology"]["events"]
        return next(
            item["occurred_at"]
            for item in events
            if item["event_type"] == "delivery_started"
        )

    def _stage(
        self,
        *,
        import_id: str | None = None,
        imported_at: str = "2026-08-10T12:01:00Z",
        previous_status: str = "preregistered_holdout",
        next_status: str = "preregistered_holdout",
        source_exported_at: str = "2026-08-10T12:00:00Z",
        correction: dict[str, object] | None = None,
        altered_source: bytes | None = None,
        allow_unsafe_import_id: bool = False,
    ) -> Path:
        manifest = deepcopy(self.source_manifest)
        identifier = import_id or str(manifest["import_id"])
        manifest["import_id"] = identifier
        manifest["source_manifest_id"] = f"manifest-{identifier}"
        manifest["source_manifest_sha256"] = None
        manifest["source_manifest_sha256"] = sha256_json(manifest)
        handoff = deepcopy(self.handoff)
        for row in handoff["normalized_observations"]:
            row["import_id"] = identifier
            row["normalized_observation_sha256"] = None
            row["normalized_observation_sha256"] = sha256_json(row)
        for binding, row in zip(
            handoff["observation_bindings"], handoff["normalized_observations"]
        ):
            binding["normalized_observation_sha256"] = row[
                "normalized_observation_sha256"
            ]
            binding["observation_binding_sha256"] = None
            binding["observation_binding_sha256"] = sha256_json(binding)
        supporting = deepcopy(handoff)
        if identifier != self.source_manifest["import_id"]:
            if correction is None:
                observation_ids = [
                    f"{row['observation_id']}-{identifier}"
                    for row in supporting["normalized_observations"]
                ]
            else:
                observation_ids = list(
                    correction["supersedes_observation_ids"]
                )
            for row, observation_id in zip(
                supporting["normalized_observations"], observation_ids
            ):
                row["observation_id"] = observation_id
                row["normalized_observation_sha256"] = None
                row["normalized_observation_sha256"] = sha256_json(row)
            for binding, row in zip(
                supporting["observation_bindings"],
                supporting["normalized_observations"],
            ):
                binding["observation_id"] = row["observation_id"]
                binding["normalized_observation_sha256"] = row[
                    "normalized_observation_sha256"
                ]
                binding["observation_binding_sha256"] = None
                binding["observation_binding_sha256"] = sha256_json(binding)
        if next_status != "preregistered_holdout":
            for row in supporting["normalized_observations"]:
                row["validation_projection"]["evidence_status"] = next_status
                row["validation_projection"]["assignment"]["design"] = (
                    "observational"
                )
                row["normalized_observation_sha256"] = None
                row["normalized_observation_sha256"] = sha256_json(row)
            for binding, row in zip(
                supporting["observation_bindings"],
                supporting["normalized_observations"],
            ):
                binding["evidence_status"] = next_status
                binding["normalized_observation_sha256"] = row[
                    "normalized_observation_sha256"
                ]
                binding["observation_binding_sha256"] = None
                binding["observation_binding_sha256"] = sha256_json(binding)
        # The validation observations bind the original normalized rows. A new
        # import identity is unnecessary for ordinary fixture retries, so only
        # the default fixture import is used with a real handoff.
        if identifier != self.source_manifest["import_id"]:
            handoff = None
        elif handoff is not None:
            handoff["handoff_sha256"] = None
            handoff["handoff_sha256"] = sha256_json(handoff)

        stage = self.base / f"stage-{len(list(self.base.iterdir()))}"
        stage.mkdir(mode=0o700)
        files: list[dict[str, object]] = []

        def write(relative: str, raw: bytes, role: str, source_id=None) -> None:
            path = stage / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            for parent in [path.parent, *path.parents]:
                if parent == stage.parent:
                    break
                if parent.exists():
                    parent.chmod(0o700)
            path.write_bytes(raw)
            path.chmod(0o600)
            files.append({
                "relative_path": relative,
                "sha256": sha256_bytes(raw),
                "byte_count": len(raw),
                "role": role,
                "source_id": source_id,
            })

        source_raw = (
            self.source_path.read_bytes()
            if altered_source is None
            else altered_source
        )
        source = deepcopy(manifest["sources"][0])
        source["source_sha256"] = sha256_bytes(source_raw)
        manifest["sources"] = [source]
        manifest["source_manifest_sha256"] = None
        manifest["source_manifest_sha256"] = sha256_json(manifest)
        write("source-manifest.json", canonical_json_bytes(manifest), "source_manifest")
        write(
            "sources/source-one.bin",
            source_raw,
            "accepted_source",
            source["source_id"],
        )
        write(
            "normalized-observations.json",
            canonical_json_bytes(supporting["normalized_observations"]),
            "supporting_record",
        )
        write(
            "observation-bindings.json",
            canonical_json_bytes(supporting["observation_bindings"]),
            "supporting_record",
        )
        # Import IDs, observation IDs, and effective evidence status are not
        # part of the closed analytical identity.  Build it from the original
        # authenticated fixture so descriptive/blocked supporting records do
        # not have to masquerade as a validation handoff.
        identity = analytical_identity_document(self.handoff)
        write(
            "analytical-identity.json",
            canonical_json_bytes(identity),
            "analytical_identity",
        )
        observation_ids = [
            row["observation_id"]
            for row in supporting["normalized_observations"]
        ]
        if handoff is not None:
            write(
                "validation-handoff.json",
                canonical_json_bytes(handoff),
                "validation_handoff",
            )
        event_document = {
            "schema_version": "outcome-import-event-v1",
            "import_id": identifier,
            "study_id": manifest["study_id"],
            "imported_at": imported_at,
            "imported_by": "validation-owner",
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "observation_ids": observation_ids,
            "import_event_sha256": None,
        }
        event_document["import_event_sha256"] = sha256_json(event_document)
        event = (
            event_document
            if allow_unsafe_import_id
            else validate_import_event(event_document)
        )
        write("import-event.json", canonical_json_bytes(event), "import_event")
        if correction is not None:
            write(
                "correction-request.json",
                canonical_json_bytes(correction),
                "correction_request",
            )
        generation = {
            "schema_version": "outcome-import-generation-manifest-v1",
            "study_id": manifest["study_id"],
            "registration_id": self.fixture.study.registration["registration_id"],
            "registration_sha256": self.fixture.study.registration[
                "registration_sha256"
            ],
            "registration_receipt_sha256": self.fixture.study.registration_receipt[
                "receipt_sha256"
            ],
            "import_id": identifier,
            "imported_at": imported_at,
            "imported_by": "validation-owner",
            "previous_evidence_status": previous_status,
            "next_evidence_status": next_status,
            "delivery_started_at": self._delivery_started_at(),
            "first_outcome_accessed_at": "2026-08-10T11:00:00Z",
            "source_exported_at": source_exported_at,
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "validation_handoff_sha256": (
                None if handoff is None else handoff["handoff_sha256"]
            ),
            "analytical_identity_sha256": identity[
                "analytical_identity_sha256"
            ],
            "correction_id": (
                None if correction is None else correction["correction_id"]
            ),
            "correction_request_sha256": (
                None if correction is None else correction["correction_request_sha256"]
            ),
            "supersedes_import_id": (
                None if correction is None else correction["supersedes_import_id"]
            ),
            "superseded_observation_ids": (
                [] if correction is None else correction["supersedes_observation_ids"]
            ),
            "files": sorted(files, key=lambda item: item["relative_path"]),
            "generation_sha256": None,
        }
        generation["generation_sha256"] = sha256_json(generation)
        write(
            "generation-manifest.json",
            canonical_json_bytes(generation),
            "generation_manifest",
        )
        # The manifest never recursively lists itself.
        return stage

    def _commit(self, stage: Path | None = None, previous=None):
        return commit_import_generation(
            study_root=self.study,
            staged_generation=stage or self.stage,
            expected_previous_ledger_digest=previous,
            authority=self.authority,
        )

    def _completion_paths(self, import_id: str | None = None) -> tuple[Path, Path]:
        identifier = import_id or str(self.source_manifest["import_id"])
        completed = self.study / "completed-import-transactions"
        return (
            completed / f"{identifier}.receipt.json",
            completed / f"{identifier}.claim.json",
        )

    @staticmethod
    def _stable_file_identity(path: Path) -> tuple[int, int, int, int, int, int]:
        info = path.lstat()
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            stat.S_IMODE(info.st_mode),
            info.st_nlink,
            info.st_size,
        )

    @staticmethod
    def _rewrite_manifested_json(
        stage: Path, relative_path: str, document: object
    ) -> None:
        raw = canonical_json_bytes(document)
        path = stage / relative_path
        path.write_bytes(raw)
        path.chmod(0o600)
        manifest_path = stage / "generation-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        entries = [
            item for item in manifest["files"]
            if item["relative_path"] == relative_path
        ]
        if len(entries) != 1:
            raise AssertionError("fixture manifest entry is not unique")
        entries[0]["sha256"] = sha256_bytes(raw)
        entries[0]["byte_count"] = len(raw)
        manifest["generation_sha256"] = None
        manifest["generation_sha256"] = sha256_json(manifest)
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        manifest_path.chmod(0o600)

    def test_identical_retry_is_idempotent_and_conflicting_retry_fails(self):
        first = self._commit()
        same = self._stage()
        second = self._commit(same)
        self.assertEqual(first, second)
        changed = self._stage(altered_source=b"changed aggregate bytes\n")
        with self.assertRaisesRegex(ImportConflict, "conflicting retry"):
            self._commit(changed)

    def test_stale_expected_ledger_fails_for_a_new_import(self):
        first = self._commit()
        second = self._stage(
            import_id="import-two",
            imported_at="2026-08-11T12:01:00Z",
            next_status="descriptive_only",
            source_exported_at="2026-08-11T12:00:00Z",
        )
        with self.assertRaisesRegex(ImportConflict, "previous ledger digest"):
            self._commit(second, previous=None)
        self.assertTrue(first.generation_path.exists())

    def test_concurrent_same_target_has_one_commit_and_one_idempotent_result(self):
        stages = [self.stage, self._stage()]
        results: list[object] = []

        def run(stage: Path) -> None:
            try:
                results.append(self._commit(stage))
            except BaseException as exc:  # captured for the assertion
                results.append(exc)

        threads = [threading.Thread(target=run, args=(stage,)) for stage in stages]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse([item for item in results if isinstance(item, BaseException)])
        self.assertEqual(results[0], results[1])

    def test_crash_after_pending_cleans_prepared_unpublished_state(self):
        def crash(name: str) -> None:
            if name == "after_pending":
                raise RuntimeError("injected crash")

        with patch.object(publication_module, "_transaction_step", side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self._commit()
        self.assertFalse((self.study / "imports").exists())
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertIsNone(state.current_import_id)
        self.assertFalse((self.study / ".outcome-import-pending.json").exists())

    def test_recovery_completes_generation_only_and_pointer_stale_states(self):
        def crash(name: str) -> None:
            if name == "after_generation":
                raise RuntimeError("injected crash")
        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaises(RuntimeError):
                self._commit()
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(self.source_manifest["import_id"], state.current_import_id)
        self.assertTrue(state.ledger_verified)

    def test_recovery_completes_ledger_written_pointer_stale_with_pending(self):
        def crash(name: str) -> None:
            if name == "after_ledger":
                raise RuntimeError("injected crash")
        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaises(RuntimeError):
                self._commit()
        self.assertFalse((self.study / "current-import.json").exists())
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(self.source_manifest["import_id"], state.current_import_id)

    def test_recovery_completes_pending_after_pointer_was_durable(self):
        def crash(name: str) -> None:
            if name == "after_pointer":
                raise RuntimeError("injected crash")
        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaises(RuntimeError):
                self._commit()
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(self.source_manifest["import_id"], state.current_import_id)
        self.assertFalse((self.study / ".outcome-import-pending.json").exists())

    def test_successful_commit_moves_exact_pending_inode_and_bytes_to_receipt(self):
        pending_path = self.study / ".outcome-import-pending.json"
        observed: dict[str, object] = {}
        real = publication_module._claim_completed_transaction

        def observe(*args, **kwargs):
            observed["raw"] = pending_path.read_bytes()
            observed["identity"] = self._stable_file_identity(pending_path)
            return real(*args, **kwargs)

        with patch.object(
            publication_module, "_claim_completed_transaction", side_effect=observe
        ):
            committed = self._commit()
        receipt, claim = self._completion_paths(committed.import_id)
        self.assertFalse(pending_path.exists())
        self.assertEqual(observed["raw"], receipt.read_bytes())
        self.assertEqual(observed["identity"], self._stable_file_identity(receipt))
        self.assertTrue(claim.exists())
        self.assertEqual(
            committed.ledger_digest,
            recover_study(
                study_root=self.study, authority=self.authority
            ).ledger_digest,
        )
        self.assertEqual(
            committed.ledger_digest,
            replay_authenticated_ledger(
                self.study, authority=self.authority
            ).ledger_digest,
        )

    def test_generation_only_recovery_moves_exact_pending_inode_and_bytes(self):
        def crash(name: str) -> None:
            if name == "after_generation":
                raise RuntimeError("generation-only")

        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaisesRegex(RuntimeError, "generation-only"):
                self._commit()
        pending_path = self.study / ".outcome-import-pending.json"
        expected_raw = pending_path.read_bytes()
        expected_identity = self._stable_file_identity(pending_path)
        state = recover_study(study_root=self.study, authority=self.authority)
        receipt, claim = self._completion_paths(state.current_import_id)
        self.assertFalse(pending_path.exists())
        self.assertEqual(expected_raw, receipt.read_bytes())
        self.assertEqual(expected_identity, self._stable_file_identity(receipt))
        self.assertTrue(claim.exists())

    def test_completed_receipt_mutation_replacement_deletion_and_inventory_reject(self):
        self._commit()
        receipt, claim = self._completion_paths()
        completed = receipt.parent
        receipt_raw = receipt.read_bytes()

        receipt.write_bytes(b"mutated completion receipt\n")
        receipt.chmod(0o600)
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)
        receipt.write_bytes(receipt_raw)
        receipt.chmod(0o600)

        displaced = self.base / "original-completed-receipt.json"
        receipt.rename(displaced)
        receipt.write_bytes(receipt_raw)
        receipt.chmod(0o600)
        with self.assertRaisesRegex(ImportConflict, "identity"):
            recover_study(study_root=self.study, authority=self.authority)
        receipt.unlink()
        displaced.rename(receipt)

        missing = self.base / "temporarily-missing-completed-receipt.json"
        receipt.rename(missing)
        with self.assertRaisesRegex(ImportConflict, "missing"):
            recover_study(study_root=self.study, authority=self.authority)
        missing.rename(receipt)

        missing_claim = self.base / "temporarily-missing-completion-claim.json"
        claim.rename(missing_claim)
        with self.assertRaisesRegex(ImportConflict, "missing"):
            recover_study(study_root=self.study, authority=self.authority)
        missing_claim.rename(claim)

        unexpected = completed / "unexpected.json"
        unexpected.write_bytes(b"unexpected\n")
        unexpected.chmod(0o600)
        with self.assertRaisesRegex(ImportConflict, "unexpected"):
            recover_study(study_root=self.study, authority=self.authority)
        unexpected.unlink()

        nested = completed / "unexpected-directory"
        nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(ImportConflict, "unexpected"):
            recover_study(study_root=self.study, authority=self.authority)

    def test_completion_claim_mutation_rejects_authority_and_pointer_binding(self):
        self._commit()
        _, claim = self._completion_paths()
        document = json.loads(claim.read_bytes())
        document["pointer_file_sha256"] = "sha256:" + "0" * 64
        document["completion_claim_sha256"] = None
        document["completion_claim_sha256"] = sha256_json({
            **document,
            "claim_hmac_sha256": None,
        })
        claim.write_bytes(canonical_json_bytes(document))
        claim.chmod(0o600)
        with self.assertRaisesRegex(ImportConflict, "authentication"):
            recover_study(study_root=self.study, authority=self.authority)

    def test_repeated_unsafe_completed_receipt_recovery_does_not_leak_descriptors(self):
        self._commit()
        receipt, _ = self._completion_paths()
        receipt.chmod(0o644)
        before = len(os.listdir("/dev/fd"))
        for _ in range(100):
            with self.assertRaises(ImportConflict):
                recover_study(study_root=self.study, authority=self.authority)
        after = len(os.listdir("/dev/fd"))
        self.assertEqual(before, after)

    def test_completion_collision_preserves_pending_and_existing_record(self):
        receipt, _ = self._completion_paths()
        collision = b"racer-owned completion\n"
        real = publication_module._claim_completed_transaction

        def collide_then_claim(*args, **kwargs):
            receipt.parent.mkdir(mode=0o700, exist_ok=True)
            receipt.write_bytes(collision)
            receipt.chmod(0o600)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_claim_completed_transaction",
            side_effect=collide_then_claim,
        ):
            with self.assertRaises(ImportConflict):
                self._commit()
        self.assertEqual(collision, receipt.read_bytes())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(collision, receipt.read_bytes())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())

    def test_recovery_reuses_durable_pre_move_completion_claim(self):
        def crash(name: str) -> None:
            if name == "after_completion_claim":
                raise RuntimeError("claim-durable")

        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaisesRegex(RuntimeError, "claim-durable"):
                self._commit()
        pending = self.study / ".outcome-import-pending.json"
        receipt, claim = self._completion_paths()
        self.assertTrue(pending.exists())
        self.assertTrue(claim.exists())
        self.assertFalse(receipt.exists())
        expected_raw = pending.read_bytes()
        expected_identity = self._stable_file_identity(pending)
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(self.source_manifest["import_id"], state.current_import_id)
        self.assertFalse(pending.exists())
        self.assertEqual(expected_raw, receipt.read_bytes())
        self.assertEqual(expected_identity, self._stable_file_identity(receipt))

    def test_normal_completion_boundary_pending_replacement_fails_closed(self):
        pending_name = ".outcome-import-pending.json"
        displaced = self.study / ".displaced-authenticated-pending-normal.json"
        real = publication_module._rename_entry_no_replace

        def replace_then_move(source_fd, source_name, target_fd, target_name):
            if source_name == pending_name:
                os.rename(
                    source_name,
                    displaced.name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=source_fd,
                )
                wrong_fd = os.open(
                    source_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source_fd,
                )
                try:
                    os.write(wrong_fd, b"wrong transaction record\n")
                    os.fsync(wrong_fd)
                finally:
                    os.close(wrong_fd)
            return real(source_fd, source_name, target_fd, target_name)

        with patch.object(
            publication_module, "_rename_entry_no_replace", side_effect=replace_then_move
        ):
            with self.assertRaises(ImportConflict):
                self._commit()
        receipt, claim = self._completion_paths()
        self.assertTrue(displaced.exists())
        self.assertTrue(receipt.exists())
        self.assertTrue(claim.exists())
        self.assertEqual(b"wrong transaction record\n", receipt.read_bytes())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)
        self.assertTrue(displaced.exists())
        self.assertTrue(receipt.exists())

    def test_recovery_completion_boundary_pending_replacement_fails_closed(self):
        def crash(name: str) -> None:
            if name == "after_generation":
                raise RuntimeError("generation-only")

        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaisesRegex(RuntimeError, "generation-only"):
                self._commit()
        pending_name = ".outcome-import-pending.json"
        displaced = self.study / ".displaced-authenticated-pending-recovery.json"
        real = publication_module._rename_entry_no_replace

        def replace_then_move(source_fd, source_name, target_fd, target_name):
            if source_name == pending_name:
                os.rename(
                    source_name,
                    displaced.name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=source_fd,
                )
                wrong_fd = os.open(
                    source_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source_fd,
                )
                try:
                    os.write(wrong_fd, b"wrong recovery transaction\n")
                    os.fsync(wrong_fd)
                finally:
                    os.close(wrong_fd)
            return real(source_fd, source_name, target_fd, target_name)

        with patch.object(
            publication_module, "_rename_entry_no_replace", side_effect=replace_then_move
        ):
            with self.assertRaises(ImportConflict):
                recover_study(study_root=self.study, authority=self.authority)
        receipt, claim = self._completion_paths()
        self.assertTrue(displaced.exists())
        self.assertTrue(receipt.exists())
        self.assertTrue(claim.exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)
        self.assertTrue(displaced.exists())

    def test_recovery_completes_only_an_exact_partial_ledger_append(self):
        def crash(name: str) -> None:
            if name == "after_generation":
                raise RuntimeError("injected crash")
        with patch.object(
            publication_module, "_transaction_step", side_effect=crash
        ):
            with self.assertRaises(RuntimeError):
                self._commit()
        pending = json.loads(
            (self.study / ".outcome-import-pending.json").read_bytes()
        )
        event_bytes = canonical_json_bytes(pending["event_envelope"])
        ledger = self.study / "import-ledger.jsonl"
        ledger.write_bytes(event_bytes[: len(event_bytes) // 2])
        ledger.chmod(0o600)
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(self.source_manifest["import_id"], state.current_import_id)
        self.assertEqual(event_bytes, ledger.read_bytes())

    def test_ledger_committed_pointer_stale_recovers_without_pending(self):
        commit = self._commit()
        pointer = self.study / "current-import.json"
        pointer.unlink()
        state = recover_study(study_root=self.study, authority=self.authority)
        self.assertEqual(commit.import_id, state.current_import_id)
        self.assertTrue(pointer.exists())

    def test_forged_pending_or_orphan_generation_fails_closed(self):
        pending = self.study / ".outcome-import-pending.json"
        pending.write_bytes(canonical_json_bytes({"forged": True}))
        pending.chmod(0o600)
        with self.assertRaisesRegex(ImportConflict, "pending"):
            recover_study(study_root=self.study, authority=self.authority)
        pending.unlink()
        imports = self.study / "imports"
        imports.mkdir(mode=0o700)
        orphan = imports / "orphan"
        orphan.mkdir(mode=0o700)
        with self.assertRaisesRegex(ImportConflict, "orphan"):
            recover_study(study_root=self.study, authority=self.authority)

    def test_existing_empty_or_nonempty_target_is_never_replaced(self):
        target = self.study / "imports" / self.source_manifest["import_id"]
        target.parent.mkdir(mode=0o700)
        for nonempty in (False, True):
            with self.subTest(nonempty=nonempty):
                target.mkdir(mode=0o700, exist_ok=True)
                if nonempty:
                    marker = target / "marker"
                    marker.write_bytes(b"keep")
                    marker.chmod(0o600)
                with self.assertRaises(ImportConflict):
                    self._commit()
                if nonempty:
                    self.assertEqual(b"keep", marker.read_bytes())
                __import__("shutil").rmtree(target)
                (self.study / ".outcome-import-pending.json").unlink(missing_ok=True)

    def test_target_created_at_publish_boundary_is_never_replaced(self):
        real = publication_module._rename_directory_no_replace

        def race(
            source_parent_fd: int,
            source_name: str,
            target_parent_fd: int,
            target_name: str,
        ) -> None:
            os.mkdir(target_name, mode=0o700, dir_fd=target_parent_fd)
            marker_fd = os.open(
                f"{target_name}/racer",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_parent_fd,
            )
            try:
                os.write(marker_fd, b"racer-owned")
            finally:
                os.close(marker_fd)
            real(
                source_parent_fd,
                source_name,
                target_parent_fd,
                target_name,
            )

        with patch.object(
            publication_module, "_rename_directory_no_replace", side_effect=race
        ):
            with self.assertRaises(ImportConflict):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        self.assertEqual(b"racer-owned", (target / "racer").read_bytes())

    def test_stage_replacement_after_final_validation_fails_before_move(self):
        displaced = self.stage.with_name(self.stage.name + "-displaced")

        def replace(name):
            if name == "after_final_stage_validation":
                self.stage.rename(displaced)
                replacement = self._stage()
                replacement.rename(self.stage)

        with patch.object(
            publication_module, "_publication_race_step", side_effect=replace
        ):
            with self.assertRaisesRegex(ImportConflict, "identity changed"):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        self.assertFalse(target.exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())

    def test_published_modification_before_ledger_fails_and_preserves_evidence(self):
        target = self.study / "imports" / self.source_manifest["import_id"]

        def modify(name):
            if name == "after_generation_before_validation":
                source = target / "sources" / "source-one.bin"
                source.write_bytes(b"changed after publication\n")
                source.chmod(0o600)

        with patch.object(
            publication_module, "_publication_race_step", side_effect=modify
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)

    def test_mutation_on_ledger_append_entry_does_not_advance_ledger(self):
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._append_ledger_bytes

        def mutate_on_entry(*args, **kwargs):
            source = target / "sources" / "source-one.bin"
            source.write_bytes(b"changed on append entry\n")
            source.chmod(0o600)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_append_ledger_bytes",
            side_effect=mutate_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)

    def test_recovery_mutation_on_writer_entry_does_not_advance_ledger(self):
        def crash(name):
            if name == "after_generation":
                raise RuntimeError("injected generation-only crash")

        with patch.object(
            publication_module,
            "_transaction_step",
            side_effect=crash,
        ):
            with self.assertRaisesRegex(RuntimeError, "generation-only"):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._append_ledger_bytes

        def mutate_on_entry(*args, **kwargs):
            source = target / "sources" / "source-one.bin"
            source.write_bytes(b"changed during recovery append\n")
            source.chmod(0o600)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_append_ledger_bytes",
            side_effect=mutate_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                recover_study(study_root=self.study, authority=self.authority)
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())

    def test_identical_byte_file_inode_swap_is_rejected_at_writer_entry(self):
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._append_ledger_bytes

        def swap_on_entry(*args, **kwargs):
            source = target / "sources" / "source-one.bin"
            replacement = source.with_name("replacement.bin")
            replacement.write_bytes(source.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, source)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_append_ledger_bytes",
            side_effect=swap_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "identity changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())

    def test_identical_nested_directory_inode_swap_is_rejected_at_writer_entry(self):
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._append_ledger_bytes

        def swap_on_entry(*args, **kwargs):
            sources = target / "sources"
            replacement = target / "replacement-sources"
            replacement.mkdir(mode=0o700)
            copied = replacement / "source-one.bin"
            copied.write_bytes((sources / "source-one.bin").read_bytes())
            copied.chmod(0o600)
            displaced = self.base / "displaced-sources"
            sources.rename(displaced)
            replacement.rename(sources)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_append_ledger_bytes",
            side_effect=swap_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "identity changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())

    def test_post_ledger_mutation_blocks_pointer_publication(self):
        target = self.study / "imports" / self.source_manifest["import_id"]

        def mutate(name):
            if name == "after_ledger":
                source = target / "sources" / "source-one.bin"
                source.write_bytes(b"changed after ledger\n")
                source.chmod(0o600)

        with patch.object(
            publication_module,
            "_transaction_step",
            side_effect=mutate,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertTrue((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)

    def test_normal_commit_completion_entry_mutation_preserves_pending(self):
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._claim_completed_transaction

        def mutate_on_entry(*args, **kwargs):
            source = target / "sources" / "source-one.bin"
            source.write_bytes(b"changed at commit removal\n")
            source.chmod(0o600)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_claim_completed_transaction",
            side_effect=mutate_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                self._commit()
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertTrue((self.study / "import-ledger.jsonl").exists())
        self.assertTrue((self.study / "current-import.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)

    def test_recovery_completion_entry_mutation_preserves_pending(self):
        def crash(name):
            if name == "after_generation":
                raise RuntimeError("injected generation-only crash")

        with patch.object(
            publication_module,
            "_transaction_step",
            side_effect=crash,
        ):
            with self.assertRaisesRegex(RuntimeError, "generation-only"):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        real = publication_module._claim_completed_transaction

        def mutate_on_entry(*args, **kwargs):
            source = target / "sources" / "source-one.bin"
            source.write_bytes(b"changed at recovery removal\n")
            source.chmod(0o600)
            return real(*args, **kwargs)

        with patch.object(
            publication_module,
            "_claim_completed_transaction",
            side_effect=mutate_on_entry,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                recover_study(study_root=self.study, authority=self.authority)
        self.assertTrue(target.exists())
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertTrue((self.study / "import-ledger.jsonl").exists())
        self.assertTrue((self.study / "current-import.json").exists())
        with self.assertRaises(ImportConflict):
            recover_study(study_root=self.study, authority=self.authority)

    def test_unmanifested_empty_directory_is_rejected(self):
        (self.stage / "unmanifested-empty").mkdir(mode=0o700)
        with self.assertRaisesRegex(ImportConflict, "directory inventory"):
            self._commit()
        self.assertFalse((self.study / "imports").exists())

    def test_unmanifested_nested_empty_directory_is_rejected(self):
        nested = self.stage / "sources" / "nested" / "empty"
        nested.mkdir(mode=0o700, parents=True)
        (self.stage / "sources" / "nested").chmod(0o700)
        with self.assertRaisesRegex(ImportConflict, "directory inventory"):
            self._commit()
        self.assertFalse((self.study / "imports").exists())

    def test_idempotent_retry_rejects_changed_directory_inventory(self):
        first = self._commit()
        retry = self._stage()
        (retry / "retry-only-empty").mkdir(mode=0o700)
        with self.assertRaisesRegex(ImportConflict, "directory inventory"):
            self._commit(retry)
        self.assertEqual(
            first.ledger_digest,
            recover_study(
                study_root=self.study,
                authority=self.authority,
            ).ledger_digest,
        )

    def test_recovery_rejects_extra_published_directory(self):
        def crash(name):
            if name == "after_generation":
                raise RuntimeError("injected generation-only crash")

        with patch.object(
            publication_module,
            "_transaction_step",
            side_effect=crash,
        ):
            with self.assertRaisesRegex(RuntimeError, "generation-only"):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        (target / "unmanifested-empty").mkdir(mode=0o700)
        with self.assertRaisesRegex(ImportConflict, "directory inventory"):
            recover_study(study_root=self.study, authority=self.authority)
        self.assertTrue((self.study / ".outcome-import-pending.json").exists())
        self.assertFalse((self.study / "import-ledger.jsonl").exists())
        self.assertFalse((self.study / "current-import.json").exists())

    def test_import_id_must_be_one_portable_safe_component(self):
        invalid = (
            "x/../../escaped-by-import-id",
            "/absolute-import",
            ".",
            "..",
            "x..y",
            "x\\..\\escaped",
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            "x\N{DIVISION SLASH}y",
            "CON",
            "con.txt",
            "LPT1",
            "file.",
            "x\x00y",
            "x" * 129,
        )
        escaped = self.study / "escaped-by-import-id"
        for import_id in invalid:
            with self.subTest(import_id=repr(import_id)):
                stage = self._stage(
                    import_id=import_id,
                    next_status="descriptive_only",
                    allow_unsafe_import_id=True,
                )
                with self.assertRaisesRegex(ImportConflict, "safe component"):
                    self._commit(stage)
                self.assertFalse(escaped.exists())
                self.assertFalse((self.study / "import-ledger.jsonl").exists())
                self.assertFalse((self.study / "current-import.json").exists())

    def test_unsupported_platform_has_no_racy_publication_fallback(self):
        with patch.object(publication_module.sys, "platform", "unsupported-os"):
            with self.assertRaisesRegex(ImportConflict, "unsupported"):
                self._commit()
        target = self.study / "imports" / self.source_manifest["import_id"]
        self.assertFalse(target.exists())

    def test_stage_symlink_hardlink_traversal_and_permission_attacks_fail(self):
        source = self.stage / "sources" / "source-one.bin"
        attacks = []
        link_stage = self._stage()
        (link_stage / "sources" / "source-one.bin").unlink()
        (link_stage / "sources" / "source-one.bin").symlink_to(source)
        attacks.append(link_stage)
        hard_stage = self._stage()
        (hard_stage / "sources" / "source-one.bin").unlink()
        os.link(source, hard_stage / "sources" / "source-one.bin")
        attacks.append(hard_stage)
        mode_stage = self._stage()
        (mode_stage / "source-manifest.json").chmod(0o644)
        attacks.append(mode_stage)
        for stage in attacks:
            with self.subTest(stage=stage.name):
                with self.assertRaises(ImportConflict):
                    self._commit(stage)

    def test_manifest_parent_traversal_is_rejected_before_publication(self):
        manifest_path = self.stage / "generation-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"][0]["relative_path"] = "../outside"
        manifest["generation_sha256"] = None
        manifest["generation_sha256"] = sha256_json(manifest)
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        manifest_path.chmod(0o600)
        with self.assertRaisesRegex(ImportConflict, "traversal"):
            self._commit()

    def test_stage_root_substitution_after_first_validation_fails(self):
        original = publication_module.validate_complete_staged_generation
        calls = 0

        def substitute(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                old = self.stage.with_name(self.stage.name + "-old")
                self.stage.rename(old)
                replacement = self._stage()
                replacement.rename(self.stage)
            return result

        with patch.object(
            publication_module,
            "validate_complete_staged_generation",
            side_effect=substitute,
        ):
            with self.assertRaisesRegex(ImportConflict, "changed"):
                self._commit()

    def test_study_root_substitution_after_pending_fails_closed(self):
        displaced = self.study.with_name(self.study.name + "-displaced")

        def substitute(name):
            if name == "after_pending":
                self.study.rename(displaced)

        try:
            with patch.object(
                publication_module, "_transaction_step", side_effect=substitute
            ):
                with self.assertRaisesRegex(ImportConflict, "reauthentication"):
                    self._commit()
            self.assertFalse(
                (displaced / "imports" / self.source_manifest["import_id"]).exists()
            )
            self.assertFalse((displaced / "import-ledger.jsonl").exists())
            self.assertFalse((displaced / "current-import.json").exists())
        finally:
            if displaced.exists() and not self.study.exists():
                displaced.rename(self.study)

    def test_persisted_handoff_must_equal_its_matched_projection(self):
        handoff = deepcopy(self.handoff)
        observation = handoff["validation_observations"][0]
        observation["observation_id"] = "forged-validation-observation"
        observation["observation_sha256"] = None
        observation["observation_sha256"] = sha256_json(observation)
        handoff["handoff_sha256"] = None
        handoff["handoff_sha256"] = sha256_json(handoff)
        with self.assertRaisesRegex(ContractError, "matched projection"):
            validate_validation_handoff_document(handoff)

    def test_non_preregistered_generation_rejects_validation_handoff(self):
        for status in ("descriptive_only", "blocked"):
            with self.subTest(status=status):
                stage = self._stage(next_status=status)
                with self.assertRaisesRegex(
                    ImportConflict, "non-preregistered generation"
                ):
                    validate_complete_staged_generation(
                        stage, authority=self.authority
                    )

    def test_preregistered_generation_still_requires_validation_handoff(self):
        stage = self._stage(import_id="preregistered-without-handoff")
        with self.assertRaisesRegex(
            ImportConflict, "requires exactly one validation_handoff"
        ):
            validate_complete_staged_generation(
                stage, authority=self.authority
            )

    def test_supporting_record_statuses_must_match_each_other_and_manifest(self):
        cases = (
            ("normalized-only", "descriptive_only", "preregistered_holdout"),
            ("binding-only", "preregistered_holdout", "descriptive_only"),
            ("both-versus-manifest", "descriptive_only", "descriptive_only"),
        )
        for label, projection_status, binding_status in cases:
            with self.subTest(case=label):
                stage = self._stage()
                normalized = json.loads(
                    (stage / "normalized-observations.json").read_bytes()
                )
                for row in normalized:
                    projection = row["validation_projection"]
                    projection["evidence_status"] = projection_status
                    projection["assignment"]["design"] = (
                        "randomized"
                        if projection_status == "preregistered_holdout"
                        else "observational"
                    )
                    row["normalized_observation_sha256"] = None
                    row["normalized_observation_sha256"] = sha256_json(row)
                self._rewrite_manifested_json(
                    stage, "normalized-observations.json", normalized
                )

                normalized_by_id = {
                    row["observation_id"]: row for row in normalized
                }
                bindings = json.loads(
                    (stage / "observation-bindings.json").read_bytes()
                )
                for binding in bindings:
                    binding["evidence_status"] = binding_status
                    binding["normalized_observation_sha256"] = (
                        normalized_by_id[binding["observation_id"]][
                            "normalized_observation_sha256"
                        ]
                    )
                    binding["observation_binding_sha256"] = None
                    binding["observation_binding_sha256"] = sha256_json(
                        binding
                    )
                self._rewrite_manifested_json(
                    stage, "observation-bindings.json", bindings
                )

                with self.assertRaisesRegex(
                    ImportConflict,
                    "statuses do not match generation evidence status",
                ):
                    validate_complete_staged_generation(
                        stage, authority=self.authority
                    )

    def test_ledger_reseal_hmac_replay_and_byte_attacks_fail(self):
        self._commit()
        ledger = self.study / "import-ledger.jsonl"
        original = ledger.read_bytes()
        envelope = json.loads(original)
        mutations = (
            ("next_evidence_status", "blocked"),
            ("first_outcome_accessed_at", "2026-08-10T11:01:00Z"),
            ("source_exported_at", "2026-08-10T12:00:30Z"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = deepcopy(envelope)
                changed[field] = value
                changed["envelope_sha256"] = None
                changed["envelope_sha256"] = sha256_json({
                    **changed,
                    "event_hmac_sha256": None,
                })
                ledger.write_bytes(canonical_json_bytes(changed))
                with self.assertRaisesRegex(ImportConflict, "authentication"):
                    recover_study(study_root=self.study, authority=self.authority)
        for attacked in (original.rstrip(b"\n"), original + original):
            ledger.write_bytes(attacked)
            with self.assertRaises(ImportConflict):
                recover_study(study_root=self.study, authority=self.authority)

    def test_pointer_tamper_and_status_or_chronology_upgrade_fail(self):
        self._commit()
        pointer = self.study / "current-import.json"
        document = json.loads(pointer.read_bytes())
        document["import_id"] = "forged"
        pointer.write_bytes(canonical_json_bytes(document))
        with self.assertRaisesRegex(ImportConflict, "pointer"):
            recover_study(study_root=self.study, authority=self.authority)

        # Fresh stages are rejected before any publication.
        bad_status = self._stage(
            import_id="bad-status",
            previous_status="descriptive_only",
            next_status="preregistered_holdout",
        )
        with self.assertRaisesRegex(ImportConflict, "monotone"):
            self._commit(bad_status)
        bad_time = self._stage(
            import_id="bad-time",
            imported_at="2026-08-10T10:00:00Z",
        )
        with self.assertRaisesRegex(ImportConflict, "chronology"):
            self._commit(bad_time)

    def test_ledger_reordering_is_rejected(self):
        first = self._commit()
        second_stage = self._stage(
            import_id="import-two",
            imported_at="2026-08-11T12:01:00Z",
            next_status="descriptive_only",
            source_exported_at="2026-08-11T12:00:00Z",
        )
        self._commit(second_stage, previous=first.ledger_digest)
        ledger = self.study / "import-ledger.jsonl"
        lines = ledger.read_bytes().splitlines(keepends=True)
        ledger.write_bytes(lines[1] + lines[0])
        with self.assertRaisesRegex(ImportConflict, "chain"):
            recover_study(study_root=self.study, authority=self.authority)

    def test_correction_is_additive_and_binds_old_and_new_source(self):
        first = self._commit()
        prior_bytes = {
            path.relative_to(first.generation_path): path.read_bytes()
            for path in first.generation_path.rglob("*")
            if path.is_file()
        }
        replacement = b"corrected aggregate export bytes\n"
        observation_ids = [
            row["observation_id"] for row in self.handoff["normalized_observations"]
        ]
        request = {
            "schema_version": "outcome-correction-request-v1",
            "correction_id": "correction-one",
            "study_id": self.source_manifest["study_id"],
            "requested_at": "2026-08-11T12:00:30Z",
            "actor": "validation-owner",
            "reason_code": "measurement-correction",
            "reason": "platform issued corrected aggregate values",
            "supersedes_import_id": first.import_id,
            "supersedes_observation_ids": observation_ids,
            "expected_analytical_identity_sha256": first.analytical_identity_sha256,
            "replacement_source_sha256": sha256_bytes(replacement),
            "correction_request_sha256": None,
        }
        request["correction_request_sha256"] = sha256_json(request)
        corrected_stage = self._stage(
            import_id="correction-import",
            imported_at="2026-08-11T12:01:00Z",
            next_status="descriptive_only",
            source_exported_at="2026-08-11T12:00:00Z",
            correction=request,
            altered_source=replacement,
        )
        corrected = self._commit(corrected_stage, previous=first.ledger_digest)
        self.assertEqual(
            first.analytical_identity_sha256,
            corrected.analytical_identity_sha256,
        )
        self.assertEqual(prior_bytes, {
            path.relative_to(first.generation_path): path.read_bytes()
            for path in first.generation_path.rglob("*")
            if path.is_file()
        })
        self.assertNotEqual(first.import_digest, corrected.import_digest)

    def test_correction_rejects_wrong_replacement_source_binding(self):
        first = self._commit()
        observation_ids = [
            row["observation_id"] for row in self.handoff["normalized_observations"]
        ]
        request = {
            "schema_version": "outcome-correction-request-v1",
            "correction_id": "correction-one",
            "study_id": self.source_manifest["study_id"],
            "requested_at": "2026-08-11T12:00:30Z",
            "actor": "validation-owner",
            "reason_code": "measurement-correction",
            "reason": "platform issued corrected aggregate values",
            "supersedes_import_id": first.import_id,
            "supersedes_observation_ids": observation_ids,
            "expected_analytical_identity_sha256": first.analytical_identity_sha256,
            "replacement_source_sha256": "sha256:" + "0" * 64,
            "correction_request_sha256": None,
        }
        request["correction_request_sha256"] = sha256_json(request)
        stage = self._stage(
            import_id="correction-import",
            imported_at="2026-08-11T12:01:00Z",
            next_status="descriptive_only",
            source_exported_at="2026-08-11T12:00:00Z",
            correction=request,
            altered_source=b"different replacement\n",
        )
        with self.assertRaisesRegex(ImportConflict, "correction source"):
            self._commit(stage, previous=first.ledger_digest)

    def test_files_are_private_and_prior_generation_is_immutable(self):
        commit = self._commit()
        before = {
            path.relative_to(commit.generation_path): path.read_bytes()
            for path in commit.generation_path.rglob("*")
            if path.is_file()
        }
        for path in commit.generation_path.rglob("*"):
            mode = stat.S_IMODE(path.lstat().st_mode)
            self.assertEqual(0o700 if path.is_dir() else 0o600, mode)
        self.assertEqual(before, {
            path.relative_to(commit.generation_path): path.read_bytes()
            for path in commit.generation_path.rglob("*")
            if path.is_file()
        })
        for path in (
            self.study / ".outcome-import.lock",
            self.study / "import-ledger.jsonl",
            self.study / "current-import.json",
        ):
            self.assertEqual(0o600, stat.S_IMODE(path.lstat().st_mode))

    def test_study_lock_hardlink_rejection_does_not_leak_descriptors(self):
        lock = self.study / ".outcome-import.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        alias = self.study / ".outcome-import.lock-hardlink"
        os.link(lock, alias)
        before = len(os.listdir("/dev/fd"))
        for _ in range(100):
            with self.assertRaisesRegex(ImportConflict, "lock is unsafe"):
                with publication_module.StudyLock(self.study):
                    self.fail("unsafe hard-linked lock was acquired")
        after = len(os.listdir("/dev/fd"))
        self.assertEqual(before, after)

    def test_study_lock_success_and_acquisition_failure_close_descriptors(self):
        before = len(os.listdir("/dev/fd"))
        with publication_module.StudyLock(self.study):
            self.assertEqual(before + 1, len(os.listdir("/dev/fd")))
        self.assertEqual(before, len(os.listdir("/dev/fd")))
        with patch.object(
            publication_module.fcntl,
            "flock",
            side_effect=OSError("injected acquisition failure"),
        ):
            with self.assertRaisesRegex(ImportConflict, "acquisition failed"):
                with publication_module.StudyLock(self.study):
                    self.fail("failed lock was acquired")
        self.assertEqual(before, len(os.listdir("/dev/fd")))

    def test_transaction_steps_are_in_exact_durable_order(self):
        observed: list[str] = []
        with patch.object(
            publication_module, "_transaction_step", side_effect=observed.append
        ):
            self._commit()
        self.assertEqual([
            "after_pending",
            "after_generation",
            "after_ledger",
            "after_pointer",
            "after_completion_claim",
            "after_pending_completed",
        ], observed)


if __name__ == "__main__":
    unittest.main()
