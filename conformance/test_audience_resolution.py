from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"
CLI = SCRIPTS / "manage-audience-library.py"
PREPARE_JOBS_CLI = SCRIPTS / "prepare-panel-jobs.py"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_library import (  # noqa: E402
    AudienceResolutionBlocked,
    audience_package_binding,
    load_audience_resolution,
    materialize_provisional_audience,
    register_package,
    resolve_audience_panel,
    validate_audience_intake,
    verify_file_package_binding,
)
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.dispatch import enrich_assignment_jobs  # noqa: E402


class AudienceResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = json.loads((FIXTURES / "approved-brief.json").read_text())
        self.panel = json.loads((FIXTURES / "approved-panel.json").read_text())
        scope = self.panel["audience_scope"]
        self.scope = {
            key: copy.deepcopy(scope[key])
            for key in ("audience", "market", "geography", "category", "buying_context", "exclusions")
        }

    def _package(self, root: Path, *, brief=None, panel=None):
        return build_audience_package(
            self.brief if brief is None else brief,
            self.panel if panel is None else panel,
            root,
        )

    def test_intake_routes_are_exact_and_mutually_exclusive(self) -> None:
        research = {
            "target_audience": {
                **self.scope,
                "research_mode": "public_research",
                "research_depth": "standard",
                "supplied_research_paths": [],
            }
        }
        library = {
            "audience_panel": {
                "source": "library",
                "panel_id": "operations-leaders",
                "version": "1.0.0",
            }
        }
        package = {
            "audience_panel": {"source": "file", "package_path": "/tmp/panel.zip"}
        }
        provisional = {
            "provisional_audience": {
                "scope": self.scope,
                "user_defined_segments": [{
                    "segment_id": "operations-leaders",
                    "name": "Operations leaders",
                    "description": "A user-defined planning segment.",
                }],
                "accepted_by": "owner",
                "accepted_at": "2026-07-22T12:00:00Z",
                "expires_at": "2026-08-10T12:00:00Z",
            }
        }
        for value in (research, library, package, provisional):
            with self.subTest(value=next(iter(value))):
                self.assertEqual(next(iter(value)), validate_audience_intake(value)["route"])

        for bad in (
            {},
            {**research, **library},
            {"audience_panel": {**library["audience_panel"], "extra": True}},
            {"audience_panel": {"source": "file", "package_path": "/tmp/x.zip", "panel_id": "x"}},
            {"provisional_audience": {**provisional["provisional_audience"], "extra": True}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "audience|keys|route|allowlist"):
                    validate_audience_intake(bad)

    def test_library_and_file_routes_create_identical_immutable_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            library = base / "library"
            register_package(package.package_zip_path, library_root=library)

            library_result = resolve_audience_panel(
                {"source": "library", "panel_id": "operations-leaders", "version": "1.0.0"},
                self.scope,
                run_dir=base / "run-library",
                library_root=library,
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            file_result = resolve_audience_panel(
                {"source": "file", "package_path": str(package.package_zip_path)},
                self.scope,
                run_dir=base / "run-file",
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )

            self.assertEqual("ready", library_result["status"])
            self.assertEqual(library_result, file_result)
            self.assertEqual(
                self.panel["context_strata"],
                library_result["context_strata"],
            )
            self.assertEqual(
                self.panel["grounded_context_profiles"],
                library_result["grounded_context_profiles"],
            )
            self.assertEqual(
                {
                    "schema_version", "status", "reasons", "panel_id", "panel_version",
                    "snapshot_dir", "audience_lock", "context_strata",
                    "grounded_context_profiles", "hashes",
                },
                set(library_result),
            )
            self.assertEqual("audience/snapshot", library_result["snapshot_dir"])
            self.assertEqual([], library_result["reasons"])
            self.assertEqual(
                {
                    "persona_research_brief_id", "panel_id", "panel_version",
                    "segment_weights", "segment_names", "archetype_names",
                    "segment_weight_provenance", "unique_archetypes",
                    "unique_grounded_context_profiles", "attribute_provenance",
                },
                set(library_result["audience_lock"]),
            )
            left = base / "run-library" / "audience" / "snapshot"
            right = base / "run-file" / "audience" / "snapshot"
            self.assertEqual(
                {p.name: p.read_bytes() for p in left.iterdir()},
                {p.name: p.read_bytes() for p in right.iterdir()},
            )
            self.assertTrue((base / "run-library" / "audience" / "resolution.json").is_file())

            binding = audience_package_binding(base / "run-library", library_result)
            self.assertEqual("audience/snapshot", binding["resolved_snapshot_path"])
            self.assertEqual(12, len(binding))
            self.assertEqual(
                (left / "saved-audience-panel.json").stat().st_size,
                binding["panel_byte_count"],
            )

            # Re-resolution is idempotent, but changed bytes cannot replace a snapshot.
            self.assertEqual(library_result, resolve_audience_panel(
                {"source": "library", "panel_id": "operations-leaders", "version": "1.0.0"},
                self.scope,
                run_dir=base / "run-library",
                library_root=library,
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            ))
            (left / "README.txt").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "snapshot"):
                resolve_audience_panel(
                    {"source": "library", "panel_id": "operations-leaders", "version": "1.0.0"},
                    self.scope,
                    run_dir=base / "run-library",
                    library_root=library,
                    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                )

    def test_scope_and_refresh_states_block_without_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            cases = (
                ("audience", "Different audience", "incompatible", "audience_mismatch"),
                ("category", "Different category", "incompatible", "category_mismatch"),
                ("market", "Different market", "needs_refresh", "market_mismatch"),
                ("geography", "Canada", "needs_refresh", "geography_mismatch"),
                ("buying_context", "Renewal", "needs_refresh", "buying_context_mismatch"),
                ("exclusions", ["A different exclusion"], "needs_refresh", "exclusions_mismatch"),
            )
            for field, value, status, code in cases:
                with self.subTest(field=field):
                    scope = copy.deepcopy(self.scope)
                    scope[field] = value
                    run = base / f"run-{field}"
                    with self.assertRaises(AudienceResolutionBlocked) as caught:
                        resolve_audience_panel(
                            {"source": "file", "package_path": str(package.package_zip_path)},
                            scope,
                            run_dir=run,
                            now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                        )
                    result = caught.exception.result
                    self.assertEqual(status, result["status"])
                    self.assertIn(code, {reason["code"] for reason in result["reasons"]})
                    self.assertFalse((run / "audience" / "snapshot").exists())

            with self.assertRaises(AudienceResolutionBlocked) as expired:
                resolve_audience_panel(
                    {"source": "file", "package_path": str(package.package_zip_path)},
                    self.scope,
                    run_dir=base / "expired",
                    now=datetime(2027, 1, 3, tzinfo=timezone.utc),
                )
            self.assertIn("review_after_elapsed", {x["code"] for x in expired.exception.result["reasons"]})

            with self.assertRaises(AudienceResolutionBlocked) as triggered:
                resolve_audience_panel(
                    {"source": "file", "package_path": str(package.package_zip_path)},
                    self.scope,
                    run_dir=base / "triggered",
                    explicit_refresh_triggers=[" New first-party evidence "],
                    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                )
            self.assertIn("refresh_trigger_present", {x["code"] for x in triggered.exception.result["reasons"]})

            panel = copy.deepcopy(self.panel)
            panel["refresh_conditions"]["review_after"] = "2027-12-31T12:00:00Z"
            panel["refresh_conditions"]["max_age_days"] = 10
            max_age_package = self._package(base / "max-age-package", panel=panel)
            with self.assertRaises(AudienceResolutionBlocked) as aged:
                resolve_audience_panel(
                    {"source": "file", "package_path": str(max_age_package.package_zip_path)},
                    self.scope,
                    run_dir=base / "max-age-run",
                    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                )
            self.assertIn("max_age_elapsed", {x["code"] for x in aged.exception.result["reasons"]})

    def test_provisional_package_is_not_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            brief = copy.deepcopy(self.brief)
            panel = copy.deepcopy(self.panel)
            brief.update(status="provisional_no_research", research_mode="provisional_no_research")
            brief["evidence_sources"] = []
            brief["findings"] = []
            brief["research_questions"] = []
            brief["coverage"] = {key: "empty" for key in brief["coverage"]}
            brief["segment_hypotheses"][0].update(origin="provisional_user_defined", finding_ids=[], evidence_ids=[], confidence="low")
            panel["segments"][0].update(origin="provisional_user_defined", finding_ids=[], evidence_ids=[], weight_source_evidence=[], weighting_rule="planning_allocation")
            panel["persona_archetypes"][0].update(finding_ids=[], evidence_ids=[], evidence_strength="low")
            for dimension in panel["context_strata"][0]["dimensions"]:
                dimension.update(status="experimental", source_evidence=[], finding_ids=[])
            for provenance in panel["grounded_context_profiles"][0]["context_attribute_provenance"]:
                provenance.update(status="experimental", source_evidence=[], finding_ids=[])
            panel["persona_research"].update(
                mode="provisional_no_research", status="provisional_no_research",
                expires_at="2026-07-30T12:00:00Z", source_types=[], evidence_ids=[],
                source_state="no_research_sources", coverage=brief["coverage"],
            )
            package = self._package(base / "provisional", brief=brief, panel=panel)
            with self.assertRaises(AudienceResolutionBlocked) as caught:
                resolve_audience_panel(
                    {"source": "file", "package_path": str(package.package_zip_path)},
                    self.scope,
                    run_dir=base / "run",
                    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                )
            self.assertEqual("needs_refresh", caught.exception.result["status"])
            self.assertIn("provisional_requires_research_refresh", {x["code"] for x in caught.exception.result["reasons"]})

    def test_tampered_portable_package_is_rejected_before_snapshot_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            package.package_zip_path.write_bytes(b"not-a-valid-package")
            run = base / "run"
            with self.assertRaisesRegex(ValueError, "ZIP|archive|package"):
                resolve_audience_panel(
                    {"source": "file", "package_path": str(package.package_zip_path)},
                    self.scope,
                    run_dir=run,
                    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
                )
            self.assertFalse((run / "audience" / "snapshot").exists())

    def test_resolution_rejects_symlinks_and_loose_snapshot_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            resolution = resolve_audience_panel(
                {"source": "file", "package_path": str(package.package_zip_path)},
                self.scope,
                run_dir=base / "run",
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            resolution_path = base / "run" / "audience" / "resolution.json"
            resolution_link = base / "resolution-link.json"
            resolution_link.symlink_to(resolution_path)
            with self.assertRaisesRegex(ValueError, "symlink|canonical"):
                load_audience_resolution(resolution_link)

            readme = base / "run" / "audience" / "snapshot" / "README.txt"
            original = readme.read_bytes()
            readme.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "snapshot"):
                audience_package_binding(base / "run", resolution)
            readme.write_bytes(original)
            target = base / "loose-readme.txt"
            target.write_bytes(original)
            readme.unlink()
            readme.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "snapshot|symlink"):
                audience_package_binding(base / "run", resolution)

    def test_file_binding_must_match_the_exact_requested_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = self._package(base / "first")
            resolution = resolve_audience_panel(
                {"source": "file", "package_path": str(first.package_zip_path)},
                self.scope,
                run_dir=base / "run",
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            binding = audience_package_binding(base / "run", resolution)
            verify_file_package_binding(first.package_zip_path, binding)
            missing = base / "missing.zip"
            with self.assertRaises(ValueError):
                verify_file_package_binding(missing, binding)
            altered = bytearray(first.package_zip_path.read_bytes())
            altered[-1] ^= 1
            different = base / "different.zip"
            different.write_bytes(bytes(altered))
            with self.assertRaises(ValueError):
                verify_file_package_binding(different, binding)

    def test_initial_provisional_run_is_bound_but_cannot_be_registered_or_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            provisional = {
                "scope": self.scope,
                "user_defined_segments": [{
                    "segment_id": "operations-leaders",
                    "name": "Operations leaders",
                    "description": "A user-defined provisional planning segment.",
                }],
                "accepted_by": "panel-owner",
                "accepted_at": "2026-07-22T12:00:00Z",
                "expires_at": "2026-08-10T12:00:00Z",
            }
            result = materialize_provisional_audience(
                provisional,
                run_dir=base / "run",
                now=datetime(2026, 7, 22, 13, tzinfo=timezone.utc),
            )
            self.assertEqual("ready", result["status"])
            snapshot = base / "run" / "audience" / "snapshot"
            brief = json.loads((snapshot / "persona-research-brief.json").read_text())
            panel = json.loads((snapshot / "saved-audience-panel.json").read_text())
            self.assertEqual("provisional_no_research", brief["status"])
            self.assertEqual("panel-owner", brief["approval"]["approved_by"])
            self.assertEqual("2026-08-10T12:00:00Z", panel["persona_research"]["expires_at"])
            with self.assertRaises(ValueError):
                register_package(snapshot / "audience-panel-package.zip", library_root=base / "library")
            with self.assertRaises(AudienceResolutionBlocked):
                resolve_audience_panel(
                    {"source": "file", "package_path": str(snapshot / "audience-panel-package.zip")},
                    self.scope,
                    run_dir=base / "reuse",
                    now=datetime(2026, 7, 23, tzinfo=timezone.utc),
                )

    def test_resolve_cli_outputs_one_json_object_and_exit_five_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            intake = base / "intake.json"
            scope = base / "scope.json"
            intake.write_text(json.dumps({"audience_panel": {"source": "file", "package_path": str(package.package_zip_path)}}))
            changed = copy.deepcopy(self.scope)
            changed["market"] = "Different market"
            scope.write_text(json.dumps(changed))
            result = subprocess.run(
                [sys.executable, str(CLI), "resolve", str(intake), str(scope), str(base / "run")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(5, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("needs_refresh", payload["status"])
            self.assertEqual("", result.stderr)

            malformed = base / "malformed.json"
            malformed.write_text("{}", encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, str(CLI), "resolve", str(malformed), str(scope), str(base / "bad-run")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, invalid.returncode)
            self.assertEqual("validation", json.loads(invalid.stdout)["error"])
            self.assertEqual("", invalid.stderr)

            malformed_scope = base / "malformed-scope.json"
            malformed_scope.write_text("{}", encoding="utf-8")
            bad_scope = subprocess.run(
                [sys.executable, str(CLI), "resolve", str(intake), str(malformed_scope), str(base / "bad-scope-run")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, bad_scope.returncode)
            self.assertEqual("validation", json.loads(bad_scope.stdout)["error"])
            self.assertEqual("", bad_scope.stderr)

            bad_timestamp_intake = base / "bad-timestamp-intake.json"
            bad_timestamp_intake.write_text(json.dumps({
                "provisional_audience": {
                    "scope": self.scope,
                    "user_defined_segments": [{
                        "segment_id": "operations-leaders",
                        "name": "Operations leaders",
                        "description": "A provisional segment.",
                    }],
                    "accepted_by": "owner", "accepted_at": "not-a-timestamp",
                    "expires_at": "2026-08-10T12:00:00Z",
                }
            }), encoding="utf-8")
            bad_timestamp = subprocess.run(
                [sys.executable, str(CLI), "resolve", str(bad_timestamp_intake), str(scope), str(base / "bad-time-run")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, bad_timestamp.returncode)
            self.assertEqual("validation", json.loads(bad_timestamp.stdout)["error"])
            self.assertEqual("", bad_timestamp.stderr)

    def test_v2_dispatch_uses_only_profiles_bound_to_the_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            resolution = resolve_audience_panel(
                {"source": "file", "package_path": str(package.package_zip_path)},
                self.scope,
                run_dir=base / "run",
                now=datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            binding = audience_package_binding(base / "run", resolution)
            resolution_path = base / "run" / "audience" / "resolution.json"
            plan = {
                "study_id": "bound-study", "method": "partial_exposure_maxdiff",
                "audience_lock": resolution["audience_lock"],
                "audience_package": binding,
                "synthetic_replicate_capacity": {"screening_planned": 1},
                "assignment": {"synthetic_replicate_jobs": [{
                    "synthetic_replicate_id": "replicate-1",
                    "segment_id": "operations-leaders",
                    "context_stratum_id": "active-evaluation",
                    "variation_ids": ["V1", "V2", "V3", "V4"],
                    "shown_order": ["V1", "V2", "V3", "V4"],
                }]},
            }
            context = {
                "study_id": "bound-study", "record_type": "screening_response",
                "reaction_protocol": "progressive_reveal",
                "worker_context_isolation": "isolated",
                "creative_prompts": {f"V{i}": f"Review V{i}." for i in range(1, 5)},
                "comparison_prompts": {
                    "partial_exposure_maxdiff": "Choose strongest and weakest.",
                    "complete_exposure": "Rank the set.",
                },
            }
            output = enrich_assignment_jobs(
                plan, context, audience_resolution=resolution_path
            )
            job = output["synthetic_replicate_jobs"][0]
            self.assertEqual("operations-director-evaluating-v1", job["grounded_profile_id"])
            self.assertEqual(
                resolution["grounded_context_profiles"][0]["profile_snapshot"],
                job["profile_snapshot"],
            )

            forged_resolution = copy.deepcopy(resolution)
            forged_resolution["grounded_context_profiles"][0]["profile_snapshot"]["role_context"] = "Invented role"
            resolution_path.write_text(json.dumps(forged_resolution))
            with self.assertRaisesRegex(ValueError, "snapshot"):
                enrich_assignment_jobs(
                    plan, context, audience_resolution=resolution_path
                )
            resolution_path.write_text(json.dumps(resolution))

            injected = copy.deepcopy(resolution["grounded_context_profiles"][0])
            injected["profile_snapshot"]["role_context"] = "Invented role"
            with self.assertRaisesRegex(ValueError, "absent from the resolved"):
                enrich_assignment_jobs(
                    plan, {**context, "profiles": [injected]},
                    audience_resolution=resolution_path,
                )
            with self.assertRaisesRegex(ValueError, "must.*match|must contain"):
                enrich_assignment_jobs(
                    plan,
                    context,
                    manifest={
                        "study_id": "bound-study",
                        "method": "partial_exposure_maxdiff",
                        "audience_lock": {
                            "panel_id": "legacy-panel",
                            "segment_weights": {"operations-leaders": 1.0},
                        },
                    },
                    audience_resolution=resolution_path,
                )
            with self.assertRaisesRegex(ValueError, "requires the ready"):
                enrich_assignment_jobs(plan, context)
            forged_plan = copy.deepcopy(plan)
            forged_plan["audience_package"]["panel_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "audience_package"):
                enrich_assignment_jobs(
                    forged_plan, context, audience_resolution=resolution_path
                )
            forged_counts = copy.deepcopy(plan)
            forged_counts["audience_package"]["panel_byte_count"] += 1
            with self.assertRaisesRegex(ValueError, "audience_package"):
                enrich_assignment_jobs(
                    forged_counts, context, audience_resolution=resolution_path
                )
            with self.assertRaisesRegex(ValueError, "manifest.*audience_package|v2 audience"):
                enrich_assignment_jobs(
                    plan,
                    {**context, "profiles": [injected]},
                    manifest={
                        "study_id": "bound-study",
                        "method": "partial_exposure_maxdiff",
                    },
                    audience_resolution=resolution_path,
                )
            exact_manifest = {
                "study_id": "different-study",
                "method": "partial_exposure_maxdiff",
                "audience_lock": copy.deepcopy(plan["audience_lock"]),
                "audience_package": copy.deepcopy(plan["audience_package"]),
            }
            with self.assertRaisesRegex(ValueError, "study_id"):
                enrich_assignment_jobs(
                    plan,
                    context,
                    manifest=exact_manifest,
                    audience_resolution=resolution_path,
                )
            unbound_plan = copy.deepcopy(plan)
            unbound_plan.pop("audience_lock")
            unbound_plan.pop("audience_package")
            with self.assertRaisesRegex(ValueError, "plan.*audience_package|v2 audience"):
                enrich_assignment_jobs(
                    unbound_plan,
                    context,
                    manifest={
                        **exact_manifest,
                        "study_id": "bound-study",
                    },
                    audience_resolution=resolution_path,
                )

            plan_path = base / "plan.json"
            context_path = base / "context.json"
            output_path = base / "jobs.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            context_path.write_text(json.dumps(context), encoding="utf-8")
            cli_result = subprocess.run(
                [
                    sys.executable,
                    str(PREPARE_JOBS_CLI),
                    str(plan_path),
                    str(context_path),
                    str(output_path),
                    "--audience-resolution",
                    str(resolution_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, cli_result.returncode, cli_result.stderr)
            cli_jobs = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "operations-director-evaluating-v1",
                cli_jobs["synthetic_replicate_jobs"][0]["grounded_profile_id"],
            )

    def test_legacy_audience_lock_is_read_only_and_v2_fields_cannot_be_stripped(self) -> None:
        legacy_plan = {
            "study_id": "legacy-study", "method": "partial_exposure_maxdiff",
            "audience_lock": {"panel_id": "legacy-panel", "segment_weights": {"s1": 1}},
            "synthetic_replicate_capacity": {"screening_planned": 1},
            "assignment": {"synthetic_replicate_jobs": [{
                "synthetic_replicate_id": "legacy-1", "segment_id": "s1",
                "variation_ids": ["V1", "V2", "V3", "V4"],
                "shown_order": ["V1", "V2", "V3", "V4"],
            }]},
        }
        with self.assertRaisesRegex(ValueError, "read-only|audience_package"):
            enrich_assignment_jobs(legacy_plan, {
                "study_id": "legacy-study",
                "profiles": [{"segment_id": "s1"}],
            })

        stripped_v2 = copy.deepcopy(legacy_plan)
        stripped_v2["audience_lock"] = {}
        stripped_v2["grounded_context_profiles"] = []
        with self.assertRaisesRegex(ValueError, "audience_package"):
            enrich_assignment_jobs(stripped_v2, {
                "study_id": "legacy-study", "profiles": [{"segment_id": "s1"}],
            })


if __name__ == "__main__":
    unittest.main()
