from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"),
)

from audience_panel_builder.common import sha256_json  # noqa: E402
from audience_panel_builder.population.validation import library as library_module  # noqa: E402
from audience_panel_builder.population.validation import package as package_module  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    ContractError,
    validate_preregistration,
)
from audience_panel_builder.population.validation.library import (  # noqa: E402
    LibraryLock,
    LibraryLockError,
)
from conformance.test_tier4_held_out_evaluation import (  # noqa: E402
    _AUTHORITY_REGISTRIES,
    build_claim_family,
    comparison,
    evaluate_held_out_ordering,
    sealed_registration,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    approved_seal,
    preregistration_fixture,
)
from conformance.test_tier4_validation_library import (  # noqa: E402
    append_claim_lifecycle_event,
    current_claim,
    register_validation_package,
    show_claim,
)
from conformance.test_tier4_validation_package import (  # noqa: E402
    build_validation_package,
    validate_validation_package,
)
from conformance.test_tier4_validation_reporting import (  # noqa: E402
    build_validation_report_payload,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )


def _package_helper() -> object:
    from conformance.test_tier4_validation_package import (
        Tier4ValidationPackageTests,
    )

    return Tier4ValidationPackageTests()


class Tier4ReviewerRegressionTests(unittest.TestCase):
    def test_live_authority_and_closed_claim_copy_block_portable_forgery(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            inputs = helper._inputs(root / "inputs", panel)
            package = build_validation_package(
                inputs=inputs,
                panel_package_path=panel,
                output_dir=root / "valid",
            )
            with self.assertRaises(ContractError):
                package_module.validate_validation_package(
                    package, authority_registry={},
                )

            claim_path = inputs["panel-tier4-claim.json"]
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            claim["claim_text"] = "Guarantees the winning ad across every market."
            claim["required_disclaimer"] = "Guaranteed performance."
            claim["claim_sha256"] = sha256_json({
                **claim, "claim_sha256": None,
            })
            _write_json(claim_path, claim)
            report_manifest_path = inputs[
                "panel-validation-report-manifest.json"
            ]
            report_manifest = json.loads(
                report_manifest_path.read_text(encoding="utf-8"),
            )
            report_manifest["result_sha256"] = claim["claim_sha256"]
            _write_json(report_manifest_path, report_manifest)
            with self.assertRaises(ContractError):
                build_validation_package(
                    inputs=inputs,
                    panel_package_path=panel,
                    output_dir=root / "forged",
                )

    def test_holdout_partition_never_splits_one_study(self) -> None:
        draft = preregistration_fixture()
        first = draft["validation_blocks"][0]
        second = deepcopy(first)
        second["block_id"] = "campaign-q4"
        second["planned_arm_ids"] = ["arm-b"]
        second["planned_segment_membership"] = [{
            "arm_id": "arm-b", "segment_ids": ["enterprise"],
        }]
        draft["validation_blocks"].append(second)
        draft["segment_inventory"][0]["planned_block_ids"].append("campaign-q4")
        with self.assertRaisesRegex(ContractError, "never split"):
            approved_seal(draft)

        draft["holdout_partition"] = {
            "partition_unit": "campaign",
            "held_out_ids": [first["study_id"]],
        }
        registered = approved_seal(draft)
        self.assertEqual(
            "campaign",
            validate_preregistration(registered)["holdout_partition"][
                "partition_unit"
            ],
        )

    def test_stricter_registered_coverage_threshold_blocks_claim_support(self) -> None:
        registration = sealed_registration(blocks=13, minimum_blocks=13)
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration,
            comparisons=comparisons,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(
            "evaluated_with_limitations", evaluation["decision"]["status"],
        )
        self.assertIn(
            "reason-code:coverage-threshold", evaluation["limitations"],
        )
        self.assertEqual(
            "insufficient", evaluation["sample_sufficiency"]["status"],
        )
        self.assertIn(
            "reason-code:sample-sufficiency", evaluation["limitations"],
        )

    def test_package_replays_evaluation_instead_of_trusting_edited_results(
        self,
    ) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            inputs = helper._inputs(root / "inputs", panel)
            evaluation_path = inputs["panel-held-out-evaluation.json"]
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
            evaluation["overall_diagnostics"]["tau"]["point"] = 0.99
            evaluation["evaluation_sha256"] = sha256_json({
                **evaluation, "evaluation_sha256": None,
            })
            _write_json(evaluation_path, evaluation)
            with self.assertRaisesRegex(
                package_module.ValidationPackageError, "replayed",
            ):
                build_validation_package(
                    inputs=inputs,
                    panel_package_path=panel,
                    output_dir=root / "output",
                )

    def test_standalone_report_replays_edited_evaluation_evidence(
        self,
    ) -> None:
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration,
            comparisons=comparisons,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        evaluation["overall_diagnostics"]["tau"]["point"] = 0.99
        evaluation["evaluation_sha256"] = sha256_json({
            **evaluation, "evaluation_sha256": None,
        })
        with self.assertRaisesRegex(ContractError, "replayed"):
            build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=None,
            )

    def test_leave_one_batch_instability_is_disclosed_but_descriptive(
        self,
    ) -> None:
        registration = sealed_registration()
        comparisons = [
            comparison(
                registration,
                index,
                middle_swap=index < 10,
            )
            for index in range(12)
        ]
        family = build_claim_family(
            registrations=[registration],
            comparisons_by_registration={
                registration["registration_id"]: comparisons,
            },
            built_at="2026-09-01T00:00:00Z",
        )
        evaluation = evaluate_held_out_ordering(
            registration=registration,
            comparisons=comparisons,
            claim_family=family,
            evaluated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(
            "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds",
            evaluation["influence_diagnostics"]["status"],
        )
        self.assertEqual(
            12,
            len(evaluation["influence_diagnostics"]["leave_one_block"]),
        )
        self.assertEqual(
            3,
            len(evaluation["influence_diagnostics"]["leave_one_batch"]),
        )
        self.assertNotIn(
            "reason-code:influence-instability", evaluation["limitations"],
        )
        self.assertEqual(
            "tier4_supported", evaluation["decision"]["status"],
        )

    def test_directory_publish_never_replaces_a_concurrent_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "stage"
            target = root / "output"
            source.mkdir()
            target.mkdir()
            (source / "member").write_text("staged", encoding="utf-8")
            with self.assertRaisesRegex(
                package_module.ValidationPackageSafetyError,
                "already exists",
            ):
                package_module._rename_directory_no_replace(source, target)
            self.assertTrue(source.exists())
            self.assertTrue(target.exists())
            self.assertEqual([], list(target.iterdir()))

    def test_v1_rejects_assertion_only_interim_looks(self) -> None:
        draft = preregistration_fixture()
        draft["interim_analysis_rules"] = {
            "allowed": True,
            "maximum_looks": 2,
        }
        with self.assertRaisesRegex(ContractError, "one final analysis only"):
            approved_seal(draft)

    def test_package_audit_observations_are_the_exact_evaluated_set(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            inputs = helper._inputs(root / "inputs", panel)
            observations_path = inputs["panel-validation-observations.json"]
            observations = json.loads(
                observations_path.read_text(encoding="utf-8"),
            )
            _write_json(observations_path, observations[:-1])
            with self.assertRaisesRegex(
                package_module.ValidationPackageError,
                "exactly equal",
            ):
                build_validation_package(
                    inputs=inputs,
                    panel_package_path=panel,
                    output_dir=root / "out",
                )

    def test_packaged_report_is_the_exact_canonical_result_projection(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            first_inputs = helper._inputs(root / "first-inputs", panel)
            first_package = build_validation_package(
                inputs=first_inputs,
                panel_package_path=panel,
                output_dir=root / "first-output",
            )
            library = root / "library"
            register_validation_package(
                first_package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )

            second_inputs = helper._inputs(root / "second-inputs", panel)
            report_path = second_inputs["panel-validation-report.html"]
            report_bytes = report_path.read_bytes().replace(
                b"</body>", b"<p>Different authenticated report.</p></body>",
            )
            report_path.write_bytes(report_bytes)
            manifest_path = second_inputs[
                "panel-validation-report-manifest.json"
            ]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
            manifest["report_byte_count"] = len(report_bytes)
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                Exception, "canonical projection",
            ):
                build_validation_package(
                    inputs=second_inputs,
                    panel_package_path=panel,
                    output_dir=root / "second-output",
                )

    def test_same_scope_registration_does_not_silently_replace_current(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            first_inputs = helper._inputs(root / "first-inputs", panel)
            first_package = build_validation_package(
                inputs=first_inputs,
                panel_package_path=panel,
                output_dir=root / "first-output",
            )
            replacement_inputs = helper._inputs(
                root / "replacement-inputs", panel,
                registration_id="validation-q3-replacement",
            )
            replacement_package = build_validation_package(
                inputs=replacement_inputs,
                panel_package_path=panel,
                output_dir=root / "replacement-output",
            )
            library = root / "library"
            first = register_validation_package(
                first_package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            replacement = register_validation_package(
                replacement_package,
                library_root=library,
                registered_at="2026-09-03T00:00:00Z",
            )["claim"]
            selected = current_claim(
                first["panel_id"],
                first["panel_version"],
                first["claim_scope_sha256"],
                library_root=library,
                as_of="2026-09-04T00:00:00Z",
            )["claim"]
            self.assertEqual(first["claim_id"], selected["claim_id"])
            self.assertNotEqual(replacement["claim_id"], selected["claim_id"])

    def test_explicit_supersession_selects_its_named_replacement_only(
        self,
    ) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            first_package = build_validation_package(
                inputs=helper._inputs(
                    root / "first-inputs", panel,
                    registration_id="validation-chain-a",
                ),
                panel_package_path=panel,
                output_dir=root / "first-output",
            )
            dormant_package = build_validation_package(
                inputs=helper._inputs(
                    root / "dormant-inputs", panel,
                    registration_id="validation-chain-c",
                ),
                panel_package_path=panel,
                output_dir=root / "dormant-output",
            )
            replacement_package = build_validation_package(
                inputs=helper._inputs(
                    root / "replacement-inputs", panel,
                    registration_id="validation-chain-b",
                ),
                panel_package_path=panel,
                output_dir=root / "replacement-output",
            )
            library = root / "library"
            first = register_validation_package(
                first_package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            dormant = register_validation_package(
                dormant_package,
                library_root=library,
                registered_at="2026-09-03T00:00:00Z",
            )["claim"]
            replacement = register_validation_package(
                replacement_package,
                library_root=library,
                registered_at="2026-09-04T00:00:00Z",
            )["claim"]
            append_claim_lifecycle_event(
                claim_id=first["claim_id"],
                event_type="superseded",
                effective_at="2026-09-05T00:00:00Z",
                actor_id="maintainer-001",
                reason="Named replacement passed the refresh evaluation.",
                evidence_sha256=[
                    first["claim_sha256"], replacement["claim_sha256"],
                ],
                replacement_claim_id=replacement["claim_id"],
                library_root=library,
            )
            selected = current_claim(
                first["panel_id"],
                first["panel_version"],
                first["claim_scope_sha256"],
                library_root=library,
                as_of="2026-09-06T00:00:00Z",
            )["claim"]
            self.assertEqual(replacement["claim_id"], selected["claim_id"])
            self.assertNotEqual(dormant["claim_id"], selected["claim_id"])

    def test_report_marks_a_registered_same_scope_claim_not_current(
        self,
    ) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            current_inputs = helper._inputs(
                root / "current-inputs", panel,
                registration_id="validation-report-current",
            )
            dormant_inputs = helper._inputs(
                root / "dormant-inputs", panel,
                registration_id="validation-report-dormant",
            )
            current_package = build_validation_package(
                inputs=current_inputs,
                panel_package_path=panel,
                output_dir=root / "current-output",
            )
            dormant_package = build_validation_package(
                inputs=dormant_inputs,
                panel_package_path=panel,
                output_dir=root / "dormant-output",
            )
            library = root / "library"
            register_validation_package(
                current_package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )
            register_validation_package(
                dormant_package,
                library_root=library,
                registered_at="2026-09-03T00:00:00Z",
            )
            payload = build_validation_report_payload(
                registration=json.loads(
                    dormant_inputs[
                        "panel-validation-preregistration.json"
                    ].read_text(encoding="utf-8"),
                ),
                evaluation=json.loads(
                    dormant_inputs[
                        "panel-held-out-evaluation.json"
                    ].read_text(encoding="utf-8"),
                ),
                claim=json.loads(
                    dormant_inputs["panel-tier4-claim.json"].read_text(
                        encoding="utf-8",
                    ),
                ),
                as_of="2026-09-04T00:00:00Z",
                library_root=library,
                validation_package_path=dormant_package,
            )
            self.assertFalse(payload["active_claim"])
            self.assertEqual("not_current", payload["claim_status"])

    def test_out_of_order_lifecycle_append_is_rejected_without_corruption(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            package = build_validation_package(
                inputs=helper._inputs(root / "inputs", panel),
                panel_package_path=panel,
                output_dir=root / "output",
            )
            library = root / "library"
            claim = register_validation_package(
                package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            append_claim_lifecycle_event(
                claim_id=claim["claim_id"],
                event_type="withdrawn",
                effective_at="2026-12-01T00:00:00Z",
                actor_id="maintainer-001",
                reason="Evidence was withdrawn.",
                evidence_sha256=[claim["claim_sha256"]],
                replacement_claim_id=None,
                library_root=library,
            )
            with self.assertRaisesRegex(Exception, "strictly increasing"):
                append_claim_lifecycle_event(
                    claim_id=claim["claim_id"],
                    event_type="invalidated",
                    effective_at="2026-11-01T00:00:00Z",
                    actor_id="maintainer-001",
                    reason="Attempted out-of-order transition.",
                    evidence_sha256=[claim["claim_sha256"]],
                    replacement_claim_id=None,
                    library_root=library,
                )
            self.assertEqual(
                1,
                show_claim(
                    claim["claim_id"], library_root=library,
                )["claim"]["event_count"],
            )

    def test_panel_binding_uses_one_immutable_snapshot(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            original = panel.read_bytes()
            inputs = helper._inputs(root / "inputs", panel)
            real_validate = package_module.validate_supported_audience_package

            def mutate_original_after_snapshot(snapshot: Path):
                result = real_validate(snapshot)
                panel.write_bytes(b"concurrent replacement")
                return result

            with patch.object(
                package_module,
                "validate_supported_audience_package",
                side_effect=mutate_original_after_snapshot,
            ):
                package = build_validation_package(
                    inputs=inputs,
                    panel_package_path=panel,
                    output_dir=root / "output",
                )
            validated = validate_validation_package(package)
            self.assertEqual(
                "sha256:" + hashlib.sha256(original).hexdigest(),
                validated["panel_binding"]["package_sha256"],
            )

    def test_package_and_registration_failures_are_recoverable(self) -> None:
        helper = _package_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = helper._panel(root / "panel")
            inputs = helper._inputs(root / "inputs", panel)
            real_member_write = package_module._atomic_write_new
            calls = 0

            def fail_second_member(path: Path, data: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected staged member failure")
                real_member_write(path, data)

            output = root / "output"
            with patch.object(
                package_module,
                "_atomic_write_new",
                side_effect=fail_second_member,
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    build_validation_package(
                        inputs=inputs,
                        panel_package_path=panel,
                        output_dir=output,
                    )
            self.assertFalse(output.exists())
            package = build_validation_package(
                inputs=inputs,
                panel_package_path=panel,
                output_dir=output,
            )

            library = root / "library"
            library_module._initialize(library)
            real_atomic = library_module._atomic

            def fail_index(path: Path, data: bytes) -> None:
                if path.name == "index.json":
                    raise OSError("injected index failure")
                real_atomic(path, data)

            with patch.object(
                library_module, "_atomic", side_effect=fail_index,
            ):
                with self.assertRaisesRegex(OSError, "injected index"):
                    register_validation_package(
                        package,
                        library_root=library,
                        registered_at="2026-09-02T00:00:00Z",
                    )
            recovered = register_validation_package(
                package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )
            self.assertEqual("registered", recovered["status"])

    def test_file_lock_never_reclaims_or_unlinks_a_live_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryLock(root):
                inode = (root / "library.lock").stat().st_ino
                with self.assertRaises(LibraryLockError):
                    with LibraryLock(root, timeout_seconds=0):
                        pass
                self.assertEqual(inode, (root / "library.lock").stat().st_ino)
            with LibraryLock(root, timeout_seconds=0):
                self.assertEqual(inode, (root / "library.lock").stat().st_ino)


if __name__ == "__main__":
    unittest.main()
