from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PANEL_BUILDER_SCRIPTS = (
    ROOT / "skills" / "audience-panel-builder" / "scripts"
)
AD_TESTING_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures"
CLI = PANEL_BUILDER_SCRIPTS / "migrate-audience-v2-to-v3.py"
sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))
sys.path.insert(0, str(AD_TESTING_SCRIPTS))

from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_audience_research_v3,
    validate_composition_plan,
    validate_research_brief_v3,
    validate_saved_panel_v3,
    validate_validity_profile,
)
import audience_panel_builder.population.migration as migration_module  # noqa: E402
from audience_panel_builder.population.migration import (  # noqa: E402
    MIGRATION_PROVENANCE_VERSION,
    migrate_v2_to_v3,
)


OUTPUT_NAMES = {
    "audience-research-brief-v3.json",
    "saved-audience-panel-v3.json",
    "panel-composition-plan.json",
    "panel-validity-profile.json",
    "migration-provenance.json",
}
MIGRATED_AT = "2026-07-24T16:00:00Z"


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


class AudienceV3MigrationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        research = FIXTURES / "audience-research"
        cls.approved_brief = json.loads(
            (research / "approved-brief.json").read_text(encoding="utf-8")
        )
        cls.approved_panel = json.loads(
            (research / "approved-panel.json").read_text(encoding="utf-8")
        )
        large = FIXTURES / "e2e-large"
        cls.large_brief = json.loads(
            (large / "audience-research-brief.json").read_text(
                encoding="utf-8"
            )
        )
        cls.large_panel = json.loads(
            (large / "saved-audience-panel.json").read_text(encoding="utf-8")
        )

    def provisional_documents(
        self,
    ) -> tuple[dict[str, object], dict[str, object]]:
        brief = copy.deepcopy(self.approved_brief)
        panel = copy.deepcopy(self.approved_panel)
        brief.update(
            status="provisional_no_research",
            research_mode="provisional_no_research",
            evidence_sources=[],
            findings=[],
            research_questions=[],
        )
        brief["coverage"] = {
            key: "empty" for key in brief["coverage"]
        }
        brief["segment_hypotheses"][0].update(
            origin="provisional_user_defined",
            finding_ids=[],
            evidence_ids=[],
            confidence="low",
        )
        panel["segments"][0].update(
            origin="provisional_user_defined",
            finding_ids=[],
            evidence_ids=[],
            weight_source_evidence=[],
            weighting_rule="planning_allocation",
        )
        panel["persona_archetypes"][0].update(
            finding_ids=[],
            evidence_ids=[],
            evidence_strength="low",
        )
        for dimension in panel["context_strata"][0]["dimensions"]:
            dimension.update(
                status="experimental",
                source_evidence=[],
                finding_ids=[],
            )
        for provenance in panel["grounded_context_profiles"][0][
            "context_attribute_provenance"
        ]:
            provenance.update(
                status="experimental",
                source_evidence=[],
                finding_ids=[],
            )
        panel["persona_research"].update(
            mode="provisional_no_research",
            status="provisional_no_research",
            expires_at="2026-07-30T12:00:00Z",
            source_types=[],
            evidence_ids=[],
            source_state="no_research_sources",
            coverage=copy.deepcopy(brief["coverage"]),
        )
        return brief, panel

    def build_v2(
        self,
        root: Path,
        *,
        brief: dict[str, object] | None = None,
        panel: dict[str, object] | None = None,
    ) -> Path:
        selected_brief = self.approved_brief if brief is None else brief
        selected_panel = self.approved_panel if panel is None else panel
        research = selected_panel.get("persona_research")
        now = None
        if (
            isinstance(research, dict)
            and research.get("status") == "provisional_no_research"
        ):
            now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        result = build_audience_package(
            selected_brief,
            selected_panel,
            root,
            generator_version="1.0.0",
            now=now,
        )
        return result.package_zip_path

    def migrate(
        self,
        package: Path,
        output: Path,
        *,
        version: str = "2.0.0",
        now: datetime | None = None,
    ) -> dict[str, object]:
        return migrate_v2_to_v3(
            v2_package_path=package,
            new_panel_version=version,
            migrated_at=MIGRATED_AT,
            migrated_by="migration-maintainer",
            output_dir=output,
            now=now,
        )

    def read_outputs(self, output: Path) -> dict[str, dict[str, object]]:
        self.assertEqual(OUTPUT_NAMES, {path.name for path in output.iterdir()})
        return {
            name: json.loads((output / name).read_text(encoding="utf-8"))
            for name in OUTPUT_NAMES
        }

    def assert_full_v3_migration_valid(
        self,
        documents: dict[str, dict[str, object]],
        *,
        now: datetime | None = None,
    ) -> None:
        provenance = documents["migration-provenance.json"]
        validated = validate_audience_research_v3(
            documents["audience-research-brief-v3.json"],
            documents["saved-audience-panel-v3.json"],
            frame=provenance["no_defensible_frame_result"],
            composition=documents["panel-composition-plan.json"],
            validity=documents["panel-validity-profile.json"],
            workflow_state=None,
            construction_audit=None,
            now=now,
        )
        self.assertEqual(7, len(validated))
        self.assertIsNone(validated[-2])
        self.assertIsNone(validated[-1])

    def test_approved_package_emits_only_valid_tier_one_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            original = package.read_bytes()
            result = self.migrate(package, root / "v3")
            documents = self.read_outputs(root / "v3")
            brief = documents["audience-research-brief-v3.json"]
            panel = documents["saved-audience-panel-v3.json"]
            composition = documents["panel-composition-plan.json"]
            validity = documents["panel-validity-profile.json"]
            provenance = documents["migration-provenance.json"]

            self.assertEqual("migrated", result["status"])
            self.assertEqual(0o700, (root / "v3").stat().st_mode & 0o777)
            self.assertTrue(all(
                (root / "v3" / filename).stat().st_mode & 0o777 == 0o600
                for filename in OUTPUT_NAMES
            ))
            self.assertEqual(
                [],
                list(root.glob(".audience-v3-migration-*")),
            )
            self.assertEqual(MIGRATION_PROVENANCE_VERSION, provenance["schema_version"])
            self.assertEqual("audience-research-brief-v3", brief["schema_version"])
            self.assertEqual("saved-audience-panel-v3", panel["schema_version"])
            self.assertEqual("2.0.0", panel["version"])
            self.assertEqual("tier_1", brief["panel_tier"])
            self.assertEqual("tier_1", panel["panel_tier"])
            self.assertEqual("public", brief["evidence_basis"])
            self.assertEqual("public", panel["evidence_basis"])
            self.assertEqual("public", composition["evidence_basis"])
            self.assertEqual("public", validity["evidence_basis"])
            self.assertEqual("public", provenance["target"]["evidence_basis"])
            self.assertIsNone(brief["population_frame_sha256"])
            self.assertIsNone(panel["population_frame_sha256"])
            self.assertEqual("unpackaged", panel["package_status"])
            self.assertFalse(provenance["target"]["v3_archive_created"])
            self.assertFalse(any((root / "v3").glob("*.zip")))

            no_frame = provenance["no_defensible_frame_result"]
            self.assertEqual(
                "no_defensible_frame",
                no_frame["eligibility"],
            )
            self.assertEqual(
                composition,
                validate_composition_plan(composition, frame=no_frame),
            )
            self.assertEqual(validity, validate_validity_profile(validity))
            self.assertEqual(panel, validate_saved_panel_v3(panel))
            self.assertEqual(
                {
                    "applicability": "legacy_v2_migration",
                    "status": "not_available",
                    "source_package_sha256": (
                        "sha256:" + hashlib.sha256(original).hexdigest()
                    ),
                    "reason": (
                        "The legacy v2 package contains no Release B1 "
                        "construction audit."
                    ),
                },
                panel["audit_binding"],
            )
            self.assertNotIn("unavailable_bindings", provenance)
            self.assertNotIn("documentary_support_ids", provenance)
            for filename, document in (
                ("audience-research-brief-v3.json", brief),
                ("saved-audience-panel-v3.json", panel),
                ("panel-composition-plan.json", composition),
                ("panel-validity-profile.json", validity),
            ):
                self.assertEqual(
                    "sha256:"
                    + hashlib.sha256(canonical_bytes(document)).hexdigest(),
                    provenance["target"]["document_sha256"][filename],
                )
            self.assertEqual(
                self.approved_brief["evidence_sources"],
                brief["evidence_sources"],
            )
            self.assertEqual(
                self.approved_brief["findings"],
                brief["findings"],
            )
            self.assertTrue(all(
                group["origin"] == "tier_1_evidence"
                and group["structural_finding_ids"]
                and group["evidence_ids"]
                for group in composition["structural_groups"]
            ))
            self.assertTrue(all(
                profile["support_status"] == "supported"
                and profile["support_finding_ids"]
                and profile["support_evidence_ids"]
                for profile in composition["profiles"]
            ))
            self.assert_full_v3_migration_valid(documents)
            self.assertEqual(original, package.read_bytes())

    def test_provisional_package_stays_explicitly_no_research(self) -> None:
        brief, panel = self.provisional_documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(
                root / "v2",
                brief=brief,
                panel=panel,
            )
            self.migrate(
                package,
                root / "v3",
                now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
            )
            documents = self.read_outputs(root / "v3")
            migrated_brief = documents["audience-research-brief-v3.json"]
            migrated_panel = documents["saved-audience-panel-v3.json"]
            composition = documents["panel-composition-plan.json"]
            provenance = documents["migration-provenance.json"]

            self.assertEqual([], migrated_brief["evidence_sources"])
            self.assertEqual([], migrated_brief["findings"])
            self.assertEqual("none", migrated_brief["evidence_basis"])
            self.assertEqual("none", migrated_panel["evidence_basis"])
            self.assertEqual([], migrated_brief["structural_findings"])
            self.assertEqual([], migrated_brief["overlay_findings"])
            self.assertEqual(
                "provisional_no_research",
                migrated_panel["persona_research"]["status"],
            )
            self.assertTrue(all(
                overlay["allocation_basis"] == "experimental"
                and overlay["finding_ids"] == []
                and overlay["evidence_ids"] == []
                and overlay["topic_bindings"] == []
                for overlay in composition["overlay_hypotheses"]
            ))
            self.assertTrue(all(
                group["origin"] == "tier_1_provisional"
                and group["structural_finding_ids"] == []
                and group["evidence_ids"] == []
                for group in composition["structural_groups"]
            ))
            self.assertTrue(all(
                profile["support_status"] == "provisional"
                and profile["support_finding_ids"] == []
                and profile["support_evidence_ids"] == []
                for profile in composition["profiles"]
            ))
            self.assertIn(
                "The v2 package contains no research sources.",
                provenance["limitations"],
            )
            self.assert_full_v3_migration_valid(
                documents,
                now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
            )

    def test_planning_allocations_preserve_weights_and_minimum_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brief = copy.deepcopy(self.large_brief)
            panel = copy.deepcopy(self.large_panel)
            brief["research_mode"] = "public_research"
            panel["persona_research"]["mode"] = "public_research"
            package = self.build_v2(
                root / "v2",
                brief=brief,
                panel=panel,
            )
            self.migrate(package, root / "v3")
            documents = self.read_outputs(root / "v3")
            panel = documents["saved-audience-panel-v3.json"]
            composition = documents["panel-composition-plan.json"]
            provenance = documents["migration-provenance.json"]

            self.assertTrue(all(
                segment["weighting_rule"] == "planning_allocation"
                for segment in panel["segments"]
            ))
            self.assertTrue(all(
                stratum["weighting_rule"] == "planning_allocation"
                for stratum in panel["context_strata"]
            ))
            self.assertEqual(
                {
                    segment["segment_id"]: segment["study_weight"]
                    for segment in panel["segments"]
                },
                {
                    group["structural_group_id"]: group["structural_weight"]
                    for group in composition["structural_groups"]
                },
            )
            self.assertTrue(all(
                group["weight_semantic"] == "planning_allocation"
                and group["origin"] == "tier_1_evidence"
                and group["cell_ids"] == []
                for group in composition["structural_groups"]
            ))
            self.assertEqual(
                len(panel["grounded_context_profiles"]),
                len(composition["profiles"]),
            )
            self.assertEqual(
                {
                    profile["grounded_profile_id"]
                    for profile in panel["grounded_context_profiles"]
                },
                {
                    row["legacy_grounded_profile_id"]
                    for row in provenance["profile_mappings"]
                },
            )
            self.assertTrue(all(
                profile["overlay_weight_semantic"] == "planning_allocation"
                and profile["effective_weight_semantic"] == "planning_allocation"
                and profile["source_cell_ids"] == []
                for profile in composition["profiles"]
            ))

    def test_research_modes_map_to_explicit_evidence_basis(self) -> None:
        cases = (
            ("public_research", "public", "industry_report"),
            ("crm_first_party", "first_party_aggregate", "crm_aggregate"),
            ("hybrid_research", "hybrid", "industry_report"),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, (mode, expected, source_type) in enumerate(cases):
                with self.subTest(mode=mode):
                    brief = copy.deepcopy(self.approved_brief)
                    panel = copy.deepcopy(self.approved_panel)
                    brief["research_mode"] = mode
                    brief["evidence_sources"][0]["type"] = source_type
                    panel["persona_research"]["mode"] = mode
                    panel["persona_research"]["source_types"] = [source_type]
                    package = self.build_v2(
                        root / f"v2-{index}",
                        brief=brief,
                        panel=panel,
                    )
                    output = root / f"v3-{index}"
                    self.migrate(package, output)
                    documents = self.read_outputs(output)
                    self.assertEqual(
                        expected,
                        documents["audience-research-brief-v3.json"][
                            "evidence_basis"
                        ],
                    )
                    self.assertEqual(
                        expected,
                        documents["migration-provenance.json"]["target"][
                            "evidence_basis"
                        ],
                    )
                    self.assertEqual(
                        brief["evidence_sources"],
                        documents["audience-research-brief-v3.json"][
                            "evidence_sources"
                        ],
                    )
                    self.assert_full_v3_migration_valid(documents)

    def test_ambiguous_research_modes_fail_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, mode in enumerate(
                ("use_existing_saved_panel", "user_provided_research")
            ):
                with self.subTest(mode=mode):
                    brief = copy.deepcopy(self.approved_brief)
                    panel = copy.deepcopy(self.approved_panel)
                    brief["research_mode"] = mode
                    panel["persona_research"]["mode"] = mode
                    package = self.build_v2(
                        root / f"v2-{index}",
                        brief=brief,
                        panel=panel,
                    )
                    output = root / f"v3-{index}"
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"cannot infer|ambiguous|{mode}",
                    ):
                        self.migrate(package, output)
                    self.assertFalse(output.exists())

    def test_original_archive_bytes_and_hash_are_bound_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            before = package.read_bytes()
            expected = "sha256:" + hashlib.sha256(before).hexdigest()
            self.assertEqual(
                "sha256:"
                "62b38b8a7f7265c89682627f8f30a3cc"
                "f9ab0fc8142227389de2aaeca5609f5e",
                expected,
            )
            self.migrate(package, root / "v3")
            provenance = self.read_outputs(root / "v3")[
                "migration-provenance.json"
            ]
            self.assertEqual(before, package.read_bytes())
            self.assertEqual(
                expected,
                provenance["source_package"]["package_sha256"],
            )
            self.assertEqual(
                len(before),
                provenance["source_package"]["package_byte_count"],
            )
            self.assertEqual(
                "sha256:"
                "d4bbeb00ccd8ec0c4cf63b5b706f7753"
                "2c00fc362d33b10794d1ba0113c5ee27",
                provenance["source_package"]["brief_sha256"],
            )
            self.assertEqual(
                "sha256:"
                "24993586b2b1d27a17ae1a8584111c32"
                "d066def9afae21b75918914bb56bdb7d",
                provenance["source_package"]["panel_sha256"],
            )
            self.assertTrue(
                provenance["source_package"]["original_bytes_preserved"]
            )

    def test_identical_inputs_and_metadata_emit_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            self.migrate(package, root / "first")
            self.migrate(package, root / "second")
            for name in OUTPUT_NAMES:
                self.assertEqual(
                    (root / "first" / name).read_bytes(),
                    (root / "second" / name).read_bytes(),
                    name,
                )

    def test_corrupt_archive_and_same_or_lower_version_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(b"not a zip")
            with self.assertRaisesRegex(ValueError, "archive|ZIP|package"):
                self.migrate(corrupt, root / "corrupt-output")
            self.assertFalse((root / "corrupt-output").exists())

            package = self.build_v2(root / "v2")
            for version in (
                "1.0.0",
                "0.9.0",
                "2.0",
                "v2.0.0",
                "02.0.0",
                "2.00.0",
                "2.0.00",
                "01.0.1",
            ):
                with self.subTest(version=version):
                    output = root / ("output-" + version.replace(".", "-"))
                    with self.assertRaisesRegex(
                        ValueError,
                        "semantic|newer|version",
                    ):
                        self.migrate(package, output, version=version)
                    self.assertFalse(output.exists())

    def test_existing_output_is_not_clobbered_and_source_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            source_before = package.read_bytes()
            output = root / "v3"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.migrate(package, output)
            self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(source_before, package.read_bytes())
            self.assertEqual({"keep.txt"}, {path.name for path in output.iterdir()})

    def test_symlink_ancestor_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            output = linked_parent / "v3"
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.migrate(package, output)
            self.assertEqual([], list(real_parent.iterdir()))

    def test_attacker_controlled_tmpdir_cannot_allowlist_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            real_temp = root / "real-temp"
            configured_temp = real_temp / "configured"
            configured_temp.mkdir(parents=True)
            attacker_alias = root / "attacker-temp-alias"
            attacker_alias.symlink_to(real_temp, target_is_directory=True)
            attacker_tmpdir = attacker_alias / "configured"
            output = attacker_tmpdir / "v3"

            with mock.patch.object(
                tempfile,
                "gettempdir",
                return_value=str(attacker_tmpdir),
            ):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.migrate(package, output)
            self.assertFalse((configured_temp / "v3").exists())

    def test_destination_race_cannot_replace_independent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            output = root / "v3"
            sentinel = output / "independent.txt"
            original_mkdir = os.mkdir
            raced = False

            def racing_mkdir(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal raced
                candidate = Path(path)
                output_claim = candidate == output or (
                    dir_fd is not None and candidate == Path(output.name)
                )
                if output_claim and not raced:
                    raced = True
                    original_mkdir(path, mode, dir_fd=dir_fd)
                    sentinel.write_text("independent", encoding="utf-8")
                original_mkdir(path, mode, dir_fd=dir_fd)

            with mock.patch.object(
                migration_module.os,
                "mkdir",
                side_effect=racing_mkdir,
            ):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.migrate(package, output)
            self.assertTrue(raced)
            self.assertEqual(
                "independent",
                sentinel.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                {"independent.txt"},
                {path.name for path in output.iterdir()},
            )

    def test_post_claim_symlink_swap_cannot_redirect_any_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            output = root / "v3"
            displaced = root / "displaced-claim"
            attacker = root / "attacker-target"
            attacker.mkdir()
            original_rename = os.rename
            original_replace = os.replace
            swapped = False

            def swap_claim() -> None:
                nonlocal swapped
                if swapped:
                    return
                swapped = True
                original_rename(output, displaced)
                output.symlink_to(attacker, target_is_directory=True)

            def swapping_rename(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                swap_claim()
                original_rename(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            def swapping_replace(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                swap_claim()
                original_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with (
                mock.patch.object(
                    migration_module.os,
                    "rename",
                    side_effect=swapping_rename,
                ),
                mock.patch.object(
                    migration_module.os,
                    "replace",
                    side_effect=swapping_replace,
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "changed|replaced|publication",
                ):
                    self.migrate(package, output)
            self.assertTrue(swapped)
            self.assertTrue(output.is_symlink())
            self.assertEqual(attacker.resolve(), output.resolve())
            self.assertEqual([], list(attacker.iterdir()))
            self.assertTrue(displaced.is_dir())
            self.assertEqual([], list(displaced.iterdir()))

    def test_failed_publish_does_not_delete_independent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            output = root / "v3"
            displaced = root / "displaced-claim"
            sentinel = output / "independent.txt"
            original_rename = os.rename
            swapped = False

            def replace_claim_and_fail() -> None:
                nonlocal swapped
                if swapped:
                    return
                swapped = True
                original_rename(output, displaced)
                output.mkdir()
                sentinel.write_text("independent", encoding="utf-8")
                raise OSError("forced post-claim publication failure")

            def failing_transfer(
                _source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                _destination:
                    str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                del src_dir_fd, dst_dir_fd
                replace_claim_and_fail()

            with (
                mock.patch.object(
                    migration_module.os,
                    "rename",
                    side_effect=failing_transfer,
                ),
                mock.patch.object(
                    migration_module.os,
                    "replace",
                    side_effect=failing_transfer,
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "forced post-claim publication failure",
                ):
                    self.migrate(package, output)
            self.assertTrue(swapped)
            self.assertTrue(output.is_dir())
            self.assertEqual(
                "independent",
                sentinel.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                {"independent.txt"},
                {path.name for path in output.iterdir()},
            )
            self.assertTrue(displaced.is_dir())
            self.assertEqual([], list(displaced.iterdir()))

    def test_partial_publication_failure_removes_only_claimed_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            output = root / "v3"
            original_rename = os.rename
            original_replace = os.replace
            transfers = 0

            def transfer_or_fail(
                operation: Callable[..., None],
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                nonlocal transfers
                transfers += 1
                if transfers == 2:
                    raise OSError("simulated publication failure")
                operation(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with (
                mock.patch.object(
                    migration_module.os,
                    "rename",
                    side_effect=lambda source, destination, **kwargs:
                        transfer_or_fail(
                            original_rename,
                            source,
                            destination,
                            **kwargs,
                        ),
                ),
                mock.patch.object(
                    migration_module.os,
                    "replace",
                    side_effect=lambda source, destination, **kwargs:
                        transfer_or_fail(
                            original_replace,
                            source,
                            destination,
                            **kwargs,
                        ),
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "simulated publication failure",
                ):
                    self.migrate(package, output)
            self.assertFalse(output.exists())
            self.assertEqual(
                [],
                list(root.glob(".audience-v3-migration-*")),
            )

    def test_migration_uses_only_public_archive_and_brief_boundaries(self) -> None:
        source = (
            PANEL_BUILDER_SCRIPTS
            / "audience_panel_builder"
            / "population"
            / "migration.py"
        ).read_text(encoding="utf-8")
        for private_name in (
            "_archive_bytes",
            "_safe_read_package_archive",
            "_validate_v3_brief",
        ):
            self.assertNotIn(private_name, source)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            with (
                mock.patch.object(
                    migration_module,
                    "read_validated_package_archive",
                    wraps=migration_module.read_validated_package_archive,
                ) as archive_reader,
                mock.patch.object(
                    migration_module,
                    "validate_research_brief_v3",
                    wraps=validate_research_brief_v3,
                ) as brief_validator,
            ):
                self.migrate(package, root / "v3")
            archive_reader.assert_called_once_with(package, now=None)
            brief_validator.assert_called_once()

    def test_migration_does_not_invent_population_or_outcome_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            self.migrate(package, root / "v3")
            documents = self.read_outputs(root / "v3")
            brief = documents["audience-research-brief-v3.json"]
            panel = documents["saved-audience-panel-v3.json"]
            composition = documents["panel-composition-plan.json"]
            validity = documents["panel-validity-profile.json"]
            no_frame = documents["migration-provenance.json"][
                "no_defensible_frame_result"
            ]

            self.assertEqual(
                self.approved_brief["evidence_sources"],
                brief["evidence_sources"],
            )
            self.assertEqual(
                self.approved_brief["findings"],
                brief["findings"],
            )
            self.assertEqual(
                self.approved_panel["calibration_history"],
                panel["calibration_history"],
            )
            self.assertEqual([], no_frame["cells"])
            self.assertEqual([], no_frame["source_bindings"])
            self.assertEqual([], no_frame["modeled_weight_by_dimension"])
            self.assertEqual(0.0, no_frame["modeled_weight_share"])
            self.assertEqual(0.0, composition["modeled_cell_share"])
            self.assertEqual(
                "not_available",
                validity["axes"]["outcome_calibration"]["status"],
            )
            self.assertEqual(
                "not_available",
                validity["axes"]["external_validation"]["status"],
            )
            self.assertIsNone(validity["predeclared_validation_design"])
            self.assertEqual([], validity["held_out_outcome_evidence"])

    def test_cli_writes_five_documents_and_reports_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.build_v2(root / "v2")
            output = root / "v3"
            command = [
                sys.executable,
                str(CLI),
                "--v2-package",
                str(package),
                "--new-panel-version",
                "2.0.0",
                "--migrated-at",
                MIGRATED_AT,
                "--migrated-by",
                "migration-maintainer",
                "--output-dir",
                str(output),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual("migrated", payload["status"])
            self.read_outputs(output)

            collision = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(3, collision.returncode)
            self.assertEqual("output_collision", json.loads(collision.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
