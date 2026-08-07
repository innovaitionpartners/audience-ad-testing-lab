"""Attack-first conformance for the synthetic-only evidence library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.contracts import (
    validate_evidence_library,
)
from audience_panel_builder.population.experimental_calibration.evidence_library import (
    EvidenceHistoryError,
    EvidenceLibraryConflict,
    EvidenceLibrarySafetyError,
    append_evidence_correction,
    append_evidence_entry,
    initialize_evidence_library,
    list_compatible_evidence,
    load_evidence_library,
)
from conformance.experimental_calibration_fixtures import (
    evidence_observation_fixture,
    registry_fixture,
    rehash,
)


class ExperimentalEvidenceLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary.name)
        self.root = self.temp / "library"
        self.registry = registry_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> dict[str, object]:
        return initialize_evidence_library(
            library_root=self.root,
            library_id="fictional-outcome-history",
            created_at="2026-07-01T00:00:00Z",
        )

    def append(self, sequence: int, timestamp: str | None = None):
        return append_evidence_entry(
            library_root=self.root,
            observation=evidence_observation_fixture(sequence),
            attribute_registry=self.registry,
            ingested_at=timestamp or f"2026-07-{sequence + 1:02d}T00:00:00Z",
        )

    def correction_observation(
        self,
        sequence: int,
        *,
        base_sequence: int = 1,
    ) -> dict[str, object]:
        """A new report for the exact same analytical row."""

        result = evidence_observation_fixture(base_sequence)
        result["observation_id"] = f"fictional-correction-{sequence:03d}"
        result["source"]["source_sha256"] = (
            "sha256:"
            + hashlib.sha256(
                f"fictional-correction-source-{sequence:03d}".encode()
            ).hexdigest()
        )
        primary = result["measurement_definition"]["primary_metric_id"]
        for event in result["outcome_events"]:
            if event["metric_id"] == primary and event["count"] is not None:
                event["count"] += sequence
        return rehash(result, "observation_sha256")

    def snapshot(self, path: Path) -> dict[str, tuple[int, bytes]]:
        if not path.exists() and not path.is_symlink():
            return {}
        result = {}
        for item in sorted([path, *path.rglob("*")]):
            relative = str(item.relative_to(path.parent))
            if item.is_symlink():
                value = ("symlink:" + str(item.readlink())).encode()
            elif item.is_file():
                value = item.read_bytes()
            else:
                value = b"directory"
            result[relative] = (item.lstat().st_mode, value)
        return result

    def test_initialize_requires_new_root_and_writes_closed_empty_library(self):
        initialized = self.initialize()
        self.assertEqual([], initialized["entry_ids"])
        self.assertEqual(0, initialized["event_count"])
        before = self.snapshot(self.root)
        with self.assertRaisesRegex(EvidenceLibraryConflict, "already exists"):
            self.initialize()
        self.assertEqual(before, self.snapshot(self.root))

    def test_concurrent_initializers_have_one_atomic_owner(self):
        def initialize():
            return self.initialize()

        successes = failures = 0
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(initialize) for _ in range(2)]
            for future in futures:
                try:
                    future.result()
                    successes += 1
                except EvidenceLibraryConflict:
                    failures += 1
        self.assertEqual((1, 1), (successes, failures))
        loaded = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-01T00:00:00Z",
            expected_head_receipt=None,
        )
        self.assertEqual(0, loaded["event_count"])

    def test_append_is_immutable_and_replayable_as_of_time(self):
        self.initialize()
        first = self.append(1, "2026-07-02T00:00:00Z")
        first_bytes = (self.root / "entries" / "fictional-observation-001.json").read_bytes()
        second = self.append(2, "2026-07-03T00:00:00Z")
        replay = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-02T12:00:00Z",
            expected_head_receipt=first["head_receipt"],
        )
        self.assertEqual(["fictional-observation-001"], replay["entry_ids"])
        self.assertNotEqual(
            first["head_receipt"]["event_sha256"],
            second["head_receipt"]["event_sha256"],
        )
        self.assertEqual(
            first_bytes,
            (self.root / "entries" / "fictional-observation-001.json").read_bytes(),
        )

    def test_duplicate_source_row_and_observation_hashes_are_rejected(self):
        self.initialize()
        original = evidence_observation_fixture(1)
        append_evidence_entry(
            library_root=self.root,
            observation=original,
            attribute_registry=self.registry,
            ingested_at="2026-07-02T00:00:00Z",
        )
        duplicate_source_row = deepcopy(original)
        duplicate_source_row["observation_id"] = "fictional-observation-copy"
        duplicate_source_row = rehash(
            duplicate_source_row, "observation_sha256"
        )
        with self.assertRaisesRegex(
            EvidenceLibraryConflict, "dependent evidence.*already active"
        ):
            append_evidence_entry(
                library_root=self.root,
                observation=duplicate_source_row,
                attribute_registry=self.registry,
                ingested_at="2026-07-03T00:00:00Z",
            )
        distinct_row_same_source = evidence_observation_fixture(2)
        distinct_row_same_source["source"]["source_sha256"] = (
            original["source"]["source_sha256"]
        )
        distinct_row_same_source = rehash(
            distinct_row_same_source, "observation_sha256"
        )
        append_evidence_entry(
            library_root=self.root,
            observation=distinct_row_same_source,
            attribute_registry=self.registry,
            ingested_at="2026-07-03T00:00:00Z",
        )
        duplicate_observation = deepcopy(original)
        duplicate_observation["observation_id"] = "fictional-observation-copy"
        duplicate_observation["source"]["source_sha256"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(ContractError, "observation_sha256"):
            append_evidence_entry(
                library_root=self.root,
                observation=duplicate_observation,
                attribute_registry=self.registry,
                ingested_at="2026-07-04T00:00:00Z",
            )

    def test_dependent_experiment_block_identity_is_rejected(self):
        self.initialize()
        first = evidence_observation_fixture(1)
        self.append(1)
        dependent = evidence_observation_fixture(2)
        dependent["experiment_binding"] = deepcopy(first["experiment_binding"])
        dependent["entity_identity"]["campaign_id"] = first["entity_identity"]["campaign_id"]
        dependent["design_quality"]["grouping_identity"] = first["design_quality"]["grouping_identity"]
        dependent = rehash(dependent, "observation_sha256")
        with self.assertRaisesRegex(EvidenceLibraryConflict, "dependent.*already active"):
            append_evidence_entry(
                library_root=self.root,
                observation=dependent,
                attribute_registry=self.registry,
                ingested_at="2026-07-03T00:00:00Z",
            )

    def test_correction_appends_replacement_and_preserves_historical_view(self):
        self.initialize()
        first = self.append(1)
        old_bytes = (self.root / "entries" / "fictional-observation-001.json").read_bytes()
        corrected = append_evidence_correction(
            library_root=self.root,
            superseded_entry_id="fictional-observation-001",
            replacement_observation=self.correction_observation(2),
            attribute_registry=self.registry,
            correction_reason="A fictional platform finalized its report.",
            corrected_at="2026-07-03T00:00:00Z",
        )
        historical = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-02T12:00:00Z",
            expected_head_receipt=first["head_receipt"],
        )
        current = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-03T12:00:00Z",
            expected_head_receipt=corrected["head_receipt"],
        )
        self.assertEqual(["fictional-observation-001"], historical["entry_ids"])
        self.assertEqual(["fictional-correction-002"], current["entry_ids"])
        self.assertEqual(
            ["fictional-observation-001"],
            historical["historical_entry_ids"],
        )
        self.assertEqual(
            ["fictional-observation-001", "fictional-correction-002"],
            current["historical_entry_ids"],
        )
        self.assertEqual(
            old_bytes,
            (self.root / "entries" / "fictional-observation-001.json").read_bytes(),
        )
        self.assertEqual("correct", current["events"][-1]["operation"])
        self.assertEqual(
            "fictional-observation-001",
            current["events"][-1]["superseded_entry_id"],
        )

    def test_correction_history_cannot_collapse_into_resealed_append(self):
        self.initialize()
        self.append(1)
        current = append_evidence_correction(
            library_root=self.root,
            superseded_entry_id="fictional-observation-001",
            replacement_observation=self.correction_observation(2),
            attribute_registry=self.registry,
            correction_reason="A fictional platform finalized its report.",
            corrected_at="2026-07-03T00:00:00Z",
        )
        collapsed = deepcopy(current)
        event = deepcopy(collapsed["events"][-1])
        event.update(
            {
                "event_id": f"event-000001-{event['entry_id']}",
                "operation": "append",
                "superseded_entry_id": None,
                "superseded_entry_sha256": None,
                "correction_reason": None,
                "previous_event_sha256": None,
            }
        )
        event = rehash(event, "event_sha256")
        receipt = deepcopy(collapsed["head_receipt"])
        receipt.update(
            {
                "receipt_id": event["event_id"],
                "event_count": 1,
                "event_id": event["event_id"],
                "event_sha256": event["event_sha256"],
                "projection_sha256": None,
                "receipt_sha256": None,
            }
        )
        collapsed.update(
            {
                "events": [event],
                "event_count": 1,
                "head_receipt": receipt,
                "library_sha256": None,
            }
        )
        projection_preimage = deepcopy(collapsed)
        projection_preimage["head_receipt"]["projection_sha256"] = None
        projection_preimage["head_receipt"]["receipt_sha256"] = None
        receipt["projection_sha256"] = sha256_json(projection_preimage)
        receipt["receipt_sha256"] = sha256_json(receipt)
        collapsed["head_receipt"] = receipt
        collapsed["library_sha256"] = sha256_json(collapsed)
        with self.assertRaises(ContractError):
            validate_evidence_library(collapsed)

    def test_correction_rejects_any_change_to_the_analytical_row(self):
        self.initialize()
        self.append(1)

        mutations = {
            "unrelated experiment": lambda row: row["experiment_binding"].update(
                {
                    "experiment_id": "unrelated-experiment",
                    "campaign_id": "unrelated-campaign",
                    "block_id": "unrelated-block",
                    "batch_id": "unrelated-batch",
                }
            ),
            "cross study": lambda row: row["synthetic_study_binding"].update(
                {
                    "study_id": "other-study",
                    "study_manifest_sha256": "sha256:" + "8" * 64,
                }
            ),
            "cross segment": lambda row: row["audience_scope"].update(
                {"segment_id": "other-segment"}
            ),
            "cross scope": lambda row: row["audience_scope"].update(
                {
                    "objective": "other-objective",
                    "placement": "other-placement",
                }
            ),
            "cross platform": lambda row: row["source"].update(
                {"platform": "google"}
            ),
            "cross creative": lambda row: row["creative_binding"].update(
                {
                    "creative_id": "other-creative",
                    "asset_sha256": "sha256:" + "7" * 64,
                }
            ),
            "cross registry": lambda row: row[
                "creative_attribute_binding"
            ].update(
                {
                    "registry_id": "other-registry",
                    "registry_sha256": "sha256:" + "6" * 64,
                }
            ),
            "cross attribution": lambda row: row[
                "measurement_definition"
            ].update({"click_window": "99-day"}),
            "cross metric": lambda row: row[
                "measurement_definition"
            ].update({"primary_metric_id": "lead"}),
            "cross denominator": lambda row: row["denominators"][0].update(
                {"denominator_kind": "clicks_all"}
            ),
            "cross hypothesis": lambda row: (
                row["creative_attribute_binding"].update({"hypothesis_ids": []}),
                [
                    attribute.update({"hypothesis_id": None})
                    for attribute in row["creative_attribute_binding"][
                        "attributes"
                    ]
                ],
            ),
            "cross maturity": lambda row: row["reporting_context"].update(
                {"maturity": "recent"}
            ),
            "cross design": lambda row: row["design_quality"].update(
                {"design": "observational"}
            ),
        }
        for index, (label, mutate) in enumerate(mutations.items(), start=10):
            with self.subTest(label=label):
                replacement = self.correction_observation(index)
                mutate(replacement)
                replacement = rehash(replacement, "observation_sha256")
                registry = self.registry
                if label == "cross registry":
                    registry = deepcopy(self.registry)
                    registry["registry_id"] = "other-registry"
                    registry = rehash(registry, "registry_sha256")
                    replacement["creative_attribute_binding"][
                        "registry_sha256"
                    ] = registry["registry_sha256"]
                    replacement = rehash(replacement, "observation_sha256")
                with self.assertRaises(
                    (ContractError, EvidenceLibraryConflict)
                ):
                    append_evidence_correction(
                        library_root=self.root,
                        superseded_entry_id="fictional-observation-001",
                        replacement_observation=replacement,
                        attribute_registry=registry,
                        correction_reason="Invalid cross-row replacement",
                        corrected_at=f"2026-07-{index:02d}T00:00:00Z",
                    )

    def test_replacement_cannot_reuse_any_registered_hash(self):
        self.initialize()
        first = evidence_observation_fixture(1)
        self.append(1)
        replacement = self.correction_observation(2)
        replacement["source"]["source_sha256"] = first["source"]["source_sha256"]
        replacement = rehash(replacement, "observation_sha256")
        with self.assertRaisesRegex(EvidenceLibraryConflict, "source.*already registered"):
            append_evidence_correction(
                library_root=self.root,
                superseded_entry_id="fictional-observation-001",
                replacement_observation=replacement,
                attribute_registry=self.registry,
                correction_reason="Correction",
                corrected_at="2026-07-03T00:00:00Z",
            )

    def test_equal_or_reordered_timestamps_fail(self):
        self.initialize()
        self.append(1)
        for timestamp in ("2026-07-02T00:00:00Z", "2026-07-01T12:00:00Z"):
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(ContractError, "strictly after"):
                    self.append(2, timestamp)

    def test_expected_head_tail_truncation_and_tampering_fail(self):
        self.initialize()
        first = self.append(1)
        second = self.append(2)
        with self.assertRaisesRegex(EvidenceHistoryError, "expected head"):
            load_evidence_library(
                library_root=self.root,
                as_of="2026-07-03T12:00:00Z",
                expected_head_receipt=first["head_receipt"],
            )
        events = self.root / "events.jsonl"
        complete = events.read_bytes()
        events.write_bytes(complete.splitlines(keepends=True)[0])
        with self.assertRaises(EvidenceHistoryError):
            load_evidence_library(
                library_root=self.root,
                as_of="2026-07-03T12:00:00Z",
                expected_head_receipt=second["head_receipt"],
            )
        events.write_bytes(complete.replace(b'"operation":"append"', b'"operation":"correc"', 1))
        with self.assertRaises(EvidenceHistoryError):
            load_evidence_library(
                library_root=self.root,
                as_of="2026-07-03T12:00:00Z",
                expected_head_receipt=second["head_receipt"],
            )

    def test_symlink_components_and_path_traversal_fail_without_writes(self):
        target = self.temp / "target"
        target.mkdir()
        symlink = self.temp / "alias"
        symlink.symlink_to(target, target_is_directory=True)
        before = self.snapshot(target)
        with self.assertRaises(EvidenceLibrarySafetyError):
            initialize_evidence_library(
                library_root=symlink / "library",
                library_id="fictional-history",
                created_at="2026-07-01T00:00:00Z",
            )
        self.assertEqual(before, self.snapshot(target))
        with self.assertRaises(EvidenceLibrarySafetyError):
            initialize_evidence_library(
                library_root=self.temp / "a" / ".." / "library",
                library_id="fictional-history",
                created_at="2026-07-01T00:00:00Z",
            )

    def test_concurrent_appenders_serialize_without_lost_updates(self):
        self.initialize()
        def run(sequence: int):
            if sequence == 2:
                time.sleep(0.05)
            return self.append(sequence, f"2026-07-0{sequence + 1}T00:00:00Z")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, (1, 2)))
        replay = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-04T00:00:00Z",
            expected_head_receipt=max(
                (item["head_receipt"] for item in results),
                key=lambda item: item["event_count"],
            ),
        )
        self.assertEqual(2, replay["event_count"])
        self.assertEqual(
            ["fictional-observation-001", "fictional-observation-002"],
            replay["entry_ids"],
        )

    def test_conflicting_concurrent_bytes_produce_one_failure(self):
        self.initialize()
        first = evidence_observation_fixture(1)
        second = evidence_observation_fixture(2)
        second["observation_id"] = first["observation_id"]
        second = rehash(second, "observation_sha256")
        def run(observation):
            return append_evidence_entry(
                library_root=self.root,
                observation=observation,
                attribute_registry=self.registry,
                ingested_at="2026-07-02T00:00:00Z",
            )
        successes = failures = 0
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, item) for item in (first, second)]
            for future in futures:
                try:
                    future.result()
                    successes += 1
                except (ContractError, EvidenceLibraryConflict):
                    failures += 1
        self.assertEqual((1, 1), (successes, failures))

    def test_two_concurrent_corrections_allow_one_active_replacement(self):
        self.initialize()
        self.append(1)
        def run(sequence: int):
            return append_evidence_correction(
                library_root=self.root,
                superseded_entry_id="fictional-observation-001",
                replacement_observation=self.correction_observation(sequence),
                attribute_registry=self.registry,
                correction_reason="Fictional correction",
                corrected_at=f"2026-07-0{sequence + 1}T00:00:00Z",
            )
        successes = failures = 0
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, sequence) for sequence in (2, 3)]
            for future in futures:
                try:
                    future.result()
                    successes += 1
                except (EvidenceLibraryConflict, ContractError):
                    failures += 1
        self.assertEqual((1, 1), (successes, failures))

    def test_concurrent_append_and_correction_preserve_serial_history(self):
        self.initialize()
        self.append(1)
        def add():
            return self.append(2, "2026-07-03T00:00:00Z")
        def correct():
            return append_evidence_correction(
                library_root=self.root,
                superseded_entry_id="fictional-observation-001",
                replacement_observation=self.correction_observation(3),
                attribute_registry=self.registry,
                correction_reason="Fictional correction",
                corrected_at="2026-07-04T00:00:00Z",
            )
        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(add), executor.submit(correct))
            for future in futures:
                try:
                    results.append(future.result())
                except ContractError:
                    # A later caller timestamp may win the serialized lock.
                    # The now-reordered request must then fail, not rewrite
                    # the valid serial history.
                    pass
        self.assertGreaterEqual(len(results), 1)
        latest = max(
            (result["head_receipt"] for result in results),
            key=lambda row: row["event_count"],
        )
        replay = load_evidence_library(
            library_root=self.root,
            as_of="2026-07-05T00:00:00Z",
            expected_head_receipt=latest,
        )
        self.assertIn(replay["event_count"], {2, 3})
        self.assertEqual(
            replay["entry_ids"],
            sorted(set(replay["entry_ids"])),
        )

    def test_compatibility_keeps_semantic_boundaries_and_design_visible(self):
        self.initialize()
        randomized = self.append(1)
        observational = append_evidence_entry(
            library_root=self.root,
            observation=evidence_observation_fixture(2, design="observational"),
            attribute_registry=self.registry,
            ingested_at="2026-07-03T00:00:00Z",
        )
        entry = randomized["entries"][0]
        compatible = list_compatible_evidence(
            library_root=self.root,
            persona_id="finance-pricing-archetype",
            segment_id=entry["segment_id"],
            platform=entry["platform"],
            metric_identity_sha256=entry["metric_identity_sha256"],
            as_of="2026-07-04T00:00:00Z",
        )
        self.assertEqual(2, len(compatible))
        self.assertEqual(
            {"randomized", "observational"},
            {row["design"] for row in compatible},
        )
        self.assertTrue(all(row["descriptive_claim_boundary"] == "associated_with_outcome" for row in compatible))
        self.assertEqual(2, observational["event_count"])

    def test_entry_and_receipt_directories_reject_unsafe_json_members(self):
        poison_target = self.temp / "poison-target"
        poison_target.write_text("poison")
        cases = (
            ("entry symlink", "entries", "symlink"),
            ("receipt symlink", "receipts", "symlink"),
            ("entry json directory", "entries", "directory"),
            ("receipt json directory", "receipts", "directory"),
        )
        for index, (label, directory, kind) in enumerate(cases, start=1):
            with self.subTest(label=label):
                self.root = self.temp / f"poison-library-{index}"
                self.initialize()
                appended = self.append(1)
                poison = self.root / directory / "poison.json"
                if kind == "symlink":
                    poison.symlink_to(poison_target)
                else:
                    poison.mkdir()
                with self.assertRaisesRegex(
                    EvidenceLibrarySafetyError,
                    "unsafe|regular",
                ):
                    load_evidence_library(
                        library_root=self.root,
                        as_of="2026-07-03T00:00:00Z",
                        expected_head_receipt=appended["head_receipt"],
                    )

    def test_synthetic_only_origin_and_registry_binding_are_enforced(self):
        self.initialize()
        real = evidence_observation_fixture(1)
        real["evidence_origin"] = "real"
        real = rehash(real, "observation_sha256")
        with self.assertRaises(ContractError):
            append_evidence_entry(
                library_root=self.root,
                observation=real,
                attribute_registry=self.registry,
                ingested_at="2026-07-02T00:00:00Z",
            )
        mismatch = evidence_observation_fixture(1)
        mismatch["creative_attribute_binding"]["registry_sha256"] = "sha256:" + "0" * 64
        mismatch = rehash(mismatch, "observation_sha256")
        with self.assertRaisesRegex(ContractError, "registry"):
            append_evidence_entry(
                library_root=self.root,
                observation=mismatch,
                attribute_registry=self.registry,
                ingested_at="2026-07-02T00:00:00Z",
            )

    def test_recovery_completes_all_three_recognized_states(self):
        self.initialize()
        cases = (
            ("old-log-old-index", "_append_event_bytes"),
            ("new-log-old-index", "_atomic_replace"),
            ("new-log-new-index", "_publish_immutable"),
        )
        import audience_panel_builder.population.experimental_calibration.evidence_library as library
        for index, (label, helper) in enumerate(cases, start=1):
            with self.subTest(label=label):
                if index > 1:
                    self.root = self.temp / f"library-{index}"
                    self.initialize()
                original = getattr(library, helper)
                state = {"failed": False}
                def fail_once(*args, **kwargs):
                    path = kwargs.get("path") or (args[0] if args else None)
                    should_fail = (
                        helper == "_append_event_bytes"
                        or helper == "_atomic_replace" and Path(path).name == "library.json"
                        or helper == "_publish_immutable" and Path(path).parent.name == "receipts"
                    )
                    if should_fail and not state["failed"]:
                        state["failed"] = True
                        raise RuntimeError(label)
                    return original(*args, **kwargs)
                with mock.patch.object(library, helper, side_effect=fail_once):
                    with self.assertRaisesRegex(RuntimeError, label):
                        self.append(1)
                recovered = load_evidence_library(
                    library_root=self.root,
                    as_of="2026-07-03T00:00:00Z",
                    expected_head_receipt=None,
                )
                self.assertEqual(["fictional-observation-001"], recovered["entry_ids"])
                self.assertFalse((self.root / "pending-evidence-transaction.json").exists())

    def test_unrecognized_partial_log_fails_closed(self):
        self.initialize()
        import audience_panel_builder.population.experimental_calibration.evidence_library as library
        original = library._append_event_bytes
        with mock.patch.object(library, "_append_event_bytes", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.append(1)
        transaction = json.loads((self.root / "pending-evidence-transaction.json").read_text())
        event_bytes = transaction["event_bytes"].encode()
        (self.root / "events.jsonl").write_bytes(event_bytes[: len(event_bytes) // 2])
        with self.assertRaisesRegex(EvidenceHistoryError, "unrecognized"):
            load_evidence_library(
                library_root=self.root,
                as_of="2026-07-03T00:00:00Z",
                expected_head_receipt=None,
            )
        library._append_event_bytes = original

    def test_static_import_guard_and_production_fixture_nonmutation(self):
        module = (
            SCRIPTS
            / "audience_panel_builder"
            / "population"
            / "experimental_calibration"
            / "evidence_library.py"
        )
        cli = SCRIPTS / "manage-synthetic-outcome-evidence-library.py"
        forbidden = (
            "population.validation",
            "audience_library",
            "package_registration",
            "active_pointer",
            "current_library",
        )
        for path in (module, cli):
            source = path.read_text()
            self.assertFalse(any(token in source for token in forbidden))

        for root_name in ("production-audience-library", "production-validation-library"):
            production = self.temp / root_name
            production.mkdir()
            (production / "index.json").write_bytes(b"production-index\n")
            (production / "active.json").write_bytes(b"production-active\n")
            before = self.snapshot(production)
            poisoned_targets = (
                production,
                production / "index.json",
                production / "active.json",
            )
            alias = self.temp / f"{root_name}-alias"
            alias.symlink_to(production, target_is_directory=True)
            for poisoned in (*poisoned_targets, alias):
                operations = (
                    lambda poisoned=poisoned: append_evidence_entry(
                        library_root=poisoned,
                        observation=evidence_observation_fixture(1),
                        attribute_registry=self.registry,
                        ingested_at="2026-07-02T00:00:00Z",
                    ),
                    lambda poisoned=poisoned: append_evidence_correction(
                        library_root=poisoned,
                        superseded_entry_id="anything",
                        replacement_observation=evidence_observation_fixture(2),
                        attribute_registry=self.registry,
                        correction_reason="Fictional",
                        corrected_at="2026-07-03T00:00:00Z",
                    ),
                    lambda poisoned=poisoned: load_evidence_library(
                        library_root=poisoned,
                        as_of="2026-07-03T00:00:00Z",
                        expected_head_receipt=None,
                    ),
                )
                for operation in operations:
                    with self.assertRaises(
                        (ContractError, EvidenceLibrarySafetyError)
                    ):
                        operation()
                    self.assertEqual(before, self.snapshot(production))

    def test_cli_failure_paths_do_not_change_poisoned_root(self):
        observation = self.temp / "observation.json"
        registry = self.temp / "registry.json"
        observation.write_bytes(canonical_json_bytes(evidence_observation_fixture(1)))
        registry.write_bytes(canonical_json_bytes(self.registry))
        for root_name in ("production-audience-library", "production-validation-library"):
            production = self.temp / root_name
            production.mkdir()
            (production / "index.json").write_bytes(b"production-index\n")
            (production / "active.json").write_bytes(b"production-active\n")
            before = self.snapshot(production)
            for poisoned in (
                production,
                production / "index.json",
                production / "active.json",
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "manage-synthetic-outcome-evidence-library.py"),
                        "append",
                        "--library-root", str(poisoned),
                        "--observation", str(observation),
                        "--attribute-registry", str(registry),
                        "--ingested-at", "2026-07-02T00:00:00Z",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(3, result.returncode)
                self.assertEqual(before, self.snapshot(production))

    def test_cli_round_trip_covers_all_public_commands(self):
        cli = SCRIPTS / "manage-synthetic-outcome-evidence-library.py"
        root = self.temp / "cli-library"
        observation = self.temp / "observation.json"
        replacement = self.temp / "replacement.json"
        registry = self.temp / "registry.json"
        receipt = self.temp / "receipt.json"
        observation.write_bytes(canonical_json_bytes(evidence_observation_fixture(1)))
        replacement.write_bytes(
            canonical_json_bytes(self.correction_observation(2))
        )
        registry.write_bytes(canonical_json_bytes(self.registry))

        def run(*arguments: str):
            result = subprocess.run(
                [sys.executable, str(cli), *arguments],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            return json.loads(result.stdout)

        run(
            "init", "--library-root", str(root),
            "--library-id", "fictional-cli-history",
            "--created-at", "2026-07-01T00:00:00Z",
        )
        appended = run(
            "append", "--library-root", str(root),
            "--observation", str(observation),
            "--attribute-registry", str(registry),
            "--ingested-at", "2026-07-02T00:00:00Z",
        )
        receipt.write_bytes(canonical_json_bytes(appended["head_receipt"]))
        shown = run(
            "show", "--library-root", str(root),
            "--entry-id", "fictional-observation-001",
            "--as-of", "2026-07-02T12:00:00Z",
        )
        self.assertEqual("fictional-observation-001", shown["entry_id"])
        verified = run(
            "verify", "--library-root", str(root),
            "--as-of", "2026-07-02T12:00:00Z",
            "--expected-head-receipt", str(receipt),
        )
        self.assertEqual(1, verified["event_count"])
        run(
            "correct", "--library-root", str(root),
            "--superseded-entry-id", "fictional-observation-001",
            "--replacement-observation", str(replacement),
            "--attribute-registry", str(registry),
            "--reason", "Fictional finalized correction",
            "--corrected-at", "2026-07-03T00:00:00Z",
        )
        listing = run(
            "list", "--library-root", str(root),
            "--as-of", "2026-07-04T00:00:00Z",
        )
        self.assertEqual(["fictional-correction-002"], listing["entry_ids"])


if __name__ == "__main__":
    unittest.main()
