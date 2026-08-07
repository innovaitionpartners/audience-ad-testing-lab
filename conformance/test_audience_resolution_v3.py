from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURE = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-package-v3"
    / "approved-package-inputs.json"
)
CLI = SCRIPTS / "manage-audience-library.py"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_library import (  # noqa: E402
    ImmutableVersionConflict,
    list_panels,
    register_package,
    show_panel,
)
from audience_lab.audience_package import (  # noqa: E402
    PackageSafetyError,
    PackageValidationError,
)
from audience_lab.audience_package_v3 import (  # noqa: E402
    ARCHIVE_FILES_V3,
    build_audience_package_v3,
)
from audience_lab import audience_package_v3  # noqa: E402
from audience_lab import audience_resolution_v3  # noqa: E402
from audience_lab.audience_resolution_v3 import (  # noqa: E402
    RUN_ENVELOPE_VERSION,
    resolve_audience_v3,
)
from audience_lab.planning import (  # noqa: E402
    load_reusable_v3_audience_resolution,
)


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


class AudienceResolutionV3Test(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        panel_scope = self.fixture["bundles"]["tier_2"]["panel"]["audience_scope"]
        self.scope = {
            key: copy.deepcopy(panel_scope[key])
            for key in (
                "audience",
                "market",
                "geography",
                "category",
                "buying_context",
                "exclusions",
            )
        }

    def _materialize(
        self,
        root: Path,
        *,
        bundle: str = "tier_2",
    ) -> dict[str, Path]:
        source = self.fixture["bundles"][bundle]
        root.mkdir(parents=True)
        paths: dict[str, Path] = {}
        for key, filename in self.fixture["inputs"].items():
            path = root / filename
            value = source[key]
            path.write_bytes(
                value.encode("utf-8")
                if key == "report"
                else canonical_bytes(value)
            )
            paths[key] = path
        return paths

    def _build(self, root: Path, *, bundle: str = "tier_2"):
        return build_audience_package_v3(
            inputs=self._materialize(root / "inputs", bundle=bundle),
            output_dir=root / "output",
        )

    def _mutated_package(
        self,
        root: Path,
        mutate,
        *,
        bundle: str = "tier_2",
    ) -> Path:
        built = self._build(root / "source", bundle=bundle)
        with zipfile.ZipFile(built.package_zip_path) as archive:
            members = {
                name: archive.read(name)
                for name in archive.namelist()
            }
        mutate(members)
        package = root / "mutated.zip"
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(audience_package_v3._zip_bytes(members))
        return package

    def test_approved_tier_one_registers_and_resolves_directionally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package", bundle="tier_1")
            registered = register_package(
                package.package_zip_path,
                library_root=base / "library",
            )
            self.assertEqual("registered", registered["status"])
            self.assertEqual(
                "operations-leaders",
                show_panel(
                    "operations-leaders",
                    "1.0.0",
                    library_root=base / "library",
                )["panel"]["panel_id"],
            )

            result = resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=base / "run",
            )
            self.assertEqual(RUN_ENVELOPE_VERSION, result["schema_version"])
            self.assertEqual("ready", result["resolution_status"])
            self.assertEqual("directional_planning", result["allocation_basis"])
            self.assertEqual("tier_1", result["audience_package"]["tier"])

    def test_resolution_accepts_only_exact_canonical_or_scoped_ad_testing_uses(
        self,
    ) -> None:
        panel = copy.deepcopy(self.fixture["bundles"]["tier_2"]["panel"])
        exact_allowed_uses = (
            "Synthetic ad testing",
            (
                "Directional synthetic ad testing under the named public "
                "proxy boundary"
            ),
            (
                "Synthetic ad testing for the exact authorized aggregate "
                "cohort"
            ),
        )
        rejected_near_matches = (
            "synthetic ad testing",
            "Synthetic ad testing ",
            "Directional synthetic ad testing",
            "Synthetic ad testing for an authorized aggregate cohort",
            "Synthetic ad testing for the exact authorized aggregate cohort.",
        )
        for allowed_use in exact_allowed_uses:
            with self.subTest(allowed_use=allowed_use):
                panel["governance"]["allowed_uses"] = [allowed_use]
                status, reasons = audience_resolution_v3._scope_resolution(
                    panel,
                    self.scope,
                    now=audience_resolution_v3.datetime(
                        2026,
                        7,
                        25,
                        tzinfo=audience_resolution_v3.timezone.utc,
                    ),
                    explicit_refresh_triggers=[],
                )
                self.assertEqual("ready", status)
                self.assertFalse(
                    any(
                        reason["code"] == "permission_incompatible"
                        for reason in reasons
                    )
                )
        for allowed_use in rejected_near_matches:
            with self.subTest(allowed_use=allowed_use):
                panel["governance"]["allowed_uses"] = [allowed_use]
                status, reasons = audience_resolution_v3._scope_resolution(
                    panel,
                    self.scope,
                    now=audience_resolution_v3.datetime(
                        2026,
                        7,
                        25,
                        tzinfo=audience_resolution_v3.timezone.utc,
                    ),
                    explicit_refresh_triggers=[],
                )
                self.assertEqual("incompatible", status)
                self.assertEqual(
                    ["permission_incompatible"],
                    [
                        reason["code"]
                        for reason in reasons
                        if reason["field"] == "governance.allowed_uses"
                    ],
                )

    def test_structural_package_resolution_has_exact_current_envelope_and_bindings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            result = resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=base / "run",
            )
            self.assertEqual(
                {
                    "schema_version",
                    "resolved_at",
                    "resolution_status",
                    "resolution_reasons",
                    "audience_package",
                    "audience_lock",
                    "context_strata",
                    "grounded_context_profiles",
                    "profile_weights",
                    "allocation_constraints",
                    "allocation_basis",
                    "claim_boundary",
                    "snapshot",
                },
                set(result),
            )
            self.assertEqual("ready", result["resolution_status"])
            self.assertEqual([], result["resolution_reasons"])
            self.assertEqual("structural_frame", result["allocation_basis"])
            self.assertEqual(
                {
                    "schema_version",
                    "generator_version",
                    "package_manifest_sha256",
                    "package_zip_sha256",
                    "panel_id",
                    "panel_version",
                    "tier",
                    "evidence_basis",
                },
                set(result["audience_package"]),
            )
            self.assertEqual(
                self.fixture["bundles"]["tier_2"]["panel"]["claim_boundary"],
                result["claim_boundary"],
            )
            self.assertEqual(
                self.fixture["bundles"]["tier_2"]["composition"][
                    "allocation_constraints"
                ],
                result["allocation_constraints"],
            )

            expected_weights = [
                {
                    "grounded_profile_id": "enterprise-risk-averse",
                    "reported_segment_id": "operations-leaders",
                    "structural_group_id": "enterprise-group",
                    "structural_weight": 0.3,
                    "conditional_overlay_allocation": 1.0,
                    "effective_weight": 0.3,
                    "weight_semantics": "experimental_modeled_weight",
                    "must_cover_group_ids": ["enterprise-group"],
                },
                {
                    "grounded_profile_id": "midmarket-proof-seeking",
                    "reported_segment_id": "operations-leaders",
                    "structural_group_id": "midmarket-group",
                    "structural_weight": 0.7,
                    "conditional_overlay_allocation": 0.75,
                    "effective_weight": 0.525,
                    "weight_semantics": "population_weight",
                    "must_cover_group_ids": ["midmarket-group"],
                },
                {
                    "grounded_profile_id": "midmarket-risk-averse",
                    "reported_segment_id": "operations-leaders",
                    "structural_group_id": "midmarket-group",
                    "structural_weight": 0.7,
                    "conditional_overlay_allocation": 0.25,
                    "effective_weight": 0.175,
                    "weight_semantics": "population_weight",
                    "must_cover_group_ids": ["midmarket-group"],
                },
            ]
            self.assertEqual(expected_weights, result["profile_weights"])

            source_profiles = {
                item["grounded_profile_id"]: item
                for item in self.fixture["bundles"]["tier_2"]["panel"][
                    "grounded_context_profiles"
                ]
            }
            source_composition = {
                item["profile_id"]: item
                for item in self.fixture["bundles"]["tier_2"]["composition"][
                    "profiles"
                ]
            }
            for joined in result["grounded_context_profiles"]:
                profile_id = joined["grounded_profile_id"]
                saved = source_profiles[profile_id]
                composition = source_composition[profile_id]
                self.assertEqual(saved["segment_id"], joined["segment_id"])
                self.assertEqual(
                    saved["context_stratum_id"],
                    joined["context_stratum_id"],
                )
                self.assertEqual(
                    composition["structural_group_id"],
                    joined["structural_group_id"],
                )
                self.assertEqual(composition["overlay_ids"], joined["overlay_ids"])
                self.assertEqual(
                    composition["effective_profile_allocation"],
                    joined["effective_weight"],
                )
                self.assertEqual(
                    composition["effective_weight_semantic"],
                    joined["weight_semantics"],
                )
                self.assertEqual(
                    "sha256:"
                    + hashlib.sha256(
                        canonical_bytes(saved["profile_snapshot"])
                    ).hexdigest(),
                    joined["profile_snapshot_sha256"],
                )
                self.assertTrue(joined["eligible"])

    def test_snapshot_hashes_every_member_and_identical_replay_is_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            run = base / "run"
            first = resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=run,
                now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
            )
            second = resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=run,
                now=datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(first, second)
            self.assertEqual("2026-07-25T12:00:00Z", first["resolved_at"])
            self.assertEqual(
                {
                    "relative_path",
                    "package_sha256",
                    "manifest_sha256",
                    "members",
                },
                set(first["snapshot"]),
            )
            self.assertEqual("audience/snapshot", first["snapshot"]["relative_path"])
            snapshot = run / "audience" / "snapshot"
            records = first["snapshot"]["members"]
            self.assertEqual(sorted(ARCHIVE_FILES_V3), [item["path"] for item in records])
            for record in records:
                payload = (snapshot / record["path"]).read_bytes()
                self.assertEqual(len(payload), record["byte_count"])
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(),
                    record["sha256"],
                )
            self.assertEqual(
                package.package_zip_path.read_bytes(),
                (snapshot / "audience-panel-package.zip").read_bytes(),
            )

            (snapshot / "README.txt").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex((ValueError, PackageSafetyError), "snapshot"):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=run,
                )

    def test_resolution_timestamp_cannot_self_authenticate_after_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            run = base / "run"
            resolution_path = run / "audience" / "resolution.json"
            resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=run,
                now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
            )
            mutated = json.loads(resolution_path.read_text(encoding="utf-8"))
            mutated["resolved_at"] = "2026-07-26T12:00:00Z"
            resolution_path.write_bytes(canonical_bytes(mutated))

            with self.assertRaisesRegex(
                ValueError,
                "resolution.*(authority|timestamp)|immutable",
            ):
                load_reusable_v3_audience_resolution(resolution_path)

    def test_scope_statuses_are_ready_needs_refresh_and_incompatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            cases = (
                ("market", "Different market", "needs_refresh", "market_mismatch"),
                (
                    "geography",
                    "Canada",
                    "incompatible",
                    "geography_mismatch",
                ),
                (
                    "buying_context",
                    "Renewal decision",
                    "incompatible",
                    "buying_context_mismatch",
                ),
                (
                    "audience",
                    "Different decision makers",
                    "incompatible",
                    "audience_mismatch",
                ),
            )
            for field, changed, status, reason_code in cases:
                with self.subTest(field=field):
                    scope = copy.deepcopy(self.scope)
                    scope[field] = changed
                    result = resolve_audience_v3(
                        package_path=package.package_zip_path,
                        study_scope=scope,
                        run_directory=base / f"run-{field}",
                    )
                    self.assertEqual(status, result["resolution_status"])
                    self.assertIn(
                        reason_code,
                        {item["code"] for item in result["resolution_reasons"]},
                    )
                    self.assertFalse(
                        (base / f"run-{field}" / "audience" / "snapshot").exists()
                    )

    def test_cli_v3_explicit_refresh_trigger_returns_needs_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            intake = base / "intake.json"
            scope = base / "scope.json"
            intake.write_bytes(
                canonical_bytes(
                    {
                        "audience_panel": {
                            "source": "file",
                            "package_path": str(package.package_zip_path),
                        }
                    }
                )
            )
            scope.write_bytes(canonical_bytes(self.scope))
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "resolve",
                    str(intake),
                    str(scope),
                    str(base / "run"),
                    "--refresh-trigger",
                    " New first-party evidence ",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(5, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("needs_refresh", payload["resolution_status"])
            self.assertIn(
                "refresh_trigger_present",
                {item["code"] for item in payload["resolution_reasons"]},
            )
            self.assertFalse((base / "run" / "audience" / "snapshot").exists())

    def test_invalid_profile_identity_packages_resolve_incompatible(self) -> None:
        def mutate_duplicate(members: dict[str, bytes]) -> None:
            composition = json.loads(members["panel-composition-plan.json"])
            composition["profiles"][1]["profile_id"] = (
                composition["profiles"][0]["profile_id"]
            )
            members["panel-composition-plan.json"] = canonical_bytes(composition)

        def mutate_missing(members: dict[str, bytes]) -> None:
            composition = json.loads(members["panel-composition-plan.json"])
            composition["profiles"].pop()
            members["panel-composition-plan.json"] = canonical_bytes(composition)

        def mutate_extra(members: dict[str, bytes]) -> None:
            panel = json.loads(members["saved-audience-panel.json"])
            panel["grounded_context_profiles"].pop()
            members["saved-audience-panel.json"] = canonical_bytes(panel)

        cases = (
            ("duplicate", mutate_duplicate, "duplicate_profile_id"),
            ("missing", mutate_missing, "missing_profile_id"),
            ("extra", mutate_extra, "extra_profile_id"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, mutation, code in cases:
                with self.subTest(case=name):
                    package = self._mutated_package(
                        base / name,
                        mutation,
                    )
                    result = resolve_audience_v3(
                        package_path=package,
                        study_scope=self.scope,
                        run_directory=base / f"run-{name}",
                    )
                    self.assertEqual(
                        {
                            "schema_version",
                            "resolved_at",
                            "resolution_status",
                            "resolution_reasons",
                            "audience_package",
                            "audience_lock",
                            "context_strata",
                            "grounded_context_profiles",
                            "profile_weights",
                            "allocation_constraints",
                            "allocation_basis",
                            "claim_boundary",
                            "snapshot",
                        },
                        set(result),
                    )
                    self.assertEqual("incompatible", result["resolution_status"])
                    self.assertIn(
                        code,
                        {item["code"] for item in result["resolution_reasons"]},
                    )
                    self.assertEqual([], result["grounded_context_profiles"])
                    self.assertEqual([], result["profile_weights"])
                    self.assertFalse(
                        (base / f"run-{name}" / "audience" / "snapshot").exists()
                    )

    def test_non_string_profile_identities_resolve_canonical_incompatible(
        self,
    ) -> None:
        malformed_values = (
            ("dict", {"bad": "identity"}),
            ("list", ["bad", "identity"]),
            ("null", None),
            ("number", 7),
            ("empty", ""),
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for position in ("composition", "saved_panel"):
                for label, malformed in malformed_values:
                    with self.subTest(position=position, malformed=label):
                        def mutate(
                            members: dict[str, bytes],
                            *,
                            selected_position=position,
                            selected_value=malformed,
                        ) -> None:
                            if selected_position == "composition":
                                composition = json.loads(
                                    members["panel-composition-plan.json"]
                                )
                                composition["profiles"][0]["profile_id"] = (
                                    selected_value
                                )
                                members["panel-composition-plan.json"] = (
                                    canonical_bytes(composition)
                                )
                            else:
                                panel = json.loads(
                                    members["saved-audience-panel.json"]
                                )
                                panel["grounded_context_profiles"][0][
                                    "grounded_profile_id"
                                ] = selected_value
                                members["saved-audience-panel.json"] = (
                                    canonical_bytes(panel)
                                )

                        package = self._mutated_package(
                            base / f"{position}-{label}",
                            mutate,
                        )
                        result = resolve_audience_v3(
                            package_path=package,
                            study_scope=self.scope,
                            run_directory=base / f"run-{position}-{label}",
                        )
                        self.assertEqual(
                            "incompatible",
                            result["resolution_status"],
                        )
                        self.assertEqual(
                            ["invalid_profile_identity"],
                            [
                                item["code"]
                                for item in result["resolution_reasons"]
                            ],
                        )
                        self.assertEqual(
                            [],
                            result["grounded_context_profiles"],
                        )
                        self.assertEqual([], result["profile_weights"])
                        self.assertFalse(
                            (
                                base
                                / f"run-{position}-{label}"
                                / "audience"
                                / "snapshot"
                            ).exists()
                        )

    def test_unsupported_package_version_resolves_incompatible(self) -> None:
        def mutate(members: dict[str, bytes]) -> None:
            manifest = json.loads(members["package-manifest.json"])
            manifest["generator_version"] = "9.0.0"
            members["package-manifest.json"] = canonical_bytes(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._mutated_package(base / "package", mutate)
            result = resolve_audience_v3(
                package_path=package,
                study_scope=self.scope,
                run_directory=base / "run",
            )
            self.assertEqual("incompatible", result["resolution_status"])
            self.assertIn(
                "unsupported_package",
                {item["code"] for item in result["resolution_reasons"]},
            )
            self.assertEqual("9.0.0", result["audience_package"]["generator_version"])
            self.assertFalse((base / "run" / "audience" / "snapshot").exists())

    def test_safely_inspectable_unsupported_package_shape_resolves_incompatible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = base / "incomplete.zip"
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr(
                    "package-manifest.json",
                    canonical_bytes(
                        {
                            "schema_version": "audience-panel-package-v3",
                            "generator_version": "2.0.0",
                        }
                    ),
                )
            package.write_bytes(raw.getvalue())
            result = resolve_audience_v3(
                package_path=package,
                study_scope=self.scope,
                run_directory=base / "run",
            )
            self.assertEqual("incompatible", result["resolution_status"])
            self.assertIn(
                "invalid_package",
                {item["code"] for item in result["resolution_reasons"]},
            )
            self.assertEqual(
                ["package-manifest.json"],
                [item["path"] for item in result["snapshot"]["members"]],
            )
            self.assertFalse((base / "run" / "audience" / "snapshot").exists())

    def test_caller_cannot_choose_allocation_basis_and_paths_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            with self.assertRaises(TypeError):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=base / "run",
                    allocation_basis="directional_planning",
                )

            outside = base / "outside"
            outside.mkdir()
            linked = base / "linked-run"
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex((ValueError, PackageSafetyError), "symlink"):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=linked,
                )
            self.assertEqual([], list(outside.iterdir()))

    def test_v3_registration_replays_identically_and_conflicts_immutably(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            structural = self._build(base / "structural", bundle="tier_2")
            directional = self._build(base / "directional", bundle="tier_1")
            library = base / "library"
            first = register_package(
                structural.package_zip_path,
                library_root=library,
            )
            index_before = (library / "index.json").read_bytes()
            stored_before = (
                library
                / first["panel"]["relative_path"]
            ).read_bytes()
            replay = register_package(
                structural.package_zip_path,
                library_root=library,
            )
            self.assertEqual("already_registered", replay["status"])
            self.assertEqual(index_before, (library / "index.json").read_bytes())
            self.assertEqual(
                stored_before,
                (library / first["panel"]["relative_path"]).read_bytes(),
            )
            with self.assertRaises(ImmutableVersionConflict):
                register_package(
                    directional.package_zip_path,
                    library_root=library,
                )
            self.assertEqual(index_before, (library / "index.json").read_bytes())
            self.assertEqual(1, len(list_panels(library_root=library)["panels"]))

    def test_unknown_and_provisional_package_versions_do_not_register(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            unknown = base / "unknown.zip"
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr(
                    "package-manifest.json",
                    canonical_bytes(
                        {
                            "schema_version": "audience-panel-package-v3",
                            "generator_version": "9.0.0",
                        }
                    ),
                )
            unknown.write_bytes(raw.getvalue())
            with self.assertRaises((PackageSafetyError, PackageValidationError)):
                register_package(unknown, library_root=base / "library")

            def mutate_provisional(members: dict[str, bytes]) -> None:
                workflow = json.loads(members["panel-workflow-state.json"])
                workflow["state"] = "provisional"
                members["panel-workflow-state.json"] = canonical_bytes(workflow)

            provisional = self._mutated_package(
                base / "provisional",
                mutate_provisional,
            )
            with self.assertRaises(PackageValidationError):
                register_package(
                    provisional,
                    library_root=base / "library",
                )
            self.assertFalse((base / "library" / "index.json").exists())

    def test_run_snapshot_and_envelope_are_one_immutable_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")

            tampered_run = base / "tampered-run"
            resolve_audience_v3(
                package_path=package.package_zip_path,
                study_scope=self.scope,
                run_directory=tampered_run,
            )
            resolution_path = tampered_run / "audience" / "resolution.json"
            resolution_path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                ImmutableVersionConflict, "resolution|envelope|run"
            ):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=tampered_run,
                )

            resolution_only = base / "resolution-only"
            (resolution_only / "audience").mkdir(parents=True)
            (resolution_only / "audience" / "resolution.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                ImmutableVersionConflict, "partial|resolution|run"
            ):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=resolution_only,
                )
            self.assertFalse(
                (resolution_only / "audience" / "snapshot").exists()
            )

            snapshot_only = base / "snapshot-only"
            (snapshot_only / "audience" / "snapshot").mkdir(parents=True)
            with self.assertRaisesRegex(
                ImmutableVersionConflict, "partial|snapshot|run"
            ):
                resolve_audience_v3(
                    package_path=package.package_zip_path,
                    study_scope=self.scope,
                    run_directory=snapshot_only,
                )
            self.assertFalse(
                (snapshot_only / "audience" / "resolution.json").exists()
            )

    def test_existing_ready_run_rejects_changed_resolution_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            for field, value in (
                ("market", "Different market"),
                ("geography", "Canada"),
            ):
                with self.subTest(field=field):
                    run = base / f"run-{field}"
                    ready = resolve_audience_v3(
                        package_path=package.package_zip_path,
                        study_scope=self.scope,
                        run_directory=run,
                    )
                    resolution_before = (
                        run / "audience" / "resolution.json"
                    ).read_bytes()
                    snapshot_before = {
                        path.name: path.read_bytes()
                        for path in (run / "audience" / "snapshot").iterdir()
                    }
                    changed = copy.deepcopy(self.scope)
                    changed[field] = value
                    with self.assertRaisesRegex(
                        ImmutableVersionConflict, "resolution|envelope|run"
                    ):
                        resolve_audience_v3(
                            package_path=package.package_zip_path,
                            study_scope=changed,
                            run_directory=run,
                        )
                    self.assertEqual("ready", ready["resolution_status"])
                    self.assertEqual(
                        resolution_before,
                        (run / "audience" / "resolution.json").read_bytes(),
                    )
                    self.assertEqual(
                        snapshot_before,
                        {
                            path.name: path.read_bytes()
                            for path in (run / "audience" / "snapshot").iterdir()
                        },
                    )

    def test_cli_dispatches_v3_file_and_library_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = self._build(base / "package")
            library = base / "library"
            register_package(package.package_zip_path, library_root=library)
            scope = base / "scope.json"
            scope.write_bytes(canonical_bytes(self.scope))
            for name, intake_value in (
                (
                    "file",
                    {
                        "audience_panel": {
                            "source": "file",
                            "package_path": str(package.package_zip_path),
                        }
                    },
                ),
                (
                    "library",
                    {
                        "audience_panel": {
                            "source": "library",
                            "panel_id": "operations-leaders",
                            "version": "1.0.0",
                        }
                    },
                ),
            ):
                intake = base / f"{name}-intake.json"
                intake.write_bytes(canonical_bytes(intake_value))
                run = base / f"{name}-run"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(CLI),
                        "resolve",
                        str(intake),
                        str(scope),
                        str(run),
                    ],
                    env={
                        **dict(__import__("os").environ),
                        "AUDIENCE_LAB_LIBRARY_DIR": str(library),
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(RUN_ENVELOPE_VERSION, payload["schema_version"])
                self.assertEqual("ready", payload["resolution_status"])
                self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()
